from __future__ import annotations

import json
from pathlib import Path

import pytest

from preop_triage.settings import ENV_FORCE_LLM, ENV_NO_CACHE, ENV_OFFLINE

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "patients_sample_50.jsonl"


@pytest.fixture(autouse=True)
def clean_preop_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with no PREOP_* flags set, whatever the shell exports."""

    for name in (ENV_OFFLINE, ENV_NO_CACHE, ENV_FORCE_LLM):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def sample_submission() -> dict[str, object]:
    """The first sample case's submission, as the harness passes it (a plain dict)."""

    with DATA_PATH.open(encoding="utf-8") as handle:
        return json.loads(handle.readline())["submission"]
