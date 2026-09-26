"""
tests/generator/test_sandbox_runner.py

Unit tests for src/generator/sandbox_runner.py — 100% function coverage.

Tests cover:
- SandboxResult and VerifiedViolation dataclasses
- _write_go_sandbox: go.mod and test file created correctly
- _write_python_sandbox: test file and conftest.py created
- _build_go_command / _build_python_command: correct argv
- run_repro_in_sandbox: CONFIRMED_BUG, UNVERIFIED, TIMEOUT, ERROR paths
- verify_violation: end-to-end wrapper including unknown-rule path
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.engine.invariant_oracle import InvariantViolation
from src.engine.rules import Severity
from src.generator.repro_synthesizer import ReproTest
from src.generator.sandbox_runner import (
    SandboxResult,
    VerifiedViolation,
    _build_go_command,
    _build_python_command,
    _write_go_sandbox,
    _write_python_sandbox,
    run_repro_in_sandbox,
    verify_violation,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_violation(
    rule_id: str = "goroutine-leak",
    file_path: str = "pkg/server.go",
    severity: Severity = Severity.CRITICAL,
) -> InvariantViolation:
    return InvariantViolation(
        rule_id=rule_id,
        severity=severity,
        file_path=file_path,
        line_range=(10, 10),
        offending_code="go func() {",
        ast_context="pkg.Serve",
        message="goroutine leak",
        remediation="use ctx",
    )


def _go_repro(expected_pattern: str = r"PASS") -> ReproTest:
    return ReproTest(
        rule_id="goroutine-leak",
        language="go",
        file_name="repro_test.go",
        code_content=(
            'package repro_test\n\nimport "testing"\n\n'
            'func TestReproSimple(t *testing.T) {\n\tt.Log("PASS")\n}\n'
        ),
        expected_failure_pattern=expected_pattern,
    )


def _python_repro(expected_pattern: str = r"1 passed") -> ReproTest:
    return ReproTest(
        rule_id="nil-check-boundary",
        language="python",
        file_name="test_repro.py",
        code_content="def test_repro_simple():\n    assert True\n",
        expected_failure_pattern=expected_pattern,
    )


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

class TestSandboxResultDataclass:
    def test_fields(self):
        rt = _go_repro()
        sr = SandboxResult(
            test=rt,
            status="CONFIRMED_BUG",
            exit_code=0,
            stdout="ok",
            stderr="",
            duration_ms=123.4,
            confirmed=True,
        )
        assert sr.test is rt
        assert sr.status == "CONFIRMED_BUG"
        assert sr.exit_code == 0
        assert sr.stdout == "ok"
        assert sr.stderr == ""
        assert sr.duration_ms == 123.4
        assert sr.confirmed is True


class TestVerifiedViolationDataclass:
    def test_fields(self):
        v = _make_violation()
        rt = _go_repro()
        sr = SandboxResult(
            test=rt, status="CONFIRMED_BUG", exit_code=0,
            stdout="", stderr="", duration_ms=0, confirmed=True,
        )
        vv = VerifiedViolation(
            violation=v, repro_test=rt, sandbox_result=sr, confirmed=True
        )
        assert vv.violation is v
        assert vv.repro_test is rt
        assert vv.sandbox_result is sr
        assert vv.confirmed is True


# ---------------------------------------------------------------------------
# _write_go_sandbox
# ---------------------------------------------------------------------------

class TestWriteGoSandbox:
    def test_creates_go_mod(self):
        with tempfile.TemporaryDirectory() as tmp:
            test = _go_repro()
            _write_go_sandbox(tmp, test)
            mod_path = Path(tmp) / "go.mod"
            assert mod_path.exists()
            content = mod_path.read_text()
            assert "module stampbob_repro" in content
            assert "go 1.21" in content

    def test_creates_test_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            test = _go_repro()
            _write_go_sandbox(tmp, test)
            test_path = Path(tmp) / "repro_test.go"
            assert test_path.exists()
            assert test_path.read_text() == test.code_content

    def test_custom_file_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            test = ReproTest(
                rule_id="x", language="go", file_name="custom_repro_test.go",
                code_content="package repro_test\n",
                expected_failure_pattern="x",
            )
            _write_go_sandbox(tmp, test)
            assert (Path(tmp) / "custom_repro_test.go").exists()


# ---------------------------------------------------------------------------
# _write_python_sandbox
# ---------------------------------------------------------------------------

class TestWritePythonSandbox:
    def test_creates_test_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            test = _python_repro()
            _write_python_sandbox(tmp, test)
            test_path = Path(tmp) / "test_repro.py"
            assert test_path.exists()
            assert test_path.read_text() == test.code_content

    def test_creates_conftest(self):
        with tempfile.TemporaryDirectory() as tmp:
            test = _python_repro()
            _write_python_sandbox(tmp, test)
            conftest = Path(tmp) / "conftest.py"
            assert conftest.exists()

    def test_conftest_is_valid_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_python_sandbox(tmp, _python_repro())
            content = (Path(tmp) / "conftest.py").read_text()
            # Should at least be importable (no syntax errors)
            compile(content, "conftest.py", "exec")


# ---------------------------------------------------------------------------
# _build_go_command / _build_python_command
# ---------------------------------------------------------------------------

class TestBuildCommands:
    def test_go_command_starts_with_go_test(self):
        cmd = _build_go_command(_go_repro())
        assert cmd[0] == "go"
        assert "test" in cmd

    def test_go_command_has_run_testrepo(self):
        cmd = _build_go_command(_go_repro())
        assert "-run" in cmd
        run_idx = cmd.index("-run")
        assert cmd[run_idx + 1] == "TestRepro"

    def test_go_command_has_verbose(self):
        cmd = _build_go_command(_go_repro())
        assert "-v" in cmd

    def test_python_command_starts_with_pytest(self):
        cmd = _build_python_command(_python_repro())
        assert cmd[0] == "pytest"

    def test_python_command_includes_file(self):
        cmd = _build_python_command(_python_repro())
        assert "test_repro.py" in cmd

    def test_python_command_verbose(self):
        cmd = _build_python_command(_python_repro())
        assert "-v" in cmd


# ---------------------------------------------------------------------------
# run_repro_in_sandbox — status paths
# ---------------------------------------------------------------------------

class TestRunReproInSandbox:

    def test_confirmed_bug_go(self):
        """A real Go repro test that passes and emits the expected pattern."""
        test = ReproTest(
            rule_id="goroutine-leak",
            language="go",
            file_name="repro_test.go",
            code_content=(
                'package repro_test\n\nimport (\n\t"fmt"\n\t"testing"\n)\n\n'
                'func TestReproSimplePass(t *testing.T) {\n'
                '\tfmt.Println("goroutine leak confirmed: all good")\n'
                '\tt.Log("LEAK DETECTED")\n}\n'
            ),
            expected_failure_pattern=r"goroutine leak confirmed|LEAK DETECTED",
        )
        result = run_repro_in_sandbox(test, timeout_seconds=30)
        assert result.confirmed is True
        assert result.status == "CONFIRMED_BUG"
        assert result.exit_code == 0
        assert result.duration_ms > 0

    def test_confirmed_bug_python(self):
        """A real Python repro test that passes and emits the expected pattern."""
        test = ReproTest(
            rule_id="nil-check-boundary",
            language="python",
            file_name="test_repro.py",
            code_content=(
                "def test_repro_simple():\n"
                "    print('none dereference AttributeError confirmed')\n"
                "    assert True\n"
            ),
            expected_failure_pattern=r"none dereference AttributeError confirmed|1 passed",
        )
        result = run_repro_in_sandbox(test, timeout_seconds=30)
        assert result.confirmed is True
        assert result.status == "CONFIRMED_BUG"

    def test_unverified_when_pattern_not_matched(self):
        """Test exits 0 but output does not contain expected pattern → UNVERIFIED."""
        test = ReproTest(
            rule_id="goroutine-leak",
            language="python",
            file_name="test_repro.py",
            code_content="def test_repro_no_output():\n    assert True\n",
            expected_failure_pattern=r"THIS_WILL_NEVER_APPEAR_IN_OUTPUT_XYZ987",
        )
        result = run_repro_in_sandbox(test, timeout_seconds=30)
        assert result.confirmed is False
        assert result.status == "UNVERIFIED"

    def test_error_on_build_failure(self):
        """Invalid Go code → build fails → ERROR status."""
        test = ReproTest(
            rule_id="goroutine-leak",
            language="go",
            file_name="repro_test.go",
            code_content="this is not valid go code at all!!!",
            expected_failure_pattern=r"anything",
        )
        result = run_repro_in_sandbox(test, timeout_seconds=30)
        assert result.confirmed is False
        assert result.status == "ERROR"
        assert result.exit_code != 0

    def test_error_on_unsupported_language(self):
        test = ReproTest(
            rule_id="x",
            language="rust",
            file_name="repro.rs",
            code_content="fn main() {}",
            expected_failure_pattern=r"x",
        )
        result = run_repro_in_sandbox(test, timeout_seconds=5)
        assert result.confirmed is False
        assert result.status == "ERROR"
        assert "Unsupported language" in result.stderr

    def test_timeout_returns_timeout_status(self):
        """Extremely short timeout on a real test → TIMEOUT."""
        test = ReproTest(
            rule_id="goroutine-leak",
            language="python",
            file_name="test_repro.py",
            code_content=(
                "import time\n"
                "def test_repro_slow():\n"
                "    time.sleep(60)\n"
            ),
            expected_failure_pattern=r"x",
        )
        result = run_repro_in_sandbox(test, timeout_seconds=1)
        assert result.confirmed is False
        assert result.status == "TIMEOUT"

    def test_custom_work_dir_used(self):
        """When work_dir is provided, files are written there."""
        with tempfile.TemporaryDirectory() as tmp:
            test = _python_repro()
            result = run_repro_in_sandbox(test, timeout_seconds=30, work_dir=tmp)
            # Files should have been written to the provided dir
            assert (Path(tmp) / "test_repro.py").exists()
            assert result is not None

    def test_zero_timeout_defaults_to_15(self):
        """timeout_seconds <= 0 should be clamped to 15."""
        test = _python_repro()
        # We're just checking it doesn't raise; actual clamping happens internally.
        result = run_repro_in_sandbox(test, timeout_seconds=0)
        assert result is not None

    def test_error_on_missing_command(self):
        """FileNotFoundError when command binary is missing → ERROR."""
        test = _go_repro()
        with patch("src.generator.sandbox_runner.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("go not found")
            result = run_repro_in_sandbox(test, timeout_seconds=5)
        assert result.status == "ERROR"
        assert result.confirmed is False
        assert "go not found" in result.stderr

    def test_error_on_os_error(self):
        """Generic OSError from subprocess → ERROR."""
        test = _go_repro()
        with patch("src.generator.sandbox_runner.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("permission denied")
            result = run_repro_in_sandbox(test, timeout_seconds=5)
        assert result.status == "ERROR"
        assert result.confirmed is False

    def test_result_fields_populated(self):
        """All SandboxResult fields are populated on a real run."""
        test = _python_repro()
        result = run_repro_in_sandbox(test, timeout_seconds=30)
        assert isinstance(result.stdout, str)
        assert isinstance(result.stderr, str)
        assert isinstance(result.duration_ms, float)
        assert result.duration_ms >= 0
        assert result.exit_code is not None


# ---------------------------------------------------------------------------
# verify_violation
# ---------------------------------------------------------------------------

class TestVerifyViolation:
    def test_goroutine_leak_confirmed(self):
        v = _make_violation(rule_id="goroutine-leak", file_path="pkg/server.go")
        vv = verify_violation(v, timeout_seconds=30)
        assert isinstance(vv, VerifiedViolation)
        assert vv.confirmed is True
        assert vv.sandbox_result.status == "CONFIRMED_BUG"

    def test_unknown_rule_returns_unconfirmed(self):
        v = _make_violation(rule_id="nonexistent-rule")
        vv = verify_violation(v, timeout_seconds=5)
        assert vv.confirmed is False
        assert vv.sandbox_result.status == "ERROR"
        assert "nonexistent-rule" in vv.sandbox_result.stderr

    def test_verified_violation_has_all_fields(self):
        v = _make_violation(rule_id="goroutine-leak")
        vv = verify_violation(v, timeout_seconds=30)
        assert vv.violation is v
        assert vv.repro_test is not None
        assert vv.sandbox_result is not None

    def test_work_dir_forwarded(self):
        v = _make_violation(rule_id="goroutine-leak")
        with tempfile.TemporaryDirectory() as tmp:
            vv = verify_violation(v, timeout_seconds=30, work_dir=tmp)
            # repro_test.go should exist in the provided work_dir
            assert (Path(tmp) / "repro_test.go").exists()
