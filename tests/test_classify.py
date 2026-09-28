from __future__ import annotations

import re

import pytest

from preop_triage.classify import (
    DocKind,
    anticoagulant_generic,
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
    assert judge_plan_clear(text, "apixaban") is TextJudgment.NEGATIVE


def test_trap_no_clear_beats_hold_and_resume() -> None:
    # case_00004
    text = "Anticoagulation mentioned; no clear hold/resume guidance documented."
    assert judge_plan_clear(text, "apixaban") is TextJudgment.NEGATIVE


def test_case_00000_plan_text_is_unplaced() -> None:
    text = (
        "Patient takes apixaban for atrial fibrillation. "
        "Follow up with cardiology for peri-op recommendations."
    )
    assert judge_plan_clear(text, "apixaban") is TextJudgment.UNPLACED


# --- Verifier probe strings, verbatim -------------------------------------------
# Every one of these was cleared or accepted by the milestone 2 keyword rules.
# None may be rule-POSITIVE. UNPLACED plans reach the resolver; offline they
# resolve not-clear.

VAGUE_PLAN_PROBES = {
    "Home meds reviewed incl. apixaban. Hold metformin morning of surgery; resume diet after.": (
        TextJudgment.UNPLACED
    ),
    "Cardiology to advise whether to hold or resume apixaban.": TextJudgment.NEGATIVE,
    "Unclear whether to hold apixaban; will resume later.": TextJudgment.NEGATIVE,
    "Patient on apixaban. Hold and resume per cardiology.": TextJudgment.UNPLACED,
}

NEGATED_CONSENT_PROBES = (
    "Consent not signed.",
    "Patient has not signed consent.",
    "Consent to be signed on day of surgery.",
    "Consent form signed by witness; patient signature missing.",
)


@pytest.mark.parametrize(("text", "expected"), list(VAGUE_PLAN_PROBES.items()))
def test_vague_plan_probes_are_never_positive(text: str, expected: TextJudgment) -> None:
    assert judge_plan_clear(text, "apixaban") is expected


@pytest.mark.parametrize("text", NEGATED_CONSENT_PROBES)
def test_negated_consent_probes_are_negative(text: str) -> None:
    assert judge_consent_signed(text) is TextJudgment.NEGATIVE


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A negation in another clause does not cancel a signature.
        ("Consent signed by patient; no complications noted.", TextJudgment.POSITIVE),
        # A sign-word and a negation in one clause is negative.
        ("Patient declined to sign consent.", TextJudgment.NEGATIVE),
        ("Consent signed; witness signature missing.", TextJudgment.NEGATIVE),
        ("Signature pending: consent reviewed.", TextJudgment.NEGATIVE),
        ("Consent withdrawn after signature.", TextJudgment.NEGATIVE),
        # "noted" does not match "no"; "unsigned" does not match "signed".
        ("Consent noted and signed.", TextJudgment.POSITIVE),
        # A negation with no sign-word in its clause is neutral.
        ("Consent reviewed; no questions.", TextJudgment.UNPLACED),
    ],
)
def test_consent_is_judged_per_clause(text: str, expected: TextJudgment) -> None:
    assert judge_consent_signed(text) is expected


def test_signature_alone_is_not_positive() -> None:
    # "awaiting" and "signature" are in different clauses, so the clause rule
    # does not make the first text NEGATIVE. Neither text is POSITIVE.
    assert judge_consent_signed("Consent form awaiting review; signature line present.") is (
        TextJudgment.UNPLACED
    )
    assert judge_consent_signed("Consent reviewed with patient; signature line present.") is (
        TextJudgment.UNPLACED
    )


@pytest.mark.parametrize("text", [None, "", "   "])
def test_empty_text_is_negative_not_unplaced(text: str | None) -> None:
    assert judge_consent_signed(text) is TextJudgment.NEGATIVE
    assert judge_plan_clear(text, "apixaban") is TextJudgment.NEGATIVE


# --- Plan judgments -------------------------------------------------------


def test_vague_plan_with_anchor_is_negative() -> None:
    # case_00042: "pending" beside "plan".
    text = "Discussed blood thinner use with patient; final plan pending specialist input."
    assert judge_plan_clear(text, "apixaban") is TextJudgment.NEGATIVE


def test_perioperative_management_not_yet_is_negative() -> None:
    # Cases 6, 21, 26: "not yet" sits beside "perioperative management", which
    # are plan scope words.
    text = (
        "Anticoagulant noted in medication list; "
        "perioperative management details not yet documented."
    )
    assert judge_plan_clear(text, "apixaban") is TextJudgment.NEGATIVE


@pytest.mark.parametrize("anchor", ["management", "perioperative", "peri-op", "periop"])
def test_widened_scope_words_count(anchor: str) -> None:
    assert judge_plan_clear(f"Details of {anchor} not yet documented.", "apixaban") is (
        TextJudgment.NEGATIVE
    )


def test_negative_without_any_scope_word_is_unplaced() -> None:
    text = "Anticoagulant noted in medication list; cardiology note not yet received."
    assert judge_plan_clear(text, "apixaban") is TextJudgment.UNPLACED


def test_negative_counts_with_drug_name_as_anchor() -> None:
    assert judge_plan_clear("Warfarin dosing unclear.", "warfarin") is TextJudgment.NEGATIVE
    assert judge_plan_clear("Warfarin dosing unclear.", "apixaban") is TextJudgment.UNPLACED


