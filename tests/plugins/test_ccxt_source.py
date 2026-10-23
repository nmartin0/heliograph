# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The CCXT source, offline: a fake exchange that answers as CCXT's Kraken does.

CCXT's own description of an exchange (what it offers, its timeframes, its
rate limit) is read for real; it needs no request. The pages are written in
CCXT's unified format, shaped by how Kraken answers through CCXT: trades
paged from whole seconds (so a page repeats the end of the one before), only
the latest 720 candles kept, and the newest candle still forming. Whether
the real exchange still answers that way is the live tests' job
(tests/live).
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

import ccxt
import pytest
from pydantic import ValidationError

from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Message, Point
from heliograph.core.sources import (
    HistoryRequest,
    RateLimit,
    SourceUnavailableError,
    Subscription,
)
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MINUTE, Timestamp, parse_span
from heliograph.plugins.ccxt_source import CcxtParams, CcxtSource
from tests.support.world import BTC, ETH

MIN, HOUR, MS = NANOS_PER_MINUTE, NANOS_PER_HOUR, 1_000_000
T0 = 1_791_000_000_000  # ms: 2026-10-03T03:20:00Z, a whole minute


def unified_trade(
    ms: int, price: float, amount: float = 0.5, side: str | None = "buy"
) -> dict[str, Any]:
    """A trade as CCXT's fetch_trades returns it (Kraken gives no ids but the last)."""
    return {
        "id": None,
        "timestamp": ms,
        "datetime": None,
        "symbol": "BTC/USD",
        "order": None,
        "type": "limit",
        "side": side,
        "takerOrMaker": None,
        "price": price,
        "amount": amount,
        "cost": price * amount,
        "fee": None,
        "info": [str(price), str(amount), ms / 1000, side and side[0], "l", ""],
    }  # fmt: skip


class FakeExchange:
    """Answers like CCXT's Kraken: pages from whole seconds, a short candle history."""

    def __init__(
        self,
        trades: Sequence[dict[str, Any]] = (),
        candles: Sequence[list[Any]] = (),
        *,
        keep_candles: int = 720,
        cap: int = 1000,
        live: Sequence[Any] = (),
    ) -> None:
        self.trades = list(trades)
        self.candles = list(candles)
        self.keep_candles = keep_candles
        self.cap = cap  # the most trades the exchange sends in one answer
        self.live = list(live)
        self.calls: list[tuple[Any, ...]] = []
        self.closed = False
        self.fail: Exception | None = None

    async def load_markets(self) -> dict[str, Any]:
        self.calls.append(("load_markets",))
        return {"BTC/USD": {}, "ETH/USD": {}}

    async def fetch_trades(
        self, symbol: str, since: int | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """As CCXT's Kraken answers: the exchange counts its page from the whole
        second of `since`; CCXT then drops what precedes `since`, and gives the
        page's last trade Kraken's cursor as its id. No `since`: the newest."""
        self.calls.append(("fetch_trades", symbol, since, limit))
        if self.fail is not None:
            raise self.fail
        count = min(limit or self.cap, self.cap)
        if since is None:
            return [dict(t) for t in self.trades[-count:]]
        floor = since // 1000 * 1000
        page = [dict(t) for t in self.trades if t["timestamp"] >= floor][:count]
        if page:
            page[-1]["id"] = str(page[-1]["timestamp"] * 1_000_000)
        return [t for t in page if t["timestamp"] >= since]

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[Any]]:
        self.calls.append(("fetch_ohlcv", symbol, timeframe, since, limit))
        kept = self.candles[-self.keep_candles :]
        if kept and since < kept[0][0]:
            return kept[-limit:]  # too far back: the latest ones instead
        return [c for c in kept if c[0] >= since][:limit]

    async def watch_trades(self, symbol: str) -> list[dict[str, Any]]:
        return await self._next_live()

    async def watch_trades_for_symbols(self, symbols: list[str]) -> list[dict[str, Any]]:
        self.calls.append(("watch_trades_for_symbols", tuple(symbols)))
        return await self._next_live()

    async def _next_live(self) -> list[dict[str, Any]]:
        step = self.live.pop(0)
        if isinstance(step, Exception):
            raise step
        return list(step)

    async def close(self) -> None:
        self.closed = True


