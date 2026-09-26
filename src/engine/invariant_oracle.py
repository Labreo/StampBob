"""
invariant_oracle.py — StampBob's static invariant enforcement engine.

Consumes a ``git diff`` payload and a ``RepositoryIndex``, applies each
rule from ``rules.py`` against every changed code hunk, and returns a list
of ``InvariantViolation`` objects with precise source attribution.

Key design constraints
----------------------
* Zero false positives on comment / documentation-only hunks.
* Each checker is independent; one rule crashing never silences others.
* All analysis is scope-aware: safety signals are checked against the
  *enclosing function body* extracted from the repository index, not
  just the raw diff lines.
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from src.engine.context_indexer import FileSymbols, RepositoryIndex, parse_ast_symbols
from src.engine.rules import (
    ALL_RULES,
    GOROUTINE_LEAK_RULE,
    NIL_CHECK_BOUNDARY_RULE,
    OTEL_SEMANTIC_RULE,
    UNBUFFERED_CHANNEL_RULE,
    GoroutineLeakRule,
    InvariantRule,
    Language,
    NilCheckBoundaryRule,
    OpenTelemetrySemanticRule,
    Severity,
    UnbufferedChannelRule,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Violation model
# ---------------------------------------------------------------------------


@dataclass
class InvariantViolation:
    """A single rule violation detected in a diff hunk."""

    rule_id: str
    severity: Severity
    file_path: str
    line_range: Tuple[int, int]      # (start_line, end_line) — 1-based, inclusive
    offending_code: str              # the exact line(s) that triggered the rule
    ast_context: str                 # enclosing function/class qualname or description
    message: str                     # human-readable explanation
    remediation: str                 # copied from the rule specification

    def __str__(self) -> str:
        return (
            f"[{self.severity.value}] {self.rule_id} "
            f"{self.file_path}:{self.line_range[0]}-{self.line_range[1]}\n"
            f"  {self.message}\n"
            f"  Offending: {self.offending_code.strip()}\n"
            f"  Context:   {self.ast_context}\n"
            f"  Fix:       {self.remediation}"
        )


# ---------------------------------------------------------------------------
# Diff parser
# ---------------------------------------------------------------------------

# Matches unified diff file headers: +++ b/path/to/file.go
_DIFF_FILE_RE = re.compile(r"^\+\+\+\s+(?:b/)?(.+)$")
# Matches hunk headers: @@ -10,6 +10,8 @@  (optional trailing context)
_HUNK_HEADER_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@")
# Lines that are purely comment / documentation (Go and Python)
_COMMENT_LINE_RE = re.compile(
    r"^\s*(?:"
    r"//.*"          # Go single-line comment
    r"|/\*.*\*/"     # Go inline block comment
    r"|#.*"          # Python / shell comment
    r'|"""'          # Python docstring delimiter
    r"|'''"          # Python docstring delimiter
    r"|(?:\*\s+.*)"  # Go block-comment interior line
    r")$"
)


@dataclass
class DiffHunk:
    """A single added-lines hunk from a unified diff."""

    file_path: str
    start_line: int           # 1-based line number in the new file
    lines: List[str]          # raw added lines (prefix ``+`` stripped)
    line_numbers: List[int]   # parallel list of absolute line numbers


def parse_diff(diff_content: str) -> List[DiffHunk]:
    """
    Parse a unified diff string into ``DiffHunk`` objects covering only
    *added* lines (``+`` prefix).  Context and removed lines are discarded.

    Comment-only and blank added lines are preserved in ``lines`` so that
    rule checkers can recognise them and skip them explicitly.
    """
    hunks: List[DiffHunk] = []
    current_file: Optional[str] = None
    current_start: int = 0
    current_lines: List[str] = []
    current_numbers: List[int] = []
    cursor: int = 0  # tracks the new-file line counter within a hunk

    for raw_line in diff_content.splitlines():
        file_match = _DIFF_FILE_RE.match(raw_line)
        if file_match:
            if current_file and current_lines:
                hunks.append(DiffHunk(current_file, current_start,
                                      current_lines, current_numbers))
            current_file = file_match.group(1).strip()
            current_lines = []
            current_numbers = []
            current_start = 0
            cursor = 0
            continue

        hunk_match = _HUNK_HEADER_RE.match(raw_line)
        if hunk_match:
            if current_file and current_lines:
                hunks.append(DiffHunk(current_file, current_start,
                                      current_lines, current_numbers))
            current_lines = []
            current_numbers = []
            current_start = int(hunk_match.group(1))
            cursor = current_start
            continue

        if current_file is None:
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            stripped = raw_line[1:]  # remove leading '+'
            current_lines.append(stripped)
            current_numbers.append(cursor)
            cursor += 1
        elif not raw_line.startswith("-"):
            # context line — advance cursor but do not record
            cursor += 1

    if current_file and current_lines:
        hunks.append(DiffHunk(current_file, current_start,
                              current_lines, current_numbers))

    return hunks


