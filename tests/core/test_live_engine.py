# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The live engine: arrival-stamped inputs, alarms and settling, and a live day that replays."""

from dataclasses import dataclass

import pytest

from heliograph.core.alarms import AlarmFired
from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.engine import BacktestEngine, LiveEngine
from heliograph.core.errors import (
    ClockError,
    InvalidTypeError,
    InvalidValueError,
    OutOfOrderError,
    ReentrancyError,
)
from heliograph.core.journal import JournalEntry, MemoryJournal, runs
from heliograph.core.messages import Message, RunEnded, Settled
from heliograph.core.time import Timestamp
from tests.support.world import Tick


@dataclass(frozen=True, slots=True)
class Echo:
    """What the toy component publishes: derived from an input or an alarm."""

    ts: Timestamp
    of: str


def live(
    start: int = 100,
) -> tuple[SimulatedClock, LiveEngine, MemoryJournal, list[str], MessageBus]:
    clock = SimulatedClock(Timestamp(start))
    journal = MemoryJournal()
    bus = MessageBus(clock, journal)
    engine = LiveEngine(clock, bus)
    seen: list[str] = []
    for kind in (Tick, AlarmFired, Settled, Echo, RunEnded):
        bus.subscribe(kind, lambda m: seen.append(f"{type(m).__name__}{m.ts}"))
    bus.subscribe(Tick, lambda m: bus.publish(Echo(clock.now(), "tick")))
    bus.subscribe(Tick, lambda m: engine.scheduler.at(Timestamp(m.ts + 10), "later"))
    bus.subscribe(AlarmFired, lambda m: bus.publish(Echo(m.ts, "alarm")))
    return clock, engine, journal, seen, bus


def test_a_clock_reading_that_repeats_joins_one_instant_instead_of_ending_the_run() -> None:
    clock, engine, _, seen, _ = live()
    engine.receive(Tick(clock.now()))
    engine.receive(Tick(clock.now()))
    engine.tick()
    assert "Settled100" not in seen  # the clock is still at 100: more may arrive
    clock.advance_to(Timestamp(101))
    engine.tick()
    assert seen.count("Settled100") == 1
    assert seen.index("Settled100") > max(i for i, s in enumerate(seen) if s == "Tick100")


def test_alarms_due_by_an_inputs_stamp_fire_before_it_and_after_the_earlier_instant_settles() -> (
    None
):
    clock, engine, _, seen, _ = live()
    engine.receive(Tick(clock.now()))  # sets an alarm at 110
    clock.advance_to(Timestamp(110))
    engine.receive(Tick(clock.now()))
    assert seen[seen.index("Tick100") :] == [
        "Tick100",
        "Echo100",
        "Settled100",
        "AlarmFired110",
        "Echo110",
        "Tick110",
        "Echo110",
    ]


def test_tick_fires_the_alarms_the_clock_has_reached_and_settles_their_instant() -> None:
    clock, engine, _, seen, _ = live()
    engine.receive(Tick(clock.now()))
    clock.advance_to(Timestamp(125))
    engine.tick()
    assert seen[-3:] == ["AlarmFired110", "Echo110", "Settled110"]


def test_the_next_wake_is_the_next_alarm_or_the_end_of_the_open_instant() -> None:
    clock, engine, _, _, _ = live()
    assert engine.next_wake() is None
    engine.receive(Tick(clock.now()))  # alarm at 110; instant 100 open
    assert engine.next_wake() == 101
    clock.advance_to(Timestamp(101))
    engine.tick()  # instant 100 settled
    assert engine.next_wake() == 110
    clock.advance_to(Timestamp(110))
    engine.tick()  # alarm fired at 110, which stays open while the clock reads 110
    assert engine.next_wake() == 111
    clock.advance_to(Timestamp(111))
    engine.tick()
    assert engine.next_wake() is None


def test_finish_settles_the_open_instant_and_marks_the_end() -> None:
    clock, engine, journal, seen, _ = live()
    engine.receive(Tick(clock.now()))
    assert engine.finish() == 100
    assert seen[-2:] == ["Settled100", "RunEnded100"]
    assert journal.entries[-1].message == RunEnded(Timestamp(100))


def test_an_engine_message_is_not_a_live_input() -> None:
    clock, engine, *_ = live()
    with pytest.raises(InvalidTypeError, match="Settled"):
        engine.receive(Settled(clock.now()))


def test_an_input_stamped_before_the_latest_instant_is_refused() -> None:
    clock, engine, *_ = live()
    engine.receive(Tick(clock.now()))
    with pytest.raises(OutOfOrderError, match="went back in time from 100 to 99"):
        engine.receive(Tick(Timestamp(99)))


def test_an_input_for_an_instant_that_has_settled_is_refused() -> None:
    clock, engine, *_ = live()
    engine.receive(Tick(clock.now()))
    engine.finish()
    with pytest.raises(ClockError, match="already settled"):
        engine.receive(Tick(clock.now()))


def test_a_handler_cannot_feed_the_engine() -> None:
    clock, engine, _, _, bus = live()
    again = [True]

    def feed_again(_: Tick) -> None:
        if again and again.pop():
            engine.receive(Tick(clock.now()))

    bus.subscribe(Tick, feed_again)
    with pytest.raises(ReentrancyError):
        engine.receive(Tick(clock.now()))


def test_the_engine_and_its_bus_must_share_a_clock() -> None:
    clock = SimulatedClock(Timestamp(0))
    with pytest.raises(InvalidValueError, match="share one clock"):
        LiveEngine(clock, MessageBus(SimulatedClock(Timestamp(0))))


def test_the_engine_and_its_alarms_must_share_a_clock() -> None:
    from heliograph.core.alarms import AlarmQueue

    clock = SimulatedClock(Timestamp(0))
    with pytest.raises(InvalidValueError, match="share one clock"):
        LiveEngine(clock, MessageBus(clock), AlarmQueue(SimulatedClock(Timestamp(0))))


def replayed(entries: list[JournalEntry]) -> list[JournalEntry]:
    """Feed a journal's inputs to a fresh backtest wired the same way."""
    ((feed, until),) = runs(entries)
    clock = SimulatedClock(Timestamp(0))
    journal = MemoryJournal()
    bus = MessageBus(clock, journal)
    engine = BacktestEngine(clock, bus)
    bus.subscribe(Tick, lambda m: bus.publish(Echo(clock.now(), "tick")))
    bus.subscribe(Tick, lambda m: engine.scheduler.at(Timestamp(m.ts + 10), "later"))
    bus.subscribe(AlarmFired, lambda m: bus.publish(Echo(m.ts, "alarm")))
    engine.run(feed, until=until)
    return journal.entries


def test_a_live_day_replays_to_the_same_journal() -> None:
    """Repeated readings, an idle gap with alarms due, and a late alarm all replay exactly."""
    clock, engine, journal, _, _ = live()
    stamps: list[Message] = []
    for at in (100, 100, 103, 110, 110, 111, 150):
        clock.advance_to(Timestamp(at))
        if at == 103:  # otherwise only an input moves the engine on
            engine.tick()
        tick = Tick(clock.now())
        stamps.append(tick)
        engine.receive(tick)
    clock.advance_to(Timestamp(400))
    engine.tick()
    engine.finish()
    assert replayed(journal.entries) == journal.entries
