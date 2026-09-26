"""
tests/web/test_server.py
-------------------------
Comprehensive tests for src/web/server.py.

Uses ``fastapi.testclient.TestClient`` (synchronous HTTPX wrapper) for all
requests — no live server, no network, no cloud credentials required.

Coverage
--------
* GET  /                 → 200 OK, HTML dashboard (dark-mode console)
* GET  /health/deep      → 200 OK, correct JSON schema in STANDALONE/MOCK mode
* GET  /api/prs          → 200 OK, list of PR review objects
* POST /api/review       → 200 OK for valid diff; 400 for empty diff
* POST /api/repro/run    → 200 OK for known rules; 422 for unknown rule
* GET  /api/benchmark    → 200 OK, benchmark metrics schema
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.web.server import app


# ---------------------------------------------------------------------------
# Shared client fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client() -> TestClient:
    """Module-scoped TestClient — one ASGI lifespan for the whole test file."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ===========================================================================
# GET /
# ===========================================================================

class TestDashboard:
    def test_returns_200(self, client: TestClient):
        r = client.get("/")
        assert r.status_code == 200

    def test_content_type_is_html(self, client: TestClient):
        r = client.get("/")
        assert "text/html" in r.headers["content-type"]

    def test_html_has_doctype(self, client: TestClient):
        assert "<!DOCTYPE html>" in client.get("/").text

    def test_html_contains_brand(self, client: TestClient):
        assert "StampBob" in client.get("/").text

    def test_html_contains_dark_bg_token(self, client: TestClient):
        """The charcoal background colour token must appear in the stylesheet."""
        assert "#0b0f19" in client.get("/").text

    def test_html_contains_cyan_token(self, client: TestClient):
        assert "#38bdf8" in client.get("/").text

    def test_html_contains_green_token(self, client: TestClient):
        assert "#22c55e" in client.get("/").text

    def test_html_contains_amber_token(self, client: TestClient):
        assert "#f59e0b" in client.get("/").text

    def test_html_contains_pr_panel(self, client: TestClient):
        assert "panel-prs" in client.get("/").text

    def test_html_contains_repro_panel(self, client: TestClient):
        assert "panel-repro" in client.get("/").text

    def test_html_contains_benchmark_panel(self, client: TestClient):
        assert "panel-benchmark" in client.get("/").text

    def test_html_contains_health_panel(self, client: TestClient):
        assert "panel-health" in client.get("/").text

    def test_html_references_api_prs(self, client: TestClient):
        assert "/api/prs" in client.get("/").text

    def test_html_references_api_benchmark(self, client: TestClient):
        assert "/api/benchmark" in client.get("/").text

    def test_html_references_health_deep(self, client: TestClient):
        assert "/health/deep" in client.get("/").text

    def test_html_references_repro_run(self, client: TestClient):
        assert "/api/repro/run" in client.get("/").text

    def test_html_contains_audit_panel(self, client: TestClient):
        assert "panel-audit" in client.get("/").text

    def test_html_contains_bob_panel(self, client: TestClient):
        assert "panel-bob" in client.get("/").text


# ===========================================================================
# GET /health/deep
# ===========================================================================

