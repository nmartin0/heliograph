# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from heliograph.core.alarms import AlarmFired
from heliograph.core.errors import InvalidValueError
from heliograph.core.journal import JournalEntry, runs
from heliograph.core.messages import Bar, Message, Point, RunEnded, Settled
from heliograph.core.time import NANOS_PER_HOUR, Timestamp
from heliograph.plugins import sqlite_journal
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import (
    SqliteJournal,
    SqliteJournalParams,
    raw_rows,
    read_entries,
    read_header,
    tidy,
)
from tests.support.world import BTC, END, Crossed, toy_session
from tests.support.world import points as btc_points

TYPES = (Point, Bar, AlarmFired, Settled, RunEnded, Crossed)


def session(path: Path, *segments: tuple[Iterable[Message], Timestamp | None]) -> None:
    """The toy session, journalled to ``path`` in batches of seven."""
    params = SqliteJournalParams(path=str(path), batch=7)
    with SqliteJournal(params, header={"run": "test"}) as journal:
        toy_session(journal, *segments)


def points(raw: list[tuple[int, int]]) -> list[Point]:
    return btc_points(raw, size=0.5)


def test_entries_read_back_equal_to_what_was_written(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"
    session(path, (points([(10, 100), (70, 110), (300, 104)]), END))
    entries = list(read_entries(path, TYPES))
    assert [e.seq for e in entries] == list(range(1, len(entries) + 1))
    assert {type(e.message) for e in entries} == set(TYPES)
    header = read_header(path)
    assert (header["format"], header["status"], header["run"]) == ("1", "complete", "test")
    assert header["msgspec"]


# No deadline: each example writes and reads two SQLite files, and disk
# latency on a loaded machine is no defect (an example once took 271 ms).
@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=40, deadline=None
)
@given(st.lists(st.tuples(st.integers(0, 700), st.integers(90, 120)), max_size=60))
def test_phase_1_gate_a_replayed_journal_is_identical_byte_for_byte(
    tmp_path: Path, raw: list[tuple[int, int]]
) -> None:
    folder = tmp_path / str(len(list(tmp_path.iterdir())))
    folder.mkdir()
    first, second = folder / "first.sqlite", folder / "second.sqlite"
    session(first, (points(raw), END))
    session(second, *runs(read_entries(first, TYPES)))
    assert raw_rows(second) == raw_rows(first)


def test_a_journal_is_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"
    session(path, (points([(10, 100)]), END))
    with pytest.raises(InvalidValueError, match="already exists"):
        SqliteJournal(SqliteJournalParams(path=str(path)))


def test_reading_refuses_types_it_was_not_given(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"
    session(path, (points([(10, 100)]), END))
    with pytest.raises(InvalidValueError, match=r"which was not given"):
        list(read_entries(path, (Point,)))


def test_a_missing_journal_is_reported_when_asked_for(tmp_path: Path) -> None:
    with pytest.raises(InvalidValueError, match="no journal at"):
        read_entries(tmp_path / "absent.sqlite", TYPES)


def test_an_empty_file_or_a_folder_is_not_taken_for_a_new_journal(tmp_path: Path) -> None:
    (tmp_path / "empty.sqlite").touch()
    with pytest.raises(InvalidValueError, match="already exists"):
        SqliteJournal(SqliteJournalParams(path=str(tmp_path / "empty.sqlite")))
    with pytest.raises(InvalidValueError, match=r"already exists|cannot create"):
        SqliteJournal(SqliteJournalParams(path=str(tmp_path)))


def test_reserved_header_keys_are_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidValueError, match="reserved"):
        SqliteJournal(SqliteJournalParams(path=str(tmp_path / "j.sqlite")), {"format": "999"})


def test_a_run_that_raised_is_marked_failed(tmp_path: Path) -> None:
    path = tmp_path / "j.sqlite"
    with pytest.raises(RuntimeError), SqliteJournal(SqliteJournalParams(path=str(path))):
        raise RuntimeError("boom")
    assert read_header(path)["status"] == "failed: RuntimeError: boom"


def test_input_bars_with_integer_prices_replay_byte_for_byte(tmp_path: Path) -> None:
    bar = Bar(
        instrument=BTC,
        start=Timestamp(0),
        end=Timestamp(NANOS_PER_HOUR),
        open=100,
        high=110,
        low=90,
        close=105,
        volume=3,
    )
    first, second = tmp_path / "a.sqlite", tmp_path / "b.sqlite"
    session(first, ([bar], END))
    session(second, *runs(read_entries(first, TYPES)))
    assert raw_rows(second) == raw_rows(first)


def test_the_journal_is_an_installed_plug_in() -> None:
    registry = Registry()
    registry.discover("journals")
    assert registry.get("journals", "sqlite") is SqliteJournal


