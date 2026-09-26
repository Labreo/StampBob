"""
local_demo.py — StampBob zero-credential end-to-end interactive demo.

Steps executed by `make local`
-------------------------------
1. Offline eval gate     — runs the 50-fixture benchmark in --no-sandbox mode
                           (fast, static-analysis-only, zero cloud credentials).
2. Breaking PR simulation — audits a deliberate goroutine-leak diff through
                            InvariantOracle and prints the full violation report.
3. Repro synthesis        — synthesizes the executable reproduction test for the
                            detected violation and runs it in the sandbox.
4. Tactical Review Command Center — starts the FastAPI server at localhost:8000
                            and opens the dashboard in the default browser.

All steps run locally; no IBM Cloud API key or internet connection required.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
import webbrowser

# ---------------------------------------------------------------------------
# Ensure project root is importable regardless of CWD.
# ---------------------------------------------------------------------------
_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---------------------------------------------------------------------------
# Load .env at module startup (stdlib only — no python-dotenv required)
# ---------------------------------------------------------------------------
_env_file = os.path.join(_ROOT, ".env")
if os.path.isfile(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
    del _f, _line, _k, _v
del _env_file


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

_RESET = "\033[0m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_DIM = "\033[2m"


def _banner(text: str) -> None:
    width = 72
    print(f"\n{_BOLD}{_CYAN}{'─' * width}{_RESET}")
    print(f"{_BOLD}{_CYAN}  {text}{_RESET}")
    print(f"{_BOLD}{_CYAN}{'─' * width}{_RESET}")


def _ok(msg: str) -> None:
    print(f"  {_GREEN}✓{_RESET}  {msg}")


def _info(msg: str) -> None:
    print(f"  {_DIM}·{_RESET}  {msg}")


def _warn(msg: str) -> None:
    print(f"  {_YELLOW}⚠{_RESET}  {msg}")


def _fail(msg: str) -> None:
    print(f"  {_RED}✗{_RESET}  {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Demo diff — deliberate goroutine-leak injection
# ---------------------------------------------------------------------------

_DEMO_DIFF = textwrap.dedent("""\
    diff --git a/internal/worker/pool.go b/internal/worker/pool.go
    --- a/internal/worker/pool.go
    +++ b/internal/worker/pool.go
    @@ -18,6 +18,14 @@ func NewWorkerPool(size int) *WorkerPool {
     \tp := &WorkerPool{size: size}
     \tp.mu.Lock()
     \tdefer p.mu.Unlock()
    +\tfor i := 0; i < size; i++ {
    +\t\tgo func() {
    +\t\t\tfor job := range p.queue {
    +\t\t\t\tjob.Run()
    +\t\t\t}
    +\t\t}()
    +\t}
    +\tp.started = true
     \treturn p
     }
    """)


# ---------------------------------------------------------------------------
# Step 1 — Offline eval gate
# ---------------------------------------------------------------------------

def step_eval() -> bool:
    _banner("STEP 1 / 3 — Offline Eval Gate (50 golden fixtures, no sandbox)")
    import io, contextlib
    from src.eval.benchmark_harness import run_offline_eval

    buf = io.StringIO()
    passed = False
    try:
        with contextlib.redirect_stdout(buf):
            summary = run_offline_eval(
                dataset_path="fixtures/golden_prs",
                min_precision_threshold=0.92,
                run_sandbox=False,
            )
        passed = summary.passed_ci_gate
    except SystemExit:
        passed = False

    for line in buf.getvalue().splitlines():
        if line.startswith("="):
            print(f"  {_DIM}{line}{_RESET}")
        elif "PASSED" in line:
            print(f"  {_GREEN}{line}{_RESET}")
        elif "FAILED" in line or "✗" in line:
            print(f"  {_RED}{line}{_RESET}")
        elif "✓" in line:
            print(f"  {_GREEN}{line}{_RESET}")
        else:
            print(f"  {line}")

    if passed:
        _ok("Eval gate PASSED — precision ≥ 92 %")
    else:
        _warn("Eval gate did not reach the 92 % precision threshold in this run.")
    return passed


# ---------------------------------------------------------------------------
# Step 2 — Simulate breaking PR
# ---------------------------------------------------------------------------

def step_simulate_pr() -> list:
    _banner("STEP 2 / 3 — Simulating Incoming Breaking PR (goroutine-leak injection)")
    from src.engine.context_indexer import RepositoryIndex
    from src.engine.invariant_oracle import InvariantOracle

    _info("Diff injected:")
    for line in _DEMO_DIFF.splitlines()[:10]:
        print(f"    {_DIM}{line}{_RESET}")
    print(f"    {_DIM}…{_RESET}")
    print()

    oracle = InvariantOracle()
    repo_index = RepositoryIndex(root=".")
    violations = oracle.audit_diff(_DEMO_DIFF, repo_index)

    if violations:
        for v in violations:
            print(f"  {_RED}[{v.severity.value}]{_RESET} {_BOLD}{v.rule_id}{_RESET}")
            print(f"         File    : {v.file_path}:{v.line_range[0]}-{v.line_range[1]}")
            print(f"         Message : {v.message}")
            print(f"         Context : {v.ast_context}")
            print(f"         Fix     : {v.remediation}")
            print()
        _ok(f"{len(violations)} violation(s) detected — PR would be BLOCKED")
    else:
        _warn("No violations detected in demo diff — check rule configuration.")

    return violations


# ---------------------------------------------------------------------------
# Step 3 — Synthesize & execute repro test
# ---------------------------------------------------------------------------

def step_repro(violations: list) -> None:
    _banner("STEP 3 / 3 — Repro Synthesis & Sandbox Execution")

    if not violations:
        _warn("No violations to synthesize — skipping sandbox step.")
        return

    from src.generator.repro_synthesizer import synthesize_repro_test
    from src.generator.sandbox_runner import run_repro_in_sandbox

    v = violations[0]
    _info(f"Synthesizing repro test for rule: {_BOLD}{v.rule_id}{_RESET}")

    try:
        repro = synthesize_repro_test(v)
    except ValueError as exc:
        _warn(f"No synthesizer for rule {v.rule_id!r}: {exc}")
        return

    _ok(f"Synthesized {repro.file_name} ({repro.language})")
    _info("Executing in isolated sandbox …")

    result = run_repro_in_sandbox(repro)

    status_color = _GREEN if result.confirmed else _YELLOW
    print(f"  {status_color}Sandbox status : {result.status}{_RESET}")
    print(f"  {_DIM}Exit code      : {result.exit_code}{_RESET}")
    print(f"  {_DIM}Duration       : {result.duration_ms:.0f} ms{_RESET}")

    if result.stdout.strip():
        print(f"\n  {_DIM}stdout:{_RESET}")
        for line in result.stdout.strip().splitlines()[:12]:
            print(f"    {_DIM}{line}{_RESET}")

    if result.confirmed:
        _ok("Violation CONFIRMED by sandbox — StampBob will block this PR")
    else:
        _warn(f"Sandbox could not confirm ({result.status}). "
              "This may indicate the runtime (go/pytest) is not installed.")


# ---------------------------------------------------------------------------
# Start the web server
# ---------------------------------------------------------------------------

def step_start_server() -> None:
    _banner("Tactical Review Command Center — http://localhost:8000")
    print(f"\n  {_GREEN}Opening browser …{_RESET}")
    print(f"  {_DIM}Press Ctrl+C to stop.{_RESET}\n")

    # Small delay so the server socket is bound before the browser opens.
    def _open_browser() -> None:
        time.sleep(1.5)
        webbrowser.open("http://localhost:8000")

    import threading
    t = threading.Thread(target=_open_browser, daemon=True)
    t.start()

    import uvicorn
    from src.web.server import app
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"\n{_BOLD}{'=' * 72}{_RESET}")
    print(f"{_BOLD}  🔏  StampBob — The 90-Second Demo{_RESET}")
    print(f"{_BOLD}  Zero credentials required · all analysis runs offline{_RESET}")
    print(f"{_BOLD}{'=' * 72}{_RESET}\n")

    step_eval()
    violations = step_simulate_pr()
    step_repro(violations)
    step_start_server()


if __name__ == "__main__":
    main()
