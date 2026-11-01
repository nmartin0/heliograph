# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The inbox a person's answers travel through to a live session."""

import json
from pathlib import Path

import pytest

from heliograph.app.answers import Answers, hand_over, inbox_for, submit
from heliograph.core.answers import SignalAnswer
from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.engine import LiveEngine
from heliograph.core.errors import HeliographError, InvalidValueError
from heliograph.core.journal import JournalEntry, MemoryJournal
from heliograph.core.pipeline import Signal
from heliograph.core.time import Timestamp
from tests.support.world import BTC


class Quiet:
    """A sleep that does not wait (the tests call ``poll`` themselves)."""

    async def __call__(self, seconds: float) -> None:
        return None


def signal(id_: str = "s1") -> Signal:
    return Signal(
        ts=Timestamp(1),
        id=id_,
        strategy="trend",
        instrument=BTC,
        side="long",
        target=1.0,
        held=0.0,
        price=100.0,
        forecast=5.0,
    )


def session(tmp_path: Path) -> tuple[SimulatedClock, Answers, MemoryJournal, MessageBus, Path]:
    clock = SimulatedClock(Timestamp(100))
    journal = MemoryJournal()
    bus = MessageBus(clock, journal)
    inbox = inbox_for(tmp_path / "live.sqlite")
    answers = Answers(LiveEngine(clock, bus), clock, inbox, Quiet())
    return clock, answers, journal, bus, inbox


def answered(journal: MemoryJournal) -> list[JournalEntry]:
    return [e for e in journal.entries if isinstance(e.message, SignalAnswer)]


def test_the_inbox_sits_beside_the_journal(tmp_path: Path) -> None:
    assert inbox_for(tmp_path / "live.sqlite") == tmp_path / "live.sqlite.answers"


def test_a_session_makes_its_inbox(tmp_path: Path) -> None:
    *_, inbox = session(tmp_path)
    assert inbox.is_dir()


def test_an_answer_left_in_the_inbox_is_journalled_as_an_input_stamped_on_arrival(
    tmp_path: Path,
) -> None:
    clock, answers, journal, bus, inbox = session(tmp_path)
    bus.publish(signal(), origin="input")
    left = submit(inbox, clock, "s1", "took", price=101.5, quantity=0.5)
    clock.advance_to(Timestamp(250))
    assert answers.poll() == 1
    (entry,) = answered(journal)
    assert entry.origin == "input"
    assert entry.message == SignalAnswer(
        ts=Timestamp(250), signal="s1", verdict="took", price=101.5, quantity=0.5
    )
    assert not left.exists()


def test_answers_are_taken_oldest_first_and_a_later_one_replaces_an_earlier(
    tmp_path: Path,
) -> None:
    clock, answers, journal, bus, inbox = session(tmp_path)
    bus.publish(signal(), origin="input")
    submit(inbox, clock, "s1", "took")
    clock.advance_to(Timestamp(101))
    submit(inbox, clock, "s1", "skipped")
    assert answers.poll() == 2
    assert [
        e.message.verdict for e in answered(journal) if isinstance(e.message, SignalAnswer)
    ] == [
        "took",
        "skipped",
    ]


def test_two_answers_in_one_instant_get_two_files(tmp_path: Path) -> None:
    clock, _, _, _, inbox = session(tmp_path)
    first = submit(inbox, clock, "s1", "took")
    second = submit(inbox, clock, "s1", "skipped")
    assert first != second
    assert sorted(p.name for p in inbox.glob("*.json")) == [first.name, second.name]
    assert not list(inbox.glob("*.tmp"))


def test_an_answer_to_a_signal_never_made_is_set_aside_with_its_reason(tmp_path: Path) -> None:
    clock, answers, journal, _, inbox = session(tmp_path)
    submit(inbox, clock, "ghost", "took")
    assert answers.poll() == 0
    assert not answered(journal)
    (kept,) = (inbox / "rejected").glob("*.json")
    assert "no signal ghost has been made" in (inbox / "rejected" / f"{kept.name}.why").read_text()
    assert not list(inbox.glob("*.json"))


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("not json", "Expecting value"),
        ("[1, 2]", "not an answer"),
        ('{"signal": "s1", "verdict": "took", "colour": "red"}', "not an answer"),
        ('{"signal": "s1", "verdict": "perhaps"}', "took or skipped"),
        ('{"signal": "s1", "verdict": "skipped", "price": 5}', "no price or quantity"),
        ('{"verdict": "took"}', "signal"),
    ],
)
def test_a_file_that_is_not_an_answer_is_set_aside_not_lost(
    tmp_path: Path, text: str, why: str
) -> None:
    _, answers, journal, bus, inbox = session(tmp_path)
    bus.publish(signal(), origin="input")
    (inbox / "00-bad.json").write_text(text)
    assert answers.poll() == 0
    assert not answered(journal)
    assert (inbox / "rejected" / "00-bad.json").read_text() == text
    assert why in (inbox / "rejected" / "00-bad.json.why").read_text()


def test_a_bad_file_does_not_hold_up_the_good_ones_after_it(tmp_path: Path) -> None:
    clock, answers, journal, bus, inbox = session(tmp_path)
    bus.publish(signal(), origin="input")
    (inbox / "00-bad.json").write_text("{")
    submit(inbox, clock, "s1", "skipped")
    assert answers.poll() == 1
    assert len(answered(journal)) == 1


def test_submitting_checks_the_answer_and_needs_an_inbox(tmp_path: Path) -> None:
    clock, _, _, _, inbox = session(tmp_path)
    with pytest.raises(InvalidValueError, match="price must be positive"):
        submit(inbox, clock, "s1", "took", price=-1.0)
    assert not list(inbox.iterdir())
    with pytest.raises(HeliographError, match="no live session has started with it"):
        submit(tmp_path / "nowhere.answers", clock, "s1", "took")


def test_the_file_holds_plain_json(tmp_path: Path) -> None:
    clock, _, _, _, inbox = session(tmp_path)
    left = submit(inbox, clock, "s1", "took", quantity=2.0)
    assert json.loads(left.read_text()) == {
        "signal": "s1",
        "verdict": "took",
        "price": None,
        "quantity": 2.0,
    }


def test_answers_unread_when_a_session_is_resumed_move_to_the_new_inbox(tmp_path: Path) -> None:
    clock = SimulatedClock(Timestamp(100))
    old, new = tmp_path / "old.sqlite", tmp_path / "new.sqlite"
    inbox_for(old).mkdir()
    submit(inbox_for(old), clock, "s1", "took")
    assert hand_over(old, new) == 1
    assert [p.name for p in inbox_for(old).glob("*.json")] == []
    assert len(list(inbox_for(new).glob("*.json"))) == 1


def test_an_answer_to_a_resumed_session_names_the_session_that_took_over(tmp_path: Path) -> None:
    clock = SimulatedClock(Timestamp(100))
    old, new = tmp_path / "old.sqlite", tmp_path / "new.sqlite"
    inbox_for(old).mkdir()
    hand_over(old, new)
    with pytest.raises(HeliographError, match=r"resumed; answer new\.sqlite instead"):
        submit(inbox_for(old), clock, "s1", "took")
    assert list(inbox_for(old).glob("*.json")) == []


def test_there_is_nothing_to_hand_over_from_a_session_that_never_had_an_inbox(
    tmp_path: Path,
) -> None:
    assert hand_over(tmp_path / "old.sqlite", tmp_path / "new.sqlite") == 0
