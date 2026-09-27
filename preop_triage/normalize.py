"""Input normalization (DESIGN.md section 2, "normalize").

Dates are compared as calendar dates in UTC, ignoring time of day. Lab codes
are aliased (``LAB-CBC`` to ``CBC``, ``LAB-CMP`` to ``CMP``). Document titles
are lowercased and stripped of known prefixes and suffixes, repeatedly, before
classification.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
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


def parse_date(value: str | None) -> date | None:
    """Parse an ISO date or datetime string to a UTC calendar date.

    Accepts ``YYYY-MM-DD`` and ISO datetimes with ``Z`` or a UTC offset. An
    aware datetime is converted to UTC before its date is taken; a naive one is
    taken as-is. Returns None for a missing or unparseable value.
    """

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if _DATE_ONLY_RE.match(text):
            return date.fromisoformat(text)
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
    return parsed.date()


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

    Whitespace is collapsed first. Doubled prefixes such as
    "Pre-op Pre-op Evaluation [PDF]" (case_00048) reduce to the core.
    """

    if not isinstance(title, str):
        return ""
    current = _WHITESPACE_RE.sub(" ", title).strip().lower()
    while True:
        stripped = _strip_prefix(current)
        if stripped is None:
            stripped = _strip_suffix(current)
        if stripped is None:
            return current
        current = stripped


def most_recent(
    items: Sequence[T], date_of: Callable[[T], date | None]
) -> tuple[int, T] | None:
    """Return ``(index, item)`` for the item with the latest valid date, or None.

    Items without a valid date are ignored. On a tie the later list index wins.
    The index is the item's position in ``items`` so issues can cite
    ``collection[i]``.
    """

    best: tuple[date, int] | None = None
    for index, item in enumerate(items):
        item_date = date_of(item)
        if item_date is None:
            continue
        if best is None or (item_date, index) >= best:
            best = (item_date, index)
    if best is None:
        return None
    return best[1], items[best[1]]
