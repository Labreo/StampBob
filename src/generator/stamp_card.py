"""
stamp_card.py — StampBob Verified Stamp Card Generator.

Produces a single, high-contrast, markdown-formatted "Verified Stamp Card"
for posting as the primary PR review comment.

StampBob NEVER posts inline diff comments. Every finding is consolidated into
one structured card so maintainers see the full picture in one glance.

Card structure
--------------
1. Header — stamp status (BLOCKED / CONDITIONAL / APPROVED)
2. Invariant Verification Matrix — table of all violations
3. Executable Repro Proof — collapsible <details> per confirmed violation
4. Governance & Token Audit footer
5. Auto-remediation call-to-action
"""

from __future__ import annotations

import datetime
from typing import List, Optional

from src.engine.rules import Severity
from src.generator.sandbox_runner import VerifiedViolation


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _stamp_header(verified_violations: List[VerifiedViolation]) -> str:
    """Return the single-line stamp status header."""
    confirmed = [vv for vv in verified_violations if vv.confirmed]
    critical_confirmed = [
        vv for vv in confirmed
        if vv.violation.severity == Severity.CRITICAL
    ]

    if critical_confirmed:
        return "### ❌ STAMP: BLOCKED (Verified Invariant Breach)"
    if confirmed:
        return "### ⚠️ STAMP: CONDITIONAL APPROVAL (Warnings Detected)"
    return "### ✅ STAMP: APPROVED (All Invariants Verified & Passing)"


def _severity_badge(severity: Severity) -> str:
    """Return a compact markdown badge for a severity level."""
    if severity == Severity.CRITICAL:
        return "🔴 CRITICAL"
    return "🟡 WARNING"


def _status_cell(vv: VerifiedViolation) -> str:
    """Return the Status column value for a verified violation row."""
    if vv.confirmed:
        return "❌ CONFIRMED BUG"
    return "⚠️ UNVERIFIED"


def _repro_verified_cell(vv: VerifiedViolation) -> str:
    """Return the 'Repro Verified?' column value."""
    if not vv.confirmed:
        return "—"
    lang_icon = "🐹" if vv.repro_test.language == "go" else "🐍"
    return f"✅ Yes ({lang_icon} `{vv.repro_test.file_name}`)"


def _invariant_matrix(verified_violations: List[VerifiedViolation]) -> str:
    """Render the Invariant Verification Matrix table."""
    if not verified_violations:
        return "_No invariant violations detected._\n"

    rows: List[str] = [
        "| Rule ID | Severity | File & Lines | Status | Repro Verified? |",
        "|---------|----------|--------------|--------|-----------------|",
    ]
    for vv in verified_violations:
        v = vv.violation
        file_ref = f"`{v.file_path}:{v.line_range[0]}`"
        rows.append(
            f"| `{v.rule_id}` "
            f"| {_severity_badge(v.severity)} "
            f"| {file_ref} "
            f"| {_status_cell(vv)} "
            f"| {_repro_verified_cell(vv)} |"
        )
    return "\n".join(rows) + "\n"


def _code_fence(code: str, language: str) -> str:
    """Wrap code in a markdown fenced code block."""
    lang = "go" if language == "go" else "python"
    # Trim trailing blank lines for cleaner rendering
    return f"```{lang}\n{code.rstrip()}\n```"


def _repro_proof_section(verified_violations: List[VerifiedViolation]) -> str:
    """
    Render one collapsible <details> block per confirmed violation containing:
    - Offending AST context and source line
    - Synthesized test code snippet
    - Terminal execution output proving the failure
    """
    confirmed = [vv for vv in verified_violations if vv.confirmed]
    if not confirmed:
        return ""

    blocks: List[str] = ["## 🔬 Executable Repro Proof\n"]
    for vv in confirmed:
        v = vv.violation
        sr = vv.sandbox_result
        rt = vv.repro_test

        # Truncate long stdout/stderr for readability (keep first 60 lines)
        raw_output = (sr.stdout + sr.stderr).strip()
        output_lines = raw_output.splitlines()
        if len(output_lines) > 60:
            output_lines = output_lines[:60] + ["… (truncated)"]
        terminal_output = "\n".join(output_lines)

        summary_line = (
            f"<code>{v.rule_id}</code> — "
            f"{v.file_path}:{v.line_range[0]} "
            f"[{sr.status} in {sr.duration_ms:.0f} ms]"
        )

        block = (
            f"<details>\n"
            f"<summary>{summary_line}</summary>\n\n"
            f"**Offending context:** `{v.ast_context}`  \n"
            f"**Source line:** `{v.offending_code.strip()}`\n\n"
            f"**Synthesized repro test** (`{rt.file_name}`):\n\n"
            f"{_code_fence(rt.code_content, rt.language)}\n\n"
            f"**Terminal output** (sandbox execution):\n\n"
            f"```\n{terminal_output}\n```\n\n"
            f"</details>"
        )
        blocks.append(block)

    return "\n".join(blocks) + "\n"


