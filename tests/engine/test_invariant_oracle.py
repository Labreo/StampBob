"""
Tests for src/engine/invariant_oracle.py and src/engine/rules.py
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import List

import pytest

from src.engine.context_indexer import RepositoryIndex, build_repository_index
from src.engine.invariant_oracle import (
    DiffHunk,
    InvariantOracle,
    InvariantViolation,
    _is_purely_documentation,
    parse_diff,
)
from src.engine.rules import (
    ALL_RULES,
    GOROUTINE_LEAK_RULE,
    NIL_CHECK_BOUNDARY_RULE,
    OTEL_SEMANTIC_RULE,
    UNBUFFERED_CHANNEL_RULE,
    Language,
    Severity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_index(tmp_path: Path, files: dict[str, str]) -> RepositoryIndex:
    """Write *files* to *tmp_path* and return a fully built RepositoryIndex."""
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return build_repository_index(str(tmp_path))


def _unified_diff(file_path: str, start: int, lines: list[str]) -> str:
    """Build a minimal unified diff string for *lines* added at *start*."""
    count = len(lines)
    header = (
        f"diff --git a/{file_path} b/{file_path}\n"
        f"--- a/{file_path}\n"
        f"+++ b/{file_path}\n"
        f"@@ -{start},{count} +{start},{count} @@\n"
    )
    body = "".join(f"+{line}\n" for line in lines)
    return header + body


# ---------------------------------------------------------------------------
# Rules module — structural tests
# ---------------------------------------------------------------------------

class TestRulesModule:
    def test_all_rules_populated(self):
        assert len(ALL_RULES) == 4

    def test_goroutine_rule_metadata(self):
        r = GOROUTINE_LEAK_RULE
        assert r.rule_id == "goroutine-leak"
        assert r.severity == Severity.CRITICAL
        assert Language.GO in r.languages

    def test_unbuffered_rule_metadata(self):
        r = UNBUFFERED_CHANNEL_RULE
        assert r.rule_id == "unbuffered-channel-in-loop"
        assert r.severity == Severity.CRITICAL
        assert Language.GO in r.languages

    def test_nil_rule_metadata(self):
        r = NIL_CHECK_BOUNDARY_RULE
        assert r.rule_id == "nil-check-boundary"
        assert r.severity == Severity.CRITICAL
        assert Language.GO in r.languages
        assert Language.PYTHON in r.languages

    def test_otel_rule_metadata(self):
        r = OTEL_SEMANTIC_RULE
        assert r.rule_id == "otel-semantic-convention"
        assert r.severity == Severity.WARNING
        assert Language.GO in r.languages
        assert Language.PYTHON in r.languages

    def test_otel_approved_prefixes(self):
        assert "gen_ai" in OTEL_SEMANTIC_RULE.approved_prefixes
        assert "server" in OTEL_SEMANTIC_RULE.approved_prefixes
        assert "http" in OTEL_SEMANTIC_RULE.approved_prefixes

    def test_goroutine_spawn_pattern_matches(self):
        assert GOROUTINE_LEAK_RULE.spawn_pattern.search("go func() {")

    def test_goroutine_spawn_pattern_no_false_positive_named(self):
        # Named goroutine — "go processItem(ctx)" — should NOT match
        assert not GOROUTINE_LEAK_RULE.spawn_pattern.search("go processItem(ctx)")

    def test_unbuffered_pattern_matches(self):
        assert UNBUFFERED_CHANNEL_RULE.unbuffered_pattern.search("ch := make(chan int)")

    def test_unbuffered_pattern_ignores_buffered(self):
        assert not UNBUFFERED_CHANNEL_RULE.unbuffered_pattern.search("ch := make(chan int, 10)")

    def test_remediation_nonempty(self):
        for rule in ALL_RULES:
            assert rule.remediation.strip()


# ---------------------------------------------------------------------------
# parse_diff
# ---------------------------------------------------------------------------

class TestParseDiff:
    def test_returns_hunks(self):
        diff = _unified_diff("foo.go", 1, ["x := 1"])
        hunks = parse_diff(diff)
        assert len(hunks) == 1

    def test_hunk_file_path(self):
        diff = _unified_diff("pkg/worker.go", 5, ["go func() {}"])
        hunks = parse_diff(diff)
        assert hunks[0].file_path == "pkg/worker.go"

    def test_hunk_lines_stripped_of_plus(self):
        diff = _unified_diff("a.go", 1, ["hello world"])
        hunks = parse_diff(diff)
        assert hunks[0].lines[0] == "hello world"

    def test_hunk_line_numbers(self):
        diff = _unified_diff("a.go", 10, ["line1", "line2", "line3"])
        hunks = parse_diff(diff)
        assert hunks[0].line_numbers == [10, 11, 12]

    def test_removed_lines_not_included(self):
        diff = (
            "--- a/a.go\n+++ b/a.go\n"
            "@@ -1,2 +1,2 @@\n"
            "-removed line\n"
            "+added line\n"
        )
        hunks = parse_diff(diff)
        assert len(hunks[0].lines) == 1
        assert hunks[0].lines[0] == "added line"

    def test_empty_diff(self):
        assert parse_diff("") == []

    def test_multiple_files(self):
        diff = (
            "--- a/a.go\n+++ b/a.go\n@@ -1 +1 @@\n+line a\n"
            "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n+line b\n"
        )
        hunks = parse_diff(diff)
        files = {h.file_path for h in hunks}
        assert "a.go" in files
        assert "b.py" in files


# ---------------------------------------------------------------------------
# _is_purely_documentation
# ---------------------------------------------------------------------------

class TestIsPurelyDocumentation:
    def _hunk(self, lines: list[str]) -> DiffHunk:
        return DiffHunk("f.go", 1, lines, list(range(1, len(lines) + 1)))

    def test_comment_only_go(self):
        assert _is_purely_documentation(self._hunk(["// This is a comment", ""]))

    def test_comment_only_python(self):
        assert _is_purely_documentation(self._hunk(["# This is a comment"]))

    def test_code_line_not_documentation(self):
        assert not _is_purely_documentation(self._hunk(["x := 1"]))

    def test_mixed_false(self):
        assert not _is_purely_documentation(self._hunk(["// comment", "x := 1"]))

    def test_empty_lines_only(self):
        assert _is_purely_documentation(self._hunk(["", "   ", ""]))


# ---------------------------------------------------------------------------
# InvariantViolation str()
# ---------------------------------------------------------------------------

class TestInvariantViolationStr:
    def test_str_contains_rule_id(self):
        v = InvariantViolation(
            rule_id="goroutine-leak",
            severity=Severity.CRITICAL,
            file_path="worker.go",
            line_range=(10, 10),
            offending_code="go func() {",
            ast_context="compute.Worker.Run",
            message="leaked goroutine",
            remediation="use ctx",
        )
        s = str(v)
        assert "goroutine-leak" in s
        assert "CRITICAL" in s
        assert "worker.go" in s


# ---------------------------------------------------------------------------
# InvariantOracle — empty / trivial inputs
# ---------------------------------------------------------------------------

class TestOracleEmptyInputs:
    def test_empty_diff_returns_empty(self, tmp_path):
        idx = _make_index(tmp_path, {"placeholder.py": "x = 1"})
        oracle = InvariantOracle()
        assert oracle.audit_diff("", idx) == []

    def test_whitespace_diff_returns_empty(self, tmp_path):
        idx = _make_index(tmp_path, {"placeholder.py": "x = 1"})
        oracle = InvariantOracle()
        assert oracle.audit_diff("   \n\n  ", idx) == []

    def test_documentation_only_diff_zero_violations(self, tmp_path):
        idx = _make_index(tmp_path, {"pkg/worker.go": "package pkg\n"})
        oracle = InvariantOracle()
        diff = _unified_diff(
            "pkg/worker.go", 1,
            ["// This function starts the worker pool.", "// See README for details."]
        )
        assert oracle.audit_diff(diff, idx) == []

    def test_python_comment_only_zero_violations(self, tmp_path):
        idx = _make_index(tmp_path, {"mod.py": "x = 1\n"})
        oracle = InvariantOracle()
        diff = _unified_diff("mod.py", 1, ["# just a comment"])
        assert oracle.audit_diff(diff, idx) == []

    def test_non_source_file_skipped(self, tmp_path):
        idx = _make_index(tmp_path, {"mod.py": "x = 1\n"})
        oracle = InvariantOracle()
        diff = _unified_diff("README.md", 1, ["go func() {"])
        assert oracle.audit_diff(diff, idx) == []


# ---------------------------------------------------------------------------
# Rule 1 — Goroutine Leak
# ---------------------------------------------------------------------------

GO_LEAK_SOURCE = textwrap.dedent("""\
    package compute

    func SpawnWorker() {
        go func() {
            doWork()
        }()
    }