class Counting:
    def __init__(self) -> None:
        self.entered = 0

    async def __aenter__(self) -> None:
        self.entered += 1

    async def __aexit__(self, *exc: object) -> None:
        pass


class Faked(CcxtSource):
    """The CCXT source talking to a fake exchange."""

    manifest = CcxtSource.manifest
    exchange: FakeExchange = FakeExchange()

    def _connect(self) -> Any:
        return Faked.exchange


def source(exchange: FakeExchange, **params: Any) -> tuple[Faked, Counting]:
    Faked.exchange = exchange
    params.setdefault("backoff", 0)  # waiting between attempts is tested on its own
    built = Faked(CcxtParams(exchange="kraken", **params))
    throttle = Counting()
    built.bind(throttle)
    return built, throttle


def read(built: CcxtSource, wanted: HistoryRequest) -> list[Message]:
    async def collect() -> list[Message]:
        try:
            return [m async for m in built.history(wanted)]
        finally:
            await built.close()

    return asyncio.run(collect())


def trades_request(start_ms: int, end_ms: int) -> HistoryRequest:
    return HistoryRequest(
        instrument=BTC, kind="trades", start=Timestamp(start_ms * MS), end=Timestamp(end_ms * MS)
    )


def bars_request(start_ms: int, end_ms: int, size: int = MIN) -> HistoryRequest:
    return HistoryRequest(
        instrument=BTC,
        kind="bars",
        start=Timestamp(start_ms * MS),
        end=Timestamp(end_ms * MS),
        bar_size=size,
    )


# -- what it declares --------------------------------------------------------------------


def test_capabilities_are_what_ccxt_declares_about_the_exchange() -> None:
    caps = CcxtSource(CcxtParams(exchange="kraken", terms=("attribution",))).capabilities
    assert caps.history == frozenset({"trades", "bars"})
    assert caps.live == frozenset({"trades"})
    assert {parse_span(s) for s in ("1m", "1h", "4h", "1d", "1w")} <= caps.bar_sizes
    assert caps.rate_limit == RateLimit(calls=1, period=10**9)  # Kraken: one a second
    assert caps.terms == frozenset({"attribution"})
    assert caps.lookback is None
    assert caps.live_delay == 0


def test_an_exchange_ccxt_does_not_know_is_refused() -> None:
    with pytest.raises(ValidationError, match="CCXT has no exchange 'krakken'"):
        CcxtParams(exchange="krakken")


def test_constructing_the_source_contacts_nothing() -> None:
    built = CcxtSource(CcxtParams(exchange="kraken"))
    assert built._client is None
    asyncio.run(built.close())  # nothing to close


# -- trades --------------------------------------------------------------------------------


def test_trades_are_read_page_by_page_each_once_in_order_within_the_request() -> None:
    """Pages of 5 from whole seconds: each page after the first repeats trades, from
    earlier in the second of the last trade delivered as well as at its instant."""
    trades = [
        unified_trade(T0 - 500, 99),  # before the start
        unified_trade(T0 + 100, 1, side="sell"),
        unified_trade(T0 + 100, 1, side="sell"),  # an identical trade: both are real
        unified_trade(T0 + 300, 3, side=None),
        unified_trade(T0 + 1_050, 4),  # repeated by page 2, before its boundary
        unified_trade(T0 + 1_100, 5),  # page 1 ends here
        unified_trade(T0 + 1_100, 6),
        unified_trade(T0 + 1_900, 7),
        unified_trade(T0 + 2_500, 8),
        unified_trade(T0 + 3_000, 9),  # at the end: excluded
    ]
    built, throttle = source(FakeExchange(trades), page=5)
    got = read(built, trades_request(T0, T0 + 3_000))
    points = [m for m in got if isinstance(m, Point)]
    assert [p.values["price"] for p in points] == [1, 1, 3, 4, 5, 6, 7, 8]
    assert [p.event_time for p in points] == [
        (T0 + d) * MS for d in (100, 100, 300, 1_050, 1_100, 1_100, 1_900, 2_500)
    ]
    assert dict(points[0].values) == {"price": 1.0, "size": 0.5, "side": -1.0}
    assert dict(points[3].values) == {"price": 4.0, "size": 0.5, "side": 1.0}
    assert "side" not in points[2].values
    assert points[0].instrument == BTC
    fetches = [c for c in Faked.exchange.calls if c[0] == "fetch_trades"]
    assert [c[2] for c in fetches] == [T0, T0 + 1_100, T0 + 2_500]
    assert throttle.entered == 1 + len(fetches)  # the market list, then every page


