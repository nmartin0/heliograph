# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Crypto market data through CCXT: history and live trades from one exchange (D21).

The exchange is a parameter (``exchange = "kraken"``), and an instrument's
venue must name it; its symbol is CCXT's unified symbol (``BTC/USD``).

**Capabilities** come from what CCXT declares about the exchange, without a
request: trades and bars in history where it offers them, live trades where
CCXT can watch them, the bar sizes among its timeframes that are exact spans,
and its rate limit (one request per ``rateLimit`` milliseconds). CCXT's own
throttle is turned off: the app's throttle does the job (D13), around every
request, the market list and every page included. Live data comes over a
websocket and is not throttled.

**History** is read page by page from the start of the request. Trades are
``Point``s with ``price``, ``size`` and, when the exchange says, ``side``
(1 for a buy, -1 for a sell). Each page starts at the last instant already
delivered, so at that instant as many copies of each trade as were
delivered are dropped. Trades are recognised by what they are, never by an
id, which CCXT gives Kraken's trades only at the end of a page. When a page
brings nothing new the source asks for the newest trade: if none is later,
the read is complete; if one is, the pages cannot reach it, and the read
stops with an error rather than return less. (Kraken counts a page from the
whole second and CCXT then discards what precedes `since`, so a second
holding more trades than a page can never be paged past.)

Bars come as the exchange's candles, and only whole ones: a bar ending
after the request does is left out, so the candle still forming is never
taken for a finished one. An exchange that keeps a short history of
candles (Kraken, 720) answers an early start with its latest ones; a first
candle a bar or more after the start says so (or that nothing traded), and
the read stops rather than leave a hole.

