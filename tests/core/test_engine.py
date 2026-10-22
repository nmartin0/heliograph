# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from hypothesis import given
from hypothesis import strategies as st

from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.engine import BacktestEngine
from heliograph.core.errors import (
    ClockError,
    InvalidValueError,
    LookAheadError,
    OutOfOrderError,
    ReentrancyError,
)
from heliograph.core.time import Timestamp
from tests.support.world import world


@dataclass(frozen=True, slots=True)
class Tick:
    ts: Timestamp
    feed: int
    seq: int


@dataclass(frozen=True, slots=True)
class Derived:
    """What a component publishes in reaction to a tick."""

    ts: Timestamp
    source: Tick


def build(start: int = 0) -> tuple[SimulatedClock, MessageBus, BacktestEngine]:
    w = world(start)
    return w.clock, w.bus, w.engine


def ticks(raw_feeds: list[list[int]]) -> list[list[Tick]]:
    return [
        [Tick(Timestamp(ts), feed, seq) for seq, ts in enumerate(sorted(values))]
        for feed, values in enumerate(raw_feeds)
    ]


raw_feeds = st.lists(st.lists(st.integers(0, 10**15), max_size=40), max_size=6)


@given(raw_feeds)
def test_no_component_ever_sees_the_future(raw: list[list[int]]) -> None:
    """The Phase 0 gate: nothing is delivered before or after its own instant.

    Covers recorded feed messages and the messages components publish in
    reaction to them.
    """
    clock, bus, engine = build()
    feeds = ticks(raw)
    seen: list[Tick] = []
    derived: list[Derived] = []

    def react(tick: Tick) -> None:
        assert tick.ts == clock.now(), "tick delivered at a time other than its own"
        seen.append(tick)
        bus.publish(Derived(clock.now(), tick))

    def record(message: Derived) -> None:
        assert message.ts == clock.now(), "derived message delivered at another time"
        derived.append(message)

    bus.subscribe(Tick, react)
    bus.subscribe(Derived, record)
    published = engine.run(*feeds)

    everything = [tick for feed in feeds for tick in feed]
    assert published == len(everything)
    # Time order, ties broken by feed order, each feed's own order kept.
    assert seen == sorted(everything, key=lambda t: (t.ts, t.feed, t.seq))
    assert [message.source for message in derived] == seen


def test_a_component_reaching_into_the_future_stops_the_run() -> None:
    clock, bus, engine = build()
    bus.subscribe(Tick, lambda tick: bus.publish(Derived(Timestamp(tick.ts + 1), tick)))
    with pytest.raises(LookAheadError, match="stamped 6 published at 5"):
        engine.run([Tick(Timestamp(5), 0, 0), Tick(Timestamp(9), 0, 1)])
    assert clock.now() == 5


def test_a_run_cannot_start_from_inside_a_handler() -> None:
    """A nested run would move the clock while the outer message is in hand."""
    _, bus, engine = build()
    inner = [Tick(Timestamp(2), 1, 0), Tick(Timestamp(3), 1, 1)]

    def start_nested_run(tick: Tick) -> None:
        if tick.feed == 0:
            engine.run(inner)

    bus.subscribe(Tick, start_nested_run)
    with pytest.raises(ReentrancyError):
        engine.run([Tick(Timestamp(1), 0, 0)])


def test_the_engine_can_run_again_after_a_failed_run() -> None:
    _, _, engine = build()
    with pytest.raises(OutOfOrderError):
        engine.run([Tick(Timestamp(5), 0, 0), Tick(Timestamp(4), 0, 1)])
    assert engine.run([Tick(Timestamp(6), 0, 0)]) == 1


def test_a_feed_that_goes_back_in_time_is_refused() -> None:
    _, _, engine = build()
    feed = [Tick(Timestamp(5), 0, 0), Tick(Timestamp(4), 0, 1)]
    with pytest.raises(OutOfOrderError, match="feed 0 went back in time from 5 to 4"):
        engine.run(feed)


def test_a_feed_that_starts_before_the_clock_is_refused() -> None:
    _, _, engine = build(start=10)
    with pytest.raises(ClockError):
        engine.run([Tick(Timestamp(9), 0, 0)])


def test_engine_and_bus_must_share_one_clock() -> None:
    bus = MessageBus(SimulatedClock(Timestamp(0)))
    with pytest.raises(InvalidValueError, match="share one clock"):
        BacktestEngine(SimulatedClock(Timestamp(0)), bus)


def test_feeds_are_read_at_most_one_message_ahead() -> None:
    """Feeds may be endless or huge: the engine never reads one to the end first."""
    _, bus, engine = build()
    produced = 0
    delivered = 0
    feed_count = 2

    def feed(index: int) -> Iterator[Tick]:
        nonlocal produced
        for seq in range(100):
            produced += 1
            yield Tick(Timestamp(seq), index, seq)

    def deliver(_: Tick) -> None:
        nonlocal delivered
        delivered += 1
        assert produced - delivered <= feed_count

    bus.subscribe(Tick, deliver)
    assert engine.run(*(feed(i) for i in range(feed_count))) == 200


def test_a_run_cannot_start_from_a_handler_even_outside_a_run() -> None:
    """A message published straight to the bus is in hand just as one the engine delivers."""
    _, bus, engine = build()

    def start_a_run(tick: Tick) -> None:
        engine.run([])

    bus.subscribe(Tick, start_a_run)
    with pytest.raises(ReentrancyError):
        bus.publish(Tick(Timestamp(0), 0, 0))


def test_a_run_cannot_start_while_a_feed_is_being_read() -> None:
    _, _, engine = build()

    def feed() -> Iterator[Tick]:
        engine.run([])
        yield Tick(Timestamp(1), 0, 0)

    with pytest.raises(ReentrancyError):
        engine.run(feed())


def test_a_run_may_stop_exactly_where_the_clock_already_is() -> None:
    clock, _, engine = build(start=7)
    engine.run(until=Timestamp(7))
    assert clock.now() == 7
