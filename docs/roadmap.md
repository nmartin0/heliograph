# Roadmap

Seven phases ordered by dependency, not by date. Each ends with a gate: a
test or demonstration that must pass before the next phase starts, and a tagged
release. Heliograph becomes a usable signal provider at the end of Phase 4.

| Phase | Theme | Status |
| --- | --- | --- |
| 0 | Foundations | done; CI green; tag deferred by the owner |
| 1 | Data | built, gate passing; left: the first live check against Kraken |
| 2 | Backtest engine and strategy pipeline | in progress (D1 the indicator graph, D2 the conformance kit and D3 library indicators done) |
| 3 | Graphical workspace | planned |
| 4 | Signals and paper trading | planned |
| 5 | Research tools | planned |
| 6 | Advanced plug-ins | planned |

## Phase 0 · Foundations (done)

Repository and tooling; UTC nanosecond time; `Instrument` and `Bar`; simulated
and wall clocks; the time-guarded message bus; the backtest event loop;
plug-in manifests, parameter schemas and registry; run configuration and
composition root; layering checks; one error hierarchy; the eight defects
found by the Elysium team's review fixed.

**Gate (passed):** layer contracts and the no-look-ahead property test pass on
every commit, and CI is green on GitHub. Tagging `v0.1.0` is deferred at the
owner's request (2026-10-21).

## Phase 1 · Data

**Goal:** real data flows from a source through clean storage into streams at
any bar size, closed on time, recorded in a replayable journal.

Planned commits, in order:

- [x] `build`: copyright header on every file we own, checked in CI
      (`third_party/` exempt).
- [x] `build`: licence report of runtime dependencies committed and checked;
      AGPL and source-available fail without an approved exception.
- [x] `feat(core)`: clock alarms (`at`, `every`) fired in time order by the
      engine between feed messages.
- [x] `feat(core)`: `Point` with `received_at`; `Bar` gains `received_at`.
- [x] `feat(core)`: stream and bar specs: size, anchor, grace, gap policy,
      price and volume fields; spans such as `4h` parsed exactly. (Time zones
      and sessions moved to Phase 4 with the session calendar.)
- [x] `feat(core)`: `Settled` marks the end of each instant (added: one
      snapshot per timestamp).
- [x] `feat(core)`: time bars from points, closed by alarms; late points
      counted. (Building bars from finer bars, and refusing smaller-from-larger,
      waits for a consumer that needs it; `BarSpec.can_be_built_from` is ready.)
- [x] `feat(core)`: sequence numbers on the bus and a `Journal` port; the
      bus journals each message before delivery, in every mode.
- [x] `feat(plugins)`: SQLite journal (msgspec records) and replay. (Building
      it from run configuration needs the header and bus wiring in the app;
      that comes with the mount table in batch C.)
Testing batch, before the data sources (D23; [testing.md](testing.md)). It
came first because batch C adds asynchronous adapters and a live exchange,
which are hard to test well once built:

- [x] `test`: shared builders in `tests/support`.
- [x] `build`: random test order (pytest-randomly), a 90% branch-coverage
      floor per file, and CI failing if the checks change the working tree.
- [x] `test`: cross-process determinism: fresh interpreters with different
      `PYTHONHASHSEED` and `TZ` write identical journal entries and headers,
      and sensitivity tests show the harness sees output that follows the
      hash seed or the time zone.
- [x] `test`: suite rules in `tests/meta`, each with a control.
- [x] `build`: `scripts/check_controls.py` and `tests/controls.toml` (52
      controls), with a CI job.
- [x] `build`: `scripts/mutation_test.py`; added (not planned) a
      `refactor(core)` removing two unreachable branches in `TimeBars`,
      and a `test` commit closing the gaps found. What survives is wording,
      falsy equivalents, overwritten start values and the alarm heap's
      clean-up threshold.
- [x] `test`: a Hypothesis state machine checking the engine and alarm
      queue against a slow reference.

Batch C:

- [x] `feat(core)`: `DataSource` and `InstrumentProvider` ports (async
      protocols) with declared capabilities; `SourceUnavailableError`.
- [x] `feat(app)`: the asyncio bridge that feeds adapters into the
      synchronous bus in timestamp order, stamping when data became known
      (D24).
