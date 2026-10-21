# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

from collections.abc import Iterable
from dataclasses import dataclass

from hypothesis import given
from hypothesis import strategies as st

from heliograph.core.alarms import AlarmFired
from heliograph.core.bars import TimeBars
from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.engine import BacktestEngine
from heliograph.core.journal import JournalEntry, MemoryJournal, inputs
from heliograph.core.messages import Bar, Instrument, Message, Point, Settled
from heliograph.core.streams import BarSpec, StreamSpec
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MINUTE, Timestamp

BTC = Instrument(symbol="BTC/USD", venue="kraken", asset_class="crypto", quote_currency="USD")


@dataclass(frozen=True, slots=True)
class Crossed:
    """What a toy strategy publishes when a bar closes above 105."""

    ts: Timestamp
    close: float


def session(feed: Iterable[Message]) -> list[JournalEntry]:
    """Run bars and a toy strategy over ``feed``; return the journal."""
    journal = MemoryJournal()
    clock = SimulatedClock(Timestamp(0))
    bus = MessageBus(clock, journal)
    engine = BacktestEngine(clock, bus)
    for size in ("1h", "4h"):
        TimeBars(StreamSpec(instrument=BTC, bars=BarSpec.of(size)), bus, engine.scheduler)

    def strategy(bar: Bar) -> None:
        if bar.close > 105:
            bus.publish(Crossed(bar.ts, bar.close))

    bus.subscribe(Bar, strategy)
    engine.run(feed, until=Timestamp(12 * NANOS_PER_HOUR))
    return journal.entries


def points(raw: list[tuple[int, int]]) -> list[Point]:
    out = []
    for minute, price in sorted(raw):
        ts = Timestamp(minute * NANOS_PER_MINUTE)
        values = {"price": price, "size": 1.0}
        out.append(Point(instrument=BTC, event_time=ts, received_at=ts, values=values))
    return out


def test_every_message_is_numbered_and_marked_with_its_origin() -> None:
    entries = session(points([(10, 100), (70, 110)]))
    assert [e.seq for e in entries] == list(range(1, len(entries) + 1))
    kinds = {(type(e.message).__name__, e.origin) for e in entries}
    assert kinds == {
        ("Point", "input"),
        ("AlarmFired", "engine"),
        ("Settled", "engine"),
        ("Bar", "derived"),
        ("Crossed", "derived"),
    }


def test_a_message_is_recorded_before_anyone_receives_it() -> None:
    journal = MemoryJournal()
    clock = SimulatedClock(Timestamp(0))
    bus = MessageBus(clock, journal)
    recorded_when_delivered: list[int] = []
    bus.subscribe(Settled, lambda s: recorded_when_delivered.append(len(journal.entries)))
    bus.publish(Settled(Timestamp(0)))
    assert recorded_when_delivered == [1]
    assert bus.sequence == 1


def test_the_journal_follows_delivery_order() -> None:
    entries = session(points([(10, 100), (20, 120), (250, 99)]))
    times = [e.message.ts for e in entries]
    assert times == sorted(times)
    assert isinstance(entries[-1].message, Settled)
    assert any(isinstance(e.message, AlarmFired) for e in entries)


@given(st.lists(st.tuples(st.integers(0, 700), st.integers(90, 120)), max_size=80))
def test_phase_1_gate_replaying_the_inputs_reproduces_the_journal(
    raw: list[tuple[int, int]],
) -> None:
    """Replay feeds only the inputs back; every other entry must come out identical."""
    original = session(points(raw))
    replayed = session(inputs(original))
    assert replayed == original
