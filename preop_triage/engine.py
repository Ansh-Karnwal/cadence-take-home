"""Orchestrator: normalize, classify, apply rules, resolve leftovers, assemble.

Decision derivation: any ACUTE_SAFETY_EXCLUSION issue gives NOT_CLEARED;
otherwise any issue gives NEEDS_FOLLOW_UP; no issues gives READY.
"""

from __future__ import annotations

from core import Decision, PatientSubmission, TriageIssue, TriageOutput

from . import rules
from .classify import OfflineResolver, Resolver
from .llm import LLMResolver
from .normalize import parse_date
from .settings import Settings

# Issues are grouped by category in this order. Within a category they keep
# the order the rules emitted them.
CATEGORY_ORDER = (
    "MISSING_REQUIRED_DATA",
    "REQUIRED_DOCUMENTATION",
    "REQUIRED_TESTING",
    "ANTICOAGULATION_MANAGEMENT",
    "ACUTE_SAFETY_EXCLUSION",
)

READY_EXPLANATION = (
    "All required documentation, testing, anticoagulation planning, "
    "and safety checks are satisfied."
)


def evaluate(
    submission: PatientSubmission,
    *,
    model: str,
    settings: Settings,
) -> TriageOutput:
    """Triage one validated submission package."""

    resolver = build_resolver(model=model, settings=settings)
    procedure_date = parse_date(
        submission.procedure.procedure_date if submission.procedure else None
    )

    issues = [
        *rules.check_missing_procedure_fields(submission),
        *rules.check_documentation(submission, procedure_date, resolver),
        *rules.check_testing(submission, procedure_date),
        *rules.check_anticoagulation(submission, resolver),
        *rules.check_vitals(submission),
    ]
    issues.sort(key=lambda issue: CATEGORY_ORDER.index(issue.category))

    return TriageOutput(
        decision=derive_decision(issues),
        issues=issues,
        explanation=build_explanation(issues),
    )


def build_resolver(*, model: str, settings: Settings) -> Resolver:
    """Return the offline resolver when ``settings.offline``, else the LLM resolver."""

    if settings.offline:
        return OfflineResolver()
    return LLMResolver(model=model, settings=settings)


def derive_decision(issues: list[TriageIssue]) -> Decision:
    """Map the assembled issues to a decision."""

    if any(issue.category == "ACUTE_SAFETY_EXCLUSION" for issue in issues):
        return "NOT_CLEARED"
    if issues:
        return "NEEDS_FOLLOW_UP"
    return "READY"


def build_explanation(issues: list[TriageIssue]) -> str:
    """Join issues as ``"CATEGORY: description | CATEGORY: description"``."""

    if not issues:
        return READY_EXPLANATION
    return " | ".join(f"{issue.category}: {issue.description}" for issue in issues)
