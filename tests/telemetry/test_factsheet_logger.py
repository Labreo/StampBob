"""
tests/telemetry/test_factsheet_logger.py
-----------------------------------------
Comprehensive tests for src/telemetry/factsheet_logger.py.

Covers:
- FactsheetRecord dataclass construction and field types
- FactsheetLogger.log_session — file creation, append behaviour, JSON validity
- FactsheetLogger.log_session — parent directory auto-creation
- FactsheetLogger.log_session — multiple records yield multiple lines
- FactsheetLogger.format_stamp_footer — content correctness and single-line contract
- FactsheetLogger — custom ledger path
- Round-trip: write then read back and deserialise
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.telemetry.factsheet_logger import (
    FactsheetRecord,
    FactsheetLogger,
    _DEFAULT_LEDGER_PATH,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_record(**overrides) -> FactsheetRecord:
    """Return a fully populated FactsheetRecord, accepting field overrides."""
    defaults = dict(
        pr_id="PR-99",
        commit_sha="deadbeef",
        timestamp="2024-06-01T12:00:00Z",
        bob_task_id="task-001",
        mode="reviewer",
        bobcoins_consumed=2.50,
        tokens_processed=4096,
        offline_eval_precision=0.942,
        granite_guardian_score=0.0,
        execution_latency_ms=317.5,
        verdict="APPROVED",
    )
    defaults.update(overrides)
    return FactsheetRecord(**defaults)


@pytest.fixture
def record():
    return _make_record()


@pytest.fixture
def logger(tmp_path):
    return FactsheetLogger(ledger_path=str(tmp_path / "ledger.json"))


# ---------------------------------------------------------------------------
# FactsheetRecord — dataclass
# ---------------------------------------------------------------------------

class TestFactsheetRecord:
    def test_all_fields_accessible(self, record):
        assert record.pr_id == "PR-99"
        assert record.commit_sha == "deadbeef"
        assert record.timestamp == "2024-06-01T12:00:00Z"
        assert record.bob_task_id == "task-001"
        assert record.mode == "reviewer"
        assert record.bobcoins_consumed == pytest.approx(2.50)
        assert record.tokens_processed == 4096
        assert record.offline_eval_precision == pytest.approx(0.942)
        assert record.granite_guardian_score == pytest.approx(0.0)
        assert record.execution_latency_ms == pytest.approx(317.5)
        assert record.verdict == "APPROVED"

    def test_field_types(self, record):
        assert isinstance(record.pr_id, str)
        assert isinstance(record.commit_sha, str)
        assert isinstance(record.timestamp, str)
        assert isinstance(record.bob_task_id, str)
        assert isinstance(record.mode, str)
        assert isinstance(record.bobcoins_consumed, float)
        assert isinstance(record.tokens_processed, int)
        assert isinstance(record.offline_eval_precision, float)
        assert isinstance(record.granite_guardian_score, float)
        assert isinstance(record.execution_latency_ms, float)
        assert isinstance(record.verdict, str)

    def test_blocked_verdict(self):
        r = _make_record(verdict="BLOCKED")
        assert r.verdict == "BLOCKED"

    def test_zero_cost_record(self):
        r = _make_record(bobcoins_consumed=0.0, tokens_processed=0)
        assert r.bobcoins_consumed == 0.0
        assert r.tokens_processed == 0

    def test_high_precision(self):
        r = _make_record(offline_eval_precision=1.0)
        assert r.offline_eval_precision == pytest.approx(1.0)

    def test_high_guardian_score(self):
        r = _make_record(granite_guardian_score=0.95)
        assert r.granite_guardian_score == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# FactsheetLogger — log_session
# ---------------------------------------------------------------------------

class TestLogSession:
    def test_creates_file_on_first_write(self, logger, record, tmp_path):
        ledger = tmp_path / "ledger.json"
        assert not ledger.exists()
        logger.log_session(record)
        assert ledger.exists()

    def test_single_record_is_valid_json(self, logger, record, tmp_path):
        logger.log_session(record)
        lines = (tmp_path / "ledger.json").read_text().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["pr_id"] == "PR-99"

    def test_all_fields_serialised(self, logger, record, tmp_path):
        logger.log_session(record)
        data = json.loads((tmp_path / "ledger.json").read_text())
        for field in (
            "pr_id", "commit_sha", "timestamp", "bob_task_id", "mode",
            "bobcoins_consumed", "tokens_processed", "offline_eval_precision",
            "granite_guardian_score", "execution_latency_ms", "verdict",
        ):
            assert field in data

    def test_appends_multiple_records(self, logger, tmp_path):
        for i in range(5):
            logger.log_session(_make_record(pr_id=f"PR-{i}"))
        lines = (tmp_path / "ledger.json").read_text().splitlines()
        assert len(lines) == 5

    def test_each_line_is_independent_json(self, logger, tmp_path):
        logger.log_session(_make_record(pr_id="PR-A", verdict="APPROVED"))
        logger.log_session(_make_record(pr_id="PR-B", verdict="BLOCKED"))
        lines = (tmp_path / "ledger.json").read_text().splitlines()
        records = [json.loads(l) for l in lines]
        assert records[0]["pr_id"] == "PR-A"
        assert records[0]["verdict"] == "APPROVED"
        assert records[1]["pr_id"] == "PR-B"
        assert records[1]["verdict"] == "BLOCKED"

    def test_parent_directory_auto_created(self, tmp_path, record):
        nested = tmp_path / "a" / "b" / "c" / "ledger.json"
        logger = FactsheetLogger(ledger_path=str(nested))
        logger.log_session(record)
        assert nested.exists()

    def test_existing_file_is_appended_not_overwritten(self, logger, tmp_path, record):
        logger.log_session(record)
        logger.log_session(_make_record(pr_id="PR-SECOND"))
        lines = (tmp_path / "ledger.json").read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["pr_id"] == "PR-99"
        assert json.loads(lines[1])["pr_id"] == "PR-SECOND"

    def test_each_line_ends_with_newline(self, logger, tmp_path, record):
        logger.log_session(record)
        raw = (tmp_path / "ledger.json").read_text()
        assert raw.endswith("\n")

    def test_round_trip_preserves_float_precision(self, logger, tmp_path):
        r = _make_record(
            bobcoins_consumed=3.141592653589793,
            offline_eval_precision=0.9876543210,
            granite_guardian_score=0.0012345,
            execution_latency_ms=1234.567,
        )
        logger.log_session(r)
        data = json.loads((tmp_path / "ledger.json").read_text())
        assert abs(data["bobcoins_consumed"] - r.bobcoins_consumed) < 1e-9
        assert abs(data["offline_eval_precision"] - r.offline_eval_precision) < 1e-9

    def test_default_ledger_path_constant(self):
        assert _DEFAULT_LEDGER_PATH == "telemetry/factsheet_ledger.json"

    def test_custom_ledger_path_used(self, tmp_path, record):
        custom = str(tmp_path / "custom_path.json")
        logger = FactsheetLogger(ledger_path=custom)
        logger.log_session(record)
        assert Path(custom).exists()


# ---------------------------------------------------------------------------
# FactsheetLogger — format_stamp_footer
# ---------------------------------------------------------------------------

class TestFormatStampFooter:
    def test_returns_string(self, logger, record):
        footer = logger.format_stamp_footer(record)
        assert isinstance(footer, str)

    def test_single_line(self, logger, record):
        footer = logger.format_stamp_footer(record)
        assert "\n" not in footer

    def test_starts_with_stamp_prefix(self, logger, record):
        footer = logger.format_stamp_footer(record)
        assert footer.startswith("> 🔏 StampBob")

    def test_contains_pr_id(self, logger, record):
        footer = logger.format_stamp_footer(record)
        assert "PR-99" in footer

    def test_contains_commit_sha(self, logger, record):
        footer = logger.format_stamp_footer(record)
        assert "deadbeef" in footer

    def test_contains_timestamp(self, logger, record):
        footer = logger.format_stamp_footer(record)
        assert "2024-06-01T12:00:00Z" in footer

    def test_contains_task_id(self, logger, record):
        footer = logger.format_stamp_footer(record)
        assert "task-001" in footer

    def test_contains_mode(self, logger, record):
        footer = logger.format_stamp_footer(record)
        assert "reviewer" in footer

    def test_contains_bobcoins(self, logger, record):
        footer = logger.format_stamp_footer(record)
        assert "2.50" in footer
        assert "coins" in footer

    def test_contains_tokens(self, logger, record):
        footer = logger.format_stamp_footer(record)
        assert "4096" in footer
        assert "tok" in footer

    def test_contains_precision(self, logger, record):
        footer = logger.format_stamp_footer(record)
        assert "0.942" in footer
        assert "prec=" in footer

    def test_contains_guardian_score(self, logger, record):
        footer = logger.format_stamp_footer(record)
        assert "guardian=" in footer
        assert "0.000" in footer

    def test_contains_latency(self, logger, record):
        footer = logger.format_stamp_footer(record)
        assert "317.5" in footer
        assert "ms" in footer

    def test_approved_verdict_bold(self, logger, record):
        footer = logger.format_stamp_footer(record)
        assert "**APPROVED**" in footer

    def test_blocked_verdict_bold(self, logger):
        r = _make_record(verdict="BLOCKED")
        footer = logger.format_stamp_footer(r)
        assert "**BLOCKED**" in footer

    def test_footer_different_records_differ(self, logger):
        r1 = _make_record(pr_id="PR-1", verdict="APPROVED")
        r2 = _make_record(pr_id="PR-2", verdict="BLOCKED")
        assert logger.format_stamp_footer(r1) != logger.format_stamp_footer(r2)

    def test_footer_high_latency(self, logger):
        r = _make_record(execution_latency_ms=99999.9)
        footer = logger.format_stamp_footer(r)
        assert "99999.9" in footer

    def test_footer_zero_bobcoins(self, logger):
        r = _make_record(bobcoins_consumed=0.0)
        footer = logger.format_stamp_footer(r)
        assert "0.00 coins" in footer