def _is_purely_documentation(hunk: DiffHunk) -> bool:
    """
    Return True when every non-blank added line in the hunk is a comment
    or documentation delimiter — these hunks must never produce violations.
    """
    for line in hunk.lines:
        stripped = line.strip()
        if stripped == "":
            continue
        if not _COMMENT_LINE_RE.match(stripped):
            return False
    return True


# ---------------------------------------------------------------------------
# Scope helpers — extract enclosing function body from the full file
# ---------------------------------------------------------------------------

def _enclosing_function_body(
    file_path: str,
    line: int,
    fs: Optional[FileSymbols],
) -> Tuple[str, List[str]]:
    """
    Return (qualname, body_lines) for the function that contains *line*.

    Falls back to (file_path, []) when the file cannot be read or the line
    does not fall inside any known function.
    """
    if fs is None or fs.parse_error:
        return file_path, []

    # Find the function whose start line is closest to but <= target line.
    best_sig = None
    for sig in fs.functions:
        if sig.line <= line:
            if best_sig is None or sig.line > best_sig.line:
                best_sig = sig

    if best_sig is None:
        return file_path, []

    try:
        source_lines = Path(file_path).read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return best_sig.qualname, []

    # Determine the end of the function by scanning forward from its start
    # line until indentation returns to the level before the function header.
    start_idx = best_sig.line - 1  # 0-based
    if start_idx >= len(source_lines):
        return best_sig.qualname, []

    # Use the indentation of the ``func`` / ``def`` line as the baseline.
    base_indent = len(source_lines[start_idx]) - len(source_lines[start_idx].lstrip())
    body: List[str] = []
    for src_line in source_lines[start_idx:]:
        body.append(src_line)
        # Stop when we hit a non-blank line with less-or-equal indent
        # (i.e., we've left the function body).  Skip the first line itself.
        if len(body) > 1 and src_line.strip():
            indent = len(src_line) - len(src_line.lstrip())
            if indent <= base_indent:
                break

    return best_sig.qualname, body


# ---------------------------------------------------------------------------
# Rule checkers
# ---------------------------------------------------------------------------

class _BaseChecker:
    """Common interface for per-rule checkers."""

    rule: InvariantRule

    def check(
        self,
        hunk: DiffHunk,
        fs: Optional[FileSymbols],
        repo_index: RepositoryIndex,
    ) -> List[InvariantViolation]:
        raise NotImplementedError


# ── Rule 1: Goroutine Leak ──────────────────────────────────────────────────

class _GoroutineLeakChecker(_BaseChecker):
    rule = GOROUTINE_LEAK_RULE

    def check(
        self,
        hunk: DiffHunk,
        fs: Optional[FileSymbols],
        repo_index: RepositoryIndex,
    ) -> List[InvariantViolation]:
        violations: List[InvariantViolation] = []
        spawn_re = GOROUTINE_LEAK_RULE.spawn_pattern

        for i, (line, lineno) in enumerate(zip(hunk.lines, hunk.line_numbers)):
            if _COMMENT_LINE_RE.match(line.strip()):
                continue
            if not spawn_re.search(line):
                continue

            # Fetch the enclosing function body from the full file for safety
            # signal inspection — not just the diff hunk.
            qualname, body = _enclosing_function_body(hunk.file_path, lineno, fs)

            body_text = "\n".join(body) if body else "\n".join(hunk.lines)

            has_safety = any(
                p.search(body_text)
                for p in GOROUTINE_LEAK_RULE.safety_patterns
            )
            if has_safety:
                continue

            violations.append(InvariantViolation(
                rule_id=self.rule.rule_id,
                severity=self.rule.severity,
                file_path=hunk.file_path,
                line_range=(lineno, lineno),
                offending_code=line,
                ast_context=qualname,
                message=(
                    f"Anonymous goroutine spawned in ``{qualname}`` "
                    "with no context cancellation or exit signal in scope."
                ),
                remediation=self.rule.remediation,
            ))

        return violations


