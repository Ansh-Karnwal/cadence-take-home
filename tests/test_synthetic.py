"""Synthetic edge cases, one path per test, from a minimal hand-written submission.

The base submission is READY: LOW risk, procedure 2026-03-03, a recent H&P, a
signed consent, a recent CBC, normal vitals, no anticoagulant. Each test
changes only what its path needs.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from core import PatientSubmission, TriageOutput, triage_submission
from preop_triage import engine
from preop_triage.classify import DocKind, TextJudgment, judge_plan_clear
from preop_triage.settings import Settings
from run_evals import _check_issues_value_grounding

PROCEDURE_DATE = "2026-03-03"

BASE: dict[str, Any] = {
    "procedure": {"procedure_risk": "LOW", "procedure_date": PROCEDURE_DATE},
    "vitals": [
        {"type": "blood_pressure", "systolic": 120, "diastolic": 80, "date": "2026-02-24T10:12:00Z"},
        {"type": "temperature", "value_f": 98.6, "date": "2026-02-24T10:15:00Z"},
    ],
    "labs": [
        {"code": "CBC", "display": "Complete Blood Count", "effective_at": "2026-02-25T08:00:00Z", "status": "final"},
    ],
    "medications": [{"name": "lisinopril", "active": True}],
    "conditions": [],
    "documents": [
        {"type": "History and Physical", "date": "2026-02-20", "text": "History and physical completed."},
        {"type": "Surgical Consent", "date": "2026-02-21", "text": "Consent obtained; signature on file."},
    ],
}

APIXABAN_ACTIVE = [{"name": "lisinopril", "active": True}, {"name": "apixaban", "active": True}]


def make(**overrides: Any) -> dict[str, Any]:
    submission = copy.deepcopy(BASE)
    submission.update(copy.deepcopy(overrides))
    return submission


def run(submission: dict[str, Any]) -> TriageOutput:
    return triage_submission(
        submission=submission, model="unused", settings=Settings(offline=True)
    )


def triples(output: TriageOutput) -> set[tuple[str, str, str]]:
    return {(i.category, i.evidence.source, i.description) for i in output.issues}


def grounded(submission: dict[str, Any], output: TriageOutput) -> bool:
    dumped = PatientSubmission.model_validate(submission).model_dump()
    return _check_issues_value_grounding(dumped, output)


def test_base_submission_is_ready() -> None:
    output = run(make())
    assert output.decision == "READY"
    assert output.issues == []
    assert output.explanation == (
        "All required documentation, testing, anticoagulation planning, "
        "and safety checks are satisfied."
    )


# --- Rule 3: anticoagulation ------------------------------------------------


COMPLETE_PLAN = "Hold apixaban 48 hours before surgery; resume apixaban 24 hours after surgery."


class FakeResolver:
    """A ``Resolver`` whose plan answers come from a per-drug table (injected for tests)."""

    def __init__(self, clear_for: set[str]) -> None:
        self.clear_for = clear_for
        self.plan_calls: list[tuple[str, str]] = []
        self.title_calls: list[str] = []
        self.consent_calls: list[str] = []
        self.consent_answer = False

    def resolve_title(self, title: str) -> DocKind:
        self.title_calls.append(title)
        return DocKind.OTHER

    def resolve_consent_signed(self, text: str, *, fallback: bool = False) -> bool:
        self.consent_calls.append(text)
        return self.consent_answer

    def resolve_plan_clear(self, text: str, drug: str) -> bool:
        self.plan_calls.append((text, drug))
        return drug in self.clear_for


@pytest.fixture
def fake_resolver(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Install a FakeResolver in place of the engine's resolver; set ``clear_for`` per test."""

    fake = FakeResolver(clear_for=set())
    monkeypatch.setattr(engine, "build_resolver", lambda **_: fake)
    return fake


def _with_plan(text: str, title: str = "Perioperative Medication Plan") -> list[dict[str, Any]]:
    return BASE["documents"] + [{"type": title, "date": "2026-02-22", "text": text}]


def test_complete_plan_is_flagged_offline() -> None:
    # Checkpoint 1 (D4): keywords never call a plan clear, and the offline
    # resolver never does either. Offline never clears an active anticoagulant.
    assert judge_plan_clear(COMPLETE_PLAN, "apixaban") is TextJudgment.UNPLACED
    submission = make(medications=APIXABAN_ACTIVE, documents=_with_plan(COMPLETE_PLAN))
    output = run(submission)
    assert output.decision == "NEEDS_FOLLOW_UP"
    assert triples(output) == {
        ("ANTICOAGULATION_MANAGEMENT", "documents[2]", "Missing perioperative anticoagulation plan")
    }


