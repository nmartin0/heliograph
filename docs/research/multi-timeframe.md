# Indicators on a different timeframe from the chart

Every surveyed platform lets an indicator use another timeframe, and most get
the boundary wrong in one direction: too early (look-ahead, repainting) or too
late (one bar of lag). With bars stamped at their end, the correct rule is that
a higher-timeframe value ending at T is visible to every consumer at T or later
(decision D9).

Researched 2026-10-21.

## Platforms

| Platform | Mechanism | Behaviour at the boundary |
| --- | --- | --- |
| MetaTrader 5 | Timeframe argument (`iMA(symbol, PERIOD_H4, …)`); `iBarShift` maps chart bars to higher-timeframe bars | Index 0 is the forming bar, so values repaint; `CopyBuffer` returns −1 until data is ready |
| TradingView | `request.security(sym, tf, expr, gaps, lookahead)`; `indicator(timeframe=…)` | `lookahead_on` without `[1]` leaks the future on history; realtime returns the last value regardless, so history and live differ. Recommended idiom: `expr[1]` with `lookahead_on` |
| NinjaTrader 8 | `AddDataSeries` in `State.Configure`; `BarsInProgress`; `BarsArray[i]` | Primary series processed first on history, so a secondary bar ending at the same time is seen one bar late |
| LEAN | Consolidators per indicator; `RegisterIndicator(…, resolution)` | Bars rounded in exchange time; emitted on the next point or when `Scan` sees the period elapse; warm-up counted in base bars |
| Backtrader | `resampledata`, `replaydata`; `rightedge=True` stamps at the end | `replaydata` delivers the forming bar without advancing `len` |
| Freqtrade | `merge_informative_pair` (candles stamped at open) | Shifts by `inf_tf − base_tf` since issue #4073; the docstring still describes the old rule |
| NautilusTrader | Bar types `instrument-step-agg-price-INTERNAL/EXTERNAL`, composite with `@` | `time_bars_build_delay`, `timestamp_on_close`, origin offset as explicit settings |

TradingView's lower-timeframe call `request.security_lower_tf()` returns an
array of every intrabar value per chart bar.

## Correctness rules

1. A higher-timeframe value ending at T may join any chart bar ending at T or
   later. Earlier is look-ahead; requiring T + one chart bar is lag.
2. Ties need a rule: everything stamped T must be delivered before consumers
   that join streams at T evaluate.
3. Closing a bar on time needs the clock, not the next tick; otherwise a
   weekend delays it by days.
4. Signals and history use closed values only; forming values are display.
5. Anchors are part of the spec: UTC midnight, exchange time zone with offset,
   session open, weekly anchor day, daylight-saving behaviour. Crypto daily
   bars end 00:00 UTC; US equities end at the session close.
6. Gap policy is explicit: emit nothing, carry the last value, or emit an empty
   bar.
7. Warm-up is counted in the indicator's own bars: period × (its size ÷ base
   size) base points.
8. A higher-timeframe series on a lower-timeframe chart is a step; a smooth
   line implies interpolation, which is look-ahead.

## Display with Lightweight Charts

- Candles are conventionally placed at their open; convert end stamps at the
  chart adapter.
- Higher timeframe: line series with `lineType: LineType.WithSteps`, placed on
  the first chart bar whose end is at or after the value's time;
  `WhitespaceData` for gaps and warm-up.
- Lower timeframe: aggregate per chart bar (last, min–max band, count), or a
  linked pane. Mixing raw 15-minute times onto a 1-hour time scale probably
  inserts empty slots (unverified).
- Label every series with its stream.

## Implications for Heliograph

- A stream spec per consumer: instrument, size, anchor, zone, session, gaps,
  price field; one shared resampler per spec.
- Clock alarms close bars; the indicator graph stabilises once per timestamp.
- Tests pin both directions at the boundary (visible at T, not before).
- Refuse resampling to a size smaller than the source.

## Sources

- https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/
- https://www.tradingview.com/pine-script-docs/concepts/repainting/
- https://www.tradingview.com/pine-script-docs/concepts/timeframes/
- https://www.mql5.com/en/docs/indicators/ima
- https://www.mql5.com/en/docs/series/ibarshift
- https://www.mql5.com/en/docs/series/CopyBuffer
- https://static.ninjatrader.com/support/helpGuides/nt8/multi-time_frame__instruments.htm
- https://www.quantconnect.com/docs/v2/writing-algorithms/consolidating-data/updating-indicators
- https://raw.githubusercontent.com/QuantConnect/Lean/master/Common/Data/Consolidators/PeriodCountConsolidatorBase.cs
- https://www.backtrader.com/docu/data-resampling/data-resampling/
- https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/strategy/strategy_helper.py
- https://github.com/freqtrade/freqtrade/issues/4073
- https://nautilustrader.io/docs/latest/concepts/data/
- https://tradingview.github.io/lightweight-charts/docs/api/enumerations/LineType