# ── Rule 2: Unbuffered Channel in Loop ─────────────────────────────────────

class _UnbufferedChannelChecker(_BaseChecker):
    rule = UNBUFFERED_CHANNEL_RULE

    def check(
        self,
        hunk: DiffHunk,
        fs: Optional[FileSymbols],
        repo_index: RepositoryIndex,
    ) -> List[InvariantViolation]:
        violations: List[InvariantViolation] = []
        unbuf_re = UNBUFFERED_CHANNEL_RULE.unbuffered_pattern
        loop_patterns = UNBUFFERED_CHANNEL_RULE.loop_patterns

        for line, lineno in zip(hunk.lines, hunk.line_numbers):
            if _COMMENT_LINE_RE.match(line.strip()):
                continue
            if not unbuf_re.search(line):
                continue

            qualname, body = _enclosing_function_body(hunk.file_path, lineno, fs)
            body_text = "\n".join(body) if body else "\n".join(hunk.lines)

            in_loop = any(p.search(body_text) for p in loop_patterns)
            if not in_loop:
                continue

            violations.append(InvariantViolation(
                rule_id=self.rule.rule_id,
                severity=self.rule.severity,
                file_path=hunk.file_path,
                line_range=(lineno, lineno),
                offending_code=line,
                ast_context=qualname,
                message=(
                    f"Unbuffered channel allocated inside a loop in "
                    f"``{qualname}`` — can deadlock worker pool."
                ),
                remediation=self.rule.remediation,
            ))

        return violations


# ── Rule 3: Nil Check Boundary ─────────────────────────────────────────────

# Names that are safe to dereference without a nil check (builtins / keywords)
_GO_SAFE_NAMES: Set[str] = {
    # Standard library packages
    "ctx", "err", "ok", "os", "fmt", "log", "http", "io", "strings",
    "bytes", "json", "sync", "atomic", "math", "sort", "time", "strconv",
    "filepath", "context", "errors", "unicode", "reflect", "runtime",
    # HTTP request parameter — r *http.Request fields are never nil
    "r", "req", "resp",
    # Standard test parameters (testing.T / testing.B) — always valid
    "t", "b", "tb",
    # gRPC / metadata / tracing framework package-level names
    "grpc", "metadata", "trace",
    # "next" is the standard HTTP middleware chain — always non-nil
    "next",
}
_PY_SAFE_NAMES: Set[str] = {
    "self", "cls", "os", "sys", "re", "json", "logging", "pathlib",
    "typing", "dataclasses", "collections", "itertools", "functools",
    "asyncio", "threading", "subprocess", "shutil", "tempfile",
}


# Go: recognises "if err != nil { return/fatal/... }" error-guard that
# implies the paired value variable is non-nil.
_GO_ERR_GUARD_RE = re.compile(
    r"\bif\s+err\s*!=\s*nil\s*\{[^}]*(?:return|t\.Fatal|panic|log\.Fatal)",
    re.DOTALL,
)

# Detects `x, err :=` so we can correlate x with its error guard
_GO_MULTI_RETURN_ERR_RE = re.compile(
    r"\b(\w+)\s*,\s*err\s*:="
)


def _has_nil_guard(body_text: str, var_name: str, language: str) -> bool:
    """Return True when *body_text* contains a nil/None guard for *var_name*.

    Comment lines (``//`` for Go, ``#`` for Python) are stripped before
    searching so that advisory notes like ``// Missing: if x == nil`` do not
    produce false-safe signals.

    For Go, also recognises the idiomatic error-guard pattern:
    ``x, err := f(); if err != nil { return/fatal/... }`` — when `var_name`
    was assigned as the first value in such a pair and the body contains a
    well-formed error guard, the value is considered protected.
    """
    # Strip single-line comments before pattern matching
    if language == "go":
        # Remove everything from // to end of line
        clean = re.sub(r"//.*", "", body_text)
        pattern = re.compile(
            rf"\bif\s+{re.escape(var_name)}\s*[!=]=\s*nil\b"
            rf"|\bif\s+\w+(?:,\s*\w+)*\s*:=.*;\s*{re.escape(var_name)}\s*[!=]=\s*nil\b"
        )
        if pattern.search(clean):
            return True
        # Idiomatic error guard: `x, err := f()` + `if err != nil { return }`
        paired_vars = {m.group(1) for m in _GO_MULTI_RETURN_ERR_RE.finditer(clean)}
        if var_name in paired_vars and _GO_ERR_GUARD_RE.search(clean):
            return True
        return False
    else:
        clean = re.sub(r"#.*", "", body_text)
        pattern = re.compile(
            rf"\bif\s+{re.escape(var_name)}\s+is\s+(?:not\s+)?None\b"
        )
        return bool(pattern.search(clean))