def test_identical_trades_at_a_page_boundary_are_each_delivered_once() -> None:
    """Three identical trades end the first page; the second repeats them from the second."""
    trades = [unified_trade(T0 + 10, 1)] + [unified_trade(T0 + 1_500, 2)] * 3
    built, _ = source(FakeExchange([*trades, unified_trade(T0 + 2_200, 3)]), page=4)
    got = read(built, trades_request(T0, T0 + 5_000))
    assert [m.values["price"] for m in got if isinstance(m, Point)] == [1, 2, 2, 2, 3]


def test_reading_trades_stops_when_the_exchange_has_nothing_newer() -> None:
    built, _ = source(FakeExchange([unified_trade(T0 + 1, 1)]), page=4)
    got = read(built, trades_request(T0, T0 + 3_600_000))
    assert len(got) == 1


def test_a_second_busier_than_a_page_stops_the_read_rather_than_return_less() -> None:
    """Six trades in one second, pages of 4: Kraken can never send the last two."""
    busy = [unified_trade(T0 + 100 * i, float(i + 1)) for i in range(6)]
    built, _ = source(FakeExchange(busy), page=4)
    with pytest.raises(SourceUnavailableError, match="cannot page past"):
        read(built, trades_request(T0, T0 + 60_000))


def test_a_page_larger_than_the_exchange_sends_still_reads_everything() -> None:
    """Asking for 10 from an exchange that sends 3: no page is ever 'full'."""
    spread = [unified_trade(T0 + 1_000 * i, float(i + 1)) for i in range(6)]
    built, _ = source(FakeExchange(spread, cap=3), page=10)
    got = read(built, trades_request(T0, T0 + 60_000))
    assert [m.values["price"] for m in got if isinstance(m, Point)] == [1, 2, 3, 4, 5, 6]


def test_an_id_on_one_page_and_not_the_next_does_not_make_a_trade_look_new() -> None:
    """CCXT's Kraken gives a page's last trade an id; the next page repeats it without."""
    trades = [unified_trade(T0 + 10, 1), unified_trade(T0 + 1_500, 2)]
    built, _ = source(FakeExchange([*trades, unified_trade(T0 + 1_700, 3)]), page=2)
    got = read(built, trades_request(T0, T0 + 5_000))
    assert [m.values["price"] for m in got if isinstance(m, Point)] == [1, 2, 3]


def candle(ms: int, close: float = 2.0, volume: float | None = 1.0) -> list[Any]:
    return [ms, 2.0, max(2.0, close) + 1, min(2.0, close) - 1, close, volume]


def test_bars_are_the_whole_candles_within_the_request_page_by_page() -> None:
    candles = [candle(T0 + i * 60_000, close=float(i + 1)) for i in range(10)]
    built, _ = source(FakeExchange(candles=candles), page=3)
    got = read(built, bars_request(T0 + 60_000, T0 + 6 * 60_000))
    bars = [m for m in got if isinstance(m, Bar)]
    assert [b.close for b in bars] == [2, 3, 4, 5, 6]
    assert all(b.end - b.start == MIN for b in bars)
    assert bars[0].start == (T0 + 60_000) * MS


def test_the_candle_still_forming_is_never_taken_for_a_finished_bar() -> None:
    """The request ends mid-candle: that candle is not whole within it."""
    candles = [candle(T0), candle(T0 + 3_600_000)]  # hourly; the second is forming
    built, _ = source(FakeExchange(candles=candles))
    got = read(built, bars_request(T0, T0 + 3_600_000 + 1_000, size=HOUR))
    assert [m.start for m in got if isinstance(m, Bar)] == [T0 * MS]


