# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The store: raw kept as given, cleaned by rule, published, and rebuilt exactly."""

import asyncio
import dataclasses
import hashlib
import json
import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from heliograph.app.bridge import HistoryFeed, SourceLoop
from heliograph.core.clock import SimulatedClock
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Instrument, Message, Point
from heliograph.core.sources import (
    Capabilities,
    HistoryRequest,
    SourceUnavailableError,
    Subscription,
)
from heliograph.core.time import NANOS_PER_MINUTE, Timestamp, from_iso
from heliograph.plugins.store import (
    Dataset,
    Download,
    Store,
    StoreError,
    StoreSource,
    StoreSourceParams,
    _audit,
    _build,
    _snapshot_id,
    canonical_json,
    clean,
)
from tests.support.sources import ScriptedSource, trade
from tests.support.world import BTC, ETH

MIN = NANOS_PER_MINUTE
NOW = from_iso("2026-10-21T12:00:00Z")
TRADE_CAPS = Capabilities(
    history=frozenset({"trades"}), live_delay=5 * 10**9, terms=frozenset({"attribution"})
)
BAR_CAPS = Capabilities(history=frozenset({"bars"}), bar_sizes=frozenset({MIN}))
TRADES = Dataset(source="feed", instrument=BTC, kind="trades")
BARS = Dataset(source="feed", instrument=BTC, kind="bars", bar_size=MIN)
ZERO, ONE = Timestamp(0), Timestamp(MIN)


def request(start: int = 0, end: int = 60, kind: str = "trades") -> HistoryRequest:
    """Minutes ``start`` to ``end``."""
    size = MIN if kind == "bars" else None
    return HistoryRequest(
        instrument=BTC,
        kind=kind,  # type: ignore[arg-type]
        start=Timestamp(start * MIN),
        end=Timestamp(end * MIN),
        bar_size=size,
    )


def download(
    store: Store,
    messages: Sequence[Message],
    wanted: HistoryRequest | None = None,
    *,
    at: int = 0,
    caps: Capabilities = TRADE_CAPS,
) -> Download:
    """What a source yields for ``wanted``, kept as a raw file made ``at`` seconds after NOW."""
    source = ScriptedSource(history_messages=messages, caps=caps)
    clock = SimulatedClock(Timestamp(NOW + at * 10**9))
    wanted = wanted or request()
    return asyncio.run(store.download("feed", {"exchange": "kraken"}, source, wanted, clock))


def priced(minute: float, price: float, **values: float) -> Point:
    at = Timestamp(int(minute * MIN))
    return Point(instrument=BTC, event_time=at, received_at=at, values={"price": price, **values})


def bar(minute: int, minutes: int = 1, low: float = 1.0) -> Bar:
    return Bar(
        instrument=BTC,
        start=Timestamp(minute * MIN),
        end=Timestamp((minute + minutes) * MIN),
        open=2,
        high=3,
        low=low,
        close=2,
        volume=1,
    )


def prices(store: Store, dataset: Dataset = TRADES) -> list[float]:
    return [row["price"] for row in clean(store.downloads(dataset)).table.to_pylist()]


# -- raw -------------------------------------------------------------------------------


