# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

from collections.abc import Iterable
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from heliograph.core.alarms import AlarmFired
from heliograph.core.errors import InvalidValueError
from heliograph.core.journal import runs
from heliograph.core.messages import Bar, Message, Point, RunEnded, Settled
from heliograph.core.time import NANOS_PER_HOUR, Timestamp
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import (
    SqliteJournal,
    SqliteJournalParams,
    raw_rows,
    read_entries,
    read_header,
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