def test_a_start_further_back_than_the_exchange_keeps_candles_stops_the_read() -> None:
    candles = [candle(T0 + i * 60_000) for i in range(800)]  # Kraken keeps the last 720
    built, _ = source(FakeExchange(candles=candles), page=720)
    with pytest.raises(SourceUnavailableError, match="it keeps a shorter history"):
        read(built, bars_request(T0, T0 + 800 * 60_000))


def test_a_candle_without_volume_is_refused() -> None:
    built, _ = source(FakeExchange(candles=[candle(T0, volume=None)]))
    with pytest.raises(InvalidValueError, match="has no volume"):
        read(built, bars_request(T0, T0 + 60_000))


# -- refusals and errors -----------------------------------------------------------------------


def test_an_instrument_on_another_venue_is_refused() -> None:
    built, _ = source(FakeExchange())
    elsewhere = HistoryRequest(
        instrument=BTC.__class__(
            symbol="BTC/USD", venue="coinbase", asset_class="crypto", quote_currency="USD"
        ),
        kind="trades",
        start=Timestamp(0),
        end=Timestamp(MIN),
    )
    with pytest.raises(InvalidValueError, match="coinbase:BTC/USD is not on kraken"):
        read(built, elsewhere)


@pytest.mark.parametrize(
    ("failure", "error", "reason"),
    [
        (ccxt.RequestTimeout("timed out"), SourceUnavailableError, "kraken: timed out"),
        (ccxt.RateLimitExceeded("slow down"), SourceUnavailableError, "kraken: slow down"),
        (ccxt.ExchangeNotAvailable("down"), SourceUnavailableError, "kraken: down"),
        (ccxt.BadSymbol("no such pair"), InvalidValueError, "kraken: no such pair"),
        (ccxt.NotSupported("no candles"), InvalidValueError, "kraken: no candles"),
        (ccxt.AuthenticationError("bad key"), InvalidValueError, "kraken: bad key"),
        (ccxt.PermissionDenied("not allowed"), InvalidValueError, "kraken: not allowed"),
        (ccxt.OperationRejected("rejected"), InvalidValueError, "kraken: rejected"),
        (ccxt.DDoSProtection("banned"), SourceUnavailableError, "kraken: banned"),
        (ccxt.ExchangeError("odd"), SourceUnavailableError, "kraken: odd"),
    ],
)
def test_ccxt_errors_become_the_ports_errors(
    failure: Exception, error: type[Exception], reason: str
) -> None:
    exchange = FakeExchange()
    exchange.fail = failure
    built, _ = source(exchange)
    with pytest.raises(error, match=reason):
        read(built, trades_request(T0, T0 + 1_000))


def test_a_source_used_before_it_is_bound_is_refused() -> None:
    Faked.exchange = FakeExchange()
    with pytest.raises(InvalidValueError, match="used before it was bound"):
        read(Faked(CcxtParams(exchange="kraken")), trades_request(T0, T0 + 1))


# -- live --------------------------------------------------------------------------------------


async def first(stream: AsyncIterator[Message], n: int) -> list[Message]:
    out = []
    async for message in stream:
        out.append(message)
        if len(out) == n:
            break
    return out


def test_live_trades_are_delivered_as_the_exchange_sends_them() -> None:
    eth = unified_trade(T0 + 2, 7) | {"symbol": "ETH/USD"}
    batches = [[unified_trade(T0, 1)], [], [unified_trade(T0 + 1, 2), eth]]
    built, _ = source(FakeExchange(live=batches))
    subscriptions = [
        Subscription(instrument=BTC, kind="trades"),
        Subscription(instrument=ETH, kind="trades"),
    ]

    async def scenario() -> list[Message]:
        stream = built.subscribe(subscriptions)
        try:
            return await first(stream, 3)
        finally:
            await stream.aclose()  # type: ignore[attr-defined]
            await built.close()

    got = [m for m in asyncio.run(scenario()) if isinstance(m, Point)]
    assert [(p.instrument, p.values["price"]) for p in got] == [(BTC, 1), (BTC, 2), (ETH, 7)]
    assert ("watch_trades_for_symbols", ("BTC/USD", "ETH/USD")) in Faked.exchange.calls


