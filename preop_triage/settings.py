"""Runtime mode flags for the triage engine (DESIGN.md D1).

The harness calls ``triage_submission(submission=..., model=...)`` and nothing
else, so modes cannot be harness arguments. They are read from environment
variables, and tests may pass an explicit ``Settings`` instead.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

ENV_OFFLINE = "PREOP_OFFLINE"
ENV_NO_CACHE = "PREOP_NO_CACHE"
ENV_FORCE_LLM = "PREOP_FORCE_LLM"

_TRUTHY = frozenset({"1", "true", "yes"})


def _flag(environ: Mapping[str, str], name: str) -> bool:
    """Return True when ``name`` is set to 1, true, or yes (case-insensitive)."""

    return environ.get(name, "").strip().lower() in _TRUTHY


@dataclass(frozen=True)
class Settings:
    """Engine mode flags.

    Attributes:
        offline: Never call the LLM. UNKNOWN titles resolve to not-required and
            UNPLACED texts resolve to negative.
        no_cache: Bypass the in-memory LLM response cache.
        force_llm: Send inputs to the LLM even when rules could place them
            (used by the stability script to measure rule agreement).
    """

    offline: bool = False
    no_cache: bool = False
    force_llm: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Build settings from ``PREOP_*`` variables in ``environ`` (default ``os.environ``)."""

        env = os.environ if environ is None else environ
        return cls(
            offline=_flag(env, ENV_OFFLINE),
            no_cache=_flag(env, ENV_NO_CACHE),
            force_llm=_flag(env, ENV_FORCE_LLM),
        )
