# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The engine marks the end of each instant with a Settled message."""

from dataclasses import dataclass

import pytest
from hypothesis import given
from hypothesis import strategies as st

from heliograph.core.alarms import AlarmFired
from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.engine import BacktestEngine
from heliograph.core.errors import ClockError
from heliograph.core.messages import Message, Settled
from heliograph.core.time import Timestamp


@dataclass(frozen=True, slots=True)
class Tick:
    ts: Timestamp


@dataclass(frozen=True, slots=True)
class Signal:
    ts: Timestamp


def build() -> tuple[SimulatedClock, MessageBus, BacktestEngine, list[str]]:
    clock = SimulatedClock(Timestamp(0))
    bus = MessageBus(clock)
    seen: list[str] = []
    for kind in (Tick, AlarmFired, Settled, Signal):
        bus.subscribe(kind, lambda m: seen.append(f"{type(m).__name__}{m.ts}"))
    return clock, bus, BacktestEngine(clock, bus), seen


def ticks(*times: int) -> list[Tick]:
    return [Tick(Timestamp(t)) for t in times]


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