def test_complete_plan_is_cleared_when_resolver_says_clear(fake_resolver: FakeResolver) -> None:
    fake_resolver.clear_for = {"apixaban"}
    output = run(make(medications=APIXABAN_ACTIVE, documents=_with_plan(COMPLETE_PLAN)))
    assert output.decision == "READY"
    assert output.issues == []
    assert fake_resolver.plan_calls == [(COMPLETE_PLAN, "apixaban")]


def test_negative_plan_never_reaches_resolver(fake_resolver: FakeResolver) -> None:
    # A rule match is final (D2): a NEGATIVE plan is flagged without asking.
    fake_resolver.clear_for = {"apixaban"}
    text = "Cardiology to advise whether to hold or resume apixaban."
    output = run(make(medications=APIXABAN_ACTIVE, documents=_with_plan(text)))
    assert output.decision == "NEEDS_FOLLOW_UP"
    assert fake_resolver.plan_calls == []


def test_each_title_is_classified_once(fake_resolver: FakeResolver) -> None:
    documents = _with_plan(COMPLETE_PLAN) + [
        {"type": "Medical Clearance [PDF]", "date": "2026-02-10", "text": "Prior clearance."}
    ]
    run(make(medications=APIXABAN_ACTIVE, documents=documents))
    assert fake_resolver.title_calls == ["Medical Clearance [PDF]"]


def test_inactive_anticoagulant_produces_no_issue() -> None:
    output = run(make(medications=[{"name": "warfarin", "active": False}]))
    assert output.decision == "READY"
    assert output.issues == []


def test_unknown_anticoagulant_status_is_missing_data_only() -> None:
    output = run(make(medications=[{"name": "lisinopril", "active": True}, {"name": "warfarin", "active": None}]))
    assert triples(output) == {
        ("MISSING_REQUIRED_DATA", "medications[1]", "Unknown anticoagulant active status")
    }


def test_active_anticoagulant_with_no_plan_document_cites_documents_and_grounds() -> None:
    submission = make(medications=APIXABAN_ACTIVE)
    output = run(submission)
    assert triples(output) == {
        ("ANTICOAGULATION_MANAGEMENT", "documents", "Missing perioperative anticoagulation plan")
    }
    assert "apixaban" in output.issues[0].evidence.details
    assert grounded(submission, output)


# --- D4 substring traps, end to end ----------------------------------------


def test_trap_unsigned_consent_is_flagged() -> None:
    documents = [
        BASE["documents"][0],
        {"type": "Consent Counseling Note", "date": "2026-02-21", "text": "Unsigned consent noted; signature not yet on file."},
    ]
    submission = make(documents=documents)
    output = run(submission)
    assert triples(output) == {
        ("REQUIRED_DOCUMENTATION", "documents[1]", "Surgical consent not clearly signed")
    }
    assert grounded(submission, output)


@pytest.mark.parametrize(
    "text",
    [
        "Apixaban listed; perioperative management plan to be finalized.",
        "Anticoagulation mentioned; no clear hold/resume guidance documented.",
    ],
)
def test_trap_vague_plan_is_flagged(text: str) -> None:
    documents = BASE["documents"] + [
        {"type": "Perioperative Medication Review", "date": "2026-02-22", "text": text}
    ]
    submission = make(medications=APIXABAN_ACTIVE, documents=documents)
    output = run(submission)
    assert triples(output) == {
        ("ANTICOAGULATION_MANAGEMENT", "documents[2]", "Missing perioperative anticoagulation plan")
    }
    assert grounded(submission, output)


# --- Rule 1: documentation windows and selection ----------------------------


@pytest.mark.parametrize(("hp_date", "flagged"), [("2026-02-01", False), ("2026-01-31", True)])
def test_hp_window_boundary(hp_date: str, flagged: bool) -> None:
    # 2026-02-01 is exactly 30 days before 2026-03-03; 2026-01-31 is 31.
    documents = copy.deepcopy(BASE["documents"])
    documents[0]["date"] = hp_date
    output = run(make(documents=documents))
    expected = {("REQUIRED_DOCUMENTATION", "documents[0]", "H&P outside 30-day window")}
    assert triples(output) == (expected if flagged else set())


def test_hp_dated_after_procedure_is_out_of_window() -> None:
    documents = copy.deepcopy(BASE["documents"])
    documents[0]["date"] = "2026-03-05"
    submission = make(documents=documents)
    output = run(submission)
    assert triples(output) == {
        ("REQUIRED_DOCUMENTATION", "documents[0]", "H&P outside 30-day window")
    }
    assert grounded(submission, output)


