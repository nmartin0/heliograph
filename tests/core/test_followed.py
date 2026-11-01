# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The position a person holds: the strategy's signals as answers and the clock leave them."""

from dataclasses import dataclass, field

import pytest

from heliograph.core.alarms import AlarmFired
from heliograph.core.answers import Followed, SignalAnswer, SignalExpired, Verdict
from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.engine import LiveEngine
from heliograph.core.errors import InvalidValueError
from heliograph.core.journal import JournalEntry, MemoryJournal
from heliograph.core.messages import Message
from heliograph.core.pipeline import Signal
from heliograph.core.time import NANOS_PER_HOUR, Timestamp
from tests.support.world import BTC, World, world

HOUR = NANOS_PER_HOUR
LONG = 100 * HOUR  # a time to answer that does not run out within a test


@dataclass
class Holder:
    """A strategy as far as the follower is concerned: told each position, in order."""

    told: list[float] = field(default_factory=list)

    def hold(self, position: float) -> None:
        self.told.append(position)


@dataclass
class Case:
    w: World
    holder: Holder
    followed: Followed
    journal: MemoryJournal

    def run(self, *messages: Message, until: int = 50) -> None:
        self.w.engine.run(messages, until=Timestamp(until * HOUR))

    def expired(self) -> list[JournalEntry]:
        return [e for e in self.journal.entries if isinstance(e.message, SignalExpired)]


def case(filled: bool = False, within: int = LONG) -> Case:
    journal = MemoryJournal()
    w = world(journal=journal)
    holder = Holder()
    return Case(
        w,
        holder,
        Followed(
            "trend",
            holder,
            w.bus,
            w.engine.scheduler,
            filled=filled,
            within=within,
            now=w.clock.now,
        ),
        journal,
    )


def signal(hour: int, target: float, strategy: str = "trend") -> Signal:
    return Signal(
        ts=Timestamp(hour * HOUR),
        id=f"s{hour}",
        strategy=strategy,
        instrument=BTC,
        side="long",
        target=target,
        held=0.0,
        price=100.0,
        forecast=5.0,
    )


def answer(
    hour: int, signal_id: str, verdict: Verdict, quantity: float | None = None
) -> SignalAnswer:
    return SignalAnswer(
        ts=Timestamp(hour * HOUR), signal=signal_id, verdict=verdict, quantity=quantity
    )


def test_a_signal_the_strategy_takes_as_made_moves_the_position_at_once() -> None:
    c = case()
    c.run(signal(1, 1.0), signal(2, 2.5))
    assert c.holder.told == [1.0, 2.5]
    assert c.followed.position == 2.5


def test_a_signal_the_person_skipped_never_happened_for_them() -> None:
    c = case()
    c.run(signal(1, 1.0), answer(2, "s1", "skipped"))
    assert c.holder.told == [1.0, 0.0]


def test_skipping_a_later_signal_returns_to_what_the_earlier_ones_left() -> None:
    c = case()
    c.run(signal(1, 1.0), signal(2, 2.0), answer(3, "s2", "skipped"))
    assert c.holder.told == [1.0, 2.0, 1.0]


def test_a_signal_taken_as_signalled_changes_nothing_further() -> None:
    c = case()
    c.run(signal(1, 1.0), answer(2, "s1", "took"))
    assert c.holder.told == [1.0]


def test_a_signal_taken_in_another_quantity_moves_the_position_by_that_quantity() -> None:
    c = case()
    c.run(signal(1, 1.0), answer(2, "s1", "took", quantity=0.4), signal(3, 3.0))
    # 0.4 from flat; the next signal asks for 3 and, assumed taken, sets it
    assert c.holder.told == [1.0, 0.4, 3.0]


def test_a_quantity_is_added_to_what_the_earlier_signals_left() -> None:
    c = case()
    c.run(signal(1, 1.0), signal(2, 2.0), answer(3, "s2", "took", quantity=-0.5))
    assert c.holder.told == [1.0, 2.0, 0.5]


def test_a_later_answer_to_a_signal_replaces_an_earlier() -> None:
    c = case()
    c.run(signal(1, 1.0), answer(2, "s1", "skipped"), answer(3, "s1", "took"))
    assert c.holder.told == [1.0, 0.0, 1.0]


def test_an_answer_to_another_strategys_or_an_unknown_signal_is_ignored() -> None:
    c = case()
    c.run(
        signal(1, 5.0, strategy="other"),
        signal(2, 1.0),
        answer(3, "s1", "took", 9.0),
        answer(4, "nobody", "skipped"),
    )
    assert c.holder.told == [1.0]


def test_a_signal_nobody_answers_in_time_expires_and_counts_as_skipped() -> None:
    c = case(within=2 * HOUR)
    c.run(signal(1, 1.0), until=10)
    assert c.holder.told == [1.0, 0.0]
    (entry,) = c.expired()
    assert entry.message == SignalExpired(Timestamp(3 * HOUR), "s1")
    assert entry.origin == "derived"


def test_a_signal_answered_in_time_does_not_expire() -> None:
    c = case(within=2 * HOUR)
    c.run(signal(1, 1.0), answer(2, "s1", "took"), until=10)
    assert c.holder.told == [1.0]
    assert not c.expired()


