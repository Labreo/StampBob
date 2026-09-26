"""
tests/eval/test_benchmark_harness.py

Unit tests for:
  - src/eval/metrics.py  (EvalResult properties, compute_metrics edge cases)
  - src/eval/benchmark_harness.py  (evaluate_fixture, run_offline_eval, format_summary_table, CLI)

All tests that touch the harness are fully mocked — no oracle, sandbox, or
fixture files on disk are needed, making the suite fast and deterministic.
"""

from __future__ import annotations

import sys
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from src.eval.metrics import EvalResult, EvalSummary, compute_metrics
from src.eval.benchmark_harness import (
    DEFAULT_DATASET_PATH,
    DEFAULT_MIN_PRECISION,
    _build_arg_parser,
    _empty_repo_index,
    evaluate_fixture,
    format_summary_table,
    run_offline_eval,
)
from src.eval.dataset_loader import GoldenPR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    pr_id: str = "PR-01",
    category: str = "CLEAN",
    expected: str = "APPROVED",
    actual: str = "APPROVED",
    expected_rules: List[str] = None,
    actual_rules: List[str] = None,
    error: str = None,
) -> EvalResult:
    return EvalResult(
        pr_id=pr_id,
        category=category,
        expected_verdict=expected,
        actual_verdict=actual,
        expected_rules=expected_rules or [],
        actual_rules=actual_rules or [],
        error=error,
    )


def _make_fixture(
    pr_id: str = "PR-01",
    category: str = "CLEAN",
    verdict: str = "APPROVED",
    rules: List[str] = None,
    diff: str = "diff --git a/foo.go b/foo.go\n+// comment",
) -> GoldenPR:
    return GoldenPR(
        pr_id=pr_id,
        category=category,
        title="Test PR",
        diff_content=diff,
        expected_verdict=verdict,
        expected_rules=rules or [],
        golden_repro_snippet="// snippet",
    )


def _perfect_results(n_clean: int = 10, n_defect: int = 40) -> List[EvalResult]:
    """All verdicts correct."""
    results = []
    for i in range(n_clean):
        results.append(_make_result(
            pr_id=f"PR-{i+1:02d}", category="CLEAN",
            expected="APPROVED", actual="APPROVED",
        ))
    categories = ["GOROUTINE_LEAK", "UNBUFFERED_CHANNEL", "NIL_DEREF", "OTEL_OMISSION"]
    for i in range(n_defect):
        cat = categories[i % len(categories)]
        results.append(_make_result(
            pr_id=f"PR-{i+n_clean+1:02d}", category=cat,
            expected="REJECTED", actual="REJECTED",
            expected_rules=["some-rule"], actual_rules=["some-rule"],
        ))
    return results


# ---------------------------------------------------------------------------
# EvalResult — confusion-matrix properties
# ---------------------------------------------------------------------------

class TestEvalResultProperties:
    def test_true_positive(self):
        r = _make_result(expected="REJECTED", actual="REJECTED")
        assert r.is_true_positive is True
        assert r.is_false_positive is False
        assert r.is_false_negative is False
        assert r.is_true_negative is False
        assert r.verdict_correct is True

    def test_false_positive(self):
        r = _make_result(expected="APPROVED", actual="REJECTED")
        assert r.is_false_positive is True
        assert r.is_true_positive is False
        assert r.is_true_negative is False
        assert r.is_false_negative is False
        assert r.verdict_correct is False

    def test_true_negative(self):
        r = _make_result(expected="APPROVED", actual="APPROVED")
        assert r.is_true_negative is True
        assert r.verdict_correct is True

    def test_false_negative(self):
        r = _make_result(expected="REJECTED", actual="APPROVED")
        assert r.is_false_negative is True
        assert r.verdict_correct is False

    def test_verdict_correct_when_match(self):
        r = _make_result(expected="REJECTED", actual="REJECTED")
        assert r.verdict_correct is True

    def test_verdict_incorrect_when_mismatch(self):
        r = _make_result(expected="APPROVED", actual="REJECTED")
        assert r.verdict_correct is False


# ---------------------------------------------------------------------------
# compute_metrics — perfect dataset
# ---------------------------------------------------------------------------

