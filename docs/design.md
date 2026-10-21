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
heliograph.core      standard library only: time, messages, clock, bus, engine, errors; ports (planned)
heliograph.plugins   plug-in base class, manifests, registry; built-in plug-ins later
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
- **Indicator graph (planned).** Indicators form a dependency graph that is
  stabilised once per timestamp, after every input stamped at that time has
  arrived (Jane Street's Incremental idea). Nodes recompute in dependency
  order, stop propagating when a value is unchanged (cutoff), and are skipped
  when nothing observes them. Nodes are whole series, not scalars.

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
  `strategies`. Unknown names fail with the list of valid ones;
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
bar is published by an alarm when its interval ends (plus the spec's grace);
a point arriving at or after its bar's close is counted as late and changes
nothing. One builder per stream: a second is refused, because components
claim their alarm keys.

**Streams.** Every consumer (indicator, chart, strategy) is bound to a
*stream*: an instrument plus a resampler spec (bar size, anchor, time zone and
session calendar, gap policy, price field). Consumers on the same stream share
one resampler. The rules for streams of different sizes are below.

**Storage tiers** (from Elysium's bronze/silver/gold):

1. *Raw*: exactly what the source returned; never edited.
2. *Clean*: types standardised, checks applied (positive prices, high ≥ low,
   ascending unique timestamps); failing rows quarantined with the reason.
3. *Published*: the snapshot runs read, published only after it passes its
   audit. A run records the snapshot it read.

Files are Parquet partitioned by source, instrument and date, queried with
polars or DuckDB. Signals, runs and the trial registry live in SQLite.

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

Optional: meta-labelling as a sizer that scales a forecast by the probability
it pays off. The design is Carver's, with MQL5's veto, pattern mask and invert
flag kept and its averaging vote dropped (see
[research/strategy-design.md](research/strategy-design.md)).

## Strategy SDK

`heliograph.sdk` is the stable surface for strategy authors. It exposes series
views that end at the last closed value, `ctx.indicator(...)` bound to a stream
from the run configuration, and `ctx.signal(...)`. A strategy never names a
timeframe, source or broker, and never sees orders.

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
- The core wraps every adapter with rate and concurrency limits; adapters never
  throttle themselves. One error type per port (`SourceUnavailable`,
  `SinkUnavailable`). Secrets are `${VAR}` or keyring references that fail
  loudly when missing.
- The **mount table** in the run configuration binds logical instruments to
  sources:

```toml
[mounts]
"BTC-USD" = { source = "ccxt", params = { exchange = "kraken", symbol = "BTC/USD" } }
"SPY"     = { source = "alpaca", params = { feed = "iex" } }
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
engine runs in its own supervisable process (D18). Times cross the API as ISO
8601 strings (D19). Elysium may host these screens later (D20,
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
| Plug-ins | installed plug-ins, versions, licences, parameter docs |

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
