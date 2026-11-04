# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Alpaca source against the real service: does it still answer as we read it?

Marked ``live`` and excluded by default; run on request with ``uv run pytest -m live``. Needs
``APCA_API_KEY_ID`` and ``APCA_API_SECRET_KEY`` (a free paper account is enough); without them
the tests are skipped. The offline tests use replies written from Alpaca's documentation; what
they cannot say is whether the service still sends exactly that.
"""

import asyncio
import os

import pytest

from heliograph.app.bridge import HistoryFeed, SourceLoop
from heliograph.app.limits import RateThrottle
from heliograph.core.clock import WallClock
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Instrument, Point
from heliograph.core.sources import HistoryRequest, Subscription
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MINUTE, Timestamp
from heliograph.plugins.alpaca_source import AlpacaParams, AlpacaSource
from heliograph.plugins.market_calendars import market_calendar

pytestmark = pytest.mark.live
CLOCK = WallClock()
AAPL = Instrument(symbol="AAPL", venue="alpaca", asset_class="equity", quote_currency="USD")
DAY = 24 * NANOS_PER_HOUR


@pytest.fixture
def alpaca() -> AlpacaSource:
    if not (os.environ.get("APCA_API_KEY_ID") and os.environ.get("APCA_API_SECRET_KEY")):
        pytest.skip("APCA_API_KEY_ID and APCA_API_SECRET_KEY are not set")
    source = AlpacaSource(AlpacaParams())
    source.bind(RateThrottle(source.capabilities, CLOCK))
    return source


def week_of_bars() -> HistoryRequest:
    """Five-minute bars for the week ending an hour ago (the free plan's recent-data limit)."""
    end = (CLOCK.now() - NANOS_PER_HOUR) // NANOS_PER_MINUTE * NANOS_PER_MINUTE
    return HistoryRequest(
        instrument=AAPL,
        kind="bars",
        start=Timestamp(end - 7 * DAY),
        end=Timestamp(end),
        bar_size=5 * NANOS_PER_MINUTE,
    )


def test_a_weeks_bars_are_whole_ordered_and_sane(alpaca: AlpacaSource) -> None:
    with SourceLoop() as loop:
        bars = [m for m in HistoryFeed(loop, alpaca, week_of_bars()) if isinstance(m, Bar)]
        loop.run(alpaca.close())
    assert bars, "Alpaca sent no AAPL bar in a week"
    assert [b.start for b in bars] == sorted({b.start for b in bars})
    assert all(b.end - b.start == 5 * NANOS_PER_MINUTE and b.low <= b.high for b in bars)


def test_trades_in_one_bar_stay_within_its_range(alpaca: AlpacaSource) -> None:
    with SourceLoop() as loop:
        bars = [m for m in HistoryFeed(loop, alpaca, week_of_bars()) if isinstance(m, Bar)]
        assert bars
        bar = bars[len(bars) // 2]
        request = HistoryRequest(instrument=AAPL, kind="trades", start=bar.start, end=bar.end)
        points = [m for m in HistoryFeed(loop, alpaca, request) if isinstance(m, Point)]
        loop.run(alpaca.close())
    assert points, "Alpaca sent bars but no trades for the same five minutes"
    times = [p.event_time for p in points]
    assert times == sorted(times)
    assert all(bar.start <= t < bar.end for t in times)
    prices = [p.values["price"] for p in points]
    assert bar.low - 1e-6 <= min(prices)
    assert max(prices) <= bar.high + 1e-6


def test_a_wrong_key_is_refused_at_once(
    alpaca: AlpacaSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APCA_API_KEY_ID", "not-a-key")
    refused = AlpacaSource(AlpacaParams())
    refused.bind(RateThrottle(refused.capabilities, CLOCK))

    async def first() -> None:
        stream = refused.subscribe([Subscription(instrument=AAPL, kind="trades")])
        await anext(stream)

    with pytest.raises(InvalidValueError, match="HTTP 40"):
        asyncio.run(asyncio.wait_for(first(), timeout=60))


def test_a_live_trade_arrives_while_the_market_is_open(alpaca: AlpacaSource) -> None:
    now = CLOCK.now()
    if market_calendar("XNYS").after_open_time(now, 0) != now:
        pytest.skip("the market is closed")

    async def first() -> Point:
        stream = alpaca.subscribe([Subscription(instrument=AAPL, kind="trades")])
        try:
            message = await anext(stream)
        finally:
            await stream.aclose()  # type: ignore[attr-defined]
        assert isinstance(message, Point)
        return message

    point = asyncio.run(asyncio.wait_for(first(), timeout=120))
    assert point.values["price"] > 0
    assert point.values["size"] > 0
    assert abs(point.event_time - CLOCK.now()) < 5 * 60 * 10**9
