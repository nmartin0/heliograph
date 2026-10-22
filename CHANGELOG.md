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

### Fixed

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
