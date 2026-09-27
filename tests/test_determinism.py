"""Offline mode is pure: two runs over all 50 cases give byte-identical JSON."""

from __future__ import annotations

import json

from core import triage_submission
from preop_triage.settings import Settings


def _canonical(dataset: list[dict]) -> list[str]:
    settings = Settings(offline=True)
    return [
        json.dumps(
            triage_submission(
                submission=row["submission"], model="unused", settings=settings
            ).model_dump(),
            sort_keys=True,
            separators=(",", ":"),
        )
        for row in dataset
    ]


def test_two_offline_runs_are_byte_identical(dataset: list[dict]) -> None:
    first = _canonical(dataset)
    second = _canonical(dataset)

    assert len(first) == 50
    assert first == second
