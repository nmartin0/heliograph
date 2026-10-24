# Licensing

Heliograph is proprietary (all rights reserved, Nicholas Martin). The rule that
protects that is about where code comes from, not which libraries we install.

## Policy

- **Our tree is ours.** Everything in this repository (`src/`, `tests/`,
  `scripts/`, templates, configs, docs) is written by us and carries our
  copyright. Nothing copyleft is copied, vendored, adapted or translated in,
  not even a short function.
- **Copied permissive code** (MIT, BSD, Apache-2.0, ISC, PSF, Zlib, 0BSD, CC0,
  Unlicense) is allowed, but keeps its original notice: it goes in
  `third_party/` with its licence file and a line in
  `THIRD_PARTY_NOTICES.md`, and can sit inside a proprietary product.
- **Libraries may be copyleft** (GPL, LGPL, MPL, EPL, CDDL) when used as
  unmodified, separately installed dependencies. A library we need to change
  is forked as its own package under its own licence, never folded into our
  tree.
- **AGPL is avoided.** If one is ever needed, it runs as-is as a separate tool
  or service that is never shipped or bundled with Heliograph.
- **Source-available licences are avoided** (Business Source License, SPDX
  `BUSL`; Commons Clause; SSPL; Elastic). Only something critical is
  considered, case by case, with the decision and its restrictions written in
  [decisions.md](decisions.md).
- **Ideas are free.** Reading GPL code (pysystemtrade, Freqtrade, Lumibot,
  NautilusTrader) and implementing the method in our own words is fine; copying
  or translating it line by line is not.
- **Distribution is a future decision.** If Heliograph is ever shipped to or
  hosted for others, copyleft dependencies carry obligations of their own; the
  licence report shows which.
- **Data has terms too.** Each data source's terms go into its adapter's
  manifest as flags (personal only, non-commercial, no live trading,
  attribution) and are enforced when a run is composed.

## Checks

- `scripts/licence_report.py` (run by `./scripts/check.sh`) walks every
  runtime dependency in `uv.lock` on every platform, reads its licence from
  package metadata, and generates the table in `THIRD_PARTY_NOTICES.md`; the
  check fails when the table is out of date, so a new copyleft dependency
  appears as a reviewed diff marked `copyleft`. An SPDX expression takes the
  best of its `OR` choices and the worst of its `AND` parts, so a package
  offered as "MIT OR AGPL-3.0-only" is used under MIT. AGPL, source-available,
  proprietary and unidentifiable licences fail unless listed with a reason
  under `[tool.heliograph.licences.exceptions]` in `pyproject.toml`; wrong,
  empty or not-installed-here metadata is corrected under `...overrides`
  (TA-Lib's licence field is blank; arctic says GPL on PyPI but is LGPL-2.1).
  Entries for packages no longer depended on fail too, so approvals do not
  outlive their reason. We wrote this instead of depending on pip-licenses
  because it needs the runtime set only and our policy's verdicts.
- Every Python and shell file under `src/`, `tests/` and `scripts/` starts
  with the two-line SPDX header (`SPDX-FileCopyrightText: 2026 Nicholas
  Martin`, `SPDX-License-Identifier: LicenseRef-Proprietary`), after any
  shebang; `tests/test_headers.py` enforces it. TypeScript joins the list when
  the GUI arrives. Files under `third_party/` keep their own notices. The
  LICENSE file covers everything else in the repository.

## Library choices

Checked on PyPI, npm and each project's LICENSE file on 2026-10-21. Versions
decay; re-check before adding.

| Area | Use | Licence |
| --- | --- | --- |
| Parameters, configuration | pydantic 2.14 | MIT |
| Journal encoding | msgspec 0.22 | BSD-3 |
| Hooks (if needed) | pluggy 1.7 | MIT |
| Data frames, queries, files | polars 2.0, duckdb 2.0, pyarrow 26 | MIT, MIT, Apache-2.0 |
| Ingest checks | pandera 0.34 | MIT |
| Market calendars | exchange_calendars 4.13, pandas_market_calendars 5.5 | Apache-2.0, MIT |
| Indicators | TA-Lib 0.8.2 (wrapper and C library), talipp 2.7 | BSD-2, BSD-style, MIT |
| Statistics, filters | statsmodels 0.15, pykalman 0.11, hmmlearn 0.3, ruptures 1.1, PyWavelets 1.10, arch 8.0, scipy, scikit-learn, river 0.26 | BSD family, MIT, NCSA |
| Fast kernels | numba 0.68 | BSD |
| Optimisation | optuna 5.0 | MIT |
| Metrics, reports | empyrical-reloaded 0.7, pyfolio-reloaded 0.10, quantstats 0.0.86, Great Tables 1.1, Jinja2, WeasyPrint 70 | Apache-2.0, MIT, BSD |
| Scheduling, resilience | APScheduler 3.11, tenacity 9.2, aiolimiter 1.3, websockets 17 | MIT, Apache-2.0, MIT, BSD-3 |
| HTTP | httpx2 2.16 (httpx has not released since 2024-12) | BSD-3 |
| Engine API server | Starlette 1.7, uvicorn 0.54 (what NiceGUI runs on) | BSD-3 |
| Logging, paths, secrets | structlog 26, platformdirs 4.13, keyring 25.7 | MIT or Apache-2.0, MIT, MIT |
| Data and broker clients | ccxt 4.5, alpaca-py 0.44, ib_async 2.1, databento 0.87, massive 2.9, twelvedata 1.4, fredapi 0.5 | MIT, Apache-2.0, BSD-2, Apache-2.0, MIT, MIT, Apache-2.0 |
| Notifications | apprise 2.0, desktop-notifier 6.2; ntfy over HTTP | BSD-2, MIT |
| GUI shell | NiceGUI 3.18, pywebview 6.2 | MIT, BSD-3 |
| Price charts | Lightweight Charts 5.2.1 (attribution: NOTICE text and a tradingview.com link) | Apache-2.0 |
| Other charts, grids | ECharts 6.1 + echarts-gl, uPlot 1.6, Plotly, AG Grid Community, Perspective 5.6 | Apache-2.0, MIT, MIT, MIT, Apache-2.0 |
| Wizard, forms, editor | Vue Flow 1.48, JSON Forms 3.8, CodeMirror 6 | MIT |
| Tests | hypothesis, inline-snapshot, syrupy, pytest-randomly | MPL-2.0, MIT, MIT, MIT |
| Type stubs | pyarrow-stubs 20 | BSD-2 |

## Avoid

| Library | Reason |
| --- | --- |
| vectorbt | Commons Clause (source-available) |
| ArcticDB, pathway | BSL (source-available) |
| Highcharts, `nicegui[highcharts]`, TradingView Advanced Charts | Proprietary |
| AG Grid Enterprise modules | Paid licence |
| mlfinlab | Proprietary, removed from PyPI |
| bytewax | Company wound down; no Python 3.13 wheel |
| pandas-ta (original) | Repository gone; use pandas-ta-classic if ever needed |
| backtrader, tulipy, finta, filterpy, arctic | Unmaintained |
| yfinance (outside development) | Yahoo's terms prohibit automated collection |
| Binance Vision data (for live use) | Terms allow personal backtesting only |
| PyPI `ntfy` package | Unrelated GPL tool; use the ntfy.sh HTTP API |