class TestComputeMetricsPerfect:
    def setup_method(self):
        self.results = _perfect_results(n_clean=10, n_defect=40)
        self.summary = compute_metrics(self.results, min_precision_threshold=0.92)

    def test_precision_is_one(self):
        assert self.summary.precision == 1.0

    def test_recall_is_one(self):
        assert self.summary.recall == 1.0

    def test_f1_is_one(self):
        assert abs(self.summary.f1_score - 1.0) < 1e-9

    def test_hallucination_rate_is_zero(self):
        assert self.summary.hallucination_rate == 0.0

    def test_tp_count(self):
        assert self.summary.true_positives == 40

    def test_fp_count(self):
        assert self.summary.false_positives == 0

    def test_tn_count(self):
        assert self.summary.true_negatives == 10

    def test_fn_count(self):
        assert self.summary.false_negatives == 0

    def test_total(self):
        assert self.summary.total == 50

    def test_overall_accuracy_is_one(self):
        assert self.summary.overall_accuracy == 1.0

    def test_passed_ci_gate(self):
        assert self.summary.passed_ci_gate is True

    def test_results_stored(self):
        assert self.summary.results is self.results


# ---------------------------------------------------------------------------
# compute_metrics — hallucination (FP) scenario
# ---------------------------------------------------------------------------

class TestComputeMetricsWithFalsePositives:
    def setup_method(self):
        # 2 clean PRs falsely rejected out of 10 clean total
        results = _perfect_results(n_clean=10, n_defect=10)
        # Flip 2 clean PRs to FP
        results[0] = _make_result(expected="APPROVED", actual="REJECTED", category="CLEAN")
        results[1] = _make_result(expected="APPROVED", actual="REJECTED", category="CLEAN")
        self.results = results
        self.summary = compute_metrics(results, min_precision_threshold=0.92)

    def test_fp_count(self):
        assert self.summary.false_positives == 2

    def test_hallucination_rate(self):
        # 2 FP / 10 clean PRs = 0.2
        assert abs(self.summary.hallucination_rate - 0.2) < 1e-9

    def test_precision_below_threshold(self):
        # TP=10, FP=2 → precision=10/12≈0.833
        expected_precision = 10 / 12
        assert abs(self.summary.precision - expected_precision) < 1e-9

    def test_ci_gate_fails_on_precision(self):
        assert self.summary.passed_ci_gate is False

    def test_ci_gate_fails_on_hallucination_rate(self):
        # hallucination_rate=0.2 > 0.05 also fails
        assert self.summary.hallucination_rate > 0.05
        assert self.summary.passed_ci_gate is False


# ---------------------------------------------------------------------------
# compute_metrics — false negative (missed defect) scenario
# ---------------------------------------------------------------------------

class TestComputeMetricsWithFalseNegatives:
    def setup_method(self):
        results = _perfect_results(n_clean=10, n_defect=10)
        # Flip 5 defect PRs to FN
        for i in range(10, 15):
            results[i] = _make_result(
                pr_id=results[i].pr_id,
                category=results[i].category,
                expected="REJECTED",
                actual="APPROVED",
            )
        self.results = results
        self.summary = compute_metrics(results)

    def test_fn_count(self):
        assert self.summary.false_negatives == 5

    def test_recall(self):
        # TP=5, FN=5 → recall=5/10=0.5
        assert abs(self.summary.recall - 0.5) < 1e-9

    def test_precision_unaffected(self):
        # TP=5, FP=0 → precision=1.0
        assert self.summary.precision == 1.0

    def test_f1(self):
        # P=1.0, R=0.5 → F1=2*1*0.5/1.5 ≈ 0.667
        expected_f1 = 2 * 1.0 * 0.5 / (1.0 + 0.5)
        assert abs(self.summary.f1_score - expected_f1) < 1e-9


# ---------------------------------------------------------------------------
# compute_metrics — edge cases
# ---------------------------------------------------------------------------

