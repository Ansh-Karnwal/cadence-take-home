"""Missing-data gates and policy rules 1 through 4 (DESIGN.md section 4).

Each function returns the issues it raises, in the fixed order the engine
assembles them. Rules write every issue themselves; a ``Resolver`` only
answers the title and text questions the keyword layer left open.
"""

from __future__ import annotations

from datetime import date

from core import PatientSubmission, TriageIssue

from .classify import Resolver


def check_missing_procedure_fields(submission: PatientSubmission) -> list[TriageIssue]:
    """Flag a null ``procedure.procedure_date`` or ``procedure.procedure_risk``."""

    raise NotImplementedError


def check_documentation(
    submission: PatientSubmission,
    procedure_date: date | None,
    resolver: Resolver,
) -> list[TriageIssue]:
    """Rule 1: a recent H&P (within 30 days) and a signed surgical consent.

    With a null procedure date, only the presence checks run.
    """

    raise NotImplementedError


def check_testing(
    submission: PatientSubmission,
    procedure_date: date | None,
) -> list[TriageIssue]:
    """Rule 2: CBC (and CMP for HIGH risk) within the risk-dependent window.

    Skipped entirely when procedure risk is null. With a null procedure date,
    only the presence checks run.
    """

    raise NotImplementedError


def check_anticoagulation(
    submission: PatientSubmission,
    resolver: Resolver,
) -> list[TriageIssue]:
    """Rule 3: an active anticoagulant needs a clear perioperative plan.

    An anticoagulant with unknown active status raises a missing-data issue
    instead.
    """

    raise NotImplementedError


def check_vitals(submission: PatientSubmission) -> list[TriageIssue]:
    """Rule 4: safety exclusions on the most recent BP and temperature, or missing-vitals issues."""

    raise NotImplementedError
