# MetaTrader 5 Strategy Tester

Bottom line: MT5's tester is the model for discoverability, settings,
optimisation and reports; Heliograph copies its task-based layout and adds
rolling walk-forward, overfitting-aware metrics and size-dependent costs.

Researched 2026-10-20; reviewed 2026-10-20.

## Start page

- Opens on a list of common testing tasks, not a wall of settings.
- Picking a task hides every setting that task does not need.
- Past tasks can be rerun and searched by any parameter: program, symbol,
  timeframe, modelling mode.
- This is the single best idea to copy for discoverability.

## Test settings

| Setting | What MT5 offers |
| --- | --- |
| Symbol and timeframe | Auto-loads extra history before the start date for indicator warm-up |
| Tick generation mode | Every tick (simulated); every tick based on real ticks; 1-minute OHLC; open prices only; math calculations |
| Execution delay | None, random, or fixed (set to measured broker ping) |
| Forward split | Last 1/2, 1/3, 1/4 or a custom period held out as a forward test |
| Account | Deposit, currency, leverage |
| Advanced | Trading limits, margin rules, blocked sessions, commission schedules |
| Instrument overrides | Override most of the symbol spec without a custom symbol |
| Presets | Save to file, share, or copy and paste as text |

- Commissions can be per lot or per deal, tiered by volume, charged instantly,
  daily or monthly.
- "Math calculations" uses no market data; it is pure parameter search.

## Optimisation

- Three search modes: complete search, genetic search, same parameters across
  every selected symbol.
- Each parameter has start, step and stop. Total combinations show before the
  run.
- Ranking criteria: balance, profit factor, expected payoff, recovery factor,
  Sharpe, a built-in complex criterion, a custom criterion.
- The custom criterion is computed by the strategy itself.

Results table:

- Sortable by any column. Double-click a row to rerun it as a full single test.
- Filters hide passes with no trades, losses, drawdown over 50%, recovery
  factor under 1, or Sharpe under 0.5.
- Colour-coded cells.

Other features:

- Forward checking retests the best 10% (complete search) or 25% (genetic) on
  the forward period automatically.
- Finished passes are cached: stop and resume, revisit, and re-rank by another
  criterion later.
- Graphs: 1D criterion vs one parameter; 2D heatmap over two parameters;
  rotatable 3D surface.
- Parallel runs on local cores, remote machines, or a paid cloud network.

## Report

| Group | Contents |
| --- | --- |
| Data quality | History-quality score |
| Profit and loss | Net, gross profit, gross loss, profit factor, recovery factor, expected payoff |
| Drawdown | Balance and equity drawdown, each absolute, maximal and relative |
| Return quality | Arithmetic and geometric mean return per trade; Sharpe; balance-curve linearity; Z-score for streak clustering |
| Trade statistics | Win/loss streaks, holding times, MFE and MAE correlation with result |
| Charts | Entries and profit by hour, weekday, month; MFE/MAE scatter; holding time vs profit; balance and equity curves |

## Visual mode

- Replays the test like a live chart: pause, speed up, slow down, jump to date.
- Every trade is drawn on the chart.
- Shows a simulated Market Watch, data window and trade history.

## What Heliograph copies

| MT5 feature | Heliograph equivalent |
| --- | --- |
| Task-based start page, searchable history | Task list; searchable run registry |
| Tick generation modes | Fill-model fidelity: bar-open only for scans up to intrabar or tick replay |
| Execution delay | Signal-to-fill delay in the simulated broker |
| Forward split | Forward split setting |
| Commission schedules, spec overrides | Cost model and instrument overrides in run config |
| Saved presets | Run configuration files |
| Complete and genetic search | Grid and Optuna search |
| Results table, filters, rerun-on-click | Same, with cached trials |
| 1D/2D/3D optimisation graphs | Plotly heatmaps and surfaces |
| Report metrics and charts | Same groups in the text and GUI report |
| Visual mode | Visual replay on Lightweight Charts |

## What Heliograph adds

- **Rolling walk-forward.** MT5 has only one forward split. Repeat
  optimise-then-test across rolling windows.
- Report Walk-Forward Efficiency: out-of-sample versus in-sample return.
- Use purged splits so training and test data never overlap in time.
- **Overfitting-aware metrics.** Show deflated Sharpe and probability of
  backtest overfitting for every optimisation.
- MT5's table encourages picking the top row. Heliograph should warn against
  it.
- **Size-dependent costs.** Slippage and fills scale with order size relative
  to volume. Critical for small-cap niches.
- **Modern optimiser.** Optuna's TPE sampler instead of a basic genetic
  algorithm.
- Optuna studies can be multi-objective (e.g. out-of-sample Sharpe vs
  Walk-Forward Efficiency).
- Caution: multi-objective search raises overfitting risk, so governance still
  applies.
- **Trial registry and sealed holdout.** Log every variation tried; the holdout
  is looked at once.

## Lessons from other engines

- QuantConnect LEAN splits strategies into Universe, Alpha, Portfolio
  Construction, Risk Management and Execution modules.
- LEAN's Alpha emits Insights; Portfolio Construction turns them into
  PortfolioTargets for Risk and Execution.
- LEAN modules do not talk to each other, so signals are identical under any
  sizing or execution model.
- NautilusTrader: event-driven, one message bus, swappable clock, adapters at
  the edges.
- The same Nautilus strategy runs in backtest and live with no code changes.

## GUI notes

- NiceGUI suits a desktop-like workbench and can run as a native window.
- Streamlit reruns the whole script per interaction; weaker for many panels and
  live state.
- Dash is fast and mature but needs more code. PySide6/Qt was not researched
  deeply.
- TradingView Lightweight Charts for candles and trade markers; Plotly for
  heatmaps and 3D surfaces.

## Implications for Heliograph

- Copy the task start page, task-dependent settings, sortable results and
  visual replay; add walk-forward, deflated Sharpe, PBO, a trial registry and a
  sealed holdout. What is built when: [roadmap.md](../roadmap.md).
- Fill-model fidelity is a run setting; final checks use the highest fidelity
  available.
- The custom criterion maps to a plug-in metric computed from the run record.

## Sources

- https://www.metatrader5.com/en/terminal/help/algotrading/testing
- https://www.metatrader5.com/en/terminal/help/algotrading/strategy_optimization
- https://www.metatrader5.com/en/terminal/help/algotrading/optimization_types
- https://www.metatrader5.com/en/terminal/help/algotrading/testing_report
- https://www.metatrader5.com/en/automated-trading/strategy-tester
- https://www.mql5.com/en/blogs/post/772184
- https://quantammsim.readthedocs.io/en/latest/tutorials/hyperparameter_tuning.html
- https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview
- https://docs.nautilustrader.io/concepts/overview.html
- https://pypi.org/project/nautilus-trader/
- https://www.marketcalls.in/python/introduction-to-nautilus-trader-part-1-architecture-backtesting-openalgo.html
- https://www.bitdoze.com/md/streamlit-vs-nicegui.md
- https://lup.lub.lu.se/student-papers/search/publication/9226195
- https://pypi.org/project/violit/0.6.11/
- https://pypi.org/project/streamlit-lightweight-charts/
- https://www.Github.com/tysonwu/dash-tradingview
- https://papers.ssrn.com/abstract=2460551
- https://papers.ssrn.com/abstract=2326253