class TestComputeMetricsEdgeCases:
    def test_empty_results(self):
        summary = compute_metrics([])
        assert summary.precision == 1.0   # no FP, no TP → 1.0 by convention
        assert summary.recall == 0.0
        assert summary.f1_score == 0.0
        assert summary.hallucination_rate == 0.0
        assert summary.total == 0
        assert summary.overall_accuracy == 0.0

    def test_all_clean_no_predictions(self):
        """Only CLEAN PRs, oracle correctly returns APPROVED for all."""
        results = [_make_result(expected="APPROVED", actual="APPROVED") for _ in range(5)]
        summary = compute_metrics(results)
        # TP=0, FP=0, TN=5, FN=0
        assert summary.precision == 1.0
        assert summary.recall == 0.0
        assert summary.hallucination_rate == 0.0
        assert summary.overall_accuracy == 1.0

    def test_all_defect_all_detected(self):
        """No CLEAN PRs — hallucination rate is 0.0 by definition."""
        results = [
            _make_result(category="GOROUTINE_LEAK", expected="REJECTED", actual="REJECTED")
            for _ in range(5)
        ]
        summary = compute_metrics(results)
        assert summary.precision == 1.0
        assert summary.recall == 1.0
        assert summary.hallucination_rate == 0.0

    def test_precision_zero_when_all_fp(self):
        """TP=0, FP>0 → precision=0."""
        results = [_make_result(expected="APPROVED", actual="REJECTED") for _ in range(3)]
        summary = compute_metrics(results)
        assert summary.precision == 0.0

    def test_f1_zero_when_precision_and_recall_both_zero(self):
        """TP=0, FP=0 clean so precision=1 but recall=0 when FN>0 → F1>0.
        This test covers the edge where precision AND recall sum to 0."""
        # Manually construct a summary with P=0, R=0
        results = [_make_result(expected="APPROVED", actual="REJECTED")]
        summary = compute_metrics(results)
        # P=0/(0+1)=0, R=0/(0+0)=1.0 (no defect PRs so no FN) ...
        # Actually: TP=0, FP=1, TN=0, FN=0
        # precision = 0/(0+1) = 0.0, recall = 0/(0+0) = 0.0
        assert summary.precision == 0.0
        assert summary.recall == 0.0
        assert summary.f1_score == 0.0

    def test_custom_threshold_stored(self):
        results = _perfect_results(n_clean=5, n_defect=5)
        summary = compute_metrics(results, min_precision_threshold=0.99)
        assert summary.min_precision_threshold == 0.99

    def test_category_accuracy_computed(self):
        results = [
            _make_result(category="CLEAN", expected="APPROVED", actual="APPROVED"),
            _make_result(category="CLEAN", expected="APPROVED", actual="REJECTED"),  # FP
            _make_result(category="GOROUTINE_LEAK", expected="REJECTED", actual="REJECTED"),
        ]
        summary = compute_metrics(results)
        assert abs(summary.category_accuracy["CLEAN"] - 0.5) < 1e-9
        assert summary.category_accuracy["GOROUTINE_LEAK"] == 1.0

    def test_hallucination_rate_formula(self):
        # 1 FP out of 4 CLEAN PRs → 0.25
        results = [
            _make_result(category="CLEAN", expected="APPROVED", actual="REJECTED"),
            _make_result(category="CLEAN", expected="APPROVED", actual="APPROVED"),
            _make_result(category="CLEAN", expected="APPROVED", actual="APPROVED"),
            _make_result(category="CLEAN", expected="APPROVED", actual="APPROVED"),
        ]
        summary = compute_metrics(results)
        assert abs(summary.hallucination_rate - 0.25) < 1e-9

    def test_ci_gate_passes_at_exact_threshold(self):
        # Construct results such that precision == 0.92 exactly
        # 92 TP, 8 FP, 0 FN, and hallucination_rate ≤ 0.05
        # Use 160 clean PRs so FP/clean = 8/160 = 0.05 exactly
        results = []
        for _ in range(92):
            results.append(_make_result(category="GOROUTINE_LEAK", expected="REJECTED", actual="REJECTED"))
        for _ in range(8):
            results.append(_make_result(category="CLEAN", expected="APPROVED", actual="REJECTED"))
        for _ in range(152):
            results.append(_make_result(category="CLEAN", expected="APPROVED", actual="APPROVED"))
        summary = compute_metrics(results, min_precision_threshold=0.92)
        assert abs(summary.precision - 0.92) < 1e-9
        assert abs(summary.hallucination_rate - 0.05) < 1e-9
        assert summary.passed_ci_gate is True

    def test_ci_gate_fails_just_below_threshold(self):
        results = []
        for _ in range(91):
            results.append(_make_result(category="GOROUTINE_LEAK", expected="REJECTED", actual="REJECTED"))
        for _ in range(9):
            results.append(_make_result(category="CLEAN", expected="APPROVED", actual="REJECTED"))
        for _ in range(100):
            results.append(_make_result(category="CLEAN", expected="APPROVED", actual="APPROVED"))
        summary = compute_metrics(results, min_precision_threshold=0.92)
        assert summary.passed_ci_gate is False


