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
**Why:** any part can be replaced without touching the rest; the same
direction of layers as Elysium (whose core, unlike ours, uses third-party
libraries) and NautilusTrader. **Rejected:** a framework-first design (Backtrader-style
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
registries (Elysium's adapter registries are hard-coded dicts; its sub-apps
moved to `module.json` manifests at `fae3e4e`), stevedore (adds nothing).

## D6 · TOML run configuration, validated whole and fingerprinted (2026-10-20)

**Why:** standard-library parser; a bad configuration builds nothing; the
SHA-256 digest ties every run and signal to the exact text. **Rejected:** YAML
(needs a dependency, ambiguous types).

## D7 · NiceGUI on pywebview; Lightweight Charts for prices (2026-10-20, confirmed 2026-10-21; provisional, see D20)

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
capabilities checked at composition; limits declared by each source and
enforced by a throttle the app binds to it (`DataSource.bind`), as
Hummingbot's connectors declare rate-limit rules for a shared throttler.
**Why:** Elysium measured
a 40× slowdown when filter push-down an adapter could not do fell back to a
full scan, which declared capabilities avoid; and the Unix-file model the
owner proposed. See [research/adapters.md](research/adapters.md).

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
`exits`, `risk_gates`, `strategies`, `journals`. **Why:** the code and tests already use
plural slugs (`filters`, `data_sources`, `sinks`); one convention prevents
plug-ins registering under a group nobody reads.

## D17 · Jane Street's OCaml libraries are not adopted (2026-10-21)

**Why:** none of their 411 GitHub repositories is a trading library, and the
one component they would replace, our indicator graph, is about 300–600
lines. Rewriting in OCaml was estimated at 90–180 engineer-days extra
(losing CCXT, TA-Lib, polars, Optuna, statsmodels); bridging Incremental from
Python at 5–15 days extra, because `pythonlib` is archived and incompatible
with current Incremental. We take ideas, not code. See
[research/jane-street.md](research/jane-street.md).

## D18 · The engine runs as its own supervisable process behind a JSON API (2026-10-21)

Every screen reads and acts through a JSON HTTP API (with WebSocket or SSE for
streams); the GUI holds no logic of its own; parameter schemas are served
from `model_json_schema()`. The process takes its port, data directory and
configuration path from arguments or environment, binds to `127.0.0.1` by
default, saves the paper session and closes the journal on SIGTERM within a
deadline, serves a health endpoint, logs one structured line per event to
stderr, and runs as a single worker. **Why:** it keeps every front end open
(NiceGUI, an Elysium module, a CLI, tests), it answers the open question of
where the engine runs, and it is the likely shape of Elysium's add-on
lifecycle contract. **Rejected:** the engine in the GUI's process.

## D19 · Times cross JSON as ISO 8601 strings (2026-10-21)

Inside Heliograph a time is an integer of nanoseconds; on the wire it is an
ISO 8601 UTC string with up to nine fractional digits, or milliseconds where a
chart needs numbers. **Why:** a nanosecond timestamp (about 1.8×10¹⁸) is far
beyond the largest integer a JavaScript number holds exactly (2⁵³).

## D20 · Standalone first; Elysium integration when its module API settles (2026-10-21)

Heliograph runs standalone and does not build against parts of Elysium's
module system that are not yet decided: module bundles and `activate`, slots,
the add-on launch contract, identity for add-on servers, the proxy, and add-on
Inbox, timers and jobs. Allowed now: a draft manifest
(`integrations/elysium/module.json`) and, optionally, publishing low-rate
records (signals, simulated fills, daily equity) to a separate SQLite file
that Elysium reads as a silo by configuration alone. How Heliograph's screens
appear inside Elysium (a React module over our API, NiceGUI in an iframe, or
data only) is decided at the start of Phase 3. See
[research/elysium.md](research/elysium.md).

## D21 · Crypto through CCXT is the first market (2026-10-21)

The first live source is CCXT; Alpaca for US stocks follows in Phase 4. The
exchange is a parameter of each mount, so the owner picks one available where
he lives (Binance is closed to US residents; Kraken and Coinbase are not).
**Why:** free, no API key for public data, trades around the clock so the live
path is exercised constantly, and one adapter covers many exchanges.
**Rejected for now:** Alpaca first (free tier limited to IEX real time and
history older than 15 minutes; market hours make live testing slower).

## D22 · The journal: SQLite rows of deterministic msgspec JSON, replayed by run (2026-10-22)

Each bus message is one row (sequence, origin, type, timestamp, payload) with
the payload encoded by msgspec in deterministic order; messages hold floats,
not ints, so a value encodes the same before and after a round trip. The
header records the format, the msgspec version and whether the run finished.
A replay feeds each run's inputs back with the same `until`, which RunEnded
marks record; it also needs the same components built in the same order from
the same start time, which the header should record once runs are built from
configuration. **Why:** byte-for-byte equality is a test anyone can run, and
it catches nondeterminism the moment it appears. **Rejected:** pickle (not
stable across versions, executes code on load), Parquet for the journal
(append-heavy writes suit SQLite better; Parquet stays for market data).

## D23 · Our own testing framework, after Elysium's practice (2026-10-22)

A test is unproven until a deliberate break has made it fail. The breaks
are kept as declared controls in a script that CI runs. mutmut looks for
the breaks nobody thought of. Rules about the suite itself are checked
mechanically. A missing dependency fails rather than skips. Tests run in
random order and in a second time zone. Journals are compared across
processes with different hash seeds. Plug-ins share conformance kits that
run by default, with each check `error`, `todo` or `off` and a reason for
softening. Whole backtests are regression-tested against recorded counts
and statistics, as LEAN does. Details in [testing.md](testing.md).

**Why:**
- Elysium counted nine controls that had silently stopped firing before it
  kept them in a script.
- Its production read path went untested behind a skip.
- Measured here: in-process replay cannot see output that depends on the
  hash seed.

**Rejected:**
- Coverage targets as the measure: Elysium's 96% hid a 63% file.
- Mocking the clock (time-machine, freezegun): our core already takes a
  `Clock`.
- cosmic-ray: heavier than mutmut.
- pytest-asyncio: `asyncio.run` in a test suffices.
- A copied test template per plug-in: copies drift, and an inherited kit
  carries every fix.

See [research/testing.md](research/testing.md).

## D24 · When source data became known is stamped by the bridge (2026-10-22)

A source reports when things happened; when Heliograph learned them is
stamped in one place, the bridge (`core/sources.py`, `app/bridge.py`):

- history is known at its event time plus the source's declared
  `live_delay` (a bar, at its end plus the delay);
- live data is known when it arrives, by the run's clock.

**Why:** a backtest must see data no sooner than a live run would have. A
source that set its own receive times could make history look fresher than
the feed it stands for, and every source would have to get it right.
Stamping in one place also lets a source's clock run ahead of ours without
tripping the look-ahead guard: a point is simply known when it arrives. A
bar cannot be known before it closes, so a live bar that arrives up to a
second (`bridge.SKEW`) before its end by our clock is held until then, and
one arriving earlier is refused as a partial bar.

**Rejected:** trusting a vendor's own receive times (few provide them, and
none for our clock); a per-source stamping rule (the same rule written N
times).