def test_a_download_keeps_what_the_source_yielded_in_its_order_and_what_was_asked(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    yielded = [trade(3, 103), trade(1, 101), trade(90, 190)]  # out of order, one outside
    raw = download(store, yielded, at=7)
    assert raw.path.parent == tmp_path / "raw/feed/kraken%3ABTC%2FUSD/trades"
    assert raw.path.name.startswith("20261021T120007.000000000Z-")
    rows = pq.read_table(raw.path).to_pylist()
    assert rows == [
        {"event_time": 3 * MIN, "price": 103.0, "size": 1.0},
        {"event_time": 1 * MIN, "price": 101.0, "size": 1.0},
        {"event_time": 90 * MIN, "price": 190.0, "size": 1.0},
    ]
    assert (raw.start, raw.end, raw.rows) == (0, 60 * MIN, 3)
    assert raw.downloaded_at == NOW + 7 * 10**9
    assert raw.live_delay == 5 * 10**9
    assert raw.terms == frozenset({"attribution"})
    assert raw.params == {"exchange": "kraken"}
    assert store.downloads(TRADES) == [raw]
    assert store.datasets() == [TRADES]


def test_an_empty_download_is_kept_it_says_the_interval_had_nothing(tmp_path: Path) -> None:
    raw = download(Store(tmp_path), [])
    assert raw.rows == 0
    assert pq.read_table(raw.path).num_rows == 0


@pytest.mark.parametrize(
    ("sent", "reason"),
    [
        (
            Point(instrument=ETH, event_time=ZERO, received_at=ZERO, values={"price": 1.0}),
            "asked for kraken:BTC/USD, the source sent kraken:ETH/USD",
        ),
        (bar(0), "asked for trades, the source sent a Bar"),
    ],
)  # fmt: skip
def test_a_download_of_data_not_asked_for_is_refused_and_nothing_is_kept(
    tmp_path: Path, sent: Message, reason: str
) -> None:
    store = Store(tmp_path)
    with pytest.raises(InvalidValueError, match=reason):
        download(store, [trade(1), sent])
    assert store.downloads(TRADES) == []


def test_a_download_whose_source_fails_keeps_nothing(tmp_path: Path) -> None:
    store = Store(tmp_path)
    source = ScriptedSource(
        history_messages=[trade(1), trade(2)],
        fail_after=1,
        failure=SourceUnavailableError("rate limited"),
    )
    with pytest.raises(SourceUnavailableError):
        asyncio.run(store.download("feed", {}, source, request(), SimulatedClock(NOW)))
    assert store.downloads(TRADES) == []
    assert source.streams_closed == 1
    assert not list(tmp_path.rglob("*.partial"))


def test_a_raw_file_that_was_edited_is_refused(tmp_path: Path) -> None:
    store = Store(tmp_path)
    raw = download(store, [trade(1)])
    raw.path.write_bytes(raw.path.read_bytes() + b"\0")
    with pytest.raises(StoreError, match="has changed since it was downloaded"):
        store.downloads(TRADES)


# -- clean -------------------------------------------------------------------------------


def test_a_later_download_replaces_what_an_earlier_said_about_its_interval(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    download(store, [priced(m, 100 + m) for m in range(0, 60, 10)], request(0, 60), at=0)
    download(store, [priced(25, 999)], request(20, 40), at=1)  # a correction of 20-40
    cleaned = clean(store.downloads(TRADES))
    assert [row["price"] for row in cleaned.table.to_pylist()] == [100, 110, 999, 140, 150]
    assert (cleaned.raw_rows, cleaned.superseded, cleaned.quarantine) == (7, 2, [])


def test_downloads_apply_in_the_order_they_were_made_not_the_order_given(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    late = download(store, [priced(5, 2)], at=9)
    early = download(store, [priced(5, 1)], at=1)
    assert prices(store) == [2]
    assert clean([late, early]).table.to_pylist() == clean([early, late]).table.to_pylist()


def test_clean_rows_are_in_time_order_and_trades_at_one_instant_keep_theirs(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    download(store, [priced(5, 3), priced(1, 1), priced(5, 4), priced(2, 2)])
    assert prices(store) == [1, 2, 3, 4]


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (priced(90, 1), "outside the download's interval"),
        (priced(1, 0), "price 0.0 is not positive"),
        (priced(1, -2), "price -2.0 is not positive"),
        (priced(1, 1, size=-1), "size -1.0 is negative"),
        (
            Point(instrument=BTC, event_time=ONE, received_at=ONE, values={"bid": 1.0}),
            "has no price",
        ),
    ],
)  # fmt: skip
def test_a_trade_that_fails_a_check_is_quarantined_with_where_it_came_from_and_why(
    tmp_path: Path, row: Point, reason: str
) -> None:
    store = Store(tmp_path)
    raw = download(store, [priced(0, 5), row])
    cleaned = clean(store.downloads(TRADES))
    assert prices(store) == [5]
    (quarantined,) = cleaned.quarantine
    assert (quarantined.download, quarantined.row) == (raw.id, 2)
    assert reason in quarantined.reason
    assert json.loads(quarantined.data)["event_time"] == row.event_time


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([bar(0), bar(1, minutes=2)], "lasts 2m, not the dataset's 1m"),
        ([bar(0), bar(0)], "overlaps the bar before, which ends at"),
        ([bar(0, minutes=1), bar(1, low=0.0)], "a price is not positive"),
    ],
)
def test_a_bar_that_fails_a_check_is_quarantined(
    tmp_path: Path, rows: list[Bar], reason: str
) -> None:
    store = Store(tmp_path)
    raw = download(store, rows, request(kind="bars"), caps=BAR_CAPS)
    cleaned = clean(store.downloads(BARS))
    assert cleaned.rows == 1
    (quarantined,) = cleaned.quarantine
    assert (quarantined.download, quarantined.row, quarantined.reason[:20]) == (
        raw.id,
        2,
        reason[:20],
    )
    assert reason in quarantined.reason


