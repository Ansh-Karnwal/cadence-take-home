"""Title classification and keyword text judgments (DESIGN.md D2, D3, D4).

Rules decide whenever they match. Only ``UNKNOWN`` titles and ``UNPLACED``
texts are handed to a ``Resolver``: the LLM fallback in default mode, or the
offline resolver, which treats UNKNOWN as not-required and UNPLACED as negative.

Plan clarity is never decided positive by keywords (D4, checkpoint 1): the
rules return NEGATIVE or UNPLACED, and only a resolver can say a plan is clear.

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

    def resolve_plan_clear(self, text: str, drug: str) -> bool:
        """Return True when an UNPLACED plan text clearly describes how ``drug`` is
        managed before and after the procedure. ``drug`` is the lowercase generic name."""
        ...


# ---------------------------------------------------------------------------
# Title patterns (D3). Matched against the normalized core only.
# ---------------------------------------------------------------------------

# H&P cores, with the first case using each: "h&p" (case_00001), "h+p"
# (case_00014), "h/p" (case_00015), "h and p" (case_00001), "history & physical"
# (case_00007), "history/physical" (case_00007), "history and physical"
# (case_00000), "hist & phys" (case_00016), "hx & physical" (case_00005).
# "h & p" is rewritten to "h&p" by normalize_title.
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
# Optional word before the core. First cases: admission (case_00011), clinic
# (case_00022), consult (case_00014), initial (case_00019), outpatient
# (case_00021), hospitalist (case_00037), surgical (case_00020), pre-anesthesia
# (case_00010), preoperative (case_00019). "interval" is not in the sample;
# added at checkpoint 1.
_HP_QUALIFIERS = (
    "admission",
    "clinic",
    "consult",
    "initial",
    "interval",
    "outpatient",
    "hospitalist",
    "surgical",
    "pre-anesthesia",
    "preoperative",
)
# Optional words after the core, repeatable ("History & Physical Exam (H&P)").
# First cases: (h&p) (case_00005), exam (case_00002), examination (case_00002),
# note (case_00001), update (case_00023). "addendum" is not in the sample;
# added at checkpoint 1.
_HP_FOLLOWERS = ("(h&p)", "exam", "examination", "note", "update", "addendum")


def _alternation(options: tuple[str, ...]) -> str:
    return "|".join(re.escape(option) for option in options)


_HP_RE = re.compile(
    rf"^(?:(?:{_alternation(_HP_QUALIFIERS)}) )?"
    rf"(?:{_alternation(_HP_CORES)})"
    rf"(?: (?:{_alternation(_HP_FOLLOWERS)}))*$"
)

# A title containing the word "consent" is a surgical consent. Includes
# "Consent Counseling Note" (case_00044 flags one, case_00012 accepts one) and
# "Pre-op Consent Discussion" (case_00003 accepts one).
_CONSENT_RE = re.compile(r"\bconsent\b")
# ...unless it names another kind of consent, or a consent that no longer
# stands; then it is KNOWN_OTHER (checkpoint 1, no sample case). Assumption:
# a consent that names no other kind is the surgical one.
CONSENT_TITLE_EXCLUSIONS = (
    "anesthesia",
    "anaesthesia",
    "research",
    "blood",
    "transfusion",
    "photo",
    "withdrawn",
    "refused",
    "revoked",
)
# Matched at word start, so "photo" also covers "photography".
_CONSENT_EXCLUSION_RE = re.compile(rf"\b(?:{_alternation(CONSENT_TITLE_EXCLUSIONS)})")

# Anticoagulation plan titles: any core containing "anticoag" ("anticoag plan",
# case_00004; "cardiology progress note - anticoag", case_00021) or starting
# with "perioperative medication" ("... plan", case_00000; "... review",
# case_00026).
_ANTICOAG_TITLE_RE = re.compile(r"anticoag|^perioperative medication\b")

# Known non-required documents; never sent to the LLM. "anesthesia
# pre-assessment" (case_00001), "clinic follow-up note" (case_00002),
# "nursing intake" (case_00000).
_KNOWN_OTHER_CORES = frozenset(
    {"anesthesia pre-assessment", "clinic follow-up note", "nursing intake"}
)


def classify_title(title: str | None) -> TitleClassification:
    """Classify a raw document title as REQUIRED (with kind), KNOWN_OTHER, or UNKNOWN.

    An empty or missing title is UNKNOWN; callers must not send it to a
    resolver (see ``rules.classify_documents``).
    """

    core = normalize_title(title)
    if not core:
        return TitleClassification(TitleOutcome.UNKNOWN)
    if _HP_RE.match(core):
        return TitleClassification(TitleOutcome.REQUIRED, DocKind.H_AND_P)
    if _CONSENT_RE.search(core):
        if _CONSENT_EXCLUSION_RE.search(core):
            return TitleClassification(TitleOutcome.KNOWN_OTHER)
        return TitleClassification(TitleOutcome.REQUIRED, DocKind.CONSENT)
    if _ANTICOAG_TITLE_RE.search(core):
        return TitleClassification(TitleOutcome.REQUIRED, DocKind.ANTICOAG_PLAN)
    if core in _KNOWN_OTHER_CORES:
        return TitleClassification(TitleOutcome.KNOWN_OTHER)
    return TitleClassification(TitleOutcome.UNKNOWN)


# ---------------------------------------------------------------------------
# Text judgments (D4). Case-insensitive, word-boundary matching, per clause.
# Clauses are split on ".", ";", and ":". A negative clause always wins.
# ---------------------------------------------------------------------------

_CLAUSE_SPLIT_RE = re.compile(r"[.;:]")

# Consent. Sample negatives all contain "unsigned" (case_00008, case_00017,
# case_00044); the other tokens are checkpoint 1 decisions.
CONSENT_SIGN_WORDS = ("sign", "signed", "signature")
CONSENT_NEGATION_TOKENS = (
    "not",  # case_00044: "signature not yet on file"
    "no",
    "never",
    "unsigned",  # case_00008, case_00017, case_00044
    "missing",
    "pending",
    "awaiting",  # case_00008: "awaiting patient signature"
    "to be",
    "will be",
    "refused",
    "declined",
    "withdrawn",
)
CONSENT_BARE_NEGATIVE = ("unsigned",)
CONSENT_POSITIVE = (
    "signed",  # case_00000: "Electronic consent obtained and signed by patient for procedure."
    "signature on file",  # case_00003: "Consent obtained; signature on file."
)

# Plan clarity. Negative tokens only; there is no positive set (D4).
PLAN_NEGATIVE = (
    "no clear",  # case_00004: "...no clear hold/resume guidance documented."
    "not yet",  # checkpoint 1 keeps it; the sample "not yet" clauses (cases 6, 21, 26) have no anchor
    "to be finalized",  # case_00038: "...perioperative management plan to be finalized."
    "pending",  # case_00042: "...final plan pending specialist input."
    "unclear",
    "whether",  # verifier probe: "Cardiology to advise whether to hold or resume apixaban."
    "awaiting",
    "tbd",
)
# A plan negative counts only in a clause that also names the drug or one of these.
# "management", "perioperative", "peri-op", "periop" added after checkpoint 1 so
# the "perioperative management details not yet documented" texts (cases 6, 21,
# 26) are rule-decided NEGATIVE.
PLAN_ANCHORS = (
    "anticoag",
    "hold",
    "resume",
    "bridge",
    "plan",
    "management",
    "perioperative",
    "peri-op",
    "periop",
)


def _phrase_re(phrases: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(rf"\b(?:{_alternation(phrases)})\b", re.IGNORECASE)


_SIGN_WORD_RE = _phrase_re(CONSENT_SIGN_WORDS)
_CONSENT_NEGATION_RE = _phrase_re(CONSENT_NEGATION_TOKENS)
_CONSENT_BARE_NEGATIVE_RE = _phrase_re(CONSENT_BARE_NEGATIVE)
_CONSENT_POSITIVE_RE = _phrase_re(CONSENT_POSITIVE)
_PLAN_NEGATIVE_RE = _phrase_re(PLAN_NEGATIVE)
# "anticoag" is a word prefix (anticoagulant, anticoagulation); the rest are words.
_PLAN_ANCHOR_RE = re.compile(
    r"\banticoag\w*|\b(?:" + _alternation(PLAN_ANCHORS[1:]) + r")\b", re.IGNORECASE
)


def _clauses(text: str) -> list[str]:
    return [clause for clause in _CLAUSE_SPLIT_RE.split(text) if clause.strip()]


def judge_consent_signed(text: str | None) -> TextJudgment:
    """Judge whether consent text indicates a signature, clause by clause.

    A clause is NEGATIVE if it has a sign-word and a negation token, or says
    "unsigned". A clause is POSITIVE if it has "signed" or "signature on file"
    and no negation token. Any NEGATIVE clause wins; else any POSITIVE clause;
    else UNPLACED. Empty or missing text is NEGATIVE: nothing indicates a
    signature, and there is nothing for a resolver to read.
    """

    if not text or not text.strip():
        return TextJudgment.NEGATIVE
    positive = False
    for clause in _clauses(text):
        negated = bool(_CONSENT_NEGATION_RE.search(clause))
        if _CONSENT_BARE_NEGATIVE_RE.search(clause) or (
            negated and _SIGN_WORD_RE.search(clause)
        ):
            return TextJudgment.NEGATIVE
        if not negated and _CONSENT_POSITIVE_RE.search(clause):
            positive = True
    return TextJudgment.POSITIVE if positive else TextJudgment.UNPLACED


def judge_plan_clear(text: str | None, drug: str) -> TextJudgment:
    """Judge plan text for ``drug``: NEGATIVE or UNPLACED, never POSITIVE.

    A negative token counts only in a clause that also names ``drug`` by word
    or one of ``PLAN_ANCHORS``. Empty or missing text is NEGATIVE. Only a
    resolver can find a plan clear.
    """

    if not text or not text.strip():
        return TextJudgment.NEGATIVE
    drug_re = re.compile(rf"\b{re.escape(drug)}\b", re.IGNORECASE)
    for clause in _clauses(text):
        if _PLAN_NEGATIVE_RE.search(clause) and (
            _PLAN_ANCHOR_RE.search(clause) or drug_re.search(clause)
        ):
            return TextJudgment.NEGATIVE
    return TextJudgment.UNPLACED


class OfflineResolver:
    """Resolver used when the LLM is disabled: UNKNOWN is OTHER, UNPLACED is negative.

    Consequence: offline mode never finds an anticoagulation plan clear.
    """

    def resolve_title(self, title: str) -> DocKind:
        return DocKind.OTHER

    def resolve_consent_signed(self, text: str) -> bool:
        return False

    def resolve_plan_clear(self, text: str, drug: str) -> bool:
        return False
