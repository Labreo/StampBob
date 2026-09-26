"""
metrics.py — StampBob Offline Evaluation Metrics Engine.

Computes standard retrieval and classification metrics over the results of a
benchmark evaluation run.

Metrics
-------
* **Precision**        = TP / (TP + FP)
  Fraction of predicted violations that are real (anti-hallucination signal).
* **Recall**           = TP / (TP + FN)
  Fraction of real violations that were detected (coverage signal).
* **F1 Score**         = 2 * (P * R) / (P + R)
  Harmonic mean of precision and recall.
* **Hallucination Rate** = FP / |CLEAN PRs|
  Rate at which the oracle fires on known-clean PRs.  The CI gate hard-fails
  when this exceeds 0.05.
* **Category Accuracy** = Correct / Total within each benchmark category.

Design
------
The metric layer is *pure* — it has no I/O dependencies and operates entirely
on :class:`EvalResult` instances so it can be tested without a live oracle or
fixtures on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Per-PR evaluation result
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    """
    The outcome of evaluating a single golden fixture.

    Attributes
    ----------
    pr_id:
        Stable PR identifier, e.g. ``"PR-01"``.
    category:
        Benchmark category (``CLEAN``, ``GOROUTINE_LEAK``, …).
    expected_verdict:
        Ground-truth verdict from the fixture: ``"APPROVED"`` or
        ``"REJECTED"``.
    actual_verdict:
        Verdict produced by the oracle: ``"APPROVED"`` or ``"REJECTED"``.
    expected_rules:
        Rule IDs the oracle *should* have fired (empty for CLEAN fixtures).
    actual_rules:
        Rule IDs the oracle *actually* fired (derived from
        ``InvariantViolation.rule_id``).
    sandbox_confirmed:
        ``True`` when at least one violation was confirmed by sandbox execution.
        ``None`` when the sandbox was not invoked (no violations found).
    error:
        Human-readable description of any runtime error during evaluation.
        ``None`` on clean runs.
    """

    pr_id: str
    category: str
    expected_verdict: str
    actual_verdict: str
    expected_rules: List[str]
    actual_rules: List[str]
    sandbox_confirmed: Optional[bool] = None
    error: Optional[str] = None

    # --- Derived convenience properties ---

    @property
    def is_true_positive(self) -> bool:
        """Oracle correctly predicted REJECTED on a genuinely defective PR."""
        return self.expected_verdict == "REJECTED" and self.actual_verdict == "REJECTED"

    @property
    def is_false_positive(self) -> bool:
        """Oracle incorrectly predicted REJECTED on a clean PR (hallucination)."""
        return self.expected_verdict == "APPROVED" and self.actual_verdict == "REJECTED"

    @property
    def is_true_negative(self) -> bool:
        """Oracle correctly predicted APPROVED on a clean PR."""
        return self.expected_verdict == "APPROVED" and self.actual_verdict == "APPROVED"

    @property
    def is_false_negative(self) -> bool:
        """Oracle missed a real defect (predicted APPROVED on a defective PR)."""
        return self.expected_verdict == "REJECTED" and self.actual_verdict == "APPROVED"

    @property
    def verdict_correct(self) -> bool:
        """True when ``actual_verdict == expected_verdict``."""
        return self.actual_verdict == self.expected_verdict


# ---------------------------------------------------------------------------
# Aggregate evaluation summary
# ---------------------------------------------------------------------------

@dataclass
class EvalSummary:
    """
    Aggregate metrics computed over a full benchmark run.

    Attributes
    ----------
    results:
        The full list of per-PR :class:`EvalResult` objects.
    precision:
        TP / (TP + FP).  ``1.0`` when FP == 0 (no hallucinations, including
        the edge case of TP + FP == 0).
    recall:
        TP / (TP + FN).  ``0.0`` when TP + FN == 0 (no defective PRs).
    f1_score:
        Harmonic mean of precision and recall.  ``0.0`` when both are zero.
    hallucination_rate:
        FP / |CLEAN PRs|.  ``0.0`` when there are no CLEAN PRs or no FPs.
    category_accuracy:
        Mapping of category name → fraction of correctly classified PRs
        within that category.
    total:
        Total number of PRs evaluated.
    true_positives:
        Count of correct REJECTED predictions.
    false_positives:
        Count of incorrect REJECTED predictions (hallucinations).
    true_negatives:
        Count of correct APPROVED predictions.
    false_negatives:
        Count of missed defects.
    passed_ci_gate:
        ``True`` when precision ≥ ``min_precision_threshold`` AND
        hallucination_rate ≤ 0.05.
    min_precision_threshold:
        The threshold used to evaluate ``passed_ci_gate``.
    """

    results: List[EvalResult]
    precision: float
    recall: float
    f1_score: float
    hallucination_rate: float
    category_accuracy: Dict[str, float]
    total: int
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    passed_ci_gate: bool
    min_precision_threshold: float

    @property
    def overall_accuracy(self) -> float:
        """Fraction of all PRs with a correct verdict."""
        if self.total == 0:
            return 0.0
        correct = self.true_positives + self.true_negatives
        return correct / self.total


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def compute_metrics(
    results: List[EvalResult],
    min_precision_threshold: float = 0.92,
) -> EvalSummary:
    """
    Compute all evaluation metrics from a list of per-PR results.

    Parameters
    ----------
    results:
        List of :class:`EvalResult` objects, one per evaluated fixture.
    min_precision_threshold:
        Minimum precision required to pass the CI gate.  Defaults to ``0.92``.

    Returns
    -------
    EvalSummary
        Fully populated summary including per-category accuracy, CI gate
        status, and all confusion-matrix counts.

    Notes
    -----
    Edge cases:
    * When ``TP + FP == 0`` (no positive predictions), precision is defined
      as ``1.0`` (the oracle made no false claims).
    * When ``TP + FN == 0`` (no defective PRs in the dataset), recall is
      defined as ``0.0``.
    * When ``precision + recall == 0``, F1 is defined as ``0.0``.
    * When there are no CLEAN PRs, hallucination rate is ``0.0``.
    """
    tp = sum(1 for r in results if r.is_true_positive)
    fp = sum(1 for r in results if r.is_false_positive)
    tn = sum(1 for r in results if r.is_true_negative)
    fn = sum(1 for r in results if r.is_false_negative)

    # --- Precision ---
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0

    # --- Recall ---
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    # --- F1 ---
    if precision + recall > 0:
        f1 = 2.0 * precision * recall / (precision + recall)
    else:
        f1 = 0.0

    # --- Hallucination Rate: FP / |CLEAN PRs| ---
    clean_total = sum(1 for r in results if r.expected_verdict == "APPROVED")
    hallucination_rate = fp / clean_total if clean_total > 0 else 0.0

    # --- Per-category accuracy ---
    category_totals: Dict[str, int] = {}
    category_correct: Dict[str, int] = {}
    for r in results:
        category_totals[r.category] = category_totals.get(r.category, 0) + 1
        if r.verdict_correct:
            category_correct[r.category] = category_correct.get(r.category, 0) + 1

    category_accuracy: Dict[str, float] = {
        cat: (category_correct.get(cat, 0) / total)
        for cat, total in category_totals.items()
    }

    # --- CI gate ---
    HALLUCINATION_GATE = 0.05
    passed_ci_gate = (
        precision >= min_precision_threshold
        and hallucination_rate <= HALLUCINATION_GATE
    )

    return EvalSummary(
        results=results,
        precision=precision,
        recall=recall,
        f1_score=f1,
        hallucination_rate=hallucination_rate,
        category_accuracy=category_accuracy,
        total=len(results),
        true_positives=tp,
        false_positives=fp,
        true_negatives=tn,
        false_negatives=fn,
        passed_ci_gate=passed_ci_gate,
        min_precision_threshold=min_precision_threshold,
    )
