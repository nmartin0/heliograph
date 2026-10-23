# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The CCXT source against the real exchange: does it still answer as we read it?

Marked ``live`` and excluded by default; run on request with
``uv run pytest -m live``. Each test reads a little real data through the
throttle and the bridge, as a run would, and checks its shape: what a
recorded page cannot say is whether the exchange still pages, rounds and
truncates the way the offline tests assume.
"""

import asyncio
import dataclasses
from itertools import pairwise
from pathlib import Path

import pytest

from heliograph.app.bridge import HistoryFeed, SourceLoop
from heliograph.app.config import parse_config
from heliograph.app.limits import RateThrottle
from heliograph.app.run import JOURNALLED, run_backtest
from heliograph.core.clock import WallClock
from heliograph.core.messages import Bar, Point
from heliograph.core.sources import HistoryRequest, Subscription
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MINUTE, Timestamp, to_datetime
from heliograph.plugins.ccxt_source import PAGES_TRADES_BY_TIME, CcxtParams, CcxtSource
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import read_entries, read_header
from tests.support.world import BTC

pytestmark = pytest.mark.live
CLOCK = WallClock()


@pytest.fixture
def kraken() -> CcxtSource:
    source = CcxtSource(CcxtParams(exchange="kraken"))
    source.bind(RateThrottle(source.capabilities, CLOCK))
    return source


def last(span: int, kind: str = "trades", size: int | None = None) -> HistoryRequest:
    """The ``span`` before now, ending on a whole minute."""
    end = CLOCK.now() // NANOS_PER_MINUTE * NANOS_PER_MINUTE
    return HistoryRequest(
        instrument=BTC,
        kind=kind,  # type: ignore[arg-type]
        start=Timestamp(end - span),
        end=Timestamp(end),
        bar_size=size,
    )


def test_recent_trades_arrive_in_order_with_prices_and_sizes(kraken: CcxtSource) -> None:
    with SourceLoop() as loop:
        feed = HistoryFeed(loop, kraken, last(10 * NANOS_PER_MINUTE))
        points = [m for m in feed if isinstance(m, Point)]
        loop.run(kraken.close())
    assert points, "Kraken reported no BTC/USD trade in ten minutes"
    times = [p.event_time for p in points]
    assert times == sorted(times)
    assert all(p.values["price"] > 0 and p.values["size"] > 0 for p in points)
    assert all(p.values.get("side") in (1.0, -1.0) for p in points)


@pytest.mark.parametrize("exchange", sorted(PAGES_TRADES_BY_TIME))
def test_trade_history_from_hours_back_starts_where_it_was_asked_to(exchange: str) -> None:
    """What PAGES_TRADES_BY_TIME claims: asked for trades from six hours back, the
    exchange answers from then, not with its newest trades."""
    source = CcxtSource(CcxtParams(exchange=exchange))
    source.bind(RateThrottle(source.capabilities, CLOCK))
    recent = last(6 * NANOS_PER_HOUR)
    wanted = dataclasses.replace(
        recent,
        instrument=dataclasses.replace(BTC, venue=exchange),
        end=Timestamp(recent.start + 30 * NANOS_PER_MINUTE),
    )
    with SourceLoop() as loop:
        feed = HistoryFeed(loop, source, wanted)
        try:
            first = next(m for m in feed if isinstance(m, Point))
        finally:
            feed.close()
            loop.run(source.close())
    assert wanted.start <= first.event_time < wanted.end, f"{exchange} ignored the start"
    assert first.event_time - wanted.start < 10 * NANOS_PER_MINUTE


def test_hourly_bars_for_a_day_are_whole_and_contiguous(kraken: CcxtSource) -> None:
    with SourceLoop() as loop:
        wanted = last(24 * NANOS_PER_HOUR, "bars", NANOS_PER_HOUR)
        wanted = HistoryRequest(
            instrument=BTC,
            kind="bars",
            start=Timestamp(wanted.start // NANOS_PER_HOUR * NANOS_PER_HOUR),
            end=wanted.end,
            bar_size=NANOS_PER_HOUR,
        )
        bars = [m for m in HistoryFeed(loop, kraken, wanted) if isinstance(m, Bar)]
        loop.run(kraken.close())
    assert len(bars) >= 23
    assert all(b.end - b.start == NANOS_PER_HOUR for b in bars)
    assert all(later.start == earlier.end for earlier, later in pairwise(bars))
    assert bars[-1].end <= wanted.end  # the forming candle is not among them


def test_a_live_trade_arrives_within_a_minute(kraken: CcxtSource) -> None:
    async def one() -> Point:
        stream = kraken.subscribe([Subscription(instrument=BTC, kind="trades")])
        try:
            message = await asyncio.wait_for(anext(aiter(stream)), timeout=60)
        finally:
            await stream.aclose()  # type: ignore[attr-defined]
            await kraken.close()
        assert isinstance(message, Point)
        return message

    trade = asyncio.run(one())
    assert trade.values["price"] > 0


def test_a_backtest_on_minute_candles_builds_whole_hours(tmp_path: Path) -> None:
    """Six hours of Kraken's 1m candles, as they come and built into 1h bars (D33)."""
    end = CLOCK.now() // NANOS_PER_HOUR * NANOS_PER_HOUR
    start = end - 6 * NANOS_PER_HOUR
    text = f"""
[mounts.btc]
source = "ccxt"
params = {{ exchange = "kraken" }}
kind = "bars"
bar_size = "1m"
start = {to_datetime(Timestamp(start)).isoformat()}
end = {to_datetime(Timestamp(end)).isoformat()}

[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"

[[streams]]
mount = "btc"
bars = "1m"

[[streams]]
mount = "btc"
bars = "1h"
"""
    journal = tmp_path / "run.sqlite"
    registry = Registry.installed(["data_sources"])
    run_backtest(parse_config(text), registry, journal, CLOCK, asyncio.sleep)
    assert read_header(journal)["status"] == "complete"
    bars = [e.message for e in read_entries(journal, JOURNALLED) if isinstance(e.message, Bar)]
    minutes = [b for b in bars if b.end - b.start == NANOS_PER_MINUTE]
    hours = [b for b in bars if b.end - b.start == NANOS_PER_HOUR]
    assert [h.start for h in hours] == list(range(start, end, NANOS_PER_HOUR))
    for hour in hours:
        parts = [m for m in minutes if hour.start <= m.start < hour.end]
        assert parts, "an hour was built with no minutes in it"
        assert (hour.open, hour.close) == (parts[0].open, parts[-1].close)
        assert hour.high == max(m.high for m in parts)
        assert hour.low == min(m.low for m in parts)
        assert hour.volume == pytest.approx(sum(m.volume for m in parts))
        if parts[-1].end == hour.end:  # closed on its last minute, known with it
            assert hour.ts == parts[-1].ts
