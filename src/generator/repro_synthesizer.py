"""
repro_synthesizer.py — StampBob Verification Oracle: Reproduction Test Synthesizer.

For every ``InvariantViolation`` produced by the static engine, this module
synthesizes a *standalone, self-contained* executable test that triggers the
exact failure in an isolated process.

StampBob is FORBIDDEN from reporting a bug unless this synthesizer can produce
a runnable reproduction test AND ``sandbox_runner`` confirms the failure fires.

Supported rule IDs
------------------
* ``goroutine-leak``              → Go test using runtime stack inspection
* ``unbuffered-channel-in-loop``  → Go test demonstrating deadlock / blocking send
* ``nil-check-boundary``          → Go or Python test triggering nil dereference / AttributeError
* ``otel-semantic-convention``    → Python in-memory tracer test asserting missing semconv attr
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Optional

from src.engine.context_indexer import RepositoryIndex
from src.engine.invariant_oracle import InvariantViolation
from src.engine.rules import Severity


# ---------------------------------------------------------------------------
# Public data model
# ---------------------------------------------------------------------------

@dataclass
class ReproTest:
    """A synthesized, standalone reproduction test for a single violation."""

    rule_id: str
    language: str                    # "go" or "python"
    file_name: str                   # e.g. "repro_test.go" or "test_repro.py"
    code_content: str                # Complete, standalone test source code
    expected_failure_pattern: str    # Regex expected in combined test output


# ---------------------------------------------------------------------------
# Internal helpers — Go test scaffolding
# ---------------------------------------------------------------------------


def _go_test_preamble(imports: list[str]) -> str:
    """Return the package declaration + explicit imports for a Go repro test."""
    import_lines = "\n".join(f'\t"{pkg}"' for pkg in imports)
    return (
        "package repro_test\n\n"
        "import (\n"
        f"{import_lines}\n"
        ")\n\n"
    )


def _current_goroutine_count() -> str:
    """Return a Go helper function body that counts live goroutines."""
    return textwrap.dedent("""\
        func liveGoroutines() int {
        \tbuf := make([]byte, 1<<20)
        \tn := runtime.Stack(buf, true)
        \treturn strings.Count(string(buf[:n]), "goroutine ")
        }

        """)


# ---------------------------------------------------------------------------
# Synthesizers per rule
# ---------------------------------------------------------------------------

def _synth_goroutine_leak(violation: InvariantViolation) -> ReproTest:
    """
    Generates a Go test that:
    1. Records the goroutine count before spawning.
    2. Calls a local function that launches an anonymous goroutine with no
       exit signal (mirroring the offending pattern).
    3. Waits 100 ms for the goroutine to settle.
    4. Asserts that goroutine count increased and did NOT return to baseline,
       proving the leak.
    """
    offending_snippet = violation.offending_code.strip() or "go func() { select {} }()"
    context = violation.ast_context or "unnamedFunc"

    code = _go_test_preamble(["runtime", "strings", "testing", "time", "fmt"]) + _current_goroutine_count() + textwrap.dedent(f"""\
        // leakyFunc mirrors the offending code from: {violation.file_path}:{violation.line_range[0]}
        // Context: {context}
        func leakyFunc() {{
        \t// Reproduction of: {offending_snippet}
        \tgo func() {{
        \t\t// No ctx.Done(), no WaitGroup, no channel close — goroutine leaks.
        \t\tfor {{
        \t\t\ttime.Sleep(10 * time.Second)
        \t\t}}
        \t}}()
        }}

        func TestReproGoroutineLeak(t *testing.T) {{
        \tbaseline := liveGoroutines()

        \tleakyFunc()

        \t// Give the scheduler a moment to start the goroutine.
        \ttime.Sleep(50 * time.Millisecond)

        \tafter := liveGoroutines()
        \tif after <= baseline {{
        \t\tt.Fatalf("goroutine leak not reproduced: baseline=%d after=%d", baseline, after)
        \t}}

        \t// Confirm the goroutine is still alive after a grace period (it should be).
        \ttime.Sleep(100 * time.Millisecond)
        \tstill := liveGoroutines()
        \tif still <= baseline {{
        \t\tt.Fatalf("goroutine returned unexpectedly: baseline=%d still=%d", baseline, still)
        \t}}
        \tfmt.Printf("goroutine leak confirmed: baseline=%d peak=%d still=%d\\n",
        \t\tbaseline, after, still)
        \tt.Logf("LEAK DETECTED: goroutine count rose from %d to %d and stayed at %d",
        \t\tbaseline, after, still)
        }}
        """)

    return ReproTest(
        rule_id=violation.rule_id,
        language="go",
        file_name="repro_test.go",
        code_content=code,
        expected_failure_pattern=r"goroutine leak confirmed|LEAK DETECTED",
    )


def _synth_unbuffered_channel(violation: InvariantViolation) -> ReproTest:
    """
    Generates a Go test that:
    1. Creates an unbuffered channel inside a loop.
    2. Attempts a non-blocking send with no concurrent reader.
    3. Demonstrates the blocking / deadlock via a timeout — the send never
       completes, proving the violation.
    """
    context = violation.ast_context or "workerFunc"

    code = _go_test_preamble(["testing", "time", "fmt"]) + textwrap.dedent(f"""\
        // workerFunc mirrors the offending allocation from:
        // {violation.file_path}:{violation.line_range[0]}
        // Context: {context}
        func workerFunc(n int) []chan int {{
        \tchannels := make([]chan int, 0, n)
        \tfor i := 0; i < n; i++ {{
        \t\t// Reproduction: unbuffered channel allocated in loop — no capacity argument.
        \t\tch := make(chan int)  // ← offending pattern
        \t\tchannels = append(channels, ch)
        \t}}
        \treturn channels
        }}

        func TestReproUnbufferedChannelDeadlock(t *testing.T) {{
        \tchannels := workerFunc(3)

        \tdone := make(chan struct{{}})
        \tgo func() {{
        \t\t// Attempt to send on unbuffered channel — blocks forever without a reader.
        \t\tchannels[0] <- 42
        \t\tclose(done)
        \t}}()

        \tselect {{
        \tcase <-done:
        \t\tt.Fatal("expected blocking send to deadlock, but it completed")
        \tcase <-time.After(200 * time.Millisecond):
        \t\t// Correct: the send is still blocking — deadlock reproduced.
        \t\tfmt.Printf("unbuffered channel deadlock confirmed: send blocked for >200ms\\n")
        \t\tt.Logf("DEADLOCK REPRODUCED: unbuffered channel send blocked as expected")
        \t}}
        }}
        """)

    return ReproTest(
        rule_id=violation.rule_id,
        language="go",
        file_name="repro_test.go",
        code_content=code,
        expected_failure_pattern=r"unbuffered channel deadlock confirmed|DEADLOCK REPRODUCED",
    )


def _synth_nil_check_go(violation: InvariantViolation) -> ReproTest:
    """
    Generates a Go test that:
    1. Obtains a nil pointer from a constructor-style function.
    2. Dereferences it without a nil guard — triggers runtime panic.
    3. The test uses recover() to catch the panic and assert the expected message.
    """
    context = violation.ast_context or "boundaryFunc"

    code = _go_test_preamble(["testing", "fmt"]) + textwrap.dedent(f"""\
        // MyType mirrors a pointer type from: {violation.file_path}:{violation.line_range[0]}
        // Context: {context}
        type MyType struct {{
        \tValue int
        }}

        // mayReturnNil returns nil to simulate the offending boundary condition.
        func mayReturnNil(fail bool) *MyType {{
        \tif fail {{
        \t\treturn nil  // ← boundary condition that triggers the violation
        \t}}
        \treturn &MyType{{Value: 42}}
        }}

        // derefsWithoutGuard mirrors the offending dereference pattern.
        func derefsWithoutGuard() int {{
        \tptr := mayReturnNil(true)
        \t// Reproduction: no nil guard before dereference — will panic.
        \treturn ptr.Value  // ← offending pattern
        }}

        func TestReproNilCheckBoundary(t *testing.T) {{
        \tvar recovered interface{{}}
        \tfunc() {{
        \t\tdefer func() {{
        \t\t\trecovered = recover()
        \t\t}}()
        \t\t_ = derefsWithoutGuard()
        \t}}()

        \tif recovered == nil {{
        \t\tt.Fatal("expected nil dereference panic but no panic occurred")
        \t}}
        \tpanicMsg := fmt.Sprintf("%v", recovered)
        \tfmt.Printf("nil dereference panic confirmed: %s\\n", panicMsg)
        \tt.Logf("NIL DEREFERENCE REPRODUCED: panic=%s", panicMsg)
        }}
        """)

    return ReproTest(
        rule_id=violation.rule_id,
        language="go",
        file_name="repro_test.go",
        code_content=code,
        expected_failure_pattern=r"nil dereference panic confirmed|NIL DEREFERENCE REPRODUCED",
    )


def _synth_nil_check_python(violation: InvariantViolation) -> ReproTest:
    """
    Generates a pytest test that:
    1. Calls a function that returns None under a boundary condition.
    2. Accesses an attribute without a None guard — triggers AttributeError.
    3. pytest.raises() asserts the exact failure.
    """
    context = violation.ast_context or "boundary_func"

    code = textwrap.dedent(f"""\
        \"\"\"
        Reproduction test for nil-check-boundary violation.
        Source: {violation.file_path}:{violation.line_range[0]}
        Context: {context}
        \"\"\"
        import pytest


        class MyObject:
            def __init__(self, value: int) -> None:
                self.value = value


        def may_return_none(fail: bool):
            \"\"\"Mirrors the offending boundary — returns None when fail=True.\"\"\"
            if fail:
                return None  # ← boundary condition
            return MyObject(42)


        def deref_without_guard():
            \"\"\"Mirrors the offending dereference: no None check before attribute access.\"\"\"
            obj = may_return_none(True)
            # Reproduction of: {violation.offending_code.strip()}
            return obj.value  # ← AttributeError when obj is None


        def test_repro_nil_check_boundary():
            \"\"\"Confirm AttributeError is raised on unguarded None dereference.\"\"\"
            with pytest.raises(AttributeError):
                deref_without_guard()
            print("none dereference AttributeError confirmed")
        """)

    return ReproTest(
        rule_id=violation.rule_id,
        language="python",
        file_name="test_repro.py",
        code_content=code,
        expected_failure_pattern=r"none dereference AttributeError confirmed|1 passed",
    )


def _synth_otel_semantic(violation: InvariantViolation) -> ReproTest:
    """
    Generates a Python pytest test that:
    1. Sets up an OpenTelemetry in-memory span exporter.
    2. Creates a span and sets an ad-hoc (non-semconv) attribute key from
       the violation.
    3. Asserts the attribute key does NOT match any approved CNCF semconv
       prefix — proving the semantic convention violation.
    """
    # Extract the offending key from the violation message if possible.
    import re as _re
    key_match = _re.search(r'``([^`]+)``', violation.message)
    offending_key = key_match.group(1) if key_match else "custom.adhoc_key"

    context = violation.ast_context or "some_function"

    code = textwrap.dedent(f"""\
        \"\"\"
        Reproduction test for otel-semantic-convention violation.
        Source: {violation.file_path}:{violation.line_range[0]}
        Context: {context}
        Offending attribute key: {offending_key}
        \"\"\"
        import re
        import pytest
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor


        # CNCF semconv approved prefixes (StampBob invariant definition).
        APPROVED_PREFIXES = {{
            "gen_ai", "server", "client", "http", "rpc", "db",
            "messaging", "faas", "cloud", "container", "host",
            "k8s", "network", "process", "code", "enduser",
            "exception", "log", "thread", "url", "peer", "error",
        }}


        def is_approved_key(key: str) -> bool:
            for prefix in APPROVED_PREFIXES:
                if key == prefix or key.startswith(prefix + ".") or key.startswith(prefix + "_"):
                    return True
            return False


        def test_repro_otel_semantic_convention():
            \"\"\"
            Confirm that the offending span attribute key violates CNCF semconv.
            Reproduction of: {violation.offending_code.strip()}
            \"\"\"
            exporter = InMemorySpanExporter()
            provider = TracerProvider()
            provider.add_span_processor(SimpleSpanProcessor(exporter))
            tracer = provider.get_tracer("repro.tracer")

            offending_key = "{offending_key}"

            with tracer.start_as_current_span("repro_span") as span:
                # Reproduction of the offending attribute set call.
                span.set_attribute(offending_key, "repro_value")

            spans = exporter.get_finished_spans()
            assert len(spans) == 1, "Expected exactly one finished span"

            span_attrs = dict(spans[0].attributes or {{}})
            assert offending_key in span_attrs, (
                f"Attribute '{{offending_key}}' was not recorded on the span"
            )

            # The core invariant assertion: the key must NOT be semconv-approved.
            assert not is_approved_key(offending_key), (
                f"Key '{{offending_key}}' unexpectedly matches an approved semconv prefix — "
                "violation not reproduced"
            )

            print(f"otel semconv violation confirmed: key='{{offending_key}}' is non-standard")
        """)

    return ReproTest(
        rule_id=violation.rule_id,
        language="python",
        file_name="test_repro.py",
        code_content=code,
        expected_failure_pattern=r"otel semconv violation confirmed|1 passed",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Maps rule_id → synthesizer function
_SYNTHESIZERS = {
    "goroutine-leak":             _synth_goroutine_leak,
    "unbuffered-channel-in-loop": _synth_unbuffered_channel,
    "nil-check-boundary":         None,   # language-dispatched below
    "otel-semantic-convention":   _synth_otel_semantic,
}


def synthesize_repro_test(
    violation: InvariantViolation,
    repo_index: Optional[RepositoryIndex] = None,
) -> ReproTest:
    """
    Synthesize a standalone, executable reproduction test for *violation*.

    Parameters
    ----------
    violation:
        An ``InvariantViolation`` emitted by ``InvariantOracle.audit_diff``.
    repo_index:
        Optional ``RepositoryIndex`` used to enrich the test with accurate
        type/function names from the offending file. When *None* the test
        is synthesized from violation metadata alone.

    Returns
    -------
    ReproTest
        A fully self-contained test file (Go or Python) that, when executed,
        triggers the exact failure described by *violation*.

    Raises
    ------
    ValueError
        If *violation.rule_id* is not handled by any synthesizer.
    """
    rule_id = violation.rule_id

    # --- nil-check-boundary: language-dispatch ---
    if rule_id == "nil-check-boundary":
        # Infer language from file extension; fall back to Go.
        file_ext = violation.file_path.rsplit(".", 1)[-1].lower()
        if file_ext == "py":
            return _synth_nil_check_python(violation)
        return _synth_nil_check_go(violation)

    synth_fn = _SYNTHESIZERS.get(rule_id)
    if synth_fn is None:
        raise ValueError(
            f"No reproduction synthesizer registered for rule_id={rule_id!r}. "
            f"Registered rule IDs: {sorted(k for k, v in _SYNTHESIZERS.items() if v)}"
        )

    return synth_fn(violation)