def test_a_finished_journal_is_one_file_and_reading_it_leaves_nothing_beside_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run.sqlite"
    session(path, (points([(10, 100), (70, 110)]), END))
    list(read_entries(path, TYPES))
    read_header(path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run.sqlite"]


def test_a_journal_closes_while_another_reader_has_it_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader that stays open outlasts the close's tidying: the journal is whole in
    WAL mode, and becomes one file when ``tidy`` finds it alone."""
    monkeypatch.setattr(sqlite_journal, "TIDYING", 0.1)
    path = tmp_path / "run.sqlite"
    with SqliteJournal(SqliteJournalParams(path=str(path)), header={"run": "test"}) as journal:
        reader = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        reader.execute("SELECT count(*) FROM header").fetchone()
        toy_session(journal, (points([(10, 100)]), END))
    assert read_header(path)["status"] == "complete"
    assert tidy(path, timeout=0.1) is False  # still open
    reader.close()
    assert list(read_entries(path, TYPES))
    assert tidy(path) is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run.sqlite"]


def test_a_journal_read_as_it_closes_still_ends_as_one_file(tmp_path: Path) -> None:
    """A chart reading a running backtest has the journal open as it closes: the close
    leaves WAL mode once the read is over, trying again until then."""
    path = tmp_path / "run.sqlite"
    opened, done = threading.Event(), threading.Event()

    def read() -> None:
        reader = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        reader.execute("SELECT count(*) FROM header").fetchall()
        opened.set()
        done.wait(0.2)  # a read that outlasts the close's first try
        reader.close()

    with SqliteJournal(SqliteJournalParams(path=str(path)), header={"run": "test"}) as journal:
        toy_session(journal, (points([(10, 100), (70, 110)]), END))
        reading = threading.Thread(target=read)
        reading.start()
        assert opened.wait(10)
    reading.join()
    assert read_header(path)["status"] == "complete"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run.sqlite"]


@pytest.mark.parametrize("name", ["a?b.sqlite", "a#b.sqlite", "a%20b.sqlite"])
def test_a_journal_named_with_uri_characters_is_read_as_itself(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    session(path, (points([(10, 100)]), END))
    assert read_header(path)["run"] == "test"
    assert sorted(p.name for p in tmp_path.iterdir()) == [name]


def test_a_file_that_is_not_a_journal_is_said_to_be_so(tmp_path: Path) -> None:
    text = tmp_path / "notes.sqlite"
    text.write_text("hello")
    empty = tmp_path / "empty.sqlite"
    sqlite3.connect(empty).close()
    for path in (text, empty):
        with pytest.raises(InvalidValueError, match="is not a journal"):
            read_header(path)
        with pytest.raises(InvalidValueError, match="is not a journal"):
            raw_rows(path)


class Unwritable:
    """Stands in for the database connection: every write of entries fails."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self.real = real

    def executemany(self, *args: object) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    def __getattr__(self, name: str) -> object:
        return getattr(self.real, name)


def test_entries_that_cannot_be_written_mark_the_journal_failed_and_say_so(
    tmp_path: Path,
) -> None:
    """A full disk at the last batch: the journal is closed, marked failed with why, and
    the error is raised, instead of a journal left 'running' with nothing said."""
    path = tmp_path / "run.sqlite"
    journal = SqliteJournal(SqliteJournalParams(path=str(path)), header={"run": "test"})
    journal._db = Unwritable(journal._db)  # type: ignore[assignment]
    journal.append(JournalEntry(1, "input", Settled(Timestamp(5))))
    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        journal.close()
    status = read_header(path)["status"]
    assert status.startswith("failed: its last entries could not be written: OperationalError")


def test_a_run_that_failed_keeps_its_own_error_when_its_entries_cannot_be_written(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run.sqlite"
    journal = SqliteJournal(SqliteJournalParams(path=str(path)), header={"run": "test"})
    journal._db = Unwritable(journal._db)  # type: ignore[assignment]
    journal.append(JournalEntry(1, "input", Settled(Timestamp(5))))
    journal.close("failed: ValueError: the source broke")  # no error of its own
    assert read_header(path)["status"] == (
        "failed: ValueError: the source broke (its last entries could not be written: "
        "OperationalError: database or disk is full)"
    )


def test_a_stamp_sqlite_cannot_hold_is_refused_when_the_entry_is_added(tmp_path: Path) -> None:
    journal = SqliteJournal(SqliteJournalParams(path=str(tmp_path / "run.sqlite")))
    with pytest.raises(InvalidValueError, match="SQLite holds 64-bit integers"):
        journal.append(JournalEntry(1, "input", Settled(Timestamp(2**63))))
    journal.close()


class Full(Unwritable):
    """No room even for the status."""

    def execute(self, sql: str, *args: Any) -> object:
        if sql.startswith("UPDATE"):
            raise sqlite3.OperationalError("disk I/O error")
        return self.real.execute(sql, *args)


def test_on_a_disk_too_full_for_the_status_a_failed_run_keeps_its_own_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run.sqlite"
    journal = SqliteJournal(SqliteJournalParams(path=str(path)), header={"run": "test"})
    journal._db = Full(journal._db)  # type: ignore[assignment]
    journal.append(JournalEntry(1, "input", Settled(Timestamp(5))))
    journal.close("failed: ValueError: the source broke")  # raises nothing of its own
    assert read_header(path)["status"] == "running"  # which a replay refuses
    other = SqliteJournal(SqliteJournalParams(path=str(tmp_path / "b.sqlite")))
    other._db = Full(other._db)  # type: ignore[assignment]
    other.append(JournalEntry(1, "input", Settled(Timestamp(5))))
    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        other.close()  # a completed run learns of it


def test_a_read_may_keep_some_types_and_a_span_of_known_times(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"
    session(path, (points([(10, 100), (70, 110), (300, 104)]), END))
    every = list(read_entries(path, TYPES))
    bars = list(read_entries(path, TYPES, only=(Bar,)))
    assert bars == [e for e in every if isinstance(e.message, Bar)]
    assert bars
    middle = every[len(every) // 2].message.ts
    later = list(read_entries(path, TYPES, start=middle))
    earlier = list(read_entries(path, TYPES, end=middle))
    assert later == [e for e in every if e.message.ts >= middle]
    assert earlier == [e for e in every if e.message.ts < middle]
    assert later
    assert earlier
    both = list(read_entries(path, TYPES, only=(Point, Settled), start=middle, end=middle + 1))
    assert both == [
        e for e in later if isinstance(e.message, Point | Settled) and e.message.ts == middle
    ]
