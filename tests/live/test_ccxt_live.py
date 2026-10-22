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
from itertools import pairwise

import pytest

from heliograph.app.bridge import HistoryFeed, SourceLoop
from heliograph.app.limits import RateThrottle
from heliograph.core.clock import WallClock
from heliograph.core.messages import Bar, Point
from heliograph.core.sources import HistoryRequest, Subscription
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MINUTE, Timestamp
from heliograph.plugins.ccxt_source import CcxtParams, CcxtSource
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
