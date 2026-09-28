"""The harness contract: run_baseline.py and run_evals.py call
``triage_submission(submission=<dict>, model=<str>)`` and nothing else."""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import core
from core import PatientSubmission, TriageOutput, triage_submission
from preop_triage import engine
from preop_triage.settings import ENV_NO_CACHE, ENV_OFFLINE, Settings


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Replace engine.evaluate with a recorder and return what it was called with."""

    calls: dict[str, object] = {}

    def fake_evaluate(submission, *, model, settings):  # type: ignore[no-untyped-def]
        calls.update(submission=submission, model=model, settings=settings)
        return "sentinel"

    monkeypatch.setattr(engine, "evaluate", fake_evaluate)
    return calls


def test_signature_matches_harness_call_shape() -> None:
    params = inspect.signature(triage_submission).parameters

    assert list(params) == ["submission", "model", "settings"]
    assert params["submission"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params["model"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["model"].default is inspect.Parameter.empty
    assert params["settings"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["settings"].default is None


def test_default_mode_returns_triage_output_without_raising(
    fake_client, sample_submission: dict[str, object]  # type: ignore[no-untyped-def]
) -> None:
    """Default (non-offline) mode uses the LLM resolver; with a fake client it returns."""

    output = triage_submission(submission=sample_submission, model="test-model")

    assert isinstance(output, TriageOutput)
    assert fake_client.calls, "case_00000 has an UNKNOWN title and an UNPLACED plan"
    assert "LLM fallback unavailable" not in output.explanation


def test_default_mode_does_not_raise_when_the_api_is_down(
    sample_submission: dict[str, object],
) -> None:
    # The autouse fixture's client raises on every call.
    output = triage_submission(submission=sample_submission, model="test-model")

    assert isinstance(output, TriageOutput)
    assert "LLM fallback unavailable" in output.explanation


def test_offline_mode_returns_triage_output_for_harness_call_shape(
    monkeypatch: pytest.MonkeyPatch, sample_submission: dict[str, object]
) -> None:
    monkeypatch.setenv(ENV_OFFLINE, "1")

    output = triage_submission(submission=sample_submission, model="test-model")

    assert isinstance(output, TriageOutput)
    assert TriageOutput.model_validate(output.model_dump()) == output


def test_invalid_submission_fails_validation_before_engine(
    captured: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        triage_submission(
            submission={"procedure": {"procedure_risk": "EXTREME"}}, model="test-model"
        )

    assert captured == {}


def test_dict_is_validated_and_model_passed_through(
    captured: dict[str, object], sample_submission: dict[str, object]
) -> None:
    result = triage_submission(submission=sample_submission, model="test-model")

    assert result == "sentinel"
    assert isinstance(captured["submission"], PatientSubmission)
    assert captured["submission"] == PatientSubmission.model_validate(sample_submission)
    assert captured["model"] == "test-model"


def test_validated_submission_is_accepted(
    captured: dict[str, object], sample_submission: dict[str, object]
) -> None:
    submission = PatientSubmission.model_validate(sample_submission)

    triage_submission(submission, model="test-model")

    assert captured["submission"] == submission


def test_settings_resolved_from_env_when_omitted(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, object],
    sample_submission: dict[str, object],
) -> None:
    monkeypatch.setenv(ENV_OFFLINE, "true")

    triage_submission(submission=sample_submission, model="test-model")

    assert captured["settings"] == Settings(offline=True)


def test_settings_keyword_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, object],
    sample_submission: dict[str, object],
) -> None:
    monkeypatch.setenv(ENV_OFFLINE, "1")
    monkeypatch.setenv(ENV_NO_CACHE, "1")
    explicit = Settings(offline=False, no_cache=False, force_llm=True)

    triage_submission(submission=sample_submission, model="test-model", settings=explicit)

    assert captured["settings"] is explicit


def test_core_import_does_not_load_package_or_openai() -> None:
    """Importing core must not pull in preop_triage (circular import) or openai."""

    root = Path(core.__file__).resolve().parent
    check = (
        "import sys, core; "
        "loaded = [m for m in ('preop_triage', 'openai') if m in sys.modules]; "
        "assert not loaded, loaded"
    )

    result = subprocess.run(
        [sys.executable, "-c", check], cwd=root, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