def _governance_footer(
    pr_metadata: dict,
    verified_violations: List[VerifiedViolation],
    bob_telemetry: Optional[dict],
) -> str:
    """Render the Governance & Token Audit footer."""
    telem = bob_telemetry or {}

    model = telem.get("model", "IBM Bob 2.0 (Agent Mode) + Granite Guardian 3.0")
    bobcoins = telem.get("bobcoins_consumed", 0)
    tokens = telem.get("tokens_processed", 0)
    latency_ms = telem.get("latency_ms", 0)
    precision = telem.get("evaluation_precision", 0.0)

    # Derive total sandbox duration from results when latency not supplied
    if not latency_ms and verified_violations:
        latency_ms = sum(
            vv.sandbox_result.duration_ms for vv in verified_violations
        )

    pr_url = pr_metadata.get("url", "")
    pr_number = pr_metadata.get("number", "")
    author = pr_metadata.get("author", "")
    base_branch = pr_metadata.get("base_branch", "main")
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        "---",
        "## 📋 Governance & Token Audit",
        "",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| **Model** | `{model}` |",
        f"| **Bobcoins consumed** | `{bobcoins}` |",
        f"| **Tokens processed** | `{tokens:,}` |",
        f"| **Latency** | `{latency_ms:.0f} ms` |",
        f"| **Evaluation precision** | `{precision:.1f}%` |",
        f"| **PR** | {('#' + str(pr_number)) if pr_number else '—'}{(' by @' + author) if author else ''} |",
        f"| **Base branch** | `{base_branch}` |",
        f"| **Stamped at** | `{ts}` |",
    ]

    if pr_url:
        lines.append(f"| **PR URL** | {pr_url} |")

    return "\n".join(lines) + "\n"


def _remediation_cta() -> str:
    """Return the auto-remediation call-to-action block."""
    return (
        "\n---\n"
        "> 💡 **To auto-remediate with IBM Bob, reply:** `/stamp fix`\n"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_stamp_card(
    pr_metadata: dict,
    verified_violations: List[VerifiedViolation],
    bob_telemetry: Optional[dict] = None,
) -> str:
    """
    Generate a Verified Stamp Card as a markdown string for posting as the
    primary PR review comment.

    Parameters
    ----------
    pr_metadata:
        Dict with keys: ``url``, ``number``, ``author``, ``base_branch``,
        ``title``.  All keys are optional; missing values are rendered as
        ``—``.
    verified_violations:
        List of ``VerifiedViolation`` objects from ``sandbox_runner``.
        May be empty (produces an APPROVED stamp).
    bob_telemetry:
        Optional dict with telemetry keys: ``model``, ``bobcoins_consumed``,
        ``tokens_processed``, ``latency_ms``, ``evaluation_precision``.

    Returns
    -------
    str
        Complete markdown-formatted stamp card ready to post as a PR comment.
    """
    pr_title = pr_metadata.get("title", "")
    pr_number = pr_metadata.get("number", "")
    pr_author = pr_metadata.get("author", "")

    title_line = "# 🔏 StampBob Verified Review"
    if pr_title or pr_number:
        parts = []
        if pr_number:
            parts.append(f"PR #{pr_number}")
        if pr_title:
            parts.append(f"— {pr_title}")
        if pr_author:
            parts.append(f"(by @{pr_author})")
        title_line += ": " + " ".join(parts)

    sections = [
        title_line,
        "",
        _stamp_header(verified_violations),
        "",
        "## 📊 Invariant Verification Matrix",
        "",
        _invariant_matrix(verified_violations),
        _repro_proof_section(verified_violations),
        _governance_footer(pr_metadata, verified_violations, bob_telemetry),
        _remediation_cta(),
    ]

    return "\n".join(sections)
