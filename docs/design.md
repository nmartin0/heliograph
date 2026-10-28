# Design

Heliograph generates trading signals for one person to act on by hand,
backtests and optimises the strategies behind them, and paper-trades them on
live data. It is a small core of contracts and messages with every concrete
technology plugged in from outside (ports and adapters). Items marked
**(built)** exist in the code; the rest is the agreed target. Reasons for each
choice are in [decisions.md](decisions.md).

## Scope

**Version 1:** signals with reasons; charts at any bar size with indicators as
removable plug-ins; an MT5-style Strategy Tester plus walk-forward and
overfitting checks; paper trading on live data from the start; plug-ins that
appear in the GUI with generated settings panels; single user on the desktop,
with the same code able to run as a web app later.

**Not in version 1:** real-money orders; multiple users and authentication;
tick or order-book replay (the data model leaves room); sandboxing of
third-party plug-ins (they run in-process and are trusted).

## Packages

```
heliograph.core      standard library only: time, messages, clock, alarms, bus, engine, streams,
                     bars, journal, errors; data ports (planned)
heliograph.plugins   plug-in base class, manifests, registry; built-in plug-ins (journals/sqlite)
heliograph.app       configuration, composition root, application services, GUI
heliograph.sdk       (planned) the stable surface strategy authors import
```

Dependencies point inward only: app → plugins → core (built: import-linter
contract and a core purity test).

## Kernel

- **Timestamp (built).** `int` nanoseconds since the Unix epoch, UTC.
  Conversions refuse naive datetimes. Display zones are a GUI concern.
- **Messages and value objects (built: `Point` and `Bar` messages,
  `Instrument` value object).** Frozen, slotted, keyword-only dataclasses that
  validate themselves. Every message has `ts`, the instant it became known.
  A `Point` carries the source's `event_time` and Heliograph's `received_at`,
  which is its `ts`. A bar covers `[start, end)` and has `ts = end`, or a later
  `received_at` when it arrived after closing.
- **Clock (built).** The only source of time. `SimulatedClock` moves forward
  only when told; `WallClock` reads the system and never goes backwards.
- **Alarms (built).** Components set keyed alarms (`at`, `every`, `cancel`)
  through a `Scheduler`; the engine publishes an `AlarmFired` message when one
  is due, so bars close and sessions open on time. An alarm already set for an
  instant fires before data at that instant; one a handler sets for the
  current instant fires after the message it is handling. `run(until=…)`
  stops a replay at that instant and, when the data ends earlier, carries it
  on to close what is still open. More than 10,000 alarms at one instant end
  the run with `AlarmLoopError`.
- **MessageBus (built).** Typed publish and subscribe, synchronous and
  breadth-first. Refuses any message stamped later than the clock
  (`LookAheadError`). A failing handler ends delivery and discards the queue.
  The clock is not exposed; components can only ask `runs_on(clock)`.
- **BacktestEngine (built).** Merges time-ordered feeds, advances the clock to
  each message's `ts`, then publishes it. Equal timestamps keep feed order.
  Refuses re-entrant runs. The no-look-ahead property test is the Phase 0 gate.
- **LiveEngine (built; D43).** The same loop for live data: inputs arrive
  stamped by the clock, alarms fire before them, instants settle once the
  clock has passed them (a repeated reading joins its instant), and a
  timekeeper task wakes it for alarms and settling while nothing arrives.
  A live day's journal replays through `BacktestEngine`.
- **Settled (built).** When everything stamped T has been delivered, and
  before time moves on, the engine publishes `Settled(T)`. Consumers that
  combine streams act on it, so a 1-hour bar and a 4-hour bar both closing at
  16:00 are seen together; the Phase 2 indicator graph stabilises on it.