def test_one_live_symbol_is_watched_alone_and_its_failure_is_the_ports_error() -> None:
    built, _ = source(FakeExchange(live=[[unified_trade(T0, 1)], ccxt.NetworkError("dropped")]))

    async def scenario() -> list[Message]:
        return [m async for m in built.subscribe([Subscription(instrument=BTC, kind="trades")])]

    with pytest.raises(SourceUnavailableError, match="kraken: dropped"):
        asyncio.run(scenario())


def test_live_bars_are_refused_the_exchange_sends_trades() -> None:
    built, _ = source(FakeExchange())

    async def scenario() -> list[Message]:
        bars = Subscription(instrument=BTC, kind="bars", bar_size=MIN)
        return [m async for m in built.subscribe([bars])]

    with pytest.raises(InvalidValueError, match="live trades only"):
        asyncio.run(scenario())


def test_health_is_whether_the_exchange_answers_and_close_releases_it() -> None:
    exchange = FakeExchange()
    built, _ = source(exchange)
    assert asyncio.run(built.health_check()).ok
    asyncio.run(built.close())
    assert exchange.closed
    failing = FakeExchange()

    async def broken() -> dict[str, Any]:
        raise ccxt.ExchangeNotAvailable("maintenance")

    failing.load_markets = broken  # type: ignore[method-assign]
    built, _ = source(failing)
    health = asyncio.run(built.health_check())
    assert not health.ok
    assert "maintenance" in health.detail


