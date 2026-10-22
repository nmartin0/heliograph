# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The engine marks the end of each instant with a Settled message."""

from dataclasses import dataclass

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.support.world import Tick, ticks, world

from heliograph.core.alarms import AlarmFired
from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.engine import BacktestEngine
from heliograph.core.errors import ClockError, InvalidTypeError
from heliograph.core.messages import Message, RunEnded, Settled
from heliograph.core.time import Timestamp


@dataclass(frozen=True, slots=True)
class Signal:
    ts: Timestamp


def build() -> tuple[SimulatedClock, MessageBus, BacktestEngine, list[str]]:
    w = world()
    seen: list[str] = []
    for kind in (Tick, AlarmFired, Settled, Signal):
        w.bus.subscribe(kind, lambda m: seen.append(f"{type(m).__name__}{m.ts}"))
    return w.clock, w.bus, w.engine, seen


def test_each_instant_settles_once_after_everything_at_it() -> None:
    _, _, engine, seen = build()
    engine.scheduler.at(Timestamp(5), "close")
    engine.run(ticks(3, 5, 5, 9))
    assert seen == [
        "Tick3",
        "Settled3",
        "AlarmFired5",
        "Tick5",
        "Tick5",
        "Settled5",
        "Tick9",
        "Settled9",
    ]


def test_instants_where_nothing_happened_do_not_settle() -> None:
    _, _, engine, seen = build()
    engine.run(ticks(2), until=Timestamp(100))
    assert seen == ["Tick2", "Settled2"]


def test_a_handler_may_act_on_settled_at_the_same_instant() -> None:
    clock, bus, engine, seen = build()
    bus.subscribe(Settled, lambda s: bus.publish(Signal(clock.now())))
    engine.run(ticks(4, 7))
    assert seen == ["Tick4", "Settled4", "Signal4", "Tick7", "Settled7", "Signal7"]


def test_a_settled_instant_cannot_be_given_another_alarm() -> None:
    clock, bus, engine, _ = build()
    bus.subscribe(Settled, lambda s: engine.scheduler.at(clock.now(), "too-late"))
    with pytest.raises(ClockError, match="already settled"):
        engine.run(ticks(4))


@given(st.lists(st.integers(0, 50), max_size=40), st.lists(st.integers(0, 50), max_size=8))
def test_nothing_arrives_at_an_instant_after_it_settled(data: list[int], alarms: list[int]) -> None:
    _, bus, engine, _ = build()
    events: list[Message] = []
    for kind in (Tick, AlarmFired, Settled):
        bus.subscribe(kind, events.append)
    for i, ts in enumerate(alarms):
        engine.scheduler.at(Timestamp(ts), f"a{i}")
    engine.run(ticks(*sorted(data)), until=Timestamp(50))
    settled = [m.ts for m in events if isinstance(m, Settled)]
    assert settled == sorted(set(data) | set(alarms))
    for index, message in enumerate(events):
        if isinstance(message, Settled):
            assert all(later.ts > message.ts for later in events[index + 1 :])


def alarm_after_five(engine: BacktestEngine, settled: Settled) -> None:
    if settled.ts == 5:
        engine.scheduler.at(Timestamp(6), "next")


def test_a_settled_handler_may_set_a_later_alarm_before_the_next_message() -> None:
    _, bus, engine, seen = build()
    bus.subscribe(Settled, lambda s: alarm_after_five(engine, s))
    engine.run(ticks(5, 10))
    assert seen == ["Tick5", "Settled5", "AlarmFired6", "Settled6", "Tick10", "Settled10"]


def test_an_alarm_set_while_the_last_instant_settles_still_fires_before_the_run_ends() -> None:
    clock, bus, engine, seen = build()
    bus.subscribe(Settled, lambda s: alarm_after_five(engine, s))
    engine.run(ticks(5), until=Timestamp(100))
    assert seen == ["Tick5", "Settled5", "AlarmFired6", "Settled6"]
    engine.run(until=Timestamp(200))
    assert clock.now() == 200


def test_a_later_run_cannot_add_to_an_instant_that_settled() -> None:
    _, _, engine, _ = build()
    engine.run(ticks(5))
    with pytest.raises(ClockError, match="already settled"):
        engine.run(ticks(5, 7))


def test_a_run_may_continue_where_the_last_one_stopped() -> None:
    _, _, engine, seen = build()
    engine.run(ticks(5))
    engine.run(ticks(7))
    assert seen == ["Tick5", "Settled5", "Tick7", "Settled7"]


@pytest.mark.parametrize("message", [AlarmFired(ts=Timestamp(1), key="k"), Settled(Timestamp(1))])
def test_a_feed_cannot_carry_messages_only_the_engine_makes(message: Message) -> None:
    _, _, engine, _ = build()
    with pytest.raises(InvalidTypeError, match="only the engine produces"):
        engine.run([message])


def test_every_run_ends_with_a_run_ended_mark() -> None:
    _, bus, engine, _ = build()
    ends: list[int] = []
    bus.subscribe(RunEnded, lambda m: ends.append(m.ts))
    engine.run(ticks(3))
    engine.run(until=Timestamp(9))
    assert ends == [3, 9]