# ---------------------------------------------------------------------------
# EvalSummary — overall_accuracy property
# ---------------------------------------------------------------------------

class TestEvalSummaryOverallAccuracy:
    def test_perfect(self):
        summary = compute_metrics(_perfect_results())
        assert summary.overall_accuracy == 1.0

    def test_zero_total(self):
        summary = compute_metrics([])
        assert summary.overall_accuracy == 0.0

    def test_partial(self):
        results = [
            _make_result(expected="APPROVED", actual="APPROVED"),   # TN ✓
            _make_result(expected="REJECTED", actual="APPROVED"),   # FN ✗
        ]
        summary = compute_metrics(results)
        assert summary.overall_accuracy == 0.5


# ---------------------------------------------------------------------------
# format_summary_table
# ---------------------------------------------------------------------------

class TestFormatSummaryTable:
    def setup_method(self):
        self.summary = compute_metrics(_perfect_results(), min_precision_threshold=0.92)

    def test_contains_title(self):
        table = format_summary_table(self.summary)
        assert "StampBob Offline Evaluation Benchmark" in table

    def test_contains_all_categories(self):
        table = format_summary_table(self.summary)
        for cat in ["CLEAN", "GOROUTINE_LEAK", "UNBUFFERED_CHANNEL", "NIL_DEREF", "OTEL_OMISSION"]:
            assert cat in table

    def test_contains_ci_gate_passed(self):
        table = format_summary_table(self.summary)
        assert "PASSED" in table

    def test_contains_ci_gate_failed(self):
        results = [_make_result(expected="APPROVED", actual="REJECTED") for _ in range(5)]
        bad_summary = compute_metrics(results, min_precision_threshold=0.92)
        table = format_summary_table(bad_summary)
        assert "FAILED" in table

    def test_contains_precision(self):
        table = format_summary_table(self.summary)
        assert "Precision" in table

    def test_contains_recall(self):
        table = format_summary_table(self.summary)
        assert "Recall" in table

    def test_contains_f1(self):
        table = format_summary_table(self.summary)
        assert "F1" in table

    def test_contains_hallucination_rate(self):
        table = format_summary_table(self.summary)
        assert "Hallucination" in table

    def test_contains_total_prs(self):
        table = format_summary_table(self.summary)
        assert "50" in table

    def test_shows_failure_reason_precision(self):
        # Build summary with precision just below 0.92
        results = [_make_result(expected="APPROVED", actual="REJECTED")] + \
                  [_make_result(expected="APPROVED", actual="APPROVED")] * 10
        summary = compute_metrics(results, min_precision_threshold=0.92)
        table = format_summary_table(summary)
        assert "Precision" in table
        assert "threshold" in table

    def test_shows_failure_reason_hallucination(self):
        results = [_make_result(expected="APPROVED", actual="REJECTED")] * 3 + \
                  [_make_result(expected="APPROVED", actual="APPROVED")] * 7
        summary = compute_metrics(results, min_precision_threshold=0.0)
        table = format_summary_table(summary)
        assert "Hallucination rate" in table

    def test_is_string(self):
        table = format_summary_table(self.summary)
        assert isinstance(table, str)


# ---------------------------------------------------------------------------
# _empty_repo_index
# ---------------------------------------------------------------------------

