# Libraries and trading-firm open source

The chosen libraries and the avoid list are in [../licensing.md](../licensing.md).
This file keeps the evaluation behind them: what each candidate does, why it
won or lost, and the open-source work of trading firms.

Researched 2026-10-21 from PyPI and npm metadata and each project's LICENSE.
Current versions of the chosen libraries are kept only in licensing.md.

## Indicators

- **TA-Lib** (BSD-2; C library BSD-style): new streaming API,
  `stream.SMA(history)` then `update(bar)` in O(1), `peek(forming)` without
  committing, `copy()` to fork; values match the batch functions exactly.
  Handles cannot be pickled, so snapshots re-open from history. Releases the
  GIL. Unverified: whether the wheels bundle the C library.
- **talipp** (MIT): pure Python; append, update the last value, remove;
  chainable.
- Runners-up: hexital 4.1 (MIT, incremental), pandas-ta-classic 0.8 (MIT,
  batch), polars-talib 0.2 (MIT), wickra 2.0 (Rust, new, not evaluated).

## Streams and incremental computation

- **Point72 csp** (Apache-2.0): C++ engine with typed `@csp.node` and
  `@csp.graph`; one graph for simulation and realtime (realtime simulates from
  start to now, then switches to the wall clock); push modes for same-time
  ticks; timers on simulated or wall time; history buffers; Parquet and Kafka
  adapters. It replaces a bus, clock and engine rather than plugging in;
  requires pandas, `pyarrow<24`, SQLAlchemy and others. Its node ordering and
  determinism guarantees were not confirmed in the docs.
- reactivex 6.0 (MIT): push operators without glitch-free stabilisation; I/O
  plumbing at most.
- streamz (low activity), tributary (stale since 2023), bytewax (company wound
  down), pathway (BSL).
- No maintained Python library implements Incremental-style self-adjusting
  computation. The PyPI package `incremental` is Twisted's versioning tool.

## Filters and statistics

statsmodels (state-space Kalman, Markov switching), pykalman (revived),
hmmlearn (no Python 3.14 wheel), ruptures (requires Python below 3.14),
PyWavelets, arch (GARCH, block bootstrap), river (online learning). Plan:
write small incremental Kalman and online change-point nodes ourselves and use
these libraries as batch oracles in tests.

## Research and reports

optuna (5.0 was a major release), empyrical-reloaded and pyfolio-reloaded (both
released 2026-10-17), quantstats 0.0.86, ffn. Walk-forward with purge and
embargo, deflated Sharpe and Monte Carlo resampling are written by us.
zipline-reloaded, bt and hftbacktest are useful as oracles, not dependencies.

## GUI

- NiceGUI bundles ag-grid-community ^34.2, echarts ^6.0 with echarts-gl,
  plotly.js ^3.1, CodeMirror 6, vanilla-jsoneditor with ajv, and `ui.anywidget`
  for Jupyter widgets. `ui.run(native=True)` opens a pywebview window; the
  default mode is a FastAPI server. Packaging with `nicegui-pack` or Nuitka.
- Every interaction is a websocket round-trip; keep chart interaction
  client-side and the engine off the UI's event loop.
- Highcharts moved to a separate `nicegui-highcharts` package "due to
  Highcharts' restrictive license".
- Lightweight Charts 5: multi-pane, pane primitives, modular markers,
  about 35 kB. The `lightweight-charts` Python wrapper (MIT) has not released
  since 2024-09 and opens its own window; we write our own element.
- KLineChart (Apache-2.0) has built-in drawing tools: the fallback.
- Vue Flow (MIT) for the Wizard; Rete.js v2 and BaklavaJS as
  alternatives; litegraph.js original is stale.
- Perspective (Apache-2.0) joined the OpenJS Foundation (announced
  2025-10-30); repository `perspective-dev/perspective`; maintainers include
  its founder (The Prospective Company) and a Point72-linked co-maintainer.
- Rejected shells: Streamlit, Dash, Flet 1.0, PySide6/PyQt6; Reflex 0.10 is
  the credible web alternative; Textual 8 for an optional terminal console.

## Trading-firm open source

| Project | Licence | Use |
| --- | --- | --- |
| Jane Street Incremental, Bonsai | MIT | Ideas (OCaml) |
| Jane Street pythonlib, magic-trace, torch, Hardcaml | MIT | Not relevant |
| Point72 csp | Apache-2.0 | Design reference; optional engine adapter |
| Point72 csp-gateway 3.0 | Apache-2.0 | Reference for a web version: FastAPI, WebSocket, Perspective UI |
| Point72 ccflow 0.9 | Apache-2.0 | Candidate for Wizard configuration (pydantic composable configs) |
| Bloomberg memray, pystack | Apache-2.0 | Development tools |
| Bloomberg bqplot, ipydatagrid | Apache-2.0, BSD-3 | Jupyter-centric; not needed |
| Man Group ArcticDB | BSL 1.1 | Avoid; licence file and FAQ disagree on business use |
| Man Group dtale, arctic | LGPL-2.1 | Development only, if at all |
| Two Sigma flint, beakerx, marbles | Apache-2.0, MIT | Dead or minor |
| Goldman Sachs gs-quant | Apache-2.0 | Needs Marquee access; ideas only |

Citadel, Optiver, IMC and HRT were not researched.

## Sources

- https://pypi.org (JSON API per package) and https://registry.npmjs.org
- https://github.com/Point72/csp/wiki
- https://github.com/Point72/csp/wiki/Execution-Modes
- https://point72.com/blog/point72-launches-its-first-open-source-library/
- https://openjsf.org/blog/perspective-joins-openjs
- https://www.tradingview.com/blog/en/tradingview-lightweight-charts-version-5-50837
- https://www.tradingview.com/free-charting-libraries/
- https://nicegui.io/documentation/section_configuration_deployment
- https://pypi.org/project/nicegui-highcharts/
- https://docs.arcticdb.io/dev/licensing/
- https://www.quantconnect.com/forum/discussion/16833/mlfinlab-where-is-the-hudson-amp-thames-lean-engine/
