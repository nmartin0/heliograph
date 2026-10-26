# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A backtest's series for charts, read from its journal: checked against the
journal's own entries and the report's figures, on the example strategy."""

from collections import Counter
from pathlib import Path

import pytest

from heliograph.app import charts
from heliograph.app.charts import NotInRunError
from heliograph.app.config import load_config, parse_config
from heliograph.app.report import figures
from heliograph.app.run import JOURNALLED, RunSummary, run_backtest
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MILLISECOND, from_iso
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import read_entries
from tests.support.simulated import SimulatedTime

ROOT = Path(__file__).resolve().parents[2]
FEB_1 = from_iso("2026-02-01T00:00:00Z")


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> RunSummary:
    journal = tmp_path_factory.mktemp("example") / "trend.sqlite"
    time = SimulatedTime(start=from_iso("2026-10-23T00:00:00Z"))
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(ROOT)
        loaded = load_config(ROOT / "examples" / "trend.toml")
        return run_backtest(loaded, Registry.installed(), journal, time.clock, time.advance)


def test_the_contents_name_the_streams_nodes_and_strategies(run: RunSummary) -> None:
    found = charts.contents(run.journal)
    assert found["streams"] == [
        {"stream": "btc@1h", "mount": "btc", "instrument": "kraken:BTC/USD", "size": "1h"}
    ]
    assert [(n["name"], n["kind"]) for n in found["nodes"]] == [
        ("ewmac16", "forecasters"),
        ("ewmac32", "forecasters"),
        ("atr24", "indicators"),
    ]
    assert found["strategies"] == [{"name": "trend", "price": "btc@1h.close"}]


def test_bars_are_the_journals_bars_of_the_stream_in_order(run: RunSummary) -> None:
    journalled = [
        e.message for e in read_entries(run.journal, JOURNALLED) if isinstance(e.message, Bar)
    ]
    got = charts.bars(run.journal, "btc@1h")
    assert len(got) == len(journalled) == 2880
    assert [(b["open"], b["close"]) for b in got] == [(b.open, b.close) for b in journalled]
    assert got[0]["t"] == journalled[0].start // NANOS_PER_MILLISECOND  # a bar sits at its start
    assert (got[0]["start"], got[0]["end"]) == ("2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z")
    assert got[0]["end_t"] - got[0]["t"] == 3_600_000  # in milliseconds, as ``t`` is
    assert [b["t"] for b in got] == sorted(b["t"] for b in got)


def test_a_span_keeps_what_became_known_within_it(run: RunSummary) -> None:
    got = charts.bars(run.journal, "btc@1h", FEB_1, FEB_1 + 3 * NANOS_PER_HOUR)
    # Known at 00:00, 01:00 and 02:00: the bars that end then; the one ending at 03:00 is not.
    assert [b["known"] for b in got] == [
        "2026-02-01T00:00:00Z",
        "2026-02-01T01:00:00Z",
        "2026-02-01T02:00:00Z",
    ]


def test_a_nodes_values_are_the_ones_it_changed_to(run: RunSummary) -> None:
    got = charts.node(run.journal, "ewmac16", FEB_1, FEB_1 + 3 * NANOS_PER_HOUR)
    assert [v["time"] for v in got] == [
        "2026-02-01T00:00:00Z",
        "2026-02-01T01:00:00Z",
        "2026-02-01T02:00:00Z",
    ]
    assert all(isinstance(v["value"], float) for v in got)
    whole = charts.node(run.journal, "atr24")
    assert len(whole) > 2000
    assert len(whole) < 2880  # nothing until its period has passed


def test_signals_and_fills_are_the_ones_the_report_counts(run: RunSummary) -> None:
    _, (trend,) = figures(run.journal)
    sent = charts.signals(run.journal, "trend")
    kinds = Counter(s["kind"] for s in sent)
    assert (kinds["signal"], kinds["refused"]) == (trend.signals, trend.refused + trend.doubted)
    filled = charts.fills(run.journal, "trend")
    assert len(filled) == trend.fills
    # A fill sits where it happened, at its bar's open, an hour before that bar was known.
    assert all(
        from_iso(f["known"]) - from_iso(f["time"]) == NANOS_PER_HOUR
        and f["t"] == from_iso(f["time"]) // NANOS_PER_MILLISECOND
        for f in filled
    )
    assert {f["signal"] for f in filled} <= {s["id"] for s in sent if s["kind"] == "signal"}
    refused = next(s for s in sent if s["kind"] == "refused")
    assert refused["refused_by"] == "limits"
    assert refused["reason"]


def test_the_equity_ends_where_the_report_does(run: RunSummary) -> None:
    _, (trend,) = figures(run.journal)
    curve = charts.equity_of(run.journal, "trend")
    assert len(curve) == trend.closes
    assert curve[0]["time"] == "2026-01-01T01:00:00Z"  # the first close, once settled
    assert curve[-1]["value"] == pytest.approx(trend.result)
    assert [p["t"] for p in curve] == sorted(p["t"] for p in curve)


@pytest.mark.parametrize(
    ("read", "says"),
    [
        (lambda j: charts.bars(j, "eth@1h"), "no stream 'eth@1h'"),
        (lambda j: charts.bars(j, "btc@4h"), "no stream 'btc@4h'"),
        (lambda j: charts.node(j, "trend"), "no indicator, forecaster or gate 'trend'"),
        (lambda j: charts.signals(j, "nobody"), "no strategy 'nobody'"),
        (lambda j: charts.fills(j, "nobody"), "no strategy 'nobody'"),
        (lambda j: charts.equity_of(j, "nobody"), "no strategy 'nobody'"),
    ],
)
def test_what_the_run_does_not_have_is_said(run: RunSummary, read: object, says: str) -> None:
    with pytest.raises(NotInRunError, match=says):
        read(run.journal)  # type: ignore[operator]


def test_a_journal_that_is_not_a_backtests_cannot_be_charted(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "other.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE header (key TEXT, value TEXT); CREATE TABLE entries (seq INTEGER);"
            "INSERT INTO header VALUES ('format', '1');"
        )
    db.close()
    with pytest.raises(InvalidValueError, match="is not the journal of a backtest run"):
        charts.contents(path)


def test_a_number_json_cannot_hold_is_null() -> None:
    assert [charts._number(v) for v in (1.5, float("nan"), float("inf"), None)] == [
        1.5,
        None,
        None,
        None,
    ]


def test_bars_are_those_of_the_stream_asked_for_when_a_run_has_two(tmp_path: Path) -> None:
    data = tmp_path / "btc.csv"
    data.write_text(
        "time,price,size\n"
        + "".join(
            f"2026-01-01T{h:02d}:{m:02d}:00Z,{100 + h},1\n" for h in range(6) for m in (5, 35)
        )
    )
    text = f"""