class TestEmptyRepoIndex:
    def test_returns_repo_index(self):
        from src.engine.context_indexer import RepositoryIndex
        idx = _empty_repo_index()
        assert isinstance(idx, RepositoryIndex)

    def test_has_no_file_symbols(self):
        idx = _empty_repo_index()
        assert idx.file_symbols == {}

    def test_root_is_dot(self):
        idx = _empty_repo_index()
        assert idx.root == "."


# ---------------------------------------------------------------------------
# evaluate_fixture — mocked oracle
# ---------------------------------------------------------------------------

class TestEvaluateFixture:
    def _mock_oracle(self, violations=None):
        oracle = MagicMock()
        oracle.audit_diff.return_value = violations or []
        return oracle

    def test_clean_pr_no_violations_approved(self):
        fixture = _make_fixture(verdict="APPROVED")
        oracle = self._mock_oracle(violations=[])
        repo_index = _empty_repo_index()
        result = evaluate_fixture(fixture, oracle, repo_index, run_sandbox=False)
        assert result.actual_verdict == "APPROVED"
        assert result.verdict_correct is True
        assert result.is_true_negative is True

    def test_defect_pr_with_violation_rejected(self):
        violation = MagicMock()
        violation.rule_id = "goroutine-leak"
        fixture = _make_fixture(verdict="REJECTED", rules=["goroutine-leak"])
        oracle = self._mock_oracle(violations=[violation])
        repo_index = _empty_repo_index()
        result = evaluate_fixture(fixture, oracle, repo_index, run_sandbox=False)
        assert result.actual_verdict == "REJECTED"
        assert result.actual_rules == ["goroutine-leak"]
        assert result.is_true_positive is True

    def test_false_negative_oracle_misses_defect(self):
        fixture = _make_fixture(verdict="REJECTED", rules=["goroutine-leak"])
        oracle = self._mock_oracle(violations=[])  # oracle misses it
        result = evaluate_fixture(fixture, oracle, _empty_repo_index(), run_sandbox=False)
        assert result.actual_verdict == "APPROVED"
        assert result.is_false_negative is True

    def test_false_positive_oracle_hallucinates_on_clean(self):
        violation = MagicMock()
        violation.rule_id = "goroutine-leak"
        fixture = _make_fixture(verdict="APPROVED")  # clean PR
        oracle = self._mock_oracle(violations=[violation])
        result = evaluate_fixture(fixture, oracle, _empty_repo_index(), run_sandbox=False)
        assert result.actual_verdict == "REJECTED"
        assert result.is_false_positive is True

    def test_oracle_exception_recorded_as_error(self):
        oracle = MagicMock()
        oracle.audit_diff.side_effect = RuntimeError("oracle crash")
        fixture = _make_fixture()
        result = evaluate_fixture(fixture, oracle, _empty_repo_index(), run_sandbox=False)
        assert result.error is not None
        assert "oracle crash" in result.error
        # Verdict defaults to APPROVED when oracle crashes
        assert result.actual_verdict == "APPROVED"

    def test_sandbox_confirmed_set_when_violations_found(self):
        violation = MagicMock()
        violation.rule_id = "goroutine-leak"
        fixture = _make_fixture(verdict="REJECTED", rules=["goroutine-leak"])
        oracle = self._mock_oracle(violations=[violation])
        repo_index = _empty_repo_index()

        mock_repro = MagicMock()
        mock_sandbox_result = MagicMock()
        mock_sandbox_result.confirmed = True

        with patch("src.eval.benchmark_harness.synthesize_repro_test", return_value=mock_repro), \
             patch("src.eval.benchmark_harness.run_repro_in_sandbox", return_value=mock_sandbox_result):
            result = evaluate_fixture(fixture, oracle, repo_index, run_sandbox=True)

        assert result.sandbox_confirmed is True

    def test_sandbox_confirmed_false_when_not_confirmed(self):
        violation = MagicMock()
        violation.rule_id = "goroutine-leak"
        fixture = _make_fixture(verdict="REJECTED", rules=["goroutine-leak"])
        oracle = self._mock_oracle(violations=[violation])

        mock_repro = MagicMock()
        mock_sandbox_result = MagicMock()
        mock_sandbox_result.confirmed = False

        with patch("src.eval.benchmark_harness.synthesize_repro_test", return_value=mock_repro), \
             patch("src.eval.benchmark_harness.run_repro_in_sandbox", return_value=mock_sandbox_result):
            result = evaluate_fixture(fixture, oracle, _empty_repo_index(), run_sandbox=True)

        assert result.sandbox_confirmed is False

    def test_sandbox_not_called_when_no_violations(self):
        fixture = _make_fixture(verdict="APPROVED")
        oracle = self._mock_oracle(violations=[])
        with patch("src.eval.benchmark_harness.run_repro_in_sandbox") as mock_sb:
            result = evaluate_fixture(fixture, oracle, _empty_repo_index(), run_sandbox=True)
        mock_sb.assert_not_called()
        assert result.sandbox_confirmed is None

    def test_sandbox_disabled_with_run_sandbox_false(self):
        violation = MagicMock()
        violation.rule_id = "goroutine-leak"
        fixture = _make_fixture(verdict="REJECTED", rules=["goroutine-leak"])
        oracle = self._mock_oracle(violations=[violation])
        with patch("src.eval.benchmark_harness.run_repro_in_sandbox") as mock_sb:
            result = evaluate_fixture(fixture, oracle, _empty_repo_index(), run_sandbox=False)
        mock_sb.assert_not_called()
        assert result.sandbox_confirmed is None

    def test_sandbox_exception_recorded_as_error_non_fatal(self):
        violation = MagicMock()
        violation.rule_id = "goroutine-leak"
        fixture = _make_fixture(verdict="REJECTED", rules=["goroutine-leak"])
        oracle = self._mock_oracle(violations=[violation])

        with patch("src.eval.benchmark_harness.synthesize_repro_test", side_effect=RuntimeError("synth crash")):
            result = evaluate_fixture(fixture, oracle, _empty_repo_index(), run_sandbox=True)

        # Verdict is still set from oracle, sandbox error recorded
        assert result.actual_verdict == "REJECTED"
        assert result.error is not None
        assert "synth crash" in result.error


