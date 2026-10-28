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
different grace, which close at different instants (since D35: streams that
close at different times after their end, delays included). Two bars of one input
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

## D28 · The conformance kit lives in the package; plug-ins declare what it expects (2026-10-22)

The kit is `heliograph.testkit`, shipped with the package (pytest as the
`testkit` extra), not a copy in `tests/`, so plug-ins written elsewhere run
the same checks. Its checks are plain functions; pytest only wraps them. A
plug-in states what the kit should expect in a class attribute,
`conformance = Conformance(...)`, defined in `plugins/conformance.py`, below
the kit in the layers, so no plug-in imports the kit.

**Why:** common behaviour tested once and inherited, never copied (the
design Elysium agreed for its modules). The settings belong to the plug-in,
next to the code they excuse, where a reviewer sees them.

**Rejected:** settings in a central file (they drift from the plug-ins they
describe); skipping softened checks (the suite allows skips only for
platform facts: `off` checks are left out and reported instead); a
no-peeking check that only compares a run alone with a longer one (a cache
shared between instances and warmed by an earlier run passes it); running
that check in the test process (a cache filled by another check makes every
run agree, so it runs in a fresh interpreter); a gaps check feeding None to
every input at once (the graph never does; it updates an indicator only when
some input has a sample).

## D29 · Library indicators: TA-Lib streams opened on retry, one node per output, talipp bounded (2026-10-22)

TA-Lib indicators hold a TA-Lib 0.8 stream (`talib.stream`). Samples are
kept until the stream opens on them, trying each new one and taking
`InsufficientHistory` as "not yet", as TA-Lib's own documentation asks;
the first try waits for the function's lookback plus one, so the warm-up
costs one opening rather than one per sample. From then on each sample is
one O(1) `update`, bit-identical to the Function API. A function with several
outputs (MACD, Bollinger Bands) is one plug-in instance per output, chosen
by an `output` parameter, so every node of the graph is one series and
nothing downstream unpacks tuples. talipp indicators drop their oldest
values (`purge_oldest`) once they hold twice what they keep, which is far
more than any of them reads. Reference values for TA-Lib indicators come
from its batch functions over a fixed series; for talipp, from the
definitions worked by hand. The zero-lag average is our own, in plain
Python: talipp rounds its lag half to even, so for periods of 4, 8, 12...
it lags one sample more than the definition most tools use.

**Why:** a stream per node is O(1) per sample and matches TA-Lib's batch
values exactly, so a backtest and its live run agree with each other and
with any chart built on TA-Lib. talipp keeps every value it sees; a run of
years would otherwise grow without end.

**Rejected:** recomputing a window per sample with the batch functions
(O(lookback) each, and the warm-up length computed by us rather than
asked of TA-Lib); one node returning several outputs (the graph carries
one float per node, and a strategy reading "the MACD" should name which
line); trying to open a stream on every sample (O(n²) over the warm-up: 19 s
for a MACD at its bounds); talipp for indicators TA-Lib has (TA-Lib's streams are exact
against its batch values and faster); talipp unbounded.

## D30 · Forecasters and gates are graph nodes; a strategy observes the graph (2026-10-22)

A forecaster or a gate is an `Indicator`: it reads graph nodes and gives one
sample per instant, so it runs in the indicator graph, its samples are
journalled in `IndicatorValues`, and the conformance kit checks it with the
indicator checks. The per-strategy stages (combiner, sizer, exits, risk
gates) are plain objects a `Strategy` calls. The strategy observes the graph
(`Graph.observe`): after each stabilisation it hears which nodes took a
sample, and decides when its price stream's close is among them, so every
value it reads is the latest known then. It decides nothing until every
forecaster and gate has a sample. A gate passes above 0; any veto makes the
combined forecast 0 and the target flat, whatever the sizer's buffer.
Forecasts are capped at ±20 each and once combined; a combined forecast that
is not finite stops the run. A signal is published only when the target
differs from the position held (the sizer's buffer keeps small changes from
trading). Risk gates guard against adding risk: a proposal that only takes
risk off (to flat, or smaller on the same side) never reaches them, so a
stop or a veto always leaves. A gate that refuses is published as
`SignalRefused`, and the position held stays, unless the refused signal was
a reversal: then the strategy is signalled flat instead; a gate may also
doubt a close's price, which is then not acted on (D34); every gate hears of each signal
sent (`sent`), so one counting signals counts only those that left, and sees
the previous close of the price stream. A signal's id hashes the
configuration's digest, the strategy, the instant and the target (−0.0 as
0.0), so the same decision always has the same id and runs do not share
ids. Each stage belongs to one strategy, since it keeps state; every stage
must be used. The position held is the last signalled target, or what the
simulated broker has filled when the strategy has one (D31).

