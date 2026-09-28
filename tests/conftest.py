from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from preop_triage import llm
from preop_triage.settings import ENV_FORCE_LLM, ENV_NO_CACHE, ENV_OFFLINE

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "patients_sample_50.jsonl"


@pytest.fixture(autouse=True)
def clean_preop_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with no PREOP_* flags set, whatever the shell exports."""

    for name in (ENV_OFFLINE, ENV_NO_CACHE, ENV_FORCE_LLM):
        monkeypatch.delenv(name, raising=False)


class NetworkBlocked(Exception):
    """Raised by the default fake client: no test may reach the OpenAI API."""


class _BlockedResponses:
    def create(self, **kwargs: object) -> object:
        raise NetworkBlocked("tests must inject a fake client; the API is never called")


class _BlockedClient:
    responses = _BlockedResponses()


@pytest.fixture(autouse=True)
def isolated_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh LLM cache and resolver registry per test; no network; no backoff sleeps.

    A test that reaches the resolver without injecting a fake gets
    ``NetworkBlocked`` from every call, which the resolver turns into its
    offline answer plus a failure count, so an accidental API path is visible.
    """

    llm.reset_state()
    monkeypatch.setattr(llm, "_make_client", _BlockedClient)
    monkeypatch.setattr(llm, "RETRY_BACKOFF_SECONDS", 0.0)
    yield
    llm.reset_state()


class FakeClient:
    """Stand-in for ``openai.OpenAI``: records every ``responses.create`` call.

    ``answer`` maps (question, input) to a label: question is "title",
    "consent", or "plan"; the label is a DocKind value for titles and a bool
    otherwise. Defaults: titles OTHER, texts False. ``fail_next`` makes the
    next N calls raise; ``reject_temperature`` makes calls carrying
    ``temperature`` raise the way the API does.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answer: Callable[[str, str], object] = lambda question, _input: (
            "OTHER" if question == "title" else False
        )
        self.fail_next = 0
        self.reject_temperature = False
        self.responses = SimpleNamespace(create=self._create)

    @staticmethod
    def question_of(kwargs: dict[str, Any]) -> str:
        if kwargs["instructions"] == llm.TITLE_PROMPT:
            return "title"
        if kwargs["instructions"] == llm.CONSENT_PROMPT:
            return "consent"
        if kwargs["instructions"] == llm.PLAN_PROMPT:
            return "plan"
        raise AssertionError("unknown prompt")

    def calls_for(self, question: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if self.question_of(c) == question]

    def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.reject_temperature and "temperature" in kwargs:
            error = Exception("Unsupported parameter: 'temperature' is not supported with this model.")
            error.param = "temperature"  # type: ignore[attr-defined]
            raise error
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ConnectionError("simulated API outage")
        question = self.question_of(kwargs)
        label = self.answer(question, kwargs["input"])
        key = "kind" if question == "title" else "answer"
        return SimpleNamespace(output_text=json.dumps({key: label}))


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    """Install a FakeClient as the LLM client for this test and return it."""

    fake = FakeClient()
    monkeypatch.setattr(llm, "_make_client", lambda: fake)
    return fake


@pytest.fixture(scope="session")
def dataset() -> list[dict[str, object]]:
    """All sample rows (case_id, submission, expected_output), in file order."""

    with DATA_PATH.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


@pytest.fixture
def sample_submission(dataset: list[dict[str, object]]) -> dict[str, object]:
    """The first sample case's submission, as the harness passes it (a plain dict)."""

    return copy.deepcopy(dataset[0]["submission"])  # type: ignore[return-value]
