"""
tests/generator/test_repro_synthesizer.py

Unit tests for src/generator/repro_synthesizer.py — 100% function coverage.

Tests verify:
- ReproTest dataclass construction
- _go_test_preamble helper
- _current_goroutine_count helper
- All four rule synthesizers (_synth_goroutine_leak, _synth_unbuffered_channel,
  _synth_nil_check_go, _synth_nil_check_python, _synth_otel_semantic)
- synthesize_repro_test public API including language dispatch and ValueError
"""

from __future__ import annotations

import pytest

from src.engine.invariant_oracle import InvariantViolation
from src.engine.rules import Severity
from src.generator.repro_synthesizer import (
    ReproTest,
    _current_goroutine_count,
    _go_test_preamble,
    _synth_goroutine_leak,
    _synth_nil_check_go,
    _synth_nil_check_python,
    _synth_otel_semantic,
    _synth_unbuffered_channel,
    synthesize_repro_test,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_violation(
    rule_id: str = "goroutine-leak",
    file_path: str = "pkg/server.go",
    severity: Severity = Severity.CRITICAL,
    offending_code: str = "  go func() {",
    ast_context: str = "pkg.ServeForever",
    message: str = "goroutine leak detected",
    line: int = 42,
) -> InvariantViolation:
    return InvariantViolation(
        rule_id=rule_id,
        severity=severity,
        file_path=file_path,
        line_range=(line, line),
        offending_code=offending_code,
        ast_context=ast_context,
        message=message,
        remediation="use ctx.Done()",
    )


# ---------------------------------------------------------------------------
# ReproTest dataclass
# ---------------------------------------------------------------------------

class TestReproTestDataclass:
    def test_fields_stored_correctly(self):
        rt = ReproTest(
            rule_id="goroutine-leak",
            language="go",
            file_name="repro_test.go",
            code_content="package repro_test",
            expected_failure_pattern=r"LEAK",
        )
        assert rt.rule_id == "goroutine-leak"
        assert rt.language == "go"
        assert rt.file_name == "repro_test.go"
        assert rt.code_content == "package repro_test"
        assert rt.expected_failure_pattern == r"LEAK"


# ---------------------------------------------------------------------------
# _go_test_preamble
# ---------------------------------------------------------------------------

class TestGoTestPreamble:
    def test_package_declaration_present(self):
        preamble = _go_test_preamble(["testing"])
        assert preamble.startswith("package repro_test")

    def test_all_imports_included(self):
        preamble = _go_test_preamble(["testing", "fmt", "time"])
        assert '"testing"' in preamble
        assert '"fmt"' in preamble
        assert '"time"' in preamble

    def test_empty_imports(self):
        preamble = _go_test_preamble([])
        assert "import (" in preamble
        assert ")" in preamble

    def test_single_import(self):
        preamble = _go_test_preamble(["runtime"])
        assert '"runtime"' in preamble

    def test_ends_with_double_newline(self):
        preamble = _go_test_preamble(["testing"])
        assert preamble.endswith(")\n\n")


# ---------------------------------------------------------------------------
# _current_goroutine_count
# ---------------------------------------------------------------------------

class TestCurrentGoroutineCount:
    def test_returns_string(self):
        result = _current_goroutine_count()
        assert isinstance(result, str)

    def test_contains_func_signature(self):
        result = _current_goroutine_count()
        assert "func liveGoroutines() int" in result

    def test_uses_runtime_stack(self):
        result = _current_goroutine_count()
        assert "runtime.Stack" in result

    def test_uses_strings_count(self):
        result = _current_goroutine_count()
        assert "strings.Count" in result


# ---------------------------------------------------------------------------
# _synth_goroutine_leak
# ---------------------------------------------------------------------------

class TestSynthGoroutineLeak:
    def setup_method(self):
        self.v = _make_violation(rule_id="goroutine-leak")
        self.rt = _synth_goroutine_leak(self.v)

    def test_rule_id(self):
        assert self.rt.rule_id == "goroutine-leak"

    def test_language_is_go(self):
        assert self.rt.language == "go"

    def test_file_name(self):
        assert self.rt.file_name == "repro_test.go"

    def test_code_has_go_package(self):
        assert "package repro_test" in self.rt.code_content

    def test_code_has_leak_function(self):
        assert "leakyFunc" in self.rt.code_content

    def test_code_has_test_function(self):
        assert "TestReproGoroutineLeak" in self.rt.code_content

    def test_code_uses_live_goroutines_helper(self):
        assert "liveGoroutines" in self.rt.code_content

    def test_expected_pattern(self):
        assert "goroutine leak confirmed" in self.rt.expected_failure_pattern
        assert "LEAK DETECTED" in self.rt.expected_failure_pattern

    def test_offending_code_embedded(self):
        assert self.v.offending_code.strip() in self.rt.code_content

    def test_file_context_embedded(self):
        assert self.v.file_path in self.rt.code_content

    def test_empty_offending_code_falls_back(self):
        v = _make_violation(rule_id="goroutine-leak", offending_code="")
        rt = _synth_goroutine_leak(v)
        assert "select {}" in rt.code_content

    def test_empty_ast_context_falls_back(self):
        v = _make_violation(rule_id="goroutine-leak", ast_context="")
        rt = _synth_goroutine_leak(v)
        assert "unnamedFunc" in rt.code_content

    def test_required_imports_present(self):
        for pkg in ("runtime", "strings", "testing", "time", "fmt"):
            assert f'"{pkg}"' in self.rt.code_content


# ---------------------------------------------------------------------------
# _synth_unbuffered_channel
# ---------------------------------------------------------------------------

class TestSynthUnbufferedChannel:
    def setup_method(self):
        self.v = _make_violation(
            rule_id="unbuffered-channel-in-loop",
            offending_code="  ch := make(chan int)",
            ast_context="pkg.RunWorkers",
        )
        self.rt = _synth_unbuffered_channel(self.v)

    def test_rule_id(self):
        assert self.rt.rule_id == "unbuffered-channel-in-loop"

    def test_language_is_go(self):
        assert self.rt.language == "go"

    def test_file_name(self):
        assert self.rt.file_name == "repro_test.go"

    def test_code_has_worker_func(self):
        assert "workerFunc" in self.rt.code_content

    def test_code_has_test_function(self):
        assert "TestReproUnbufferedChannelDeadlock" in self.rt.code_content

    def test_code_contains_unbuffered_make(self):
        assert "make(chan int)" in self.rt.code_content

    def test_expected_pattern(self):
        assert "unbuffered channel deadlock confirmed" in self.rt.expected_failure_pattern
        assert "DEADLOCK REPRODUCED" in self.rt.expected_failure_pattern

    def test_file_context_embedded(self):
        assert self.v.file_path in self.rt.code_content

    def test_required_imports_present(self):
        for pkg in ("testing", "time", "fmt"):
            assert f'"{pkg}"' in self.rt.code_content

    def test_no_unused_imports(self):
        for pkg in ("runtime", "strings", "sync"):
            assert f'"{pkg}"' not in self.rt.code_content

    def test_empty_context_falls_back(self):
        v = _make_violation(
            rule_id="unbuffered-channel-in-loop", ast_context=""
        )
        rt = _synth_unbuffered_channel(v)
        assert "workerFunc" in rt.code_content


# ---------------------------------------------------------------------------
# _synth_nil_check_go
# ---------------------------------------------------------------------------

class TestSynthNilCheckGo:
    def setup_method(self):
        self.v = _make_violation(
            rule_id="nil-check-boundary",
            file_path="pkg/api.go",
            offending_code="  result.Value",
            ast_context="pkg.HandleRequest",
        )
        self.rt = _synth_nil_check_go(self.v)

    def test_rule_id(self):
        assert self.rt.rule_id == "nil-check-boundary"

    def test_language_is_go(self):
        assert self.rt.language == "go"

    def test_file_name(self):
        assert self.rt.file_name == "repro_test.go"

    def test_code_has_struct(self):
        assert "type MyType struct" in self.rt.code_content

    def test_code_has_nil_returning_func(self):
        assert "mayReturnNil" in self.rt.code_content

    def test_code_has_unguarded_deref(self):
        assert "derefsWithoutGuard" in self.rt.code_content

    def test_code_has_test_function(self):
        assert "TestReproNilCheckBoundary" in self.rt.code_content

    def test_code_uses_recover(self):
        assert "recover()" in self.rt.code_content

    def test_expected_pattern(self):
        assert "nil dereference panic confirmed" in self.rt.expected_failure_pattern

    def test_required_imports_present(self):
        for pkg in ("testing", "fmt"):
            assert f'"{pkg}"' in self.rt.code_content

    def test_no_unused_imports(self):
        for pkg in ("runtime", "strings", "time", "sync"):
            assert f'"{pkg}"' not in self.rt.code_content

    def test_empty_context_falls_back(self):
        v = _make_violation(
            rule_id="nil-check-boundary", file_path="x.go", ast_context=""
        )
        rt = _synth_nil_check_go(v)
        assert "boundaryFunc" in rt.code_content


# ---------------------------------------------------------------------------
# _synth_nil_check_python
# ---------------------------------------------------------------------------

class TestSynthNilCheckPython:
    def setup_method(self):
        self.v = _make_violation(
            rule_id="nil-check-boundary",
            file_path="pkg/api.py",
            offending_code="  obj.value",
            ast_context="api.handle_request",
        )
        self.rt = _synth_nil_check_python(self.v)

    def test_rule_id(self):
        assert self.rt.rule_id == "nil-check-boundary"

    def test_language_is_python(self):
        assert self.rt.language == "python"

    def test_file_name(self):
        assert self.rt.file_name == "test_repro.py"

    def test_code_has_pytest_import(self):
        assert "import pytest" in self.rt.code_content

    def test_code_has_test_function(self):
        assert "def test_repro_nil_check_boundary" in self.rt.code_content

    def test_code_uses_pytest_raises(self):
        assert "pytest.raises(AttributeError)" in self.rt.code_content

    def test_code_has_may_return_none(self):
        assert "may_return_none" in self.rt.code_content

    def test_offending_code_embedded(self):
        assert self.v.offending_code.strip() in self.rt.code_content

    def test_expected_pattern_has_confirmed(self):
        assert "none dereference AttributeError confirmed" in self.rt.expected_failure_pattern

    def test_empty_context_falls_back(self):
        v = _make_violation(
            rule_id="nil-check-boundary", file_path="x.py", ast_context=""
        )
        rt = _synth_nil_check_python(v)
        assert "boundary_func" in rt.code_content


# ---------------------------------------------------------------------------
# _synth_otel_semantic
# ---------------------------------------------------------------------------

class TestSynthOtelSemantic:
    def setup_method(self):
        self.v = _make_violation(
            rule_id="otel-semantic-convention",
            file_path="pkg/tracing.py",
            offending_code='  span.set_attribute("custom.my_key", val)',
            ast_context="tracing.instrument",
            message="Ad-hoc OTel span attribute key ``custom.my_key`` in tracing.instrument",
            severity=Severity.WARNING,
        )
        self.rt = _synth_otel_semantic(self.v)

    def test_rule_id(self):
        assert self.rt.rule_id == "otel-semantic-convention"

    def test_language_is_python(self):
        assert self.rt.language == "python"

    def test_file_name(self):
        assert self.rt.file_name == "test_repro.py"

    def test_extracts_key_from_message(self):
        assert "custom.my_key" in self.rt.code_content

    def test_code_has_test_function(self):
        assert "def test_repro_otel_semantic_convention" in self.rt.code_content

    def test_code_has_approved_prefixes(self):
        assert "APPROVED_PREFIXES" in self.rt.code_content

    def test_code_uses_in_memory_exporter(self):
        assert "InMemorySpanExporter" in self.rt.code_content

    def test_expected_pattern(self):
        assert "otel semconv violation confirmed" in self.rt.expected_failure_pattern

    def test_key_fallback_when_no_backtick_in_message(self):
        v = _make_violation(
            rule_id="otel-semantic-convention",
            message="no backtick message here",
        )
        rt = _synth_otel_semantic(v)
        assert "custom.adhoc_key" in rt.code_content

    def test_empty_context_falls_back(self):
        v = _make_violation(
            rule_id="otel-semantic-convention",
            ast_context="",
            message="key ``x.y`` bad",
        )
        rt = _synth_otel_semantic(v)
        assert "some_function" in rt.code_content


# ---------------------------------------------------------------------------
# synthesize_repro_test — public API
# ---------------------------------------------------------------------------

class TestSynthesizeReproTest:
    def test_goroutine_leak_dispatches_to_go(self):
        v = _make_violation(rule_id="goroutine-leak", file_path="a.go")
        rt = synthesize_repro_test(v)
        assert rt.language == "go"
        assert rt.rule_id == "goroutine-leak"

    def test_unbuffered_channel_dispatches_to_go(self):
        v = _make_violation(rule_id="unbuffered-channel-in-loop", file_path="a.go")
        rt = synthesize_repro_test(v)
        assert rt.language == "go"

    def test_nil_check_go_dispatch(self):
        v = _make_violation(rule_id="nil-check-boundary", file_path="api.go")
        rt = synthesize_repro_test(v)
        assert rt.language == "go"
        assert rt.file_name == "repro_test.go"

    def test_nil_check_python_dispatch(self):
        v = _make_violation(rule_id="nil-check-boundary", file_path="api.py")
        rt = synthesize_repro_test(v)
        assert rt.language == "python"
        assert rt.file_name == "test_repro.py"

    def test_otel_semantic_dispatches_to_python(self):
        v = _make_violation(
            rule_id="otel-semantic-convention",
            file_path="tracing.py",
            message="key ``bad.key`` found",
            severity=Severity.WARNING,
        )
        rt = synthesize_repro_test(v)
        assert rt.language == "python"

    def test_unknown_rule_raises_value_error(self):
        v = _make_violation(rule_id="totally-unknown-rule")
        with pytest.raises(ValueError, match="totally-unknown-rule"):
            synthesize_repro_test(v)

    def test_repo_index_none_is_accepted(self):
        v = _make_violation(rule_id="goroutine-leak")
        rt = synthesize_repro_test(v, repo_index=None)
        assert rt is not None

    def test_nil_check_no_extension_falls_back_to_go(self):
        # File path with no extension → should default to Go
        v = _make_violation(rule_id="nil-check-boundary", file_path="Makefile")
        rt = synthesize_repro_test(v)
        assert rt.language == "go"

    def test_all_repro_tests_have_nonempty_content(self):
        rule_ids_and_files = [
            ("goroutine-leak", "a.go"),
            ("unbuffered-channel-in-loop", "a.go"),
            ("nil-check-boundary", "a.go"),
            ("nil-check-boundary", "a.py"),
            ("otel-semantic-convention", "a.py"),
        ]
        for rule_id, fp in rule_ids_and_files:
            v = _make_violation(
                rule_id=rule_id,
                file_path=fp,
                message="key ``custom.x`` bad" if rule_id == "otel-semantic-convention" else "msg",
                severity=Severity.WARNING if rule_id == "otel-semantic-convention" else Severity.CRITICAL,
            )
            rt = synthesize_repro_test(v)
            assert rt.code_content, f"Empty code_content for {rule_id}"
            assert rt.expected_failure_pattern, f"Empty pattern for {rule_id}"
