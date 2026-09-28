"""Narrowly scoped LLM fallback (DESIGN.md D1, D2, D4, section 6).

The LLM only labels UNKNOWN titles and UNPLACED texts (every item under
``force_llm``). It returns a label through a strict JSON schema at
temperature 0 and never writes descriptions, details, dates, issues, or
decisions. The model ID always comes from the caller; none is named here.

State is process-scoped and in memory only:

- one ``FallbackCache`` at module level, keyed on (model, question, text);
- one ``LLMResolver`` per (model, settings) at module level, so the harness's
  ten determinism calls in one process share the resolver and the cache.

On an API error the resolver retries twice with a short backoff, then returns
the offline answer for that item (OTHER, False, False), writes one line to
stderr, and counts the failure so the engine can note it. It never raises.
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from .classify import DocKind
from .settings import Settings

# ---------------------------------------------------------------------------
# Prompts. Each ends with the same instruction; the model returns a label only.
# ---------------------------------------------------------------------------

_TEXT_ALONE = "Answer from the text alone; do not infer."

TITLE_PROMPT = f"""You classify the title of a clinical document in a pre-operative scheduling package.
You see only the title. Return exactly one label.

The three required document kinds, with example titles:
- H_AND_P: a History and Physical. Examples: "History and Physical", "Pre-op H&P [PDF]".
- CONSENT: a surgical consent. Examples: "Surgical Consent", "Procedure Consent Form".
- ANTICOAG_PLAN: a perioperative anticoagulation or medication plan. Examples: "Perioperative Medication Plan", "Anticoag Plan (scanned)".

Everything else is OTHER.

{_TEXT_ALONE}"""

CONSENT_PROMPT = f"""You read the text of a surgical consent document and decide whether the patient has signed it.

Return true only if the text states that the patient has signed the consent.
Any statement that signing is pending, missing, refused, or withdrawn means false.
If the text does not say, return false.

{_TEXT_ALONE}"""

# Rule 3 bullets, verbatim from the policy appendix of the take-home PDF.
RULE_3_BULLETS = """- A documented perioperative anticoagulation management plan is required prior to scheduling.
- The plan must clearly describe how the medication will be managed before and after the procedure."""

PLAN_PROMPT = f"""You read a clinical document and decide whether it contains a clear perioperative management plan for one named anticoagulant.

The scheduling policy says, for patients currently taking anticoagulant medication:
{RULE_3_BULLETS}

Return true only if the text explicitly states how this drug is managed before the procedure and how it is managed after the procedure.
Deferral to another clinician, pending details, or management of a different drug means false.

