# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from heliograph.core.errors import InvalidValueError
from heliograph.core.time import (
    NANOS_PER_DAY,
    NANOS_PER_HOUR,
    NANOS_PER_MILLISECOND,
    NANOS_PER_MINUTE,
    NANOS_PER_SECOND,
    Timestamp,
    format_span,
    from_datetime,
    from_iso,
    parse_span,
    to_datetime,
    to_iso,
)

aware_datetimes = st.datetimes(timezones=st.timezones())


def test_a_known_instant_has_the_expected_value() -> None:
    assert from_datetime(datetime(2026, 10, 20, tzinfo=UTC)) == 1_792_454_400 * NANOS_PER_SECOND


def test_the_same_instant_in_any_zone_gives_the_same_timestamp() -> None:
    utc = datetime(2026, 10, 20, 14, 30, tzinfo=UTC)
    st_johns = utc.astimezone(timezone(timedelta(hours=-2, minutes=-30)))
    assert from_datetime(utc) == from_datetime(st_johns)


def test_a_naive_datetime_is_refused() -> None:
    with pytest.raises(InvalidValueError, match="no time zone"):
        from_datetime(datetime(2026, 10, 20))  # noqa: DTZ001 - naive on purpose


@given(aware_datetimes)
def test_round_trip_keeps_the_instant(moment: datetime) -> None:
    # Compared in UTC: PEP 495 makes an ambiguous local time (a daylight-saving
    # fall-back hour) unequal to every instant in another zone.
    assert to_datetime(from_datetime(moment)) == moment.astimezone(UTC)


@given(aware_datetimes, aware_datetimes)
def test_conversion_preserves_order(a: datetime, b: datetime) -> None:
    assert (a < b) == (from_datetime(a) < from_datetime(b))


def test_sub_microsecond_precision_rounds_down() -> None:
    assert to_datetime(Timestamp(1_999)) == datetime(1970, 1, 1, 0, 0, 0, 1, tzinfo=UTC)
    assert to_datetime(Timestamp(-1)) == datetime(1969, 12, 31, 23, 59, 59, 999_999, tzinfo=UTC)


def test_iso_text_with_an_offset_is_parsed() -> None:
    assert from_iso("2026-10-20T00:00:00Z") == 1_792_454_400 * NANOS_PER_SECOND
    assert from_iso("2026-10-19T21:30:00-02:30") == 1_792_454_400 * NANOS_PER_SECOND


def test_iso_text_without_an_offset_is_refused() -> None:
    with pytest.raises(InvalidValueError, match="no time zone"):
        from_iso("2026-10-20T00:00:00")


def test_text_that_is_not_a_date_time_is_refused() -> None:
    with pytest.raises(InvalidValueError, match="not an ISO 8601 date-time"):
        from_iso("yesterday")


@pytest.mark.parametrize(
    ("text", "nanos"),
    [
        ("2026-01-01T00:00:00.123456789Z", 123_456_789),
        ("2026-01-01T00:00:00.1234567+00:00", 123_456_700),
        ("2026-01-01T00:00:00,000000001Z", 1),
        ("2026-01-01T00:00:00.5Z", 500_000_000),
    ],
)
def test_iso_fractions_are_kept_to_the_nanosecond(text: str, nanos: int) -> None:
    assert from_iso(text) == from_iso("2026-01-01T00:00:00Z") + nanos


def test_iso_text_finer_than_a_nanosecond_is_refused() -> None:
    with pytest.raises(InvalidValueError, match="more precise than a nanosecond"):
        from_iso("2026-01-01T00:00:00.1234567891Z")


@pytest.mark.parametrize("ts", [10**21, -(10**21)])
def test_an_instant_outside_datetime_range_is_refused(ts: int) -> None:
    with pytest.raises(InvalidValueError, match="outside the range"):
        to_datetime(Timestamp(ts))


@pytest.mark.parametrize(
    ("text", "nanos"),
    [
        ("15m", 15 * NANOS_PER_MINUTE),
        ("4h", 4 * NANOS_PER_HOUR),
        ("1d", NANOS_PER_DAY),
        ("1w", 7 * NANOS_PER_DAY),
        ("1h30m", 90 * NANOS_PER_MINUTE),
        ("250ms", 250 * NANOS_PER_MILLISECOND),
        ("1ns", 1),
    ],
)
def test_spans_are_read_and_written_back_exactly(text: str, nanos: int) -> None:
    assert parse_span(text) == nanos
    assert format_span(nanos) == text


MALFORMED_SPANS = ["", "15", "m", "1.5h", "1h 30m", "30m1h", "1h1h", "0m", "-1h", "1h0m", "01h"]
MALFORMED_SPANS += ["\u0661h", "99999999999999999999w"]


@pytest.mark.parametrize("text", MALFORMED_SPANS)
def test_malformed_spans_are_refused(text: str) -> None:
    with pytest.raises(InvalidValueError):
        parse_span(text)


@given(st.integers(1, 10**18))
def test_any_positive_span_round_trips(nanos: int) -> None:
    assert parse_span(format_span(nanos)) == nanos


def test_the_longest_span_is_exactly_the_largest_int64() -> None:
    longest = 2**63 - 1
    assert parse_span(f"{longest}ns") == longest
    assert parse_span(format_span(longest)) == longest
    with pytest.raises(InvalidValueError, match="longer than"):
        parse_span(f"{longest + 1}ns")


@pytest.mark.parametrize("nanos", [0, -1, 2**63, 1.5, True])
def test_only_a_positive_int64_count_can_be_written_as_a_span(nanos: object) -> None:
    with pytest.raises(InvalidValueError, match="positive int64"):
        format_span(nanos)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("nanos", "text"),
    [
        (1_792_454_400 * NANOS_PER_SECOND, "2026-10-20T00:00:00Z"),
        (1_792_454_400 * NANOS_PER_SECOND + 250_000_000, "2026-10-20T00:00:00.25Z"),
        (1_792_454_400 * NANOS_PER_SECOND + 1, "2026-10-20T00:00:00.000000001Z"),
        (-1, "1969-12-31T23:59:59.999999999Z"),
    ],
)
def test_an_instant_is_written_as_iso_text_to_the_nanosecond(nanos: int, text: str) -> None:
    assert to_iso(Timestamp(nanos)) == text


@given(st.integers(min_value=-(2**63), max_value=2**63 - 1))
def test_iso_text_reads_back_as_the_same_instant(nanos: int) -> None:
    assert from_iso(to_iso(Timestamp(nanos))) == nanos