In the configuration, `[[strategies]]` name their price stream (a price
field, not a volume) and stages; stages are `[[components]]` with names, and
only exits read inputs (graph nodes or streams' fields). A strategy with one
forecaster needs no combiner. The sizer trades to the nearest edge of its
no-trade buffer, as pysystemtrade does by default (`buffer_trade_to_edge:
True`).

**Why:** forecasters are series over indicator values, the same shape as
indicators; the graph already gives them stabilisation, journalling, replay
and the kit's no-peeking check. Observing the graph rather than
`IndicatorValues` lets a strategy decide at every close of its price
stream, including instants at which no indicator produced a sample.

**Rejected:** forecasters as their own engine stage (a second scheduler
with the graph's guarantees rebuilt); deciding on `IndicatorValues`
(published only when an indicator produced, so a strategy would miss
closes); risk gates seeing every proposal (a jump or rate limit then blocked
the stop-out it exists to allow, found in review); MQL5's averaged vote with
silent filters diluting (D8); signalling
every bar whatever the target (idempotent delivery would hide it, but the
journal and the person would drown).

## D31 · Simulated fills at the next bar's open; the report reads only the journal (2026-10-22)

A strategy with `fills` in its configuration has a `SimulatedBroker`. A
signal is made at a close of the strategy's price stream; the broker fills
it at the open of that stream's next bar: the first bar of the same
instrument and size starting at or after the end of the bar the signal was
made on. (Not at or after the signal's instant: with a grace period both
the bar and the signal on it are published after the bar ends, by when the
next bar has started.) That open is known only when the bar
is, so the `Fill` is published then (`ts`), with the bar's start as
`event_time`; it arrives before the instant settles, so the strategy's
decision at that close already holds the filled position. The broker trades
the difference between the target and the position it holds, and a later
signal before a fill replaces the earlier one. A fill model (plug-in kind
`fills`) prices each fill and its commission; the first, `next_open`, pays
half the spread and the slippage against the trader and a commission as a
fraction of the value traded, and refuses costs at which a sale would
fetch nothing. With a broker, the position a strategy holds
is what has been filled, so a signal not yet filled is asked for again at
the next close if it is still wanted. Fills are journalled and replay byte
for byte.

The text report (`app/report.py`) is computed from the journal alone: the
configuration from its header, then the entries. Equity is the strategy's
cash, starting at 0, plus its position at the close, marked once per close
of its price stream after the instant settles. It reports signals sent and
refused, fills, units and value traded, commission, what fills paid beyond
the open, the end position, profit and loss, the largest drawdown, and the
share of closes spent in the market. A strategy without a fill model was
not simulated, and the report says so instead of showing a result of 0.

**Why:** the next open is the first price a person acting on a close could
get; filling at the signal's own close would trade on a price already
gone. A report from the journal is the same for a run and its replay, so it
can be compared as text in the scenario tests (D4c).

**Rejected:** filling at the signal's close (look-ahead by one bar's
latency); a fill per signal however many were superseded (a person acts on
the latest); the position held still following the signals when a broker
exists (the strategy would believe in trades that never happened);
computing the report from live objects in the run (a replay could not
reproduce it, and it would need the plug-ins).

## D32 · A `heliograph` command for now; the SDK names what plug-ins import (2026-10-23)

`heliograph backtest CONFIG` runs a backtest, journals it under `runs/` (or
`--journal`) and prints its report; `heliograph report` prints a journal's
report (exit 1 if the run did not complete, which the report says) and
`heliograph replay` runs one again and says whether every entry is the same
(exit 1 if not). A problem Heliograph can name is one line on stderr and
exit status 2. Only the installed plug-ins of the kinds a configuration
uses are loaded, so a broken plug-in of another kind does not stop a run. The command calls the app directly, in its own process; the
engine process and its JSON API (D18) arrive with the graphical workspace,
and the command can then become one of its clients.

`heliograph.sdk` re-exports what a plug-in written outside this repository
needs: the plug-in base class, manifest and parameters, each kind's
protocol, `Proposal`, the records a run journals, the conformance declarations, errors and
time. Nothing inside Heliograph imports it, and it imports neither the app
nor the kit (both checked by import-linter). Its docstring's example is a
test.

The example strategy (`examples/trend.toml`) runs on made-up data written
by a seeded script, so it runs offline and its scenario test is exact; the
same configuration runs on real trades by changing its mount.

A finished journal is switched out of WAL mode as it is closed, so it is one
file: read-only readers cannot remove the `-wal` and `-shm` files they
would otherwise leave beside it.

**Why:** the example must be runnable by a person today, before Phase 3;
a command over the app is the least that does it. Plug-in authors need a
surface that does not move when modules are rearranged.

**Rejected:** waiting for the engine process to run anything (nothing a
person could try until Phase 3); real exchange data committed for the
example (its terms, its size, and a scenario that changes when the data is
refreshed); the SDK as the strategy-as-one-class API of design.md (that
waits for the pipeline to need it).

## D33 · Bars built from bars close on their last part (2026-10-23)

A stream on a mount of bars is either the mount's own bars (the same size,
no builder; no grace or gaps, since nothing is built) or bars of a whole
multiple of that size built by `BarsFromBars`. A built bar is its parts put
together: first open and last close by start, highest high, lowest low,
summed volume. It is published when its last part (the finer bar ending at
its end) is known, at that instant; if that part never comes, by an alarm
one finer bar after its end plus the mount's live delay (recorded in the
run's header, so a replay builds the same bars) and the stream's grace.
Bars are published in order and one per instant (the graph takes one bar
of a stream per instant, D27): a bar complete while an earlier one is still
open closes that one then, and is itself published at its own close. A
part of a bar already closed, or known at or after its close, is late,
counted and left out; a part off its own grid, or repeating a start, is
refused. Price and volume options name a trade's values and are refused on
bars; carrying gaps is not offered yet. The run lasts long enough for the
last fallback alarm.