def test_a_raw_row_that_is_not_a_valid_bar_is_quarantined_not_raised(tmp_path: Path) -> None:
    """Raw files are read back from disk; a row the message types refuse is quarantined."""
    store = Store(tmp_path)
    raw = download(store, [bar(0)], request(kind="bars"), caps=BAR_CAPS)
    table = pq.read_table(raw.path)
    broken = table.set_column(4, "low", [[9.0]])  # low above high
    pq.write_table(broken.replace_schema_metadata(table.schema.metadata), raw.path)
    cleaned = clean([raw])
    assert cleaned.rows == 0
    assert "prices outside [low, high]" in cleaned.quarantine[0].reason


def test_cleaning_needs_downloads_of_one_dataset(tmp_path: Path) -> None:
    store = Store(tmp_path)
    trades = download(store, [trade(1)])
    bars = download(store, [bar(0)], request(kind="bars"), caps=BAR_CAPS)
    with pytest.raises(InvalidValueError, match="at least one download"):
        clean([])
    with pytest.raises(InvalidValueError, match="mixes datasets"):
        clean([trades, bars])


# -- publish -----------------------------------------------------------------------------


def two_datasets(store: Store) -> None:
    download(store, [priced(1, 1), priced(0, 9, size=-1), priced(2, 2)], at=0)
    download(store, [bar(0), bar(1)], request(kind="bars"), at=1, caps=BAR_CAPS)


