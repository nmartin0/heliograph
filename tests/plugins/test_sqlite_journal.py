# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from heliograph.core.alarms import AlarmFired
from heliograph.core.bars import TimeBars
from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.engine import BacktestEngine
from heliograph.core.errors import InvalidValueError
from heliograph.core.journal import runs
from heliograph.core.messages import Bar, Instrument, Message, Point, RunEnded, Settled
from heliograph.core.streams import BarSpec, StreamSpec
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MINUTE, Timestamp
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import (
    SqliteJournal,
    SqliteJournalParams,
    raw_rows,
    read_entries,
    read_header,
)

BTC = Instrument(symbol="BTC/USD", venue="kraken", asset_class="crypto", quote_currency="USD")


@dataclass(frozen=True, slots=True)
class Crossed:
    ts: Timestamp
    close: float


TYPES = (Point, Bar, AlarmFired, Settled, RunEnded, Crossed)
END = Timestamp(12 * NANOS_PER_HOUR)


def session(path: Path, *segments: tuple[Iterable[Message], Timestamp | None]) -> None:
    """Bars and a toy strategy over each (feed, until) in turn, journalled to ``path``."""
    params = SqliteJournalParams(path=str(path), batch=7)
    with SqliteJournal(params, header={"run": "test"}) as journal:
        clock = SimulatedClock(Timestamp(0))
        bus = MessageBus(clock, journal)
        engine = BacktestEngine(clock, bus)
        for size in ("1h", "4h"):
            TimeBars(StreamSpec(instrument=BTC, bars=BarSpec.of(size)), bus, engine.scheduler)

        def strategy(bar: Bar) -> None:
            if bar.close > 105:
                bus.publish(Crossed(bar.ts, bar.close))

        bus.subscribe(Bar, strategy)
        for feed, until in segments:
            engine.run(feed, until=until)


def points(raw: list[tuple[int, int]]) -> list[Point]:
    out = []
    for minute, price in sorted(raw):
        ts = Timestamp(minute * NANOS_PER_MINUTE)
        values = {"price": price, "size": 0.5}
        out.append(Point(instrument=BTC, event_time=ts, received_at=ts, values=values))
    return out


def test_entries_read_back_equal_to_what_was_written(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"
    session(path, (points([(10, 100), (70, 110), (300, 104)]), END))
    entries = list(read_entries(path, TYPES))
    assert [e.seq for e in entries] == list(range(1, len(entries) + 1))
    assert {type(e.message) for e in entries} == set(TYPES)
    header = read_header(path)
    assert (header["format"], header["status"], header["run"]) == ("1", "complete", "test")
    assert header["msgspec"]


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=40)
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
