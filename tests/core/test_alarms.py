# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.support.world import Tick, ticks, world

from heliograph.core.alarms import AlarmFired, AlarmQueue
from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.engine import BacktestEngine
from heliograph.core.errors import AlarmLoopError, ClockError, InvalidValueError
from heliograph.core.messages import Message
from heliograph.core.time import Timestamp


def build(start: int = 0) -> tuple[SimulatedClock, MessageBus, BacktestEngine, list[Message]]:
    w = world(start)
    return w.clock, w.bus, w.engine, w.record(Tick, AlarmFired)


def test_alarms_fire_between_messages_and_before_a_message_at_the_same_time() -> None:
    _, _, engine, seen = build()
    engine.scheduler.at(Timestamp(5), "close")
    engine.scheduler.at(Timestamp(20), "late")
    engine.run(ticks(1, 5, 9))
    assert seen == [Tick(Timestamp(1)), AlarmFired(ts=Timestamp(5), key="close"), *ticks(5, 9)]


def test_the_clock_reads_the_alarm_time_while_it_is_delivered() -> None:
    clock, bus, engine, _ = build()
    times: list[int] = []
    bus.subscribe(AlarmFired, lambda alarm: times.append(clock.now() - alarm.ts))
    engine.scheduler.at(Timestamp(7), "a")
    engine.run(ticks(3, 10))
    assert times == [0]


def test_a_run_can_continue_past_its_data_to_close_what_is_open() -> None:
    clock, _, engine, seen = build()
    engine.scheduler.every(Timestamp(10), 10, "bar")
    engine.run(ticks(4, 15), until=Timestamp(35))
    fired = [m.ts for m in seen if isinstance(m, AlarmFired)]
    assert fired == [10, 20, 30]
    assert clock.now() == 35


def test_repeating_alarms_stop_when_cancelled_and_move_when_set_again() -> None:
    _, bus, engine, seen = build()
    scheduler = engine.scheduler

    def on_alarm(alarm: AlarmFired) -> None:
        if alarm.ts == 20:
            scheduler.cancel("bar")
            scheduler.at(Timestamp(25), "once")
            scheduler.at(Timestamp(27), "once")

    bus.subscribe(AlarmFired, on_alarm)
    scheduler.every(Timestamp(10), 10, "bar")
    engine.run(until=Timestamp(100))
    assert [(m.ts, m.key) for m in seen if isinstance(m, AlarmFired)] == [
        (10, "bar"),
        (20, "bar"),
        (27, "once"),
    ]


def test_alarms_due_at_the_same_time_fire_in_the_order_they_were_set() -> None:
    _, _, engine, seen = build()
    for key in ("b", "a", "c"):
        engine.scheduler.at(Timestamp(5), key)
    engine.run(until=Timestamp(5))
    assert [m.key for m in seen if isinstance(m, AlarmFired)] == ["b", "a", "c"]


def test_an_alarm_set_by_a_handler_for_now_fires_before_the_next_message() -> None:
    clock, bus, engine, seen = build()
    bus.subscribe(Tick, lambda tick: engine.scheduler.at(clock.now(), "echo"))
    engine.run(ticks(3, 3, 8))
    kinds = [type(m).__name__ + str(m.ts) for m in seen]
    assert kinds == ["Tick3", "AlarmFired3", "Tick3", "AlarmFired3", "Tick8", "AlarmFired8"]


def test_an_alarm_in_the_past_is_refused() -> None:
    queue = AlarmQueue(SimulatedClock(Timestamp(10)))
    with pytest.raises(ClockError, match="before now"):
        queue.at(Timestamp(9), "late")


@pytest.mark.parametrize("period", [0, -5])
def test_a_repeating_alarm_needs_a_positive_period(period: int) -> None:
    queue = AlarmQueue(SimulatedClock(Timestamp(0)))
    with pytest.raises(InvalidValueError, match="positive period"):
        queue.every(Timestamp(0), period, "bar")