class _NilCheckBoundaryChecker(_BaseChecker):
    rule = NIL_CHECK_BOUNDARY_RULE

    # Go: pointer/nil-producing assignments — five forms matched:
    #
    #   1. Multi-return:    user, err := db.Find(...)  /  val, _ := f()
    #      — captures the first identifier (the value, not the error/blank)
    #   2. Call (method chain / constructor):
    #                       user := s.db.FindUser(...)  /  obj := NewClient(cfg)
    #   3. Address-of / new: p := &Foo{}  /  u := new(User)
    #   4. Field / chained field access (assigned pointer field):
    #                       addr := order.ShippingAddress
    #                       discountPct := cart.Discount.Percentage
    #   5. Map/slice index: p := l.registry[name]  /  e := m["key"]
    #   6. Type assertion:  user := val.(*User)
    _GO_PTR_ASSIGN_RE = re.compile(
        r"(?:"
        # (1) multi-return: captures first ident before the comma
        r"\b(\w+)\s*,\s*\w+\s*:="
        r"|"
        # (2)+(3) single-var: call, constructor, address-of, or dotted call
        r"\b(\w+)\s*(?::=|=)\s*(?:new\(|&\w+|\w+(?:\.\w+)+\(|[A-Z]\w*\()"
        r"|"
        # (4) field access assignment: `x := a.B` or `x := a.B.C`
        r"\b(\w+)\s*:=\s*\w+(?:\.\w+)+"
        r"|"
        # (5) map/slice index: `x := m[key]`
        r"\b(\w+)\s*:=\s*\w+(?:\.\w+)*\[.+?\]"
        r"|"
        # (6) type assertion: `x := expr.(*Type)` or `x := expr.(Type)`
        r"\b(\w+)\s*:=\s*.+\.\([*]?\w+\)"
        r")"
    )
    # Python: assignment from Optional-annotated call (heuristic)
    _PY_OPT_ASSIGN_RE = re.compile(
        r"\b(\w+)\s*(?::=|=)\s*\w+\("
    )

    # Go: map/slice subscript dereference without a preceding nil guard —
    # e.g.  `md["key"]` where `md` may be a nil map.
    _GO_MAP_DEREF_RE = re.compile(r"\b([a-z_]\w*)\[")

    # Go: chained field access — `a.B.C` — where `a.B` may be nil.
    # We capture `a` + `B` so we can synthesise the intermediate name `a_B`
    # and check for a nil guard on it using comment-stripped body text.
    _GO_CHAIN_DEREF_RE = re.compile(r"\b([a-z_]\w*)\.([A-Z]\w*)(?:\.\w+)+")

    # Go: range over a field that may be nil — `for k, v := range x.Field {`
    # emits `x` as a potentially-nil receiver.
    _GO_RANGE_FIELD_RE = re.compile(r"\brange\s+([a-z_]\w*)\.\w+")

    def check(
        self,
        hunk: DiffHunk,
        fs: Optional[FileSymbols],
        repo_index: RepositoryIndex,
    ) -> List[InvariantViolation]:
        violations: List[InvariantViolation] = []
        # Infer language from file extension when FileSymbols not available
        # (diff-only mode used during offline benchmark evaluation).
        if fs is not None:
            language = fs.language
        else:
            ext = Path(hunk.file_path).suffix.lower()
            language = "go" if ext == ".go" else ("python" if ext == ".py" else "unknown")

        if language not in ("go", "python"):
            return violations

        deref_re = (
            NIL_CHECK_BOUNDARY_RULE.go_deref_pattern
            if language == "go"
            else NIL_CHECK_BOUNDARY_RULE.py_deref_pattern
        )
        safe_names = _GO_SAFE_NAMES if language == "go" else _PY_SAFE_NAMES

        ptr_re = (
            self._GO_PTR_ASSIGN_RE
            if language == "go"
            else self._PY_OPT_ASSIGN_RE
        )

        for line, lineno in zip(hunk.lines, hunk.line_numbers):
            stripped = line.strip()
            if _COMMENT_LINE_RE.match(stripped) or not stripped:
                continue

            # ----------------------------------------------------------------
            # Build the set of potentially-nil variables once per line so
            # that body_text is only resolved a minimum number of times.
            # ----------------------------------------------------------------
            qualname, body = _enclosing_function_body(
                hunk.file_path, lineno, fs
            )
            body_text = "\n".join(body) if body else "\n".join(hunk.lines)

            ptr_vars = {
                next(g for g in m2.groups() if g is not None)
                for m2 in ptr_re.finditer(body_text)
            }

            # Also treat variables used in `for … range x.Field` as
            # potentially-nil receivers (e.g. `m` in `range m.Labels`).
            if language == "go":
                for rm in self._GO_RANGE_FIELD_RE.finditer(body_text):
                    ptr_vars.add(rm.group(1))

            # ----------------------------------------------------------------
            # Case A — standard deref candidates: `var.Field` / `var[…]`
            # ----------------------------------------------------------------
            candidates = list(deref_re.finditer(line))
            if language == "go":
                for mm in self._GO_MAP_DEREF_RE.finditer(line):
                    candidates.append(mm)

            seen_vars: set[str] = set()
            for m in candidates:
                var_name = m.group(1)
                if var_name in safe_names or var_name in seen_vars:
                    continue
                seen_vars.add(var_name)

                # Skip if a guard already exists anywhere in the enclosing body
                if _has_nil_guard(body_text, var_name, language):
                    continue

                if var_name not in ptr_vars:
                    continue

                violations.append(InvariantViolation(
                    rule_id=self.rule.rule_id,
                    severity=self.rule.severity,
                    file_path=hunk.file_path,
                    line_range=(lineno, lineno),
                    offending_code=line,
                    ast_context=qualname,
                    message=(
                        f"``{var_name}`` dereferenced in ``{qualname}`` "
                        "without a preceding nil/None guard."
                    ),
                    remediation=self.rule.remediation,
                ))

            # ----------------------------------------------------------------
            # Case B — chained field dereference: `a.B.C` where `a.B` may
            # be nil.  We synthesise the intermediate expression as the
            # "variable name" and look for `if a.B == nil` guards.
            # Only runs for Go (Python uses is-None guards on scalars).
            # ----------------------------------------------------------------
            if language == "go":
                for cm in self._GO_CHAIN_DEREF_RE.finditer(line):
                    receiver, field = cm.group(1), cm.group(2)
                    if receiver in safe_names:
                        continue
                    # Synthesised intermediate: `receiver.Field`
                    intermediate = f"{receiver}.{field}"
                    # Guard check: `if receiver.Field == nil`
                    guard_pat = re.compile(
                        rf"\bif\s+{re.escape(intermediate)}\s*[!=]=\s*nil\b"
                    )
                    clean_body = re.sub(r"//.*", "", body_text)
                    if guard_pat.search(clean_body):
                        continue
                    violations.append(InvariantViolation(
                        rule_id=self.rule.rule_id,
                        severity=self.rule.severity,
                        file_path=hunk.file_path,
                        line_range=(lineno, lineno),
                        offending_code=line,
                        ast_context=qualname,
                        message=(
                            f"``{intermediate}`` accessed in ``{qualname}`` "
                            "without a preceding nil guard — chained field "
                            "may be nil."
                        ),
                        remediation=self.rule.remediation,
                    ))

        return violations


