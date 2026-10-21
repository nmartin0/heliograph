# Adapters, data sources and Elysium

Elysium's adapter layer and the adapter interfaces of NautilusTrader, LEAN,
Freqtrade and Hummingbot shaped Heliograph's four ports (decision D13). This
file keeps the evidence and the terms of each data source.

Researched 2026-10-20 (data sources) and 2026-10-21 (adapters).

## Elysium (read from its code at 2aebe3b)

- `core/` holds contracts; `adapters/` one file per technology;
  `core/deployment_loader.py` is the only composition root; import-linter
  enforces api > adapters > core with listed exceptions.
- `core/adapter_roles.py`: marker classes `ReadAdapter`, `WriteAdapter`
  (reads and writes deliberately separate).
- `core/ontology/interface.py`: `ExternalReadAdapter`, `ExternalWriteAdapter`,
  one shared `StorageUnavailable` error.
- **Capabilities as class attributes**: `max_concurrent_reads`,
  `max_concurrent_writes`, `pushable_operators`,
  `supports_atomic_conditional_write`. The core never asks for what was not
  declared; the old raise-and-retry approach measured 40× slower.
- `core/llm/interface.py`: a narrow `Protocol` plus `max_concurrent_requests`,
  a shared `LLMUnavailable`, request deadlines.
- `core/llm/concurrency_limited_adapter.py`: limits enforced by a wrapper.
- `core/source_health.py`: `health_check()` on every source.
- `core/secret_references.py`: `${VAR}` references, fatal when missing.
- `core/notifications.py`: email and webhooks treated as "places data leaves".
- `PRINCIPLES.md` §11: copyleft libraries unmodified are fine; modified code
  must be permissive.
- Registries are hard-coded dicts; `THIRD_PARTY_EXTENSIONS.md` plans entry
  points. Heliograph starts with entry points.

## Engine interfaces worth mirroring

- **NautilusTrader:** `InstrumentProvider.load_all_async/load_ids_async`;
  `DataClient._subscribe_*` and `_request_*`; `ExecutionClient._submit_order`,
  `_modify_order`, `_cancel_order` and reconciliation reports;
  `DataClientFactory.create(name, config, cache, clock)`.
- **LEAN:** `IDataQueueHandler.Subscribe/Unsubscribe`,
  `IHistoryProvider.GetHistory`, `IBrokerage.PlaceOrder/UpdateOrder/
  CancelOrder/GetOpenOrders/GetAccountHoldings/GetCashBalance` with events,
  `ISymbolMapper`.
- **Freqtrade:** a declarative capability dictionary `_ft_has`
  (`ohlcv_has_history`, `ohlcv_partial_candle`, `ohlcv_candle_limit`).
- **Hummingbot:** connectors declare rate-limit rules and supported order
  types; data sources write into asyncio queues.

NautilusTrader (LGPL), Freqtrade and Lumibot (GPL) are references for interface
shape only; their code is never copied.

## Data sources and their terms

| Source | Use | Terms (as checked) |
| --- | --- | --- |
| CCXT | Live crypto from 100+ exchanges | MIT library; each exchange's terms apply; `ccxt.pro` websockets now free |
| Alpaca | US stocks history and live IEX, paper account | Free plan: IEX real time, SIP history older than 15 minutes, 200 calls a minute, 30 streamed symbols |
| Binance Vision dumps | Bulk crypto history | CC BY-NC-SA 4.0 terms: "algorithmic historical backtesting for purely personal non-production research"; no live trading |
| Databento | Quality futures and equity history | $125 credit, expires after 6 months; pricing page not verified |
| Massive (formerly Polygon) | US stocks | Rebranded 2025-10-30; free tier rate-limited |
| Twelve Data | Prices, FX | Free: 8 credits a minute, 800 a day |
| Tiingo | Daily US stocks, crypto, FX | Personal or evaluation use (limits unverified) |
| OANDA practice | Forex, demo account | Personal practice account; Python SDKs stale, use REST |
| FRED | Macro series | Some series personal use only; attribution notice required |
| yfinance | Development convenience | Yahoo's terms prohibit automated collection |
| Alpha Vantage | Not used | 25 requests a day |
| Files | Anything you bring | Your own data |

Binance is unavailable to US residents; CCXT lets you choose an exchange that
is. If Heliograph ever serves other users, every source must be re-checked: most
free tiers forbid redistribution.

## Signal formats

No widely adopted public JSON schema for trading signals was found. Ours is
versioned and maps onto FIX tags (Side 54, OrdType 40, Price 44, StopPx 99).
TradingView webhook conventions: JSON body as `application/json`, ports 80 and
443, a three-second timeout, no secrets in the body.

## Sources

- https://github.com/nmartin0/elysium
- https://nautilustrader.io/docs/latest/developer_guide/adapters
- https://www.quantconnect.com/docs/v2/lean-engine/contributions/brokerages
- https://docs.alpaca.markets/docs/about-market-data-api
- https://docs.ccxt.com/docs/pro
- https://data.binance.vision/Binance_Vision-Terms_of_Use.pdf
- https://databento.com/docs/faqs/usage-pricing-and-data-credits
- https://support.twelvedata.com/en/articles/5194820-api-credits-limits
- https://www.alphavantage.co/support/api-key
- https://fred.stlouisfed.org/docs/api/terms_of_use.html
- https://github.com/ranaroussi/yfinance
- https://www.tradingview.com/support/solutions/43000529348-about-webhooks/
