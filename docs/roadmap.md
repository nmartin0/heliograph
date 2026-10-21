# Roadmap

Seven phases ordered by dependency, not by date. Each ends with a gate: a
test or demonstration that must pass before the next phase starts, and a tagged
release. Heliograph becomes a usable signal provider at the end of Phase 4.

| Phase | Theme | Status |
| --- | --- | --- |
| 0 | Foundations | done; tag `v0.1.0` pending a green CI run |
| 1 | Data | next |
| 2 | Backtest engine and strategy pipeline | planned |
| 3 | Graphical workspace | planned |
| 4 | Signals and paper trading | planned |
| 5 | Research tools | planned |
| 6 | Advanced plug-ins | planned |

## Phase 0 · Foundations (done)

Repository and tooling; UTC nanosecond time; `Instrument` and `Bar`; simulated
and wall clocks; the time-guarded message bus; the backtest event loop;
plug-in manifests, parameter schemas and registry; run configuration and
composition root; layering checks; one error hierarchy; the eight defects
found by the Elysium team's review fixed. 128 tests.

**Gate (passed locally):** layer contracts and the no-look-ahead property test
pass on every commit. **To close:** CI green on GitHub, then tag `v0.1.0`.

## Phase 1 · Data

**Goal:** real data flows from a source through clean storage into streams at
any bar size, closed on time, recorded in a replayable journal.

Planned commits, in order:

- [ ] `build`: copyright header on every file we own, checked in CI
      (`third_party/` exempt).
- [ ] `build`: licence report of runtime dependencies (pip-licenses) committed
      and checked; AGPL and source-available fail without an approved exception.
- [ ] `feat(core)`: clock alarms (`at`, `every`) fired in time order by the
      engine between feed messages.
- [ ] `feat(core)`: `Point` with `received_at`; `Bar` gains `received_at`.
- [ ] `feat(core)`: stream specs: bar size, anchor, time zone, session, gap
      policy, price field.
- [ ] `feat(core)`: time-bar resampler closed by clock alarms; refuses
      smaller-from-larger.
- [ ] `feat(core)`: sequence numbers on the bus and a `Journal` port; the
      bus journals each message before delivery, in every mode.
- [ ] `feat(plugins)`: SQLite journal (msgspec records) and replay.
- [ ] `feat(core)`: `DataSource` and `InstrumentProvider` ports (async
      protocols) with declared capabilities; `SourceUnavailable`.
- [ ] `feat(app)`: the asyncio bridge that feeds adapters into the
      synchronous bus in timestamp order.
- [ ] `feat(app)`: mount table in the run configuration, checked against
      capabilities and terms flags; adapter rate-limit wrapper.
- [ ] `feat(plugins)`: CSV and Parquet source; static instrument provider.
- [ ] `feat(plugins)`: raw, clean and published store with quarantine.
- [ ] `feat(plugins)`: the first live source, CCXT or Alpaca, per the
      first-market decision below.

**Gate:** a 4h value ending at T is visible to the 1h consumer at T and never
earlier, in a test; replaying a journal reproduces the run byte for byte; a
published snapshot rebuilds exactly from its raw files.

**Decide during the phase:** first market (crypto or US stocks). The engine
runs as its own process (D18).

## Phase 2 · Backtest engine and strategy pipeline

- Indicator graph with stabilisation per timestamp, cutoffs and observers.
- Conformance kit (no peeking, edge cases, reference values, manifest).
- Indicator nodes wrapping TA-Lib streams and talipp; a first set of
  indicators.
- Pipeline ports: forecaster, gate, combiner, sizer, exit, risk gate; the
  `Signal` record and its lifecycle types.
- One example strategy; simulated fills and cost model; a text report.
- `heliograph.sdk` first cut.

**Gate:** a snapshot test matches a hand-checked backtest exactly, and the
conformance kit fails on a deliberately peeking indicator.

## Phase 3 · Graphical workspace

- First: the engine process and its JSON API (D18): supervisable (arguments
  or environment for port, data and config; `127.0.0.1`; SIGTERM; health;
  stderr logs; one worker), times as ISO strings (D19), parameter schemas
  served from `model_json_schema()`.
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
- The second live source (Alpaca or CCXT, whichever Phase 1 did not build).
- Paper broker on live data; took-it/skipped journal.
- Risk gate and `Halted` state live; signal-rate and staleness alerts.

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