- [x] `feat(app)`: a throttle enforcing each source's declared rate limit
      and concurrency, bound to the source (`DataSource.bind`). Added (not
      planned): `check_controls.py` reports a control whose tests hang, and
      kills what they started.
- [x] `feat(app)`: mount table in the run configuration, checked against
      capabilities and terms flags.
- [x] `feat(plugins)`: CSV and Parquet source; static instrument provider.
- [x] `feat(plugins)`: raw, clean and published store with quarantine
      (D25), and the `store` source that reads a snapshot. Added (not
      planned): a `refactor(plugins)` sharing the batch reader, and
      `feat(app)`: downloading a backtest's mounts into the store and
      publishing them.
- [x] `feat(plugins)`: CCXT source, history and live (D21); the exchange is
      a parameter of the mount. Offline tests on pages written in CCXT's
      format; `tests/live` checks Kraken itself (`uv run pytest -m live`).

To finish the phase:

- [x] `feat(app)`: a backtest run assembled from its configuration (mounts,
      bridge, engine, journal), its journal header recording the
      configuration's fingerprint and the snapshot it read, and replayable
      from the journal alone (D26). Added (not planned): `[[streams]]` in
      the run configuration, the bars a run builds.
- [ ] The live tests run once against Kraken from the owner's machine; the
      offline pages are corrected wherever Kraken answers otherwise.

**Gate:** a 4h value ending at T is visible to the 1h consumer at T and never
earlier, in a test (passing: `tests/core/test_bars.py`); replaying a journal
reproduces the run byte for byte (passing:
`tests/plugins/test_sqlite_journal.py`), and in a fresh process with a
different hash seed and time zone (testing batch); a published snapshot
rebuilds exactly from its raw files (passing: `tests/plugins/test_store.py`,
and across processes in `tests/determinism`).

The first market is crypto through CCXT (D21). The engine runs as its own
process (D18).

## Phase 2 · Backtest engine and strategy pipeline

In four batches: D1 the indicator graph and indicators in runs; D2 the
conformance kits; D3 indicators on TA-Lib and talipp; D4 the pipeline, the
example strategy and the gate, in three parts: D4a the pipeline, D4b
simulated fills, costs and a report, D4c the example strategy, scenario
tests and the gate. Then D5: streams on mounts of bars, for an exchange's
candles.

- [x] Indicator graph with stabilisation per timestamp and cutoffs (D27);
      indicators in a run from configuration, journalled, recorded in the
      header and replayed; first indicators `sma` and `ema` (D1). Graph
      observers, which strategies are, came in D4a; skipping nodes nothing
      reads waits until it is measured to matter.
- [x] Conformance kits (no peeking, edge cases, reference values, manifest)
      run on every registered plug-in by default; each check `error`,
      `todo`, `off` or `replaced` with a reason; a broken sample plug-in per
      check; a softened-checks report; `heliograph.testkit` for outside
      plug-ins ([testing.md](testing.md)) (D2). The adapters' own checks
      wait for a scripted source per adapter.
- [x] Scenario tests after LEAN's regression algorithms: a backtest worked
      out by hand and the example strategy, their entry counts and reports
      compared as text with inline-snapshot (D4c).
- [x] Indicator nodes wrapping TA-Lib streams and talipp (D29); a first
      set: `rsi`, `atr`, `macd`, `bbands`, `stddev` on TA-Lib, `hma` and
      `alma` on talipp, `zlema` in plain Python (D3).
- [x] Streams on mounts of bars: a mount's own bars, and bars built from
      finer bars (`BarsFromBars`), so strategies run on an exchange's
      candles (D33, D5). Carrying gaps in bars built from bars waits until a
      strategy needs it.
- Fixes from the review of everything built (2026-10-23), in three
  batches: F1 signals (D34) done; F2 bars and timing; F3 sources and the
  store.
- Open, from the review of F1: a doubted price reaches the graph's nodes
  (a forecaster, an ATR a stop reads) before any risk gate can doubt it;
  a sanity filter on bars in front of the graph would keep it out.
- Open, found in D5's review: late points and late bars are counted but
  not reported or journalled; the check that a graph node's inputs share a
  grace takes grace for the time a bar closes, which a bar built from bars
  does not (a pass-through 15m and a built 1h with grace 5m close together
  yet are refused); CCXT names Kraken's 15-day candles `2w`, which reads as
  14 days.
