"""Title classification and keyword text judgments (DESIGN.md D2, D3, D4).

Rules decide whenever they match. Only ``UNKNOWN`` titles and ``UNPLACED``
texts are handed to a ``Resolver``: the LLM fallback in default mode, or the
offline resolver, which treats UNKNOWN as not-required and UNPLACED as negative.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


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


def classify_title(title: str | None) -> TitleClassification:
    """Classify a raw document title as REQUIRED (with kind), KNOWN_OTHER, or UNKNOWN."""

    raise NotImplementedError


def judge_consent_signed(text: str | None) -> TextJudgment:
    """Judge whether consent text indicates a signature. Negative keywords win."""

    raise NotImplementedError


def judge_plan_clear(text: str | None) -> TextJudgment:
    """Judge whether plan text gives both hold and resume guidance. Negative keywords win."""

    raise NotImplementedError


class OfflineResolver:
    """Resolver used when the LLM is disabled: UNKNOWN is OTHER, UNPLACED is negative."""

    def resolve_title(self, title: str) -> DocKind:
        raise NotImplementedError

    def resolve_consent_signed(self, text: str) -> bool:
        raise NotImplementedError

    def resolve_plan_clear(self, text: str) -> bool:
        raise NotImplementedError
