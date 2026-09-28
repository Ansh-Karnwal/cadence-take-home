"""Missing-data gates and policy rules 1 through 4 (DESIGN.md section 4).

Each function returns the issues it raises. Rules write every issue
themselves; a ``Resolver`` only answers the title and text questions the
keyword layer left open (D2). Document titles are classified once per
evaluation by ``classify_documents`` and the result is passed to Rules 1 and 3.

Descriptions are the oracle's exact strings. Details follow the oracle's shape
plus the builder extension in the DESIGN.md section 4 table, so that every
non-missing issue cites a concrete value the grounding metric can find. Every
``source`` matches ``^[a-z_]+(\\[\\d+\\])?(\\.[a-z_]\\w*)?$``.

Day arithmetic uses local calendar dates (``parse_date``); "most recent" uses
full instants (``parse_instant``).
"""

from __future__ import annotations

import re
from datetime import date

from core import Document, LabResult, PatientSubmission, TriageIssue, TriageIssueEvidence

from .classify import (
    DocKind,
    Resolver,
    TextJudgment,
    TitleOutcome,
    anticoagulant_generic,
    classify_title,
    drug_name_re,
    judge_consent_signed,
    judge_plan_clear,
)
from .normalize import (
    canonical_lab_code,
    days_before,
    most_recent,
    parse_date,
    parse_instant,
    within_window,
)

H_AND_P_WINDOW_DAYS = 30
# Rule 2: (window in days, required canonical lab codes) per procedure risk.
TESTING_REQUIREMENTS: dict[str, tuple[int, tuple[str, ...]]] = {
    "LOW": (30, ("CBC",)),
    "MODERATE": (30, ("CBC",)),
    "HIGH": (14, ("CBC", "CMP")),
}
# A lab counts only with one of these statuses, or no status; a preliminary or
# cancelled result is not a result the policy can rely on.
# All 150 sample labs are "final".
COUNTED_LAB_STATUSES = frozenset({"final", "amended", "corrected"})
SYSTOLIC_LIMIT = 180  # exclusion when systolic >= 180
DIASTOLIC_LIMIT = 110  # exclusion when diastolic >= 110
TEMPERATURE_LIMIT_F = 100.4  # exclusion when temperature > 100.4

# D6: quoted in details when the structured date is null; never compared.
_TARGET_DATE_RE = re.compile(r"procedure date target:\s*\d{4}-\d{2}-\d{2}", re.IGNORECASE)
_ANTICOAG_WORD_RE = re.compile(r"\banticoag", re.IGNORECASE)


def _issue(category: str, description: str, source: str, details: str) -> TriageIssue:
    return TriageIssue(
        category=category,  # type: ignore[arg-type]
        description=description,
        evidence=TriageIssueEvidence(source=source, details=details),
    )


def _gap_phrase(item_date: date, procedure_date: date, window: int) -> str:
    gap = days_before(item_date, procedure_date)
    if gap >= 0:
        return f"({gap} days prior; must be within {window})"
    return f"({-gap} days after the procedure; must be within {window} days prior)"


def classify_documents(
    submission: PatientSubmission, resolver: Resolver, *, force_llm: bool = False
) -> list[DocKind]:
    """Classify every document title once. Only non-empty UNKNOWN titles reach the resolver.

    Under ``force_llm`` (measurement only) every non-empty title goes to the
    resolver and its answer replaces the rules' answer.
    """

    kinds: list[DocKind] = []
    for doc in submission.documents:
        if force_llm and doc.type and doc.type.strip():
            kinds.append(resolver.resolve_title(doc.type))
            continue
        result = classify_title(doc.type)
        if result.outcome is TitleOutcome.REQUIRED and result.kind is not None:
            kinds.append(result.kind)
        elif result.outcome is TitleOutcome.UNKNOWN and doc.type and doc.type.strip():
            kinds.append(resolver.resolve_title(doc.type))
        else:
            kinds.append(DocKind.OTHER)
    return kinds


def _documents_present(documents: list[Document]) -> str:
    if not documents:
        return "Documents present: none."
    listed = "; ".join(
        f"documents[{i}] '{doc.type or ''}' dated {doc.date or 'unknown'}"
        for i, doc in enumerate(documents)
    )
    return f"Documents present: {listed}."


