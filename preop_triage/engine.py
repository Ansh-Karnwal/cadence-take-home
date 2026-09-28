"""Orchestrator: normalize, classify, apply rules, resolve leftovers, assemble.

Decision derivation: any ACUTE_SAFETY_EXCLUSION issue gives NOT_CLEARED;
otherwise any issue gives NEEDS_FOLLOW_UP; no issues gives READY.

Modes (``Settings``):

- ``offline``: the ``OfflineResolver`` answers everything the rules leave
  open; no API call is ever made.
- default: the process-wide ``LLMResolver`` for the model answers UNKNOWN
  titles and UNPLACED texts, and confirms keyword-POSITIVE consents.
  Keywords can add an issue but never remove one.
- ``force_llm``: FOR MEASUREMENT ONLY. Every title, the judged consent text,
  and every plan candidate go to the resolver, and its answer replaces the
  rules' answer. This measures the model against the rules; it is not a
  production mode. Ignored when ``offline`` is set, because the offline
  resolver would turn every recognized document into OTHER.

If the LLM fails during an evaluation, the affected items get their offline
answers and the explanation says so. The engine never raises because of the
API.
"""

from __future__ import annotations

from core import Decision, PatientSubmission, TriageIssue, TriageOutput

from . import rules
from .classify import OfflineResolver, Resolver
from .llm import get_resolver
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
    force = settings.force_llm and not settings.offline
    failures_before = getattr(resolver, "failures", 0)
    procedure_date = parse_date(
        submission.procedure.procedure_date if submission.procedure else None
    )

    # Each title is classified once; Rules 1 and 3 share the result, so the
    # fallback resolver sees each UNKNOWN title at most once per evaluation.
    kinds = rules.classify_documents(submission, resolver, force_llm=force)

    issues = [
        *rules.check_missing_procedure_fields(submission),
        *rules.check_documentation(
            submission,
            procedure_date,
            kinds,
            resolver,
            force_llm=force,
            confirm_positives=not settings.offline,
        ),
        *rules.check_testing(submission, procedure_date),
        *rules.check_anticoagulation(submission, kinds, resolver, force_llm=force),
        *rules.check_vitals(submission),
        *rules.check_unparseable_dates(submission),
    ]
    issues.sort(key=lambda issue: CATEGORY_ORDER.index(issue.category))

    explanation = build_explanation(issues)
    failed = getattr(resolver, "failures", 0) - failures_before
    if failed:
        explanation += (
            f" | NOTE: LLM fallback unavailable for {failed} item(s); "
            "conservative offline answers were used."
        )
    return TriageOutput(
        decision=derive_decision(issues),
        issues=issues,
        explanation=explanation,
    )


def build_resolver(*, model: str, settings: Settings) -> Resolver:
    """Return the offline resolver when ``settings.offline``, else the process-wide
    LLM resolver for ``(model, settings)``, which shares the module-level cache."""

    if settings.offline:
        return OfflineResolver()
    return get_resolver(model, settings)


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