""")

GO_SAFE_GOROUTINE = textwrap.dedent("""\
    package compute

    import "context"

    func SpawnSafe(ctx context.Context) {
        go func() {
            select {
            case <-ctx.Done():
                return
            }
        }()
    }
""")


class TestGoroutineLeakRule:
    def test_detects_leak(self, tmp_path):
        idx = _make_index(tmp_path, {"compute/worker.go": GO_LEAK_SOURCE})
        oracle = InvariantOracle()
        diff = _unified_diff("compute/worker.go", 4, ["\tgo func() {"])
        viols = oracle.audit_diff(diff, idx)
        assert any(v.rule_id == "goroutine-leak" for v in viols)

    def test_no_violation_with_ctx(self, tmp_path):
        idx = _make_index(tmp_path, {"compute/safe.go": GO_SAFE_GOROUTINE})
        oracle = InvariantOracle()
        diff = _unified_diff("compute/safe.go", 6, ["\tgo func() {"])
        viols = oracle.audit_diff(diff, idx)
        assert not any(v.rule_id == "goroutine-leak" for v in viols)

    def test_violation_is_critical(self, tmp_path):
        idx = _make_index(tmp_path, {"w.go": GO_LEAK_SOURCE})
        oracle = InvariantOracle()
        diff = _unified_diff("w.go", 4, ["\tgo func() {"])
        viols = oracle.audit_diff(diff, idx)
        leak_viols = [v for v in viols if v.rule_id == "goroutine-leak"]
        assert all(v.severity == Severity.CRITICAL for v in leak_viols)

    def test_offending_code_captured(self, tmp_path):
        idx = _make_index(tmp_path, {"w.go": GO_LEAK_SOURCE})
        oracle = InvariantOracle()
        diff = _unified_diff("w.go", 4, ["\tgo func() {"])
        viols = oracle.audit_diff(diff, idx)
        leak_viols = [v for v in viols if v.rule_id == "goroutine-leak"]
        assert leak_viols
        assert "go func" in leak_viols[0].offending_code

    def test_comment_goroutine_not_flagged(self, tmp_path):
        idx = _make_index(tmp_path, {"w.go": "package p\n"})
        oracle = InvariantOracle()
        diff = _unified_diff("w.go", 1, ["// go func() spawns a goroutine"])
        viols = oracle.audit_diff(diff, idx)
        assert not any(v.rule_id == "goroutine-leak" for v in viols)

    def test_waitgroup_suppresses_violation(self, tmp_path):
        src = textwrap.dedent("""\
            package compute
            import "sync"
            func Pool() {
                var wg sync.WaitGroup
                go func() {
                    defer wg.Done()
                }()
                wg.Wait()
            }
        """)
        idx = _make_index(tmp_path, {"pool.go": src})
        oracle = InvariantOracle()
        diff = _unified_diff("pool.go", 5, ["\tgo func() {"])
        viols = oracle.audit_diff(diff, idx)
        assert not any(v.rule_id == "goroutine-leak" for v in viols)


# ---------------------------------------------------------------------------
# Rule 2 — Unbuffered Channel in Loop
# ---------------------------------------------------------------------------

GO_LOOP_UNBUF = textwrap.dedent("""\
    package compute

    func Dispatch(items []int) {
        for _, item := range items {
            ch := make(chan int)
            go func() { ch <- item }()
        }
    }
