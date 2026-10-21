# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Alarms: "wake me at this time", for things that must happen on time.

A bar closes at its end whether or not a trade arrives then; a session opens at
09:30 whether or not anyone is looking. Components ask a ``Scheduler`` for an
alarm, and when the time comes an ``AlarmFired`` message is published on the
bus like any other, so alarms are ordered, guarded and (later) journalled with
everything else.

Alarms are keyed: setting an alarm again under the same key moves it. An alarm
already set for an instant fires before recorded data at that instant, because
what it closes (a bar ending at T) concerns data before T; an alarm a handler
sets for the current instant fires after the message being handled. Alarms
due at the same instant fire in the order they were first set; a repeating
alarm keeps its place.

If a handler raises while an alarm is delivered, the run ends (as for any
message): a one-off alarm is spent and a repeating one already rescheduled.

Live use: an engine on a wall clock must fire an alarm whose time has just
passed instead of refusing it, because the clock moves between computing a
boundary and setting the alarm. That engine does not exist yet; this queue
refuses past times, which is exact for a simulated clock.
"""

import heapq
from dataclasses import dataclass
from typing import Protocol

from heliograph.core.clock import Clock
from heliograph.core.errors import ClockError, InvalidValueError
from heliograph.core.time import Timestamp


@dataclass(frozen=True, slots=True, kw_only=True)
class AlarmFired:
    """Published when an alarm goes off; ``ts`` is the time it was set for."""

    ts: Timestamp
    key: str


class Scheduler(Protocol):
    """What a component may do with alarms."""

    def at(self, ts: Timestamp, key: str) -> None:
        """Fire once at ``ts``, replacing any alarm under ``key``."""
        ...

    def every(self, first: Timestamp, period: int, key: str) -> None:
        """Fire at ``first`` and every ``period`` nanoseconds after, replacing ``key``."""
        ...

    def cancel(self, key: str) -> None:
        """Remove the alarm under ``key``, if any."""
        ...


@dataclass(frozen=True, slots=True)
class _Entry:
    ts: Timestamp
    order: int
    key: str
    period: int | None


class SchedulerView:
    """The part of an AlarmQueue a component may use: set and cancel, never fire."""

    __slots__ = ("_queue",)

    def __init__(self, queue: "AlarmQueue") -> None:
        self._queue = queue

    def at(self, ts: Timestamp, key: str) -> None:
        self._queue.at(ts, key)

    def every(self, first: Timestamp, period: int, key: str) -> None:
        self._queue.every(first, period, key)

    def cancel(self, key: str) -> None:
        self._queue.cancel(key)


class AlarmQueue:
    """The alarms of one run, in time order. An engine fires them; components set them."""

    __slots__ = ("_clock", "_current", "_heap", "_order")

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._heap: list[tuple[Timestamp, int, int, _Entry]] = []
        self._current: dict[str, _Entry] = {}
        self._order = 0

    def runs_on(self, clock: Clock) -> bool:
        """Whether alarms are checked against ``clock``."""
        return self._clock is clock

    def at(self, ts: Timestamp, key: str) -> None:
        """Fire once at ``ts``, replacing any alarm under ``key``.

        Raises:
            ClockError: if ``ts`` is earlier than the current time.
        """
        self._set(ts, key, None)

    def every(self, first: Timestamp, period: int, key: str) -> None:
        """Fire at ``first`` and every ``period`` nanoseconds after, replacing ``key``.

        Raises:
            ClockError: if ``first`` is earlier than the current time.
            InvalidValueError: if ``period`` is not positive.
        """
        if period <= 0:
            raise InvalidValueError(f"alarm {key!r} needs a positive period, got {period}")
        self._set(first, key, period)

    def cancel(self, key: str) -> None:
        """Remove the alarm under ``key``, if any."""
        self._current.pop(key, None)

    def next_due(self) -> Timestamp | None:
        """When the earliest alarm is due, or None if there is none."""
        self._discard_stale()
        return self._heap[0][0] if self._heap else None

    def pop_due(self, limit: Timestamp) -> AlarmFired | None:
        """Take the earliest alarm due at or before ``limit``, rescheduling it if it repeats."""
        self._discard_stale()
        if not self._heap or self._heap[0][0] > limit:
            return None
        *_, entry = heapq.heappop(self._heap)
        if entry.period is None:
            del self._current[entry.key]
        else:
            self._push(
                _Entry(Timestamp(entry.ts + entry.period), entry.order, entry.key, entry.period)
            )
        return AlarmFired(ts=entry.ts, key=entry.key)

    def __len__(self) -> int:
        return len(self._current)

    def _set(self, ts: Timestamp, key: str, period: int | None) -> None:
        now = self._clock.now()
        if ts < now:
            raise ClockError(f"alarm {key!r} set for {ts}, which is before now ({now})")
        self._push(_Entry(ts, self._next(), key, period))

    def heap_size(self) -> int:
        """Entries held, live and stale; for tests of the clean-up."""
        return len(self._heap)

    def _push(self, entry: _Entry) -> None:
        self._current[entry.key] = entry
        heapq.heappush(self._heap, (entry.ts, entry.order, self._next(), entry))
        # Moved and cancelled alarms stay in the heap until they reach the top;
        # rebuild it when they outnumber the live ones, so it cannot grow
        # without bound when one key is moved on every message.
        if len(self._heap) > 2 * len(self._current) + 16:
            live = set(map(id, self._current.values()))
            self._heap = [item for item in self._heap if id(item[3]) in live]
            heapq.heapify(self._heap)

    def _discard_stale(self) -> None:
        while self._heap and self._current.get(self._heap[0][3].key) is not self._heap[0][3]:
            heapq.heappop(self._heap)

    def _next(self) -> int:
        self._order += 1
        return self._order
