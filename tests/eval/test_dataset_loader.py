"""
tests/eval/test_dataset_loader.py — Unit tests for src/eval/dataset_loader.py.

Covers:
  - Happy-path: loading all 50 golden fixtures from the real fixtures directory.
  - Schema validation: missing fields, wrong types, invalid enum values,
    cross-field consistency rules.
  - Edge cases: non-existent directory, empty directory, non-JSON-object root.
"""

from __future__ import annotations

import json
import os
import pytest

from pathlib import Path

from src.eval.dataset_loader import (
    VALID_CATEGORIES,
    VALID_VERDICTS,
    DatasetValidationError,
    GoldenPR,
    load_golden_dataset,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "golden_prs"

VALID_FIXTURE = {
    "pr_id": "PR-01",
    "category": "CLEAN",
    "title": "Refactor logging",
    "diff_content": "diff --git a/foo.go b/foo.go\n--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
    "expected_verdict": "APPROVED",
    "expected_rules": [],
    "golden_repro_snippet": "// No defect — APPROVED",
}

VALID_REJECTED_FIXTURE = {
    "pr_id": "PR-11",
    "category": "GOROUTINE_LEAK",
    "title": "Goroutine leak",
    "diff_content": "diff --git a/leak.go b/leak.go\n+go func() { for {} }()",
    "expected_verdict": "REJECTED",
    "expected_rules": ["goroutine-leak"],
    "golden_repro_snippet": "func TestReproLeak(t *testing.T) {}",
}


def _write_fixture(tmp_path: Path, filename: str, content: dict) -> Path:
    p = tmp_path / filename
    p.write_text(json.dumps(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Happy-path tests against the real fixture directory
# ---------------------------------------------------------------------------

class TestLoadGoldenDatasetHappyPath:
    def test_loads_50_fixtures(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        assert len(prs) == 50

    def test_returns_list_of_golden_pr(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        for pr in prs:
            assert isinstance(pr, GoldenPR)

    def test_sorted_by_pr_id(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        ids = [pr.pr_id for pr in prs]
        assert ids == sorted(ids)

    def test_category_distribution(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        counts: dict[str, int] = {}
        for pr in prs:
            counts[pr.category] = counts.get(pr.category, 0) + 1
        for cat in VALID_CATEGORIES:
            assert counts.get(cat, 0) == 10, (
                f"Expected 10 fixtures for category {cat}, got {counts.get(cat, 0)}"
            )

    def test_all_categories_valid(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        for pr in prs:
            assert pr.category in VALID_CATEGORIES

    def test_all_verdicts_valid(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        for pr in prs:
            assert pr.expected_verdict in VALID_VERDICTS

    def test_clean_prs_are_approved_with_empty_rules(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        clean = [pr for pr in prs if pr.category == "CLEAN"]
        assert len(clean) == 10
        for pr in clean:
            assert pr.expected_verdict == "APPROVED"
            assert pr.expected_rules == []

    def test_defect_prs_are_rejected_with_rules(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        defect = [pr for pr in prs if pr.category != "CLEAN"]
        assert len(defect) == 40
        for pr in defect:
            assert pr.expected_verdict == "REJECTED"
            assert len(pr.expected_rules) >= 1

    def test_goroutine_leak_prs_have_correct_rule(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        leak_prs = [pr for pr in prs if pr.category == "GOROUTINE_LEAK"]
        for pr in leak_prs:
            assert "goroutine-leak" in pr.expected_rules

    def test_unbuffered_channel_prs_have_correct_rule(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        uc_prs = [pr for pr in prs if pr.category == "UNBUFFERED_CHANNEL"]
        for pr in uc_prs:
            assert "unbuffered-channel-in-loop" in pr.expected_rules

    def test_nil_deref_prs_have_correct_rule(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        nil_prs = [pr for pr in prs if pr.category == "NIL_DEREF"]
        for pr in nil_prs:
            assert "nil-check-boundary" in pr.expected_rules

    def test_otel_omission_prs_have_correct_rule(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        otel_prs = [pr for pr in prs if pr.category == "OTEL_OMISSION"]
        for pr in otel_prs:
            assert "otel-semantic-convention" in pr.expected_rules

    def test_all_fields_non_empty(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        for pr in prs:
            assert pr.pr_id.strip()
            assert pr.title.strip()
            assert pr.diff_content.strip()
            assert pr.golden_repro_snippet.strip()

    def test_pr_ids_are_unique(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        ids = [pr.pr_id for pr in prs]
        assert len(ids) == len(set(ids)), "Duplicate pr_id values detected"

    def test_golden_pr_is_frozen(self):
        prs = load_golden_dataset(str(FIXTURES_DIR))
        pr = prs[0]
        with pytest.raises((AttributeError, TypeError)):
            pr.pr_id = "MUTATED"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Edge case — directory errors
# ---------------------------------------------------------------------------

class TestDirectoryErrors:
    def test_nonexistent_directory_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_golden_dataset(str(tmp_path / "does_not_exist"))

    def test_empty_directory_raises_validation_error(self, tmp_path):
        with pytest.raises(DatasetValidationError, match="No JSON fixture files found"):
            load_golden_dataset(str(tmp_path))

    def test_path_is_a_file_raises_not_a_directory(self, tmp_path):
        f = tmp_path / "file.json"
        f.write_text("{}")
        with pytest.raises(NotADirectoryError):
            load_golden_dataset(str(f))


# ---------------------------------------------------------------------------
# Schema validation — missing required fields
# ---------------------------------------------------------------------------

class TestMissingFields:
    @pytest.mark.parametrize("missing_field", [
        "pr_id", "category", "title", "diff_content",
        "expected_verdict", "expected_rules", "golden_repro_snippet",
    ])
    def test_missing_field_raises_validation_error(self, tmp_path, missing_field):
        data = {k: v for k, v in VALID_FIXTURE.items() if k != missing_field}
        _write_fixture(tmp_path, "pr_01.json", data)
        with pytest.raises(DatasetValidationError, match=f"missing required field '{missing_field}'"):
            load_golden_dataset(str(tmp_path))


# ---------------------------------------------------------------------------
# Schema validation — type errors
# ---------------------------------------------------------------------------

class TestTypeErrors:
    @pytest.mark.parametrize("field,bad_value", [
        ("pr_id", 42),
        ("category", ["CLEAN"]),
        ("title", None),
        ("diff_content", 3.14),
        ("expected_verdict", True),
        ("golden_repro_snippet", {}),
        ("expected_rules", "goroutine-leak"),
    ])
    def test_wrong_type_raises_validation_error(self, tmp_path, field, bad_value):
        data = dict(VALID_FIXTURE)
        data[field] = bad_value
        # For expected_rules typed as list, fix the fixture to REJECTED first so
        # we can isolate the type error; string triggers its own type message.
        if field == "expected_rules":
            data["expected_verdict"] = "REJECTED"
        _write_fixture(tmp_path, "pr_01.json", data)
        with pytest.raises(DatasetValidationError):
            load_golden_dataset(str(tmp_path))


# ---------------------------------------------------------------------------
# Schema validation — empty string fields
# ---------------------------------------------------------------------------

class TestEmptyFields:
    @pytest.mark.parametrize("field", [
        "pr_id", "title", "diff_content", "golden_repro_snippet",
    ])
    def test_empty_string_raises_validation_error(self, tmp_path, field):
        data = dict(VALID_FIXTURE)
        data[field] = "   "
        _write_fixture(tmp_path, "pr_01.json", data)
        with pytest.raises(DatasetValidationError, match=f"must not be empty"):
            load_golden_dataset(str(tmp_path))


# ---------------------------------------------------------------------------
# Schema validation — invalid enum values
# ---------------------------------------------------------------------------

class TestInvalidEnums:
    def test_invalid_category_raises_validation_error(self, tmp_path):
        data = dict(VALID_FIXTURE)
        data["category"] = "DEADLOCK"
        _write_fixture(tmp_path, "pr_01.json", data)
        with pytest.raises(DatasetValidationError, match="invalid value 'DEADLOCK'"):
            load_golden_dataset(str(tmp_path))

    def test_invalid_verdict_raises_validation_error(self, tmp_path):
        data = dict(VALID_FIXTURE)
        data["expected_verdict"] = "PENDING"
        _write_fixture(tmp_path, "pr_01.json", data)
        with pytest.raises(DatasetValidationError, match="invalid value 'PENDING'"):
            load_golden_dataset(str(tmp_path))


# ---------------------------------------------------------------------------
# Schema validation — cross-field consistency
# ---------------------------------------------------------------------------

class TestCrossFieldConsistency:
    def test_rejected_with_empty_rules_raises_validation_error(self, tmp_path):
        data = dict(VALID_REJECTED_FIXTURE)
        data["expected_rules"] = []
        _write_fixture(tmp_path, "pr_11.json", data)
        with pytest.raises(DatasetValidationError, match="REJECTED.*expected_rules is empty"):
            load_golden_dataset(str(tmp_path))

    def test_approved_with_non_empty_rules_raises_validation_error(self, tmp_path):
        data = dict(VALID_FIXTURE)
        data["expected_rules"] = ["goroutine-leak"]
        _write_fixture(tmp_path, "pr_01.json", data)
        with pytest.raises(DatasetValidationError, match="APPROVED.*non-empty"):
            load_golden_dataset(str(tmp_path))


# ---------------------------------------------------------------------------
# Schema validation — malformed JSON
# ---------------------------------------------------------------------------

class TestMalformedJSON:
    def test_invalid_json_raises_validation_error(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json}", encoding="utf-8")
        with pytest.raises(DatasetValidationError, match="JSON parse error"):
            load_golden_dataset(str(tmp_path))

    def test_json_array_root_raises_validation_error(self, tmp_path):
        p = tmp_path / "arr.json"
        p.write_text('[{"pr_id": "PR-01"}]', encoding="utf-8")
        with pytest.raises(DatasetValidationError, match="top-level value must be a JSON object"):
            load_golden_dataset(str(tmp_path))


# ---------------------------------------------------------------------------
# Error accumulation — multiple bad files reported together
# ---------------------------------------------------------------------------

class TestErrorAccumulation:
    def test_multiple_bad_files_all_reported(self, tmp_path):
        # Two invalid files — both errors should appear in the exception message.
        bad1 = dict(VALID_FIXTURE)
        del bad1["pr_id"]
        bad2 = dict(VALID_FIXTURE)
        bad2["category"] = "INVALID_CAT"
        _write_fixture(tmp_path, "bad_a.json", bad1)
        _write_fixture(tmp_path, "bad_b.json", bad2)

        with pytest.raises(DatasetValidationError) as exc_info:
            load_golden_dataset(str(tmp_path))

        msg = str(exc_info.value)
        assert "bad_a.json" in msg
        assert "bad_b.json" in msg
        assert "2 error(s)" in msg

    def test_valid_files_coexist_with_errors_still_fails(self, tmp_path):
        _write_fixture(tmp_path, "pr_01.json", VALID_FIXTURE)
        bad = dict(VALID_REJECTED_FIXTURE)
        bad["expected_rules"] = []
        _write_fixture(tmp_path, "pr_11.json", bad)

        with pytest.raises(DatasetValidationError):
            load_golden_dataset(str(tmp_path))


# ---------------------------------------------------------------------------
# Minimal valid dataset (no dependency on real fixtures directory)
# ---------------------------------------------------------------------------

class TestMinimalDataset:
    def test_single_approved_fixture(self, tmp_path):
        _write_fixture(tmp_path, "pr_01.json", VALID_FIXTURE)
        prs = load_golden_dataset(str(tmp_path))
        assert len(prs) == 1
        pr = prs[0]
        assert pr.pr_id == "PR-01"
        assert pr.category == "CLEAN"
        assert pr.expected_verdict == "APPROVED"
        assert pr.expected_rules == []

    def test_single_rejected_fixture(self, tmp_path):
        _write_fixture(tmp_path, "pr_11.json", VALID_REJECTED_FIXTURE)
        prs = load_golden_dataset(str(tmp_path))
        assert len(prs) == 1
        pr = prs[0]
        assert pr.pr_id == "PR-11"
        assert pr.expected_verdict == "REJECTED"
        assert "goroutine-leak" in pr.expected_rules

    def test_expected_rules_returns_a_copy(self, tmp_path):
        _write_fixture(tmp_path, "pr_11.json", VALID_REJECTED_FIXTURE)
        prs = load_golden_dataset(str(tmp_path))
        rules = prs[0].expected_rules
        # GoldenPR is frozen — the list itself should not be mutably shared
        assert isinstance(rules, list)
