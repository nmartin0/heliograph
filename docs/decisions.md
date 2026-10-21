# Decisions

Each entry: the decision, why, and what was rejected. Reversing a decision
means editing its entry to say so and adding the new one, not deleting it.

## D1 · Signal provider, not an auto-trader (2026-10-20)

Heliograph tells a person what to trade; it never places real-money orders.
Paper trading is built in from the start. **Why:** the owner wants to act on
signals by hand, and keeping execution outside removes the riskiest interface
(orders) from strategy code. **Rejected:** automated execution in version 1.

## D2 · Ports and adapters with a standard-library core (2026-10-20)

The core owns protocols and messages and imports only the standard library;
everything concrete is a plug-in. Dependencies point app → plugins → core.
**Why:** any part can be replaced without touching the rest; matches Elysium
and NautilusTrader. **Rejected:** a framework-first design (Backtrader-style
inheritance) that ties strategies to one engine.

## D3 · Time is int nanoseconds UTC; `ts` is when a message became known (2026-10-20)

**Why:** integers sort and compare exactly, match Arrow, Parquet, polars and
pandas, and cannot carry a zone by accident. Stamping by knowledge time makes
look-ahead checkable by comparison. Bars are stamped at their end.
**Rejected:** aware datetimes (slower, ambiguous around daylight saving),
bar-start stamps (invite look-ahead).

## D4 · Synchronous breadth-first bus that refuses the future (2026-10-20)

**Why:** deterministic order, no stack growth on reaction chains, and reading
the future becomes an exception rather than a code-review item. A failing
handler ends delivery and drops the queue, so delivery is never half-resumed.
**Rejected:** asyncio inside the core (non-deterministic interleaving).

## D5 · Plug-ins through entry points with pydantic manifests (2026-10-20)

**Why:** third-party and built-in plug-ins work the same way; one parameter
declaration drives the GUI, optimiser and presets. **Rejected:** hard-coded
registries (Elysium's, which it plans to replace), stevedore (adds nothing).

## D6 · TOML run configuration, validated whole and fingerprinted (2026-10-20)

**Why:** standard-library parser; a bad configuration builds nothing; the
SHA-256 digest ties every run and signal to the exact text. **Rejected:** YAML
(needs a dependency, ambiguous types).

## D7 · NiceGUI on pywebview; Lightweight Charts for prices (2026-10-20, confirmed 2026-10-21)

**Why:** one codebase for desktop now and web later; Vue-based custom elements;
bundles ECharts, AG Grid Community and CodeMirror. Lightweight Charts is
Apache-2.0, small, multi-pane, with primitives for our tools. **Rejected:**
Streamlit (reruns the script), Dash (callback model), Flet (Flutter canvas),
PySide6 (no path to web), Highcharts and TradingView's Advanced Charts
(proprietary).

## D8 · Strategy pipeline after Carver, not MQL5 voting (2026-10-21)

Forecasters on a common scale (average |f| = 10, cap ±20), gates that veto,
a weighted combiner with a diversification multiplier, a volatility-target
sizer with a 10% no-trade buffer, exits, an independent risk gate. **Why:**
MQL5's `CExpertSignal` divides by every filter including silent ones (one
module at 100 plus two silent gives 33, below the open threshold of 50), uses
uncalibrated hand-picked scores, and throws away strength with thresholds.
**Kept from MQL5:** the veto, the pattern mask, the invert flag, the Wizard.
**Rejected:** LEAN's newest-insight-wins combination. See
[research/strategy-design.md](research/strategy-design.md).

## D9 · Every consumer binds to its own stream (2026-10-21)

Indicators choose their own bar size independently of the chart; streams carry
explicit anchors and sessions; values are visible at their end, inclusive.
**Why:** the user wants mixed timeframes on one chart, and every platform
surveyed gets the boundary wrong in one direction. See
[research/multi-timeframe.md](research/multi-timeframe.md).

## D10 · Journal every bus message for replay (2026-10-21)

**Why:** exact reproduction of backtests and of live signals for debugging,
from Jane Street's Concord. **Rejected:** snapshots only (cannot answer new
questions about the past).

## D11 · Write our own indicator graph (2026-10-21)

**Why:** no maintained Python library does Incremental-style stabilisation
with cutoffs and observers. Point72's csp is close but replaces the bus, clock
and engine, pins `pyarrow<24` and pulls pandas into the core path; it stays a
design reference and a possible optional engine adapter.

## D12 · TA-Lib and talipp for indicators (2026-10-21)

**Why:** TA-Lib's Python wrapper (from 0.8) has an O(1) streaming API matching its batch values, which
serves both live nodes and reference values in tests; talipp covers the rest
incrementally in pure Python. **Rejected:** pandas-ta (repository gone),
tulipy and finta (unmaintained).

## D13 · Four adapter ports with declared capabilities and a mount table (2026-10-21)

`DataSource`, `InstrumentProvider`, `SignalSink`, `Broker`; async protocols in
the core, with the event loop and a bridge to the synchronous bus in the app;
capabilities checked at composition; limits enforced by a core wrapper. **Why:** Elysium's
measured 40× slowdown from try-and-catch capability discovery, and the
Unix-file model the owner proposed. See [research/adapters.md](research/adapters.md).

## D14 · Licence policy: our code is ours (2026-10-21)

Copyleft libraries may be dependencies, unmodified; no copyleft code enters our
tree; AGPL and source-available licences are avoided. Details in
[licensing.md](licensing.md).

## D15 · `docs/` is the source of truth (2026-10-21)

The design moved from a claude.ai document into this repository so that it is
versioned with the code and available to every session. **Rejected:** keeping
two editable copies.

## D16 · Plug-in kinds are plural snake-case slugs (2026-10-21)

The kind is the entry-point group suffix (`heliograph.<kind>`) and the
`Manifest.kind` value: `data_sources`, `instrument_providers`, `sinks`,
`brokers`, `indicators`, `forecasters`, `gates`, `combiners`, `sizers`,
`exits`, `risk_gates`, `strategies`. **Why:** the code and tests already use
plural slugs (`filters`, `data_sources`, `sinks`); one convention prevents
plug-ins registering under a group nobody reads.
