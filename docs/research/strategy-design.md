# How trading robots are structured

There is no formal standard, but the reference literature and the major
frameworks agree on one decomposition: data, forecast, sizing, risk, execution.
MQL5's Expert framework is a loose version with specific flaws; Carver's
forecast pipeline is the principled version Heliograph adopts (decision D8).

Researched 2026-10-21.

## The common decomposition

| Stage | Narang, *Inside the Black Box* | QuantConnect LEAN | Carver, pysystemtrade |
| --- | --- | --- | --- |
| What to trade | Data (ch. 8) | Universe Selection | `rawdata` |
| Direction and strength | Alpha models (ch. 3) | Alpha models emit `Insight` | `rules`, `forecastScaleCap`, `combForecast` |
| How much | Portfolio construction (ch. 6), using risk (ch. 4) and cost (ch. 5) models | Portfolio Construction emits `PortfolioTarget` | `positionSize`, `portfolio` |
| What is allowed | Risk models | Risk Management models, chained | inside sizing (vol targeting) |
| How to trade | Execution (ch. 7) | Execution model | outside the backtester |

NautilusTrader is the reference for the engine underneath: single-threaded
kernel (MessageBus, Cache, DataEngine, ExecutionEngine, RiskEngine, Portfolio),
venue adapters (DataClient, ExecutionClient), and Backtest, Sandbox and Live
environments on the same code. Orders pass a RiskEngine that emits
`OrderDenied` on a failed check.

## LEAN specifics (from source)

- `Insight`: Symbol, Type (Price or Volatility), Direction (Up 1, Flat 0,
  Down −1), Period, optional Magnitude, Confidence, Weight; plus generated and
  close times, source model, score.
- `PortfolioConstructionModel.GetTargetInsights` keeps only the newest insight
  per symbol: several alpha models do not combine by default.
- Risk models chain, each adjusting the previous targets.
- The docs say the framework "works best for portfolio rebalancing, ranking,
  and multi-factor style strategies"; other styles use a hybrid escape hatch.

## MQL5 Expert framework (from `ExpertSignal.mqh` and `Expert.mqh`)

- Defaults: weight 1.0, `ThresholdOpen` 50, `ThresholdClose` 100, up to 64
  filters, all patterns on.
- `Direction()` starts from `weight × (LongCondition − ShortCondition)`, adds or
  subtracts (invert bit) each non-ignored filter's direction, counts every
  non-ignored filter even when it returns 0, and divides by the count. Any
  filter returning `EMPTY_VALUE` vetoes the signal.
- Opens long when direction ≥ ThresholdOpen; closes long only when
  −direction ≥ ThresholdClose.
- `CExpert::Processing()`: compute direction; with a position, check reverse,
  close, then trailing; manage pending orders; check open.
- Price modules decide entry levels separately from direction; highest weight
  wins.

**Flaws:** silence dilutes (one module at 100 and two silent gives 33, below
50); scores are hand-picked and not comparable between modules; thresholds
discard strength and a weakening signal never trims; no portfolio or
volatility awareness. **Worth keeping:** the veto, the pattern bitmask, the
invert flag, direction separate from entry price, and the Wizard.

## Carver's forecast pipeline (pysystemtrade defaults and docs)

- Each rule outputs a continuous forecast, multiplied by a forecast scalar so
  the average absolute value is 10, capped at ±20 (`forecast_cap: 20.0`).
  Scalars can be estimated.
- Combination: weighted sum × forecast diversification multiplier,
  FDM = 1/√(W·H·Wᵀ) with H the correlation matrix, capped at 2.5 (`dm_max`),
  then capped again.
- Sizing from a volatility target (`percentage_vol_target: 16`) × instrument
  weight × instrument diversification multiplier × forecast/10.
- A no-trade buffer (`buffer_size 0.10`) replaces entry and exit thresholds.
- An optional `forecast_mapping` adds a dead zone while keeping the average
  absolute forecast unchanged.
- pysystemtrade is GPL-3: reimplement from the books, never copy code.

## López de Prado, *Advances in Financial ML*

- §3.5–3.6: learn side and size separately; **meta-labelling** uses a primary
  model for side and a secondary classifier for whether the bet pays off.
- Ch. 10 bet sizing: size m in [−1, 1] from predicted probabilities; averaging
  active bets; size discretisation to prevent overtrading. The exact formula is
  behind the paywall and unverified here.

## Other frameworks

| Framework | Shape | Lesson |
| --- | --- | --- |
| Freqtrade | Vectorised `populate_indicators`, `populate_entry_trend`, `populate_exit_trend`; signals at candle close, fills at next open | Its docs list look-ahead traps: `shift(-1)`, `.iloc[-1]`, whole-column `.mean()`, `resample` without `label='right'`, plain `merge` |
| Backtrader | `next()` with `[0]` current, `[-1]` previous; separate Sizers | Swapping a sizer changes long-only to long-short without touching signals |
| Zipline Pipeline | Factor (number), Filter (boolean), Classifier, computed daily | `order_target_percent` ignores open orders, so calls stack |
| Hummingbot V2 | Controllers decide, Executors manage orders; `PositionExecutor` triple barrier | Exits as their own component |
| StrategyQuant | Random or genetic assembly of building blocks | Data-snooping risk by design |
| NinjaTrader | `OnBarUpdate`, `Calculate` mode, `BarsInProgress` | Multi-series ordering traps (see multi-timeframe.md) |

## Implications for Heliograph

- Pipeline stages as plug-in kinds: forecaster, gate, combiner, sizer, exit,
  risk gate; a Signal record with the per-component breakdown.
- Two risk layers: target transforms and an independent gate before output.
- Keep a single-class strategy possible, since no framework fits every style.
- Signals evaluated at bar close; simulated fills at the next open.

## Sources

- https://forge.mql5.io/1morty/mql5/src/branch/main/Include/Expert/ExpertSignal.mqh
- https://forge.mql5.io/1morty/mql5/src/branch/main/Include/Expert/Expert.mqh
- https://www.mql5.com/en/articles/367
- https://mql5.com/en/articles/987
- https://www.mql5.com/en/forum/13497
- https://www.metatrader5.com/en/metaeditor/help/MQL5_wizard/wizard_ea_generate
- https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview
- https://raw.githubusercontent.com/QuantConnect/Lean/master/Common/Algorithm/Framework/Alphas/Insight.cs
- https://nautilustrader.io/docs/latest/concepts/architecture/
- https://raw.githubusercontent.com/robcarver17/pysystemtrade/master/docs/backtesting.md
- https://raw.githubusercontent.com/robcarver17/pysystemtrade/master/sysdata/config/defaults.yaml
- https://raw.githubusercontent.com/robcarver17/pysystemtrade/master/sysquant/estimators/diversification_multipliers.py
- https://oreilly.com/library/view/advances-in-financial/9781119482086/c10.xhtml
- https://en.wikipedia.org/wiki/Meta-Labeling
- https://www.freqtrade.io/en/stable/strategy-customization/
- https://hummingbot.org/v2-strategies/executors/positionexecutor/
- https://zipline.ml4trading.io/api-reference.html
- https://www.backtrader.com/docu/sizers/sizers/
