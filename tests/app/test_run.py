# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A backtest from its configuration: journalled, recording what it read, and replayable."""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from heliograph.app.config import ConfigError, parse_config
from heliograph.app.data import download_mounts, publish_mounts
from heliograph.app.run import JOURNALLED, replay_backtest, run_backtest
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Message, Point, RunEnded
from heliograph.core.sources import (
    Capabilities,
    Health,
    HistoryRequest,
    SourceUnavailableError,
    Subscription,
    Throttle,
)
from heliograph.core.time import NANOS_PER_HOUR, from_iso
from heliograph.plugins.manifest import Manifest, NoParams, Params, Plugin, param
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import raw_rows, read_entries, read_header
from heliograph.plugins.store import Store
from tests.support.simulated import SimulatedTime

NOW = from_iso("2026-10-21T00:00:00Z")
START = from_iso("2026-10-01T00:00:00Z")
INSTRUMENT = """
[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"
"""
SPAN = "start = 2026-10-01T00:00:00Z\nend = 2026-10-01T03:00:00Z\n"
HOURLY = '\n[[streams]]\nmount = "btc"\nbars = "1h"\n'


class Failing(Plugin[NoParams]):
    """A data source that sends one trade, then fails."""

    manifest = Manifest(kind="data_sources", name="failing", version="1", summary="Fails")
    closed: ClassVar[list[bool]] = []

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(history=frozenset({"trades"}))

    def bind(self, throttle: Throttle) -> None:
        pass

    async def history(self, request: HistoryRequest) -> AsyncIterator[Message]:
        at = request.start
        yield Point(
            instrument=request.instrument, event_time=at, received_at=at, values={"price": 1.0}
        )
        raise SourceUnavailableError("the exchange went away")

    async def subscribe(self, subscriptions: Sequence[Subscription]) -> AsyncIterator[Message]:
        return
        yield

    async def health_check(self) -> Health:
        return Health(ok=True)

    async def close(self) -> None:
        Failing.closed.append(True)


@pytest.fixture
def registry() -> Registry:
    registry = Registry()
    registry.discover("data_sources")
    registry.register(Failing)
    Failing.closed.clear()
    return registry


def run(text: str, registry: Registry, journal: Path) -> Any:
    time = SimulatedTime(start=NOW)
    return run_backtest(parse_config(text), registry, journal, time.clock, time.advance)


def trades_csv(tmp_path: Path) -> Path:
    """Five trades over three hours."""
    path = tmp_path / "btc.csv"
    rows = ["00:10", "00:50", "01:05", "02:30", "02:59"]
    prices = [100, 104, 101, 107, 103]
    path.write_text(
        "time,price,size\n"
        + "".join(f"2026-10-01T{t}:00Z,{p},1\n" for t, p in zip(rows, prices, strict=True))
    )
    return path


def file_config(path: Path, *extra: str) -> str:
    return (
        f'[mounts.btc]\nsource = "file"\nparams = {{ path = {str(path)!r} }}\n'
        f'kind = "trades"\n{SPAN}{INSTRUMENT}' + "".join(extra)
    )


def published(tmp_path: Path, registry: Registry) -> tuple[str, Store]:
    """The CSV downloaded into a store and published; the configuration that reads it."""
    store = Store(tmp_path / "store")
    time = SimulatedTime(start=NOW)
    downloading = parse_config(file_config(trades_csv(tmp_path))).config
    asyncio.run(download_mounts(downloading, registry, store, time.clock, time.advance))
    snapshot = publish_mounts(downloading, store)
    params = f'{{ root = {str(store.root)!r}, snapshot = "{snapshot.id}", source = "file" }}'
    text = (
        f'[mounts.btc]\nsource = "store"\nparams = {params}\nkind = "trades"\n'
        f"{SPAN}{INSTRUMENT}{HOURLY}"
    )
    return text, store