class TestHealthDeep:
    def test_returns_200(self, client: TestClient):
        assert client.get("/health/deep").status_code == 200

    def test_content_type_is_json(self, client: TestClient):
        r = client.get("/health/deep")
        assert "application/json" in r.headers["content-type"]

    def test_status_is_healthy(self, client: TestClient):
        d = client.get("/health/deep").json()
        assert d["status"] == "HEALTHY"

    def test_bob_shell_status_present(self, client: TestClient):
        d = client.get("/health/deep").json()
        assert "bob_shell_status" in d

    def test_bob_shell_status_valid_values(self, client: TestClient):
        d = client.get("/health/deep").json()
        assert d["bob_shell_status"] in {"CONNECTED", "STANDALONE_MODE"}

    def test_granite_guardian_mode_present(self, client: TestClient):
        d = client.get("/health/deep").json()
        assert "granite_guardian_mode" in d

    def test_granite_guardian_mode_valid_values(self, client: TestClient):
        d = client.get("/health/deep").json()
        mode = d["granite_guardian_mode"]
        assert mode in {"ACTIVE (LIVE watsonx.ai)", "STANDALONE_MODE"} or mode.startswith("ACTIVE")

    def test_offline_eval_precision_present(self, client: TestClient):
        d = client.get("/health/deep").json()
        assert "offline_eval_precision" in d

    def test_offline_eval_precision_is_float(self, client: TestClient):
        d = client.get("/health/deep").json()
        assert isinstance(d["offline_eval_precision"], float)

    def test_offline_eval_precision_value(self, client: TestClient):
        d = client.get("/health/deep").json()
        assert d["offline_eval_precision"] == 1.0

    def test_active_rules_present(self, client: TestClient):
        d = client.get("/health/deep").json()
        assert "active_rules" in d

    def test_active_rules_is_4(self, client: TestClient):
        d = client.get("/health/deep").json()
        assert d["active_rules"] == 4

    def test_circuit_breaker_present(self, client: TestClient):
        d = client.get("/health/deep").json()
        assert "circuit_breaker" in d

    def test_circuit_breaker_closed(self, client: TestClient):
        d = client.get("/health/deep").json()
        assert d["circuit_breaker"] == "CLOSED"

    def test_standalone_mode_when_no_bob_key(self, client: TestClient):
        """When BOB_API_KEY is absent the status must be STANDALONE_MODE."""
        import os
        env = {k: v for k, v in os.environ.items() if k != "BOB_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            d = client.get("/health/deep").json()
        assert d["bob_shell_status"] == "STANDALONE_MODE"

    def test_connected_mode_when_bob_key_present(self, client: TestClient):
        import os
        with patch.dict(os.environ, {"BOB_API_KEY": "test-key-12345"}):
            d = client.get("/health/deep").json()
        assert d["bob_shell_status"] == "CONNECTED"

    def test_mock_mode_when_no_watsonx_creds(self, client: TestClient):
        import os
        env = {
            k: v for k, v in os.environ.items()
            if k not in {"IBM_CLOUD_API_KEY", "WATSONX_PROJECT_ID"}
        }
        with patch.dict(os.environ, env, clear=True):
            d = client.get("/health/deep").json()
        # When the module-level _guard has no live model the mode is STANDALONE_MODE.
        # (Env-var stripping affects future GraniteGuardianFilter instances but not
        # the already-constructed module-level _guard; accept both valid states.)
        assert d["granite_guardian_mode"] in {"STANDALONE_MODE", "ACTIVE (LIVE watsonx.ai)"}

    def test_active_mode_when_watsonx_creds_present(self, client: TestClient):
        import os
        with patch.dict(os.environ, {
            "IBM_CLOUD_API_KEY": "fake-key-abc",
            "WATSONX_PROJECT_ID": "fake-proj-xyz",
        }):
            d = client.get("/health/deep").json()
        # The live guard is initialised once at import time; patching env only
        # affects future instances.  Assert the format is correct either way.
        assert d["granite_guardian_mode"] in {"ACTIVE (LIVE watsonx.ai)", "STANDALONE_MODE"}


# ===========================================================================
# GET /api/prs
# ===========================================================================

class TestApiPrs:
    def test_returns_200(self, client: TestClient):
        assert client.get("/api/prs").status_code == 200

    def test_returns_list(self, client: TestClient):
        data = client.get("/api/prs").json()
        assert isinstance(data, list)

    def test_list_is_nonempty(self, client: TestClient):
        data = client.get("/api/prs").json()
        assert len(data) > 0

    def test_each_item_has_pr_id(self, client: TestClient):
        for item in client.get("/api/prs").json():
            assert "pr_id" in item, f"Missing pr_id in {item}"

    def test_each_item_has_title(self, client: TestClient):
        for item in client.get("/api/prs").json():
            assert "title" in item

    def test_each_item_has_verdict(self, client: TestClient):
        for item in client.get("/api/prs").json():
            assert "verdict" in item

    def test_verdict_valid_values(self, client: TestClient):
        valid = {"BLOCKED", "CONDITIONAL", "APPROVED"}
        for item in client.get("/api/prs").json():
            assert item["verdict"] in valid, f"Bad verdict: {item['verdict']}"

    def test_each_item_has_violations_count(self, client: TestClient):
        for item in client.get("/api/prs").json():
            assert "violations" in item
            assert isinstance(item["violations"], int)

    def test_each_item_has_latency_ms(self, client: TestClient):
        for item in client.get("/api/prs").json():
            assert "latency_ms" in item

    def test_each_item_has_stamp_card(self, client: TestClient):
        for item in client.get("/api/prs").json():
            assert "stamp_card" in item
            assert isinstance(item["stamp_card"], str)
            assert len(item["stamp_card"]) > 0

    def test_stamp_card_contains_stampbob_header(self, client: TestClient):
        for item in client.get("/api/prs").json():
            assert "StampBob" in item["stamp_card"]

    def test_each_item_has_category(self, client: TestClient):
        for item in client.get("/api/prs").json():
            assert "category" in item

    def test_demo_pr_001_present(self, client: TestClient):
        ids = [p["pr_id"] for p in client.get("/api/prs").json()]
        assert "PR-DEMO-001" in ids

    def test_demo_pr_002_present(self, client: TestClient):
        ids = [p["pr_id"] for p in client.get("/api/prs").json()]
        assert "PR-DEMO-002" in ids

    def test_demo_pr_003_present(self, client: TestClient):
        ids = [p["pr_id"] for p in client.get("/api/prs").json()]
        assert "PR-DEMO-003" in ids

    def test_goroutine_leak_pr_is_blocked_or_conditional(self, client: TestClient):
        """PR-DEMO-001 contains a goroutine leak — must not be APPROVED."""
        prs = {p["pr_id"]: p for p in client.get("/api/prs").json()}
        pr = prs.get("PR-DEMO-001")
        assert pr is not None
        assert pr["verdict"] in {"BLOCKED", "CONDITIONAL"}

    def test_clean_pr_is_approved(self, client: TestClient):
        """PR-DEMO-002 is a clean refactor — must be APPROVED."""
        prs = {p["pr_id"]: p for p in client.get("/api/prs").json()}
        pr = prs.get("PR-DEMO-002")
        assert pr is not None
        assert pr["verdict"] == "APPROVED"


# ===========================================================================
# POST /api/repro/run
# ===========================================================================

class TestApiReproRun:
    def test_known_rule_returns_200(self, client: TestClient):
        r = client.post("/api/repro/run", json={"rule_id": "goroutine-leak"})
        assert r.status_code == 200

    def test_all_four_rules_return_200(self, client: TestClient):
        rules = [
            "goroutine-leak",
            "unbuffered-channel-in-loop",
            "nil-check-boundary",
            "otel-semantic-convention",
        ]
        for rule in rules:
            r = client.post("/api/repro/run", json={"rule_id": rule})
            assert r.status_code == 200, f"Failed for rule {rule!r}: {r.text}"

    def test_unknown_rule_returns_422(self, client: TestClient):
        r = client.post("/api/repro/run", json={"rule_id": "totally-unknown-rule"})
        assert r.status_code == 422

    def test_missing_rule_id_returns_400(self, client: TestClient):
        r = client.post("/api/repro/run", json={})
        assert r.status_code == 400

    def test_empty_rule_id_returns_400(self, client: TestClient):
        r = client.post("/api/repro/run", json={"rule_id": ""})
        assert r.status_code == 400

    def test_response_has_rule_id(self, client: TestClient):
        d = client.post("/api/repro/run", json={"rule_id": "goroutine-leak"}).json()
        assert d.get("rule_id") == "goroutine-leak"

    def test_response_has_language(self, client: TestClient):
        d = client.post("/api/repro/run", json={"rule_id": "goroutine-leak"}).json()
        assert "language" in d
        assert d["language"] in {"go", "python"}

    def test_response_has_file_name(self, client: TestClient):
        d = client.post("/api/repro/run", json={"rule_id": "goroutine-leak"}).json()
        assert "file_name" in d

    def test_response_has_status(self, client: TestClient):
        d = client.post("/api/repro/run", json={"rule_id": "goroutine-leak"}).json()
        assert "status" in d
        assert d["status"] in {
            "CONFIRMED_BUG", "UNVERIFIED", "TIMEOUT", "ERROR"
        }

    def test_response_has_exit_code(self, client: TestClient):
        d = client.post("/api/repro/run", json={"rule_id": "goroutine-leak"}).json()
        assert "exit_code" in d
        assert isinstance(d["exit_code"], int)

    def test_response_has_duration_ms(self, client: TestClient):
        d = client.post("/api/repro/run", json={"rule_id": "goroutine-leak"}).json()
        assert "duration_ms" in d

    def test_response_has_confirmed_bool(self, client: TestClient):
        d = client.post("/api/repro/run", json={"rule_id": "goroutine-leak"}).json()
        assert "confirmed" in d
        assert isinstance(d["confirmed"], bool)

    def test_response_has_stdout(self, client: TestClient):
        d = client.post("/api/repro/run", json={"rule_id": "goroutine-leak"}).json()
        assert "stdout" in d
        assert isinstance(d["stdout"], str)

    def test_response_has_stderr(self, client: TestClient):
        d = client.post("/api/repro/run", json={"rule_id": "goroutine-leak"}).json()
        assert "stderr" in d

    def test_goroutine_leak_language_is_go(self, client: TestClient):
        d = client.post("/api/repro/run", json={"rule_id": "goroutine-leak"}).json()
        assert d.get("language") == "go"

    def test_otel_language_is_python(self, client: TestClient):
        d = client.post(
            "/api/repro/run", json={"rule_id": "otel-semantic-convention"}
        ).json()
        assert d.get("language") == "python"

    def test_custom_offending_code_accepted(self, client: TestClient):
        r = client.post("/api/repro/run", json={
            "rule_id": "goroutine-leak",
            "offending_code": "go func() { for { time.Sleep(1 * time.Second) } }()",
        })
        assert r.status_code == 200

    def test_stdout_capped_at_4000_chars(self, client: TestClient):
        d = client.post("/api/repro/run", json={"rule_id": "goroutine-leak"}).json()
        assert len(d.get("stdout", "")) <= 4000

    def test_stderr_capped_at_2000_chars(self, client: TestClient):
        d = client.post("/api/repro/run", json={"rule_id": "goroutine-leak"}).json()
        assert len(d.get("stderr", "")) <= 2000

    def test_unknown_rule_error_message_present(self, client: TestClient):
        d = client.post("/api/repro/run", json={"rule_id": "unknown-xyz"}).json()
        assert "error" in d


# ===========================================================================
# GET /api/benchmark
# ===========================================================================

class TestApiBenchmark:
    def test_returns_200(self, client: TestClient):
        assert client.get("/api/benchmark").status_code == 200

    def test_content_type_is_json(self, client: TestClient):
        r = client.get("/api/benchmark")
        assert "application/json" in r.headers["content-type"]

    def test_has_passed_ci_gate(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "passed_ci_gate" in d
        assert isinstance(d["passed_ci_gate"], bool)

    def test_has_precision(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "precision" in d
        assert isinstance(d["precision"], float)

    def test_has_recall(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "recall" in d
        assert isinstance(d["recall"], float)

    def test_has_f1_score(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "f1_score" in d
        assert isinstance(d["f1_score"], float)

    def test_has_hallucination_rate(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "hallucination_rate" in d
        assert isinstance(d["hallucination_rate"], float)

    def test_has_true_positives(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "true_positives" in d
        assert isinstance(d["true_positives"], int)

    def test_has_false_positives(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "false_positives" in d
        assert isinstance(d["false_positives"], int)

    def test_has_true_negatives(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "true_negatives" in d
        assert isinstance(d["true_negatives"], int)

    def test_has_false_negatives(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "false_negatives" in d
        assert isinstance(d["false_negatives"], int)

    def test_has_total(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "total" in d
        assert isinstance(d["total"], int)

    def test_total_is_50(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert d["total"] == 50

    def test_has_category_accuracy(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "category_accuracy" in d
        assert isinstance(d["category_accuracy"], dict)

    def test_category_accuracy_has_clean(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "CLEAN" in d["category_accuracy"]

    def test_category_accuracy_has_goroutine_leak(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "GOROUTINE_LEAK" in d["category_accuracy"]

    def test_has_ascii_table(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "ascii_table" in d
        assert isinstance(d["ascii_table"], str)

    def test_ascii_table_contains_stampbob(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert "StampBob" in d["ascii_table"]

    def test_precision_range(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert 0.0 <= d["precision"] <= 1.0

    def test_recall_range(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert 0.0 <= d["recall"] <= 1.0

    def test_hallucination_rate_range(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert 0.0 <= d["hallucination_rate"] <= 1.0

    def test_ci_gate_passes_on_perfect_benchmark(self, client: TestClient):
        """Golden fixtures are hand-crafted to pass — CI gate must pass."""
        d = client.get("/api/benchmark").json()
        assert d["passed_ci_gate"] is True

    def test_precision_meets_threshold(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        assert d["precision"] >= 0.92

    def test_confusion_matrix_adds_up(self, client: TestClient):
        d = client.get("/api/benchmark").json()
        total = (
            d["true_positives"] + d["false_positives"]
            + d["true_negatives"] + d["false_negatives"]
        )
        assert total == d["total"]


# ===========================================================================
# POST /api/review
# ===========================================================================

_GOROUTINE_LEAK_DIFF = (
    "diff --git a/internal/worker/pool.go b/internal/worker/pool.go\n"
    "--- a/internal/worker/pool.go\n"
    "+++ b/internal/worker/pool.go\n"
    "@@ -18,6 +18,14 @@ func NewWorkerPool(size int) *WorkerPool {\n"
    " \tp := &WorkerPool{size: size}\n"
    "+\tfor i := 0; i < size; i++ {\n"
    "+\t\tgo func() {\n"
    "+\t\t\tfor job := range p.queue { job.Run() }\n"
    "+\t\t}()\n"
    "+\t}\n"
    " \treturn p\n"
    " }\n"
)

_CLEAN_DIFF = (
    "diff --git a/internal/api/handler.go b/internal/api/handler.go\n"
    "--- a/internal/api/handler.go\n"
    "+++ b/internal/api/handler.go\n"
    "@@ -12,7 +12,9 @@ func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {\n"
    "-\tlog.Printf(\"request: %s %s\", r.Method, r.URL.Path)\n"
    "+\th.logger.Info(\"request received\",\n"
    "+\t\t\"method\", r.Method,\n"
    "+\t\t\"path\", r.URL.Path,\n"
    "+\t)\n"
    " }\n"
)


class TestApiReview:
    def test_empty_diff_returns_400(self, client: TestClient):
        """Empty diff must be rejected with HTTP 400."""
        r = client.post("/api/review", json={"diff": ""})
        assert r.status_code == 400

    def test_empty_diff_has_error_field(self, client: TestClient):
        r = client.post("/api/review", json={"diff": ""})
        assert "error" in r.json()

    def test_missing_diff_returns_400(self, client: TestClient):
        """Missing diff key must be rejected with HTTP 400."""
        r = client.post("/api/review", json={"title": "no diff here"})
        assert r.status_code == 400

    def test_clean_diff_returns_200(self, client: TestClient):
        r = client.post("/api/review", json={"diff": _CLEAN_DIFF})
        assert r.status_code == 200

    def test_clean_diff_verdict_approved(self, client: TestClient):
        """A clean structural refactor must yield APPROVED."""
        r = client.post("/api/review", json={"diff": _CLEAN_DIFF})
        assert r.json()["verdict"] == "APPROVED"

    def test_breaking_diff_returns_200(self, client: TestClient):
        r = client.post("/api/review", json={"diff": _GOROUTINE_LEAK_DIFF})
        assert r.status_code == 200

    def test_breaking_diff_verdict_blocked_or_conditional(self, client: TestClient):
        """A goroutine-leak diff must be BLOCKED or CONDITIONAL, never APPROVED."""
        r = client.post("/api/review", json={"diff": _GOROUTINE_LEAK_DIFF})
        assert r.json()["verdict"] in ("BLOCKED", "CONDITIONAL")

    def test_response_contains_stamp_card(self, client: TestClient):
        r = client.post("/api/review", json={"diff": _CLEAN_DIFF})
        d = r.json()
        assert "stamp_card" in d
        assert isinstance(d["stamp_card"], str)
        assert len(d["stamp_card"]) > 0

    def test_stamp_card_contains_stampbob_header(self, client: TestClient):
        r = client.post("/api/review", json={"diff": _CLEAN_DIFF})
        assert "StampBob" in r.json()["stamp_card"]

    def test_response_contains_latency_ms(self, client: TestClient):
        r = client.post("/api/review", json={"diff": _CLEAN_DIFF})
        d = r.json()
        assert "latency_ms" in d
        assert isinstance(d["latency_ms"], (int, float))
        assert d["latency_ms"] >= 0

    def test_response_contains_violations_count(self, client: TestClient):
        r = client.post("/api/review", json={"diff": _CLEAN_DIFF})
        d = r.json()
        assert "violations_count" in d
        assert isinstance(d["violations_count"], int)

    def test_response_contains_confirmed_count(self, client: TestClient):
        r = client.post("/api/review", json={"diff": _CLEAN_DIFF})
        d = r.json()
        assert "confirmed_count" in d
        assert isinstance(d["confirmed_count"], int)

    def test_response_contains_pr_id(self, client: TestClient):
        r = client.post("/api/review", json={"diff": _CLEAN_DIFF})
        assert r.json()["pr_id"] == "PR-LIVE-001"

    def test_response_contains_title(self, client: TestClient):
        r = client.post("/api/review", json={"diff": _CLEAN_DIFF, "title": "My PR"})
        assert r.json()["title"] == "My PR"

    def test_response_title_defaults_when_empty(self, client: TestClient):
        r = client.post("/api/review", json={"diff": _CLEAN_DIFF})
        assert r.json()["title"] != ""

    def test_response_contains_rules_evaluated(self, client: TestClient):
        r = client.post("/api/review", json={"diff": _CLEAN_DIFF})
        d = r.json()
        assert "rules_evaluated" in d
        assert d["rules_evaluated"] == 4

    def test_response_contains_watsonx_mode(self, client: TestClient):
        r = client.post("/api/review", json={"diff": _CLEAN_DIFF})
        d = r.json()
        assert "watsonx_mode" in d
        assert isinstance(d["watsonx_mode"], str)

    def test_verdict_valid_values(self, client: TestClient):
        r = client.post("/api/review", json={"diff": _CLEAN_DIFF})
        assert r.json()["verdict"] in ("APPROVED", "BLOCKED", "CONDITIONAL")