# ── Rule 4: OpenTelemetry Semantic Convention ───────────────────────────────

def _extract_attr_key(line: str) -> Optional[str]:
    """
    Return the span attribute key string from a line, or None if no
    attribute-setting call is found.
    """
    for pattern in (
        OTEL_SEMANTIC_RULE.set_attr_pattern,
        OTEL_SEMANTIC_RULE.py_set_attr_pattern,
    ):
        m = pattern.search(line)
        if m:
            return m.group(1)
    return None


def _is_approved_key(key: str) -> bool:
    """Return True when the key starts with a CNCF semconv-approved prefix."""
    approved = OTEL_SEMANTIC_RULE.approved_prefixes
    for prefix in approved:
        if key == prefix or key.startswith(prefix + ".") or key.startswith(prefix + "_"):
            return True
    return False


class _OtelSemanticChecker(_BaseChecker):
    rule = OTEL_SEMANTIC_RULE

    def check(
        self,
        hunk: DiffHunk,
        fs: Optional[FileSymbols],
        repo_index: RepositoryIndex,
    ) -> List[InvariantViolation]:
        violations: List[InvariantViolation] = []

        for line, lineno in zip(hunk.lines, hunk.line_numbers):
            if _COMMENT_LINE_RE.match(line.strip()):
                continue

            key = _extract_attr_key(line)
            if key is None:
                continue

            if _is_approved_key(key):
                continue

            qualname, _ = _enclosing_function_body(hunk.file_path, lineno, fs)

            violations.append(InvariantViolation(
                rule_id=self.rule.rule_id,
                severity=self.rule.severity,
                file_path=hunk.file_path,
                line_range=(lineno, lineno),
                offending_code=line,
                ast_context=qualname,
                message=(
                    f"Ad-hoc OTel span attribute key ``{key}`` in "
                    f"``{qualname}`` does not match any approved "
                    "CNCF semconv prefix."
                ),
                remediation=self.rule.remediation,
            ))

        return violations


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------

