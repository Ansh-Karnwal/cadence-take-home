"""Title classification and keyword text judgments (DESIGN.md D2, D3, D4).

Rules decide whenever they match. Only ``UNKNOWN`` titles and ``UNPLACED``
texts are handed to a ``Resolver``: the LLM fallback in default mode, or the
offline resolver, which treats UNKNOWN as not-required and UNPLACED as negative.

Matching is strict by construction. There is no fuzzy, edit-distance, or
similarity matching: a misspelled title such as "History & Phsyical"
(case_00002) falls through to UNKNOWN.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .normalize import normalize_title


class DocKind(str, Enum):
    """What a document is, for the purposes of the policy."""

    H_AND_P = "H_AND_P"
    CONSENT = "CONSENT"
    ANTICOAG_PLAN = "ANTICOAG_PLAN"
    OTHER = "OTHER"


class TitleOutcome(str, Enum):
    """Result of matching a normalized title against the explicit pattern list."""

    REQUIRED = "REQUIRED"
    KNOWN_OTHER = "KNOWN_OTHER"
    UNKNOWN = "UNKNOWN"


class TextJudgment(str, Enum):
    """Result of a keyword judgment on a document's free text."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    UNPLACED = "UNPLACED"


@dataclass(frozen=True)
class TitleClassification:
    """A title's outcome, and its document kind when the outcome is REQUIRED."""

    outcome: TitleOutcome
    kind: DocKind | None = None


class Resolver(Protocol):
    """Decides what the rules could not place. Implemented by offline and LLM resolvers."""

    def resolve_title(self, title: str) -> DocKind:
        """Return the document kind for an UNKNOWN title."""
        ...

    def resolve_consent_signed(self, text: str) -> bool:
        """Return True when an UNPLACED consent text indicates a signed consent."""
        ...

    def resolve_plan_clear(self, text: str) -> bool:
        """Return True when an UNPLACED plan text gives clear hold and resume guidance."""
        ...


# ---------------------------------------------------------------------------
# Title patterns (D3). Matched against the normalized core only.
# ---------------------------------------------------------------------------

# H&P cores, with the first case using each: "h&p" (case_00001), "h+p"
# (case_00014), "h/p" (case_00015), "h and p" (case_00001), "history & physical"
# (case_00007), "history/physical" (case_00007), "history and physical"
# (case_00000), "hist & phys" (case_00016), "hx & physical" (case_00005).
_HP_CORES = (
    "h&p",
    "h+p",
    "h/p",
    "h and p",
    "history & physical",
    "history/physical",
    "history and physical",
    "hist & phys",
    "hx & physical",
)
# Optional words before the core. First cases: admission (case_00011), clinic
# (case_00022), consult (case_00014), initial (case_00019), outpatient
# (case_00021), hospitalist (case_00037), surgical (case_00020), pre-anesthesia
# (case_00010), preoperative (case_00019).
_HP_QUALIFIERS = (
    "admission",
    "clinic",
    "consult",
    "initial",
    "outpatient",
    "hospitalist",
    "surgical",
    "pre-anesthesia",
    "preoperative",
)
# Optional words after the core, repeatable ("History & Physical Exam (H&P)").
# First cases: (h&p) (case_00005), exam (case_00002), examination (case_00002),
# note (case_00001), update (case_00023).
_HP_FOLLOWERS = ("(h&p)", "exam", "examination", "note", "update")


def _alternation(options: tuple[str, ...]) -> str:
    return "|".join(re.escape(option) for option in options)


_HP_RE = re.compile(
    rf"^(?:(?:{_alternation(_HP_QUALIFIERS)}) )?"
    rf"(?:{_alternation(_HP_CORES)})"
    rf"(?: (?:{_alternation(_HP_FOLLOWERS)}))*$"
)

# Any title containing the word "consent" is a consent document. Includes
# "Consent Counseling Note" (case_00044 flags one, case_00012 accepts one) and
# "Pre-op Consent Discussion" (case_00003 accepts one).
_CONSENT_RE = re.compile(r"\bconsent\b")

# Anticoagulation plan cores. "perioperative medication plan" (case_00000),
# "perioperative medication review" (case_00026), "anticoag plan" (case_00004),
# "cardiology progress note - anticoag" (case_00021).
_ANTICOAG_PLAN_CORES = frozenset(
    {
        "perioperative medication plan",
        "perioperative medication review",
        "anticoag plan",
        "cardiology progress note - anticoag",
    }
)

# Known non-required documents; never sent to the LLM. "anesthesia
# pre-assessment" (case_00001), "clinic follow-up note" (case_00002),
# "nursing intake" (case_00000).
_KNOWN_OTHER_CORES = frozenset(
    {"anesthesia pre-assessment", "clinic follow-up note", "nursing intake"}
)