{_TEXT_ALONE}"""

# ---------------------------------------------------------------------------
# Response schemas. Hand-written flat JSON schemas for strict mode, mirroring
# the pydantic models that parse the reply. Tests check they cannot drift.
# ---------------------------------------------------------------------------


class TitleLabel(BaseModel):
    """Strict response schema for title classification."""

    model_config = ConfigDict(extra="forbid")

    kind: DocKind


class TextLabel(BaseModel):
    """Strict response schema for a yes/no text judgment."""

    model_config = ConfigDict(extra="forbid")

    answer: bool


TITLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"kind": {"type": "string", "enum": [kind.value for kind in DocKind]}},
    "required": ["kind"],
    "additionalProperties": False,
}
TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "boolean"}},
    "required": ["answer"],
    "additionalProperties": False,
}

_SCHEMAS: dict[type[BaseModel], tuple[str, dict[str, Any]]] = {
    TitleLabel: ("title_label", TITLE_SCHEMA),
    TextLabel: ("text_label", TEXT_SCHEMA),
}

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class FallbackCache:
    """In-memory cache of LLM answers keyed on (model, question, text).

    ``question`` is ``title``, ``consent``, or ``plan:<drug>``. Only successful
    answers are stored, so an outage never sticks.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str, str], object] = {}
        self.hits = 0
        self.misses = 0
        self.misses_by_question: Counter[str] = Counter()

    def get(self, model: str, question: str, text: str) -> object | None:
        """Return a cached answer, or None on a miss."""

        key = (model, question, text)
        if key in self._store:
            self.hits += 1
            return self._store[key]
        self.misses += 1
        self.misses_by_question[question.split(":", 1)[0]] += 1
        return None

    def put(self, model: str, question: str, text: str, answer: object) -> None:
        """Store an answer."""

        self._store[(model, question, text)] = answer

    def __len__(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()
        self.hits = 0
        self.misses = 0
        self.misses_by_question.clear()


_CACHE = FallbackCache()


def cache_stats() -> dict[str, Any]:
    """Hits, misses, and size of the shared cache, plus misses per question type."""

    return {
        "hits": _CACHE.hits,
        "misses": _CACHE.misses,
        "size": len(_CACHE),
        "misses_by_question": dict(_CACHE.misses_by_question),
    }


# ---------------------------------------------------------------------------
# Client and resolver
# ---------------------------------------------------------------------------

MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 0.5  # multiplied by the retry number


def _default_client() -> Any:
    from openai import OpenAI  # lazy: importing the package never imports openai

    return OpenAI()


# Tests replace this factory with one that returns a fake client.
_make_client: Callable[[], Any] = _default_client


class ResolverError(Exception):
    """The OpenAI client could not be created."""


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
        self.model = model
        self.settings = settings
        self.cache = _CACHE if cache is None else cache
        self._client = client
        self._client_error: str | None = None
        # None until the first call; False once the model has rejected it.
        self.temperature_accepted: bool | None = None
        self.api_calls = 0
        self.failures = 0

    # -- Resolver protocol ---------------------------------------------------

    def resolve_title(self, title: str) -> DocKind:
        """Ask the model which document kind ``title`` names. Input is the title only."""

        label = self._ask("title", title, TITLE_PROMPT, title, TitleLabel)
        return DocKind.OTHER if label is None else label.kind  # type: ignore[attr-defined]

    def resolve_consent_signed(self, text: str) -> bool:
        """Ask the model whether consent ``text`` states the patient has signed."""

        label = self._ask("consent", text, CONSENT_PROMPT, text, TextLabel)
        return False if label is None else label.answer  # type: ignore[attr-defined]

    def resolve_plan_clear(self, text: str, drug: str) -> bool:
        """Ask the model whether plan ``text`` clearly describes how ``drug`` is
        managed before and after the procedure."""

        user_input = f"Anticoagulant: {drug}\n\nDocument text:\n{text}"
        label = self._ask(f"plan:{drug}", text, PLAN_PROMPT, user_input, TextLabel)
        return False if label is None else label.answer  # type: ignore[attr-defined]

    # -- internals -------------------------------------------------------------

    def _ask(
        self,
        question: str,
        cache_text: str,
        instructions: str,
        user_input: str,
        label_model: type[BaseModel],
    ) -> BaseModel | None:
        """Return the parsed label, or None after a logged failure."""

        use_cache = not self.settings.no_cache
        if use_cache:
            cached = self.cache.get(self.model, question, cache_text)
            if cached is not None:
                return cached  # type: ignore[return-value]
        try:
            reply = self._call(instructions, user_input, label_model)
            label = label_model.model_validate_json(reply)
        except Exception as exc:  # noqa: BLE001 - any failure degrades to offline
            self.failures += 1
            print(
                f"preop_triage: LLM fallback failed for {question} {cache_text[:80]!r}: "
                f"{type(exc).__name__}: {exc}; using the offline answer",
                file=sys.stderr,
            )
            return None
        if use_cache:
            self.cache.put(self.model, question, cache_text, label)
        return label

    def _client_or_raise(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_error is not None:
            raise ResolverError(self._client_error)
        try:
            self._client = _make_client()
        except Exception as exc:  # noqa: BLE001
            # Not transient (for example, no API key): recorded once, never retried.
            self._client_error = f"client unavailable: {type(exc).__name__}: {exc}"
            raise ResolverError(self._client_error) from exc
        return self._client

    def _call(self, instructions: str, user_input: str, label_model: type[BaseModel]) -> str:
        client = self._client_or_raise()
        schema_name, schema = _SCHEMAS[label_model]
        retries = 0
        temperature_retried = False
        while True:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "instructions": instructions,
                "input": user_input,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    }
                },
            }
            send_temperature = self.temperature_accepted is not False
            if send_temperature:
                kwargs["temperature"] = 0
            self.api_calls += 1
            try:
                response = client.responses.create(**kwargs)
            except Exception as exc:  # noqa: BLE001
                if send_temperature and not temperature_retried and _rejects_temperature(exc):
                    # Separate budget of one: stop sending temperature for this resolver.
                    self.temperature_accepted = False
                    temperature_retried = True
                    continue
                if retries >= MAX_RETRIES:
                    raise
                retries += 1
                time.sleep(RETRY_BACKOFF_SECONDS * retries)
                continue
            if send_temperature:
                self.temperature_accepted = True
            return response.output_text


def _rejects_temperature(exc: Exception) -> bool:
    if getattr(exc, "param", None) == "temperature":
        return True
    return "temperature" in str(exc).lower()


# ---------------------------------------------------------------------------
# Module-level registry
# ---------------------------------------------------------------------------

_RESOLVERS: dict[tuple[str, Settings], LLMResolver] = {}


def get_resolver(model: str, settings: Settings) -> LLMResolver:
    """Return the process-wide resolver for ``(model, settings)``, creating it once."""

    key = (model, settings)
    resolver = _RESOLVERS.get(key)
    if resolver is None:
        resolver = LLMResolver(model=model, settings=settings)
        _RESOLVERS[key] = resolver
    return resolver


def reset_state() -> None:
    """Clear the shared cache and resolver registry (tests only)."""

    _CACHE.clear()
    _RESOLVERS.clear()
