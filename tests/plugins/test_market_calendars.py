# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Exchange hours from exchange_calendars: sessions, nights, weekends, holidays, breaks."""

import pytest

from heliograph.core.calendars import ALWAYS_OPEN
from heliograph.core.errors import InvalidValueError
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MINUTE, Timestamp, from_iso
from heliograph.plugins.market_calendars import MarketCalendar, market_calendar

HOUR = NANOS_PER_HOUR
HALF = HOUR // 2


@pytest.fixture(scope="module")
def nyse() -> MarketCalendar:
    return MarketCalendar("XNYS")


def after(calendar: MarketCalendar, start: str, span: int) -> Timestamp:
    return calendar.after_open_time(from_iso(start), span)


def test_inside_a_session_open_time_is_time(nyse: MarketCalendar) -> None:
    # Monday 2 Nov 2026, New York on standard time: open 14:30 to 21:00 UTC.
    assert after(nyse, "2026-11-02T15:00:00Z", HOUR) == from_iso("2026-11-02T16:00:00Z")


def test_the_night_is_skipped(nyse: MarketCalendar) -> None:
    assert after(nyse, "2026-11-02T20:30:00Z", HOUR) == from_iso("2026-11-03T15:00:00Z")


def test_a_weekend_is_skipped(nyse: MarketCalendar) -> None:
    assert after(nyse, "2026-11-06T20:30:00Z", HOUR) == from_iso("2026-11-09T15:00:00Z")


def test_counting_from_a_closed_time_begins_at_the_next_open(nyse: MarketCalendar) -> None:
    assert after(nyse, "2026-11-07T12:00:00Z", HOUR) == from_iso("2026-11-09T15:30:00Z")


def test_a_holiday_and_an_early_close_are_the_exchanges_own(nyse: MarketCalendar) -> None:
    # Thursday 26 Nov 2026 is Thanksgiving, closed; the Friday after closes early at 18:00 UTC.
    assert after(nyse, "2026-11-25T20:30:00Z", HOUR) == from_iso("2026-11-27T15:00:00Z")
    assert after(nyse, "2026-11-27T17:30:00Z", HOUR) == from_iso("2026-11-30T15:00:00Z")


def test_a_lunch_break_is_not_open_time() -> None:
    hong_kong = MarketCalendar("XHKG")  # 3 Nov 2026: open 01:30 to 04:00, then 05:00 to 08:00 UTC
    assert after(hong_kong, "2026-11-03T03:30:00Z", HOUR) == from_iso("2026-11-03T05:30:00Z")


def test_a_span_of_nothing_ends_now_when_open_and_at_the_next_open_when_closed(
    nyse: MarketCalendar,
) -> None:
    assert after(nyse, "2026-11-02T15:00:00Z", 0) == from_iso("2026-11-02T15:00:00Z")
    assert after(nyse, "2026-11-02T22:00:00Z", 0) == from_iso("2026-11-03T14:30:00Z")


def test_past_the_last_known_session_the_market_is_taken_as_open(nyse: MarketCalendar) -> None:
    assert after(nyse, "2040-01-01T00:00:00Z", 90 * NANOS_PER_MINUTE) == from_iso(
        "2040-01-01T01:30:00Z"
    )


def test_a_negative_span_is_refused(nyse: MarketCalendar) -> None:
    with pytest.raises(InvalidValueError, match="cannot be negative"):
        nyse.after_open_time(from_iso("2026-11-02T15:00:00Z"), -1)


def test_a_name_that_is_no_exchange_is_refused_by_name() -> None:
    with pytest.raises(InvalidValueError, match="'NOWHERE' is not an exchange calendar"):
        market_calendar("NOWHERE")


def test_the_empty_name_is_a_market_that_never_closes() -> None:
    assert market_calendar("") is ALWAYS_OPEN
    assert market_calendar("XNYS").name == "XNYS"