Errors: CCXT's network errors (time-outs, rate limits, an exchange down) are
``SourceUnavailableError``; a symbol the exchange does not list is
``InvalidValueError``.
"""

import math
from collections import Counter
from collections.abc import AsyncIterator, Mapping, Sequence
from functools import cache
from typing import Any

import ccxt
import ccxt.async_support
import ccxt.pro
from pydantic import field_validator

from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Instrument, Message, Point
from heliograph.core.sources import (
    Capabilities,
    DataKind,
    Health,
    HistoryRequest,
    RateLimit,
    SourceUnavailableError,
    Subscription,
    Term,
    Throttle,
)
from heliograph.core.time import NANOS_PER_MILLISECOND, Timestamp, format_span, parse_span
from heliograph.plugins.manifest import Manifest, Params, Plugin, param

MS = NANOS_PER_MILLISECOND


class CcxtParams(Params):
    exchange: str = param(..., description="CCXT's id of the exchange, such as kraken")
    page: int = param(1000, description="Rows asked for per request", ge=1, le=100_000)
    timeout: float = param(30.0, description="Seconds before a request is abandoned", ge=1)
    terms: tuple[Term, ...] = param(
        (), description="Conditions of the exchange's terms for its market data"
    )

    @field_validator("exchange")
    @classmethod
    def _known(cls, exchange: str) -> str:
        if exchange not in ccxt.exchanges:
            raise ValueError(f"CCXT has no exchange {exchange!r}")
        return exchange


@cache
def _described(exchange: str) -> Mapping[str, Any]:
    """What CCXT declares about an exchange, read without a request."""
    rest = getattr(ccxt, exchange)()
    live = getattr(ccxt.pro, exchange, None)
    return {
        "has": dict(rest.has),
        "watch_trades": bool(live is not None and live().has.get("watchTrades")),
        "timeframes": dict(rest.timeframes or {}),
        "rate_limit": float(rest.rateLimit),
    }


def _span_or_none(timeframe: str) -> int | None:
    try:
        return parse_span(timeframe)
    except InvalidValueError:
        return None  # such as CCXT's "1M", a calendar month


class CcxtSource(Plugin[CcxtParams]):
    """History and live trades, and history candles, from one exchange through CCXT."""

    manifest = Manifest(
        kind="data_sources",
        name="ccxt",
        version="1",
        summary="Crypto history and live trades through CCXT",
        params=CcxtParams,
    )

    def __init__(self, params: CcxtParams) -> None:
        super().__init__(params)
        self._throttle: Throttle | None = None
        self._client: Any = None
        self._markets_loaded = False

    # -- what it declares ------------------------------------------------------------

    @property
    def capabilities(self) -> Capabilities:
        described = _described(self.params.exchange)
        has = described["has"]
        history: set[DataKind] = set()
        if has.get("fetchTrades"):
            history.add("trades")
        sizes = {s for t in described["timeframes"] if (s := _span_or_none(t)) is not None}
        if has.get("fetchOHLCV") and sizes:
            history.add("bars")
        period = max(1, round(described["rate_limit"] * MS))
        return Capabilities(
            history=frozenset(history),
            live=frozenset({"trades"} if described["watch_trades"] else set()),
            bar_sizes=frozenset(sizes if "bars" in history else set()),
            rate_limit=RateLimit(calls=1, period=period),
            terms=frozenset(self.params.terms),
        )

    def bind(self, throttle: Throttle) -> None:
        self._throttle = throttle

    # -- the exchange ------------------------------------------------------------------

    def _connect(self) -> Any:
        """The CCXT client, made on first use; constructing the source contacts nothing."""
        exchange_class = getattr(ccxt.pro, self.params.exchange, None) or getattr(
            ccxt.async_support, self.params.exchange
        )
        return exchange_class(
            {"enableRateLimit": False, "timeout": int(self.params.timeout * 1000)}
        )

    @property
    def _exchange(self) -> Any:
        if self._client is None:
            self._client = self._connect()
        return self._client

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """One request through the throttle, with CCXT's errors translated."""
        throttle = self._throttle
        if throttle is None:
            raise InvalidValueError("the CCXT source was used before it was bound")
        async with throttle:
            try:
                return await getattr(self._exchange, method)(*args, **kwargs)
            except ccxt.BadSymbol as exc:
                raise InvalidValueError(f"{self.params.exchange}: {exc}") from exc
            except (ccxt.NetworkError, ccxt.ExchangeError) as exc:
                raise SourceUnavailableError(f"{self.params.exchange}: {exc}") from exc

    async def _markets(self) -> None:
        if not self._markets_loaded:
            await self._call("load_markets")
            self._markets_loaded = True

    def _symbol(self, instrument: Instrument) -> str:
        if instrument.venue != self.params.exchange:
            raise InvalidValueError(
                f"{instrument.key} is not on {self.params.exchange}; this source reads only it"
            )
        return instrument.symbol

    # -- history -----------------------------------------------------------------------

    async def history(self, request: HistoryRequest) -> AsyncIterator[Message]:
        """The request's trades or whole bars, page by page, in time order.

        Raises:
            SourceUnavailableError: if the exchange cannot be reached or
                refuses, or keeps too short a history of candles.
            InvalidValueError: for an instrument on another exchange, or a
                symbol it does not list.
        """
        symbol = self._symbol(request.instrument)
        await self._markets()
        pages = self._trades if request.kind == "trades" else self._bars
        async for message in pages(symbol, request):
            yield message

    async def _trades(self, symbol: str, request: HistoryRequest) -> AsyncIterator[Message]:
        since = request.start // MS
        last: int | None = None  # the instant of the last trade delivered, in ms
        at_last: Counter[tuple[Any, ...]] = Counter()  # the trades delivered then
        while True:
            page = await self._call("fetch_trades", symbol, since=since, limit=self.params.page)
            # A page starts at the last instant delivered (CCXT drops anything
            # earlier), so it repeats the copies of each trade sent at it.
            boundary, repeats = last, at_last.copy()
            fresh = 0
            for trade in page:
                ms = trade.get("timestamp")
                if ms is None:
                    raise InvalidValueError(f"{self.params.exchange}: a trade without a time")
                key = _trade_key(trade)
                if ms == boundary and repeats[key] > 0:
                    repeats[key] -= 1
                    continue
                if ms != last:
                    last, at_last = ms, Counter()
                at_last[key] += 1
                fresh += 1
                happened = ms * MS
                if happened >= request.end:
                    return
                if happened >= request.start:
                    yield _point(request.instrument, trade)
            if fresh == 0:
                # Nothing new: either no trade comes after the last one read, or the
                # exchange cannot page past it. How full the page was cannot tell
                # (CCXT drops what precedes `since` after the exchange has counted
                # its page), so ask for the newest trade there is.
                await self._caught_up(symbol, since if last is None else last + 1)
                return
            assert last is not None
            since = last

    async def _caught_up(self, symbol: str, after: int) -> None:
        """Return if no trade at or after ``after`` ms exists; raise if one does.

        Raises:
            SourceUnavailableError: there are trades the pages could not reach,
                such as more in one second than Kraken sends in a page.
        """
        newest = await self._call("fetch_trades", symbol, limit=1)
        times = [t["timestamp"] for t in newest if t.get("timestamp") is not None]
        if times and max(times) >= after:
            raise SourceUnavailableError(
                f"{self.params.exchange}: cannot page past {after - 1} ms, though it has "
                f"trades up to {max(times)} ms (more trades at one instant or in one "
                "second than a page holds?); nothing was kept"
            )

    async def _bars(self, symbol: str, request: HistoryRequest) -> AsyncIterator[Message]:
        size = request.bar_size
        assert size is not None  # a bars request has a size
        timeframe = format_span(size)
        since = request.start // MS
        following = request.start  # where the next bar may start
        first = True
        while True:
            page = await self._call(
                "fetch_ohlcv", symbol, timeframe, since=since, limit=self.params.page
            )
            fresh = [candle for candle in page if candle[0] * MS >= following]
            if first and fresh and fresh[0][0] * MS - request.start >= size:
                raise SourceUnavailableError(
                    f"{self.params.exchange} sends {timeframe} candles from "
                    f"{fresh[0][0] * MS}, not from {request.start}: it keeps a shorter history, "
                    "or nothing traded in between; start later"
                )
            first = False
            if not fresh:
                return
            for candle in fresh:
                start = candle[0] * MS
                if start + size > request.end:  # not whole within the request
                    return
                yield _bar(request.instrument, candle, size)
                following = start + size
            since = following // MS

    # -- live --------------------------------------------------------------------------

    async def subscribe(self, subscriptions: Sequence[Subscription]) -> AsyncIterator[Message]:
        """Live trades for the subscriptions, as the exchange sends them.

        Raises:
            InvalidValueError: for a subscription to bars, or to an instrument
                on another exchange.
            SourceUnavailableError: if the exchange cannot be reached.
        """
        if not subscriptions:
            return
        wanted: dict[str, Instrument] = {}
        for subscription in subscriptions:
            if subscription.kind != "trades":
                raise InvalidValueError("the CCXT source sends live trades only")
            wanted[self._symbol(subscription.instrument)] = subscription.instrument
        await self._markets()
        symbols = sorted(wanted)
        while True:
            try:
                if len(symbols) == 1:
                    trades = await self._exchange.watch_trades(symbols[0])
                else:
                    trades = await self._exchange.watch_trades_for_symbols(symbols)
            except (ccxt.NetworkError, ccxt.ExchangeError) as exc:
                raise SourceUnavailableError(f"{self.params.exchange}: {exc}") from exc
            for trade in trades:
                yield _point(wanted[trade["symbol"]], trade)

    # -- the rest ----------------------------------------------------------------------

    async def health_check(self) -> Health:
        try:
            await self._markets()
        except (SourceUnavailableError, InvalidValueError) as exc:
            return Health(ok=False, detail=str(exc))
        return Health(ok=True)

    async def close(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            self._markets_loaded = False  # a new client loads them again, throttled
            await client.close()


def _trade_key(trade: Mapping[str, Any]) -> tuple[Any, ...]:
    """What a trade is, to recognise it when a page repeats it.

    Never its id: an id can appear on one page and not the next (CCXT gives
    Kraken's last trade of each page its cursor as an id), and the same trade
    would then look new. Identical trades are told apart by counting copies.
    """
    return (trade["timestamp"], trade["price"], trade["amount"], trade.get("side"))


def _point(instrument: Instrument, trade: Mapping[str, Any]) -> Point:
    at = Timestamp(trade["timestamp"] * MS)
    values = {"price": trade["price"], "size": trade["amount"]}
    side = trade.get("side")
    if side in ("buy", "sell"):
        values["side"] = 1.0 if side == "buy" else -1.0
    return Point(instrument=instrument, event_time=at, received_at=at, values=values)


def _bar(instrument: Instrument, candle: Sequence[Any], size: int) -> Bar:
    ms, o, h, low, c, volume = candle[:6]
    if volume is None or (isinstance(volume, float) and math.isnan(volume)):
        raise InvalidValueError(f"{instrument.key}: the candle at {ms} ms has no volume")
    start = Timestamp(ms * MS)
    return Bar(
        instrument=instrument,
        start=start,
        end=Timestamp(start + size),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=volume,
    )
