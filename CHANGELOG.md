# Changelog

All notable changes to Heliograph are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Line-ending rules and ignore list.
- Python package skeleton in a `src/` layout, built with hatchling and
  managed with uv, with a locked set of development tools.
- `scripts/check.sh`: one entry point for lint, format, strict types and
  tests, including a run in a non-UTC time zone.
- Continuous integration on GitHub Actions running the same checks.
- Core time: UTC instants as integer nanoseconds, with conversion from and to
  aware datetimes and ISO 8601 text; naive datetimes are refused.
- Core messages: `Instrument` and `Bar`, immutable and self-validating; a bar
  becomes known when its interval closes.
- Clocks: a forward-only simulated clock for backtests and a wall clock for
  live data. A lint rule bans reading the system time anywhere else.
- Message bus: typed publish and subscribe with breadth-first delivery; it
  refuses any message stamped later than the clock's current time.
- Backtest engine: merges time-ordered feeds and publishes each message at
  its own instant, proven by a property test that no component sees the
  future.
- Plug-in manifests and parameter schemas: each plug-in declares its kind,
  name, version, summary and a Pydantic parameter model whose JSON schema
  carries defaults, bounds, search steps and help text.
- First runtime dependency, pydantic, and `THIRD_PARTY_NOTICES.md` listing
  every runtime component and its license.
- Plug-in registry: plug-ins are discovered through `heliograph.<kind>` entry
  points; unknown names fail with a list of the valid ones.
- Run configuration and composition root: a TOML file names the plug-ins and
  their parameters, is validated as a whole and fingerprinted with SHA-256;
  every problem is reported at once and a bad configuration builds nothing.
- Layering checks: import-linter keeps dependencies pointing inward
  (app, then plugins, then core), and a test keeps third-party packages out of
  the core.
- One error hierarchy: every error Heliograph raises derives from
  `HeliographError` and also from the builtin it refines, such as
  `ValueError`, so callers can catch either.
- `scripts/make_apply_script.py`: packages a range of commits as a bash
  script that checks the target repository's files before and after applying
  them, makes the person running it the author, and runs the checks.
- Project documentation in `docs/`: design, roadmap, decisions, licence
  policy, principles, workflow, lessons and research notes, with
  `CLAUDE.md` as the entry point for AI-assisted sessions.
- Every Python and shell file carries an SPDX copyright header, checked by a
  test.
- `scripts/licence_report.py`, run by `./scripts/check.sh`: checks every
  runtime dependency's licence against the policy and generates the table in
  `THIRD_PARTY_NOTICES.md`.
- Alarms: components set keyed one-off or repeating alarms through a
  `Scheduler`, and the backtest engine publishes `AlarmFired` messages in time
  order between feed messages; `run(until=…)` stops a replay at an instant
  and closes what is still open.
- `Point`: one observation of an instrument with named numeric values, the
  time it happened by its source and the time Heliograph received it, which is
  when it becomes known. `Bar` gains an optional `received_at` for bars that
  arrive after they close.
- Stream and bar specs: each consumer names its instrument and how bars are
  cut (size, anchor, grace for late points, gap policy, price and volume
  fields); spans such as `15m`, `4h` or `1h30m` are parsed and written back
  exactly.
- `Settled`: the backtest engine publishes it once everything at an instant
  has been delivered and before time moves on, so consumers that combine
  streams see them together; each run ends with a `RunEnded` mark. The engine
  takes alarms and feed messages in one time-ordered loop, refuses engine
  messages in feeds, and refuses anything for an instant that has settled.
- `TimeBars`: builds the bars of a stream from its points and publishes each
  when its interval closes, by alarm, whether or not another point arrives;
  late points are counted and never change a published bar.
- Message journal: the bus numbers every message and records it, marked input,
  engine or derived, before delivering it; replaying a journal's inputs
  reproduces the whole journal.
- SQLite journal (`journals/sqlite`, the first built-in plug-in): one row per
  bus message as deterministic JSON via msgspec; replaying a journal's inputs
  writes byte-identical rows. msgspec (BSD-3-Clause) is the second runtime
  dependency.
- Tests run in a random order each time, printing the seed, and every source
  file must reach 90% branch coverage; CI fails if the checks leave changes in
  the working tree.
- A determinism test: the same session run in fresh processes with different
  hash seeds and time zones writes the same journal: identical entry rows,
  byte for byte, and the same header.
