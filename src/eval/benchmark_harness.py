"""
benchmark_harness.py — StampBob Offline Evaluation Benchmark Harness.

Evaluates the full review pipeline against the 50-fixture golden dataset and
enforces CI quality gates on precision and hallucination rate.

Pipeline per fixture
---------------------
1. Load all golden fixtures via :func:`load_golden_dataset`.
2. For each fixture, call ``InvariantOracle().audit_diff(diff_content, repo_index)``.
3. If violations are found, synthesize a repro test and run it in the sandbox
   to confirm each violation.
4. Derive the actual verdict: ``"REJECTED"`` if any violation was detected,
   ``"APPROVED"`` otherwise.
5. Compare against ground truth and accumulate :class:`EvalResult` instances.
6. Compute aggregate metrics via :func:`compute_metrics`.
7. Print a formatted ASCII summary table.
8. Exit with code 1 when precision < threshold OR hallucination rate > 0.05.

CLI usage
---------
::

    python -m src.eval.benchmark_harness
    python -m src.eval.benchmark_harness --threshold 0.95
    python -m src.eval.benchmark_harness --dataset-path path/to/fixtures --threshold 0.90
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import List, Optional

from src.engine.context_indexer import RepositoryIndex
from src.engine.invariant_oracle import InvariantOracle
from src.eval.dataset_loader import GoldenPR, load_golden_dataset
from src.eval.metrics import EvalResult, EvalSummary, compute_metrics
from src.generator.repro_synthesizer import synthesize_repro_test
from src.generator.sandbox_runner import run_repro_in_sandbox


# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_DATASET_PATH = "fixtures/golden_prs"
DEFAULT_MIN_PRECISION = 0.92
_HALLUCINATION_GATE = 0.05


# ---------------------------------------------------------------------------
# Empty repo index — used when no source tree is available during eval
# ---------------------------------------------------------------------------

def _empty_repo_index() -> RepositoryIndex:
    """Return a minimal RepositoryIndex with no indexed files.

    ``InvariantOracle.audit_diff`` operates in diff-only mode when the index
    contains no entry for a file — it falls back to the lines added in the
    diff itself, which is sufficient for the golden benchmark fixtures.
    """
    return RepositoryIndex(root=".")


# ---------------------------------------------------------------------------
# Single-fixture evaluation
# ---------------------------------------------------------------------------

def evaluate_fixture(
    fixture: GoldenPR,
    oracle: InvariantOracle,
    repo_index: RepositoryIndex,
    run_sandbox: bool = True,
) -> EvalResult:
    """
    Evaluate one golden fixture end-to-end.

    Parameters
    ----------
    fixture:
        The golden PR to evaluate.
    oracle:
        Pre-constructed :class:`InvariantOracle` instance (shared across all
        fixtures to avoid repeated checker compilation).
    repo_index:
        Repository index passed to ``audit_diff``.  Typically an empty index
        for offline evaluation.
    run_sandbox:
        When ``True`` (default), synthesize and execute a repro test for each
        violation found.  Set to ``False`` in unit tests to skip I/O.

    Returns
    -------
    EvalResult
        Per-fixture result suitable for :func:`compute_metrics`.
    """
    error: Optional[str] = None
    actual_rules: List[str] = []
    sandbox_confirmed: Optional[bool] = None

    try:
        violations = oracle.audit_diff(fixture.diff_content, repo_index)
    except Exception as exc:  # noqa: BLE001
        error = f"oracle error: {exc}"
        violations = []

    actual_rules = [v.rule_id for v in violations]
    actual_verdict = "REJECTED" if violations else "APPROVED"

    # --- Optional sandbox confirmation ---
    if violations and run_sandbox:
        confirmed_any = False
        for violation in violations:
            try:
                repro = synthesize_repro_test(violation)
                result = run_repro_in_sandbox(repro)
                if result.confirmed:
                    confirmed_any = True
                    break
            except Exception as exc:  # noqa: BLE001
                # Sandbox errors are non-fatal for the eval verdict; record but
                # continue so a single sandbox failure doesn't corrupt metrics.
                if error is None:
                    error = f"sandbox error: {exc}"
        sandbox_confirmed = confirmed_any

    return EvalResult(
        pr_id=fixture.pr_id,
        category=fixture.category,
        expected_verdict=fixture.expected_verdict,
        actual_verdict=actual_verdict,
        expected_rules=fixture.expected_rules,
        actual_rules=actual_rules,
        sandbox_confirmed=sandbox_confirmed,
        error=error,
    )


# ---------------------------------------------------------------------------
# ASCII summary table
# ---------------------------------------------------------------------------

_CATEGORY_ORDER = [
    "CLEAN",
    "GOROUTINE_LEAK",
    "UNBUFFERED_CHANNEL",
    "NIL_DEREF",
    "OTEL_OMISSION",
]

_COL_WIDTHS = {
    "category": 22,
    "total": 7,
    "correct": 9,
    "accuracy": 10,
    "tp": 4,
    "fp": 4,
    "fn": 4,
}


def _hbar(widths: dict) -> str:
    return "+" + "+".join("-" * (w + 2) for w in widths.values()) + "+"


def _row(values: list, widths: dict) -> str:
    cells = []
    for val, width in zip(values, widths.values()):
        cells.append(f" {str(val):<{width}} ")
    return "|" + "|".join(cells) + "|"


def format_summary_table(summary: EvalSummary) -> str:
    """
    Render an ASCII summary table of the benchmark evaluation.

    Returns a multi-line string ready for ``print()``.
    """
    lines: List[str] = []

    header_title = " StampBob Offline Evaluation Benchmark "
    table_width = sum(w + 3 for w in _COL_WIDTHS.values()) + 1
    lines.append("=" * table_width)
    lines.append(header_title.center(table_width))
    lines.append("=" * table_width)

    # --- Per-category table ---
    hbar = _hbar(_COL_WIDTHS)
    header_vals = ["CATEGORY", "TOTAL", "CORRECT", "ACCURACY", "TP", "FP", "FN"]
    lines.append(hbar)
    lines.append(_row(header_vals, _COL_WIDTHS))
    lines.append(hbar)

    # Build per-category row data from results
    cat_data: dict = {c: {"total": 0, "correct": 0, "tp": 0, "fp": 0, "fn": 0}
                      for c in _CATEGORY_ORDER}
    for r in summary.results:
        c = r.category
        if c not in cat_data:
            cat_data[c] = {"total": 0, "correct": 0, "tp": 0, "fp": 0, "fn": 0}
        cat_data[c]["total"] += 1
        if r.verdict_correct:
            cat_data[c]["correct"] += 1
        if r.is_true_positive:
            cat_data[c]["tp"] += 1
        if r.is_false_positive:
            cat_data[c]["fp"] += 1
        if r.is_false_negative:
            cat_data[c]["fn"] += 1

    for cat in sorted(cat_data.keys()):
        d = cat_data[cat]
        acc = summary.category_accuracy.get(cat, 0.0)
        acc_str = f"{acc * 100:.1f}%"
        row_vals = [cat, d["total"], d["correct"], acc_str,
                    d["tp"], d["fp"], d["fn"]]
        lines.append(_row(row_vals, _COL_WIDTHS))

    lines.append(hbar)
    lines.append("")

    # --- Aggregate metrics ---
    lines.append(f"  {'Overall Accuracy':<26} {summary.overall_accuracy * 100:.1f}%")
    lines.append(f"  {'Precision':<26} {summary.precision:.4f}"
                 f"  (threshold: ≥{summary.min_precision_threshold:.2f})")
    lines.append(f"  {'Recall':<26} {summary.recall:.4f}")
    lines.append(f"  {'F1 Score':<26} {summary.f1_score:.4f}")
    lines.append(f"  {'Hallucination Rate':<26} {summary.hallucination_rate:.4f}"
                 f"  (gate: ≤{_HALLUCINATION_GATE:.2f})")
    lines.append(f"  {'True Positives':<26} {summary.true_positives}")
    lines.append(f"  {'False Positives':<26} {summary.false_positives}")
    lines.append(f"  {'True Negatives':<26} {summary.true_negatives}")
    lines.append(f"  {'False Negatives':<26} {summary.false_negatives}")
    lines.append(f"  {'Total PRs evaluated':<26} {summary.total}")
    lines.append("")

    # --- CI gate verdict ---
    gate_label = "✓  PASSED" if summary.passed_ci_gate else "✗  FAILED"
    lines.append("=" * table_width)
    lines.append(f"  CI Gate: {gate_label}")
    if not summary.passed_ci_gate:
        if summary.precision < summary.min_precision_threshold:
            lines.append(
                f"  → Precision {summary.precision:.4f} < "
                f"threshold {summary.min_precision_threshold:.2f}"
            )
        if summary.hallucination_rate > _HALLUCINATION_GATE:
            lines.append(
                f"  → Hallucination rate {summary.hallucination_rate:.4f} > "
                f"gate {_HALLUCINATION_GATE:.2f}"
            )
    lines.append("=" * table_width)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_offline_eval(
    dataset_path: str = DEFAULT_DATASET_PATH,
    min_precision_threshold: float = DEFAULT_MIN_PRECISION,
    run_sandbox: bool = True,
    _oracle: Optional[InvariantOracle] = None,
) -> EvalSummary:
    """
    Run the full offline evaluation benchmark and return an :class:`EvalSummary`.

    Steps
    -----
    1. Load all golden fixtures from *dataset_path*.
    2. Evaluate each fixture against the :class:`InvariantOracle`.
    3. Optionally confirm violations in the sandbox.
    4. Compute metrics via :func:`compute_metrics`.
    5. Print the ASCII summary table to stdout.
    6. If the CI gate fails, exit with code 1.

    Parameters
    ----------
    dataset_path:
        Path to the directory containing golden fixture JSON files.
    min_precision_threshold:
        Minimum precision to pass the CI gate.
    run_sandbox:
        When ``True`` (default), sandbox-confirm each violation.  Set to
        ``False`` to skip sandbox execution during unit tests.
    _oracle:
        Optional pre-built oracle (used in tests to inject a mock).

    Returns
    -------
    EvalSummary
        Fully populated metrics summary.

    Raises
    ------
    SystemExit(1)
        When the CI gate conditions are not met (precision or hallucination
        rate outside acceptable bounds).
    """
    fixtures = load_golden_dataset(dataset_path)
    oracle = _oracle if _oracle is not None else InvariantOracle()
    repo_index = _empty_repo_index()

    print(f"\nEvaluating {len(fixtures)} golden fixtures …", flush=True)
    t_start = time.monotonic()

    results: List[EvalResult] = []
    for i, fixture in enumerate(fixtures, 1):
        result = evaluate_fixture(fixture, oracle, repo_index, run_sandbox=run_sandbox)
        results.append(result)
        status_char = "✓" if result.verdict_correct else "✗"
        print(
            f"  [{i:>2}/{len(fixtures)}] {status_char} {fixture.pr_id}"
            f"  ({fixture.category})"
            + (f"  ← {result.error}" if result.error else ""),
            flush=True,
        )

    elapsed = time.monotonic() - t_start
    print(f"\nCompleted in {elapsed:.1f}s\n", flush=True)

    summary = compute_metrics(results, min_precision_threshold=min_precision_threshold)
    print(format_summary_table(summary))

    if not summary.passed_ci_gate:
        sys.exit(1)

    return summary


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.eval.benchmark_harness",
        description=(
            "StampBob Offline Evaluation Benchmark — evaluates the review "
            "pipeline against 50 golden PR fixtures and enforces CI quality "
            "gates on precision and hallucination rate."
        ),
    )
    parser.add_argument(
        "--dataset-path",
        default=DEFAULT_DATASET_PATH,
        metavar="PATH",
        help=(
            "Path to the directory containing golden fixture JSON files. "
            f"Default: {DEFAULT_DATASET_PATH}"
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_MIN_PRECISION,
        dest="min_precision_threshold",
        metavar="FLOAT",
        help=(
            "Minimum precision [0.0–1.0] required to pass the CI gate. "
            f"Default: {DEFAULT_MIN_PRECISION}"
        ),
    )
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        default=False,
        help="Skip sandbox execution of repro tests (faster, static analysis only).",
    )
    return parser


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    run_offline_eval(
        dataset_path=args.dataset_path,
        min_precision_threshold=args.min_precision_threshold,
        run_sandbox=not args.no_sandbox,
    )