def test_document_with_null_date_is_ignored_for_selection() -> None:
    documents = [
        {"type": "H&P Update", "date": None, "text": "Undated update."},
        {"type": "History and Physical", "date": "2026-01-01", "text": "Old H&P."},
        BASE["documents"][1],
    ]
    output = run(make(documents=documents))
    # The undated H&P at index 0 is skipped; the dated one at index 1 is 61 days old.
    assert triples(output) == {
        ("REQUIRED_DOCUMENTATION", "documents[1]", "H&P outside 30-day window")
    }


def test_only_undated_hp_counts_as_missing() -> None:
    documents = [{"type": "History and Physical", "date": None, "text": "Undated."}, BASE["documents"][1]]
    output = run(make(documents=documents))
    assert triples(output) == {
        ("REQUIRED_DOCUMENTATION", "documents", "History and Physical document missing")
    }


def test_most_recent_hp_by_date_not_index() -> None:
    documents = [
        {"type": "History and Physical", "date": "2026-01-01", "text": "Old H&P."},
        BASE["documents"][1],
        {"type": "H&P Update", "date": "2026-02-25", "text": "Recent H&P."},
    ]
    output = run(make(documents=documents))
    assert output.issues == []


def test_empty_documents_gives_both_missing_issues() -> None:
    submission = make(documents=[])
    output = run(submission)
    assert triples(output) == {
        ("REQUIRED_DOCUMENTATION", "documents", "History and Physical document missing"),
        ("REQUIRED_DOCUMENTATION", "documents", "Signed surgical consent missing"),
    }
    # Grounding is unreachable here under the harness rules: the details cannot
    # cite a value from an empty collection, and the fuzzy fallback scans that
    # same empty collection (DESIGN.md section 4, section 9 item 5).
    assert grounded(submission, output) is False


def test_null_date_with_missing_consent_reports_both() -> None:
    procedure = {"procedure_risk": "LOW", "procedure_date": None}
    documents = [
        {"type": "History and Physical", "date": "2026-02-20", "text": "H&P done. Procedure date target: 2026-03-03."}
    ]
    output = run(make(procedure=procedure, documents=documents))
    assert triples(output) == {
        ("MISSING_REQUIRED_DATA", "procedure.procedure_date", "Missing procedure date"),
        ("REQUIRED_DOCUMENTATION", "documents", "Signed surgical consent missing"),
    }
    date_issue = next(i for i in output.issues if i.description == "Missing procedure date")
    # D6: the free-text target is quoted for a reviewer, never used.
    assert "Procedure date target: 2026-03-03" in date_issue.evidence.details


def test_null_date_skips_windows_but_not_presence() -> None:
    procedure = {"procedure_risk": "LOW", "procedure_date": None}
    documents = copy.deepcopy(BASE["documents"])
    documents[0]["date"] = "2025-01-01"  # would be far out of window
    output = run(make(procedure=procedure, documents=documents, labs=[]))
    assert triples(output) == {
        ("MISSING_REQUIRED_DATA", "procedure.procedure_date", "Missing procedure date"),
        ("REQUIRED_TESTING", "labs", "CBC missing"),
    }


# --- Rule 2: testing windows ------------------------------------------------


def _lab(code: str, day: str) -> dict[str, Any]:
    return {"code": code, "display": code, "effective_at": f"{day}T08:00:00Z", "status": "final"}


@pytest.mark.parametrize(
    ("risk", "cbc_day", "flagged_description"),
    [
        ("HIGH", "2026-02-17", None),  # exactly 14
        ("HIGH", "2026-02-16", "CBC outside 14-day window for HIGH risk procedure"),  # 15
        ("LOW", "2026-02-01", None),  # exactly 30
        ("LOW", "2026-01-31", "CBC outside 30-day window for LOW risk procedure"),  # 31
        ("MODERATE", "2026-01-31", "CBC outside 30-day window for MODERATE risk procedure"),
    ],
)
def test_cbc_window_boundaries(risk: str, cbc_day: str, flagged_description: str | None) -> None:
    labs = [_lab("CBC", cbc_day), _lab("CMP", "2026-02-25")]
    submission = make(procedure={"procedure_risk": risk, "procedure_date": PROCEDURE_DATE}, labs=labs)
    output = run(submission)
    expected = set() if flagged_description is None else {
        ("REQUIRED_TESTING", "labs[0]", flagged_description)
    }
    assert triples(output) == expected
    assert grounded(submission, output)


def test_lab_dated_after_procedure_is_out_of_window() -> None:
    submission = make(labs=[_lab("CBC", "2026-03-05")])
    output = run(submission)
    assert triples(output) == {
        ("REQUIRED_TESTING", "labs[0]", "CBC outside 30-day window for LOW risk procedure")
    }
    assert grounded(submission, output)