**Why:** an exchange's candles page fast where its trades do not (Kraken
pages trades a thousand at a time), and a strategy on hourly bars should
not need every trade. Closing on the last part makes a built bar known
exactly when the finer bars say it is complete, with no wait. The fallback
cannot be an alarm at the bar's end: an alarm fires before the inputs at
its instant (the engine's rule), so it would close the bar just before its
last part arrived. One finer bar later is when the next part would have
been known.

**Rejected:** an alarm at the end plus grace, as `TimeBars` uses (it closes
before the last part at the same instant); closing when a part of a later
bar arrives (a gap in the data would hold a bar back, and late parts would
reorder bars); building from bars inside the data source (sources deliver
what they hold; building is the stream's job, so the same rules serve
every source).

## D34 · Signals fail safe: refused reversals go flat, doubted closes wait, exits always act (2026-10-23)

A review of everything built found ways a strategy could signal what no
one should act on; three reviews of the fixes found more. The rules:

- **A refused reversal goes flat.** When a risk gate refuses a signal that
  would turn a long into a short (or the reverse), the strategy is
  signalled flat instead: a `Signal` whose `instead_of` names the target
  refused (schema 2). Flat only takes risk off, so no gate stops it. A
  refused entry or addition leaves the position held as it was.
- **A doubted close waits.** A risk gate may doubt the price itself
  (`Suspicious.suspect`), asked at every close before the sizer and exits
  see it: a doubted close is not acted on at all, not even to leave, and
  is journalled as a `SignalRefused` with `doubted` set (the report counts
  them apart). `limits` doubts a close more than `max_jump` from the last
  close it trusted; the next is trusted again if it is back near that
  close (the print is gone) or beyond it on the same side (the move was
  real: a crash costs one close); one the other way, a second bad print,
  is doubted too, but never more than `max_doubts` (3) in a row. The
  graph's nodes have seen a doubted price already: a forecaster, or an
  ATR a stop reads, carries a bad print for a while; a sanity filter in
  front of the graph is on the roadmap.
- **Exits always act.** While the sizer cannot size (warming its
  volatility, a price that is not positive), exits and vetoes still act on
  the position held, and a signal that only takes risk off still leaves;
  nothing that adds risk does.
- **A trailing stop follows the position held.** Its best close starts
  when the position held takes a side (from the close the strategy decided
  to take it on), so a refused or unfilled proposal moves nothing.
- **A stall is no change** (`plugins/volatility.py`): from the third
  unchanged price in a row (`stall_after`), unchanged prices are left out
  of the volatility, so a stalled feed or a market shut between its bars
  neither drains it nor sizes up, and the first move after is measured
  against the volatility from before; a market that often closes
  unchanged, but not for long, is measured as it trades.
- **Volatility has two floors**, once 100 changes are known: the 5%
  quantile of its last 500 values, as Carver's `robust_vol_calc`, and half
  its long-run level (span 500, started from the mean of the first 100
  squared changes, and taking no change over five of its volatilities, so
  a bad print or a gap does not hold it up for hundreds of bars). The
  quantile falls with a long quiet spell; the long-run level falls slowly
  enough that a crossover divided by it shrinks, for `ewmac` spans up to
  twice the window (larger are refused). Before 100 changes there is no
  floor, and `vol_target`'s positions are bounded by its cap alone.
- **Positions are capped** at `max_leverage` (1) times capital in value,
  before the no-trade buffer, whose width is a share of the cap when the
  cap is the smaller, so a capped position does not trade at every close.
- **Settings that could never work are refused:** `vol_target` without
  `bars_per_year` (the old default suited only hourly bars; daily ones
  were sized about 4.9 times too small), a `buffer` of 1 or more,
  `max_jump` of 0, NaN or infinity in any plug-in parameter.

**Why:** a signal provider is trusted to say what to hold. A gate that
blocked a reversal kept a full long against a short forecast with its stop
disarmed (22% down in the review's case); a quiet market or a stalled feed
sized positions up to 241 times capital; `ewmac` 64/256 read a stall as a
forecast of -170,163, which the cap turned into the strongest signal
there is. The first fixes then traded at a bad print, held a long down a
crash of 30% steps to -84%, froze exits for a day after a stall, and acted
on a second bad print that followed a doubted close.

**Rejected:** keeping the position on a refused reversal (the side held is
the one the strategy has left); doubting every close of a run of jumps (a
crash would never be left); never doubting the close after a doubted one
(a second bad print was traded on); a stale flag that stops sizing after
an unchanged run (it froze exits, and a thin feed was stale for good);
leaving out every unchanged price (a market that often closes unchanged
was sized at a fraction of its target); the
quantile floor alone (it falls with a long quiet spell); inferring
`bars_per_year` from the stream's size (right only for markets that trade
around the clock; stock exchanges come in Phase 4).

## D35 · A bar closes after its source's delay; partial bars and misaligned reads are refused (2026-10-23)

From the review of everything built, for bars and their timing:

- **A bar built from trades waits for its source's delay.** `TimeBars`
  closes a bar at its end plus the mount's live delay plus its grace, as
  `BarsFromBars` already did (D33): a source whose trades arrive a minute
  late lost the last minute of every bar, silently.
- **A mount's start and end lie on whole bars of each stream built from
  it.** A mount starting at 00:40 built an "00:00-01:00" bar from 20
  minutes of trades and published it as whole. A mount's own bars, used as
  they come, are whole whatever the grid (an exchange's weekly candles
  start on Mondays; 7-day bars from 1970, on Thursdays). Live data has no
  start: its first bar may be partial (Phase 4).
- **Carried bars stop at the end of their mount's data**, instead of
  running on, flat, to the end of a run that another source's delay keeps
  going.
- **A built stream's grace is shorter than its bar less one part**, or
  bars would wait on each other (D33 had let it through).
- **What a graph node or a strategy reads must close at the same time
  after its end.** That is the mount's delay, plus the grace for bars
  built from trades; bars of a mount of bars, as they come or built, close
  on their last part. The check replaces the old one on graces alone: it
  refused a stream and the minutes it was built from, which close
  together, and let through mounts of different delays, which do not. The
  configuration, not knowing its sources' delays, compares only streams
  of one mount; a run compares them all with its sources' own delays
  before it writes anything. A replay does not check again: it rebuilds
  what the run did.
- **The journal says when it could not write.** A batch that cannot be
  written (a full disk) leaves the journal closed and marked failed, with
  why, instead of "running" with its last entries gone; the error is
  raised if the run had otherwise completed. On a disk too full even for
  the status it stays "running", which a replay refuses; a run that had
  failed keeps its own error. A stamp SQLite cannot hold is refused when
  the entry is added, so the run fails there.
- **A run records the rules its bars were built by** (`bars`, 2 since this
  decision), so a journal written before is replayed under the rules it
  ran under, and still matches itself.
- **Late data is recorded.** Each stream's count of points or bars left out
  for arriving after their bar closed goes into the header at the end of
  the run (`late`), and the report says so when it is not nothing.

**Why:** each was data lost or misread without a word: closes and volumes
quietly wrong, a bar half built, an indicator reading one stream's bar
against another's a bar earlier, a journal that looked unfinished.

**Rejected:** dropping partial bars at a mount's edges (the configuration
can say what it means instead); keeping the grace check and adding a delay
check beside it (two rules for one property, and the first was wrong for
built bars); journalling late counts as messages during the run (only the
end knows the total, and the header is where a run's outcome is).

## D36 · Sources are trusted only as far as they are known; the store refuses a short re-download (2026-10-23)

From the review of everything built, for getting data in:

- **Trade history only from exchanges known to page it by time.** Many
  exchanges' `fetch_trades` ignore `since` and answer with their newest
  trades (58 in CCXT 4.5, Bybit, KuCoin, Bitstamp and Coinbase Exchange
  among them): paging them returned a fraction of the history, or nothing,
  with no error. The CCXT source offers trade history only for
  `PAGES_TRADES_BY_TIME` (Kraken, which the live tests check), or with
  `trust_since` set; candles page by time everywhere and stay available.
  The source's capabilities say why trade history is withheld, and a
  mount asking for it is told. The live tests ask each exchange in
  `PAGES_TRADES_BY_TIME` for trades from hours back and check the answer
  starts there.
- **Candles must sit on one grid.** CCXT names some timeframes for what
  they are not (Kraken's `2w` is 15 days): candles not a whole number of
  sizes apart are refused, and a timeframe whose own value (in minutes or
  seconds) is another size is not offered at all. The grid is the
  exchange's, not 1970's (weekly candles start on Mondays).
- **Temporary errors are retried; permanent ones say so.** A time-out, a
  rate limit or an outage is tried again `retries` (3) times through the
  throttle, waiting `backoff` (2 s) before the first retry and twice as
  long before each after, up to a minute, before the run fails; a bad symbol, an
  unsupported or rejected request or a refused key is `InvalidValueError`
  at once, live as in history. An exchange that thinks it is attacked
  (Binance's 418) is never asked again: its ban grows with each request.
- **One exchange, one rate limit.** Sources that declare the same
  `rate_key` (every CCXT source on one exchange, whatever its other
  parameters) share one throttle.
- **No bar crosses its request's end.** The file and store sources stop
  before such a bar, as CCXT's did, and the history feed refuses one from
  any source.
- **A mount that delivers nothing fails the run.** A wrong time unit put
  every row of a file in 1970 and the run "succeeded" on nothing. A mount
  whose quiet span is expected sets `allow_empty`.
- **The store refuses a short re-download.** A later download replaces
  what it could have delivered (for bars, only the bars whole within its
  interval), and publishing refuses a download that left more than one in
  a hundred of the most rows its interval had held without a row it
  brought at the same time (an exchange answering with its newest rows,
  one that stopped paging halfway, one that skipped rows or stopped
  sending empty bars), naming it: a source that answered short would
  otherwise erase good data while the snapshot still claimed the whole
  span. A source asked again gives back what it gave before, so rows are
  matched, not counted or compared by span, which let a quarter of an
  interval, or every other trade, go. Measuring against the most ever
  held, not the rows replaced, means neither the same short answer given
  twice nor a run of re-downloads each a little shorter wears data away.
  A short download counts only if its interval still stands short at the
  end, so one later downloaded again in full is published. When the
  source really holds fewer now, the owner publishes accepting that
  download, which later downloads of its interval are then measured
  against; the manifest records only acceptances that were needed, and
  an unknown id is refused.
- **Cleaning rules are versioned.** The rules above changed what a
  snapshot holds, so a snapshot records the rules it was cleaned under
  (`RULES`, apart from the file `FORMAT`) and is verified and rebuilt
  under them; a snapshot published before keeps its id and still verifies.

**Why:** each was data missing or wrong without a word, or a run lost to a
moment's outage.

**Rejected:** detecting an exchange that ignores `since` from its answers
(a quiet market looks the same); dropping off-grid candles (they are
another size, not noise); keeping the rows a re-download did not bring
(a vendor's correction may delete); retrying inside the app's throttle for
every source (only the source knows which errors are temporary).

## D37 · The engine process: Starlette on uvicorn, one worker, a small JSON API (2026-10-24)

The engine's process (D18) is `heliograph serve`: a Starlette application
on uvicorn, listening on `127.0.0.1:8765` by default, its options from
arguments or `HELIOGRAPH_*` environment variables (app/server.py lists the
API). Backtests run one at a time on one worker thread, in the order
submitted; each journal is `<data>/runs/<id>.sqlite`, the id the
configuration's name and the UTC second it was submitted. The journals are
the record: what the process knows of a backtest it did not run itself
comes from its journal's header, so a restarted engine lists every earlier
run, and a journal still marked running is reported as interrupted.

SIGTERM or SIGINT, the moment it arrives, tells the running backtest to
stop at its next input (`run_backtest(stop=)`: its journal is closed as
failed, saying it was stopped); then the queued ones are dropped, open
requests get three seconds to finish, and the process exits 0 within ten
seconds of the signal. The signal handler only sets plain flags (no lock,
no logging), so signals arriving inside one another cannot deadlock it.
A backtest that has not stopped by then (blocked reading a file, in a
thread Python would wait for at exit), or a request still being answered
on its thread, is left behind: the process exits 1 at once, and a journal
still marked running reads as interrupted. A start that cannot happen (a port taken or out of range, a
folder that is not one) is one message and exit 2. Every log line on
stderr is one JSON object with the time, level and event; a request that
crashes is a JSON 500 and a logged traceback. Handlers that read files or
plug-ins run on threads, so a report does not hold up the health check.
Times in the API are ISO 8601 strings to the nanosecond (`to_iso`, D19);
a plug-in's parameters are its model's JSON Schema. Backtests are listed
the latest submitted first; a name is up to 100 characters, so its id
(the name and the second) stays a plain file name.

The process is tested from outside (tests/process): started with the
`heliograph` command, spoken to over HTTP, stopped with signals, by tests
that import nothing from `heliograph` (a suite rule checks it). Coverage
follows the process (`patch = ["subprocess"]`, saved on SIGTERM) and is
combined before the per-file floor.

**Why:** Starlette and uvicorn are what NiceGUI itself runs on, so the
workspace adds no new server; Starlette is small (routes, JSON, lifespan)
and leaves validation to our own Pydantic models, where FastAPI would add
a second way to declare them. One worker matches D18 and keeps a run's
journal the only writer of its file. Ids from names and times read well
in a folder and sort by time within a configuration.

**Rejected:** FastAPI (its schemas and validation would duplicate the
manifests'); the standard library's `http.server` (no lifespan, signal
handling or streaming to build on later); a database of runs beside the
journals (two records to keep in step); runs in parallel processes (not
needed for one person's backtests yet; Phase 5's optimisation will ask
again).

## D38 · Chart series come from the journal, with times a chart can use (2026-10-24)

A backtest's bars, graph node values, signals (sent and refused), fills
and equity are served from its journal alone (app/charts.py), as the
report is (D31): a run and its replay chart the same, and nothing the
engine did not journal can appear. Each item carries its times as ISO 8601
text and `t`, milliseconds since 1970, for a chart's axis (D19). A bar's
`t` is its start, where charts draw it; anything else's is when it became
known, so a value computed from a bar sits at that bar's end and a fill at
the open it was filled at: a chart cannot show a decision before its data.
`start` and `end` select by the time an entry became known; journal reads
can now select types and a span in SQL (`read_entries(only=, start=,
end=)`), so neither a chart nor the report decodes every trade. A running
backtest can be charted as far as its journal has been written, and its
status says how far through its span it has got (`progress`), from each
instant as it settles (`run_backtest(progress=)`): never ahead to a
feed's first trade, which the engine reads early to merge its feeds. A
journal read as it closes still ends as one file: SQLite will not leave
WAL mode while any other connection has it open, so the close tries
again in the gaps between reads, for up to two seconds (a close may be
part of the process's stop, with its own deadline), the server tidies it
once more after the run unless it is stopping (`tidy`), and an engine
starting on the folder tidies any journal left beside its -wal file.

**Why:** the GUI holds no logic (D18): what a chart may draw, and where,
is decided here once, where it can be tested against the report's own
figures. Milliseconds lose nothing a chart can show; ISO text keeps every
nanosecond for anything else.

**Rejected:** recomputing indicators for display (a second implementation
to drift from the run's); drawing indicator values at their bar's start
(it shows each a bar before it could be known, as TradingView's
`lookahead_on` does); streaming progress over SSE now (a poll every second
is enough for one person; it waits for live sessions, Phase 4).


## D39 · The window is a client of the API, and draws what the run holds (2026-10-26)

The window (`heliograph gui`, package `heliograph.gui`) is a NiceGUI app
served to a browser, and a client of the engine's JSON API only: it
imports nothing else of Heliograph (an import-linter contract), takes the
engine's address (`--engine`, `HELIOGRAPH_ENGINE`; default
`http://127.0.0.1:8765`) and holds no logic of its own. The engine runs
first, in its own process (D18, D37); the window does not start it. A
screen offers what the engine says exists: the Workspace lists a run's
streams, graph nodes and strategies from `contents` (D38), so a plug-in
the engine installs appears with no code written for it here. The view is
a function of the choices made (run, stream, strategy, the nodes shown and
whether on the price or in a pane of their own): each change redraws it,
and what a choice sets going is counted, so a screen can say when it has
settled. Series become what a chart draws in `gui/series.py`, pure
functions of the API's rows, tested without a browser: a signal sits on
the bar whose close it was made on (bars now say when they end, `end_t`),
a fill on the bar it filled on; a value unknown is a gap; one point a
second. The chart is TradingView's Lightweight Charts 5.2.1 (Apache-2.0),
vendored with its licence beside the element that wraps it
(`gui/static/lightweight_charts`), showing its attribution logo and a
credit line; its panes share a time axis and a crosshair, and zoom stays
in the browser. A choice overtaken by a later one is dropped when its
answer arrives (a count of the choices made), an engine error clears the
chart so none is left that belongs to other choices, and the display
choices (volume, signals, fills, equity) stay when another run is opened;
signals and fills are drawn on the stream their strategy decides on, not
on others. The client takes an answer in any shape the engine never gives
(a web server that is not the engine) as an engine error to say, not a
traceback. Served as a web page first (`127.0.0.1`); a native
window (pywebview, D7) is the same app in another frame, and comes when
wanted.

**Why:** a client that imports the engine would hold logic, and the gate
for Phase 3 (a new indicator appears with no GUI code) holds only if the
window learns what exists by asking. A page is also what Elysium could
frame or replace (D20) without a second UI codebase; native mode waits
because pywebview adds a platform dependency for nothing a browser tab
does not do.

**Rejected:** starting the engine from the window (two lifetimes in one
process, and the engine must outlive a closed tab: sessions in Phase 4);
drawing indicator lists in GUI code; a React front end (a second
language and toolchain for one person); a browser test in the suite
(Chromium and Playwright on the owner's machine for a half of the chart
that is a hundred lines; checked by hand with a screenshot instead, and
the Python half is exercised through NiceGUI's simulated browser).

## D40 · Settings forms are generated from the parameter schemas (2026-10-26)

The Plug-ins screen lists what the engine's `/api/plugins` lists, by kind,
and draws each plug-in's settings from the JSON Schema of its parameter
model (`gui/forms.py` reads it; `gui/form.py` draws it). It knows no
plug-in and no kind: a kind it has no title for is shown under its own
name, a plug-in installed tomorrow has a form tomorrow. The schema's
subset Pydantic emits is understood (integers and numbers with bounds,
exclusive bounds and a `step` hint, strings, booleans, enums behind a
`$ref`, a value-or-null, lists of strings, each with default and help);
anything else is a JSON text box, so it can still be set. A value is
checked as it changes, and a required one not yet given is asked for from
the start; the engine checks again when the parameters are used. Only
what differs from its default is sent or shown (a default left out follows
the plug-in if it changes), written as the inline table a configuration
takes, inside the table that uses the plug-in (`[[components]]`, or a
mount for a data source), so what the form makes can be copied into a
configuration.

**Why:** the gate of Phase 3 is that a new indicator appears,
configurable, with no GUI code written for it; the schema is the one place
a plug-in already says what it takes (D3, D9), bounds included.

**Rejected:** JSON Forms (a Vue and JavaScript toolchain for forms a
hundred lines of Python draw from the same schema); a form per plug-in
(the code the gate forbids); sending every setting (a configuration that
no longer follows a changed default).

## D41 · The Strategy Tester submits a configuration and follows its run (2026-10-26)

The Tester screen (`gui/tester.py`) takes a configuration from the engine's
own (`/api/configs`) or as text typed in, submits it as a backtest, follows
the run while it is queued or running (a timer asks every second; one run
at a time from this screen), and when it has ended shows the engine's text
report, whatever way it ended (a failed run's report goes as far as the run
did), with a link that opens the run in the Workspace (`/?run=<id>`), where
the charts are. The window does not read the configuration: the engine
checks it and says everything that is wrong at once, and that is shown as
it is said. The run being followed is kept in the page's address
(`/tester?run=<id>`), so a reload goes on with it. Background work a choice
sets going is counted in `gui/work.py` for both screens, so a test waits for
it and not for a time.

Two things the window shows follow from what the engine says, not from what
the window knows. A plug-in's detail says what a configuration gives its
`inputs` (`required`, `optional`, `none`; `inputs_rule` in `app/config.py`),
and the snippet the Plug-ins screen makes reminds of it in a comment. A line
drawn over the price goes on the price's scale only if its values lie within
the candles' range, a tenth of the price level beyond it either way; any other
(an ATR, a forecast) gets a scale of its own at the left, so that it does not
flatten the candles.

**Why:** the Tester is the shortest way from a configuration to a result,
and it uses only what the API already offers (D18, D37); the charts are
the Workspace's, not a second set.

**Rejected:** building the configuration from catalogue picks in this step
(the Plug-ins screen already shows each plug-in's table, ready to paste;
assembling a whole configuration is the Wizard's work); streaming progress
to the page (the engine has none until live sessions, Phase 4).

## D42 · The Wizard assembles a configuration as a graph (2026-10-27)

The Wizard (`gui/wizard.py`) starts from one of the engine's saved
configurations and shows it as a node graph (Vue Flow 1.48.2, MIT, vendored
unmodified in `gui/static/vue_flow` with every bundled package's licence and a
reproducible build, `scripts/vendor/vue_flow/build.sh`). Streams, components and
strategies are nodes; an input a component reads, a stage a strategy uses and the
price a strategy decides at are edges. Dragging from one node's handle to another
reads an input or makes a stage; clicking a node shows its settings, a form drawn
from the plug-in's own schema (D40); the configuration's text follows every change;
"Run as a backtest" submits it and goes to the Tester (D41) to follow the run.

The configuration dictionary stays the truth (`gui/wizard_model.py`). The picture is
derived from it and an edit changes it, so everything the picture does not show
(mode, mounts, streams, a component's other keys, an input that names nothing) is
carried through untouched. Connecting refuses what cannot be wired and says why: a
bar field a bar does not have, an input read twice, a node that would read itself
through others, a stage already in another strategy, an input to a component that
reads none. Positions are laid out in layers (streams, then each node one layer
past what it reads, then the stages, then the strategies) and are not saved; a node
the person drags stays where it was put while the page is open. Components are
written in an order in which each is read after what it reads.

The window knows no kind of plug-in. What a strategy takes (its fields that name
components, the kind each must be, one or many), which kinds are nodes others can
read, and which read inputs come from `GET /api/pipeline`, said once in
`STRATEGY_ROLES` and `GRAPH_KINDS` in `app/config.py` and kept equal to
`StrategyConfig` by a test. The engine's own validation has the last word: the
Wizard may assemble something that does not run, and the engine says what is wrong
when it is run.

TOML is written with tomli-w (MIT), so comments in the saved file are lost
in the Wizard's text, which is a new text and not an edit of the saved one, and
date-times are written with a space (valid TOML).

A configuration is kept with `PUT /api/configs/{name}` (`{"config": text,
"replace": false}`): the engine makes the checks that need no plug-in (the configuration's structure,
not a plug-in's own parameters, which a run checks as it builds them), writes the text
exactly as given to a part file and moves it into `--configs` (a new name is linked,
so a name that has appeared since is not overwritten; `replace` renames over it),
and answers 201 for a new name, 200 for a replaced one, 409 if the name exists
without `replace` or the engine has no folder, 400 for a text that would not run.
The folder only ever holds whole files.

A configuration can start empty ("Start empty": a backtest with nothing in it) and
be given data: "Add data…" opens a dialog (`gui/data_form.py`) that asks for a name,
a data source (the engine's `data_sources` plug-ins) with the settings form its own
schema draws, the instrument, the span and the size of the bars to build, and adds
the mount and its stream together or neither. A source that says in its own settings
what it delivers (`kind`, `bar_size`) is believed over the dialog's boxes, so the
data and its source cannot disagree. A stream nothing reads can be removed, and its
mount with it when no other stream is of it. Built this way, from nothing, a
configuration ran to completion in the browser. The Wizard's "Keep as" box uses it and then
offers the new name among the saved ones.

**Why:** a whole configuration is hard to write by hand; the graph is what the
configuration is, so it is the clearest way to build one, and the plug-in forms
(D40) already ask for each part's settings.

**Rejected:** a layout the person arranges and the Wizard saves (the
configuration has nowhere to keep positions, and keeping them elsewhere is a second
copy of a fact); starting from nothing (mounts and streams need a data-source
form first); hard-coding the strategy's roles in the window (a second list to keep in
step with the engine); Vue Flow's built-in delete key (a person selecting text in a
form would remove a node).