- Session calendars (exchange_calendars) for local days and market hours:
  moved to Phase 4, where Alpaca first needs them.
- [x] Pipeline ports: forecaster, gate, combiner, sizer, exit, risk gate;
      the `Signal` and `SignalRefused` records; strategies in a run from
      configuration, journalled and replayed; a first plug-in per stage
      (D30, D4a). The signal's lifecycle (taken, skipped, expired) waits for
      paper trading in Phase 4.
- [x] Simulated fills at the next bar's open, a cost model (`next_open`:
      spread, slippage, commission), the position held from fills, and a
      text report computed from the journal (D31, D4b).
- [x] One example strategy (`examples/trend.toml`) on made-up data, and
      the `heliograph` command to run, report and replay it (D32, D4c).
- [x] `heliograph.sdk` first cut: the names a plug-in imports (D32, D4c).

**Gate:** a snapshot test matches a hand-checked backtest exactly, and the
conformance kit fails on a deliberately peeking indicator. Met: the
hand-checked scenario (D4c), and the kit's broken sample plug-ins (D2).

## Phase 3 · Graphical workspace

- First: the engine process and its JSON API (D18): supervisable (arguments
  or environment for port, data and config; `127.0.0.1`; SIGTERM; health;
  stderr logs; one worker), times as ISO strings (D19), parameter schemas
  served from `model_json_schema()`. Tested outside-in: the process started
  as a person would start it, by tests that import nothing from
  `heliograph`.
- Decide how the screens could appear inside Elysium (D20), once Elysium's
  module bundles (its step 1.2) exist.

- NiceGUI window; Lightweight Charts element (candles, lines, markers, panes,
  steps); series labelled by stream.
- Indicator catalogue with settings panels generated from parameter schemas.
- Single-run Strategy Tester with report and equity view.
- Wizard first cut on Vue Flow.

**Gate:** a new indicator plug-in appears in the GUI, configurable, with no GUI
code written for it.

## Phase 4 · Signals and paper trading

- Sinks: journal, ntfy, desktop, webhook, Apprise; dispatcher with retries.
- Optional: a published-records sink writing signals, simulated fills and
  daily equity to a separate SQLite file that Elysium reads as a silo (D20).
- The second live source: Alpaca, for US stocks (D21).
- Session calendars (exchange_calendars) for local days and market hours,
  which Alpaca's stocks need (moved from Phase 2).
- Paper broker on live data; took-it/skipped journal.
- Risk gate and `Halted` state live; signal-rate and staleness alerts.

- From the review of 2026-10-23, to settle before live data: a live clock
  that repeats a timestamp must not end the run (the engine treats a
  settled instant as final); SIGTERM must flush and close the journal;
  live alarms must fire and settle before each input, as the backtest
  engine does, so a live day replays; Kraken's websocket replays its last
  50 trades on each subscribe, trade ids are dropped, and nothing
  reconnects; a person's took-it or skipped answer must reach the
  strategy's position held; stage objects keep state, so optimisation
  must build them afresh per run (Phase 5).

**Gate:** a paper session survives a restart with positions and clock intact,
and a replayed day reproduces its signals.

## Phase 5 · Research tools

Grid and Optuna optimisation, forward split, walk-forward with purge and
embargo, deflated Sharpe, probability of backtest overfitting, Monte Carlo,
visual replay, heatmaps and surfaces, trial registry, sealed holdout.

**Gate:** every optimisation winner shows its deflated Sharpe and overfitting
probability.

## Phase 6 · Advanced plug-ins

Incremental Kalman trend, HAR volatility, regime models (HMM, jump models),
online change-point detection, Ornstein–Uhlenbeck exits, triple barrier,
meta-labelling sizer, wavelet denoising (causal only).

## Later, not scheduled

Configuration fingerprint over canonical JSON rather than raw text, once a
tool rather than a person writes configuration files.

Web version for several users (FastAPI underneath NiceGUI; Point72's
csp-gateway as a reference); a real broker behind the risk gate (ib_async or
Alpaca live); Databento history; FRED macro context; out-of-process plug-ins.