def _lab_counts(lab: LabResult) -> bool:
    return lab.status is None or lab.status.strip().lower() in COUNTED_LAB_STATUSES


def _labs_present(labs: list[LabResult]) -> str:
    if not labs:
        return "Labs present: none."
    listed = "; ".join(
        f"labs[{i}] {lab.code or 'unknown'} ({lab.display or 'no display'}) "
        f"effective_at {lab.effective_at or 'unknown'} status {lab.status or 'none'}"
        + ("" if _lab_counts(lab) else " (ignored: status not final/amended/corrected)")
        for i, lab in enumerate(labs)
    )
    return f"Labs present: {listed}."


def _latest_document(documents: list[Document], indexes: list[int]) -> int | None:
    """Most recent of ``indexes`` by document date; None when none has a valid date."""

    found = most_recent(indexes, lambda i: parse_instant(documents[i].date))
    return None if found is None else found[1]


def check_missing_procedure_fields(submission: PatientSubmission) -> list[TriageIssue]:
    """Flag a null or unparseable ``procedure.procedure_date`` and a null ``procedure_risk``."""

    procedure = submission.procedure
    raw_date = procedure.procedure_date if procedure else None
    risk = procedure.procedure_risk if procedure else None
    issues: list[TriageIssue] = []

    if parse_date(raw_date) is None:
        if raw_date is None:
            details = "procedure.procedure_date is null"
        else:
            details = f"procedure.procedure_date is unparseable: {raw_date!r}"
        targets = [
            f"documents[{i}] states '{match.group(0)}'"
            for i, doc in enumerate(submission.documents)
            if doc.text and (match := _TARGET_DATE_RE.search(doc.text))
        ]
        if targets:
            details += (
                "; free-text target for reviewer reference only, not used: "
                + "; ".join(targets)
            )
        issues.append(
            _issue(
                "MISSING_REQUIRED_DATA",
                "Missing procedure date",
                "procedure.procedure_date",
                details,
            )
        )

    if risk is None:
        issues.append(
            _issue(
                "MISSING_REQUIRED_DATA",
                "Missing procedure risk",
                "procedure.procedure_risk",
                "procedure.procedure_risk is null",
            )
        )
    return issues