def test_high_risk_requires_cmp_and_accepts_alias() -> None:
    procedure = {"procedure_risk": "HIGH", "procedure_date": PROCEDURE_DATE}
    assert triples(run(make(procedure=procedure, labs=[_lab("LAB-CBC", "2026-02-25")]))) == {
        ("REQUIRED_TESTING", "labs", "CMP missing")
    }
    labs = [_lab("LAB-CBC", "2026-02-25"), _lab("lab-cmp", "2026-02-25")]
    assert run(make(procedure=procedure, labs=labs)).issues == []


def test_only_most_recent_lab_counts() -> None:
    # A newer in-window result wins over an older out-of-window one.
    assert run(make(labs=[_lab("CBC", "2026-01-01"), _lab("CBC", "2026-02-25")])).issues == []
    # An older in-window result does not rescue the newest, out-of-window one.
    labs = [_lab("CBC", "2026-02-25"), _lab("CBC", "2026-03-05")]
    assert triples(run(make(labs=labs))) == {
        ("REQUIRED_TESTING", "labs[1]", "CBC outside 30-day window for LOW risk procedure")
    }


def test_null_risk_skips_rule_two() -> None:
    procedure = {"procedure_risk": None, "procedure_date": PROCEDURE_DATE}
    output = run(make(procedure=procedure, labs=[]))
    assert triples(output) == {
        ("MISSING_REQUIRED_DATA", "procedure.procedure_risk", "Missing procedure risk")
    }


def test_empty_labs_gives_cbc_missing() -> None:
    submission = make(labs=[])
    output = run(submission)
    assert triples(output) == {("REQUIRED_TESTING", "labs", "CBC missing")}
    # Grounding is unreachable here under the harness rules (empty collection).
    assert grounded(submission, output) is False


# --- Rule 4: vitals -----------------------------------------------------------


def _bp(systolic: float | None, diastolic: float | None, when: str = "2026-02-24T10:12:00Z") -> dict[str, Any]:
    return {"type": "blood_pressure", "systolic": systolic, "diastolic": diastolic, "date": when}


def _temp(value: float | None, when: str = "2026-02-24T10:15:00Z") -> dict[str, Any]:
    return {"type": "temperature", "value_f": value, "date": when}


@pytest.mark.parametrize(
    ("systolic", "diastolic", "excluded"),
    [(180, 80, True), (179, 80, False), (120, 110, True), (120, 109, False)],
)
def test_bp_thresholds(systolic: int, diastolic: int, excluded: bool) -> None:
    submission = make(vitals=[_bp(systolic, diastolic), _temp(98.6)])
    output = run(submission)
    if excluded:
        assert output.decision == "NOT_CLEARED"
        assert triples(output) == {
            ("ACUTE_SAFETY_EXCLUSION", "vitals[0]", "Blood pressure meets exclusion threshold")
        }
        assert grounded(submission, output)
    else:
        assert output.decision == "READY"


@pytest.mark.parametrize(("value", "excluded"), [(100.4, False), (100.5, True)])
def test_temperature_threshold(value: float, excluded: bool) -> None:
    submission = make(vitals=[_bp(120, 80), _temp(value)])
    output = run(submission)
    if excluded:
        assert output.decision == "NOT_CLEARED"
        assert triples(output) == {
            ("ACUTE_SAFETY_EXCLUSION", "vitals[1]", "Temperature exceeds exclusion threshold")
        }
        assert grounded(submission, output)
    else:
        assert output.decision == "READY"


def test_tied_vital_timestamps_take_later_index() -> None:
    same = "2026-02-24T10:12:00Z"
    later_high = make(vitals=[_bp(120, 80, same), _bp(190, 80, same), _temp(98.6)])
    assert triples(run(later_high)) == {
        ("ACUTE_SAFETY_EXCLUSION", "vitals[1]", "Blood pressure meets exclusion threshold")
    }
    later_normal = make(vitals=[_bp(190, 80, same), _bp(120, 80, same), _temp(98.6)])
    assert run(later_normal).issues == []


def test_most_recent_vital_by_date_not_index() -> None:
    vitals = [_bp(120, 80, "2026-02-24T10:12:00Z"), _bp(190, 80, "2026-02-20T10:12:00Z"), _temp(98.6)]
    assert run(make(vitals=vitals)).issues == []


def test_empty_vitals_gives_two_missing_issues_not_excluded() -> None:
    output = run(make(vitals=[]))
    assert output.decision == "NEEDS_FOLLOW_UP"
    assert triples(output) == {
        ("MISSING_REQUIRED_DATA", "vitals", "Missing latest blood pressure"),
        ("MISSING_REQUIRED_DATA", "vitals", "Missing latest temperature"),
    }


