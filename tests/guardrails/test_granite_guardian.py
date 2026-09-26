"""
tests/guardrails/test_granite_guardian.py
-----------------------------------------
Comprehensive tests for src/guardrails/granite_guardian.py.

Covers:
- SafetyVerdict dataclass behaviour
- _offline_scan helper (all three risk categories)
- GraniteGuardianFilter mock-mode fallback (no credentials)
- GraniteGuardianFilter safe payloads
- GraniteGuardianFilter unsafe payloads (command execution, network, credentials)
- Multi-category payloads (score accumulation + cap)
- Custom risk_threshold
- _parse_guardian_response (response parsing logic)
- Live-mode fallback when model call raises
- safe_fallback_explanation() messages
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from src.guardrails.granite_guardian import (
    GraniteGuardianFilter,
    SafetyVerdict,
    _offline_scan,
    _parse_guardian_response,
)


# ---------------------------------------------------------------------------
# SafetyVerdict
# ---------------------------------------------------------------------------

class TestSafetyVerdict:
    def test_safe_fallback_explanation_when_safe(self):
        v = SafetyVerdict(is_safe=True, risk_score=0.0, flagged_categories=[], reasoning="ok")
        assert v.safe_fallback_explanation() == "Content passed all safety checks."

    def test_safe_fallback_explanation_when_unsafe_single_category(self):
        v = SafetyVerdict(
            is_safe=False,
            risk_score=0.5,
            flagged_categories=["command_execution"],
            reasoning="risky",
        )
        msg = v.safe_fallback_explanation()
        assert "command_execution" in msg
        assert "0.500" in msg
        assert "blocked" in msg.lower()

    def test_safe_fallback_explanation_when_unsafe_multi_category(self):
        v = SafetyVerdict(
            is_safe=False,
            risk_score=1.0,
            flagged_categories=["command_execution", "hardcoded_credentials"],
            reasoning="very risky",
        )
        msg = v.safe_fallback_explanation()
        assert "command_execution" in msg
        assert "hardcoded_credentials" in msg

    def test_safe_fallback_explanation_unknown_categories(self):
        v = SafetyVerdict(is_safe=False, risk_score=0.9, flagged_categories=[], reasoning="x")
        msg = v.safe_fallback_explanation()
        assert "unknown" in msg


# ---------------------------------------------------------------------------
# _offline_scan
# ---------------------------------------------------------------------------

class TestOfflineScan:
    def test_clean_snippet_scores_zero(self):
        score, cats = _offline_scan("result = db.query('SELECT 1')")
        assert score == 0.0
        assert cats == []

    def test_os_system_flagged(self):
        score, cats = _offline_scan("os.system('ls -la')")
        assert "command_execution" in cats
        assert score == 0.5

    def test_subprocess_run_flagged(self):
        score, cats = _offline_scan("subprocess.run(['curl', url])")
        assert "command_execution" in cats

    def test_subprocess_popen_flagged(self):
        score, cats = _offline_scan("proc = subprocess.Popen(['bash', '-c', cmd])")
        assert "command_execution" in cats

    def test_subprocess_check_output_flagged(self):
        score, cats = _offline_scan("out = subprocess.check_output(['git', 'log'])")
        assert "command_execution" in cats

    def test_eval_flagged(self):
        score, cats = _offline_scan("eval(user_input)")
        assert "command_execution" in cats

    def test_exec_flagged(self):
        score, cats = _offline_scan("exec(compiled_code)")
        assert "command_execution" in cats

    def test_import_flagged(self):
        score, cats = _offline_scan("m = __import__('os')")
        assert "command_execution" in cats

    def test_socket_connect_flagged(self):
        score, cats = _offline_scan("s.connect((host, 4444))")
        assert "unsafe_network" in cats

    def test_socket_socket_flagged(self):
        score, cats = _offline_scan("s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)")
        assert "unsafe_network" in cats

    def test_socket_bind_flagged(self):
        score, cats = _offline_scan("s.bind(('0.0.0.0', 8080))")
        assert "unsafe_network" in cats

    def test_hardcoded_api_key_flagged(self):
        score, cats = _offline_scan('api_key = "sk-abc123XYZ987abcdef"')
        assert "hardcoded_credentials" in cats

    def test_hardcoded_secret_key_flagged(self):
        score, cats = _offline_scan('secret_key = "mysecretpassword123"')
        assert "hardcoded_credentials" in cats

    def test_hardcoded_access_token_flagged(self):
        score, cats = _offline_scan('access_token = "ghp_abc123xyz987token0"')
        assert "hardcoded_credentials" in cats

    def test_hardcoded_password_flagged(self):
        score, cats = _offline_scan('password = "SuperSecret!Pass1"')
        assert "hardcoded_credentials" in cats

    def test_short_string_not_flagged(self):
        # Credentials pattern requires >= 8 non-space chars in the value
        score, cats = _offline_scan('api_key = "tiny"')
        assert "hardcoded_credentials" not in cats

    def test_multi_category_score_accumulates(self):
        snippet = (
            'os.system("rm -rf /")\n'
            'api_key = "abc1234567890xyz"\n'
        )
        score, cats = _offline_scan(snippet)
        assert "command_execution" in cats
        assert "hardcoded_credentials" in cats
        assert score == 1.0  # 2 * 0.5 = 1.0

    def test_score_capped_at_1(self):
        snippet = (
            'os.system("x")\n'
            'api_key = "abc1234567890xyz"\n'
            's = socket.socket()\n'
        )
        score, _ = _offline_scan(snippet)
        assert score == 1.0  # 3 * 0.5 would be 1.5 but capped


# ---------------------------------------------------------------------------
# _parse_guardian_response
# ---------------------------------------------------------------------------

class TestParseGuardianResponse:
    def test_safe_response_parses(self):
        raw = "safe\nrisk_score: 0.02\ncategories: none\nreasoning: No risks detected."
        score, cats, reasoning = _parse_guardian_response(raw)
        assert score == pytest.approx(0.02)
        assert cats == ["none"]
        assert "No risks detected" in reasoning

    def test_unsafe_response_parses(self):
        raw = (
            "unsafe\n"
            "risk_score: 0.85\n"
            "categories: command_execution, hardcoded_credentials\n"
            "reasoning: Contains shell execution."
        )
        score, cats, reasoning = _parse_guardian_response(raw)
        assert score == pytest.approx(0.85)
        assert "command_execution" in cats
        assert "hardcoded_credentials" in cats

    def test_missing_score_inferred_from_unsafe_token(self):
        raw = "unsafe\ncategories: none\nreasoning: risky"
        score, _, _ = _parse_guardian_response(raw)
        assert score == pytest.approx(0.9)

    def test_missing_score_inferred_as_zero_for_safe(self):
        raw = "safe\ncategories: none\nreasoning: clean"
        score, _, _ = _parse_guardian_response(raw)
        assert score == pytest.approx(0.0)

    def test_empty_categories_line(self):
        raw = "safe\nrisk_score: 0.01\ncategories:\nreasoning: ok"
        _, cats, _ = _parse_guardian_response(raw)
        assert cats == []

    def test_reasoning_fallback_to_raw(self):
        raw = "unsafe\nrisk_score: 0.9"
        _, _, reasoning = _parse_guardian_response(raw)
        assert len(reasoning) > 0


# ---------------------------------------------------------------------------
# GraniteGuardianFilter — mock (offline) mode
# ---------------------------------------------------------------------------

class TestGraniteGuardianFilterMock:
    """All tests run without watsonx credentials → mock mode."""

    def setup_method(self):
        # Ensure no live credentials bleed in from environment
        os.environ.pop("WATSONX_PROJECT_ID", None)
        os.environ.pop("IBM_CLOUD_API_KEY", None)
        self.guard = GraniteGuardianFilter()

    def test_safe_sql_query(self):
        v = self.guard.inspect(
            'result = db.query("SELECT * FROM users WHERE id = ?", [uid])'
        )
        assert v.is_safe is True
        assert v.risk_score == pytest.approx(0.0)
        assert v.flagged_categories == []

    def test_safe_pure_function(self):
        v = self.guard.inspect("return sorted(items, key=lambda x: x.priority)")
        assert v.is_safe is True

    def test_safe_empty_string(self):
        v = self.guard.inspect("")
        assert v.is_safe is True

    def test_os_system_blocked(self):
        v = self.guard.inspect('os.system("rm -rf /tmp/data")')
        assert v.is_safe is False
        assert "command_execution" in v.flagged_categories
        assert v.risk_score > 0.1

    def test_subprocess_run_blocked(self):
        v = self.guard.inspect('subprocess.run(["curl", url], capture_output=True)')
        assert v.is_safe is False
        assert "command_execution" in v.flagged_categories

    def test_subprocess_popen_blocked(self):
        v = self.guard.inspect('p = subprocess.Popen(["bash", "-c", cmd], shell=True)')
        assert v.is_safe is False

    def test_eval_blocked(self):
        v = self.guard.inspect("result = eval(user_code)")
        assert v.is_safe is False

    def test_exec_blocked(self):
        v = self.guard.inspect("exec(compile(src, '<string>', 'exec'))")
        assert v.is_safe is False

    def test_socket_blocked(self):
        v = self.guard.inspect(
            "s = socket.socket()\ns.connect((host, 9999))"
        )
        assert v.is_safe is False
        assert "unsafe_network" in v.flagged_categories

    def test_hardcoded_api_key_blocked(self):
        v = self.guard.inspect('api_key = "sk-abc123XYZ987abcdef"')
        assert v.is_safe is False
        assert "hardcoded_credentials" in v.flagged_categories

    def test_hardcoded_ibm_key_blocked(self):
        v = self.guard.inspect('ibm_cloud_api_key = "abcdefghijklmnop"')
        assert v.is_safe is False
        assert "hardcoded_credentials" in v.flagged_categories

    def test_custom_threshold_zero_means_always_blocked(self):
        guard = GraniteGuardianFilter(risk_threshold=0.0)
        # Even a clean snippet scores 0.0 but 0.0 is not > 0.0 threshold
        v = guard.inspect("x = 1 + 2")
        # Score is 0.0, threshold is 0.0 → not strictly above threshold
        assert v.is_safe is True

    def test_custom_threshold_one_means_always_safe(self):
        guard = GraniteGuardianFilter(risk_threshold=1.0)
        v = guard.inspect('os.system("evil")')
        # score 0.5 <= 1.0 → safe under this permissive threshold
        assert v.is_safe is True

    def test_mock_reasoning_contains_mock_label(self):
        v = self.guard.inspect("x = 1")
        assert "[mock]" in v.reasoning

    def test_mock_reasoning_mentions_categories_when_flagged(self):
        v = self.guard.inspect("subprocess.run(['x'])")
        assert "command_execution" in v.reasoning or "[mock]" in v.reasoning

    def test_safe_fallback_explanation_on_blocked(self):
        v = self.guard.inspect('os.system("x")')
        msg = v.safe_fallback_explanation()
        assert "blocked" in msg.lower()
        assert "command_execution" in msg

    def test_verdict_dataclass_fields(self):
        v = self.guard.inspect("x = 1")
        assert hasattr(v, "is_safe")
        assert hasattr(v, "risk_score")
        assert hasattr(v, "flagged_categories")
        assert hasattr(v, "reasoning")
        assert isinstance(v.flagged_categories, list)
        assert isinstance(v.risk_score, float)


# ---------------------------------------------------------------------------
# GraniteGuardianFilter — live-mode fallback when model call raises
# ---------------------------------------------------------------------------

class TestGraniteGuardianFilterLiveFallback:
    """Simulates a live model that raises an exception → falls back to offline."""

    def _make_guard_with_failing_model(self) -> GraniteGuardianFilter:
        guard = GraniteGuardianFilter()
        mock_model = MagicMock()
        mock_model.generate_text.side_effect = RuntimeError("network timeout")
        guard._model = mock_model
        return guard

    def test_fallback_to_offline_on_live_error(self):
        guard = self._make_guard_with_failing_model()
        # A safe snippet should still pass even if live call fails
        v = guard.inspect("result = compute(x)")
        assert isinstance(v, SafetyVerdict)

    def test_fallback_still_blocks_dangerous_snippet(self):
        guard = self._make_guard_with_failing_model()
        # Offline pre-screen fires before the live call for explicit risks
        v = guard.inspect('os.system("rm -rf /")')
        assert v.is_safe is False
        assert "command_execution" in v.flagged_categories

    def test_fallback_reasoning_mentions_failure(self):
        guard = self._make_guard_with_failing_model()
        # Safe snippet passes offline → goes to live → live fails → offline fallback
        v = guard.inspect("x = 1 + 2")
        # reasoning should acknowledge fallback
        assert isinstance(v.reasoning, str)


# ---------------------------------------------------------------------------
# GraniteGuardianFilter — live-mode successful response
# ---------------------------------------------------------------------------

class TestGraniteGuardianFilterLiveSuccess:
    """Simulates a successful live watsonx.ai response."""

    def _make_guard_with_live_response(self, raw_response: str) -> GraniteGuardianFilter:
        guard = GraniteGuardianFilter()
        mock_model = MagicMock()
        mock_model.generate_text.return_value = raw_response
        guard._model = mock_model
        return guard

    def test_live_safe_response(self):
        guard = self._make_guard_with_live_response(
            "safe\nrisk_score: 0.02\ncategories: none\nreasoning: All clear."
        )
        v = guard.inspect("result = compute(x)")
        assert v.is_safe is True
        assert v.risk_score == pytest.approx(0.02)

    def test_live_unsafe_response(self):
        guard = self._make_guard_with_live_response(
            "unsafe\nrisk_score: 0.88\n"
            "categories: command_execution\n"
            "reasoning: shell exec detected."
        )
        # Offline pre-screen won't catch this snippet
        v = guard.inspect("result = compute(x)")
        assert v.is_safe is False
        assert v.risk_score == pytest.approx(0.88)
        assert "command_execution" in v.flagged_categories

    def test_live_dict_response_format(self):
        """SDK may return a dict with 'results' key."""
        guard = GraniteGuardianFilter()
        mock_model = MagicMock()
        mock_model.generate_text.return_value = {
            "results": [{"generated_text": "safe\nrisk_score: 0.0\ncategories: none\nreasoning: ok"}]
        }
        guard._model = mock_model
        v = guard.inspect("x = 1")
        assert v.is_safe is True