def test_a_backtest_on_a_published_snapshot_journals_its_trades_and_bars(
    tmp_path: Path, registry: Registry
) -> None:
    text, store = published(tmp_path, registry)
    journal = tmp_path / "run.sqlite"
    summary = run(text, registry, journal)
    entries = list(read_entries(journal, JOURNALLED))
    points = [e.message for e in entries if isinstance(e.message, Point)]
    bars = [e.message for e in entries if isinstance(e.message, Bar)]
    assert len(points) == 5
    assert summary.delivered == 5  # the inputs
    # Three hourly bars; the last closes exactly at the run's end, and is known then.
    assert [(b.start - START) // NANOS_PER_HOUR for b in bars] == [0, 1, 2]
    assert [b.ts - START for b in bars] == [h * NANOS_PER_HOUR for h in (1, 2, 3)]
    assert [(b.open, b.high, b.low, b.close) for b in bars] == [
        (100, 104, 100, 104),
        (101, 101, 101, 101),
        (107, 107, 103, 103),
    ]
    assert isinstance(entries[-1].message, RunEnded)

    header = read_header(journal)
    snapshot = json.loads(header["snapshots"])
    assert snapshot == [store.publish(store.datasets()).id]
    assert header["config_digest"] == parse_config(text).digest
    assert json.loads(header["mounts"]) == {
        "btc": {
            "source": "store",
            "version": "1",
            "params": {"root": str(store.root), "snapshot": snapshot[0], "source": "file"},
            "live_delay": 0,
        }
    }
    assert (int(header["start"]), int(header["end"])) == (START, START + 3 * NANOS_PER_HOUR)
    assert header["until"] == header["end"]  # no live delay, no grace
    assert header["config_text"] == text
    assert header["status"] == "complete"
    assert header["kind"] == "backtest"
    assert header["heliograph"]


def test_a_replay_from_the_journal_alone_reproduces_it_byte_for_byte(
    tmp_path: Path, registry: Registry
) -> None:
    text, _ = published(tmp_path, registry)
    original = tmp_path / "run.sqlite"
    run(text, registry, original)
    replayed = replay_backtest(original, tmp_path / "replay.sqlite", registry)
    assert raw_rows(replayed.journal) == raw_rows(original)
    assert read_header(replayed.journal) == read_header(original)


def test_a_backtest_can_read_a_file_directly_and_records_no_snapshot(
    tmp_path: Path, registry: Registry
) -> None:
    journal = tmp_path / "run.sqlite"
    run(file_config(trades_csv(tmp_path), HOURLY), registry, journal)
    header = read_header(journal)
    assert json.loads(header["snapshots"]) == []
    assert json.loads(header["mounts"])["btc"]["source"] == "file"


def test_a_source_failing_mid_run_leaves_a_journal_saying_so(
    tmp_path: Path, registry: Registry
) -> None:
    text = f'[mounts.btc]\nsource = "failing"\nkind = "trades"\n{SPAN}{INSTRUMENT}'
    journal = tmp_path / "run.sqlite"
    with pytest.raises(SourceUnavailableError, match="went away"):
        run(text, registry, journal)
    assert read_header(journal)["status"].startswith("failed: SourceUnavailableError")
    assert Failing.closed == [True]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ('mode = "live"\n[mounts.btc]\nsource = "file"\nkind = "trades"\n' + INSTRUMENT, "is live"),
        (
            '[[components]]\nkind = "filters"\nplugin = "sma"\n',
            "components of kind filters are not run: a run runs",
        ),
        (
            '[[components]]\nkind = "filters"\nplugin = "sma"\n'
            f'[mounts.btc]\nsource = "file"\nkind = "trades"\n{SPAN}{INSTRUMENT}',
            "components of kind filters are not run: a run runs",
        ),
        ("", "at least one mount"),
    ],
)
def test_what_a_backtest_cannot_run_is_refused(
    tmp_path: Path, registry: Registry, text: str, reason: str
) -> None:
    with pytest.raises(ConfigError, match=reason):
        run(text, registry, tmp_path / "run.sqlite")
    assert not (tmp_path / "run.sqlite").exists()


def test_a_run_never_writes_over_a_journal(tmp_path: Path, registry: Registry) -> None:
    journal = tmp_path / "run.sqlite"
    journal.write_bytes(b"")
    with pytest.raises(InvalidValueError, match="exists; a run writes a new journal"):
        run(file_config(trades_csv(tmp_path)), registry, journal)
    text, _ = published(tmp_path, registry)
    original = tmp_path / "original.sqlite"
    run(text, registry, original)
    with pytest.raises(InvalidValueError, match="exists; a replay writes a new journal"):
        replay_backtest(original, journal, registry)