def test_the_engine_refuses_alarms_on_another_clock() -> None:
    clock = SimulatedClock(Timestamp(0))
    with pytest.raises(InvalidValueError, match="alarms must share one clock"):
        BacktestEngine(clock, MessageBus(clock), AlarmQueue(SimulatedClock(Timestamp(0))))


def test_the_queue_reports_what_is_pending() -> None:
    queue = AlarmQueue(SimulatedClock(Timestamp(0)))
    assert queue.next_due() is None
    queue.at(Timestamp(9), "a")
    queue.at(Timestamp(4), "b")
    queue.at(Timestamp(6), "b")
    assert (queue.next_due(), len(queue)) == (6, 2)


@given(
    st.lists(st.integers(0, 1000), max_size=30),
    st.lists(st.integers(0, 1000), max_size=10),
    st.lists(st.tuples(st.integers(0, 1000), st.integers(1, 300)), max_size=4),
)
def test_alarms_and_data_are_delivered_in_time_order_at_their_own_instant(
    data: list[int], once: list[int], repeating: list[tuple[int, int]]
) -> None:
    clock, bus, engine, seen = build()
    lags: list[int] = []
    bus.subscribe(Tick, lambda m: lags.append(clock.now() - m.ts))
    bus.subscribe(AlarmFired, lambda m: lags.append(clock.now() - m.ts))
    for i, ts in enumerate(once):
        engine.scheduler.at(Timestamp(ts), f"once{i}")
    for i, (first, period) in enumerate(repeating):
        engine.scheduler.every(Timestamp(first), period, f"every{i}")
    engine.run(ticks(*sorted(data)), until=Timestamp(1000))
    times = [m.ts for m in seen]
    assert times == sorted(times)
    assert set(lags) <= {0}
    assert sum(isinstance(m, Tick) for m in seen) == len(data)
    assert sum(isinstance(m, AlarmFired) and m.key.startswith("once") for m in seen) == len(once)


def test_a_repeating_alarm_keeps_its_place_among_alarms_due_together() -> None:
    _, _, engine, seen = build()
    engine.scheduler.every(Timestamp(0), 10, "A")
    engine.scheduler.at(Timestamp(10), "B")
    engine.run(until=Timestamp(10))
    assert [(m.ts, m.key) for m in seen if isinstance(m, AlarmFired)] == [
        (0, "A"),
        (10, "A"),
        (10, "B"),
    ]


def test_moving_one_alarm_on_every_message_does_not_grow_memory() -> None:
    clock = SimulatedClock(Timestamp(0))
    queue = AlarmQueue(clock)
    queue.at(Timestamp(10**9), "session-close")
    for i in range(10_000):
        queue.at(Timestamp(i + 1), "watchdog")
    assert len(queue) == 2
    assert queue.heap_size() <= 2 * len(queue) + 17
    assert queue.next_due() == 10_000


def test_until_stops_the_run_at_that_instant() -> None:
    clock, _, engine, seen = build()
    assert engine.run(ticks(1, 5, 50), until=Timestamp(10)) == 2
    assert seen == ticks(1, 5)
    assert clock.now() == 10


def test_until_before_now_is_refused() -> None:
    _, _, engine, _ = build(start=100)
    with pytest.raises(ClockError, match="before now"):
        engine.run(until=Timestamp(5))


def test_a_handler_that_keeps_re_setting_an_alarm_now_stops_the_run() -> None:
    clock, bus, engine, _ = build()
    bus.subscribe(AlarmFired, lambda alarm: engine.scheduler.at(clock.now(), alarm.key))
    engine.scheduler.at(Timestamp(3), "stuck")
    with pytest.raises(AlarmLoopError, match="'stuck'"):
        engine.run(until=Timestamp(5))


def test_components_can_set_alarms_but_not_fire_them() -> None:
    _, _, engine, _ = build()
    assert not hasattr(engine.scheduler, "pop_due")