def test_a_signal_that_expired_may_still_be_answered_late() -> None:
    c = case(within=HOUR)
    c.run(signal(1, 1.0), until=5)
    c.run(answer(6, "s1", "took"), until=7)
    assert c.holder.told == [1.0, 0.0, 1.0]
    assert len(c.expired()) == 1


def test_each_signal_expires_in_turn_at_its_own_time() -> None:
    c = case(within=3 * HOUR // 2)
    c.run(signal(1, 1.0), signal(2, 2.0), answer(2, "s2", "took"), signal(3, 3.0), until=10)
    assert [e.message for e in c.expired()] == [
        SignalExpired(Timestamp(5 * HOUR // 2), "s1"),
        SignalExpired(Timestamp(9 * HOUR // 2), "s3"),
    ]
    # s1 expiring leaves 3 (the later signals hold); s3 expiring leaves what s2 took
    assert c.holder.told == [1.0, 2.0, 3.0, 2.0]


def test_with_a_broker_a_signal_counts_once_the_broker_is_done_with_it() -> None:
    c = case(filled=True)
    c.run(signal(1, 1.0))
    assert c.holder.told == []
    c.followed.applied("s1")
    assert c.holder.told == [1.0]


def test_with_a_broker_a_took_answer_counts_before_the_fill_and_a_skip_never_does() -> None:
    c = case(filled=True)
    c.run(signal(1, 1.0), signal(2, 2.0), answer(3, "s1", "took"), answer(3, "s2", "skipped"))
    assert c.holder.told == [1.0]
    c.followed.applied("s1")
    c.followed.applied("s2")
    assert c.holder.told == [1.0]
    c.followed.applied("never-made")


def test_a_signal_replaced_before_its_fill_is_never_held_unless_taken() -> None:
    c = case(filled=True)
    c.run(signal(1, 1.0), signal(2, 2.0))
    c.followed.applied("s2")  # the broker filled only the later signal
    assert c.holder.told == [2.0]


def test_the_time_to_answer_is_positive() -> None:
    w = world()
    with pytest.raises(InvalidValueError, match="time to answer must be positive"):
        Followed(
            "trend", Holder(), w.bus, w.engine.scheduler, filled=False, within=0, now=w.clock.now
        )


@dataclass
class Lagging:
    """A live engine whose clock is moved by hand, as a wall clock runs past its alarms."""

    clock: SimulatedClock
    engine: LiveEngine
    bus: MessageBus
    holder: Holder
    expired: list[SignalExpired]

    def at(self, ts: int) -> None:
        self.clock.advance_to(Timestamp(ts))


def lagging(within: int) -> Lagging:
    clock = SimulatedClock(Timestamp(0))
    bus = MessageBus(clock)
    engine = LiveEngine(clock, bus)
    holder = Holder()
    Followed("trend", holder, bus, engine.scheduler, filled=False, within=within, now=clock.now)
    expired: list[SignalExpired] = []
    bus.subscribe(SignalExpired, expired.append)
    return Lagging(clock, engine, bus, holder, expired)


def test_every_signal_past_its_deadline_expires_when_the_clock_is_found_far_ahead() -> None:
    """A stalled process, or a resume after a gap: two are waiting, 3 h to answer, now 10 h."""
    live = lagging(3 * HOUR)
    live.at(1 * HOUR)
    live.engine.receive(signal(1, 1.0))
    live.at(2 * HOUR)
    live.engine.receive(signal(2, 2.0))
    live.at(10 * HOUR)
    live.engine.tick()
    assert [(e.ts, e.signal) for e in live.expired] == [(4 * HOUR, "s1"), (5 * HOUR, "s2")]
    assert live.holder.told == [1.0, 2.0, 0.0]


def test_a_signal_made_as_an_older_one_runs_out_does_not_move_the_older_ones_alarm() -> None:
    """A signal made by an alarm just after the older one's deadline, the clock a moment late."""
    live = lagging(1 * HOUR)
    live.engine.scheduler.claim("bars")
    live.engine.scheduler.every(Timestamp(2 * HOUR), HOUR, "bars")
    live.bus.subscribe(
        AlarmFired,
        lambda a: live.bus.publish(signal(a.ts // HOUR, 2.0)) if a.key == "bars" else None,
    )
    live.at(1 * HOUR)
    live.engine.receive(signal(1, 1.0))
    live.at(2 * HOUR + 1000)
    live.engine.tick()
    assert [(e.ts, e.signal) for e in live.expired] == [(2 * HOUR, "s1")]
    assert live.holder.told == [1.0, 2.0]


def test_a_signal_made_after_its_own_deadline_expires_as_of_the_deadline() -> None:
    """The clock is found 2 h on from a signal given 1 h to be answered: no alarm in the past."""
    live = lagging(1 * HOUR)
    live.at(3 * HOUR)
    live.engine.receive(signal(1, 1.0))
    live.engine.tick()
    assert [(e.ts, e.signal) for e in live.expired] == [(2 * HOUR, "s1")]
    assert live.holder.told == [1.0, 0.0]