""")

GO_LOOP_BUFFERED = textwrap.dedent("""\
    package compute

    func Dispatch(items []int) {
        for _, item := range items {
            ch := make(chan int, 1)
            go func() { ch <- item }()
        }
    }
""")

GO_TOP_LEVEL_UNBUF = textwrap.dedent("""\
    package compute

    func Init() {
        ch := make(chan int)
        _ = ch
    }
""")


class TestUnbufferedChannelRule:
    def test_detects_unbuffered_in_loop(self, tmp_path):
        idx = _make_index(tmp_path, {"compute/dispatch.go": GO_LOOP_UNBUF})
        oracle = InvariantOracle()
        diff = _unified_diff("compute/dispatch.go", 5, ["\t\tch := make(chan int)"])
        viols = oracle.audit_diff(diff, idx)
        assert any(v.rule_id == "unbuffered-channel-in-loop" for v in viols)

    def test_buffered_channel_no_violation(self, tmp_path):
        idx = _make_index(tmp_path, {"compute/dispatch.go": GO_LOOP_BUFFERED})
        oracle = InvariantOracle()
        diff = _unified_diff("compute/dispatch.go", 5, ["\t\tch := make(chan int, 1)"])
        viols = oracle.audit_diff(diff, idx)
        assert not any(v.rule_id == "unbuffered-channel-in-loop" for v in viols)

    def test_top_level_unbuffered_no_violation(self, tmp_path):
        # Not in a loop — should not fire
        idx = _make_index(tmp_path, {"init.go": GO_TOP_LEVEL_UNBUF})
        oracle = InvariantOracle()
        diff = _unified_diff("init.go", 4, ["\tch := make(chan int)"])
        viols = oracle.audit_diff(diff, idx)
        assert not any(v.rule_id == "unbuffered-channel-in-loop" for v in viols)

    def test_violation_is_critical(self, tmp_path):
        idx = _make_index(tmp_path, {"d.go": GO_LOOP_UNBUF})
        oracle = InvariantOracle()
        diff = _unified_diff("d.go", 5, ["\t\tch := make(chan int)"])
        viols = oracle.audit_diff(diff, idx)
        chan_viols = [v for v in viols if v.rule_id == "unbuffered-channel-in-loop"]
        assert all(v.severity == Severity.CRITICAL for v in chan_viols)


# ---------------------------------------------------------------------------
# Rule 3 — Nil Check Boundary
# ---------------------------------------------------------------------------

GO_NIL_DEREF = textwrap.dedent("""\
    package compute

    type Worker struct{ Name string }

    func GetName() string {
        w := &Worker{}
        return w.Name
    }