## D25 · The store: raw files by hash, clean computed, snapshots named by their inputs (2026-10-22)

Each download is one raw Parquet file, named by the hash of its bytes and
never edited. Clean data is a pure function of a dataset's raw files, so it
is computed, not kept: downloads apply in the order they were made, a later
one replacing an earlier one over its interval, and failing rows are
quarantined, never dropped or repaired. A published snapshot is written once
after an audit, and its id is the hash of its inputs (the raw files' names,
the store's format and cleaning rules, and the pyarrow version that writes
the Parquet). Its manifest records the intervals its downloads covered, and
reading outside them is an error, so a gap between downloads is never a
quiet hole. A snapshot is staged in a directory of its own and renamed into
place whole.

**Why:** the Phase 1 gate asks that a snapshot rebuild exactly from its raw
files. With clean computed and the id derived from the inputs, a rebuild is
`verify`: compute again, compare bytes. pyarrow writes byte-identical
Parquet for the same table within one version (measured across processes,
hash seeds and time zones), which is why its version is part of the id.
"Later replaces earlier over its interval" handles both a re-download and a
vendor's correction without trade ids, which Kraken does not give.

**Rejected:**
- Keeping the clean tier on disk: a second copy that can drift from raw.
- Deduplicating trades by id across downloads: not every exchange sends ids.
- Ids from publication time: two publications of the same data would
  differ, and a rebuild could not be compared.
- polars or DuckDB now: pyarrow alone covers one file per dataset.

## D26 · A run is journalled with what produced it, and replays from its journal alone (2026-10-22)

`run_backtest` writes every run to a new journal, never over an old one.
The journal's header holds the configuration's exact text and digest (the
text, because TOML's date-times would not survive a JSON form), each
mount's source, plug-in version and validated parameters with their
defaults, the ids of the store snapshots read, the interval asked for and
where the run stopped (the end plus the longest live delay and grace, so
the last trades arrive and the last bars close), and Heliograph's version.
`replay_backtest` rebuilds a complete run from the journal alone, after
checking the text against its digest, and writes entries identical to the
original's. A failed or unfinished run is not replayed: it stopped where
nothing recorded says.

A configuration with components is refused until Phase 2's pipeline runs
them. A stream's mount must be the only one delivering its instrument,
since bars take every trade of their instrument on the bus.

**Why:** a signal is only worth what can be traced: which data, read how,
by which code. With the configuration and its inputs in the journal, a run
can be reproduced and questioned without the files or settings it started
from. Refusing components rather than ignoring them keeps a configuration
from meaning something the run does not do.

**Rejected:** recording only the configuration's digest (the text may be
gone when the run is questioned); appending runs to one journal file (a
file then mixes configurations).

## D27 · The graph's cutoff is a missing sample, not an equal value; one stream per mount and size (2026-10-22)

Every sample a node produces is passed on, even one equal to the last; a
node that produces none leaves what reads it untouched. One stream per
mount and bar size, however its size is written (`btc@1h.close` and
`btc@60m.close` are one input). An indicator may not read streams of
different grace, which close at different instants. Two bars of one input
known at one instant are refused, never one of them dropped. A replay
refuses an indicator whose installed version or parameters, defaults
included, are not the ones the run recorded.

**Why:** indicators are stateful series, not formulas. Incremental's
cutoff on equality suits pure functions, but a 20-bar average must count a
close equal to the one before, or it averages the wrong bars. Bars carry
their instrument and size, not their grace or gap policy, so two streams of
one size on one mount could not be told apart by an input; refusing them is
simpler than putting stream identity on every bar.

**Rejected:** equality cutoffs with `isclose` (the design's first plan; it
silently drops samples); a stream key on `Bar` (every source and journal
would carry it for a case nobody needs yet).