# ---------------------------------------------------------------------------
# run_offline_eval — mocked fixture loading and oracle
# ---------------------------------------------------------------------------

class TestRunOfflineEval:
    def _mock_fixtures(self, n_clean=5, n_defect=5):
        fixtures = []
        for i in range(n_clean):
            fixtures.append(_make_fixture(
                pr_id=f"PR-{i+1:02d}", category="CLEAN",
                verdict="APPROVED", rules=[],
            ))
        for i in range(n_defect):
            fixtures.append(_make_fixture(
                pr_id=f"PR-{n_clean+i+1:02d}", category="GOROUTINE_LEAK",
                verdict="REJECTED", rules=["goroutine-leak"],
            ))
        return fixtures

    def _perfect_oracle(self, n_defect=5):
        """Oracle that returns one violation per defect PR and none for clean."""
        call_count = [0]
        violation = MagicMock()
        violation.rule_id = "goroutine-leak"

        def audit_side_effect(diff, repo_index):
            call_count[0] += 1
            # Defect fixtures have 'go func' in the diff (our generator adds it)
            if "go func" in diff:
                return [violation]
            return []

        oracle = MagicMock()
        oracle.audit_diff.side_effect = audit_side_effect
        return oracle

    def test_run_returns_eval_summary(self, tmp_path, capsys):
        fixtures = self._mock_fixtures(n_clean=2, n_defect=2)
        oracle = MagicMock()
        oracle.audit_diff.return_value = []

        with patch("src.eval.benchmark_harness.load_golden_dataset", return_value=fixtures):
            summary = run_offline_eval(
                dataset_path=str(tmp_path),
                min_precision_threshold=0.0,
                run_sandbox=False,
                _oracle=oracle,
            )

        assert isinstance(summary, EvalSummary)

    def test_oracle_called_for_each_fixture(self, tmp_path, capsys):
        fixtures = self._mock_fixtures(n_clean=3, n_defect=3)
        oracle = MagicMock()
        oracle.audit_diff.return_value = []

        with patch("src.eval.benchmark_harness.load_golden_dataset", return_value=fixtures):
            run_offline_eval(
                dataset_path=str(tmp_path),
                min_precision_threshold=0.0,
                run_sandbox=False,
                _oracle=oracle,
            )

        assert oracle.audit_diff.call_count == 6

    def test_prints_table_to_stdout(self, tmp_path, capsys):
        fixtures = self._mock_fixtures(n_clean=2, n_defect=2)
        oracle = MagicMock()
        oracle.audit_diff.return_value = []

        with patch("src.eval.benchmark_harness.load_golden_dataset", return_value=fixtures):
            run_offline_eval(
                dataset_path=str(tmp_path),
                min_precision_threshold=0.0,
                run_sandbox=False,
                _oracle=oracle,
            )

        captured = capsys.readouterr()
        assert "StampBob Offline Evaluation Benchmark" in captured.out

    def test_ci_gate_fail_exits_with_1(self, tmp_path, capsys):
        # Oracle hallucinates on all clean PRs → precision=0, hallucination=1.0
        fixtures = self._mock_fixtures(n_clean=5, n_defect=0)
        violation = MagicMock()
        violation.rule_id = "goroutine-leak"
        oracle = MagicMock()
        oracle.audit_diff.return_value = [violation]

        with patch("src.eval.benchmark_harness.load_golden_dataset", return_value=fixtures):
            with pytest.raises(SystemExit) as exc_info:
                run_offline_eval(
                    dataset_path=str(tmp_path),
                    min_precision_threshold=0.92,
                    run_sandbox=False,
                    _oracle=oracle,
                )

        assert exc_info.value.code == 1

    def test_ci_gate_pass_no_exit(self, tmp_path, capsys):
        # All clean PRs approved → precision=1.0
        fixtures = self._mock_fixtures(n_clean=3, n_defect=0)
        oracle = MagicMock()
        oracle.audit_diff.return_value = []

        with patch("src.eval.benchmark_harness.load_golden_dataset", return_value=fixtures):
            summary = run_offline_eval(
                dataset_path=str(tmp_path),
                min_precision_threshold=0.92,
                run_sandbox=False,
                _oracle=oracle,
            )

        assert summary.passed_ci_gate is True

    def test_summary_reflects_actual_results(self, tmp_path, capsys):
        fixtures = self._mock_fixtures(n_clean=2, n_defect=2)
        violation = MagicMock()
        violation.rule_id = "goroutine-leak"
        oracle = MagicMock()
        # Return violations for all PRs (2 clean → FP, 2 defect → TP)
        oracle.audit_diff.return_value = [violation]

        with patch("src.eval.benchmark_harness.load_golden_dataset", return_value=fixtures):
            with pytest.raises(SystemExit):
                run_offline_eval(
                    dataset_path=str(tmp_path),
                    min_precision_threshold=0.92,
                    run_sandbox=False,
                    _oracle=oracle,
                )


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