def check_documentation(
    submission: PatientSubmission,
    procedure_date: date | None,
    kinds: list[DocKind],
    resolver: Resolver,
    *,
    force_llm: bool = False,
    confirm_positives: bool = False,
) -> list[TriageIssue]:
    """Rule 1: a recent H&P (within 30 days) and a signed surgical consent.

    Consent judging: a keyword-NEGATIVE text is final. With
    ``confirm_positives`` (default mode) a keyword-POSITIVE text is sent to the
    resolver and counts as signed only if the resolver agrees; if the resolver
    cannot answer, the keyword answer stands. Without it (offline mode) a
    keyword-POSITIVE text is trusted. UNPLACED texts always go to the resolver.

    ``kinds`` is ``classify_documents`` output, one entry per document. With a
    null procedure date, only the presence checks run. An H&P counts only with
    a valid date. A consent counts as present whatever its date; the most
    recent consent is the one judged (the last listed when none is dated), so
    an older signed consent does not rescue a newer unsigned one. Under
    ``force_llm`` a non-empty consent text goes to the resolver even when the
    keywords placed it.
    """

    documents = submission.documents
    issues: list[TriageIssue] = []

    hp_indexes = [i for i, kind in enumerate(kinds) if kind is DocKind.H_AND_P]
    hp_index = _latest_document(documents, hp_indexes)
    if hp_index is None:
        issues.append(
            _issue(
                "REQUIRED_DOCUMENTATION",
                "History and Physical document missing",
                "documents",
                "No History and Physical document with valid date found. "
                + _documents_present(documents),
            )
        )
    elif procedure_date is not None:
        doc = documents[hp_index]
        hp_date = parse_date(doc.date)
        assert hp_date is not None
        if not within_window(hp_date, procedure_date, H_AND_P_WINDOW_DAYS):
            issues.append(
                _issue(
                    "REQUIRED_DOCUMENTATION",
                    "H&P outside 30-day window",
                    f"documents[{hp_index}]",
                    f"H&P date {doc.date} vs procedure_date {procedure_date.isoformat()} "
                    + _gap_phrase(hp_date, procedure_date, H_AND_P_WINDOW_DAYS),
                )
            )

    consent_indexes = [i for i, kind in enumerate(kinds) if kind is DocKind.CONSENT]
    if not consent_indexes:
        issues.append(
            _issue(
                "REQUIRED_DOCUMENTATION",
                "Signed surgical consent missing",
                "documents",
                "No Surgical Consent document found. " + _documents_present(documents),
            )
        )
        return issues

    index = _latest_document(documents, consent_indexes)
    if index is None:
        index = consent_indexes[-1]
    doc = documents[index]
    judgment = judge_consent_signed(doc.text)
    has_text = bool(doc.text and doc.text.strip())
    keyword_positive = judgment is TextJudgment.POSITIVE
    ask = (
        judgment is TextJudgment.UNPLACED
        or (keyword_positive and confirm_positives)
        or (force_llm and has_text)
    )
    if ask:
        signed = resolver.resolve_consent_signed(doc.text or "", fallback=keyword_positive)
    else:
        signed = keyword_positive
    if not signed:
        if doc.text and doc.text.strip():
            details = "Consent document text does not clearly indicate signed consent: " + doc.text
        else:
            details = (
                "Consent document has no text to indicate signed consent: "
                f"'{doc.type or ''}' dated {doc.date or 'unknown'}"
            )
        if len(consent_indexes) > 1:
            details += (
                f" (most recent of {len(consent_indexes)} consent documents: "
                + ", ".join(f"documents[{i}]" for i in consent_indexes)
                + ")"
            )
        issues.append(
            _issue(
                "REQUIRED_DOCUMENTATION",
                "Surgical consent not clearly signed",
                f"documents[{index}]",
                details,
            )
        )
    return issues


def check_testing(
    submission: PatientSubmission,
    procedure_date: date | None,
) -> list[TriageIssue]:
    """Rule 2: CBC (and CMP for HIGH risk) within the risk-dependent window.

    Skipped entirely when procedure risk is null. With a null procedure date,
    only the presence checks run. Only the most recent counted result per test
    is judged; labs whose status is not final, amended, corrected, or null are
    ignored.
    """

    procedure = submission.procedure
    risk = procedure.procedure_risk if procedure else None
    if risk is None:
        return []

    window, required = TESTING_REQUIREMENTS[risk]
    labs = submission.labs
    issues: list[TriageIssue] = []
    for code in required:
        matching = [
            i
            for i, lab in enumerate(labs)
            if canonical_lab_code(lab.code) == code and _lab_counts(lab)
        ]
        latest = most_recent(matching, lambda i: parse_instant(labs[i].effective_at))
        if latest is None:
            issues.append(
                _issue(
                    "REQUIRED_TESTING",
                    f"{code} missing",
                    "labs",
                    f"No {code} result with valid effective_at found for procedure_risk "
                    f"{risk}. " + _labs_present(labs),
                )
            )
            continue
        if procedure_date is None:
            continue
        index = latest[1]
        lab = labs[index]
        lab_date = parse_date(lab.effective_at)
        assert lab_date is not None
        if not within_window(lab_date, procedure_date, window):
            issues.append(
                _issue(
                    "REQUIRED_TESTING",
                    f"{code} outside {window}-day window for {risk} risk procedure",
                    f"labs[{index}]",
                    f"{code} effective_at {lab.effective_at} vs procedure_date "
                    f"{procedure_date.isoformat()} "
                    + _gap_phrase(lab_date, procedure_date, window),
                )
            )
    return issues


