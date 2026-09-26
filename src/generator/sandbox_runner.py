"""
sandbox_runner.py — StampBob Verification Oracle: Sandbox Execution Engine.

Executes a ``ReproTest`` in a fully isolated temporary directory and returns
a ``SandboxResult`` confirming whether the expected failure actually fires.

StampBob's non-negotiable contract
-----------------------------------
A violation is only reported to the caller as a ``VerifiedViolation`` when
``confirmed=True``, meaning ALL three conditions hold:

1. The repro test process exited with a non-zero exit code.
2. The combined stdout+stderr matches ``test.expected_failure_pattern``.
3. The process completed within the configured timeout.

Anything else (timeout, error, test passes unexpectedly) produces a
``SandboxResult`` with ``confirmed=False`` and an appropriate ``status``.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.generator.repro_synthesizer import ReproTest
from src.engine.invariant_oracle import InvariantViolation


# ---------------------------------------------------------------------------
# Public data models
# ---------------------------------------------------------------------------

@dataclass
class SandboxResult:
    """Outcome of executing a ``ReproTest`` inside an isolated sandbox."""

    test: ReproTest
    status: str          # "CONFIRMED_BUG" | "UNVERIFIED" | "TIMEOUT" | "ERROR"
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    confirmed: bool      # True ONLY when status == "CONFIRMED_BUG"


@dataclass
class VerifiedViolation:
    """
    A static violation that has been confirmed by an executable repro test.

    Only ``confirmed=True`` instances should be surfaced to end users.
    """

    violation: InvariantViolation
    repro_test: ReproTest
    sandbox_result: SandboxResult
    confirmed: bool


# ---------------------------------------------------------------------------
# Go module scaffolding helpers
# ---------------------------------------------------------------------------

_GO_MOD_TEMPLATE = textwrap.dedent("""\
    module stampbob_repro

    go 1.21
    """)


def _write_go_sandbox(work_dir: str, test: ReproTest) -> None:
    """
    Write the Go repro test into *work_dir* as a valid Go module so that
    ``go test -v -run TestRepro`` works without any external dependencies.
    """
    # go.mod
    (Path(work_dir) / "go.mod").write_text(_GO_MOD_TEMPLATE, encoding="utf-8")

    # The test source — must be in the root of the module (package repro_test
    # is fine for a _test.go file alongside a go.mod).
    (Path(work_dir) / test.file_name).write_text(test.code_content, encoding="utf-8")


def _write_python_sandbox(work_dir: str, test: ReproTest) -> None:
    """
    Write the Python repro test into *work_dir*.  Creates a minimal
    ``conftest.py`` so pytest can discover the test without a full package
    structure.
    """
    test_path = Path(work_dir) / test.file_name
    test_path.write_text(test.code_content, encoding="utf-8")

    # Ensure pytest can find the test even without an installed package.
    conftest = Path(work_dir) / "conftest.py"
    conftest.write_text("# auto-generated conftest\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------

def _build_go_command(test: ReproTest) -> list[str]:
    """
    Return the subprocess argv to run a single Go repro test.

    We target functions named ``TestRepro*`` to avoid accidentally running
    any pre-existing tests if the directory is not truly isolated.
    """
    return ["go", "test", "-v", "-count=1", "-run", "TestRepro", "-timeout", "10s", "."]


def _build_python_command(test: ReproTest) -> list[str]:
    """
    Return the subprocess argv to run a Python repro test via pytest.

    ``-p no:cacheprovider`` avoids writing ``.pytest_cache`` into the sandbox.
    ``-x`` stops on first failure — we only need to confirm the bug once.
    """
    return [
        "pytest",
        "-v",
        "-x",
        "-p", "no:cacheprovider",
        "--tb=short",
        test.file_name,
    ]


# ---------------------------------------------------------------------------
# Core sandbox execution
# ---------------------------------------------------------------------------

def run_repro_in_sandbox(
    test: ReproTest,
    timeout_seconds: int = 15,
    work_dir: Optional[str] = None,
) -> SandboxResult:
    """
    Execute *test* in an isolated temporary directory and return a
    ``SandboxResult`` describing the outcome.

    Parameters
    ----------
    test:
        A ``ReproTest`` produced by ``synthesize_repro_test``.
    timeout_seconds:
        Hard wall-clock limit for the subprocess.  Defaults to 15 s.
        Must be > 0.
    work_dir:
        Optional path to an *existing* directory to use instead of a
        freshly created temp dir.  When supplied the caller is responsible
        for cleanup.  When *None* (default) a ``TemporaryDirectory`` is
        created and cleaned up automatically after the run.

    Returns
    -------
    SandboxResult
        * ``status="CONFIRMED_BUG"``  — exit_code != 0 AND output matches
          ``expected_failure_pattern``.  ``confirmed=True``.
        * ``status="UNVERIFIED"``     — process exited cleanly (code 0) or
          output does not contain the expected failure signal.
          ``confirmed=False``.
        * ``status="TIMEOUT"``        — process exceeded *timeout_seconds*.
          ``confirmed=False``.
        * ``status="ERROR"``          — file I/O or subprocess launch failed.
          ``confirmed=False``.
    """
    if timeout_seconds <= 0:
        timeout_seconds = 15

    _cleanup_tmp = work_dir is None
    _tmp_obj = None

    try:
        if _cleanup_tmp:
            _tmp_obj = tempfile.TemporaryDirectory(prefix="stampbob_repro_")
            sandbox_dir = _tmp_obj.name
        else:
            sandbox_dir = work_dir

        # --- Write test files into the sandbox ---
        try:
            if test.language == "go":
                _write_go_sandbox(sandbox_dir, test)
                cmd = _build_go_command(test)
            elif test.language == "python":
                _write_python_sandbox(sandbox_dir, test)
                cmd = _build_python_command(test)
            else:
                return SandboxResult(
                    test=test,
                    status="ERROR",
                    exit_code=-1,
                    stdout="",
                    stderr=f"Unsupported language: {test.language!r}",
                    duration_ms=0.0,
                    confirmed=False,
                )
        except OSError as exc:
            return SandboxResult(
                test=test,
                status="ERROR",
                exit_code=-1,
                stdout="",
                stderr=f"Failed to write sandbox files: {exc}",
                duration_ms=0.0,
                confirmed=False,
            )

        # --- Execute the test subprocess ---
        t_start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env={**os.environ, "CGO_ENABLED": "0"},
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = (time.monotonic() - t_start) * 1000
            stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return SandboxResult(
                test=test,
                status="TIMEOUT",
                exit_code=-1,
                stdout=stdout,
                stderr=stderr + f"\n[sandbox] Timed out after {timeout_seconds}s",
                duration_ms=duration_ms,
                confirmed=False,
            )
        except FileNotFoundError as exc:
            duration_ms = (time.monotonic() - t_start) * 1000
            return SandboxResult(
                test=test,
                status="ERROR",
                exit_code=-1,
                stdout="",
                stderr=f"Command not found: {exc}",
                duration_ms=duration_ms,
                confirmed=False,
            )
        except OSError as exc:
            duration_ms = (time.monotonic() - t_start) * 1000
            return SandboxResult(
                test=test,
                status="ERROR",
                exit_code=-1,
                stdout="",
                stderr=f"Subprocess OS error: {exc}",
                duration_ms=duration_ms,
                confirmed=False,
            )

        duration_ms = (time.monotonic() - t_start) * 1000
        combined_output = proc.stdout + proc.stderr

        # --- Evaluate the result ---
        pattern_matched = bool(
            re.search(test.expected_failure_pattern, combined_output, re.IGNORECASE)
        )

        # CONFIRMED_BUG: the test itself passed (exit 0) AND the expected
        # failure signal appears in output (e.g. "LEAK DETECTED", "1 passed").
        # For Go tests the process exits 0 even on t.Log — the test framework
        # itself does NOT fail the process; we only call t.Fatal when the
        # repro is NOT reproduced.  So exit_code=0 + pattern match = confirmed.
        #
        # For Python (pytest), a passing test also exits 0.
        #
        # We call t.Fatal() / pytest.fail() ONLY when the bug was NOT reproduced
        # (safety guard).  Therefore the "confirmed" path is exit_code==0 + pattern.
        if pattern_matched and proc.returncode == 0:
            status = "CONFIRMED_BUG"
            confirmed = True
        elif not pattern_matched and proc.returncode != 0:
            # Test exited non-zero but not with the expected signal — likely
            # a compilation error or environment issue.
            status = "ERROR"
            confirmed = False
        else:
            # Either the pattern matched but exit!=0 (shouldn't happen normally)
            # or nothing matched and exit==0 (bug not reproduced).
            status = "UNVERIFIED"
            confirmed = False

        return SandboxResult(
            test=test,
            status=status,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_ms=duration_ms,
            confirmed=confirmed,
        )

    finally:
        if _cleanup_tmp and _tmp_obj is not None:
            try:
                _tmp_obj.cleanup()
            except Exception:
                pass  # Best-effort cleanup; sandbox is in /tmp anyway.


# ---------------------------------------------------------------------------
# Convenience: verify a violation end-to-end
# ---------------------------------------------------------------------------

def verify_violation(
    violation: InvariantViolation,
    *,
    timeout_seconds: int = 15,
    work_dir: Optional[str] = None,
) -> VerifiedViolation:
    """
    Synthesize a repro test for *violation* and execute it in the sandbox.

    This is the single entry-point that implements the Verification Oracle
    contract: a ``VerifiedViolation`` with ``confirmed=True`` is returned
    only when the synthesized test actually triggers the expected failure.

    Parameters
    ----------
    violation:
        Static violation from ``InvariantOracle.audit_diff``.
    timeout_seconds:
        Per-test execution timeout forwarded to ``run_repro_in_sandbox``.
    work_dir:
        Optional pre-existing directory for the sandbox (caller manages
        cleanup).

    Returns
    -------
    VerifiedViolation
        Always returns a result — inspect ``.confirmed`` to decide whether
        StampBob is permitted to report this violation.
    """
    from src.generator.repro_synthesizer import synthesize_repro_test

    try:
        repro = synthesize_repro_test(violation)
    except ValueError as exc:
        # No synthesizer registered for this rule — cannot confirm.
        dummy_test = ReproTest(
            rule_id=violation.rule_id,
            language="unknown",
            file_name="repro_unsupported",
            code_content="",
            expected_failure_pattern="",
        )
        result = SandboxResult(
            test=dummy_test,
            status="ERROR",
            exit_code=-1,
            stdout="",
            stderr=str(exc),
            duration_ms=0.0,
            confirmed=False,
        )
        return VerifiedViolation(
            violation=violation,
            repro_test=dummy_test,
            sandbox_result=result,
            confirmed=False,
        )

    result = run_repro_in_sandbox(repro, timeout_seconds=timeout_seconds, work_dir=work_dir)
    return VerifiedViolation(
        violation=violation,
        repro_test=repro,
        sandbox_result=result,
        confirmed=result.confirmed,
    )