# -- found by review -------------------------------------------------------------------


def test_the_header_records_each_mounts_validated_parameters_and_plugin_version(
    tmp_path: Path, registry: Registry
) -> None:
    journal = tmp_path / "run.sqlite"
    run(file_config(trades_csv(tmp_path)), registry, journal)
    btc = json.loads(read_header(journal)["mounts"])["btc"]
    assert btc["source"] == "file"
    assert btc["version"] == "1"
    assert btc["params"]["values"] == ["price", "size"]  # a default, recorded
    assert btc["params"]["live_delay"] is None


def test_a_run_lasts_long_enough_for_the_last_trades_to_arrive_and_the_last_bar_to_close(
    tmp_path: Path, registry: Registry
) -> None:
    """A live delay of 5m and a grace of 10m: the 02:59 trade is known at 03:04, the
    last bar closes at 03:10; the run stops at 03:15."""
    path = trades_csv(tmp_path)
    text = file_config(path, '\n[[streams]]\nmount = "btc"\nbars = "1h"\ngrace = "10m"\n')
    text = text.replace(f"path = {str(path)!r} }}", f'path = {str(path)!r}, live_delay = "5m" }}')
    journal = tmp_path / "run.sqlite"
    summary = run(text, registry, journal)
    assert summary.delivered == 5
    bars = [e.message for e in read_entries(journal, JOURNALLED) if isinstance(e.message, Bar)]
    assert [(b.start - START) // NANOS_PER_HOUR for b in bars] == [0, 1, 2]
    assert int(read_header(journal)["until"]) == START + 3 * NANOS_PER_HOUR + 15 * 60 * 10**9


def test_a_stream_on_an_instrument_two_mounts_deliver_is_refused() -> None:
    other = INSTRUMENT.replace("mounts.btc", "mounts.other")
    text = (
        f'[mounts.btc]\nsource = "file"\nkind = "trades"\n{SPAN}{INSTRUMENT}'
        f'[mounts.other]\nsource = "file"\nkind = "trades"\n{SPAN}{other}{HOURLY}'
    )
    with pytest.raises(ConfigError, match="mounts btc, other all deliver kraken:BTC/USD"):
        parse_config(text)


def test_trades_without_a_size_have_a_stream_with_no_volume(
    tmp_path: Path, registry: Registry
) -> None:
    path = tmp_path / "btc.csv"
    path.write_text("time,price\n2026-10-01T00:10:00Z,100\n2026-10-01T00:20:00Z,102\n")
    params = f'{{ path = {str(path)!r}, values = ["price"] }}'
    text = (
        f'[mounts.btc]\nsource = "file"\nparams = {params}\nkind = "trades"\n{SPAN}{INSTRUMENT}'
        '\n[[streams]]\nmount = "btc"\nbars = "1h"\nvolume = ""\n'
    )
    journal = tmp_path / "run.sqlite"
    run(text, registry, journal)
    (bar,) = [e.message for e in read_entries(journal, JOURNALLED) if isinstance(e.message, Bar)]
    assert (bar.open, bar.close, bar.volume) == (100, 102, 0)


def test_a_failed_run_does_not_replay(tmp_path: Path, registry: Registry) -> None:
    text = f'[mounts.btc]\nsource = "failing"\nkind = "trades"\n{SPAN}{INSTRUMENT}'
    journal = tmp_path / "run.sqlite"
    with pytest.raises(SourceUnavailableError):
        run(text, registry, journal)
    with pytest.raises(InvalidValueError, match=r"did not finish \(failed"):
        replay_backtest(journal, tmp_path / "replay.sqlite", registry)
    assert not (tmp_path / "replay.sqlite").exists()


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("UPDATE header SET value = 'x' WHERE key = 'config_digest'", "does not match its digest"),
        ("DELETE FROM header WHERE key = 'until'", "header lacks until"),
        ("UPDATE header SET value = 'live' WHERE key = 'kind'", "not the journal of a backtest"),
    ],
)
def test_a_journal_whose_header_cannot_be_trusted_does_not_replay(
    tmp_path: Path, registry: Registry, change: str, reason: str
) -> None:
    journal = tmp_path / "run.sqlite"
    run(file_config(trades_csv(tmp_path), HOURLY), registry, journal)
    db = sqlite3.connect(journal)
    with db:
        db.execute(change)
    db.close()
    with pytest.raises(InvalidValueError, match=reason):
        replay_backtest(journal, tmp_path / "replay.sqlite", registry)


