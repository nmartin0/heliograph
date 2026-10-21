"""Instants in time as integer nanoseconds since the Unix epoch, in UTC.

Integer nanoseconds match the int64 timestamps used by Arrow, Parquet, polars
and pandas, compare and sort exactly, and cannot carry a time zone by mistake.
Display time zones belong to the user interface and never reach the core.
"""

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
    """
    return _EPOCH + timedelta(microseconds=ts // NANOS_PER_MICROSECOND)


def from_iso(text: str) -> Timestamp:
    """Parse an ISO 8601 date-time that states its offset, such as ``2026-10-20T14:30:00Z``.

    Raises:
        InvalidValueError: if the text is not ISO 8601 or has no offset.
    """
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidValueError(f"{text!r} is not an ISO 8601 date-time") from exc
    return from_datetime(moment)
