# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The file source: CSV and Parquet in, the same messages out, and nothing guessed."""

import asyncio
import threading
from collections.abc import Callable, Generator, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

import heliograph.plugins.file_source as file_source
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Message, Point
from heliograph.core.sources import HistoryRequest, SourceUnavailableError, Subscription
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MINUTE, Timestamp, from_iso
from heliograph.plugins.file_source import FileSource, FileSourceParams
from tests.support.world import BTC

MIN = NANOS_PER_MINUTE
T0 = from_iso("2026-10-01T00:00:00Z")


class Counting:
    """A throttle that admits everything and counts the requests."""

    def __init__(self) -> None:
        self.entered = 0

    async def __aenter__(self) -> None:
        self.entered += 1

    async def __aexit__(self, *exc: object) -> None:
        pass


def source(path: Path, **params: Any) -> FileSource:
    built = FileSource(FileSourceParams(path=str(path), **params))
    built.bind(Counting())
    return built


def request(start: int = 0, end: int = 60, **fields: Any) -> HistoryRequest:
    """Trades for BTC from minute ``start`` to minute ``end`` after T0."""
    return HistoryRequest(
        instrument=BTC,
        start=Timestamp(T0 + start * MIN),
        end=Timestamp(T0 + end * MIN),
        **{"kind": "trades", **fields},
    )


def read(built: FileSource, wanted: HistoryRequest) -> list[Message]:
    async def collect() -> list[Message]:
        return [message async for message in built.history(wanted)]

    return asyncio.run(collect())


def iso(minute: int) -> str:
    return f"2026-10-01T00:{minute:02d}:00Z"


def write_csv(path: Path, header: str, *rows: str) -> Path:
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


TRADE_ROWS = [(minute, 100.0 + minute, 0.5) for minute in range(0, 10, 2)]


def trades_csv(tmp_path: Path) -> Path:
    rows = [f"{iso(m)},{price},{size}" for m, price, size in TRADE_ROWS]
    return write_csv(tmp_path / "btc.csv", "time,price,size", *rows)


def trades_parquet(tmp_path: Path) -> Path:
    times = pa.array([T0 + m * MIN for m, _, _ in TRADE_ROWS], pa.timestamp("ns", tz="UTC"))
    table = pa.table(
        {
            "time": times,
            "price": [price for _, price, _ in TRADE_ROWS],
            "size": [size for _, _, size in TRADE_ROWS],
        }
    )
    path = tmp_path / "btc.parquet"
    pq.write_table(table, path)
    return path


def expected_trades(minutes: Sequence[int]) -> list[Point]:
    out = []
    for m, price, size in TRADE_ROWS:
        if m in minutes:
            at = Timestamp(T0 + m * MIN)
            values = {"price": price, "size": size}
            out.append(Point(instrument=BTC, event_time=at, received_at=at, values=values))
    return out


# -- reading -----------------------------------------------------------------------


@pytest.mark.parametrize("make", [trades_csv, trades_parquet], ids=["csv", "parquet"])
def test_a_file_yields_its_trades_in_the_requested_interval_for_the_requested_instrument(
    tmp_path: Path, make: Callable[[Path], Path]
) -> None:
    """[start, end): the row at the start is in, the row at the end is out."""
    got = read(source(make(tmp_path)), request(start=2, end=8))
    assert got == expected_trades([2, 4, 6])


@pytest.mark.parametrize(
    ("unit", "scale"), [("s", 60), ("ms", 60_000), ("us", 60_000_000), ("ns", MIN)]
)
def test_epoch_times_are_read_in_the_declared_unit(tmp_path: Path, unit: str, scale: int) -> None:
    epoch = T0 // (MIN // scale)
    rows = [f"{epoch + m * scale},{price},{size}" for m, price, size in TRADE_ROWS]
    path = write_csv(tmp_path / "btc.csv", "time,price,size", *rows)
    assert read(source(path, time_unit=unit), request()) == expected_trades(range(10))