def test_latest_bp_with_null_value_is_missing_data() -> None:
    output = run(make(vitals=[_bp(None, 80), _temp(98.6)]))
    assert triples(output) == {
        ("MISSING_REQUIRED_DATA", "vitals[0]", "Missing latest blood pressure")
    }


def test_latest_bp_with_one_null_value_still_excludes_on_the_other() -> None:
    output = run(make(vitals=[_bp(None, 115), _temp(98.6)]))
    assert output.decision == "NOT_CLEARED"


# --- Engine robustness and assembly -------------------------------------------


def test_fully_empty_submission_needs_follow_up() -> None:
    output = run({})
    assert output.decision == "NEEDS_FOLLOW_UP"
    assert {i.description for i in output.issues} == {
        "Missing procedure date",
        "Missing procedure risk",
        "History and Physical document missing",
        "Signed surgical consent missing",
        "Missing latest blood pressure",
        "Missing latest temperature",
    }


def test_document_with_all_null_fields_does_not_raise() -> None:
    documents = BASE["documents"] + [{"type": None, "date": None, "text": None, "doc_id": None}]
    assert run(make(documents=documents)).decision == "READY"


def test_unparseable_procedure_date_is_missing() -> None:
    output = run(make(procedure={"procedure_risk": "LOW", "procedure_date": "next Tuesday"}))
    assert triples(output) == {
        ("MISSING_REQUIRED_DATA", "procedure.procedure_date", "Missing procedure date")
    }
    assert "next Tuesday" in output.issues[0].evidence.details


def test_issue_order_explanation_and_not_cleared_keeps_other_issues() -> None:
    submission = make(
        procedure={"procedure_risk": "LOW", "procedure_date": None},
        medications=APIXABAN_ACTIVE,
        vitals=[_bp(185, 80), _temp(98.6)],
        labs=[],
    )
    output = run(submission)
    assert output.decision == "NOT_CLEARED"
    assert [i.category for i in output.issues] == [
        "MISSING_REQUIRED_DATA",
        "REQUIRED_TESTING",
        "ANTICOAGULATION_MANAGEMENT",
        "ACUTE_SAFETY_EXCLUSION",
    ]
    assert output.explanation == (
        "MISSING_REQUIRED_DATA: Missing procedure date | "
        "REQUIRED_TESTING: CBC missing | "
        "ANTICOAGULATION_MANAGEMENT: Missing perioperative anticoagulation plan | "
        "ACUTE_SAFETY_EXCLUSION: Blood pressure meets exclusion threshold"
    )


# --- Verifier probes, end to end (checkpoint 1), verbatim -----------------------
# Each must produce NEEDS_FOLLOW_UP or NOT_CLEARED in offline mode. Plan probes
# are (document type, text), each added with active apixaban.

VAGUE_PLAN_PROBES = (
    (
        "Pre-op Nursing Intake",
        "Home meds reviewed incl. apixaban. Hold metformin morning of surgery; resume diet after.",
    ),
    ("Clinic Follow-up Note", "Cardiology to advise whether to hold or resume apixaban."),
    ("Clinic Follow-up Note", "Unclear whether to hold apixaban; will resume later."),
    ("H&P Note", "Patient on apixaban. Hold and resume per cardiology."),
)

NEGATED_CONSENT_PROBES = (
    "Consent not signed.",
    "Patient has not signed consent.",
    "Consent to be signed on day of surgery.",
    "Consent form signed by witness; patient signature missing.",
)

HP_DOC = BASE["documents"][0]
SIGNED = "Consent obtained and signed; documentation completed."


@pytest.mark.parametrize(("doc_type", "text"), VAGUE_PLAN_PROBES)
def test_probe_vague_plan_is_flagged(doc_type: str, text: str) -> None:
    submission = make(medications=APIXABAN_ACTIVE, documents=_with_plan(text, doc_type))
    output = run(submission)
    assert output.decision == "NEEDS_FOLLOW_UP"
    assert triples(output) == {
        ("ANTICOAGULATION_MANAGEMENT", "documents[2]", "Missing perioperative anticoagulation plan")
    }
    assert grounded(submission, output)


@pytest.mark.parametrize("text", NEGATED_CONSENT_PROBES)
def test_probe_negated_consent_is_flagged(text: str) -> None:
    documents = [HP_DOC, {"type": "Surgical Consent", "date": "2026-02-21", "text": text}]
    submission = make(documents=documents)
    output = run(submission)
    assert output.decision == "NEEDS_FOLLOW_UP"
    assert triples(output) == {
        ("REQUIRED_DOCUMENTATION", "documents[1]", "Surgical consent not clearly signed")
    }
    assert grounded(submission, output)


