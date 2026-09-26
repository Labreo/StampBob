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
    "ctx", "err", "ok", "os", "fmt", "log", "http", "io", "strings",
    "bytes", "json", "sync", "atomic", "math", "sort", "time", "strconv",
    "filepath", "context", "errors", "unicode", "reflect", "runtime",
}
_PY_SAFE_NAMES: Set[str] = {
    "self", "cls", "os", "sys", "re", "json", "logging", "pathlib",
    "typing", "dataclasses", "collections", "itertools", "functools",
    "asyncio", "threading", "subprocess", "shutil", "tempfile",
}


def _has_nil_guard(body_text: str, var_name: str, language: str) -> bool:
    """Return True when *body_text* contains a nil/None guard for *var_name*."""
    if language == "go":
        pattern = re.compile(
            rf"\bif\s+{re.escape(var_name)}\s*[!=]=\s*nil\b"
            rf"|\bif\s+\w+(?:,\s*\w+)*\s*:=.*;\s*{re.escape(var_name)}\s*[!=]=\s*nil\b"
        )
    else:
        pattern = re.compile(
            rf"\bif\s+{re.escape(var_name)}\s+is\s+(?:not\s+)?None\b"
        )
    return bool(pattern.search(body_text))


class _NilCheckBoundaryChecker(_BaseChecker):
    rule = NIL_CHECK_BOUNDARY_RULE

    # Go: pointer types returned from common constructor patterns
    _GO_PTR_ASSIGN_RE = re.compile(
        r"\b(\w+)\s*(?::=|=)\s*(?:new\(|&\w+|[A-Z]\w+\()"
    )
    # Python: assignment from Optional-annotated call (heuristic)
    _PY_OPT_ASSIGN_RE = re.compile(
        r"\b(\w+)\s*(?::=|=)\s*\w+\("
    )

    def check(
        self,
        hunk: DiffHunk,
        fs: Optional[FileSymbols],
        repo_index: RepositoryIndex,
    ) -> List[InvariantViolation]:
        violations: List[InvariantViolation] = []
        language = (fs.language if fs else "unknown")

        if language not in ("go", "python"):
            return violations

        deref_re = (
            NIL_CHECK_BOUNDARY_RULE.go_deref_pattern
            if language == "go"
            else NIL_CHECK_BOUNDARY_RULE.py_deref_pattern
        )
        safe_names = _GO_SAFE_NAMES if language == "go" else _PY_SAFE_NAMES

        for line, lineno in zip(hunk.lines, hunk.line_numbers):
            stripped = line.strip()
            if _COMMENT_LINE_RE.match(stripped) or not stripped:
                continue

            for m in deref_re.finditer(line):
                var_name = m.group(1)
                if var_name in safe_names:
                    continue

                qualname, body = _enclosing_function_body(
                    hunk.file_path, lineno, fs
                )
                body_text = "\n".join(body) if body else "\n".join(hunk.lines)

                # Skip if a guard already exists anywhere in the enclosing body
                if _has_nil_guard(body_text, var_name, language):
                    continue

                # Skip if the variable is not the result of a pointer/optional
                # producing expression — reduces false positives significantly.
                ptr_re = (
                    self._GO_PTR_ASSIGN_RE
                    if language == "go"
                    else self._PY_OPT_ASSIGN_RE
                )
                ptr_vars = {m2.group(1) for m2 in ptr_re.finditer(body_text)}
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
                # Only run checkers whose language matches this file
                lang = (fs.language if fs else ext.lstrip("."))
                rule_langs = checker.rule.languages
                if (
                    Language.ANY not in rule_langs
                    and Language(lang) not in rule_langs
                    and lang not in {l.value for l in rule_langs}
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