class DatedParams(Params):
    noted: datetime = param(..., description="A date-time, as TOML writes one")


class Dated(Plugin[DatedParams]):
    """A data source with a date-time parameter: one trade at the request's start."""

    manifest = Manifest(
        kind="data_sources", name="dated", version="1", summary="Dated", params=DatedParams
    )

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(history=frozenset({"trades"}))

    def bind(self, throttle: Throttle) -> None:
        pass

    async def history(self, request: HistoryRequest) -> AsyncIterator[Message]:
        at = request.start
        values = {"price": 1.0}
        yield Point(instrument=request.instrument, event_time=at, received_at=at, values=values)

    async def subscribe(self, subscriptions: Sequence[Subscription]) -> AsyncIterator[Message]:
        return
        yield

    async def health_check(self) -> Health:
        return Health(ok=True)

    async def close(self) -> None:
        pass


def test_a_configuration_with_typed_parameters_replays_from_its_exact_text(
    tmp_path: Path, registry: Registry
) -> None:
    """A TOML date-time parameter would not survive a JSON form; the text does."""
    registry.register(Dated)
    params = "{ noted = 2026-10-01T00:00:00+02:00 }"
    text = f'[mounts.btc]\nsource = "dated"\nparams = {params}\nkind = "trades"\n{SPAN}{INSTRUMENT}'
    journal = tmp_path / "run.sqlite"
    run(text, registry, journal)
    header = read_header(journal)
    assert header["config_text"] == text
    assert json.loads(header["mounts"])["btc"]["params"]["noted"] == "2026-10-01T00:00:00+02:00"
    replayed = replay_backtest(journal, tmp_path / "replay.sqlite", registry)
    assert raw_rows(replayed.journal) == raw_rows(journal)


class ClosesBadly(Dated):
    """Delivers its trade and ends, then fails to close."""

    manifest = Manifest(
        kind="data_sources", name="closes_badly", version="1", summary="x", params=DatedParams
    )

    async def close(self) -> None:
        Failing.closed.append(False)
        raise OSError("socket already gone")


def test_a_source_that_fails_to_close_does_not_leave_the_others_open(
    tmp_path: Path, registry: Registry
) -> None:
    registry.register(ClosesBadly)
    eth = INSTRUMENT.replace("mounts.btc", "mounts.eth").replace("BTC/USD", "ETH/USD")
    noted = "params = { noted = 2026-10-01T00:00:00Z }\n"
    text = (
        f'[mounts.btc]\nsource = "closes_badly"\n{noted}kind = "trades"\n{SPAN}{INSTRUMENT}'
        f'[mounts.eth]\nsource = "failing"\nkind = "trades"\n{SPAN}{eth}'
    )
    with pytest.raises(SourceUnavailableError, match="went away"):  # the run's own error wins
        run(text, registry, tmp_path / "run.sqlite")
    assert sorted(Failing.closed) == [False, True]
    Failing.closed.clear()
    alone = f'[mounts.btc]\nsource = "closes_badly"\n{noted}kind = "trades"\n{SPAN}{INSTRUMENT}'
    with pytest.raises(OSError, match="socket already gone"):  # no run error: the close's
        run(alone, registry, tmp_path / "alone.sqlite")