def test_probe_signed_anesthesia_consent_alone_is_not_surgical_consent() -> None:
    documents = [HP_DOC, {"type": "Anesthesia Consent", "date": "2026-02-21", "text": SIGNED}]
    output = run(make(documents=documents))
    assert triples(output) == {
        ("REQUIRED_DOCUMENTATION", "documents", "Signed surgical consent missing")
    }


def test_probe_signed_anesthesia_consent_beside_unsigned_surgical_consent() -> None:
    documents = [
        HP_DOC,
        {"type": "Anesthesia Consent", "date": "2026-02-22", "text": SIGNED},
        {"type": "Surgical Consent", "date": "2026-02-21", "text": "Unsigned consent on chart."},
    ]
    output = run(make(documents=documents))
    assert triples(output) == {
        ("REQUIRED_DOCUMENTATION", "documents[2]", "Surgical consent not clearly signed")
    }


def test_probe_consent_withdrawn_title_is_not_a_consent() -> None:
    documents = [HP_DOC, {"type": "Consent Withdrawn", "date": "2026-02-21", "text": SIGNED}]
    output = run(make(documents=documents))
    assert triples(output) == {
        ("REQUIRED_DOCUMENTATION", "documents", "Signed surgical consent missing")
    }


def test_probe_same_day_later_bp_listed_first_wins() -> None:
    vitals = [
        _bp(185, 95, "2026-02-24T16:00:00Z"),
        _bp(120, 80, "2026-02-24T08:00:00Z"),
        _temp(98.6),
    ]
    output = run(make(vitals=vitals))
    assert output.decision == "NOT_CLEARED"
    assert triples(output) == {
        ("ACUTE_SAFETY_EXCLUSION", "vitals[0]", "Blood pressure meets exclusion threshold")
    }


def test_probe_same_day_later_temperature_listed_first_wins() -> None:
    vitals = [
        _bp(120, 80),
        _temp(101.2, "2026-02-24T16:00:00Z"),
        _temp(98.6, "2026-02-24T08:00:00Z"),
    ]
    output = run(make(vitals=vitals))
    assert output.decision == "NOT_CLEARED"
    assert triples(output) == {
        ("ACUTE_SAFETY_EXCLUSION", "vitals[1]", "Temperature exceeds exclusion threshold")
    }


def test_probe_offset_lab_is_31_local_days_and_rejected() -> None:
    labs = [{"code": "CBC", "display": "CBC", "effective_at": "2026-01-31T20:00:00-05:00", "status": "final"}]
    submission = make(labs=labs)
    output = run(submission)
    assert triples(output) == {
        ("REQUIRED_TESTING", "labs[0]", "CBC outside 30-day window for LOW risk procedure")
    }
    assert "31 days prior" in output.issues[0].evidence.details
    assert grounded(submission, output)


def test_probe_older_signed_consent_does_not_rescue_newer_unsigned() -> None:
    documents = [
        HP_DOC,
        {"type": "Surgical Consent", "date": "2026-02-10", "text": SIGNED},
        {"type": "Surgical Consent", "date": "2026-02-21", "text": "Consent not signed."},
    ]
    submission = make(documents=documents)
    output = run(submission)
    assert triples(output) == {
        ("REQUIRED_DOCUMENTATION", "documents[2]", "Surgical consent not clearly signed")
    }
    assert grounded(submission, output)


def test_newer_signed_consent_supersedes_older_unsigned() -> None:
    documents = [
        HP_DOC,
        {"type": "Surgical Consent", "date": "2026-02-10", "text": "Consent not signed."},
        {"type": "Surgical Consent", "date": "2026-02-21", "text": SIGNED},
    ]
    assert run(make(documents=documents)).issues == []


def test_probe_two_anticoagulants_with_plan_for_one_offline() -> None:
    meds = [{"name": "apixaban", "active": True}, {"name": "Enoxaparin 40 mg SC daily", "active": True}]
    submission = make(medications=meds, documents=_with_plan(COMPLETE_PLAN))
    output = run(submission)
    # Offline: neither plan is ever clear; one issue per drug.
    assert [i.category for i in output.issues] == ["ANTICOAGULATION_MANAGEMENT"] * 2
    details = " ".join(i.evidence.details for i in output.issues)
    assert "medications[0]: apixaban" in details
    assert "medications[1]: Enoxaparin 40 mg SC daily" in details
    assert grounded(submission, output)