- Rules about the tests themselves, checked from their source in tests/meta:
  no assertion about every item of a possibly empty collection, no imports
  from a conftest, no skip without a reason and none for a missing import, no
  sleeping, and every step of check.sh able to fail it.
- Declared negative controls (tests/controls.toml) and
  scripts/check_controls.py, which breaks each guarantee on purpose and fails
  unless every test it names fails; CI runs them as their own job.
- scripts/mutation_test.py mutation-tests one module against the tests that
  cover it (mutmut, in a scratch copy) and lists every change no test noticed.
- A state-machine test runs random sequences of alarm changes and runs against
  a slow reference model of the engine.
- Data-source ports: DataSource and InstrumentProvider protocols with declared
  capabilities (data kinds for history and live, bar sizes, lookback, live
  delay, rate limit, concurrency, terms of use) and SourceUnavailableError.
  History is stamped as known its declared delay after the event; live data
  when it arrives.
- The asyncio bridge: history from a source becomes an ordinary, lazily read
  engine feed, checked and stamped with the source delay; live data from
  several sources is published in arrival order, stamped with the run clock.
  Data of a kind, size or instrument not asked for is refused; a live bar
  from a venue whose clock runs up to a second ahead of ours is held until it
  closes by our clock, and one arriving earlier is refused as partial.
- A throttle built from each source declared rate limit and concurrency, which
  the source enters around every request; it serves one event loop after
  another.
- A `[mounts]` table in the run configuration names, for each mount, a data
  source, its parameters, an instrument and the data wanted (trades or bars of
  a size; a span for a backtest). Every mount is checked against its source's
  declared capabilities and terms, and every problem is reported at once
  before any source is used. Start and end are date-times with an offset,
  never numbers. Mounts on the same source and parameters share
  one instance, and so one rate limit.
- A data source for CSV and Parquet files: one instrument's trades or bars,
  with times as ISO 8601 or epoch seconds to nanoseconds (or a time-zone-aware
  Parquet timestamp). A row that cannot be read, that goes back in time, or
  a bar that overlaps the one before stops the read naming the file and row.
  CSV files are UTF-8, with or without a byte-order mark. Adds pyarrow
  (Apache-2.0) as a dependency.
- A `static` instrument provider: listings written out in its parameters, each
  with the venue's own symbol and optional price and size increments. An
  instrument listed twice, or two sharing one venue symbol, is refused.
- The data store: each download kept as a raw Parquet file named by its hash
  and never edited; clean data computed from the raw files by rule (a later
  download replaces an earlier one over its interval; failing rows quarantined
  with their reason); published snapshots, audited, whose id is the hash of
  their inputs. `verify` rebuilds a snapshot from its raw files and compares
  bytes (the Phase 1 gate), and the `store` data source reads a snapshot on
  the live delay and terms of the source its data came from.
- `download_mounts` downloads a backtest's mounts into the store after
  checking them, closing every source afterwards, and `publish_mounts`
  publishes their datasets as one snapshot, which a backtest then reads
  through `store` mounts.
- The `ccxt` data source (D21): history trades and candles and live trades
  from one exchange through CCXT, the exchange a parameter. Capabilities come
  from what CCXT declares about the exchange; CCXT's own throttle is off and
  every request goes through ours. Pages that repeat trades are read once;
  only whole candles become bars; a history shorter than asked stops the read.
  Live tests (`uv run pytest -m live`) check it against Kraken; they are
  excluded by default. Adds ccxt (MIT) and its dependencies.
- Streams in the run configuration (`[[streams]]`): bars of a size built from
  a mount's trades, with optional grace, gap policy, price and volume fields;
  a stream naming no mount, a bars mount, or the same stream twice is refused
  when the configuration is read.
- `run_backtest` runs a backtest from its configuration: its mounts through
  the bridge, its streams' bars, the engine, and a new SQLite journal whose
  header records the configuration (its digest and validated form), each
  mount's source and parameters, the store snapshots read, the interval and
  the version. `replay_backtest` rebuilds a run from its journal alone, entry
  for entry. Configurations with components are refused until Phase 2 runs
  them.
- The indicator graph (core/graph.py): bar-field inputs and indicators that
  read earlier nodes, updated once per instant when it settles, in dependency
  order, so a 1h indicator reading a 4h one sees the 4h value ending at the
  same instant. Every sample propagates, equal or not; a node without a sample
  stops propagation. One `IndicatorValues` message per instant carries every
  indicator's latest sample and which changed.
