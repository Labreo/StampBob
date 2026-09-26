"""
factsheet_logger.py — IBM watsonx.governance AI Factsheet-style telemetry ledger.

Records complete governance metadata for every StampBob review session so that
enterprise engineering teams have a fully auditable AI development budget trail.

The ledger is a newline-delimited JSON file (``telemetry/factsheet_ledger.json``)
where each line is one JSON-serialised :class:`FactsheetRecord`.  This format is
append-friendly, survives concurrent writes, and is trivially parseable by any
log aggregator.

Usage
-----
    from src.telemetry.factsheet_logger import FactsheetLogger, FactsheetRecord
    import datetime

    record = FactsheetRecord(
        pr_id="PR-42",
        commit_sha="abc1234",
        timestamp=datetime.datetime.utcnow().isoformat() + "Z",
        bob_task_id="task-007",
        mode="reviewer",
        bobcoins_consumed=3.14,
        tokens_processed=8192,
        offline_eval_precision=0.942,
        granite_guardian_score=0.0,
        execution_latency_ms=412.7,
        verdict="APPROVED",
    )
    logger = FactsheetLogger()
    logger.log_session(record)
    print(logger.format_stamp_footer(record))
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# FactsheetRecord
# ---------------------------------------------------------------------------

@dataclass
class FactsheetRecord:
    """
    Immutable governance snapshot for a single StampBob review run.

    Fields mirror the core dimensions tracked by IBM watsonx.governance
    AI Factsheets: identity, timing, cost, quality, safety, and outcome.

    Attributes
    ----------
    pr_id:
        Pull-request or work-item identifier (e.g. ``"PR-42"``).
    commit_sha:
        Full or abbreviated commit SHA under review.
    timestamp:
        ISO 8601 UTC timestamp when the session completed
        (e.g. ``"2024-06-01T12:00:00Z"``).
    bob_task_id:
        StampBob internal task correlation identifier.
    mode:
        Operating mode (typically ``"reviewer"``).
    bobcoins_consumed:
        AI compute budget consumed in BobCoins.
    tokens_processed:
        Total LLM tokens consumed (prompt + completion).
    offline_eval_precision:
        Precision score from the most-recent offline evaluation run
        (e.g. ``0.942``).
    granite_guardian_score:
        Risk score returned by :class:`GraniteGuardianFilter` for
        the generated output (``0.0`` = fully safe).
    execution_latency_ms:
        Wall-clock latency of the full review pipeline in milliseconds.
    verdict:
        Final review outcome: ``"APPROVED"`` or ``"BLOCKED"``.
    """

    pr_id: str
    commit_sha: str
    timestamp: str
    bob_task_id: str
    mode: str
    bobcoins_consumed: float
    tokens_processed: int
    offline_eval_precision: float
    granite_guardian_score: float
    execution_latency_ms: float
    verdict: str


# ---------------------------------------------------------------------------
# FactsheetLogger
# ---------------------------------------------------------------------------

_DEFAULT_LEDGER_PATH = "telemetry/factsheet_ledger.json"


class FactsheetLogger:
    """
    Appends :class:`FactsheetRecord` entries to a newline-delimited JSON
    ledger file and formats Verified Stamp Card audit footers.

    Parameters
    ----------
    ledger_path:
        Path to the JSON ledger file.  Parent directory is created on first
        write.  Defaults to ``telemetry/factsheet_ledger.json`` relative to
        the current working directory.
    """

    def __init__(self, ledger_path: Optional[str] = None) -> None:
        self._ledger_path = Path(ledger_path or _DEFAULT_LEDGER_PATH)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_session(self, record: FactsheetRecord) -> None:
        """
        Append *record* to the ledger as a single JSON line.

        The parent directory is created if it does not exist.  Each call
        opens, appends, and closes the file so the ledger survives
        process restarts without data loss.

        Parameters
        ----------
        record:
            Fully populated :class:`FactsheetRecord` to persist.
        """
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(record), ensure_ascii=False)
        with self._ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def format_stamp_footer(self, record: FactsheetRecord) -> str:
        """
        Return a single-line Markdown audit block for inclusion in the
        Verified Stamp Card.

        The footer is intentionally terse so it fits inside a PR comment
        without wrapping.  Example output::

            > 🔏 StampBob · PR-42 · abc1234 · 2024-06-01T12:00:00Z · \
task-007 · reviewer · 3.14 coins · 8192 tok · prec=0.942 · \
guardian=0.000 · 412.7ms · **APPROVED**

        Parameters
        ----------
        record:
            The session record whose metadata should be serialised.

        Returns
        -------
        str
            A single Markdown line (no trailing newline).
        """
        return (
            f"> 🔏 StampBob"
            f" · {record.pr_id}"
            f" · {record.commit_sha}"
            f" · {record.timestamp}"
            f" · {record.bob_task_id}"
            f" · {record.mode}"
            f" · {record.bobcoins_consumed:.2f} coins"
            f" · {record.tokens_processed} tok"
            f" · prec={record.offline_eval_precision:.3f}"
            f" · guardian={record.granite_guardian_score:.3f}"
            f" · {record.execution_latency_ms:.1f}ms"
            f" · **{record.verdict}**"
        )
