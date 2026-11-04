# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Exchange sessions from the ``exchange_calendars`` package (decision D61).

``market_calendar("XNYS")`` is a ``SessionCalendar`` for that exchange's regular hours: its
holidays, early closes and lunch breaks are the package's. Where the package knows no more
sessions (about a year ahead) the market is taken as open, so a watch counting open time halts
sooner rather than never.
"""

from bisect import bisect_right

import exchange_calendars
import pandas

from heliograph.core.calendars import ALWAYS_OPEN, SessionCalendar
from heliograph.core.errors import InvalidValueError
from heliograph.core.time import Timestamp

type Interval = tuple[int, int]


class MarketCalendar:
    """The regular sessions of one exchange, as the package lists them."""

    def __init__(self, name: str) -> None:
        try:
            calendar = exchange_calendars.get_calendar(name)
        except exchange_calendars.errors.InvalidCalendarName as exc:
            raise InvalidValueError(f"{name!r} is not an exchange calendar") from exc
        self._name = name
        self._intervals = _intervals(calendar.schedule)
        self._starts = [start for start, _ in self._intervals]

    @property
    def name(self) -> str:
        return self._name

    def after_open_time(self, start: Timestamp, span: int) -> Timestamp:
        if span < 0:
            raise InvalidValueError(f"a span of open time cannot be negative, not {span}")
        cursor, remaining = int(start), span
        # The interval before the first one starting after the cursor may hold it.
        first = max(bisect_right(self._starts, cursor) - 1, 0)
        for begin, end in self._intervals[first:]:
            if end <= cursor:
                continue
            opened = max(begin, cursor)
            if remaining <= end - opened:
                return Timestamp(opened + remaining)
            remaining -= end - opened
            cursor = end
        return Timestamp(cursor + remaining)  # past the last known session: open throughout


def _intervals(schedule: pandas.DataFrame) -> list[Interval]:
    """The open stretches, in order: a session, or its two halves around a break."""
    stretches: list[Interval] = []
    for row in schedule.itertuples():
        opens, closes = int(row.open.value), int(row.close.value)
        if pandas.isna(row.break_start):
            stretches.append((opens, closes))
        else:
            stretches.append((opens, int(row.break_start.value)))
            stretches.append((int(row.break_end.value), closes))
    return stretches


def market_calendar(name: str) -> SessionCalendar:
    """The calendar of exchange ``name``; the empty name is a market that never closes."""
    return MarketCalendar(name) if name else ALWAYS_OPEN
