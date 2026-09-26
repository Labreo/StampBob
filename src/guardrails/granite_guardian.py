"""
granite_guardian.py — IBM Granite Guardian 3.0 dual-loop runtime safety guardrail.

Inspects AI-generated reproduction tests and fix suggestions for:
  - Prompt injection / command execution risks
  - Unsafe network socket calls
  - Hardcoded API credentials or tokens

When live IBM watsonx.ai credentials are not present the module falls back to a
deterministic, offline mock so that ``pytest`` and ``make local`` never crash or
require external credentials.

Usage
-----
    from src.guardrails.granite_guardian import GraniteGuardianFilter, SafetyVerdict

    guard = GraniteGuardianFilter()
    verdict = guard.inspect(generated_text)
    if not verdict.is_safe:
        print(verdict.reasoning)
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load .env at module startup (stdlib only — no python-dotenv required)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_env_file = os.path.join(_PROJECT_ROOT, ".env")
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
# Risk threshold
# ---------------------------------------------------------------------------

_RISK_THRESHOLD: float = 0.1  # scores above this → is_safe = False

# ---------------------------------------------------------------------------
# Offline deterministic patterns (used by mock fallback + pre-screen)
# ---------------------------------------------------------------------------

# (category_label, compiled_pattern)
_OFFLINE_PATTERNS: List[tuple[str, re.Pattern[str]]] = [
    # Prompt-injection / shell execution
    (
        "command_execution",
        re.compile(
            r"(?:os\.system\s*\("
            r"|subprocess\.(?:call|run|Popen|check_output)\s*\("
            r"|eval\s*\("
            r"|exec\s*\("
            r"|__import__\s*\()",
            re.IGNORECASE,
        ),
    ),
    # Unsafe raw socket operations — matches both fully qualified
    # `socket.connect(` and bare method calls `s.connect(` / `s.bind(`.
    (
        "unsafe_network",
        re.compile(
            r"(?:socket\.|(?<!\w)\w+\.)(?:socket|connect|bind|listen|accept|recv|send)\s*\(",
            re.IGNORECASE,
        ),
    ),
    # Hardcoded secrets / tokens
    (
        "hardcoded_credentials",
        re.compile(
            r"(?:api[_\-]?key|secret[_\-]?key|access[_\-]?token"
            r"|auth[_\-]?token|password|passwd|private[_\-]?key"
            r"|ibm[_\-]?cloud[_\-]?api[_\-]?key|watsonx)"
            r"\s*[=:]\s*"
            r"""(?:["'])(?!<)[^\s"']{8,}(?:["'])""",
            re.IGNORECASE,
        ),
    ),
]


def _offline_scan(text: str) -> tuple[float, List[str]]:
    """
    Deterministic offline scan.  Returns *(risk_score, flagged_categories)*.

    Each matched category contributes 0.5 to the score (capped at 1.0).
    A single hit is already above the 0.1 threshold so the verdict is unsafe.
    """
    flagged: List[str] = []
    for label, pattern in _OFFLINE_PATTERNS:
        if pattern.search(text):
            flagged.append(label)
    score = min(1.0, len(flagged) * 0.5)
    return score, flagged


# ---------------------------------------------------------------------------
# SafetyVerdict
# ---------------------------------------------------------------------------


@dataclass
class SafetyVerdict:
    """Structured result returned by :class:`GraniteGuardianFilter`."""

    is_safe: bool
    risk_score: float
    flagged_categories: List[str] = field(default_factory=list)
    reasoning: str = ""

    # Convenience helper used in tests and callers
    def safe_fallback_explanation(self) -> str:
        if self.is_safe:
            return "Content passed all safety checks."
        cats = ", ".join(self.flagged_categories) or "unknown"
        return (
            f"Content was blocked (risk_score={self.risk_score:.3f}). "
            f"Flagged categories: {cats}. "
            "Please remove any shell execution calls, raw socket usage, or "
            "hardcoded credentials before resubmitting."
        )


# ---------------------------------------------------------------------------
# watsonx.ai REST client (thin wrapper around ibm-watsonx-ai SDK)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Regional model selection helpers
# ---------------------------------------------------------------------------

def _select_model_id(url: str) -> str:
    """
    Choose the Granite model based on the watsonx region URL.

    - ``us-south`` and ``eu-de`` host the full Guardian catalogue → use
      ``ibm/granite-guardian-3-8b``.
    - ``au-syd`` and any other region where Guardian is not yet cataloged →
      fall back to ``ibm/granite-3-1-8b-base`` for live foundation-model
      inference.
    """
    if "us-south" in url or "eu-de" in url:
        return "ibm/granite-guardian-3-8b"
    return "ibm/granite-3-1-8b-base"


def _build_watsonx_client() -> "tuple[Optional[object], str, str]":
    """
    Attempt to construct an authenticated ``ModelInference`` client from the
    IBM watsonx.ai Python SDK.

    Returns ``(model, model_id, url)`` where *model* is ``None`` (triggering
    mock mode) when:
      - ``WATSONX_PROJECT_ID`` or ``IBM_CLOUD_API_KEY`` is absent/empty.
      - The ``ibm_watsonx_ai`` package is not installed.
      - Any other import or authentication error occurs.
    """
    project_id = os.getenv("WATSONX_PROJECT_ID", "").strip()
    api_key = os.getenv("IBM_CLOUD_API_KEY", "").strip()
    url = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com").strip()

    if not project_id or not api_key:
        log.debug(
            "GraniteGuardian: watsonx credentials absent — using offline mock."
        )
        return None, "", url

    model_id = _select_model_id(url)

    try:
        from ibm_watsonx_ai import APIClient, Credentials  # type: ignore[import]
        from ibm_watsonx_ai.foundation_models import ModelInference  # type: ignore[import]

        credentials = Credentials(url=url, api_key=api_key)
        client = APIClient(credentials)
        model = ModelInference(
            model_id=model_id,
            api_client=client,
            project_id=project_id,
            params={"max_new_tokens": 64, "temperature": 0.0},
        )
        log.info(
            "GraniteGuardian: connected to watsonx.ai (%s at %s) (live mode).",
            model_id,
            url,
        )
        return model, model_id, url
    except Exception as exc:  # pragma: no cover
        log.warning(
            "GraniteGuardian: failed to initialise watsonx client (%s); "
            "falling back to offline mock.",
            exc,
        )
        return None, "", url


# ---------------------------------------------------------------------------
# Response parsing helpers
# ---------------------------------------------------------------------------

_SAFE_TOKEN = re.compile(r"\bsafe\b", re.IGNORECASE)
_UNSAFE_TOKEN = re.compile(r"\bunsafe\b", re.IGNORECASE)
_SCORE_RE = re.compile(r"risk[_\s]?score[:\s]+([0-9]*\.?[0-9]+)", re.IGNORECASE)


def _parse_guardian_response(raw: str) -> tuple[float, List[str], str]:
    """
    Parse the plain-text response from Granite Guardian.

    The model is instructed (via system prompt) to respond with a short
    structured answer like::

        unsafe
        risk_score: 0.87
        categories: command_execution, hardcoded_credentials
        reasoning: The snippet calls os.system() ...

    Returns *(risk_score, flagged_categories, reasoning)*.
    """
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
    is_unsafe_response = bool(_UNSAFE_TOKEN.search(raw))
    score_match = _SCORE_RE.search(raw)
    risk_score = float(score_match.group(1)) if score_match else (
        0.9 if is_unsafe_response else 0.0
    )

    # Extract categories line
    cats: List[str] = []
    for line in lines:
        if line.lower().startswith("categories:"):
            cats = [c.strip() for c in line.split(":", 1)[1].split(",") if c.strip()]
            break

    # Reasoning: everything after the first two structural lines
    reasoning_lines = [
        l for l in lines
        if not l.lower().startswith(("safe", "unsafe", "risk_score", "categories:"))
    ]
    reasoning = " ".join(reasoning_lines) or raw[:200]

    return risk_score, cats, reasoning


# ---------------------------------------------------------------------------
# GraniteGuardianFilter
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are Granite Guardian, an enterprise AI safety model. "
    "Inspect the following AI-generated code snippet for security risks. "
    "Check for: prompt injection, shell command execution, raw socket calls, "
    "and hardcoded API keys or tokens. "
    "Respond ONLY in this exact format:\n"
    "safe OR unsafe\n"
    "risk_score: <0.0–1.0>\n"
    "categories: <comma-separated risk categories or 'none'>\n"
    "reasoning: <one concise sentence>"
)


class GraniteGuardianFilter:
    """
    Dual-loop safety guardrail powered by IBM Granite Guardian 3.0.

    Loop 1 — Offline pre-screen:
        Fast deterministic regex scan for the most obvious risks.
        Runs unconditionally, even in live mode, to short-circuit clearly
        dangerous content without consuming API quota.

    Loop 2 — Live watsonx.ai inference (optional):
        When credentials are available, sends the content to
        ``ibm/granite-guardian-3-8b`` for probabilistic risk scoring.
        Falls back to the offline verdict when the API is unavailable.

    Parameters
    ----------
    risk_threshold:
        Scores strictly above this value result in ``is_safe = False``.
        Default: ``0.1``.
    """

    def __init__(self, risk_threshold: float = _RISK_THRESHOLD) -> None:
        self._threshold = risk_threshold
        self._model, self._watsonx_model_id, self._watsonx_url = _build_watsonx_client()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inspect(self, content: str) -> SafetyVerdict:
        """
        Inspect *content* and return a :class:`SafetyVerdict`.

        Parameters
        ----------
        content:
            The AI-generated text to evaluate (reproduction test, fix
            suggestion, etc.).
        """
        # Loop 1: fast offline pre-screen
        offline_score, offline_cats = _offline_scan(content)
        if offline_score > self._threshold:
            reasoning = (
                f"Offline scan flagged pattern(s): {', '.join(offline_cats)}."
            )
            return SafetyVerdict(
                is_safe=False,
                risk_score=offline_score,
                flagged_categories=offline_cats,
                reasoning=reasoning,
            )

        # Loop 2: watsonx.ai inference (or mock)
        if self._model is not None:
            return self._live_inspect(content)
        return self._mock_inspect(content)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _live_inspect(self, content: str) -> SafetyVerdict:
        """Call Granite Guardian via watsonx.ai SDK."""
        prompt = f"{_SYSTEM_PROMPT}\n\n---\n{content}\n---"
        try:
            response = self._model.generate_text(prompt=prompt)  # type: ignore[union-attr]
            raw: str = (
                response if isinstance(response, str)
                else response.get("results", [{}])[0].get("generated_text", "")
            )
            risk_score, cats, reasoning = _parse_guardian_response(raw)
        except Exception as exc:
            log.warning("GraniteGuardian live call failed (%s); using offline.", exc)
            risk_score, cats = _offline_scan(content)
            reasoning = f"Live call failed; offline fallback used. Detail: {exc}"

        is_safe = risk_score <= self._threshold
        return SafetyVerdict(
            is_safe=is_safe,
            risk_score=risk_score,
            flagged_categories=cats,
            reasoning=reasoning,
        )

    def _mock_inspect(self, content: str) -> SafetyVerdict:
        """
        Deterministic offline mock — used when no watsonx credentials are set.

        Identical to the offline pre-screen but treated as the final verdict.
        Guarantees ``pytest`` and ``make local`` work without external access.
        """
        risk_score, cats = _offline_scan(content)
        is_safe = risk_score <= self._threshold
        reasoning = (
            f"[mock] Offline pattern scan. Flagged: {cats}."
            if cats
            else "[mock] No risky patterns detected."
        )
        return SafetyVerdict(
            is_safe=is_safe,
            risk_score=risk_score,
            flagged_categories=cats,
            reasoning=reasoning,
        )