@pytest.mark.parametrize(
    "text",
    [
        # There is no positive keyword set for plans. Even a complete plan is
        # UNPLACED; only a resolver can call it clear.
        "Hold apixaban 48 hours before surgery; resume 24 hours after if hemostasis secured.",
        "Hold apixaban 48 hours before surgery.",
        "Resume apixaban the day after surgery.",
        "Perioperative anticoagulation plan documented.",
    ],
)
def test_plan_text_is_never_positive(text: str) -> None:
    assert judge_plan_clear(text, "apixaban") is TextJudgment.UNPLACED


def test_word_boundaries_apply() -> None:
    # "household" and "threshold" contain "hold"; "undetermined" is not a token.
    assert judge_plan_clear("Household threshold pending review.", "apixaban") is (
        TextJudgment.UNPLACED
    )


def test_offline_resolver_defaults() -> None:
    resolver = OfflineResolver()
    assert resolver.resolve_title("Medical Clearance [PDF]") is DocKind.OTHER
    assert resolver.resolve_consent_signed("anything") is False
    assert resolver.resolve_plan_clear("anything", "apixaban") is False


# --- Widened title patterns ----------------------------------------------------


@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("Anticoagulation Plan", DocKind.ANTICOAG_PLAN),
        ("Anticoagulation Management Note", DocKind.ANTICOAG_PLAN),
        ("Perioperative Medication Instructions", DocKind.ANTICOAG_PLAN),
        ("H & P", DocKind.H_AND_P),
        ("Interval H&P", DocKind.H_AND_P),
        ("H&P Addendum", DocKind.H_AND_P),
        ("Pre-op Interval History and Physical Addendum - signed", DocKind.H_AND_P),
    ],
)
def test_widened_titles_are_required(title: str, kind: DocKind) -> None:
    assert classify_title(title) == TitleClassification(TitleOutcome.REQUIRED, kind)


@pytest.mark.parametrize(
    "title",
    [
        "Anesthesia Consent",
        "Anaesthesia Consent Form",
        "Research Consent",
        "Blood Transfusion Consent",
        "Photography Consent",
        "Consent Withdrawn",
        "Consent Refused",
        "Consent Revoked",
    ],
)
def test_other_consent_kinds_are_known_other(title: str) -> None:
    assert classify_title(title) == TitleClassification(TitleOutcome.KNOWN_OTHER)


# --- Consent negatives from the pre-submission review, verbatim --------------------

REVIEW_CONSENT_NEGATIVES = (
    "Consent form signed by surgeon; patient signature outstanding.",
    "Consent signed by patient. Patient later withdrew consent.",
    "Signed consent rescinded by patient on 2026-02-22.",
    "Patient consent form. Signed: ______________ Date: ________",
    "Patient signed consent for left knee arthroscopy; new consent required for planned procedure.",
)


@pytest.mark.parametrize("text", REVIEW_CONSENT_NEGATIVES)
def test_review_consent_texts_are_negative(text: str) -> None:
    assert judge_consent_signed(text) is TextJudgment.NEGATIVE


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Tier 2 bare negatives fire without a sign-word in the clause.
        ("Consent obtained. Consent void.", TextJudgment.NEGATIVE),
        ("Consent revoked.", TextJudgment.NEGATIVE),
        # Tier 1 tokens need a sign-word in the same clause.
        ("Consent reviewed; follow-up required.", TextJudgment.UNPLACED),
        ("Consent signed; outstanding balance discussed.", TextJudgment.POSITIVE),
        # Accepted cost of "required" as a tier 1 token: the safe direction.
        ("Consent signed as required by policy.", TextJudgment.NEGATIVE),
        # "voided" is not the word "void".
        ("Consent signed; patient voided before procedure.", TextJudgment.POSITIVE),
    ],
)
def test_consent_token_tiers(text: str, expected: TextJudgment) -> None:
    assert judge_consent_signed(text) is expected


@pytest.mark.parametrize(
    "title",
    [
        "Surgical and Anesthesia Consent",
        "Anesthesia Consent for Surgery",
        "Surgery and Blood Transfusion Consent",
    ],
)
def test_consent_title_naming_surgery_is_surgical_consent(title: str) -> None:
    assert classify_title(title) == TitleClassification(TitleOutcome.REQUIRED, DocKind.CONSENT)


# --- Anticoagulant names ------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "generic"),
    [
        ("Eliquis 5 mg BID", "apixaban"),
        ("Xarelto 20 mg daily", "rivaroxaban"),
        ("COUMADIN", "warfarin"),
        ("Jantoven 5mg", "warfarin"),
        ("Lovenox 40 mg SC", "enoxaparin"),
        ("Savaysa", "edoxaban"),
        ("Lixiana", "edoxaban"),
        ("Pradaxa", "dabigatran"),
        ("Fragmin", "dalteparin"),
        ("Arixtra", "fondaparinux"),
        ("heparin flush", "heparin"),
        ("apixaban", "apixaban"),
        ("lisinopril", None),
        ("Eliquisx", None),
        (None, None),
    ],
)
def test_anticoagulant_generic(name: str | None, generic: str | None) -> None:
    assert anticoagulant_generic(name) == generic


def test_brand_name_anchors_a_plan_negative() -> None:
    assert judge_plan_clear("Eliquis dosing unclear.", "apixaban") is TextJudgment.NEGATIVE
