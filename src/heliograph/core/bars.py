# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Building time bars from points, closed on time by alarms.

A bar is published when its interval ends (plus the spec's grace), whether or
not another point arrives then. Points are assigned to bars by the time they
happened (``event_time``), in any order, and are accepted until their bar
closes; a point arriving at or after that is late, is counted, and never
changes a published bar. Each open bar closes at its own time, so a point
whose source clock runs ahead, or points arriving out of order, cannot hold
another bar back.

The builder keeps one alarm, set to the earliest close it is waiting for.
With ``gaps="skip"`` nothing is scheduled while no bar is open; with
``"carry"`` a flat bar is due at every boundary after the first bar, forever.
"""

import math
from dataclasses import dataclass

from heliograph.core.alarms import AlarmFired, Scheduler
from heliograph.core.bus import MessageBus
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Point
from heliograph.core.streams import BarSpec, StreamSpec
from heliograph.core.time import Timestamp


@dataclass(slots=True)
class _Open:
    """A bar still collecting points."""

    open: float
    high: float
    low: float
    close: float
    volume: float

    def add(self, price: float, volume: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume


class TimeBars:
    """Publishes the bars of one stream from its points, each when its interval closes.

    One builder per stream: a second for the same stream is refused, since
    both would publish every bar.
    """

    __slots__ = (
        "_bus",
        "_closed_up_to",
        "_key",
        "_last_close",
        "_late",
        "_open",
        "_scheduler",
        "_spec",
        "_stream",
    )

    def __init__(self, stream: StreamSpec, bus: MessageBus, scheduler: Scheduler) -> None:
        if stream.bars is None:
            raise InvalidValueError(f"stream {stream.key} has no bar spec to build")
        self._stream = stream
        self._spec: BarSpec = stream.bars
        self._bus = bus
        self._scheduler = scheduler
        self._key = f"bars:{stream.identity}"
        scheduler.claim(self._key)
        self._open: dict[Timestamp, _Open] = {}
        self._closed_up_to: Timestamp | None = None
        self._last_close: float | None = None
        self._late = 0
        bus.subscribe(Point, self._on_point)
        bus.subscribe(AlarmFired, self._on_alarm)

    @property
    def late_points(self) -> int:
        """Points that arrived after their bar had closed, and were left out."""
        return self._late

    def _closes_at(self, start: Timestamp) -> Timestamp:
        return Timestamp(start + self._spec.size + self._spec.grace)

    def _on_point(self, point: Point) -> None:
        if point.instrument != self._stream.instrument:
            return
        spec = self._spec
        start = spec.start_of(point.event_time)
        # Late if its bar has closed, or would have closed by the time the point
        # became known. The first is not implied by the second: a handler may
        # publish a point stamped before a close that has already happened.
        closed = self._closed_up_to is not None and start < self._closed_up_to
        if closed or self._closes_at(start) <= point.ts:
            self._late += 1
            return
        price = self._value(point, spec.price)
        volume = 0.0 if spec.volume is None else self._value(point, spec.volume)
        bar = self._open.get(start)
        if bar is None:
            self._open[start] = _Open(price, price, price, price, volume)
            self._rearm()
        else:
            bar.add(price, volume)

    def _on_alarm(self, alarm: AlarmFired) -> None:
        if alarm.key != self._key:
            return
        size = self._spec.size
        while (start := self._next_interval()) is not None and self._closes_at(start) <= alarm.ts:
            end = Timestamp(start + size)
            bar = self._open.pop(start, None)
            self._closed_up_to = end
            if bar is None:
                # Only carrying reaches an interval with no bar, and only after a
                # bar has closed, which set the last close.
                assert self._last_close is not None
                price = self._last_close
                bar = _Open(price, price, price, price, 0.0)
            self._last_close = bar.close
            self._publish(start, end, bar, alarm.ts)
        self._rearm()

    def _next_interval(self) -> Timestamp | None:
        """The start of the next interval to close: the next boundary when
        carrying, otherwise the earliest open bar."""
        earliest_open = min(self._open, default=None)
        if self._spec.gaps == "carry" and self._closed_up_to is not None:
            return self._closed_up_to
        return earliest_open

    def _rearm(self) -> None:
        # With nothing to close no alarm is pending: one is set only while a
        # bar is open, and open bars are removed only when it fires.
        start = self._next_interval()
        if start is not None:
            self._scheduler.at(self._closes_at(start), self._key)

    def _publish(self, start: Timestamp, end: Timestamp, bar: _Open, now: Timestamp) -> None:
        self._bus.publish(
            Bar(
                instrument=self._stream.instrument,
                start=start,
                end=end,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                received_at=None if now == end else now,
            )
        )

    def _value(self, point: Point, name: str) -> float:
        try:
            return point.values[name]
        except KeyError:
            raise InvalidValueError(
                f"stream {self._stream.key} needs {name!r} but a point has only "
                f"{sorted(point.values)}"
            ) from None


class BarsFromBars:
    """Publishes the bars of one stream from finer bars of its instrument (decision D33).

    A stream's bar is the finer bars whose start falls in its interval, put
    together: the first open and the last close (by start), the highest
    high, the lowest low and the summed volume. It is published when the
    last finer bar of its interval (the one ending at its end) arrives, at
    that bar's instant, so a bar built from bars is known exactly when its
    last part is. If that part never comes (nothing traded in it, or the
    data ends), the bar is published by an alarm at its close: one finer bar
    after its end, plus the source's ``delay`` (how long after its end a
    finer bar becomes known) and the spec's grace, by when the next finer
    bar would have been known. An alarm fires before inputs at its instant,
    so it cannot be set at the end itself without closing the bar before
    its last part.

    Bars are published in order, one per instant: a bar whose last part
    arrives while an earlier bar is still open closes that earlier bar
    then, and is itself published at its own close. A finer bar of a bar
    already closed, or arriving at or after its close, is late, counted, and
    changes nothing. A finer bar that is not on its own grid (its start a
    whole number of its size since 1970) or repeats a start is refused:
    nothing is guessed. An interval with no finer bar has no bar (gaps are
    skipped; carrying them is not offered yet).
    """

    __slots__ = (
        "_bus",
        "_closed_up_to",
        "_delay",
        "_finer",
        "_key",
        "_late",
        "_open",
        "_scheduler",
        "_spec",
        "_stream",
    )

    def __init__(
        self,
        stream: StreamSpec,
        finer: int,
        bus: MessageBus,
        scheduler: Scheduler,
        delay: int = 0,
    ) -> None:
        if stream.bars is None:
            raise InvalidValueError(f"stream {stream.key} has no bar spec to build")
        spec = stream.bars
        if finer <= 0 or spec.size <= finer or spec.size % finer or spec.anchor % finer:
            raise InvalidValueError(
                f"stream {stream.key} cannot be built from bars of {finer} ns: "
                "its bars must be a whole number (two or more) of them, edge to edge"
            )
        if spec.gaps != "skip":
            raise InvalidValueError(f"stream {stream.key}: bars built from bars skip gaps")
        if delay < 0:
            raise InvalidValueError(f"stream {stream.key}: a delay cannot be negative")
        self._delay = delay
        self._stream = stream
        self._spec = spec
        self._finer = finer
        self._bus = bus
        self._scheduler = scheduler
        self._key = f"bars:{stream.identity}"
        scheduler.claim(self._key)
        self._open: dict[Timestamp, list[Bar]] = {}
        self._closed_up_to: Timestamp | None = None
        self._late = 0
        bus.subscribe(Bar, self._on_bar)
        bus.subscribe(AlarmFired, self._on_alarm)

    @property
    def late_bars(self) -> int:
        """Finer bars that arrived after their bar had closed, and were left out."""
        return self._late

    def _closes_at(self, start: Timestamp) -> Timestamp:
        return Timestamp(start + self._spec.size + self._finer + self._delay + self._spec.grace)

    def _on_bar(self, bar: Bar) -> None:
        if bar.instrument != self._stream.instrument or bar.end - bar.start != self._finer:
            return
        if bar.start % self._finer:
            raise InvalidValueError(
                f"stream {self._stream.key}: a {self._finer} ns bar starting at {bar.start} "
                "is not on its own grid, so it cannot be put in a bar"
            )
        start = self._spec.start_of(bar.start)
        closed = self._closed_up_to is not None and start < self._closed_up_to
        if closed or self._closes_at(start) <= bar.ts:
            self._late += 1
            return
        parts = self._open.setdefault(start, [])
        if any(part.start == bar.start for part in parts):
            raise InvalidValueError(
                f"stream {self._stream.key}: two bars starting at {bar.start}; "
                "a source delivers each bar once"
            )
        parts.append(bar)
        if bar.end == start + self._spec.size:
            earlier = [s for s in self._open if s < start]
            if earlier:
                # One bar per instant: the earlier ones now, this one at its close.
                self._close_through(max(earlier), bar.ts)
            else:
                self._close_through(start, bar.ts)
        self._rearm()

    def _on_alarm(self, alarm: AlarmFired) -> None:
        if alarm.key != self._key:
            return
        due = [start for start in self._open if self._closes_at(start) <= alarm.ts]
        if due:
            self._close_through(max(due), alarm.ts)
        self._rearm()

    def _close_through(self, last: Timestamp, now: Timestamp) -> None:
        """Publish every open bar up to and including the one starting at ``last``."""
        for start in sorted(s for s in self._open if s <= last):
            parts = sorted(self._open.pop(start), key=lambda part: part.start)
            end = Timestamp(start + self._spec.size)
            self._closed_up_to = end
            self._bus.publish(
                Bar(
                    instrument=self._stream.instrument,
                    start=start,
                    end=end,
                    open=parts[0].open,
                    high=max(part.high for part in parts),
                    low=min(part.low for part in parts),
                    close=parts[-1].close,
                    volume=math.fsum(part.volume for part in parts),
                    received_at=None if now == end else now,
                )
            )

    def _rearm(self) -> None:
        if self._open:
            self._scheduler.at(self._closes_at(min(self._open)), self._key)
        else:
            self._scheduler.cancel(self._key)  # its bar closed on its last part