_CHECKERS: List[_BaseChecker] = [
    _GoroutineLeakChecker(),
    _UnbufferedChannelChecker(),
    _NilCheckBoundaryChecker(),
    _OtelSemanticChecker(),
]

# File extensions the oracle will analyse
_ANALYSABLE_EXTS: Set[str] = {".go", ".py"}


class InvariantOracle:
    """
    Applies all registered ``InvariantRule`` checkers to the added lines in a
    unified diff, enriched by the full ``RepositoryIndex`` for scope-aware
    analysis.

    Usage
    -----
    ::

        oracle = InvariantOracle()
        violations = oracle.audit_diff(diff_text, repo_index)
        for v in violations:
            print(v)
    """

    def __init__(self, checkers: Optional[List[_BaseChecker]] = None) -> None:
        self._checkers = checkers if checkers is not None else _CHECKERS

    def audit_diff(
        self,
        diff_content: str,
        repo_index: RepositoryIndex,
    ) -> List[InvariantViolation]:
        """
        Analyse every added-line hunk in *diff_content* against all invariant
        rules.

        Parameters
        ----------
        diff_content:
            Raw output of ``git diff`` (unified format, any context size).
        repo_index:
            Fully populated ``RepositoryIndex`` for the repository. Used to
            resolve enclosing function bodies and cross-file scope.

        Returns
        -------
        List[InvariantViolation]
            Ordered list of violations, grouped by file then by line number.
            Returns an empty list for pure documentation / comment diffs.
        """
        if not diff_content or not diff_content.strip():
            return []

        hunks = parse_diff(diff_content)
        violations: List[InvariantViolation] = []

        for hunk in hunks:
            file_path = hunk.file_path
            ext = Path(file_path).suffix.lower()

            # Skip non-source files (markdown, YAML, plain text …)
            if ext not in _ANALYSABLE_EXTS:
                continue

            # Skip hunks that are purely documentation / comments
            if _is_purely_documentation(hunk):
                continue

            # Resolve FileSymbols from the index; fall back to parsing on disk
            fs: Optional[FileSymbols] = repo_index.file_symbols.get(file_path)
            if fs is None:
                # Try to locate by basename (diff paths can be relative)
                for indexed_path, indexed_fs in repo_index.file_symbols.items():
                    if indexed_path.endswith(file_path) or file_path.endswith(
                        Path(indexed_path).name
                    ):
                        fs = indexed_fs
                        hunk = DiffHunk(indexed_path, hunk.start_line,
                                        hunk.lines, hunk.line_numbers)
                        file_path = indexed_path
                        break

            if fs is None and ext in _ANALYSABLE_EXTS:
                # Last resort: parse the file on disk if it exists
                candidate = Path(repo_index.root) / file_path
                if candidate.exists():
                    fs = parse_ast_symbols(str(candidate))

            for checker in self._checkers:
                # Only run checkers whose language matches this file.
                # Use the FileSymbols language when available (always "python"
                # or "go"); fall back to the raw extension string for files
                # that were not indexed.  Compare by value only — Language(lang)
                # would raise ValueError for short extensions like "py".
                lang = (fs.language if fs else ext.lstrip("."))
                rule_langs = checker.rule.languages
                rule_lang_values = {l.value for l in rule_langs}
                if (
                    Language.ANY not in rule_langs
                    and lang not in rule_lang_values
                ):
                    continue

                try:
                    found = checker.check(hunk, fs, repo_index)
                    violations.extend(found)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Checker %s raised on %s: %s",
                        checker.__class__.__name__,
                        hunk.file_path,
                        exc,
                        exc_info=True,
                    )

        # Sort: CRITICAL before WARNING, then by file path, then by line
        violations.sort(
            key=lambda v: (
                0 if v.severity == Severity.CRITICAL else 1,
                v.file_path,
                v.line_range[0],
            )
        )
        return violations