def test_a_snapshot_publishes_the_clean_data_with_a_manifest_accounting_for_every_row(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    two_datasets(store)
    snapshot = store.publish(store.datasets())
    assert snapshot.path == tmp_path / "published" / snapshot.id
    trades = snapshot.entry(TRADES)
    assert (trades["raw_rows"], trades["rows"], trades["quarantined"], trades["superseded"]) == (
        3,
        2,
        1,
        0,
    )
    assert (trades["live_delay"], trades["terms"]) == (5 * 10**9, ["attribution"])
    data = pq.read_table(snapshot.path / TRADES.name / "data.parquet").to_pylist()
    assert [row["price"] for row in data] == [1, 2]
    quarantine = pq.read_table(snapshot.path / TRADES.name / "quarantine.parquet").to_pylist()
    assert [q["reason"] for q in quarantine] == ["size -1.0 is negative"]
    assert snapshot.datasets() == {TRADES.name: TRADES, BARS.name: BARS}
    assert store.snapshot(snapshot.id).manifest == snapshot.manifest


def test_publishing_the_same_raw_files_again_gives_the_same_snapshot(tmp_path: Path) -> None:
    store = Store(tmp_path)
    two_datasets(store)
    first = store.publish(store.datasets())
    assert store.publish(list(reversed(store.datasets()))).id == first.id
    download(store, [priced(3, 3)], request(3, 4), at=5)
    assert store.publish(store.datasets()).id != first.id  # a new raw file: a new snapshot


def test_a_snapshot_that_fails_its_audit_is_not_published(tmp_path: Path) -> None:
    store = Store(tmp_path)
    download(store, [priced(1, -1)])
    with pytest.raises(StoreError, match="feed/kraken%3ABTC%2FUSD/trades: no rows survive"):
        store.publish([TRADES])
    assert not (tmp_path / "published").exists()


def test_a_snapshot_needs_downloaded_datasets(tmp_path: Path) -> None:
    store = Store(tmp_path)
    with pytest.raises(StoreError, match="at least one dataset"):
        store.publish([])
    with pytest.raises(StoreError, match="nothing downloaded for feed/kraken%3ABTC%2FUSD/trades"):
        store.publish([TRADES])
    with pytest.raises(StoreError, match="no snapshot 0123456789abcdef"):
        store.snapshot("0123456789abcdef")
    with pytest.raises(StoreError, match="is not a snapshot id"):
        store.snapshot("../raw")


# -- the Phase 1 gate: a published snapshot rebuilds exactly from its raw files -----------


def test_phase_1_gate_a_published_snapshot_rebuilds_exactly_from_its_raw_files(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    two_datasets(store)
    snapshot = store.publish(store.datasets())
    published = {p.relative_to(snapshot.path): p.read_bytes() for p in snapshot.path.rglob("*.*")}
    assert store.verify(snapshot.id) == []
    shutil.rmtree(snapshot.path)
    again = store.publish(store.datasets())
    rebuilt = {p.relative_to(again.path): p.read_bytes() for p in again.path.rglob("*.*")}
    assert again.id == snapshot.id
    assert rebuilt == published
    assert len(published) == 5  # the manifest, and data and quarantine for each dataset


def test_verify_names_every_way_a_snapshot_no_longer_matches_its_raw_files(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    two_datasets(store)
    snapshot = store.publish(store.datasets())
    data = snapshot.path / TRADES.name / "data.parquet"
    data.write_bytes(data.read_bytes() + b"\0")
    (snapshot.path / BARS.name / "quarantine.parquet").unlink()
    assert store.verify(snapshot.id) == [
        f"{BARS.name}/quarantine.parquet is missing",
        f"{TRADES.name}/data.parquet differs from its rebuild from the raw files",
    ]
    (raw,) = store.downloads(BARS)
    raw.path.unlink()
    assert store.verify(snapshot.id) == [f"{BARS.name}: raw files gone: {raw.id}"]
    (raw,) = store.downloads(TRADES)
    raw.path.write_bytes(raw.path.read_bytes() + b"\0")
    problems = store.verify(snapshot.id)
    assert len(problems) == 2  # the bars' raw file is still gone
    assert "has changed since it was downloaded" in problems[1]


# -- reading a snapshot ----------------------------------------------------------------------


class AdmitAll:
    async def __aenter__(self) -> None:
        pass

    async def __aexit__(self, *exc: object) -> None:
        pass


def reader(store: Store, snapshot_id: str, source: str = "feed") -> StoreSource:
    built = StoreSource(
        StoreSourceParams(root=str(store.root), snapshot=snapshot_id, source=source)
    )
    built.bind(AdmitAll())
    return built


def test_the_store_source_reads_a_snapshot_on_its_sources_terms(tmp_path: Path) -> None:
    store = Store(tmp_path)
    two_datasets(store)
    built = reader(store, store.publish(store.datasets()).id)
    caps = built.capabilities
    assert caps.history == frozenset({"trades", "bars"})
    assert caps.bar_sizes == frozenset({MIN})
    assert caps.live_delay == 5 * 10**9
    assert caps.terms == frozenset({"attribution"})
    assert caps.live == frozenset()
    with SourceLoop() as loop:
        got = list(HistoryFeed(loop, built, request(0, 2)))
    assert [(m.ts, m.values["price"]) for m in got if isinstance(m, Point)] == [
        (MIN + 5 * 10**9, 1.0)
    ]
    with SourceLoop() as loop:
        bars = list(HistoryFeed(loop, built, request(0, 60, kind="bars")))
    assert [m.start for m in bars if isinstance(m, Bar)] == [0, MIN]


def test_the_store_source_refuses_what_the_snapshot_does_not_hold_or_no_longer_matches(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    two_datasets(store)
    snapshot = store.publish([TRADES])
    built = reader(store, snapshot.id)
    with SourceLoop() as loop, pytest.raises(StoreError, match="holds no feed/"):
        list(HistoryFeed(loop, built, request(kind="bars")))
    data = snapshot.path / TRADES.name / "data.parquet"
    data.write_bytes(data.read_bytes() + b"\0")
    with SourceLoop() as loop, pytest.raises(StoreError, match="no longer matches"):
        list(HistoryFeed(loop, built, request()))


def test_the_store_source_has_no_live_data_and_reports_its_health(tmp_path: Path) -> None:
    store = Store(tmp_path)
    two_datasets(store)
    good = reader(store, store.publish(store.datasets()).id)
    gone = reader(store, "0123456789abcdef")
    with pytest.raises(SourceUnavailableError, match="has no live data"):
        good.subscribe([Subscription(instrument=BTC, kind="trades")])

    async def health() -> list[tuple[bool, str]]:
        out = [await good.health_check(), await gone.health_check()]
        await good.close()
        return [(h.ok, h.detail) for h in out]

    (ok, _), (bad, detail) = asyncio.run(health())
    assert ok
    assert not bad
    assert "no snapshot 0123456789abcdef" in detail


def test_a_snapshot_id_must_be_one() -> None:
    with pytest.raises(ValidationError, match="is not a snapshot id"):
        StoreSourceParams(root=".", snapshot="latest", source="feed")


def test_a_dataset_is_named_and_written_back_as_it_was(tmp_path: Path) -> None:
    for dataset in (TRADES, BARS):
        assert Dataset.from_json(json.loads(json.dumps(dataset.to_json()))) == dataset
    assert BARS.name == "feed/kraken%3ABTC%2FUSD/bars-1m"
    with pytest.raises(InvalidValueError, match="lowercase slug"):
        Dataset(source="../x", instrument=BTC, kind="trades")
    with pytest.raises(InvalidValueError, match="only for bars"):
        Dataset(source="feed", instrument=BTC, kind="bars")


# -- found by review -----------------------------------------------------------------------


def test_two_publishes_of_one_snapshot_at_once_leave_it_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second to finish finds the first's in place, and discards its own staging."""
    store = Store(tmp_path)
    two_datasets(store)
    rename = os.rename
    raced: list[str] = []

    def other_publish_finishes_first(source: Any, target: Any) -> None:
        if not raced:
            raced.append("done")
            monkeypatch.setattr(os, "rename", rename)
            store.publish(store.datasets())  # the other publish, start to finish
            monkeypatch.setattr(os, "rename", other_publish_finishes_first)
        rename(source, target)

    monkeypatch.setattr(os, "rename", other_publish_finishes_first)
    snapshot = store.publish(store.datasets())
    assert raced == ["done"]
    assert store.verify(snapshot.id) == []
    assert sorted(p.name for p in (tmp_path / "published").iterdir()) == [snapshot.id]


def test_the_store_source_refuses_a_request_beyond_what_was_downloaded(tmp_path: Path) -> None:
    """Downloads of minutes 0-10 and 20-30: a request across the gap is an error."""
    store = Store(tmp_path)
    download(store, [priced(m, 1 + m) for m in range(10)], request(0, 10), at=0)
    download(store, [priced(m, 1 + m) for m in range(20, 30)], request(20, 30), at=1)
    built = reader(store, store.publish([TRADES]).id)
    with SourceLoop() as loop:
        assert len(list(HistoryFeed(loop, built, request(0, 10)))) == 10
    for start, end in ((5, 25), (0, 30), (25, 35)):
        with SourceLoop() as loop, pytest.raises(StoreError, match="not all of"):
            list(HistoryFeed(loop, built, request(start, end)))


def test_a_trade_value_named_like_the_time_column_is_refused(tmp_path: Path) -> None:
    store = Store(tmp_path)
    with pytest.raises(InvalidValueError, match="may not be named event_time"):
        download(store, [priced(5, 1, event_time=1.5)])
    assert store.downloads(TRADES) == []


def test_a_bar_ending_after_its_downloads_interval_is_quarantined(tmp_path: Path) -> None:
    store = Store(tmp_path)
    narrow = HistoryRequest(
        instrument=BTC, kind="bars", start=Timestamp(0), end=Timestamp(5 * MIN // 2), bar_size=MIN
    )
    download(store, [bar(0), bar(1), bar(2)], narrow, caps=BAR_CAPS)
    cleaned = clean(store.downloads(BARS))
    assert cleaned.rows == 2
    assert "after the download's interval ends" in cleaned.quarantine[0].reason


def test_two_instruments_sharing_a_key_are_refused_not_mixed(tmp_path: Path) -> None:
    store = Store(tmp_path)
    download(store, [trade(1)])
    other = Instrument(symbol="BTC/USD", venue="kraken", asset_class="future", quote_currency="USD")
    source = ScriptedSource(
        history_messages=[
            Point(instrument=other, event_time=ONE, received_at=ONE, values={"price": 1.0})
        ]
    )
    wanted = HistoryRequest(instrument=other, kind="trades", start=ZERO, end=Timestamp(60 * MIN))
    asyncio.run(store.download("feed", {}, source, wanted, SimulatedClock(NOW)))
    with pytest.raises(StoreError, match="two instruments share the key kraken:BTC/USD"):
        store.publish([TRADES])


def test_the_audit_compares_rows_read_with_rows_the_raw_files_declare(tmp_path: Path) -> None:
    store = Store(tmp_path)
    (raw,) = (download(store, [trade(1), trade(2)]),)
    _, manifest, _ = _build("0" * 16, {TRADES.name: [dataclasses.replace(raw, rows=3)]})
    assert _audit(manifest) == [f"{TRADES.name}: 2 rows read from raw files that declare 3"]


def test_a_download_that_brings_far_fewer_rows_than_it_replaces_is_refused_at_publish(
    tmp_path: Path,
) -> None:
    """An empty re-download of minutes 10-50 would erase four good trades and still claim
    the whole hour: publishing refuses, saying which download did it."""
    store = Store(tmp_path)
    download(store, [priced(m, 100 + m) for m in range(0, 60, 10)], request(0, 60), at=0)
    short = download(store, [], request(10, 50), at=1)
    cleaned = clean(store.downloads(TRADES))
    assert cleaned.shrinking == [(short.id, 4, 0)]
    refusal = f"download {short.id}'s interval had held 4 rows, but it brought 0"
    with pytest.raises(StoreError, match=refusal):
        store.publish([TRADES])
    accepted = store.publish([TRADES], accept=[short.id])  # the source really holds fewer
    assert accepted.manifest["accepted"] == [short.id]
    assert store.verify(accepted.id) == []


def test_a_download_replaces_only_the_bars_whole_within_its_interval(tmp_path: Path) -> None:
    """Two-minute bars for minutes 0-6, then a re-download of 1-5, which can bring only the
    2-4 bar whole: the 0-2 and 4-6 bars stay (the 4-6 one was once lost)."""
    store = Store(tmp_path)
    size, caps = 2 * MIN, Capabilities(history=frozenset({"bars"}), bar_sizes=frozenset({2 * MIN}))

    def wanted(start: int, end: int) -> HistoryRequest:
        return HistoryRequest(
            instrument=BTC,
            kind="bars",
            start=Timestamp(start * MIN),
            end=Timestamp(end * MIN),
            bar_size=size,
        )

    download(store, [bar(0, 2), bar(2, 2), bar(4, 2)], wanted(0, 6), caps=caps, at=0)
    download(store, [bar(2, 2, low=0.5)], wanted(1, 5), caps=caps, at=1)
    (dataset,) = store.datasets()
    cleaned = clean(store.downloads(dataset))
    rows = cleaned.table.to_pylist()
    assert [(row["start"] // MIN, row["low"]) for row in rows] == [(0, 1.0), (2, 0.5), (4, 1.0)]
    assert cleaned.superseded == 1


def test_a_download_bringing_as_many_rows_but_elsewhere_is_refused(tmp_path: Path) -> None:
    """An exchange ignoring the start it was given answers a request for two days with its
    newest 24 trades, all on day two: as many as day one had, none where they were."""
    store = Store(tmp_path)
    download(store, [priced(m * 60, 100) for m in range(24)], request(0, 24 * 60), at=0)
    newest = [priced(24 * 60 + m * 60, 200) for m in range(24)]
    short = download(store, newest, request(0, 48 * 60), at=1)
    with pytest.raises(StoreError, match=f"download {short.id}'s interval had held 24 rows"):
        store.publish([TRADES])


def test_a_short_download_later_downloaded_again_in_full_is_published(tmp_path: Path) -> None:
    store = Store(tmp_path)
    download(store, [priced(m, 100 + m) for m in range(0, 60, 10)], request(0, 60), at=0)
    download(store, [], request(10, 50), at=1)  # short
    download(store, [priced(m, 300 + m) for m in range(0, 60, 10)], request(0, 60), at=2)
    assert store.publish([TRADES]).manifest["datasets"]  # nothing stands short at the end


def test_a_snapshot_published_under_the_first_rules_still_verifies(tmp_path: Path) -> None:
    """Rules 1 superseded a bar re-download's whole interval. A snapshot published then
    keeps its id and verifies under those rules; publishing again gives a new one."""
    store = Store(tmp_path)
    size = 2 * MIN
    caps = Capabilities(history=frozenset({"bars"}), bar_sizes=frozenset({size}))

    def wanted(start: int, end: int) -> HistoryRequest:
        return HistoryRequest(
            instrument=BTC, kind="bars", start=Timestamp(start * MIN),
            end=Timestamp(end * MIN), bar_size=size,
        )  # fmt: skip

    download(store, [bar(0, 2), bar(2, 2), bar(4, 2)], wanted(0, 6), caps=caps, at=0)
    download(store, [bar(2, 2, low=0.5)], wanted(1, 5), caps=caps, at=1)
    (dataset,) = store.datasets()
    chosen = {dataset.name: store.downloads(dataset)}
    ids = {dataset.name: [d.id for d in chosen[dataset.name]]}
    old_id = _snapshot_id(ids, rules=1)
    # The id as Heliograph computed it before rules were versioned, written out.
    before = {"format": 1, "pyarrow": pa.__version__, "datasets": ids}
    assert old_id == hashlib.sha256(canonical_json(before).encode()).hexdigest()[:16]
    files, old_manifest, _ = _build(old_id, chosen, rules=1)
    # Rules 1 let the second download supersede the bar at 4, which ends past its interval.
    assert old_manifest["datasets"][dataset.name]["rows"] == 2
    for relative, data in files.items():  # as an older Heliograph published it
        (store.published / old_id / relative).parent.mkdir(parents=True, exist_ok=True)
        (store.published / old_id / relative).write_bytes(data)
    assert "rules" not in store.snapshot(old_id).manifest
    assert store.verify(old_id) == []
    new = store.publish([dataset])
    assert new.id != old_id
    assert new.manifest["rules"] == 2
    assert new.manifest["datasets"][dataset.name]["rows"] == 3  # rules 2 keep it


def test_the_store_sends_no_bar_that_is_not_whole_within_the_request(tmp_path: Path) -> None:
    store = Store(tmp_path)
    download(store, [bar(m) for m in range(6)], request(0, 6, kind="bars"), caps=BAR_CAPS)
    snapshot = store.publish([BARS])
    source = reader(store, snapshot.id)
    two_minutes = HistoryRequest(
        instrument=BTC,
        kind="bars",
        start=Timestamp(0),
        end=Timestamp(int(2.5 * MIN)),
        bar_size=MIN,
    )

    async def read() -> list[Message]:
        return [m async for m in source.history(two_minutes)]

    assert [m.start // MIN for m in asyncio.run(read()) if isinstance(m, Bar)] == [0, 1]


def test_a_short_answer_given_again_is_still_refused(tmp_path: Path) -> None:
    """A source that answered short once usually does again: the second short download
    does not excuse the first."""
    store = Store(tmp_path)
    download(store, [priced(m, 100 + m) for m in range(0, 60, 10)], request(0, 60), at=0)
    first = download(store, [], request(10, 50), at=1)
    again = download(store, [], request(10, 50), at=2)
    with pytest.raises(StoreError, match=first.id) as refused:
        store.publish([TRADES])
    assert again.id in str(refused.value)


def test_good_data_is_not_worn_away_half_at_a_time(tmp_path: Path) -> None:
    """Each re-download brings over half of what the one before left (9, 5, 3, 2), so
    each alone would pass: measured against the most the hour ever held, they are
    refused."""
    store = Store(tmp_path)
    download(store, [priced(m, 100) for m in range(0, 60, 7)], request(0, 60), at=0)  # 9
    download(store, [priced(m, 101) for m in (0, 14, 28, 42, 56)], request(0, 60), at=1)
    download(store, [priced(m, 102) for m in (0, 28, 56)], request(0, 60), at=2)
    download(store, [priced(m, 103) for m in (0, 56)], request(0, 60), at=3)
    with pytest.raises(StoreError, match="interval had held 9 rows"):
        store.publish([TRADES])


def test_accepting_a_download_that_is_not_there_is_refused(tmp_path: Path) -> None:
    store = Store(tmp_path)
    download(store, [priced(m, 100) for m in range(0, 60, 10)], request(0, 60), at=0)
    with pytest.raises(StoreError, match="accepting nonsense: no such download"):
        store.publish([TRADES], accept=["nonsense"])


def test_a_snapshot_records_only_the_acceptances_it_needed(tmp_path: Path) -> None:
    """The same raw files give the same manifest, whatever else was accepted."""
    store = Store(tmp_path)
    full = download(store, [priced(m, 100) for m in range(0, 60, 10)], request(0, 60), at=0)
    snapshot = store.publish([TRADES], accept=[full.id])
    assert "accepted" not in snapshot.manifest


def test_a_download_that_stopped_paging_halfway_is_refused(tmp_path: Path) -> None:
    """Half the hour's trades, all in its first half: as many as half, all within the
    span of what was held, and still the second half gone."""
    store = Store(tmp_path)
    full = [priced(t / 10, 100) for t in range(600)]  # one every 6 s
    download(store, full, request(0, 60), at=0)
    halfway = download(store, full[:300], request(0, 60), at=1)
    with pytest.raises(StoreError, match=halfway.id):
        store.publish([TRADES])


def test_an_accepted_correction_is_what_later_downloads_are_measured_against(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    download(store, [priced(m, 100) for m in range(0, 60, 10)], request(0, 60), at=0)
    corrected = download(store, [priced(m, 100) for m in (0, 50)], request(0, 60), at=1)
    store.publish([TRADES], accept=[corrected.id])
    download(store, [priced(m, 100) for m in (0, 50)], request(0, 60), at=2)  # the same again
    assert store.publish([TRADES], accept=[corrected.id]).manifest["accepted"] == [corrected.id]


@pytest.mark.parametrize(
    "kept",
    [
        pytest.param(lambda trades: trades[:-144], id="the last quarter less a little"),
        pytest.param(lambda trades: trades[::2], id="every other trade"),
        pytest.param(lambda trades: trades[:300] + trades[301:], id="one in six hundred lost"),
    ],
)
def test_rows_a_source_gave_before_and_not_again_are_never_lost_quietly(
    tmp_path: Path, kept: Any
) -> None:
    """A source asked again gives back what it gave before: losing a block, thinning,
    or more than one row in a hundred is refused."""
    store = Store(tmp_path)
    full = [priced(t / 10, 100) for t in range(600)]
    download(store, full, request(0, 60), at=0)
    again = kept(full)
    short = download(store, again, request(0, 60), at=1)
    if len(full) - len(again) <= len(full) // 100:
        assert store.publish([TRADES]).manifest["datasets"][TRADES.name]["rows"] == 599
    else:
        with pytest.raises(StoreError, match=short.id):
            store.publish([TRADES])
