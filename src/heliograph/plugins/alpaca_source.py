# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""US stock data from Alpaca: history and live trades through ``alpaca-py`` (D21, D62).

The instrument's venue is ``alpaca`` and its symbol is the ticker. Only market data is used:
nothing here can place an order. Keys are named by environment variables (``key_env``,
``secret_env``), read on first use, never written in a configuration.

**History** comes from the REST client, which is synchronous, so each request runs in a worker
thread, one at a time, through the app's throttle (Alpaca's free plan allows 200 a minute).
Pages are asked for one at a time from the last instant already delivered, so a page that
brings nothing new ends the read; trades already delivered at that instant are dropped by their
exchange and id. Bars are only whole ones, as with CCXT. Prices are what traded (``raw``, not
adjusted for splits). Times arrive to the microsecond.

**Live** trades come from ``alpaca-py``'s stream, which owns its own event loop, so it runs in a
worker thread and hands trades over to ours. The stream reconnects by itself without end, so a
dead feed shows as silence rather than as an error: set ``halt_after_silence`` on the strategy
(with the mount's ``calendar``, D61). A bad key would also be retried without end, so a
subscription first makes one cheap request, which refuses a bad key at once. A trade seen
twice is dropped, and one without a time is skipped and counted.
"""

import asyncio
import contextlib
import os
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Literal

from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.live.stock import StockDataStream
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest, StockTradesRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Instrument, Message, Point
from heliograph.core.sources import (
    Capabilities,
    Health,
    HistoryRequest,
    RateLimit,
    SourceUnavailableError,
    Subscription,
    Throttle,
)
from heliograph.core.time import (
    NANOS_PER_HOUR,
    NANOS_PER_MINUTE,
    NANOS_PER_SECOND,
    Timestamp,
    format_span,
)
from heliograph.plugins.conformance import Conformance
from heliograph.plugins.manifest import Manifest, Params, Plugin, param

VENUE = "alpaca"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SEEN = 5000
LONGEST_WAIT = 60.0
FREE_CALLS_PER_MINUTE = 200

BAR_SIZES: Mapping[int, TimeFrame] = {
    NANOS_PER_MINUTE: TimeFrame(1, TimeFrameUnit.Minute),
    5 * NANOS_PER_MINUTE: TimeFrame(5, TimeFrameUnit.Minute),
    15 * NANOS_PER_MINUTE: TimeFrame(15, TimeFrameUnit.Minute),
    30 * NANOS_PER_MINUTE: TimeFrame(30, TimeFrameUnit.Minute),
    NANOS_PER_HOUR: TimeFrame(1, TimeFrameUnit.Hour),
}
"""The intraday sizes offered: a day, a week or a month is not a fixed span."""


class AlpacaParams(Params):
    feed: Literal["iex", "sip"] = param(
        "iex", description="iex (one exchange, free) or sip (all exchanges, a paid plan)"
    )
    key_env: str = param("APCA_API_KEY_ID", description="Environment variable holding the key id")
    secret_env: str = param(
        "APCA_API_SECRET_KEY", description="Environment variable holding the secret key"
    )
    page: int = param(10_000, description="Rows asked for per request", ge=1, le=10_000)
    retries: int = param(
        3, description="Times a request refused for now is tried again", ge=0, le=20
    )
    backoff: float = param(
        2.0, description="Seconds before the first retry, doubling for each after", ge=0.0, le=600.0
    )


def _when(nanos: int) -> datetime:
    return EPOCH + timedelta(microseconds=nanos // 1000)


def _nanos(moment: datetime) -> Timestamp:
    """``moment`` as nanoseconds since 1970, by integer arithmetic (a float loses digits)."""
    if moment.tzinfo is None:
        raise InvalidValueError(f"Alpaca sent a time without a zone: {moment}")
    return Timestamp((moment - EPOCH) // timedelta(microseconds=1) * 1000)


class AlpacaSource(Plugin[AlpacaParams]):
    """History and live trades of US stocks from Alpaca."""

    manifest = Manifest(
        kind="data_sources",
        name="alpaca",
        version="1",
        summary="US stock history and live trades through alpaca-py",
        params=AlpacaParams,
    )
    conformance = Conformance(params={})  # contacts nothing when built

    history_client: ClassVar[Callable[[str, str], Any] | None] = None
    """Tests put a fake REST client's maker here (key, secret); nothing else does."""
    stream: ClassVar[Callable[[str, str, str], Any] | None] = None
    """Tests put a fake stream's maker here (key, secret, feed); nothing else does."""

    def __init__(self, params: AlpacaParams) -> None:
        super().__init__(params)
        self._throttle: Throttle | None = None
        self._client: Any = None
        self.dropped = {"undated": 0, "repeated": 0}
        """Live trades not passed on, by reason."""

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            history=frozenset({"trades", "bars"}),
            live=frozenset({"trades"}),
            bar_sizes=frozenset(BAR_SIZES),
            rate_limit=RateLimit(calls=FREE_CALLS_PER_MINUTE, period=60 * NANOS_PER_SECOND),
        )

    def bind(self, throttle: Throttle) -> None:
        self._throttle = throttle

    @property
    def rate_key(self) -> str:
        return "alpaca"

    # -- the service -----------------------------------------------------------------

    def _keys(self) -> tuple[str, str]:
        found = []
        for variable in (self.params.key_env, self.params.secret_env):
            value = os.environ.get(variable)
            if not value:
                raise InvalidValueError(f"{variable} is not set (it holds an Alpaca key)")
            found.append(value)
        return found[0], found[1]

    def _rest(self) -> Any:
        if self._client is None:
            key, secret = self._keys()
            make = type(self).history_client or StockHistoricalDataClient
            self._client = make(key, secret)
        return self._client

    def _symbol(self, instrument: Instrument) -> str:
        if instrument.venue != VENUE:
            raise InvalidValueError(
                f"{instrument.key} is not on {VENUE}; this source reads only it"
            )
        return instrument.symbol

    async def _call(self, method: str, request: Any) -> Any:
        """One request through the throttle in a worker thread, with errors translated.

        A request refused for now (a 429, a 5xx, a connection that failed) is tried again, each
        time through the throttle, up to ``retries`` times; one asking again cannot mend (a
        refused key, a plan without the data, a bad symbol) is ``InvalidValueError`` at once.
        """
        throttle = self._throttle
        if throttle is None:
            raise InvalidValueError("the Alpaca source was used before it was bound")
        client = self._rest()
        attempt = 0
        while True:
            async with throttle:
                try:
                    return await asyncio.to_thread(getattr(client, method), request)
                except APIError as exc:
                    status = exc.status_code
                    if status is None or status == 429 or status >= 500:
                        failure = f"{exc} (HTTP {status})"
                    else:
                        raise InvalidValueError(f"alpaca: {exc} (HTTP {status})") from exc
                except OSError as exc:  # requests' errors are IOErrors
                    failure = f"{type(exc).__name__}: {exc}"
            if attempt >= self.params.retries:
                raise SourceUnavailableError(f"alpaca: {failure} (tried {attempt + 1} times)")
            await asyncio.sleep(min(self.params.backoff * 2**attempt, LONGEST_WAIT))
            attempt += 1

    # -- history ---------------------------------------------------------------------

    async def history(self, request: HistoryRequest) -> AsyncIterator[Message]:
        """The request's trades or whole bars, page by page, in time order.

        Raises:
            SourceUnavailableError: if Alpaca cannot be reached or keeps refusing.
            InvalidValueError: for an instrument on another venue, a refused key or plan, or
                a bar size Alpaca does not have.
        """
        symbol = self._symbol(request.instrument)
        pages = self._trades if request.kind == "trades" else self._bars
        async for message in pages(symbol, request):
            yield message

    async def _trades(self, symbol: str, request: HistoryRequest) -> AsyncIterator[Message]:
        cursor = request.start
        delivered: set[tuple[str, str]] = set()  # trades already given at ``cursor``
        while True:
            reply = await self._call(
                "get_stock_trades",
                StockTradesRequest(
                    symbol_or_symbols=symbol,
                    start=_when(cursor),
                    end=_when(request.end),
                    limit=self.params.page,
                    feed=DataFeed(self.params.feed),
                ),
            )
            page = reply.data.get(symbol, [])
            fresh = 0
            for trade in page:
                at = _nanos(trade.timestamp)
                key = (str(trade.exchange), str(trade.id))
                if at < cursor or (at == cursor and key in delivered):
                    continue
                if at >= request.end:
                    return
                if at > cursor:
                    cursor, delivered = at, set()
                delivered.add(key)
                fresh += 1
                yield _point(request.instrument, trade, at)
            if len(page) < self.params.page:
                return
            if not fresh:
                raise SourceUnavailableError(
                    f"alpaca: a full page of {symbol} trades at {cursor} brought nothing new; "
                    "the read cannot move past it"
                )

    async def _bars(self, symbol: str, request: HistoryRequest) -> AsyncIterator[Message]:
        size = request.bar_size
        assert size is not None  # a bars request has a size
        frame = BAR_SIZES.get(size)
        if frame is None:
            raise InvalidValueError(f"alpaca has no {format_span(size)} bars")
        following = request.start  # where the next bar may start
        while True:
            reply = await self._call(
                "get_stock_bars",
                StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=frame,
                    start=_when(following),
                    end=_when(request.end),
                    limit=self.params.page,
                    feed=DataFeed(self.params.feed),
                    adjustment=Adjustment.RAW,
                ),
            )
            page = reply.data.get(symbol, [])
            for bar in page:
                start = _nanos(bar.timestamp)
                if start < following:
                    continue
                if start + size > request.end:  # not whole within the request
                    return
                yield _bar(request.instrument, bar, start, size)
                following = Timestamp(start + size)
            if len(page) < self.params.page:
                return

    # -- live ------------------------------------------------------------------------

    async def subscribe(self, subscriptions: Sequence[Subscription]) -> AsyncIterator[Message]:
        """Live trades for the subscriptions, as Alpaca sends them.

        Raises:
            InvalidValueError: for a subscription to bars, an instrument on another venue, or
                a refused key.
            SourceUnavailableError: if the stream ends, or Alpaca cannot be reached.
        """
        if not subscriptions:
            return
        wanted: dict[str, Instrument] = {}
        for subscription in subscriptions:
            if subscription.kind != "trades":
                raise InvalidValueError("the Alpaca source sends live trades only")
            wanted[self._symbol(subscription.instrument)] = subscription.instrument
        symbols = sorted(wanted)
        # The stream retries a refused key without end; one request finds it out at once.
        await self._call(
            "get_stock_latest_trade", StockLatestTradeRequest(symbol_or_symbols=symbols)
        )
        key, secret = self._keys()
        loop = asyncio.get_running_loop()
        arrivals: asyncio.Queue[Any] = asyncio.Queue()

        async def handler(trade: Any) -> None:
            loop.call_soon_threadsafe(arrivals.put_nowait, trade)

        make = type(self).stream or _real_stream
        stream = make(key, secret, self.params.feed)
        stream.subscribe_trades(handler, *symbols)

        def run() -> None:
            try:
                stream.run()
            finally:
                loop.call_soon_threadsafe(arrivals.put_nowait, None)

        worker = asyncio.ensure_future(asyncio.to_thread(run))
        seen: OrderedDict[tuple[str, str, str], None] = OrderedDict()
        try:
            while True:
                trade = await arrivals.get()
                if trade is None:
                    raise SourceUnavailableError("alpaca: the trade stream ended")
                if trade.timestamp is None:
                    self.dropped["undated"] += 1
                    continue
                identity = (trade.symbol, str(trade.exchange), str(trade.id))
                if identity in seen:
                    self.dropped["repeated"] += 1
                    continue
                seen[identity] = None
                if len(seen) > SEEN:
                    seen.popitem(last=False)
                yield _point(wanted[trade.symbol], trade, _nanos(trade.timestamp))
        finally:
            with contextlib.suppress(AttributeError, TimeoutError, RuntimeError):
                # (the stream had not started, or is already down)
                await asyncio.wait_for(asyncio.to_thread(stream.stop), timeout=10)
            await asyncio.wait_for(asyncio.shield(worker), timeout=10)

    # -- the rest --------------------------------------------------------------------

    async def health_check(self) -> Health:
        try:
            self._keys()
        except InvalidValueError as exc:
            return Health(ok=False, detail=str(exc))
        return Health(ok=True, detail="keys are set; the service is asked when there is a request")

    async def close(self) -> None:
        self._client = None  # the REST client holds no connection of its own to close


def _real_stream(key: str, secret: str, feed: str) -> StockDataStream:
    return StockDataStream(key, secret, feed=DataFeed(feed))


def _point(instrument: Instrument, trade: Any, at: Timestamp) -> Point:
    return Point(
        instrument=instrument,
        event_time=at,
        received_at=at,
        values={"price": float(trade.price), "size": float(trade.size)},
    )


def _bar(instrument: Instrument, bar: Any, start: Timestamp, size: int) -> Bar:
    return Bar(
        instrument=instrument,
        start=start,
        end=Timestamp(start + size),
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=float(bar.volume),
    )