- The first indicators, `sma` and `ema` (plug-ins of kind `indicators`):
  moving averages one sample at a time, the simple one summed exactly, the
  exponential one seeded from the simple average of its first period as TA-
  Lib's is.
- Indicators in a run: `[[components]]` of kind `indicators` name an indicator
  and its inputs (a stream's bar field such as `btc@1h.close`, or an indicator
  defined earlier); a run computes them through the indicator graph, journals
  their values, and records each one's plug-in, version, parameters and inputs
  in its header. A replay refuses an indicator of another version than the run
  used.
- The conformance kit (`heliograph.testkit`, with the `testkit` extra for
  plug-ins written elsewhere): every registered plug-in is checked by default.
  Every kind: it builds (in an empty temporary folder, and is closed), and its
  parameters survive JSON. Indicators also: no peeking (in a fresh
  interpreter, the same beginning answered alike whatever future follows),
  warm-up, gaps as the graph makes them, constant input, one update alone,
  and reference values. A plug-in declares `conformance = Conformance(...)`
  to give parameters, references or the bar fields its inputs are, or to
  hold a check as `todo`, `off` or `replaced` with a reason; the report
  lists them all. A deliberately broken plug-in fails each check, the peeking
  one being Phase 2's gate.
- Indicators on TA-Lib's streams: `rsi`, `atr` (reading high, low and close),
  `macd` and `bbands` (one line or band each, chosen by `output`) and
  `stddev`, one sample at a time and bit-identical to TA-Lib's batch values.
  On talipp, for averages TA-Lib lacks: `hma` (Hull) and `alma` (Arnaud
  Legoux), their memory bounded however long the run. And `zlema` (zero lag)
  in plain Python, its lag rounded down as most tools round it. TA-Lib 0.8
  and talipp 2.7 are new dependencies (BSD and MIT); numpy is now declared.
- The strategy pipeline (core/pipeline.py): `[[strategies]]` in a run
  assemble forecasters and gates (graph nodes, journalled with the
  indicators), a combiner, a sizer, exits and risk gates. A strategy decides
  at each close of its price stream; when its target changes it publishes a
  `Signal` (target, side, forecasts, vetoes, the exit that set it, a stable
  id), or a `SignalRefused` naming the risk gate and its reason. Both are
  journalled and replay byte for byte. First stages: `ewmac` and `linear`
  forecasters, a `threshold` gate, a `weighted` combiner, a `vol_target`
  sizer with a no-trade buffer it trades to the edge of, a `trailing_stop`
  exit and a `limits` risk gate. Risk gates see only signals that add risk:
  taking risk off (a stop, a veto) always leaves.
- Simulated fills (core/broker.py): a strategy naming a fill model has its
  signals filled at the open of its price stream's next bar, as a journalled
  `Fill` known when that bar is; the strategy then holds what was filled.
  First fill model: `next_open`, with spread, slippage and commission.
- A text report of a backtest (app/report.py), computed from its journal
  alone: signals, fills, units and value traded, costs, profit and loss,
  largest drawdown and time in the market; the same for a run and its replay.
- The `heliograph` command: `backtest` a configuration and print its
  report, `report` a journal, `replay` one and say whether it is the same.
  It loads only the plug-in kinds a configuration uses, and a report says
  when its run failed or did not finish.
- An example strategy, `examples/trend.toml`: two EWMAC forecasts blended,
  volatility targeting, a trailing stop on the ATR, risk checks, and fills
  at the next open with spread and commission, on made-up hourly data
  written by `scripts/make_example_data.py`.
