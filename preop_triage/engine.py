"""Orchestrator: normalize, classify, apply rules, resolve leftovers, assemble.

Decision derivation: any ACUTE_SAFETY_EXCLUSION issue gives NOT_CLEARED;
otherwise any issue gives NEEDS_FOLLOW_UP; no issues gives READY.
"""

from __future__ import annotations

from core import Decision, PatientSubmission, TriageIssue, TriageOutput

from .classify import Resolver
from .settings import Settings


def evaluate(
    submission: PatientSubmission,
    *,
    model: str,
    settings: Settings,
) -> TriageOutput:
    """Triage one validated submission package."""

    raise NotImplementedError


def build_resolver(*, model: str, settings: Settings) -> Resolver:
    """Return the offline resolver when ``settings.offline``, else the LLM resolver."""

    raise NotImplementedError


def derive_decision(issues: list[TriageIssue]) -> Decision:
    """Map the assembled issues to a decision."""

    raise NotImplementedError


def build_explanation(issues: list[TriageIssue]) -> str:
    """Join issues as ``"CATEGORY: description | CATEGORY: description"``."""

    raise NotImplementedError