def test_a_request_that_failed_for_now_is_tried_again_through_the_throttle() -> None:
    exchange = FakeExchange([unified_trade(T0 + 100, 100.0)])
    failures = [ccxt.RequestTimeout("timed out"), ccxt.RateLimitExceeded("slow down")]
    real = exchange.fetch_trades

    async def flaky(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        if failures:
            raise failures.pop(0)
        return await real(*args, **kwargs)

    exchange.fetch_trades = flaky  # type: ignore[method-assign]
    built, throttle = source(exchange)
    got = read(built, trades_request(T0, T0 + 1_000))
    assert [m.values["price"] for m in got if isinstance(m, Point)] == [100.0]
    assert throttle.entered >= 4  # markets, two failures, and the page that came


def test_a_request_still_failing_after_its_retries_is_unavailable() -> None:
    exchange = FakeExchange()
    exchange.fail = ccxt.RequestTimeout("timed out")
    built, throttle = source(exchange, retries=2)
    with pytest.raises(SourceUnavailableError, match="timed out \\(tried 3 times\\)"):
        read(built, trades_request(T0, T0 + 1_000))
    assert throttle.entered == 1 + 3  # the markets, then the page three times


def test_trade_history_is_offered_only_where_ccxt_pages_it_by_time() -> None:
    """Bybit's fetch_trades ignores `since`: its trades would come back short, silently."""
    assert "trades" not in CcxtSource(CcxtParams(exchange="bybit")).capabilities.history
    assert "bars" in CcxtSource(CcxtParams(exchange="bybit")).capabilities.history
    trusted = CcxtSource(CcxtParams(exchange="bybit", trust_since=True)).capabilities
    assert "trades" in trusted.history


def test_a_candle_off_the_grid_of_its_size_is_refused() -> None:
    """As Kraken's "2w", 15-day candles taken for 14-day ones."""
    two_weeks = 14 * 24 * HOUR // MS
    day = 24 * HOUR // MS
    start = (T0 // two_weeks) * two_weeks
    candles = [[start + k * (two_weeks + day), 1, 2, 0.5, 1.5, 10] for k in range(3)]
    built, _ = source(FakeExchange(candles=candles))
    wanted = HistoryRequest(
        instrument=BTC,
        kind="bars",
        start=Timestamp(start * MS),
        end=Timestamp((start + 3 * (two_weeks + day)) * MS),
        bar_size=two_weeks * MS,
    )
    with pytest.raises(InvalidValueError, match="off the 2w grid: its 2w is not 2w"):
        read(built, wanted)


def test_every_source_on_one_exchange_shares_one_rate_key() -> None:
    a = CcxtSource(CcxtParams(exchange="kraken", page=1000))
    b = CcxtSource(CcxtParams(exchange="kraken", page=500))
    assert a.rate_key == b.rate_key != CcxtSource(CcxtParams(exchange="bybit")).rate_key


def test_weekly_candles_may_start_on_a_monday() -> None:
    """Exchanges start weekly candles on Mondays, not on 1970's Thursdays."""
    week = 7 * 24 * HOUR // MS
    monday = 1_791_158_400_000  # 2026-10-05T00:00:00Z, a Monday
    candles = [[monday + k * week, 1, 2, 0.5, 1.5, 10] for k in range(3)]
    built, _ = source(FakeExchange(candles=candles))
    wanted = HistoryRequest(
        instrument=BTC,
        kind="bars",
        start=Timestamp(monday * MS),
        end=Timestamp((monday + 3 * week) * MS),
        bar_size=week * MS,
    )
    assert len([m for m in read(built, wanted) if isinstance(m, Bar)]) == 3


def test_an_exchange_that_thinks_it_is_attacked_is_never_asked_again() -> None:
    """Binance's 418 bans for longer each time it is asked again."""
    exchange = FakeExchange()
    exchange.fail = ccxt.DDoSProtection("banned")
    built, throttle = source(exchange, retries=5)
    with pytest.raises(SourceUnavailableError, match=r"kraken: banned$"):
        read(built, trades_request(T0, T0 + 1_000))
    assert throttle.entered == 1 + 1  # the markets, then the page once


def test_an_error_asking_again_cannot_mend_is_never_retried() -> None:
    exchange = FakeExchange()
    exchange.fail = ccxt.BadSymbol("no such pair")
    built, throttle = source(exchange, retries=5)
    with pytest.raises(InvalidValueError):
        read(built, trades_request(T0, T0 + 1_000))
    assert throttle.entered == 2


def test_retries_wait_longer_each_time(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("heliograph.plugins.ccxt_source.asyncio.sleep", sleep)
    exchange = FakeExchange()
    exchange.fail = ccxt.RequestTimeout("timed out")
    built, _ = source(exchange, retries=3, backoff=2.0)
    with pytest.raises(SourceUnavailableError, match="tried 4 times"):
        read(built, trades_request(T0, T0 + 1_000))
    assert waits == [2.0, 4.0, 8.0]


def test_a_live_error_asking_again_cannot_mend_is_the_ports_invalid_value() -> None:
    built, _ = source(FakeExchange(live=[ccxt.BadSymbol("no such pair")]))

    async def scenario() -> list[Message]:
        return [m async for m in built.subscribe([Subscription(instrument=BTC, kind="trades")])]

    with pytest.raises(InvalidValueError, match="kraken: no such pair"):
        asyncio.run(scenario())


def test_a_timeframe_whose_own_value_is_another_size_is_not_offered() -> None:
    """Kraken's "2w" is 21600 minutes (15 days); Bitstamp's "1w" 259200 seconds (3 days)."""
    kraken = CcxtSource(CcxtParams(exchange="kraken")).capabilities.bar_sizes
    assert parse_span("1w") in kraken
    assert parse_span("2w") not in kraken
    bitstamp = CcxtSource(CcxtParams(exchange="bitstamp")).capabilities.bar_sizes
    assert parse_span("1d") in bitstamp
    assert parse_span("1w") not in bitstamp


def test_why_trade_history_is_withheld_is_said() -> None:
    caps = CcxtSource(CcxtParams(exchange="bybit")).capabilities
    assert "not known to page by time" in caps.withheld
    assert "trust_since" in caps.withheld
    assert CcxtSource(CcxtParams(exchange="kraken")).capabilities.withheld == ""
    assert "not known to page by time" in caps.problems("trades", live=False)[0]


def test_no_wait_before_a_retry_is_longer_than_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("heliograph.plugins.ccxt_source.asyncio.sleep", sleep)
    exchange = FakeExchange()
    exchange.fail = ccxt.RequestTimeout("timed out")
    built, _ = source(exchange, retries=8, backoff=1.0)
    with pytest.raises(SourceUnavailableError):
        read(built, trades_request(T0, T0 + 1_000))
    assert waits == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