def plan_candidates(
    submission: PatientSubmission, kinds: list[DocKind], drug: str
) -> tuple[list[int], list[int]]:
    """Return (text candidates, title-only candidates) for one anticoagulant.

    ``drug`` is a generic name. Text candidates name the drug (generic or any
    brand) by word or say "anticoag". Title-only candidates are
    title-classified plan documents that are not text candidates.
    """

    drug_re = drug_name_re(drug)
    text_candidates = [
        i
        for i, doc in enumerate(submission.documents)
        if doc.text and (_ANTICOAG_WORD_RE.search(doc.text) or drug_re.search(doc.text))
    ]
    title_only = [
        i
        for i, kind in enumerate(kinds)
        if kind is DocKind.ANTICOAG_PLAN and i not in text_candidates
    ]
    return text_candidates, title_only


def anticoagulant_medications(
    submission: PatientSubmission,
) -> list[tuple[int, str, str, bool | None]]:
    """Return (medication index, generic drug, full name, active) for every
    medication naming an anticoagulant by generic or brand name."""

    found = []
    for i, med in enumerate(submission.medications):
        drug = anticoagulant_generic(med.name)
        if drug is not None:
            found.append((i, drug, med.name or drug, med.active))
    return found


def active_anticoagulants(submission: PatientSubmission) -> list[tuple[int, str, str]]:
    """Return (medication index, generic drug, full name) for active anticoagulants.

    The one definition shared by Rule 3 and the stability script.
    """

    return [(i, drug, name) for i, drug, name, active in anticoagulant_medications(submission) if active]


def check_anticoagulation(
    submission: PatientSubmission,
    kinds: list[DocKind],
    resolver: Resolver,
    *,
    force_llm: bool = False,
) -> list[TriageIssue]:
    """Rule 3, once per active anticoagulant: each needs its own clear plan.

    An anticoagulant with unknown active status raises a missing-data issue
    instead; an inactive one raises nothing.

    Candidates for a drug: documents whose text names that drug by word or
    says "anticoag" (text candidates), then documents whose title classifies
    as a plan (title candidates). Keywords can only rule a plan NEGATIVE or
    leave it UNPLACED (D4); a plan is clear only if the resolver says so. For
    each unmet drug the issue cites its most recent text candidate, else its
    most recent title candidate, else ``documents``. Under ``force_llm`` every
    non-empty candidate goes to the resolver, including rule-NEGATIVE ones.
    """

    issues: list[TriageIssue] = [
        _issue(
            "MISSING_REQUIRED_DATA",
            "Unknown anticoagulant active status",
            f"medications[{i}]",
            f"Medication {name} has active=null; cannot determine if currently taking",
        )
        for i, _drug, name, is_active in anticoagulant_medications(submission)
        if is_active is None
    ]

    documents = submission.documents
    for med_index, drug, full_name in active_anticoagulants(submission):
        text_candidates, title_only = plan_candidates(submission, kinds, drug)

        clear = False
        for i in text_candidates + title_only:
            text = documents[i].text
            judgment = judge_plan_clear(text, drug)
            ask = judgment is TextJudgment.UNPLACED or (
                force_llm and bool(text and text.strip())
            )
            if ask and resolver.resolve_plan_clear(text or "", drug):
                clear = True
                break
        if clear:
            continue

        cited = None
        for group in (text_candidates, title_only):
            if group:
                cited = _latest_document(documents, group)
                if cited is None:
                    cited = group[-1]
                break

        details = (
            f"Active anticoagulant medication present (medications[{med_index}]: "
            f"{full_name}) but no clear perioperative plan document found"
        )
        if cited is None:
            source = "documents"
            details += f". No document mentions {drug} or anticoagulation."
        else:
            source = f"documents[{cited}]"
            doc = documents[cited]
            details += (
                f". documents[{cited}] '{doc.type or ''}' dated {doc.date or 'unknown'}: "
                f"{doc.text or '(no text)'}"
            )
        issues.append(
            _issue(
                "ANTICOAGULATION_MANAGEMENT",
                "Missing perioperative anticoagulation plan",
                source,
                details,
            )
        )
    return issues


# (collection, date field, description) for the unparseable-date check.
_DATED_COLLECTIONS = (
    ("vitals", "date", "Unparseable vital date"),
    ("labs", "effective_at", "Unparseable lab date"),
    ("documents", "date", "Unparseable document date"),
)