- Scenario tests: a backtest worked out by hand, its report matched
  exactly (Phase 2's gate), and the example's entry counts and report,
  compared as text with inline-snapshot (a new development dependency, MIT).
- `heliograph.sdk`: the names a plug-in written outside this repository
  imports.
- Streams on mounts of bars: a stream of the mount's size is its bars as
  they come, and one of a whole multiple is built from them, published when
  its last part is known (`BarsFromBars`, D33), allowing for the source's
  delay, one bar per instant. A strategy can run on an
  exchange's candles; `examples/trend.toml` says how. A live test runs a
  backtest on Kraken's minute candles (`uv run pytest -m live`).
- `Registry.installed()`: the installed plug-ins of the kinds asked for, or of
  every kind.
- The engine's process (D18, D37): `heliograph serve` runs a JSON API on
  `127.0.0.1` (port, data and configurations folders from options or
  `HELIOGRAPH_*` variables) for health, the installed plug-ins and their
  parameters' JSON Schemas, the configurations offered, and backtests
  submitted, listed and reported, one at a time. SIGTERM stops it within
  ten seconds, closing a running backtest's journal as stopped; every log
  line is JSON. Times cross it as ISO 8601 text to the nanosecond
  (`to_iso`).
- A backtest's series for charts through the engine's API (D38): its bars
  by stream, graph node values, signals sent and refused, fills and equity,
  each with ISO times and milliseconds for a chart's axis, selectable by
  span, readable while the backtest runs; a running backtest's progress.
  Journal reads select types and spans in SQL, so the report no longer
  decodes every trade.
- The window (D39): `heliograph gui` serves a NiceGUI page, a client of the
  engine's API only. Its Workspace screen draws a run's candles, volume,
  signals and fills, graph nodes (on the price or in panes of their own) and
  equity with TradingView's Lightweight Charts, offering what the run holds.
  Bars now say when they end (`end_t`).
- The Plug-ins screen (D40): every installed plug-in by kind with a settings
  form generated from its parameter schema, checked as it changes, and the
  configuration table that sets it up.
- The Strategy Tester (D41): a saved or typed configuration is submitted, its
  run followed to its end, and its report shown, with a link to chart it in
  the Workspace (`/?run=<id>`).
- The Wizard (D42): a saved configuration drawn as a node graph (Vue Flow,
  vendored with its licences), edited by dragging links, adding and removing
  components and strategies, and each plug-in's settings form, then run as a
  backtest. `GET /api/pipeline` says what a strategy takes, so the window
  hard-codes no kind. Written TOML uses tomli-w.
- `PUT /api/configs/{name}`: the engine keeps a configuration in `--configs`
  after checking it, whole or not at all, and never over another unless told;
  the Wizard's "Keep as" uses it.

### Fixed

- The window, from what was left open after using it: a line drawn over the
  price that is not in the price's units (an ATR) has a scale of its own at the
  left and no longer flattens the candles; the snippet for a component reminds
  of the `inputs` the engine says it takes (the plug-in's detail now says so);
  a box that can be left empty shows its default as a hint; a Tester page
  reloaded goes on with its run (`/tester?run=<id>`).

- Found by using the window in a browser: the Workspace says where runs come
  from when it has none, and says when the run asked for with `?run=` is not
  there; the Tester shows its messages beside the Run button (and in view),
  names a run `backtest` when the name is left empty, offers the chart link
  only for a run that can be charted, and can load a configuration's saved
  text again over edits; the header's dark switch can be seen in dark mode;
  the controls stay on a window narrower than 1,024 px; a plug-in's JSON box
  refuses a list where an object is wanted, and the filter says when nothing
  matches. The engine's configuration errors name each problem's place and
  reason, without the validation library's dump of the input and its link.

- The CCXT source offers trade history only from exchanges known to page it
  by time (Kraken; `trust_since` for others): many answer with their newest
  trades, which returned partial history silently. Candles off their grid
  (Kraken's "2w" is 15 days) are refused, and such timeframes not offered;
  temporary errors are retried (`retries`, waiting longer each time from
  `backoff`), permanent ones reported as such, and an exchange's DDoS ban
  is never retried; every source on one exchange shares one throttle.
- No source may send a bar crossing its request's end; a mount that
  delivers nothing fails the run unless it sets `allow_empty`.
- The store replaces only what a re-download could deliver (whole bars),
  and refuses to publish a download that does not give back the rows its
  interval held (more than one in a hundred missing), unless the owner
  accepts it. Snapshots record
  the cleaning rules they were made under, and older ones still verify.
- Bars built from trades wait for their source's delay: a source whose
  trades arrive late lost the end of every bar.
- A mount's start and end must lie on whole bars of each stream built from
  it, and a built stream's grace must be shorter than its bar less one
  part. Carried bars stop at the end of their mount's data.
- What a node or strategy reads must close at the same time after its end
  (delay, and grace for bars from trades); this replaces the check on
  graces, which refused streams that close together and let through
  mounts of different delays. Runs check it with their sources' delays.
- A journal whose last entries cannot be written is marked failed and
  says why; a stamp SQLite cannot hold is refused when it is added.
- Runs record the rules their bars were built by (`bars` in the header), so
  journals written before these fixes replay as they ran.
- Late data left out is recorded in the header (`late`) and reported.
- A risk gate refusing a reversal no longer keeps the side held: the
  strategy is signalled flat instead (`Signal.instead_of`, schema 2).
- A close whose price a risk gate doubts (`limits`: a jump from the last
  close trusted) is not acted on at all, not even to leave, until a close
  is back near the last trusted or confirms the move (at most three in a
  row): a bad print is not traded on and a crash costs one close. The
  report counts doubted closes apart from refused signals.
- While the sizer cannot size, exits and vetoes still act on the position
  held; only signals that take risk off leave.
- The trailing stop's best close follows the position held, so a refused
  proposal no longer resets it.
- `vol_target` and `ewmac` leave a stall's unchanged prices out of their
  volatility and floor it at the 5% quantile of its recent values
  (Carver's `robust_vol_calc`) and at half its long-run level; `vol_target` caps
  positions at `max_leverage` times capital, before its buffer. Quiet or
  stalled markets sized up to 241 times capital, and `ewmac` 64/256
  reached -170,163.
- `vol_target` requires `bars_per_year` (the default suited hourly bars
  only) and a buffer below 1; `limits` a `max_jump` above 0; `ewmac` a
  slow span at most twice its floor window; plug-in parameters refuse NaN
  and infinity.
- A finished journal is one file: reading it no longer leaves `-wal` and
  `-shm` files beside it.
- A journal whose name holds `?`, `#` or `%` is read as itself, and a file
  that is not a journal is said to be so instead of a database error.
- Hypothesis has no deadline anywhere in the suite (tests/conftest.py): on a
  slower computer a correct property test failed for taking 285 ms.
- `from_iso` keeps fractions of a second to the nanosecond instead of
  silently dropping digits past the microsecond, and refuses finer ones;
  `to_datetime` raises `InvalidValueError` for instants outside the years 1
  to 9999 instead of a bare `OverflowError`.
- `Instrument` refuses a venue containing `:`, which let two different
  instruments share one key (`a:b` + `c` and `a` + `b:c`).
- `WallClock` never moves backwards: when the system clock steps back it
  holds at the latest time it reported, instead of letting the bus refuse
  every message already stamped and end a live run.
- A manifest whose `params` is not a class raises `PluginDefinitionError`
  instead of a bare `TypeError`.
- A run configuration can be dumped to JSON or a dict and hashed; its
  frozen parameters made `model_dump_json()` raise, `model_dump()` warn and
  `hash()` fail.
- A run configuration refuses date-times and times of day without a UTC
  offset in plug-in parameters, naming where each one is; they reached
  plug-ins as naive values, against the project's time rule.
- `heliograph` imports from a source folder that was never installed;
  `__version__` is then `0+unknown` instead of an import error.
- `Bar` stores its prices and volume as floats and refuses booleans, so a bar
  journalled and read back encodes identically.

### Changed

- Decisions D17–D20 and `docs/research/elysium.md`: OCaml is not adopted,
  the engine runs as a supervisable process behind a JSON API, times cross
  JSON as ISO strings, and Heliograph stays standalone until Elysium's
  module API settles; a draft Elysium manifest is in `integrations/elysium/`.
- D25 records the store's design; the roadmap marks batch C done and Phase 1's
  gate passing, with two items left: a run from configuration, journalled with
  its snapshot, and a first live check against Kraken.
- D26 records how a run is journalled and replayed; the roadmap marks Phase 1
  built with its gate passing, the first live check against Kraken left before
  the tag.
- A mount carries at most one stream of each bar size: bars say their
  instrument and size, not their grace, so two such streams could not be told
  apart by what reads them.
- D27 records the indicator graph's cutoff (a missing sample, not an equal
  value) and one stream per mount and size; the roadmap plans Phase 2 in four
  batches and moves session calendars to Phase 4.
- D28 records where the conformance kit lives and how plug-ins declare what it
  expects; testing.md and the roadmap mark the kit built, the adapters' own
  checks still to come.
- D29 records how library indicators are wrapped (TA-Lib streams opened on
  retry, one node per output, talipp bounded); the roadmap marks D3 done and
  moves bars built from finer bars to D4.
- D30 records how strategies run (forecasters and gates as graph nodes, a
  strategy observing the graph); design.md and the roadmap describe the
  pipeline as built, and D4 is split into three parts.
- D31 records simulated fills at the next open and the report from the
  journal; the roadmap marks D4b done.
- D33 records how bars are built from bars; the roadmap marks streams on
  mounts of bars done.
- D32 records the command, the SDK and the example; the roadmap marks D4c
  and Phase 2's gate done, and moves streams on bar mounts to the next
  batch; testing.md describes the scenarios; the README says how to run
  the example.