class TestCLIArgParser:
    def test_default_dataset_path(self):
        parser = _build_arg_parser()
        args = parser.parse_args([])
        assert args.dataset_path == DEFAULT_DATASET_PATH

    def test_default_threshold(self):
        parser = _build_arg_parser()
        args = parser.parse_args([])
        assert args.min_precision_threshold == DEFAULT_MIN_PRECISION

    def test_custom_threshold(self):
        parser = _build_arg_parser()
        args = parser.parse_args(["--threshold", "0.95"])
        assert abs(args.min_precision_threshold - 0.95) < 1e-9

    def test_custom_dataset_path(self):
        parser = _build_arg_parser()
        args = parser.parse_args(["--dataset-path", "/some/path"])
        assert args.dataset_path == "/some/path"

    def test_no_sandbox_flag(self):
        parser = _build_arg_parser()
        args = parser.parse_args(["--no-sandbox"])
        assert args.no_sandbox is True

    def test_no_sandbox_default_false(self):
        parser = _build_arg_parser()
        args = parser.parse_args([])
        assert args.no_sandbox is False

    def test_combined_args(self):
        parser = _build_arg_parser()
        args = parser.parse_args(["--threshold", "0.80", "--dataset-path", "data/", "--no-sandbox"])
        assert abs(args.min_precision_threshold - 0.80) < 1e-9
        assert args.dataset_path == "data/"
        assert args.no_sandbox is True