mode = "backtest"
[mounts.btc]
source = "file"
params = {{ path = {str(data)!r} }}
kind = "trades"
start = 2026-01-01T00:00:00Z
end = 2026-01-01T06:00:00Z
[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"
[[streams]]
mount = "btc"
bars = "1h"
[[streams]]
mount = "btc"
bars = "2h"
[[components]]
kind = "indicators"
plugin = "atr"
name = "fast"
inputs = ["btc@1h.high", "btc@1h.low", "btc@1h.close"]
params = {{ period = 1 }}
[[components]]
kind = "indicators"
plugin = "atr"
name = "slow"
inputs = ["btc@2h.high", "btc@2h.low", "btc@2h.close"]
params = {{ period = 1 }}
"""
    time = SimulatedTime(start=from_iso("2026-10-23T00:00:00Z"))
    journal = tmp_path / "two.sqlite"
    run_backtest(parse_config(text), Registry.installed(), journal, time.clock, time.advance)
    assert [s["stream"] for s in charts.contents(journal)["streams"]] == ["btc@1h", "btc@2h"]
    hourly = charts.bars(journal, "btc@1h")
    two_hourly = charts.bars(journal, "btc@2h")
    assert [b["start"][11:16] for b in hourly] == [
        "00:00",
        "01:00",
        "02:00",
        "03:00",
        "04:00",
        "05:00",
    ]
    assert [b["start"][11:16] for b in two_hourly] == ["00:00", "02:00", "04:00"]
    # Each node's values are those it changed to: the slow one's, at its own closes only.
    fast = [v["time"][11:16] for v in charts.node(journal, "fast")]
    slow = [v["time"][11:16] for v in charts.node(journal, "slow")]
    assert len(fast) > len(slow) > 0
    assert set(slow) <= {"02:00", "04:00", "06:00"}


def test_bars_are_those_of_the_streams_instrument_when_a_run_has_two(tmp_path: Path) -> None:
    rows = "".join(f"2026-01-01T{h:02d}:05:00Z,{{price}},1\n" for h in range(3))
    for name, price in (("btc", 100), ("eth", 5)):
        (tmp_path / f"{name}.csv").write_text("time,price,size\n" + rows.format(price=price))
    mounts = "".join(
        f"""
[mounts.{name}]
source = "file"
params = {{ path = {str(tmp_path / f"{name}.csv")!r} }}
kind = "trades"
start = 2026-01-01T00:00:00Z
end = 2026-01-01T03:00:00Z
[mounts.{name}.instrument]
symbol = "{name.upper()}/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"
[[streams]]
mount = "{name}"
bars = "1h"
"""
        for name in ("btc", "eth")
    )
    time = SimulatedTime(start=from_iso("2026-10-23T00:00:00Z"))
    journal = tmp_path / "two.sqlite"
    run_backtest(
        parse_config('mode = "backtest"\n' + mounts),
        Registry.installed(),
        journal,
        time.clock,
        time.advance,
    )
    assert [b["close"] for b in charts.bars(journal, "btc@1h")] == [100, 100, 100]
    assert [b["close"] for b in charts.bars(journal, "eth@1h")] == [5, 5, 5]


@pytest.fixture(scope="module")
def two_strategies(tmp_path_factory: pytest.TempPathFactory) -> RunSummary:
    """The example with a second strategy on one forecaster, sized by its own sizer."""
    journal = tmp_path_factory.mktemp("two") / "two.sqlite"
    text = (
        (ROOT / "examples" / "trend.toml").read_text()
        + """
[[components]]
kind = "sizers"
plugin = "vol_target"
name = "vol2"
params = { capital = 10000.0, target = 0.25, bars_per_year = 8760.0 }

[[strategies]]
name = "fast"
price = "btc@1h.close"
forecasters = ["ewmac16"]
sizer = "vol2"
"""
    )
    time = SimulatedTime(start=from_iso("2026-10-23T00:00:00Z"))
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(ROOT)
        return run_backtest(
            parse_config(text), Registry.installed(), journal, time.clock, time.advance
        )


def test_signals_and_fills_are_those_of_the_strategy_asked_for(two_strategies: RunSummary) -> None:
    journal = two_strategies.journal
    every = charts.signals(journal)
    fast = charts.signals(journal, "fast")
    trend = charts.signals(journal, "trend")
    assert {s["strategy"] for s in every} == {"fast", "trend"}
    assert [s["strategy"] for s in fast] == ["fast"] * len(fast)
    assert len(fast) + len(trend) == len(every)
    assert fast
    assert charts.fills(journal, "fast") == []  # it has no fill model
    assert {f["strategy"] for f in charts.fills(journal, "trend")} == {"trend"}


def test_signals_and_equity_keep_to_the_span_asked_for(run: RunSummary) -> None:
    day = 24 * NANOS_PER_HOUR
    signals = charts.signals(run.journal, "trend", FEB_1, FEB_1 + day)
    curve = charts.equity_of(run.journal, "trend", FEB_1, FEB_1 + day)
    assert signals
    assert len(curve) == 24
    for item in (*signals, *curve):
        assert FEB_1 <= from_iso(item["time"]) < FEB_1 + day
