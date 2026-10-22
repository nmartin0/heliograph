# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A clock, bus and engine wired together, and the messages tests feed them."""

from collections.abc import Iterable
from dataclasses import dataclass

from heliograph.core.bars import TimeBars
from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.engine import BacktestEngine
from heliograph.core.journal import Journal
from heliograph.core.messages import Bar, Instrument, Message, Point
from heliograph.core.streams import BarSpec, StreamSpec
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MINUTE, Timestamp

BTC = Instrument(symbol="BTC/USD", venue="kraken", asset_class="crypto", quote_currency="USD")
ETH = Instrument(symbol="ETH/USD", venue="kraken", asset_class="crypto", quote_currency="USD")

END = Timestamp(12 * NANOS_PER_HOUR)
"""Where the toy session stops: twelve hours after the epoch."""


@dataclass(frozen=True, slots=True)
class Tick:
    """The smallest message: only a time."""

    ts: Timestamp


def ticks(*times: int) -> list[Tick]:
    return [Tick(Timestamp(t)) for t in times]


@dataclass(frozen=True, slots=True)
class World:
    clock: SimulatedClock
    bus: MessageBus
    engine: BacktestEngine

    def record(self, *kinds: type) -> list[Message]:
        """Subscribe to ``kinds`` and return the list their messages are appended to."""
        seen: list[Message] = []
        for kind in kinds:
            self.bus.subscribe(kind, seen.append)
        return seen


def world(start: int = 0, journal: Journal | None = None) -> World:
    clock = SimulatedClock(Timestamp(start))
    bus = MessageBus(clock, journal)
    return World(clock, bus, BacktestEngine(clock, bus))


def points(raw: Iterable[tuple[int, int]], size: float = 1.0) -> list[Point]:
    """BTC trades from ``(minute, price)`` pairs, in time order, each known when it happened."""
    out = []
    for minute, price in sorted(raw):
        ts = Timestamp(minute * NANOS_PER_MINUTE)
        values = {"price": price, "size": size}
        out.append(Point(instrument=BTC, event_time=ts, received_at=ts, values=values))
    return out


@dataclass(frozen=True, slots=True)
class Crossed:
    """What the toy strategy publishes when a bar closes above 105."""

    ts: Timestamp
    close: float


def toy_session(journal: Journal, *segments: tuple[Iterable[Message], Timestamp | None]) -> None:
    """1h and 4h BTC bars and a toy strategy, run over each ``(feed, until)`` in turn.

    The journal holds the result. Built the same way on every call, so a
    replay of its inputs reproduces it.
    """
    w = world(journal=journal)
    for size in ("1h", "4h"):
        TimeBars(StreamSpec(instrument=BTC, bars=BarSpec.of(size)), w.bus, w.engine.scheduler)

    def strategy(bar: Bar) -> None:
        if bar.close > 105:
            w.bus.publish(Crossed(bar.ts, bar.close))

    w.bus.subscribe(Bar, strategy)
    for feed, until in segments:
        w.engine.run(feed, until=until)