- **Journal (built; SQLite store as the `journals/sqlite` plug-in).** The bus numbers every
  message and records it in a `Journal` before delivering it, marked
  `input` (outside data), `engine` (alarms, Settled) or `derived` (what
  handlers published) (Jane Street's Concord idea). Replaying only the inputs
  through the same components reproduces every other entry exactly; that is
  how a backtest or a live day is reproduced. The SQLite journal stores one
  row per message as deterministic msgspec JSON, so a replay is byte-for-byte
  identical; its header holds key–value pairs for code, plug-in and parameter
  versions, and reading it back takes an explicit list of message types.
  A backtest run from configuration (`app/run.py`, D26) writes a new journal
  whose header holds the configuration (its exact text and digest), each
  mount's source, version and validated parameters, the store snapshots
  read and the interval; `replay_backtest` rebuilds a complete run from
  that journal alone.
- **Indicator graph (built; `core/graph.py`, D27).** Inputs take one field of
  the bars of one instrument and size; indicators read earlier nodes. The
  graph stabilises once per instant, on `Settled`, after every bar closing
  then has arrived (Jane Street's Incremental idea): each node with a changed
  input is updated once, in dependency order, which is the order nodes were
  added in; inputs closing at one instant are read together (a run refuses an
  indicator reading streams that close at different times after their end:
  its sources' delays, and the grace of bars built from trades, D35). Indicators are series, so every sample propagates, equal or not;
  the cutoff is a node producing no sample. One `IndicatorValues` per
  instant carries every indicator's latest sample and which changed. A run
  builds its indicators from `[[components]]` (D26, D27). After each
  stabilisation the graph tells its observers which nodes took a sample.
- **Strategy pipeline (built; `core/pipeline.py`, D30).** Forecasters and
  gates are graph nodes; a strategy observes the graph and, at each close of
  its price stream, combines, sizes, exits and checks; see "Strategy
  pipeline" below.

## Plug-ins

- **Manifest (built).** `kind` (the port), `name` (unique within the kind),
  `version`, `summary`, and `params`: a pydantic model that is frozen, refuses
  unknown keys and validates defaults. `param()` declares default, help text,
  bounds and a positive search step. The JSON schema of `params` drives the GUI
  settings panel, the optimiser's search space and saved presets.
- **Plugin[P] (built).** The base class. The generic parameter and the
  manifest's `params` are checked against each other when the class is defined.
  A subclass must declare its own manifest to be registered.
- **Registry (built).** Discovers plug-ins through entry points in the group
  `heliograph.<kind>`. Kinds are plural snake-case slugs (decision D16):
  `data_sources`, `instrument_providers`, `sinks`, `brokers`, `indicators`,
  `forecasters`, `gates`, `combiners`, `sizers`, `exits`, `risk_gates`,
  `fills`, `strategies`. Unknown names fail with the list of valid ones;
  duplicates are refused; discovery is all-or-nothing.
- **Run configuration (built).** TOML naming components, their plug-ins and
  parameters, validated as a whole, fingerprinted with SHA-256, parameters
  frozen deeply. `build_components` validates everything before constructing
  anything and reports every problem at once.
- **Planned manifest fields:** `inputs`/`outputs` (message types), `display`
  hint for chart series, `warmup` in the plug-in's own bars, a contract version,
  and capabilities for adapters.

## Data

**Model.** A `Point` is an instrument, event time, `received_at` time and
values. Bars are derived by a resampler. A source that only supplies bars is
stored as bars and marked so. Every record keeps lineage: source adapter,
download time, row hash. `received_at` makes data bitemporal, so a backtest can
read the past as it was known even after a vendor corrects its history.

**Bars (built).** `TimeBars` builds the bars of one stream from its points:
points go to the bar containing their `event_time`, in any order; each open
bar is published by an alarm when its interval ends (plus the source's
delay and the spec's grace, D35);
a point arriving at or after its bar's close is counted as late and changes
nothing. One builder per stream: a second is refused, because components
claim their alarm keys.

**Bars from bars (built, D33).** On a mount of bars (an exchange's candles),
a stream of the mount's size is its bars as they come, and a stream of a
whole multiple of it is built by `BarsFromBars`: the finer bars of each
interval put together, published when the last of them (ending at the
interval's end) is known, or else by an alarm one finer bar after the end,
plus the source's delay and the grace; one bar per instant, in order. A finer bar of a bar already closed, or known at or after its
close, is late and counted. Gaps are skipped; carrying them is not offered
yet.

**Streams.** Every consumer (indicator, chart, strategy) is bound to a
*stream*: an instrument plus a resampler spec (bar size, anchor, time zone and
session calendar, gap policy, price field). Consumers on the same stream share
one resampler. The rules for streams of different sizes are below.

**Storage tiers** (from Elysium's bronze/silver/gold):

1. *Raw* (built): exactly what the source yielded for one request, one
   Parquet file per download, named by the hash of its bytes; never edited.
2. *Clean* (built): computed from the raw files, never kept. A later
   download replaces an earlier one over the interval it covers; rows that
   fail a check (outside their interval, invalid, a non-positive price, a
   negative size, a bar of the wrong length or overlapping the one before)
   are quarantined with the reason and where they came from.
3. *Published* (built): a snapshot of datasets' clean data and quarantine
   with a manifest, written once after an audit; its id is the hash of its
   inputs, so it can be rebuilt and compared byte for byte (`verify`). The
   `store` data source reads it, with the live delay and terms of the
   source the data came from, and refuses a request outside the intervals
   its downloads covered. A run records the snapshots it read in its
   journal's header (D26).

Files are Parquet, one per dataset (source, instrument, kind and bar size)
in a snapshot (D25); partitioning by date, and queries with polars or
DuckDB, wait for datasets large enough to need them. Signals, runs and the
trial registry live in SQLite.

## Indicators on other timeframes

An indicator on a 1-hour chart can read 15-minute or 4-hour bars. Rules:

1. **Visible at its end, inclusive.** A 4h value ending at T is visible to the
   1h bar ending at T: not earlier (look-ahead), not later (lag).
2. **One snapshot per timestamp.** Everything stamped T is delivered before any
   strategy evaluates T, whatever the subscription order (the indicator graph's
   stabilisation).
3. **Clock-driven closing.** Alarms close bars on time, so the 4h bar exists at
   16:00 rather than when the next trade arrives.
4. **Closed values only** feed signals and history; a forming value is at most
   a dashed overlay.
5. **Explicit boundaries.** Anchors, time zones and sessions are always
   configured; crypto days end 00:00 UTC, US equity days at the session close.
6. **Warm-up in the indicator's own bars** (200 × 4h = 800 hours of base data).
7. **No smaller-from-larger** resampling.

Display: higher-timeframe series as steps on the first chart bar whose end is
at or after the value's time; lower-timeframe series as one summary per chart
bar or in a linked pane; every series labelled with its stream ("EMA20 · 4h
UTC").

## Strategy pipeline

A signal is built in stages, each a plug-in kind (`forecasters`, `gates`,
`combiners`, `sizers`, `exits`, `risk_gates`); the Wizard assembles them, and a
strategy may also be written as one class.

| Stage | Input | Output | Notes |
| --- | --- | --- | --- |
| Forecaster | closed bars, indicator values | continuous forecast | Scaled so the average absolute value is 10, capped at ±20 |
| Gate | indicator values, regime states | pass or veto | Blocks a forecast; never votes or dilutes |
| Combiner | forecasts | combined forecast | Weights summing to 1 × diversification multiplier (capped 2.5), capped at ±20 |
| Sizer | combined forecast, volatility | target position | Volatility targeting; 10% no-trade buffer instead of entry/exit thresholds |
| Exit | position, prices | target override | Stop, trailing stop, take-profit, time limit (triple barrier) |
| Risk gate | targets, signals | allowed or refused | Independent checks: stale data, signal rate, price sanity, market hours, halt |
| Signal | everything above | `Signal` record | The product |

How it runs (D30): forecasters and gates are nodes of the indicator graph,
so their values are journalled, replayed and checked by the conformance kit
like any indicator's. A strategy observes the graph and decides at each close
of its price stream, once every forecaster and gate has a sample: a veto
makes the combined forecast 0; the sizer sees every close, so it can follow
the price while forecasts warm up; exits run in order and the one that sets
the target is named; risk gates see only proposals that add risk (taking it
off always leaves), and the first to refuse stops the signal, which is
journalled as `SignalRefused`; a refused reversal goes flat instead (D34),
since the side held is one the strategy no longer wants. A gate may also
doubt the price itself (a jump that looks like a bad print): that close is
not acted on at all, until a close is back near the last one trusted or
confirms the move (at most `max_doubts` in a row). While the
sizer cannot size, exits and vetoes still act on the position held. The position a strategy holds is the last
target it signalled or, with a simulated broker, what has been filled.

Simulated fills (D31, `core/broker.py`): a strategy naming a fill model
(`fills`) has a `SimulatedBroker`, which fills each signal at the open of the
price stream's next bar, priced by the model (`next_open`: half the spread,
slippage and a commission), and publishes a journalled `Fill` when that bar
becomes known. The text report (`app/report.py`) reads only the journal:
signals, fills, costs, profit and loss, drawdown and time in the market.

Optional: meta-labelling as a sizer that scales a forecast by the probability
it pays off. The design is Carver's, with MQL5's veto, pattern mask and invert
flag kept and its averaging vote dropped (see
[research/strategy-design.md](research/strategy-design.md)).

## Strategy SDK

`heliograph.sdk` is the stable surface for plug-in authors (D32): the plug-in
base class, manifest and parameters, the protocol each kind of stage
implements, `Proposal` (what a risk gate is handed), the records a run
journals (`Signal`, `Fill`, `Bar`, for code that reads journals), the
conformance declarations, errors and time. A plug-in written outside this
repository imports it and nothing deeper; its docstring shows a forecaster
written from scratch, and a test keeps that example passing the kit.

Planned: a strategy written as one class, over series views that end at the
last closed value, with indicators bound to a stream from the run
configuration. A strategy never names a timeframe, source or broker, and
never sees orders:

```python
class Breakout(Strategy):
    class P(Params):
        lookback: int = param(20, description="Channel length", ge=5, le=200, step=5)

    def setup(self, ctx: Context) -> None:
        self.upper = ctx.indicator("donchian_upper", period=self.params.lookback)
        self.atr = ctx.indicator("atr", period=14)

    def on_bar(self, ctx: Context, bar: Bar) -> None:
        if bar.close > self.upper.previous:
            ctx.signal(Long(bar.instrument, stop=bar.close - 2 * self.atr.latest))
```

**Conformance kit.** Every plug-in of a kind passes the same tests whoever
wrote it: no peeking (appending data never changes earlier values), edge cases
(warm-up, gaps, repeated timestamps, a single point), reference values (TA-Lib
or a published example), and a manifest check. `heliograph new <kind> <name>`
will scaffold a plug-in with its tests. Your own plug-ins live in a separate
local package registered through entry points.

## Adapters

Four ports reach the outside world, like Unix files: one small interface each,
a mount table binding names to providers, and declared capabilities.

| Port | Job | First implementations |
| --- | --- | --- |
| `DataSource` | `history(request)` and `subscribe(subscriptions)` return time-ordered canonical messages; `health_check()` | CSV and Parquet, Alpaca, CCXT |
| `InstrumentProvider` | load instruments; map our IDs to venue symbols | static file |
| `SignalSink` | `emit(signal)` returns a delivery result; `health_check()` | journal, paper broker, ntfy, desktop, webhook, Apprise |
| `Broker` | reserved: account, positions, orders, reconcile, events | paper broker |

- Ports are async protocols declared in the core (plain `async def`
  signatures; no event loop in the core). The app owns the asyncio loop and a
  bridge that feeds what adapters produce into the synchronous bus in
  timestamp order, so the bus stays deterministic (decision D4). Entry-point
  groups: `heliograph.data_sources`, `heliograph.instrument_providers`,
  `heliograph.sinks`, `heliograph.brokers`.
- **Capabilities are declared, never discovered**: data kinds for history and
  live, timeframes, lookback, partial bars, adjustments, depth, live delay,
  rate limit and concurrency, terms flags (personal only, non-commercial, no
  live trading, attribution), platforms. Composition refuses a mount whose
  needs its source cannot meet, or a live mount on a `no_live_trading` source.
- A source declares its rate limit and concurrency; the app builds a
  throttle from them and binds it to the source before use
  (`DataSource.bind`), and the source enters it around every request it
  makes. Sources never throttle themselves. One error type per port
  (`SourceUnavailableError`, `SinkUnavailableError`). Secrets are `${VAR}`
  or keyring references that fail loudly when missing.
- **When data became known** is stamped by the bridge, never by the source:
  history at its event time plus the source's declared live delay, live data
  by the run's clock on arrival, a bar never before it closes (D24).
- The **mount table** in the run configuration binds names to a source, its
  parameters, an instrument and the data wanted. Mounts on the same source
  with the same parameters share one instance, and so one rate limit. Every
  mount is checked against its source before anything is used, and every
  problem is reported at once (`app/mounts.py`):

```toml
mode = "backtest"

[mounts.btc]
source = "file"
params = { path = "data/btc-usd.parquet", live_delay = "1s" }
kind = "trades"                 # or "bars", with bar_size = "1m"
start = 2026-09-01T00:00:00Z    # a backtest needs both; live data has neither
end = 2026-10-01T00:00:00Z
# allow_empty = true            # else a mount that delivers nothing fails the run

[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"
```

## Signals and paper trading

A `Signal` carries a stable ID (for idempotent delivery), strategy and
parameter set, instrument, side, forecast and its per-component breakdown,
entry, stop and targets, expiry, event time, emit time and a schema version.
The journal sink records it first; a dispatcher fans out to other sinks,
retrying each independently. Fields map onto FIX tags for future brokers.

The paper broker is a `Broker` on live data with the backtester's fill and cost
model, bridged to `SignalSink`; a paper session survives restarts. You mark
each signal *took it* (with your price and size), *skipped* or *expired*; the
journal compares your results with the signal's paper result.

No adapter in version 1 can place a real-money order. A live broker would ship
disabled, behind an explicit setting and the same risk gate.

## Safety

- An independent risk gate checks every signal before it leaves.
- A `Halted(reason)` state, enterable by any component and cleared only by the
  owner; signals are refused and journalled while halted.
- A "too good to be true" warning on implausible backtests, which usually means
  look-ahead.
- Startup refuses to run if a plug-in's version disagrees with its manifest;
  parameter names are never reused with a new meaning.

## Strategy Tester

Copies MT5's discoverability (task start page, settings that hide what a task
does not need, sortable results, rerun by double-click, visual replay) and adds
what MT5 lacks.

- **Tasks:** single test, optimisation (grid, Optuna TPE, random),
  walk-forward (rolling and anchored, purge and embargo), multi-instrument
  sweep, visual replay.
- **Settings:** data range and warm-up, fill fidelity, execution delay, costs
  (spread, commission schedules, volume-scaled slippage, borrow), account,
  forward split.
- **Results:** the MT5 report set, equity and drawdown, return calendars,
  MFE/MAE, optimisation tables, 1D/2D/3D parameter views.
- **Beyond MT5:** a trial registry that cannot be switched off; deflated Sharpe
  and probability of backtest overfitting beside every winner; a sealed holdout
  opened once and recorded; Monte Carlo resampling of trades; runs cached by a
  hash of configuration, data snapshot and code version.

## GUI

NiceGUI in native mode (a pywebview window) now, served as a web app later. The
GUI is a client of the engine's JSON API and holds no logic of its own; the
engine runs in its own supervisable process (D18), `heliograph serve`,
whose API app/server.py lists (D37); a run's chart series come from its
journal (D38). The window (`heliograph gui`, D39) imports nothing else of
Heliograph and learns what exists by asking the engine. Times cross the API as
ISO 8601 strings (D19). Elysium may host these screens later (D20,
[research/elysium.md](research/elysium.md)). Chart zoom and crosshair stay client-side.
The view is a function of state; changes go through actions (Jane Street's
Bonsai idea).

| Screen | Holds |
| --- | --- |
| Workspace | charts, instrument and stream pickers, indicator catalogue, signal markers |
| Signals | live log with reasons and breakdown, took-it/skipped buttons, sink settings |
| Strategy Tester | tasks, settings, results, optimisation views, replay |
| Wizard | node graph of forecasters, gates, combiner, sizer, exits, risk |
| Paper account | positions, fills, equity, your own trade journal |
| Data manager | sources, mounts, downloads, quality report, quarantine |
| Plug-ins | installed plug-ins, versions, parameter settings from their schemas (D40); licences later |

Libraries per view are in [licensing.md](licensing.md).

## Open questions

- How Heliograph's screens appear inside Elysium, if they do: a React module
  over our API, NiceGUI in an iframe, or data only (D20). Decide at Phase 3.
- Series indexing style in the SDK (`latest`/`previous` versus an index).
- Whether Point72's ccflow suits Wizard-composed configuration.

## Risks

| Risk | Mitigation |
| --- | --- |
| Free data sources change limits or terms | isolated adapters, terms flags in manifests, file import fallback |
| Event-by-event Python is slow for large optimisations | coarse graph nodes, TA-Lib streams, numba, parallel trials |
| Plug-in API churn | contract version in manifests, deprecation period |
| Overfitting despite tooling | permanent trial registry, sealed holdout, paper trading before acting |
| Unsandboxed plug-ins | only your own plug-ins in v1, stated plainly |