""")

GO_NIL_GUARDED = textwrap.dedent("""\
    package compute

    type Worker struct{ Name string }

    func GetName(w *Worker) string {
        if w == nil {
            return ""
        }
        return w.Name
    }
""")

PY_NIL_DEREF = textwrap.dedent("""\
    def get_name():
        w = Worker()
        return w.name
""")

PY_NIL_GUARDED = textwrap.dedent("""\
    def get_name():
        w = Worker()
        if w is None:
            return ""
        return w.name
""")


class TestNilCheckBoundaryRule:
    def test_go_deref_without_guard_flagged(self, tmp_path):
        idx = _make_index(tmp_path, {"compute/name.go": GO_NIL_DEREF})
        oracle = InvariantOracle()
        diff = _unified_diff("compute/name.go", 7, ["\treturn w.Name"])
        viols = oracle.audit_diff(diff, idx)
        assert any(v.rule_id == "nil-check-boundary" for v in viols)

    def test_go_deref_with_guard_not_flagged(self, tmp_path):
        idx = _make_index(tmp_path, {"compute/name.go": GO_NIL_GUARDED})
        oracle = InvariantOracle()
        diff = _unified_diff("compute/name.go", 9, ["\treturn w.Name"])
        viols = oracle.audit_diff(diff, idx)
        assert not any(v.rule_id == "nil-check-boundary" for v in viols)

    def test_python_deref_without_guard_flagged(self, tmp_path):
        idx = _make_index(tmp_path, {"mod.py": PY_NIL_DEREF})
        oracle = InvariantOracle()
        diff = _unified_diff("mod.py", 3, ["    return w.name"])
        viols = oracle.audit_diff(diff, idx)
        assert any(v.rule_id == "nil-check-boundary" for v in viols)

    def test_python_deref_with_guard_not_flagged(self, tmp_path):
        idx = _make_index(tmp_path, {"mod.py": PY_NIL_GUARDED})
        oracle = InvariantOracle()
        diff = _unified_diff("mod.py", 5, ["    return w.name"])
        viols = oracle.audit_diff(diff, idx)
        assert not any(v.rule_id == "nil-check-boundary" for v in viols)

    def test_safe_names_not_flagged(self, tmp_path):
        # "ctx.Value()" — ctx is in safe names, should not flag
        src = "package p\nfunc F(ctx context.Context) { _ = ctx.Value(nil) }\n"
        idx = _make_index(tmp_path, {"p.go": src})
        oracle = InvariantOracle()
        diff = _unified_diff("p.go", 2, ["\t_ = ctx.Value(nil)"])
        viols = oracle.audit_diff(diff, idx)
        assert not any(v.rule_id == "nil-check-boundary" for v in viols)


# ---------------------------------------------------------------------------
# Rule 4 — OTel Semantic Convention
# ---------------------------------------------------------------------------

GO_OTEL_BAD = textwrap.dedent("""\
    package telemetry

    import "go.opentelemetry.io/otel/attribute"

    func Instrument(span trace.Span) {
        span.SetAttributes(attribute.String("my_custom_key", "value"))
    }
