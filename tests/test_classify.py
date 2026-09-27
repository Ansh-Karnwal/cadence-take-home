from __future__ import annotations

import re

import pytest

from preop_triage.classify import (
    DocKind,
    OfflineResolver,
    TextJudgment,
    TitleClassification,
    TitleOutcome,
    classify_title,
    judge_consent_signed,
    judge_plan_clear,
)
from preop_triage.normalize import normalize_title

# Expected outcomes from DESIGN.md D3, as a small map rather than 107 strings.
# The eight clearance/evaluation titles plus the case_00002 misspelling.
EXPECTED_UNKNOWN_TITLES = {
    "Medical Clearance [PDF]",
    "Preop Medical Clearance [PDF]",
    "Pre-op Evaluation [PDF]",
    "Pre-op Pre-op Evaluation [PDF]",
    "Imported: Preoperative Assessment [PDF]",
    "Pre-op Surgical Clearance Note [PDF]",
    "Preop Pre-anesthesia Evaluation [PDF]",
    "Scanned Pre-anesthesia Evaluation [PDF]",
    "History & Phsyical",
}
EXPECTED_KNOWN_OTHER_CORES = {"anesthesia pre-assessment", "clinic follow-up note", "nursing intake"}
EXPECTED_PLAN_CORES = {
    "perioperative medication plan",
    "perioperative medication review",
    "anticoag plan",
    "cardiology progress note - anticoag",
}


def _expected(title: str) -> TitleClassification:
    core = normalize_title(title)
    if title in EXPECTED_UNKNOWN_TITLES:
        return TitleClassification(TitleOutcome.UNKNOWN)
    if core in EXPECTED_KNOWN_OTHER_CORES:
        return TitleClassification(TitleOutcome.KNOWN_OTHER)
    if core in EXPECTED_PLAN_CORES:
        return TitleClassification(TitleOutcome.REQUIRED, DocKind.ANTICOAG_PLAN)
    if re.search(r"\bconsent\b", core):
        return TitleClassification(TitleOutcome.REQUIRED, DocKind.CONSENT)
    return TitleClassification(TitleOutcome.REQUIRED, DocKind.H_AND_P)


@pytest.fixture(scope="module")
def dataset_titles(dataset: list[dict]) -> set[str]:
    return {doc["type"] for row in dataset for doc in row["submission"]["documents"]}


def test_every_dataset_title_gets_its_design_outcome(dataset_titles: set[str]) -> None:
    mismatches = {
        title: (classify_title(title), _expected(title))
        for title in dataset_titles
        if classify_title(title) != _expected(title)
    }
    assert mismatches == {}


def test_unknown_titles_are_exactly_the_design_list(dataset_titles: set[str]) -> None:
    unknown = {
        title
        for title in dataset_titles
        if classify_title(title).outcome is TitleOutcome.UNKNOWN
    }
    assert unknown == EXPECTED_UNKNOWN_TITLES


def test_misspelled_hp_title_falls_through() -> None:
    # case_00002: no fuzzy matching, by construction.
    assert classify_title("History & Phsyical") == TitleClassification(TitleOutcome.UNKNOWN)


@pytest.mark.parametrize("title", [None, "", "   ", "[PDF]"])
def test_empty_title_is_unknown(title: str | None) -> None:
    assert classify_title(title).outcome is TitleOutcome.UNKNOWN


# --- D4 substring traps ---------------------------------------------------


def test_trap_unsigned_contains_signed() -> None:
    # case_00044
    text = "Unsigned consent noted; signature not yet on file."
    assert judge_consent_signed(text) is TextJudgment.NEGATIVE


def test_trap_plan_to_be_finalized_contains_plan() -> None:
    # case_00038
    text = "Apixaban listed; perioperative management plan to be finalized."
    assert judge_plan_clear(text) is TextJudgment.NEGATIVE


def test_trap_no_clear_beats_hold_and_resume() -> None:
    # case_00004
    text = "Anticoagulation mentioned; no clear hold/resume guidance documented."
    assert judge_plan_clear(text) is TextJudgment.NEGATIVE


def test_case_00000_plan_text_is_unplaced() -> None:
    text = (
        "Patient takes apixaban for atrial fibrillation. "
        "Follow up with cardiology for peri-op recommendations."
    )
    assert judge_plan_clear(text) is TextJudgment.UNPLACED


# --- Consent judgments ----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Consent obtained and signed; documentation completed.",
        "Consent obtained; signature on file.",
        "Electronic consent obtained and signed by patient for procedure.",
        "Patient reviewed risks/benefits and signed surgical consent.",
        "Signed consent scanned and verified before scheduling.",
    ],
)
def test_signed_consent_texts_are_positive(text: str) -> None:
    assert judge_consent_signed(text) is TextJudgment.POSITIVE


@pytest.mark.parametrize(
    "text",
    [
        "Consent documented but unsigned; awaiting patient signature.",  # case_00008
        "Unsigned consent on chart; provider requested signature before scheduling.",  # case_00017
        "Unsigned consent noted; signature not yet on file.",  # case_00044
    ],
)
def test_unsigned_consent_texts_are_negative(text: str) -> None:
    assert judge_consent_signed(text) is TextJudgment.NEGATIVE


def test_signature_alone_is_not_positive() -> None:
    assert judge_consent_signed("Consent form awaiting review; signature line present.") is (
        TextJudgment.NEGATIVE
    )
    assert judge_consent_signed("Consent reviewed with patient; signature line present.") is (
        TextJudgment.UNPLACED
    )


@pytest.mark.parametrize("text", [None, "", "   "])
def test_empty_text_is_negative_not_unplaced(text: str | None) -> None:
    assert judge_consent_signed(text) is TextJudgment.NEGATIVE
    assert judge_plan_clear(text) is TextJudgment.NEGATIVE


# --- Plan judgments -------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Anticoagulant noted in medication list; perioperative management details not yet documented.",
        "Discussed blood thinner use with patient; final plan pending specialist input.",
    ],
)
def test_vague_plan_texts_are_negative(text: str) -> None:
    assert judge_plan_clear(text) is TextJudgment.NEGATIVE


def test_hold_and_resume_is_positive() -> None:
    text = "Hold apixaban 48 hours before surgery; resume 24 hours after if hemostasis secured."
    assert judge_plan_clear(text) is TextJudgment.POSITIVE


@pytest.mark.parametrize(
    "text",
    [
        "Hold apixaban 48 hours before surgery.",  # hold only
        "Resume apixaban the day after surgery.",  # resume only
        "Perioperative anticoagulation plan documented.",  # "plan" alone
    ],
)
def test_partial_plan_is_unplaced(text: str) -> None:
    assert judge_plan_clear(text) is TextJudgment.UNPLACED


def test_word_boundaries_apply() -> None:
    # "household" and "threshold" contain "hold"; "presumed" contains "resume" letters.
    assert judge_plan_clear("Household threshold presumed stable.") is TextJudgment.UNPLACED


def test_offline_resolver_defaults() -> None:
    resolver = OfflineResolver()
    assert resolver.resolve_title("Medical Clearance [PDF]") is DocKind.OTHER
    assert resolver.resolve_consent_signed("anything") is False
    assert resolver.resolve_plan_clear("anything") is False
