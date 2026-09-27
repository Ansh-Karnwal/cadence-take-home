"""Input normalization (DESIGN.md section 2, "normalize").

Dates are compared as calendar dates in UTC, ignoring time of day. Lab codes
are aliased (``LAB-CBC`` to ``CBC``, ``LAB-CMP`` to ``CMP``). Document titles
are lowercased and stripped of known prefixes and suffixes, repeatedly, before
classification.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from typing import TypeVar

T = TypeVar("T")


def parse_date(value: str | None) -> date | None:
    """Parse an ISO date or datetime string to a UTC calendar date.

    Returns None for a missing or unparseable value.
    """

    raise NotImplementedError


def days_before(item_date: date, procedure_date: date) -> int:
    """Return calendar days from ``item_date`` to ``procedure_date``.

    Negative when the item is dated after the procedure.
    """

    raise NotImplementedError


def within_window(item_date: date, procedure_date: date, max_days: int) -> bool:
    """Return True when ``0 <= days_before(item_date, procedure_date) <= max_days``."""

    raise NotImplementedError


def canonical_lab_code(code: str | None) -> str | None:
    """Map a raw lab code to its canonical form (``CBC``, ``CMP``) or None."""

    raise NotImplementedError


def normalize_title(title: str | None) -> str:
    """Lowercase a document title and strip known prefixes and suffixes until none match."""

    raise NotImplementedError


def most_recent(
    items: Sequence[T], date_of: Callable[[T], date | None]
) -> tuple[int, T] | None:
    """Return ``(index, item)`` for the item with the latest valid date, or None.

    Items without a valid date are ignored. The index is the item's position in
    ``items`` so issues can cite ``collection[i]``.
    """

    raise NotImplementedError