""")

GO_OTEL_GOOD = textwrap.dedent("""\
    package telemetry

    import "go.opentelemetry.io/otel/attribute"

    func Instrument(span trace.Span) {
        span.SetAttributes(attribute.String("gen_ai.system", "openai"))
    }
""")

PY_OTEL_BAD = textwrap.dedent("""\
    def instrument(span):
        span.set_attribute("my_custom_key", "value")
""")

PY_OTEL_GOOD = textwrap.dedent("""\
    def instrument(span):
        span.set_attribute("gen_ai.request.model", "gpt-4o")
""")


class TestOtelSemanticRule:
    def test_go_bad_key_flagged(self, tmp_path):
        idx = _make_index(tmp_path, {"telemetry/otel.go": GO_OTEL_BAD})
        oracle = InvariantOracle()
        diff = _unified_diff(
            "telemetry/otel.go", 6,
            ['\tspan.SetAttributes(attribute.String("my_custom_key", "value"))']
        )
        viols = oracle.audit_diff(diff, idx)
        assert any(v.rule_id == "otel-semantic-convention" for v in viols)

    def test_go_good_key_not_flagged(self, tmp_path):
        idx = _make_index(tmp_path, {"telemetry/otel.go": GO_OTEL_GOOD})
        oracle = InvariantOracle()
        diff = _unified_diff(
            "telemetry/otel.go", 6,
            ['\tspan.SetAttributes(attribute.String("gen_ai.system", "openai"))']
        )
        viols = oracle.audit_diff(diff, idx)
        assert not any(v.rule_id == "otel-semantic-convention" for v in viols)

    def test_python_bad_key_flagged(self, tmp_path):
        idx = _make_index(tmp_path, {"telemetry.py": PY_OTEL_BAD})
        oracle = InvariantOracle()
        diff = _unified_diff(
            "telemetry.py", 2,
            ['    span.set_attribute("my_custom_key", "value")']
        )
        viols = oracle.audit_diff(diff, idx)
        assert any(v.rule_id == "otel-semantic-convention" for v in viols)

    def test_python_good_key_not_flagged(self, tmp_path):
        idx = _make_index(tmp_path, {"telemetry.py": PY_OTEL_GOOD})
        oracle = InvariantOracle()
        diff = _unified_diff(
            "telemetry.py", 2,
            ['    span.set_attribute("gen_ai.request.model", "gpt-4o")']
        )
        viols = oracle.audit_diff(diff, idx)
        assert not any(v.rule_id == "otel-semantic-convention" for v in viols)

    def test_violation_is_warning(self, tmp_path):
        idx = _make_index(tmp_path, {"t.py": PY_OTEL_BAD})
        oracle = InvariantOracle()
        diff = _unified_diff(
            "t.py", 2,
            ['    span.set_attribute("my_custom_key", "value")']
        )
        viols = oracle.audit_diff(diff, idx)
        otel_viols = [v for v in viols if v.rule_id == "otel-semantic-convention"]
        assert all(v.severity == Severity.WARNING for v in otel_viols)

    def test_otel_comment_not_flagged(self, tmp_path):
        idx = _make_index(tmp_path, {"t.py": "x = 1\n"})
        oracle = InvariantOracle()
        diff = _unified_diff(
            "t.py", 1,
            ['# span.set_attribute("my_custom_key", "value")']
        )
        viols = oracle.audit_diff(diff, idx)
        assert not any(v.rule_id == "otel-semantic-convention" for v in viols)

    def test_server_prefix_approved(self, tmp_path):
        idx = _make_index(tmp_path, {"t.py": "x = 1\n"})
        oracle = InvariantOracle()
        diff = _unified_diff(
            "t.py", 1,
            ['    span.set_attribute("server.address", "localhost")']
        )
        viols = oracle.audit_diff(diff, idx)
        assert not any(v.rule_id == "otel-semantic-convention" for v in viols)

    def test_http_prefix_approved(self, tmp_path):
        idx = _make_index(tmp_path, {"t.go": "package p\n"})
        oracle = InvariantOracle()
        diff = _unified_diff(
            "t.go", 1,
            ['\tspan.SetAttributes(attribute.String("http.method", "GET"))']
        )
        viols = oracle.audit_diff(diff, idx)
        assert not any(v.rule_id == "otel-semantic-convention" for v in viols)


# ---------------------------------------------------------------------------
# Oracle — ordering guarantee
# ---------------------------------------------------------------------------

class TestOracleOrdering:
    def test_critical_before_warning(self, tmp_path):
        # Seed a file that will trigger both a CRITICAL (goroutine leak) and
        # a WARNING (otel bad key).
        src = textwrap.dedent("""\
            package p

            func Mix() {
                go func() { doStuff() }()
            }
        """)
        idx = _make_index(tmp_path, {"mix.go": src})
        oracle = InvariantOracle()
        diff = _unified_diff(
            "mix.go", 4,
            [
                "\tgo func() { doStuff() }()",
                '\tspan.SetAttributes(attribute.String("bad_key", "v"))',
            ]
        )
        viols = oracle.audit_diff(diff, idx)
        if len(viols) >= 2:
            severities = [v.severity for v in viols]
            # All CRITICALs must come before any WARNING
            saw_warning = False
            for s in severities:
                if s == Severity.WARNING:
                    saw_warning = True
                if saw_warning:
                    assert s != Severity.CRITICAL, "CRITICAL appeared after WARNING"


# ---------------------------------------------------------------------------
# Oracle — checker error isolation
# ---------------------------------------------------------------------------

class TestOracleErrorIsolation:
    def test_crashing_checker_does_not_silence_others(self, tmp_path):
        """A checker that always raises must not suppress other checkers."""
        from src.engine.invariant_oracle import _BaseChecker, _OtelSemanticChecker
        from src.engine.rules import OTEL_SEMANTIC_RULE

        class _BoomChecker(_BaseChecker):
            rule = OTEL_SEMANTIC_RULE

            def check(self, hunk, fs, repo_index):
                raise RuntimeError("intentional crash")

        idx = _make_index(tmp_path, {"t.py": "x = 1\n"})
        oracle = InvariantOracle(
            checkers=[_BoomChecker(), _OtelSemanticChecker()]
        )
        diff = _unified_diff(
            "t.py", 1,
            ['    span.set_attribute("bad_key", "v")']
        )
        # Should not raise, and the surviving OtelSemanticChecker should still fire
        viols = oracle.audit_diff(diff, idx)
        assert any(v.rule_id == "otel-semantic-convention" for v in viols)