def test_probe_two_anticoagulants_with_plan_for_one_resolver(fake_resolver: FakeResolver) -> None:
    fake_resolver.clear_for = {"apixaban"}
    meds = [{"name": "apixaban", "active": True}, {"name": "warfarin", "active": True}]
    output = run(make(medications=meds, documents=_with_plan(COMPLETE_PLAN)))
    # Apixaban's plan is clear; warfarin still needs its own.
    assert output.decision == "NEEDS_FOLLOW_UP"
    assert len(output.issues) == 1
    assert "medications[1]: warfarin" in output.issues[0].evidence.details
    assert ("warfarin" in {drug for _, drug in fake_resolver.plan_calls})


def test_probe_apixaban_with_dose_and_no_plan_grounds() -> None:
    meds = [{"name": "lisinopril", "active": True}, {"name": "Apixaban 5 mg BID", "active": True}]
    submission = make(medications=meds)
    output = run(submission)
    assert triples(output) == {
        ("ANTICOAGULATION_MANAGEMENT", "documents", "Missing perioperative anticoagulation plan")
    }
    assert "Apixaban 5 mg BID" in output.issues[0].evidence.details
    assert grounded(submission, output)


def test_probe_cmp_outside_14_days() -> None:
    procedure = {"procedure_risk": "HIGH", "procedure_date": PROCEDURE_DATE}
    labs = [_lab("CBC", "2026-02-25"), _lab("CMP", "2026-02-16")]
    submission = make(procedure=procedure, labs=labs)
    output = run(submission)
    assert triples(output) == {
        ("REQUIRED_TESTING", "labs[1]", "CMP outside 14-day window for HIGH risk procedure")
    }
    assert grounded(submission, output)


def test_probe_preliminary_lab_is_ignored() -> None:
    labs = [{**_lab("CBC", "2026-02-25"), "status": "preliminary"}]
    submission = make(labs=labs)
    output = run(submission)
    assert triples(output) == {("REQUIRED_TESTING", "labs", "CBC missing")}
    assert "ignored" in output.issues[0].evidence.details
    assert grounded(submission, output)


def test_preliminary_newer_lab_falls_back_to_older_final() -> None:
    labs = [
        {**_lab("CBC", "2026-02-27"), "status": "preliminary"},
        {**_lab("CBC", "2026-01-15"), "status": "final"},
    ]
    output = run(make(labs=labs))
    assert triples(output) == {
        ("REQUIRED_TESTING", "labs[1]", "CBC outside 30-day window for LOW risk procedure")
    }


@pytest.mark.parametrize("status", [None, "final", "Amended", "CORRECTED"])
def test_counted_lab_statuses(status: str | None) -> None:
    labs = [{**_lab("CBC", "2026-02-25"), "status": status}]
    assert run(make(labs=labs)).issues == []


def test_probe_anticoagulation_plan_title_with_vague_text_is_flagged() -> None:
    text = "Discussed blood thinner use; plan pending."
    output = run(make(medications=APIXABAN_ACTIVE, documents=_with_plan(text, "Anticoagulation Plan")))
    assert triples(output) == {
        ("ANTICOAGULATION_MANAGEMENT", "documents[2]", "Missing perioperative anticoagulation plan")
    }


@pytest.mark.parametrize("title", ["H & P", "Interval H&P", "H&P Addendum"])
def test_probe_widened_hp_titles_count_as_hp(title: str) -> None:
    # Classification probe: as the only H&P in an otherwise complete package,
    # a recognized title correctly gives READY. Unrecognized, it would give
    # "History and Physical document missing".
    documents = [{**HP_DOC, "type": title}, BASE["documents"][1]]
    assert run(make(documents=documents)).decision == "READY"


# --- Consent confirmation in default mode -----------------------------------------
# Keywords can add an issue but never remove one: a keyword-POSITIVE consent is
# confirmed by the resolver in default mode; a keyword-NEGATIVE one is final.


def run_default(submission: dict[str, Any]) -> TriageOutput:
    return triage_submission(submission=submission, model="unused", settings=Settings())


def test_keyword_positive_consent_cleared_only_when_resolver_confirms(fake_resolver: FakeResolver) -> None:
    fake_resolver.consent_answer = True
    output = run_default(make())
    assert output.issues == []
    assert fake_resolver.consent_calls == [BASE["documents"][1]["text"]]


def test_keyword_positive_consent_flagged_when_resolver_disagrees(fake_resolver: FakeResolver) -> None:
    fake_resolver.consent_answer = False
    output = run_default(make())
    assert triples(output) == {
        ("REQUIRED_DOCUMENTATION", "documents[1]", "Surgical consent not clearly signed")
    }


def test_keyword_negative_consent_never_calls_resolver(fake_resolver: FakeResolver) -> None:
    fake_resolver.consent_answer = True
    documents = [HP_DOC, {"type": "Surgical Consent", "date": "2026-02-21", "text": "Consent not signed."}]
    output = run_default(make(documents=documents))
    assert fake_resolver.consent_calls == []
    assert "Surgical consent not clearly signed" in {i.description for i in output.issues}


