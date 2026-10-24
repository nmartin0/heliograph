# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Instants in time as integer nanoseconds since the Unix epoch, in UTC.

Integer nanoseconds match the int64 timestamps used by Arrow, Parquet, polars
and pandas, compare and sort exactly, and cannot carry a time zone by mistake.
Display time zones belong to the user interface and never reach the core.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import NewType

from heliograph.core.errors import InvalidValueError

Timestamp = NewType("Timestamp", int)
"""An instant: nanoseconds since 1970-01-01T00:00:00Z."""

NANOS_PER_MICROSECOND = 1_000
NANOS_PER_MILLISECOND = 1_000_000
NANOS_PER_SECOND = 1_000_000_000
NANOS_PER_MINUTE = 60 * NANOS_PER_SECOND
NANOS_PER_HOUR = 60 * NANOS_PER_MINUTE
NANOS_PER_DAY = 24 * NANOS_PER_HOUR

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_FRACTION = re.compile(r"^(?P<head>.*?\d{2}:\d{2}:\d{2})(?P<sep>[.,])(?P<digits>\d+)(?P<tail>.*)$")


def from_datetime(moment: datetime) -> Timestamp:
    """Convert a time-zone-aware datetime to a timestamp.

    Raises:
        InvalidValueError: if ``moment`` is naive, because its instant is unknown.
    """
    if moment.utcoffset() is None:
        raise InvalidValueError(f"naive datetime {moment.isoformat()!r} has no time zone")
    delta = moment - _EPOCH
    seconds = delta.days * 86_400 + delta.seconds
    return Timestamp(seconds * NANOS_PER_SECOND + delta.microseconds * NANOS_PER_MICROSECOND)


def to_datetime(ts: Timestamp) -> datetime:
    """Convert a timestamp to an aware UTC datetime.

    ``datetime`` holds microseconds, so sub-microsecond precision is rounded
    down to the microsecond.

    Raises:
        InvalidValueError: if the instant is outside the years 1 to 9999.
    """
    try:
        return _EPOCH + timedelta(microseconds=ts // NANOS_PER_MICROSECOND)
    except OverflowError as exc:
        raise InvalidValueError(f"timestamp {ts} is outside the range of a datetime") from exc


def from_iso(text: str) -> Timestamp:
    """Parse an ISO 8601 date-time that states its offset, such as ``2026-10-20T14:30:00Z``.

    Fractions of a second are kept exactly, down to the nanosecond, although
    ``datetime`` itself stops at the microsecond.

    Raises:
        InvalidValueError: if the text is not ISO 8601, has no offset, or is
            more precise than a nanosecond.
    """
    extra_nanos = 0
    fraction = _FRACTION.match(text)
    if fraction and len(fraction["digits"]) > 6:
        digits = fraction["digits"]
        if len(digits) > 9:
            raise InvalidValueError(f"{text!r} is more precise than a nanosecond")
        extra_nanos = int(digits[6:].ljust(3, "0"))
        text = f"{fraction['head']}{fraction['sep']}{digits[:6]}{fraction['tail']}"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidValueError(f"{text!r} is not an ISO 8601 date-time") from exc
    return Timestamp(from_datetime(moment) + extra_nanos)


def to_iso(ts: Timestamp) -> str:
    """``ts`` as ISO 8601 in UTC, such as ``2026-10-20T14:30:00.25Z``: every
    nanosecond kept, trailing zeros of the fraction dropped (decision D19).

    ``from_iso`` reads it back exactly.

    Raises:
        InvalidValueError: if the instant is outside the years 1 to 9999.
    """
    seconds, nanos = divmod(ts, NANOS_PER_SECOND)
    whole = to_datetime(Timestamp(seconds * NANOS_PER_SECOND))
    fraction = f".{nanos:09d}".rstrip("0") if nanos else ""
    return f"{whole:%Y-%m-%dT%H:%M:%S}{fraction}Z"


_SPAN_UNITS = {
    "w": 7 * NANOS_PER_DAY,
    "d": NANOS_PER_DAY,
    "h": NANOS_PER_HOUR,
    "m": NANOS_PER_MINUTE,
    "s": NANOS_PER_SECOND,
    "ms": NANOS_PER_MILLISECOND,
    "us": NANOS_PER_MICROSECOND,
    "ns": 1,
}
_SPAN_PART = re.compile(r"([1-9][0-9]*)(w|d|h|ms|m|s|us|ns)")
_INT64_MAX = 2**63 - 1


def parse_span(text: str) -> int:
    """A length of time written as ``15m``, ``4h``, ``1d`` or ``1h30m``, in nanoseconds.

    Units are w, d, h, m, s, ms, us and ns, largest first, each with a whole
    number of ASCII digits and no leading zero. Days and weeks are exact
    multiples of 24 hours: calendar days are a session calendar's job.

    Raises:
        InvalidValueError: if the text is not such a span, or is longer than a
            64-bit count of nanoseconds holds (about 292 years).
    """
    parts = _SPAN_PART.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text:
        raise InvalidValueError(f"{text!r} is not a span such as 15m, 4h or 1h30m")
    units = [unit for _, unit in parts]
    order = list(_SPAN_UNITS)
    if units != sorted(set(units), key=order.index):
        raise InvalidValueError(f"{text!r} repeats a unit or lists them out of order")
    total = sum(int(number) * _SPAN_UNITS[unit] for number, unit in parts)
    if total > _INT64_MAX:
        raise InvalidValueError(f"{text!r} is longer than 2**63 nanoseconds")
    return total


def format_span(nanos: int) -> str:
    """The shortest exact way to write a positive span: 90 minutes is ``1h30m``.

    Raises:
        InvalidValueError: if ``nanos`` is not positive.
    """
    if type(nanos) is not int or not 0 < nanos <= _INT64_MAX:
        raise InvalidValueError(f"a span must be a positive int64 count, got {nanos!r}")
    parts = []
    for unit, size in _SPAN_UNITS.items():
        count, nanos = divmod(nanos, size)
        if count:
            parts.append(f"{count}{unit}")
    return "".join(parts)