def classify_title(title: str | None) -> TitleClassification:
    """Classify a raw document title as REQUIRED (with kind), KNOWN_OTHER, or UNKNOWN.

    An empty or missing title is UNKNOWN; callers must not send it to a
    resolver (see ``rules``).
    """

    core = normalize_title(title)
    if not core:
        return TitleClassification(TitleOutcome.UNKNOWN)
    if _HP_RE.match(core):
        return TitleClassification(TitleOutcome.REQUIRED, DocKind.H_AND_P)
    if _CONSENT_RE.search(core):
        return TitleClassification(TitleOutcome.REQUIRED, DocKind.CONSENT)
    if core in _ANTICOAG_PLAN_CORES:
        return TitleClassification(TitleOutcome.REQUIRED, DocKind.ANTICOAG_PLAN)
    if core in _KNOWN_OTHER_CORES:
        return TitleClassification(TitleOutcome.KNOWN_OTHER)
    return TitleClassification(TitleOutcome.UNKNOWN)


# ---------------------------------------------------------------------------
# Text keyword sets (D4). Word-boundary matching, case-insensitive.
# Any negative hit wins over any positive hit.
# ---------------------------------------------------------------------------

CONSENT_NEGATIVE = (
    "unsigned",  # case_00008, case_00017, case_00044
    "awaiting",  # case_00008: "Consent documented but unsigned; awaiting patient signature."
    "not yet on file",  # case_00044: "Unsigned consent noted; signature not yet on file."
    "requested signature",  # case_00017: "...provider requested signature before scheduling."
)
CONSENT_POSITIVE = (
    "signed",  # case_00000: "Electronic consent obtained and signed by patient for procedure."
    "signature on file",  # case_00003: "Consent obtained; signature on file."
)

PLAN_NEGATIVE = (
    "no clear",  # case_00004: "...no clear hold/resume guidance documented."
    "not yet",  # case_00006, case_00021, case_00026: "...details not yet documented."
    "to be finalized",  # case_00038: "...perioperative management plan to be finalized."
    "pending",  # case_00042: "...final plan pending specialist input."
)
# A clear plan needs both hold and resume language, because Rule 3 requires
# management "before and after the procedure". No sample case has a clear plan;
# these sets come from the policy wording and are exercised by synthetic tests.
# "plan" alone is never positive (case_00038 contains "plan").
PLAN_HOLD = ("hold", "held", "stop", "discontinue")
PLAN_RESUME = ("resume", "restart")


def _phrase_re(phrases: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(rf"\b(?:{_alternation(phrases)})\b", re.IGNORECASE)


_CONSENT_NEGATIVE_RE = _phrase_re(CONSENT_NEGATIVE)
_CONSENT_POSITIVE_RE = _phrase_re(CONSENT_POSITIVE)
_PLAN_NEGATIVE_RE = _phrase_re(PLAN_NEGATIVE)
_PLAN_HOLD_RE = _phrase_re(PLAN_HOLD)
_PLAN_RESUME_RE = _phrase_re(PLAN_RESUME)


def judge_consent_signed(text: str | None) -> TextJudgment:
    """Judge whether consent text indicates a signature. Negative keywords win.

    Empty or missing text is NEGATIVE: nothing indicates a signature, and there
    is nothing for a resolver to read.
    """

    if not text or not text.strip():
        return TextJudgment.NEGATIVE
    if _CONSENT_NEGATIVE_RE.search(text):
        return TextJudgment.NEGATIVE
    if _CONSENT_POSITIVE_RE.search(text):
        return TextJudgment.POSITIVE
    return TextJudgment.UNPLACED


def judge_plan_clear(text: str | None) -> TextJudgment:
    """Judge whether plan text gives both hold and resume guidance. Negative keywords win.

    Empty or missing text is NEGATIVE.
    """

    if not text or not text.strip():
        return TextJudgment.NEGATIVE
    if _PLAN_NEGATIVE_RE.search(text):
        return TextJudgment.NEGATIVE
    if _PLAN_HOLD_RE.search(text) and _PLAN_RESUME_RE.search(text):
        return TextJudgment.POSITIVE
    return TextJudgment.UNPLACED


class OfflineResolver:
    """Resolver used when the LLM is disabled: UNKNOWN is OTHER, UNPLACED is negative."""

    def resolve_title(self, title: str) -> DocKind:
        return DocKind.OTHER

    def resolve_consent_signed(self, text: str) -> bool:
        return False

    def resolve_plan_clear(self, text: str) -> bool:
        return False