def test_offline_trusts_keyword_positive_consent(fake_resolver: FakeResolver) -> None:
    fake_resolver.consent_answer = False
    assert run(make()).issues == []
    assert fake_resolver.consent_calls == []


# --- Review probes: consents, brands, unparseable dates, combined titles -----------


@pytest.mark.parametrize(
    "text",
    [
        "Consent form signed by surgeon; patient signature outstanding.",
        "Consent signed by patient. Patient later withdrew consent.",
        "Signed consent rescinded by patient on 2026-02-22.",
        "Patient consent form. Signed: ______________ Date: ________",
        "Patient signed consent for left knee arthroscopy; new consent required for planned procedure.",
    ],
)
def test_review_consent_probe_is_flagged_offline(text: str) -> None:
    documents = [HP_DOC, {"type": "Surgical Consent", "date": "2026-02-21", "text": text}]
    submission = make(documents=documents)
    output = run(submission)
    assert output.decision == "NEEDS_FOLLOW_UP"
    assert triples(output) == {
        ("REQUIRED_DOCUMENTATION", "documents[1]", "Surgical consent not clearly signed")
    }
    assert grounded(submission, output)


@pytest.mark.parametrize("name", ["Eliquis 5 mg BID", "Xarelto 20 mg daily"])
def test_brand_name_anticoagulant_is_flagged_offline(name: str) -> None:
    submission = make(medications=[{"name": "lisinopril", "active": True}, {"name": name, "active": True}])
    output = run(submission)
    assert triples(output) == {
        ("ANTICOAGULATION_MANAGEMENT", "documents", "Missing perioperative anticoagulation plan")
    }
    assert name in output.issues[0].evidence.details
    assert grounded(submission, output)


def test_brand_medication_matches_plan_naming_the_generic(fake_resolver: FakeResolver) -> None:
    meds = [{"name": "Eliquis 5 mg BID", "active": True}]
    documents = BASE["documents"] + [
        {"type": "Clinic Follow-up Note", "date": "2026-02-22", "text": COMPLETE_PLAN}
    ]
    fake_resolver.clear_for = {"apixaban"}
    fake_resolver.consent_answer = True
    assert run_default(make(medications=meds, documents=documents)).issues == []
    assert fake_resolver.plan_calls == [(COMPLETE_PLAN, "apixaban")]


@pytest.mark.parametrize(
    ("collection", "index", "field", "overrides", "description"),
    [
        (
            "vitals",
            2,
            "date",
            {"vitals": BASE["vitals"] + [{"type": "blood_pressure", "systolic": 190, "diastolic": 80, "date": "Feb 30th"}]},
            "Unparseable vital date",
        ),
        (
            "labs",
            1,
            "effective_at",
            {"labs": BASE["labs"] + [{"code": "CBC", "display": "CBC", "effective_at": "yesterday", "status": "final"}]},
            "Unparseable lab date",
        ),
        (
            "documents",
            2,
            "date",
            {"documents": BASE["documents"] + [{"type": "Pre-op Nursing Intake", "date": "03/01/2026", "text": "Intake."}]},
            "Unparseable document date",
        ),
    ],
)
def test_unparseable_date_is_missing_data_and_excluded(
    collection: str, index: int, field: str, overrides: dict[str, Any], description: str
) -> None:
    submission = make(**overrides)
    output = run(submission)
    raw = submission[collection][index][field]
    # The issue alone prevents READY; the bad-dated item is not selected (the
    # 190 systolic reading with a bad date does not exclude the patient).
    assert output.decision == "NEEDS_FOLLOW_UP"
    assert triples(output) == {("MISSING_REQUIRED_DATA", f"{collection}[{index}]", description)}
    assert repr(raw) in output.issues[0].evidence.details


def test_blank_date_string_counts_as_unparseable() -> None:
    documents = BASE["documents"] + [{"type": "Pre-op Nursing Intake", "date": "  ", "text": "Intake."}]
    assert triples(run(make(documents=documents))) == {
        ("MISSING_REQUIRED_DATA", "documents[2]", "Unparseable document date")
    }


def test_null_date_is_not_flagged_as_unparseable() -> None:
    documents = BASE["documents"] + [{"type": "Pre-op Nursing Intake", "date": None, "text": "Intake."}]
    assert run(make(documents=documents)).issues == []


def test_combined_surgical_and_anesthesia_consent_is_the_surgical_consent() -> None:
    documents = [HP_DOC, {"type": "Surgical and Anesthesia Consent", "date": "2026-02-21", "text": SIGNED}]
    assert run(make(documents=documents)).decision == "READY"