def check_unparseable_dates(submission: PatientSubmission) -> list[TriageIssue]:
    """Flag every vital, lab, or document whose date is present but does not parse.

    A null date is not flagged here. An unparseable date (including a blank
    string) is excluded from most-recent selection like a null one, and this
    missing-data issue is what keeps the case from being READY.
    """

    issues: list[TriageIssue] = []
    for collection, field, description in _DATED_COLLECTIONS:
        for i, item in enumerate(getattr(submission, collection)):
            raw = getattr(item, field, None)
            if raw is not None and parse_date(raw) is None:
                issues.append(
                    _issue(
                        "MISSING_REQUIRED_DATA",
                        description,
                        f"{collection}[{i}]",
                        f"{collection}[{i}].{field} is unparseable: {raw!r}",
                    )
                )
    return issues


def check_vitals(submission: PatientSubmission) -> list[TriageIssue]:
    """Rule 4: safety exclusions on the most recent BP and temperature, or missing-vitals issues.

    BP and temperature are judged separately, each on its most recent reading
    by full instant (ties go to the later list index). There is no age limit:
    the policy sets no window for "the time of pre-operative review". An
    exclusion fires on any value over its threshold. When the latest reading
    lacks a needed value and nothing excludes, a missing-data issue is raised.
    """

    vitals = submission.vitals
    issues: list[TriageIssue] = []

    def latest_of(vital_type: str) -> tuple[int, object] | None:
        indexes = [
            i for i, v in enumerate(vitals) if (v.type or "").strip().lower() == vital_type
        ]
        found = most_recent(indexes, lambda i: parse_instant(vitals[i].date))
        return None if found is None else (found[1], vitals[found[1]])

    bp = latest_of("blood_pressure")
    if bp is None:
        issues.append(
            _issue(
                "MISSING_REQUIRED_DATA",
                "Missing latest blood pressure",
                "vitals",
                "No blood_pressure vital with valid date found",
            )
        )
    else:
        index, vital = bp
        systolic = getattr(vital, "systolic", None)
        diastolic = getattr(vital, "diastolic", None)
        exceeds = (systolic is not None and systolic >= SYSTOLIC_LIMIT) or (
            diastolic is not None and diastolic >= DIASTOLIC_LIMIT
        )
        if exceeds:
            issues.append(
                _issue(
                    "ACUTE_SAFETY_EXCLUSION",
                    "Blood pressure meets exclusion threshold",
                    f"vitals[{index}]",
                    f"latest BP systolic={systolic}, diastolic={diastolic}; threshold "
                    f"systolic>={SYSTOLIC_LIMIT} or diastolic>={DIASTOLIC_LIMIT}; "
                    f"recorded {getattr(vital, 'date', None)}",
                )
            )
        elif systolic is None or diastolic is None:
            issues.append(
                _issue(
                    "MISSING_REQUIRED_DATA",
                    "Missing latest blood pressure",
                    f"vitals[{index}]",
                    f"Latest blood_pressure vital (vitals[{index}], recorded "
                    f"{getattr(vital, 'date', None)}) has systolic={systolic}, "
                    f"diastolic={diastolic}",
                )
            )

    temp = latest_of("temperature")
    if temp is None:
        issues.append(
            _issue(
                "MISSING_REQUIRED_DATA",
                "Missing latest temperature",
                "vitals",
                "No temperature vital with valid date found",
            )
        )
    else:
        index, vital = temp
        value_f = getattr(vital, "value_f", None)
        if value_f is None:
            issues.append(
                _issue(
                    "MISSING_REQUIRED_DATA",
                    "Missing latest temperature",
                    f"vitals[{index}]",
                    f"Latest temperature vital (vitals[{index}], recorded "
                    f"{getattr(vital, 'date', None)}) has value_f=None",
                )
            )
        elif value_f > TEMPERATURE_LIMIT_F:
            issues.append(
                _issue(
                    "ACUTE_SAFETY_EXCLUSION",
                    "Temperature exceeds exclusion threshold",
                    f"vitals[{index}]",
                    f"latest temperature value_f={value_f}; threshold is > "
                    f"{TEMPERATURE_LIMIT_F}; recorded {getattr(vital, 'date', None)}",
                )
            )
    return issues
