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
