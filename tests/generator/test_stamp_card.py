"""
tests/generator/test_stamp_card.py

Unit tests for src/generator/stamp_card.py — 100% function coverage.

Tests cover every helper and the public generate_stamp_card API:
- _stamp_header: BLOCKED, CONDITIONAL, APPROVED paths
- _severity_badge: CRITICAL and WARNING
- _status_cell / _repro_verified_cell
- _invariant_matrix: no violations, single row, multi-row
- _code_fence: go and python
- _repro_proof_section: no confirmed, one confirmed, truncation
- _governance_footer: with and without telemetry, with/without PR metadata
- _remediation_cta
- generate_stamp_card: full card structure, all three stamp states
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.engine.invariant_oracle import InvariantViolation
from src.engine.rules import Severity
from src.generator.repro_synthesizer import ReproTest
from src.generator.sandbox_runner import SandboxResult, VerifiedViolation
from src.generator.stamp_card import (
    _code_fence,
    _governance_footer,
    _invariant_matrix,
    _remediation_cta,
    _repro_proof_section,
    _repro_verified_cell,
    _severity_badge,
    _stamp_header,
    _status_cell,
    generate_stamp_card,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_violation(
    rule_id: str = "goroutine-leak",
    severity: Severity = Severity.CRITICAL,
    file_path: str = "pkg/server.go",
    line: int = 42,
) -> InvariantViolation:
    return InvariantViolation(
        rule_id=rule_id,
        severity=severity,
        file_path=file_path,
        line_range=(line, line),
        offending_code="  go func() {",
        ast_context="pkg.ServeForever",
        message="leak detected",
        remediation="use ctx",
    )


def _make_repro(language: str = "go", file_name: str = "repro_test.go") -> ReproTest:
    return ReproTest(
        rule_id="goroutine-leak",
        language=language,
        file_name=file_name,
        code_content='package repro_test\nfunc TestRepro(t *testing.T) {}',
        expected_failure_pattern=r"LEAK",
    )


def _make_sandbox_result(
    test: ReproTest,
    confirmed: bool = True,
    status: str = "CONFIRMED_BUG",
    duration_ms: float = 1234.5,
    stdout: str = "PASS\nLEAK DETECTED",
    stderr: str = "",
    exit_code: int = 0,
) -> SandboxResult:
    return SandboxResult(
        test=test,
        status=status,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        confirmed=confirmed,
    )


def _make_verified(
    rule_id: str = "goroutine-leak",
    severity: Severity = Severity.CRITICAL,
    confirmed: bool = True,
    status: str = "CONFIRMED_BUG",
    language: str = "go",
    duration_ms: float = 999.0,
) -> VerifiedViolation:
    violation = _make_violation(rule_id=rule_id, severity=severity)
    repro = _make_repro(language=language)
    sr = _make_sandbox_result(repro, confirmed=confirmed, status=status, duration_ms=duration_ms)
    return VerifiedViolation(
        violation=violation,
        repro_test=repro,
        sandbox_result=sr,
        confirmed=confirmed,
    )


PR_META = {
    "url": "https://github.com/org/repo/pull/99",
    "number": 99,
    "author": "dev",
    "base_branch": "main",
    "title": "Add streaming endpoint",
}


# ---------------------------------------------------------------------------
# _stamp_header
# ---------------------------------------------------------------------------

class TestStampHeader:
    def test_blocked_on_critical_confirmed(self):
        vv = _make_verified(severity=Severity.CRITICAL, confirmed=True)
        header = _stamp_header([vv])
        assert "BLOCKED" in header
        assert "❌" in header

    def test_conditional_on_warning_only(self):
        vv = _make_verified(severity=Severity.WARNING, confirmed=True)
        header = _stamp_header([vv])
        assert "CONDITIONAL" in header
        assert "⚠️" in header

    def test_approved_on_no_violations(self):
        header = _stamp_header([])
        assert "APPROVED" in header
        assert "✅" in header

    def test_approved_when_all_unconfirmed(self):
        vv = _make_verified(severity=Severity.CRITICAL, confirmed=False, status="UNVERIFIED")
        header = _stamp_header([vv])
        assert "APPROVED" in header

    def test_blocked_takes_priority_over_warning(self):
        critical = _make_verified(severity=Severity.CRITICAL, confirmed=True)
        warning = _make_verified(
            rule_id="otel-semantic-convention",
            severity=Severity.WARNING,
            confirmed=True,
        )
        header = _stamp_header([warning, critical])
        assert "BLOCKED" in header

    def test_conditional_on_mixed_confirmed_warning_and_unconfirmed_critical(self):
        warning_confirmed = _make_verified(severity=Severity.WARNING, confirmed=True)
        critical_unconfirmed = _make_verified(
            severity=Severity.CRITICAL, confirmed=False, status="UNVERIFIED"
        )
        header = _stamp_header([warning_confirmed, critical_unconfirmed])
        assert "CONDITIONAL" in header


# ---------------------------------------------------------------------------
# _severity_badge
# ---------------------------------------------------------------------------

class TestSeverityBadge:
    def test_critical_badge(self):
        badge = _severity_badge(Severity.CRITICAL)
        assert "CRITICAL" in badge
        assert "🔴" in badge

    def test_warning_badge(self):
        badge = _severity_badge(Severity.WARNING)
        assert "WARNING" in badge
        assert "🟡" in badge


# ---------------------------------------------------------------------------
# _status_cell
# ---------------------------------------------------------------------------

class TestStatusCell:
    def test_confirmed_shows_confirmed_bug(self):
        vv = _make_verified(confirmed=True)
        assert "CONFIRMED BUG" in _status_cell(vv)
        assert "❌" in _status_cell(vv)

    def test_unconfirmed_shows_unverified(self):
        vv = _make_verified(confirmed=False, status="UNVERIFIED")
        cell = _status_cell(vv)
        assert "UNVERIFIED" in cell
        assert "⚠️" in cell


# ---------------------------------------------------------------------------
# _repro_verified_cell
# ---------------------------------------------------------------------------

class TestReproVerifiedCell:
    def test_dash_when_not_confirmed(self):
        vv = _make_verified(confirmed=False, status="UNVERIFIED")
        assert _repro_verified_cell(vv) == "—"

    def test_go_icon_for_go_repro(self):
        vv = _make_verified(confirmed=True, language="go")
        cell = _repro_verified_cell(vv)
        assert "✅" in cell
        assert "🐹" in cell
        assert "repro_test.go" in cell

    def test_python_icon_for_python_repro(self):
        vv = _make_verified(confirmed=True, language="python")
        vv.repro_test.file_name  # just access to ensure it's set
        # Rebuild with python file_name
        vv = VerifiedViolation(
            violation=_make_violation(),
            repro_test=_make_repro(language="python", file_name="test_repro.py"),
            sandbox_result=_make_sandbox_result(_make_repro(language="python")),
            confirmed=True,
        )
        cell = _repro_verified_cell(vv)
        assert "🐍" in cell
        assert "test_repro.py" in cell


# ---------------------------------------------------------------------------
# _invariant_matrix
# ---------------------------------------------------------------------------

class TestInvariantMatrix:
    def test_no_violations_returns_no_detected_message(self):
        result = _invariant_matrix([])
        assert "No invariant violations detected" in result

    def test_single_row_contains_rule_id(self):
        vv = _make_verified(rule_id="goroutine-leak")
        table = _invariant_matrix([vv])
        assert "goroutine-leak" in table

    def test_header_row_present(self):
        vv = _make_verified()
        table = _invariant_matrix([vv])
        assert "Rule ID" in table
        assert "Severity" in table
        assert "File & Lines" in table
        assert "Status" in table
        assert "Repro Verified?" in table

    def test_separator_row_present(self):
        vv = _make_verified()
        table = _invariant_matrix([vv])
        assert "---" in table

    def test_multiple_rows(self):
        vv1 = _make_verified(rule_id="goroutine-leak")
        vv2 = _make_verified(rule_id="otel-semantic-convention", severity=Severity.WARNING)
        table = _invariant_matrix([vv1, vv2])
        assert "goroutine-leak" in table
        assert "otel-semantic-convention" in table

    def test_file_path_in_table(self):
        vv = _make_verified()
        table = _invariant_matrix([vv])
        assert "pkg/server.go:42" in table

    def test_unconfirmed_row(self):
        vv = _make_verified(confirmed=False, status="UNVERIFIED")
        table = _invariant_matrix([vv])
        assert "UNVERIFIED" in table
        assert "—" in table  # repro_verified_cell for unconfirmed


# ---------------------------------------------------------------------------
# _code_fence
# ---------------------------------------------------------------------------

class TestCodeFence:
    def test_go_fence(self):
        fence = _code_fence("package main", "go")
        assert fence.startswith("```go\n")
        assert fence.endswith("\n```")

    def test_python_fence(self):
        fence = _code_fence("def foo(): pass", "python")
        assert fence.startswith("```python\n")
        assert fence.endswith("\n```")

    def test_trailing_blank_lines_trimmed(self):
        fence = _code_fence("x = 1\n\n\n", "python")
        assert fence.endswith("x = 1\n```")

    def test_unknown_language_defaults_to_python_fence(self):
        fence = _code_fence("some code", "ruby")
        assert fence.startswith("```python")


# ---------------------------------------------------------------------------
# _repro_proof_section
# ---------------------------------------------------------------------------

class TestReproProofSection:
    def test_empty_when_no_confirmed(self):
        vv = _make_verified(confirmed=False, status="UNVERIFIED")
        result = _repro_proof_section([vv])
        assert result == ""

    def test_empty_on_empty_list(self):
        assert _repro_proof_section([]) == ""

    def test_contains_details_tags(self):
        vv = _make_verified()
        section = _repro_proof_section([vv])
        assert "<details>" in section
        assert "</details>" in section

    def test_contains_rule_id_in_summary(self):
        vv = _make_verified(rule_id="goroutine-leak")
        section = _repro_proof_section([vv])
        assert "goroutine-leak" in section

    def test_contains_code_fence(self):
        vv = _make_verified()
        section = _repro_proof_section([vv])
        assert "```go" in section

    def test_contains_status_and_duration(self):
        vv = _make_verified(status="CONFIRMED_BUG", duration_ms=500.0)
        section = _repro_proof_section([vv])
        assert "CONFIRMED_BUG" in section
        assert "500" in section

    def test_contains_terminal_output(self):
        vv = _make_verified()
        section = _repro_proof_section([vv])
        assert "LEAK DETECTED" in section

    def test_long_output_is_truncated(self):
        repro = _make_repro()
        long_stdout = "\n".join(f"line {i}" for i in range(100))
        sr = _make_sandbox_result(repro, stdout=long_stdout)
        vv = VerifiedViolation(
            violation=_make_violation(),
            repro_test=repro,
            sandbox_result=sr,
            confirmed=True,
        )
        section = _repro_proof_section([vv])
        assert "truncated" in section

    def test_multiple_confirmed_violations(self):
        vv1 = _make_verified(rule_id="goroutine-leak")
        vv2 = _make_verified(
            rule_id="nil-check-boundary",
            language="python",
        )
        section = _repro_proof_section([vv1, vv2])
        assert "goroutine-leak" in section
        assert "nil-check-boundary" in section

    def test_contains_offending_code(self):
        vv = _make_verified()
        section = _repro_proof_section([vv])
        assert "go func() {" in section

    def test_contains_ast_context(self):
        vv = _make_verified()
        section = _repro_proof_section([vv])
        assert "pkg.ServeForever" in section


# ---------------------------------------------------------------------------
# _governance_footer
# ---------------------------------------------------------------------------

class TestGovernanceFooter:
    def test_default_model_name(self):
        footer = _governance_footer({}, [], None)
        assert "IBM Bob 2.0" in footer
        assert "Granite Guardian 3.0" in footer

    def test_custom_model_from_telemetry(self):
        telem = {"model": "CustomModel/v1"}
        footer = _governance_footer({}, [], telem)
        assert "CustomModel/v1" in footer

    def test_bobcoins_rendered(self):
        telem = {"bobcoins_consumed": 42}
        footer = _governance_footer({}, [], telem)
        assert "42" in footer

    def test_tokens_rendered_with_comma_format(self):
        telem = {"tokens_processed": 12345}
        footer = _governance_footer({}, [], telem)
        assert "12,345" in footer

    def test_latency_rendered(self):
        telem = {"latency_ms": 987.654}
        footer = _governance_footer({}, [], telem)
        assert "988" in footer  # rounded to 0 decimals

    def test_precision_rendered(self):
        telem = {"evaluation_precision": 94.2}
        footer = _governance_footer({}, [], telem)
        assert "94.2%" in footer

    def test_pr_number_and_author(self):
        pr = {"number": 99, "author": "alice"}
        footer = _governance_footer(pr, [], None)
        assert "#99" in footer
        assert "@alice" in footer

    def test_base_branch(self):
        pr = {"base_branch": "develop"}
        footer = _governance_footer(pr, [], None)
        assert "develop" in footer

    def test_pr_url_included_when_present(self):
        pr = {"url": "https://github.com/org/repo/pull/1"}
        footer = _governance_footer(pr, [], None)
        assert "https://github.com/org/repo/pull/1" in footer

    def test_latency_derived_from_sandbox_results_when_missing(self):
        vv = _make_verified(duration_ms=300.0)
        footer = _governance_footer({}, [vv], None)
        assert "300" in footer

    def test_timestamp_present(self):
        footer = _governance_footer({}, [], None)
        # Should contain a UTC timestamp in ISO 8601 format
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", footer)

    def test_separator_line_present(self):
        footer = _governance_footer({}, [], None)
        assert "---" in footer

    def test_empty_pr_metadata(self):
        footer = _governance_footer({}, [], None)
        # Should not raise and should contain the table header
        assert "Model" in footer

    def test_dash_when_pr_number_missing(self):
        footer = _governance_footer({"author": ""}, [], None)
        # Without a PR number, the cell should show the dash placeholder
        assert "—" in footer


# ---------------------------------------------------------------------------
# _remediation_cta
# ---------------------------------------------------------------------------

class TestRemediationCta:
    def test_contains_stamp_fix_command(self):
        cta = _remediation_cta()
        assert "/stamp fix" in cta

    def test_contains_ibm_bob(self):
        cta = _remediation_cta()
        assert "IBM Bob" in cta

    def test_starts_with_separator(self):
        cta = _remediation_cta()
        assert "---" in cta


# ---------------------------------------------------------------------------
# generate_stamp_card — full integration
# ---------------------------------------------------------------------------

class TestGenerateStampCard:
    def test_returns_string(self):
        card = generate_stamp_card(PR_META, [])
        assert isinstance(card, str)

    def test_approved_card_on_no_violations(self):
        card = generate_stamp_card(PR_META, [])
        assert "APPROVED" in card
        assert "✅" in card

    def test_blocked_card_on_critical_confirmed(self):
        vv = _make_verified(severity=Severity.CRITICAL, confirmed=True)
        card = generate_stamp_card(PR_META, [vv])
        assert "BLOCKED" in card
        assert "❌" in card

    def test_conditional_card_on_warning_only(self):
        vv = _make_verified(severity=Severity.WARNING, confirmed=True)
        card = generate_stamp_card(PR_META, [vv])
        assert "CONDITIONAL" in card
        assert "⚠️" in card

    def test_card_contains_matrix_header(self):
        vv = _make_verified()
        card = generate_stamp_card(PR_META, [vv])
        assert "Invariant Verification Matrix" in card
        assert "Rule ID" in card

    def test_card_contains_repro_proof_section_when_confirmed(self):
        vv = _make_verified(confirmed=True)
        card = generate_stamp_card(PR_META, [vv])
        assert "Executable Repro Proof" in card
        assert "<details>" in card

    def test_card_no_repro_section_when_no_confirmed(self):
        vv = _make_verified(confirmed=False, status="UNVERIFIED")
        card = generate_stamp_card(PR_META, [vv])
        assert "<details>" not in card

    def test_card_contains_governance_footer(self):
        card = generate_stamp_card(PR_META, [], bob_telemetry={"bobcoins_consumed": 7})
        assert "Governance" in card
        assert "Token Audit" in card
        assert "7" in card

    def test_card_contains_remediation_cta(self):
        card = generate_stamp_card(PR_META, [])
        assert "/stamp fix" in card

    def test_pr_title_in_card_header(self):
        card = generate_stamp_card(PR_META, [])
        assert "Add streaming endpoint" in card

    def test_pr_number_in_card_header(self):
        card = generate_stamp_card(PR_META, [])
        assert "99" in card

    def test_pr_author_in_card_header(self):
        card = generate_stamp_card(PR_META, [])
        assert "@dev" in card

    def test_card_header_without_metadata(self):
        card = generate_stamp_card({}, [])
        assert "StampBob" in card

    def test_telemetry_none_is_safe(self):
        card = generate_stamp_card(PR_META, [], bob_telemetry=None)
        assert "IBM Bob 2.0" in card

    def test_full_telemetry_rendered(self):
        telem = {
            "model": "IBM Bob 3.0",
            "bobcoins_consumed": 100,
            "tokens_processed": 50000,
            "latency_ms": 2500,
            "evaluation_precision": 97.8,
        }
        card = generate_stamp_card(PR_META, [], bob_telemetry=telem)
        assert "IBM Bob 3.0" in card
        assert "100" in card
        assert "50,000" in card
        assert "2500" in card
        assert "97.8%" in card

    def test_multiple_violations_all_in_matrix(self):
        vv1 = _make_verified(rule_id="goroutine-leak", severity=Severity.CRITICAL)
        vv2 = _make_verified(
            rule_id="otel-semantic-convention", severity=Severity.WARNING
        )
        card = generate_stamp_card(PR_META, [vv1, vv2])
        assert "goroutine-leak" in card
        assert "otel-semantic-convention" in card

    def test_card_starts_with_stampbob_header(self):
        card = generate_stamp_card(PR_META, [])
        assert card.startswith("# 🔏 StampBob")
