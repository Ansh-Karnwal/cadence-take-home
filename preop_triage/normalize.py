"""Input normalization (DESIGN.md section 2, "normalize").

Day arithmetic uses each item's own local calendar date: an offset is
dropped, not converted, because the procedure date is a local calendar date.
Ordering ("most recent") uses the full instant: offsets are converted to UTC,
date-only values are midnight UTC, and naive datetimes are taken as UTC. Lab
codes are aliased (``LAB-CBC`` to ``CBC``, ``LAB-CMP`` to ``CMP``). Document titles
are lowercased and stripped of known prefixes and suffixes, repeatedly, before
classification.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time
from typing import TypeVar

T = TypeVar("T")

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Lab code aliases. LAB-CBC: case_00002, case_00012, case_00043. LAB-CMP: case_00019.
_LAB_ALIASES = {"LAB-CBC": "CBC", "LAB-CMP": "CMP"}

# Title prefixes (D3). Each entry either ends in its own separator (":" or "-")
# or must be followed by whitespace, so "preop" never eats "preoperative".
_TITLE_PREFIXES = ("imported:", "preop -", "pre-op", "preop", "scanned")
# Title suffixes (D3). Each must be preceded by whitespace.
_TITLE_SUFFIXES = ("[pdf]", "(scanned)", "(external)", "- signed")

_WHITESPACE_RE = re.compile(r"\s+")
# "h & p" and "H &P" become "h&p" before matching (checkpoint 1).
_H_AND_P_SPACED_RE = re.compile(r"\bh\s*&\s*p\b")


def _parse(value: str | None) -> date | datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if _DATE_ONLY_RE.match(text):
            return date.fromisoformat(text)
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_date(value: str | None) -> date | None:
    """Parse an ISO date or datetime string to its local calendar date.

    Accepts ``YYYY-MM-DD`` and ISO datetimes with ``Z`` or a UTC offset. The
    offset is dropped, not converted: ``2026-01-31T20:00:00-05:00`` is
    2026-01-31. Returns None for a missing or unparseable value.
    """

    parsed = _parse(value)
    if isinstance(parsed, datetime):
        return parsed.date()
    return parsed


def parse_instant(value: str | None) -> datetime | None:
    """Parse an ISO date or datetime string to an aware UTC instant, for ordering.

    Offsets are converted to UTC. A date-only value is midnight UTC. A naive
    datetime is taken as UTC. Returns None for a missing or unparseable value.
    """

    parsed = _parse(value)
    if parsed is None:
        return None
    if not isinstance(parsed, datetime):
        return datetime.combine(parsed, time.min, tzinfo=UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def days_before(item_date: date, procedure_date: date) -> int:
    """Return calendar days from ``item_date`` to ``procedure_date``.

    Negative when the item is dated after the procedure.
    """

    return (procedure_date - item_date).days


def within_window(item_date: date, procedure_date: date, max_days: int) -> bool:
    """Return True when ``0 <= days_before(item_date, procedure_date) <= max_days``."""

    return 0 <= days_before(item_date, procedure_date) <= max_days


def canonical_lab_code(code: str | None) -> str | None:
    """Map a raw lab code to its canonical form (``CBC``, ``CMP``) or None.

    Matching is case-insensitive. Unaliased codes pass through uppercased.
    """

    if not isinstance(code, str):
        return None
    upper = code.strip().upper()
    if not upper:
        return None
    return _LAB_ALIASES.get(upper, upper)


def _strip_prefix(title: str) -> str | None:
    for prefix in _TITLE_PREFIXES:
        if not title.startswith(prefix):
            continue
        rest = title[len(prefix) :]
        if prefix[-1] in ":-" or not rest or rest[0].isspace():
            return rest.strip()
    return None


def _strip_suffix(title: str) -> str | None:
    for suffix in _TITLE_SUFFIXES:
        if not title.endswith(suffix):
            continue
        rest = title[: -len(suffix)]
        if not rest or rest[-1].isspace():
            return rest.strip()
    return None


def normalize_title(title: str | None) -> str:
    """Lowercase a document title and strip known prefixes and suffixes until none match.

    Whitespace is collapsed and "h & p" becomes "h&p" first. Doubled prefixes such as
    "Pre-op Pre-op Evaluation [PDF]" (case_00048) reduce to the core.
    """

    if not isinstance(title, str):
        return ""
    current = _WHITESPACE_RE.sub(" ", title).strip().lower()
    current = _H_AND_P_SPACED_RE.sub("h&p", current)
    while True:
        stripped = _strip_prefix(current)
        if stripped is None:
            stripped = _strip_suffix(current)
        if stripped is None:
            return current
        current = stripped


def most_recent(
    items: Sequence[T], instant_of: Callable[[T], datetime | None]
) -> tuple[int, T] | None:
    """Return ``(index, item)`` for the item with the latest valid instant, or None.

    ``instant_of`` is normally ``parse_instant`` applied to the item's date
    field. Items without a valid instant are ignored. On a tie the later list
    index wins. The index is the item's position in ``items`` so issues can
    cite ``collection[i]``.
    """

    best: tuple[datetime, int] | None = None
    for index, item in enumerate(items):
        instant = instant_of(item)
        if instant is None:
            continue
        if best is None or (instant, index) >= best:
            best = (instant, index)
    if best is None:
        return None
    return best[1], items[best[1]]