def test_parquet_integer_times_are_read_in_the_declared_unit(tmp_path: Path) -> None:
    table = pa.table({"t": [T0 // 10**6, T0 // 10**6 + 60_000], "price": [1.0, 2.0]})
    pq.write_table(table, tmp_path / "btc.parquet")
    built = source(tmp_path / "btc.parquet", time_column="t", time_unit="ms", values=("price",))
    got = read(built, request())
    assert [p.ts for p in got] == [T0, T0 + MIN]


def test_bars_end_one_bar_size_after_the_time_they_start(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "bars.csv",
        "start,open,high,low,close,volume",
        f"{iso(0)},10,12,9,11,5",
        f"{iso(1)},11,11,10,10.5,0",
    )
    built = source(path, kind="bars", bar_size="1m", time_column="start")
    got = read(built, request(kind="bars", bar_size=MIN))
    assert got == [
        Bar(instrument=BTC, start=T0, end=Timestamp(T0 + MIN),
            open=10, high=12, low=9, close=11, volume=5),
        Bar(instrument=BTC, start=Timestamp(T0 + MIN), end=Timestamp(T0 + 2 * MIN),
            open=11, high=11, low=10, close=10.5, volume=0),
    ]  # fmt: skip


def test_reading_stops_at_the_first_row_past_the_end(tmp_path: Path) -> None:
    """A broken row after the request's end is never read."""
    path = write_csv(
        tmp_path / "btc.csv", "time,price,size", f"{iso(0)},1,1", f"{iso(5)},2,1", "broken,,"
    )
    got = read(source(path), request(end=5))
    assert [p.ts for p in got] == [T0]


def test_each_batch_is_read_through_the_throttle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(file_source, "BATCH", 2)
    built = source(trades_csv(tmp_path))
    assert len(read(built, request())) == 5
    assert isinstance(built._throttle, Counting)
    assert built._throttle.entered == 4  # batches of 2, 2 and 1, then the end


# -- refusing ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([f"{iso(0)},1"], "row 2: size is empty"),
        ([f"{iso(0)},1,1", f"{iso(1)},x,1"], "row 3: price is 'x', not a number"),
        (["2026-10-01T00:00:00,1,1"], "row 2: .*has no time zone"),
        (["yesterday,1,1"], "row 2: 'yesterday' is not an ISO 8601"),
        ([",1,1"], "row 2: the time is empty"),
        ([f"{iso(5)},1,1", f"{iso(4)},1,1"], "row 3: time goes back"),
        ([f"{iso(0)},nan,1"], "row 2: .*price is not finite"),
    ],
)
def test_a_row_that_cannot_be_read_stops_the_read_naming_the_file_and_row(
    tmp_path: Path, rows: list[str], reason: str
) -> None:
    path = write_csv(tmp_path / "btc.csv", "time,price,size", *rows)
    with pytest.raises(InvalidValueError, match=f"btc.csv {reason}"):
        read(source(path), request())


@pytest.mark.parametrize(
    ("value", "reason"), [("1.5", "not a whole number of s"), ("soon", "not a whole number")]
)
def test_an_epoch_time_must_be_a_whole_number(tmp_path: Path, value: str, reason: str) -> None:
    path = write_csv(tmp_path / "btc.csv", "time,price", f"{value},1")
    with pytest.raises(InvalidValueError, match=reason):
        read(source(path, time_unit="s", values=("price",)), request())


def test_a_parquet_float_time_is_refused(tmp_path: Path) -> None:
    pq.write_table(pa.table({"time": [1.0e9], "price": [1.0]}), tmp_path / "x.parquet")
    with pytest.raises(InvalidValueError, match=r"row 1: the time 1000000000\.0 is not a whole"):
        read(source(tmp_path / "x.parquet", time_unit="ns", values=("price",)), request())


def test_a_parquet_number_is_not_taken_for_iso_text(tmp_path: Path) -> None:
    pq.write_table(pa.table({"time": [T0], "price": [1.0]}), tmp_path / "x.parquet")
    with pytest.raises(InvalidValueError, match="is not ISO 8601 text"):
        read(source(tmp_path / "x.parquet", values=("price",)), request())


def test_a_parquet_timestamp_without_a_time_zone_is_refused(tmp_path: Path) -> None:
    times = pa.array([T0 // 1000], pa.timestamp("us"))
    pq.write_table(pa.table({"time": times, "price": [1.0]}), tmp_path / "x.parquet")
    with pytest.raises(InvalidValueError, match="the time column has no time zone"):
        read(source(tmp_path / "x.parquet", values=("price",)), request())


def test_a_parquet_timestamp_in_seconds_is_converted(tmp_path: Path) -> None:
    times = pa.array([T0 // 10**9], pa.timestamp("s", tz="+02:00"))
    pq.write_table(pa.table({"time": times, "price": [1.0]}), tmp_path / "x.parquet")
    (point,) = read(source(tmp_path / "x.parquet", values=("price",)), request())
    assert point.ts == T0


@pytest.mark.parametrize("suffix", [".csv", ".parquet"])
def test_a_missing_column_is_named(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"btc{suffix}"
    if suffix == ".csv":
        write_csv(path, "time,price", f"{iso(0)},1")
    else:
        pq.write_table(pa.table({"time": [iso(0)], "price": [1.0]}), path)
    with pytest.raises(InvalidValueError, match="has no column size"):
        read(source(path), request())


@pytest.mark.parametrize("suffix", [".csv", ".parquet"])
def test_a_missing_file_is_unavailable_not_invalid(tmp_path: Path, suffix: str) -> None:
    with pytest.raises(SourceUnavailableError, match="No such file"):
        read(source(tmp_path / f"gone{suffix}"), request())


def test_a_file_that_is_not_parquet_is_refused(tmp_path: Path) -> None:
    (tmp_path / "x.parquet").write_bytes(b"not parquet")
    with pytest.raises(InvalidValueError, match="is not a Parquet file"):
        read(source(tmp_path / "x.parquet"), request())


def test_a_request_for_data_the_file_does_not_hold_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidValueError, match="holds trades, not what was asked"):
        read(source(trades_csv(tmp_path)), request(kind="bars", bar_size=MIN))
    bars = source(tmp_path / "b.csv", kind="bars", bar_size="1h")
    with pytest.raises(InvalidValueError, match="holds 1h bars"):
        read(bars, request(kind="bars", bar_size=MIN))


def test_a_source_used_before_it_is_bound_is_refused(tmp_path: Path) -> None:
    unbound = FileSource(FileSourceParams(path=str(trades_csv(tmp_path))))
    with pytest.raises(InvalidValueError, match="used before it was bound"):
        read(unbound, request())


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({"path": "btc.txt"}, "not a .csv or .parquet file"),
        ({"path": "b.csv", "kind": "bars"}, "bar_size is given for bars"),
        ({"path": "b.csv", "bar_size": "1m"}, "bar_size is given for bars"),
        ({"path": "b.csv", "values": ("size",)}, "must include price"),
        ({"path": "b.csv", "live_delay": "soon"}, "is not a span"),
        ({"path": "b.csv", "time_unit": "days"}, "time_unit"),
        ({"path": "b.csv", "terms": ("free",)}, "terms"),
        ({}, "path"),
    ],
)
def test_parameters_that_cannot_be_right_are_refused(params: dict[str, Any], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        FileSourceParams(**params)


# -- what it declares ----------------------------------------------------------------


def test_capabilities_follow_the_parameters() -> None:
    bars = FileSource(
        FileSourceParams(
            path="b.parquet", kind="bars", bar_size="1h", live_delay="15m", terms=("attribution",)
        )
    ).capabilities
    assert bars.history == {"bars"}
    assert bars.live == frozenset()
    assert bars.bar_sizes == {NANOS_PER_HOUR}
    assert bars.live_delay == 15 * MIN
    assert bars.terms == {"attribution"}
    assert bars.rate_limit is None
    assert bars.lookback is None
    trades = FileSource(FileSourceParams(path="t.csv")).capabilities
    assert trades.history == {"trades"}
    assert trades.bar_sizes == frozenset()
    assert trades.live_delay == 0


def test_a_file_has_no_live_data(tmp_path: Path) -> None:
    built = source(trades_csv(tmp_path))
    with pytest.raises(SourceUnavailableError, match="a file has no live data"):
        built.subscribe([Subscription(instrument=BTC, kind="trades")])


def test_health_says_whether_the_file_is_there(tmp_path: Path) -> None:
    async def health(built: FileSource) -> tuple[bool, str]:
        report = await built.health_check()
        await built.close()
        return report.ok, report.detail

    assert asyncio.run(health(source(trades_csv(tmp_path)))) == (True, "")
    ok, detail = asyncio.run(health(source(tmp_path / "gone.csv")))
    assert not ok
    assert detail.endswith("gone.csv is not a file")


def test_a_read_cancelled_while_a_batch_is_being_read_stays_cancelled_and_closes_its_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The batch is read in a thread that cancellation cannot stop; closing waits for it."""
    started, release, closed = threading.Event(), threading.Event(), threading.Event()

    def held() -> Generator[list[file_source.Row]]:
        try:
            started.set()
            release.wait(timeout=10)
            yield []
        finally:
            closed.set()

    built = source(trades_csv(tmp_path))
    monkeypatch.setattr(built, "_batches", held)

    async def scenario() -> None:
        async def first() -> Message:
            return await anext(aiter(built.history(request())))

        reading = asyncio.create_task(first())
        await asyncio.to_thread(started.wait, 10)
        reading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reading
        assert not closed.is_set()  # still being read: closing now would fail
        release.set()
        assert await asyncio.to_thread(closed.wait, 10)

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))


def test_a_csv_file_that_is_not_utf8_is_refused_naming_where(tmp_path: Path) -> None:
    path = tmp_path / "btc.csv"
    path.write_bytes(f"time,price,size\n{iso(0)},1,1\n{iso(1)},1,1 caf\xe9\n".encode("latin-1"))
    with pytest.raises(InvalidValueError, match=r"btc\.csv is not UTF-8 text, at or after row"):
        read(source(path), request())


def test_a_byte_order_mark_is_not_part_of_the_first_column_name(tmp_path: Path) -> None:
    path = tmp_path / "btc.csv"
    path.write_bytes(b"\xef\xbb\xbf" + trades_csv(tmp_path).read_bytes())
    assert read(source(path), request()) == expected_trades(range(10))


def test_bars_that_overlap_are_refused(tmp_path: Path) -> None:
    """Rows a minute apart are not hourly bars: a wrong bar_size would be look-ahead."""
    path = write_csv(
        tmp_path / "bars.csv",
        "time,open,high,low,close,volume",
        f"{iso(0)},1,1,1,1,0",
        f"{iso(1)},1,1,1,1,0",
    )
    built = source(path, kind="bars", bar_size="1h")
    with pytest.raises(InvalidValueError, match=r"row 3: the bar starting at \d+ overlaps"):
        read(built, request(kind="bars", bar_size=NANOS_PER_HOUR))
