# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The store: raw kept as given, cleaned by rule, published, and rebuilt exactly."""

import asyncio
import dataclasses
import json
import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

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
    _, manifest = _build("0" * 16, {TRADES.name: [dataclasses.replace(raw, rows=3)]})
    assert _audit(manifest) == [f"{TRADES.name}: 2 rows read from raw files that declare 3"]