def test_a_mount_known_late_still_builds_whole_bars_from_its_trades(
    tmp_path: Path, registry: Registry
) -> None:
    """Trades known 5 minutes late and no grace: the 02:59 trade, known at 03:04, is still
    in the 02:00 bar, which closes at 03:05 (it once closed at 03:00 and lost it)."""
    path = trades_csv(tmp_path)
    text = file_config(path, HOURLY)
    text = text.replace(f"path = {str(path)!r} }}", f'path = {str(path)!r}, live_delay = "5m" }}')
    journal = tmp_path / "run.sqlite"
    run(text, registry, journal)
    bars = [e.message for e in read_entries(journal, JOURNALLED) if isinstance(e.message, Bar)]
    assert [b.close for b in bars] == [104, 101, 103]
    assert [(b.ts - b.end) // 60_000_000_000 for b in bars] == [5, 5, 5]


def test_an_indicator_reading_mounts_known_after_different_delays_is_refused_at_the_start(
    tmp_path: Path, registry: Registry
) -> None:
    """BTC known at once, ETH five minutes late: a node reading both hourly streams would
    read each ETH bar against the next BTC one. Only the run knows the sources' delays."""
    registry.discover("indicators")
    path = trades_csv(tmp_path)
    eth = INSTRUMENT.replace("mounts.btc", "mounts.eth").replace("BTC/USD", "ETH/USD")
    text = (
        file_config(path, HOURLY)
        + '\n[mounts.eth]\nsource = "file"\n'
        + f'params = {{ path = {str(path)!r}, live_delay = "5m" }}\n'
        + f'kind = "trades"\n{SPAN}{eth}'
        + '\n[[streams]]\nmount = "eth"\nbars = "1h"\n'
        + '\n[[components]]\nkind = "indicators"\nplugin = "atr"\nname = "both"\n'
        + 'inputs = ["btc@1h.high", "btc@1h.low", "eth@1h.close"]\n'
    )
    journal = tmp_path / "run.sqlite"
    with pytest.raises(ConfigError, match=r"btc@1h 0s, eth@1h 5m"):
        run(text, registry, journal)
    assert not journal.exists()


def test_a_journal_from_before_bars_waited_for_delays_replays_as_it_ran(
    tmp_path: Path, registry: Registry
) -> None:
    """A journal whose header names no bar rules (before D35) is replayed with the old
    ones, under which the 02:59 trade, known at 03:04, missed its 03:00 close."""
    path = trades_csv(tmp_path)
    text = file_config(path, HOURLY)
    text = text.replace(f"path = {str(path)!r} }}", f'path = {str(path)!r}, live_delay = "5m" }}')
    journal = tmp_path / "run.sqlite"
    run(text, registry, journal)
    assert read_header(journal)["bars"] == "2"
    with sqlite3.connect(journal) as db:  # as an older Heliograph wrote it
        db.execute("DELETE FROM header WHERE key = 'bars'")
    db.close()
    replayed = replay_backtest(journal, tmp_path / "replay.sqlite", registry)
    bars = [
        e.message for e in read_entries(replayed.journal, JOURNALLED) if isinstance(e.message, Bar)
    ]
    assert [b.close for b in bars] == [104, 101, 107]


def test_a_mount_that_delivers_nothing_fails_the_run(tmp_path: Path, registry: Registry) -> None:
    """Trades in milliseconds read as microseconds land in 1970: nothing in the span. A run
    on nothing would look like a quiet market; it fails, saying which mount."""
    path = tmp_path / "btc.csv"
    path.write_text("time,price,size\n1791000000000,100,1\n1791000060000,101,1\n")
    params = f'{{ path = {str(path)!r}, time_unit = "us" }}'
    text = (
        f'[mounts.btc]\nsource = "file"\nparams = {params}\nkind = "trades"\n'
        f"{SPAN}{INSTRUMENT}{HOURLY}"
    )
    journal = tmp_path / "run.sqlite"
    with pytest.raises(InvalidValueError, match=r"mounts\.btc delivered nothing between"):
        run(text, registry, journal)
    assert read_header(journal)["status"].startswith("failed: InvalidValueError: mounts.btc")


def test_a_mount_whose_quiet_span_is_expected_may_deliver_nothing(
    tmp_path: Path, registry: Registry
) -> None:
    path = tmp_path / "btc.csv"
    path.write_text("time,price,size\n1791000000000,100,1\n")
    params = f'{{ path = {str(path)!r}, time_unit = "us" }}'
    text = (
        f'[mounts.btc]\nsource = "file"\nparams = {params}\nkind = "trades"\n'
        f"allow_empty = true\n{SPAN}{INSTRUMENT}{HOURLY}"
    )
    journal = tmp_path / "run.sqlite"
    run(text, registry, journal)
    assert read_header(journal)["status"] == "complete"
