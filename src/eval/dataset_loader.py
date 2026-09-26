"""
dataset_loader.py — StampBob Offline Evaluation Dataset Loader.

Loads the golden benchmark dataset used for CI evaluation gating.  The
dataset lives in ``fixtures/golden_prs/`` as a directory of JSON files, one
file per PR fixture.

Public API
----------
* :class:`GoldenPR`      — Validated dataclass representing one benchmark PR.
* :func:`load_golden_dataset` — Reads all fixtures from a directory and returns
  a validated list of :class:`GoldenPR` objects.

Schema contract (per fixture file)
-----------------------------------
Every JSON file MUST contain the following keys:

+-----------------------+------------------------+---------------------------------------+
| Field                 | Type                   | Constraints                           |
+=======================+========================+=======================================+
| pr_id                 | str                    | Non-empty, e.g. "PR-01"               |
| category              | str                    | One of VALID_CATEGORIES               |
| title                 | str                    | Non-empty                             |
| diff_content          | str                    | Non-empty                             |
| expected_verdict      | str                    | "APPROVED" or "REJECTED"              |
| expected_rules        | list[str]              | [] for APPROVED; ≥1 item for REJECTED |
| golden_repro_snippet  | str                    | Non-empty                             |
+-----------------------+------------------------+---------------------------------------+

Validation errors are accumulated per-file and raised together as a single
:class:`DatasetValidationError` at the end of loading so all problems are
visible in one pass.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_CATEGORIES = frozenset({
    "CLEAN",
    "GOROUTINE_LEAK",
    "UNBUFFERED_CHANNEL",
    "NIL_DEREF",
    "OTEL_OMISSION",
})

VALID_VERDICTS = frozenset({"APPROVED", "REJECTED"})

#: The fields every fixture JSON must provide.
REQUIRED_FIELDS = (
    "pr_id",
    "category",
    "title",
    "diff_content",
    "expected_verdict",
    "expected_rules",
    "golden_repro_snippet",
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DatasetValidationError(ValueError):
    """
    Raised when one or more fixture files fail schema validation.

    The exception message lists each invalid file together with the specific
    constraint(s) that were violated so all problems are visible at once.
    """


# ---------------------------------------------------------------------------
# GoldenPR dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GoldenPR:
    """
    A single golden benchmark PR fixture.

    Attributes
    ----------
    pr_id:
        Stable identifier for this PR, e.g. ``"PR-01"``.
    category:
        One of the five benchmark categories (``CLEAN``, ``GOROUTINE_LEAK``,
        ``UNBUFFERED_CHANNEL``, ``NIL_DEREF``, ``OTEL_OMISSION``).
    title:
        Human-readable PR title.
    diff_content:
        Unified diff text for the PR.
    expected_verdict:
        ``"APPROVED"`` (zero defects) or ``"REJECTED"`` (one or more rules
        must fire).
    expected_rules:
        Ordered list of rule IDs that the Invariant Oracle MUST report when
        ``expected_verdict == "REJECTED"``.  Empty list for ``"APPROVED"``
        fixtures.
    golden_repro_snippet:
        A minimal, standalone code snippet that directly demonstrates the
        defect described by ``expected_rules``.  Used to validate the repro
        synthesizer's output in round-trip tests.
    """

    pr_id: str
    category: str
    title: str
    diff_content: str
    expected_verdict: str
    expected_rules: List[str]
    golden_repro_snippet: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_fixture(data: dict, source_file: str) -> List[str]:
    """
    Validate a single parsed fixture dictionary.

    Returns a list of human-readable error strings (empty if valid).
    """
    errors: List[str] = []

    # 1. Required field presence
    for field in REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"missing required field '{field}'")

    if errors:
        # Cannot proceed with type/value checks when fields are absent.
        return errors

    # 2. Type checks
    str_fields = ("pr_id", "category", "title", "diff_content",
                  "expected_verdict", "golden_repro_snippet")
    for field in str_fields:
        if not isinstance(data[field], str):
            errors.append(
                f"field '{field}' must be a string, got {type(data[field]).__name__}"
            )

    if not isinstance(data["expected_rules"], list):
        errors.append(
            f"field 'expected_rules' must be a list, got {type(data['expected_rules']).__name__}"
        )
    else:
        for i, rule in enumerate(data["expected_rules"]):
            if not isinstance(rule, str):
                errors.append(
                    f"expected_rules[{i}] must be a string, got {type(rule).__name__}"
                )

    if errors:
        return errors

    # 3. Non-empty string checks
    for field in str_fields:
        if not data[field].strip():
            errors.append(f"field '{field}' must not be empty or whitespace-only")

    # 4. Category enum check
    if data["category"] not in VALID_CATEGORIES:
        errors.append(
            f"field 'category' has invalid value {data['category']!r}; "
            f"must be one of {sorted(VALID_CATEGORIES)}"
        )

    # 5. Verdict enum check
    if data["expected_verdict"] not in VALID_VERDICTS:
        errors.append(
            f"field 'expected_verdict' has invalid value {data['expected_verdict']!r}; "
            f"must be 'APPROVED' or 'REJECTED'"
        )

    # 6. Cross-field consistency: REJECTED → expected_rules non-empty
    if data["expected_verdict"] == "REJECTED" and not data["expected_rules"]:
        errors.append(
            "expected_verdict is 'REJECTED' but expected_rules is empty; "
            "at least one rule ID is required"
        )

    # 7. Cross-field consistency: APPROVED → expected_rules must be empty
    if data["expected_verdict"] == "APPROVED" and data["expected_rules"]:
        errors.append(
            f"expected_verdict is 'APPROVED' but expected_rules is non-empty: "
            f"{data['expected_rules']!r}"
        )

    return errors


def _build_golden_pr(data: dict) -> GoldenPR:
    """Construct a :class:`GoldenPR` from a validated dictionary."""
    return GoldenPR(
        pr_id=data["pr_id"],
        category=data["category"],
        title=data["title"],
        diff_content=data["diff_content"],
        expected_verdict=data["expected_verdict"],
        expected_rules=list(data["expected_rules"]),
        golden_repro_snippet=data["golden_repro_snippet"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_golden_dataset(fixtures_dir: str) -> List[GoldenPR]:
    """
    Load and validate all golden PR fixtures from *fixtures_dir*.

    The function reads every ``*.json`` file in *fixtures_dir* (non-recursive),
    validates each against the :class:`GoldenPR` schema, accumulates all
    validation errors, and either:

    * returns a list of :class:`GoldenPR` objects sorted by ``pr_id``
      (lexicographic), or
    * raises :class:`DatasetValidationError` listing every invalid file and
      the specific constraint(s) violated.

    Parameters
    ----------
    fixtures_dir:
        Path to the directory that contains the JSON fixture files.
        Supports both absolute and relative paths.

    Returns
    -------
    List[GoldenPR]
        Validated fixtures sorted by ``pr_id``.

    Raises
    ------
    FileNotFoundError
        If *fixtures_dir* does not exist.
    DatasetValidationError
        If any fixture file fails schema validation.  The message enumerates
        every invalid file and its constraint violations.
    """
    dir_path = Path(fixtures_dir)
    if not dir_path.exists():
        raise FileNotFoundError(
            f"Fixtures directory not found: {dir_path.resolve()}"
        )
    if not dir_path.is_dir():
        raise NotADirectoryError(
            f"Expected a directory, got a file: {dir_path.resolve()}"
        )

    json_files = sorted(dir_path.glob("*.json"))
    if not json_files:
        raise DatasetValidationError(
            f"No JSON fixture files found in {dir_path.resolve()}"
        )

    all_errors: List[str] = []
    golden_prs: List[GoldenPR] = []

    for json_path in json_files:
        rel = os.path.relpath(json_path)

        # Parse JSON
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            all_errors.append(f"{rel}: JSON parse error — {exc}")
            continue

        if not isinstance(raw, dict):
            all_errors.append(f"{rel}: top-level value must be a JSON object, got {type(raw).__name__}")
            continue

        # Validate fields
        field_errors = _validate_fixture(raw, rel)
        if field_errors:
            formatted = "; ".join(field_errors)
            all_errors.append(f"{rel}: {formatted}")
            continue

        golden_prs.append(_build_golden_pr(raw))

    if all_errors:
        bullet_list = "\n".join(f"  • {e}" for e in all_errors)
        raise DatasetValidationError(
            f"Dataset validation failed ({len(all_errors)} error(s)):\n{bullet_list}"
        )

    # Sort by pr_id for deterministic ordering
    golden_prs.sort(key=lambda pr: pr.pr_id)
    return golden_prs
