# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

from collections.abc import Iterable

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.support.world import END, Crossed, points, toy_session

from heliograph.core.alarms import AlarmFired
from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.journal import JournalEntry, MemoryJournal, inputs, runs
from heliograph.core.messages import Message, RunEnded, Settled
from heliograph.core.time import NANOS_PER_MINUTE, Timestamp


def session(*segments: tuple[Iterable[Message], Timestamp | None]) -> list[JournalEntry]:
    """The toy session over each (feed, until) in turn; its journal."""
    journal = MemoryJournal()
    toy_session(journal, *segments)
    return journal.entries


def test_every_message_is_numbered_and_marked_with_its_origin() -> None:
    entries = session((points([(10, 100), (70, 110)]), END))
    assert [e.seq for e in entries] == list(range(1, len(entries) + 1))
    kinds = {(type(e.message).__name__, e.origin) for e in entries}
    assert kinds == {
        ("Point", "input"),
        ("AlarmFired", "engine"),
        ("Settled", "engine"),
        ("Bar", "derived"),
        ("Crossed", "derived"),
        ("RunEnded", "engine"),
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
    entries = session((points([(10, 100), (20, 120), (250, 99)]), END))
    times = [e.message.ts for e in entries]
    assert times == sorted(times)
    assert isinstance(entries[-2].message, Settled)
    assert isinstance(entries[-1].message, RunEnded)
    assert any(isinstance(e.message, AlarmFired) for e in entries)


@given(
    st.lists(st.tuples(st.integers(0, 700), st.integers(90, 120)), max_size=80),
    st.lists(st.integers(1, 719), max_size=3, unique=True),
)
def test_phase_1_gate_replaying_the_inputs_reproduces_the_journal(
    raw: list[tuple[int, int]], cuts: list[int]
) -> None:
    """Replay feeds each run's inputs back; every other entry must come out identical.

    The original session is split into several runs at random cut points, so
    the replay must stop where each run did.
    """
    feed = points(raw)
    bounds = [Timestamp(c * NANOS_PER_MINUTE) for c in sorted(cuts)] + [END]
    segments = []
    lower = -1
    for upper in bounds:
        segments.append(([p for p in feed if lower < p.ts <= upper], upper))
        lower = upper
    original = session(*segments)
    replayed = session(*runs(original))
    assert replayed == original
    assert list(inputs(replayed)) == feed


def test_a_run_cut_short_is_replayed_up_to_its_last_input() -> None:
    entries = session((points([(10, 100)]), END))
    cut = [e for e in entries if not isinstance(e.message, RunEnded)]
    ((feed, until),) = runs(cut)
    assert until is None
    assert len(feed) == 1


def test_a_message_the_journal_refuses_uses_no_sequence_number() -> None:
    class Refusing(MemoryJournal):
        def append(self, entry: JournalEntry) -> None:
            if isinstance(entry.message, Crossed):
                raise ValueError("cannot record")
            super().append(entry)

    journal = Refusing()
    bus = MessageBus(SimulatedClock(Timestamp(5)), journal)
    bus.publish(Settled(Timestamp(1)))
    with pytest.raises(ValueError, match="cannot record"):
        bus.publish(Crossed(Timestamp(2), 1.0))
    bus.publish(Settled(Timestamp(3)))
    assert [e.seq for e in journal.entries] == [1, 2]
