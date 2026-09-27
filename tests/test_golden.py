"""Golden test: all 50 sample cases in offline mode against the oracle.

Per case: decision equal; sorted unique category set equal; and for every
expected issue, an emitted issue with the same category, source, and
description. Details are not compared character for character; the grounding
metric checks them (see the separate grounding test below).
"""

from __future__ import annotations

import pytest

from core import PreparedPatientCase, TriageOutput, triage_submission
from preop_triage.settings import Settings
from run_evals import _local_metrics_for_row

OFFLINE = Settings(offline=True)

# The only permitted divergence. Key: (case_id, category, oracle source).
# Value: (our source, reason).
SOURCE_EXCEPTIONS: dict[tuple[str, str, str], tuple[str, str]] = {
    ("case_00042", "ANTICOAGULATION_MANAGEMENT", "documents"): (
        "documents[4]",
        "DESIGN.md section 5 item 2: the document titled 'Perioperative Medication "
        "Plan' at index 4 names neither the drug nor 'anticoag', so the oracle cites "
        "'documents'. It is the only plan candidate, so we cite 'documents[4]'. "
        "Source is not scored; category, decision, and grounding are unaffected.",
    ),
}


def _run(row: dict) -> TriageOutput:
    return triage_submission(submission=row["submission"], model="unused", settings=OFFLINE)


def _ids(dataset: list[dict]) -> list[str]:
    return [row["case_id"] for row in dataset]


@pytest.fixture(scope="module")
def outputs(dataset: list[dict]) -> dict[str, TriageOutput]:
    return {row["case_id"]: _run(row) for row in dataset}


def test_dataset_has_fifty_cases(dataset: list[dict]) -> None:
    assert len(dataset) == 50


def test_decision_matches_oracle(dataset: list[dict], outputs: dict[str, TriageOutput]) -> None:
    mismatches = {
        row["case_id"]: (outputs[row["case_id"]].decision, row["expected_output"]["decision"])
        for row in dataset
        if outputs[row["case_id"]].decision != row["expected_output"]["decision"]
    }
    assert mismatches == {}


def test_category_set_matches_oracle(
    dataset: list[dict], outputs: dict[str, TriageOutput]
) -> None:
    mismatches = {}
    for row in dataset:
        got = sorted({issue.category for issue in outputs[row["case_id"]].issues})
        want = sorted({issue["category"] for issue in row["expected_output"]["issues"]})
        if got != want:
            mismatches[row["case_id"]] = (got, want)
    assert mismatches == {}


def test_every_expected_issue_is_emitted(
    dataset: list[dict], outputs: dict[str, TriageOutput]
) -> None:
    missing = {}
    used_exceptions = set()
    for row in dataset:
        case_id = row["case_id"]
        emitted = {
            (issue.category, issue.evidence.source, issue.description)
            for issue in outputs[case_id].issues
        }
        for expected in row["expected_output"]["issues"]:
            category = expected["category"]
            source = expected["evidence"]["source"]
            key = (case_id, category, source)
            if key in SOURCE_EXCEPTIONS:
                source = SOURCE_EXCEPTIONS[key][0]
                used_exceptions.add(key)
            if (category, source, expected["description"]) not in emitted:
                missing.setdefault(case_id, []).append(
                    (category, source, expected["description"])
                )
    assert missing == {}
    # Every exception must still be needed; a stale one would hide a change.
    assert used_exceptions == set(SOURCE_EXCEPTIONS)


def test_all_harness_metrics_pass(dataset: list[dict], outputs: dict[str, TriageOutput]) -> None:
    """The harness's own per-case scorer, including grounding, passes on every case."""

    failures = {}
    for row in dataset:
        case = PreparedPatientCase.model_validate(row)
        local = _local_metrics_for_row(
            submission=case.submission.model_dump(),
            oracle_output=case.expected_output,
            model_output_payload=outputs[row["case_id"]].model_dump(),
        )
        failed = [name for name, passed in local["metrics"].items() if not passed]
        if failed:
            failures[row["case_id"]] = failed
    assert failures == {}
