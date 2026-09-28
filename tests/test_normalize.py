from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from preop_triage.normalize import (
    canonical_lab_code,
    days_before,
    most_recent,
    normalize_title,
    parse_date,
    parse_instant,
    within_window,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-03-03", date(2026, 3, 3)),
        ("2026-02-19T09:10:00Z", date(2026, 2, 19)),
        ("2026-02-19T09:10:00+00:00", date(2026, 2, 19)),
        ("2026-02-19T09:10:00.123Z", date(2026, 2, 19)),
        # Offsets are dropped, not converted: the local calendar date counts.
        ("2026-02-19T23:30:00-05:00", date(2026, 2, 19)),
        ("2026-02-20T01:00:00+05:00", date(2026, 2, 20)),
        ("2026-01-31T20:00:00-05:00", date(2026, 1, 31)),
        # A naive datetime is taken as-is.
        ("2026-02-19T09:10:00", date(2026, 2, 19)),
        (" 2026-03-03 ", date(2026, 3, 3)),
    ],
)
def test_parse_date_valid(value: str, expected: date) -> None:
    assert parse_date(value) == expected


@pytest.mark.parametrize(
    "value", [None, "", "   ", "not a date", "2026-13-01", "2026-02-30", "03/03/2026", 20260303]
)
def test_parse_date_invalid_returns_none(value: object) -> None:
    assert parse_date(value) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-02-19T09:10:00Z", datetime(2026, 2, 19, 9, 10, tzinfo=UTC)),
        # Offsets are converted to UTC for ordering.
        ("2026-01-31T20:00:00-05:00", datetime(2026, 2, 1, 1, 0, tzinfo=UTC)),
        # Date-only is midnight UTC; naive is taken as UTC.
        ("2026-03-03", datetime(2026, 3, 3, 0, 0, tzinfo=UTC)),
        ("2026-02-19T09:10:00", datetime(2026, 2, 19, 9, 10, tzinfo=UTC)),
    ],
)
def test_parse_instant(value: str, expected: datetime) -> None:
    assert parse_instant(value) == expected


@pytest.mark.parametrize("value", [None, "", "not a date", "2026-02-30"])
def test_parse_instant_invalid_returns_none(value: str | None) -> None:
    assert parse_instant(value) is None


def test_local_date_offset_lab_is_31_days_out() -> None:
    # Verifier probe: 2026-01-31T20:00:00-05:00 is 31 local days before 2026-03-03.
    lab_date = parse_date("2026-01-31T20:00:00-05:00")
    assert lab_date is not None
    assert days_before(lab_date, date(2026, 3, 3)) == 31
    assert within_window(lab_date, date(2026, 3, 3), 30) is False


def test_days_before_is_calendar_difference() -> None:
    # case_00002: H&P 2026-01-30, procedure 2026-03-03.
    assert days_before(date(2026, 1, 30), date(2026, 3, 3)) == 32
    # case_00010: CBC 2026-02-19T09:10:00Z, procedure 2026-03-11.
    assert days_before(parse_date("2026-02-19T09:10:00Z"), date(2026, 3, 11)) == 20  # type: ignore[arg-type]
    assert days_before(date(2026, 3, 5), date(2026, 3, 3)) == -2


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (date(2026, 3, 3), True),  # same day
        (date(2026, 2, 1), True),  # exactly 30
        (date(2026, 1, 31), False),  # 31
        (date(2026, 3, 4), False),  # after the procedure
    ],
)
def test_within_window_is_inclusive_and_rejects_future(item: date, expected: bool) -> None:
    assert within_window(item, date(2026, 3, 3), 30) is expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("LAB-CBC", "CBC"),
        ("lab-cbc", "CBC"),
        ("Lab-Cmp", "CMP"),
        ("LAB-CMP", "CMP"),
        ("CBC", "CBC"),
        ("cmp", "CMP"),
        ("HBA1C", "HBA1C"),
        ("hba1c", "HBA1C"),
        (" CBC ", "CBC"),
        (None, None),
        ("", None),
    ],
)
def test_canonical_lab_code(code: str | None, expected: str | None) -> None:
    assert canonical_lab_code(code) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # Doubled prefixes named in DESIGN.md D3.
        ("Pre-op Pre-op Evaluation [PDF]", "evaluation"),
        ("Pre-op Preop H&P (external)", "h&p"),
        # Prefix and suffix combinations.
        ("PREOP - Clinic H&P (external)", "clinic h&p"),
        ("PREOP - Pre-op H and P", "h and p"),
        ("Imported: History & Physical (H&P) - signed", "history & physical (h&p)"),
        ("Scanned H&P Note (scanned)", "h&p note"),
        ("Pre-op H/P (H&P) - signed", "h/p (h&p)"),
        ("Scanned History & Physical Exam (H&P) (external)", "history & physical exam (h&p)"),
        ("Anticoag Plan (scanned)", "anticoag plan"),
        ("Pre-op Nursing Intake", "nursing intake"),
        # "preop" must not eat "preoperative"; "pre-op" must not eat "pre-anesthesia".
        ("PREOP - Preoperative History & Physical (H&P)", "preoperative history & physical (h&p)"),
        ("Preop Pre-anesthesia Evaluation [PDF]", "pre-anesthesia evaluation"),
        # Whitespace collapsed, case folded.
        ("  History   and\tPhysical  ", "history and physical"),
        ("History & Phsyical", "history & phsyical"),
        # "h & p" is rewritten to "h&p" (checkpoint 1).
        ("H & P", "h&p"),
        ("Interval H &P Addendum", "interval h&p addendum"),
        # "- signed" needs a preceding space; "[pdf]" alone is a bare suffix.
        ("Consent-signed", "consent-signed"),
        ("[PDF]", ""),
        (None, ""),
        ("", ""),
    ],
)
def test_normalize_title(title: str | None, expected: str) -> None:
    assert normalize_title(title) == expected


def test_most_recent_picks_latest_date_not_index() -> None:
    items = ["2026-01-01", "2026-03-01", "2026-02-01"]
    assert most_recent(items, parse_instant) == (1, "2026-03-01")


def test_most_recent_ignores_unparseable_dates() -> None:
    items = [None, "garbage", "2026-01-01", ""]
    assert most_recent(items, parse_instant) == (2, "2026-01-01")


def test_most_recent_same_day_orders_by_time() -> None:
    # Checkpoint 1 fix: on the same date the later time wins, whatever the order.
    items = ["2026-02-24T10:12:00Z", "2026-02-24T08:00:00Z", "2026-02-01"]
    assert most_recent(items, parse_instant) == (0, "2026-02-24T10:12:00Z")


def test_most_recent_orders_offsets_by_instant() -> None:
    # 09:00-05:00 is 14:00 UTC, later than 12:00Z on the same local date.
    items = ["2026-02-24T09:00:00-05:00", "2026-02-24T12:00:00Z"]
    assert most_recent(items, parse_instant) == (0, "2026-02-24T09:00:00-05:00")


def test_most_recent_date_only_is_midnight_utc() -> None:
    items = ["2026-02-24T00:30:00Z", "2026-02-24"]
    assert most_recent(items, parse_instant) == (0, "2026-02-24T00:30:00Z")


def test_most_recent_identical_instants_take_later_index() -> None:
    items = ["2026-02-24T10:12:00Z", "2026-02-24T10:12:00+00:00", "2026-02-01"]
    assert most_recent(items, parse_instant) == (1, "2026-02-24T10:12:00+00:00")


def test_most_recent_empty_or_all_invalid_is_none() -> None:
    assert most_recent([], parse_instant) is None
    assert most_recent([None, "x"], parse_instant) is None
