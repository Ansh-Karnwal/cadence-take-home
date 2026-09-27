"""Narrowly scoped LLM fallback (DESIGN.md D1, D2, section 6).

The LLM only labels UNKNOWN titles and UNPLACED texts. It returns an enum
through a strict JSON schema at temperature 0 and never writes issues. The
model ID always comes from the caller; none is hardcoded here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .classify import DocKind
from .settings import Settings

TextQuestion = Literal["consent_signed", "plan_clear"]


class TitleLabel(BaseModel):
    """Strict response schema for title classification."""

    model_config = ConfigDict(extra="forbid")

    kind: DocKind


class TextLabel(BaseModel):
    """Strict response schema for a yes/no text judgment."""

    model_config = ConfigDict(extra="forbid")

    answer: bool


class FallbackCache:
    """In-memory cache of LLM answers keyed on model, question, and input text."""

    def get(self, model: str, question: str, text: str) -> object | None:
        """Return a cached answer, or None on a miss."""

        raise NotImplementedError

    def put(self, model: str, question: str, text: str, answer: object) -> None:
        """Store an answer."""

        raise NotImplementedError


class LLMResolver:
    """``classify.Resolver`` backed by the OpenAI Responses API."""

    def __init__(
        self,
        *,
        model: str,
        settings: Settings,
        cache: FallbackCache | None = None,
        client: object | None = None,
    ) -> None:
        raise NotImplementedError

    def resolve_title(self, title: str) -> DocKind:
        """Ask the model which document kind ``title`` names."""

        raise NotImplementedError

    def resolve_consent_signed(self, text: str) -> bool:
        """Ask the model whether consent ``text`` indicates a signature."""

        raise NotImplementedError

    def resolve_plan_clear(self, text: str) -> bool:
        """Ask the model whether plan ``text`` gives clear hold and resume guidance."""

        raise NotImplementedError
