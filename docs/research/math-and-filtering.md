# Advanced maths and noise filtering

Bottom line: advanced maths helps with state estimation, regime overlays,
entry/exit levels, sizing and validation, but rarely predicts direction on its
own. Every filter must be causal: a two-sided smoother in a backtest is
look-ahead.

Researched 2026-10-20; reviewed 2026-10-20.

## Method by job

| Job | Method | Verdict |
| --- | --- | --- |
| Null hypothesis | Brownian motion simulation | Essential benchmark |
| State estimation | Kalman filter | Proven; watch P&L accounting |
| Denoising | Causal wavelets | Only with trailing windows |
| Feature prep | Fractional differencing | Useful, low-risk |
| Multi-asset risk | RMT correlation cleaning | Proven for portfolio risk |
| Regimes | HMM | Weak at daily resolution |
| Regimes | Statistical jump model | Best current evidence |
| Trend-break timing | Online changepoint detection (BOCPD) | Strong for trend strategies |
| Mode choice | Hurst / fractional Brownian motion | Noisy; use as a filter |
| Volatility forecast | HAR, rough volatility | HAR is the benchmark to beat |
| Mean-reversion entries/exits | OU optimal stopping | Principled replacement for fixed z-scores |
| Exits, trade filtering | Triple barrier + meta-labelling | Practical, widely used |
| Sizing under uncertainty | Conformal prediction | Promising, unproven live |
| Validation | Deflated Sharpe, PBO | Essential |

## Brownian motion is the null, not the edge

- Geometric Brownian motion is the "nothing to exploit" random walk.
- Run the strategy on simulated random-walk prices. If it still "works", that
  is overfitting.
- Useful departures: mean reversion (Ornstein–Uhlenbeck), memory (fractional
  Brownian motion), regime shifts.

## State estimation and denoising

**Kalman filter (state-space model)**

- Treats a true quantity (hedge ratio, trend level) as hidden and updates it
  each bar.
- Proven use: dynamic hedge ratios in pairs trading. It generalises rolling
  linear regression.
- Pitfall: updating beta every step and counting estimate changes as profit
  inflates P&L.
- Fix: lock the hedge ratio at entry when computing that trade's P&L.
- One practitioner's write-up (not peer-reviewed) shows the pitfall. After
  fixing it, win rate still rose ~8.6 points.
- Treat that write-up as an illustration of the accounting trap, not
  performance evidence.

**Wavelet denoising**

- Offline wavelet transforms let a later price jump change earlier smoothed
  values. That leaks the future.
- Most papers claiming wavelet gains denoise the whole series, future included.
  Impossible live.
- Causal fix: transform a trailing window only and keep the last point.
- Use the Haar wavelet: shortest support, fewest boundary effects on short
  series.
- General rule: any zero-phase or two-sided filter is invalid in a backtest.

**Fractional differentiation**

- ML needs stationary inputs. Returns (d=1) are stationary but discard memory.
- Use 0 < d < 1 for stationarity with maximum retained memory.
- López de Prado argues almost all finance research over-differences prices.

**Random matrix theory**

- Estimated correlation matrices mix real structure with sampling noise.
- Eigenvalues inside the Marchenko–Pastur band are treated as noise.
- Optimised portfolios' real risk is usually much larger than backtested risk.
  RMT cleaning shrinks the gap.

## Regimes

**Hidden Markov models**

- Assume hidden states (e.g. calm vs crisis) and estimate the current one.
- 2026 Asian study: beat buy-and-hold on Sharpe in 3 of 5 indices (Nikkei 0.71
  vs 0.47).
- It failed in Hong Kong: post-2022 was long misclassified as "bear".
- 2026 eight-asset study (Garg): confidence signals mostly useless daily; none
  passed strict significance.
- Those results were in-sample, so live would be weaker.
- Use HMMs for weekly or monthly exposure changes, not daily triggers.

**Statistical jump models**

- Add an explicit penalty per regime switch, so regimes persist.
- Shu, Yu and Mulvey (Princeton): US, German, Japanese indices, 1990–2023, with
  costs and trading delays.
- The jump model beat both HMM and buy-and-hold on volatility, maximum drawdown
  and Sharpe.
- Persistence also makes it robust when trading is delayed.
- A continuous extension outputs regime probabilities, useful for sizing.

**Bayesian online changepoint detection (Adams and MacKay)**

- Tracks a distribution over run length: time since the last structural break.
- Cheap recursive update per new point.
- Trend strategies make bad bets just after reversals. That is where BOCPD
  helps.
- Wood, Roberts and Zohren (Oxford) added it to deep momentum, blending slow
  trend with fast reversion at turns.

## Process models for signals and volatility

- **Hurst exponent:** H > 0.5 trending, H < 0.5 mean-reverting, H = 0.5 random
  walk.
- In theory a tiny departure from 0.5 is exploitable with about a year of
  hourly data.
- In practice estimates are noisy and drift. Use Hurst to choose trend vs
  reversion modules only.
- **Rough volatility:** Gatheral, Jaisson and Rosenbaum found log-volatility
  behaves like fBm with H ≈ 0.1 at all reasonable scales.
- It improves realised-volatility forecasts for sizing and stops.
- **HAR:** forecasts tomorrow from yesterday's, last week's and last month's
  realised volatility.
- Across 1,445 stocks, heavily tuned ML failed to beat a well-fitted HAR
  (Audrino and Chassot).
- A 2025 Fed paper: regime-switching HAR beat both ML and plain linear models.

## Entries and exits

- **OU optimal stopping (Leung and Li):** derive entry and exit levels with
  costs and a stop-loss.
- Replaces the typical ±1 standard deviation entry.
- Higher stop-loss implies lower optimal take-profit. Faster reversion means
  tighter levels.
- Signature-based optimal stopping drops the strict OU assumption and beat
  conventional rules.
- **Triple barrier (López de Prado):** volatility-scaled profit target, stop
  and time limit. Label = first barrier hit.
- **Meta-labelling:** a second model predicts if the primary signal is right.
  Use it to filter or size.
- Cleanest way to add ML to an existing strategy.
- **Path signatures / Sig-Trading (Futter et al.):** strategy as a linear
  function of signature features.
- Extends mean-variance to path-dependent signals with drawdown control.
  Research-grade.

## Sizing

- Conformal prediction gives distribution-free intervals.
- A pre-registered 2026 single preprint scaled fractional Kelly by interval
  width (narrow = bigger).
- The simplest, slowest calibration beat all "adaptive" versions.
- Out of sample it hit target coverage but underperformed simple benchmarks.
  Unproven live.

## Validation

- Testing many variants almost guarantees a lucky winner.
- Example: best in-sample Sharpe 1.59 became −0.18 out of sample.
- Deflated Sharpe ratio (Bailey and López de Prado) corrects for trials tried
  and fat tails.
- Probability of backtest overfitting (Bailey et al.) estimates how often the
  in-sample winner falls below median out of sample.
- Passing both is much stronger evidence than passing either.

## Avoid or treat as overhyped

- Two-sided or zero-phase smoothers, offline wavelets, any full-series
  denoising.
- HMM signals as daily trade triggers.
- Standalone Hurst signals.
- ML volatility models that have not beaten HAR.
- Kalman Sharpe jumps without per-trade locked hedge ratios.
- Signatures and conformal sizing before simpler parts earn their place.

## Suggested stack (hourly-to-daily)

1. Causal filters only.
2. Jump-model or changepoint regime overlay.
3. Kalman-estimated spreads or trends.
4. OU-based or triple-barrier exits.
5. Volatility-scaled sizing from HAR or rough-volatility forecasts.
6. Deflated Sharpe and overfitting checks before risking capital.

## Implications for Heliograph

- Plug-ins this research supports: incremental Kalman, HAR volatility, jump
  model and HMM, BOCPD, OU exits, triple barrier, meta-labelling, causal
  wavelets. When they are built: [roadmap.md](../roadmap.md).
- Every filter plug-in must pass the conformance kit's no-peeking test;
  two-sided smoothers are rejected by design.
- Kalman pairs exits must lock the hedge ratio at entry for P&L.
- Regime plug-ins publish probabilities, slow and persistent, not hard daily
  labels.
- Validation belongs in the Strategy Tester: deflated Sharpe and PBO beside
  every winner; random-walk null tests are cheap to add.

## Sources

- Jump models: https://arxiv.org/abs/2402.05272v3 · https://collaborate.princeton.edu/en/publications/downside-risk-reduction-using-regime-switching-signals-a-statisti/ · https://papers.ssrn.com/abstract=4556048
- HMM: https://so09.tci-thaijo.org/index.php/PMR/article/view/10023 · https://papers.ssrn.com/abstract=6733198
- Changepoints: https://arxiv.org/pdf/0710.3742 · https://arxiv.org/abs/2105.13727v2
- OU and signatures: https://arxiv.org/pdf/1411.5062 · https://hudsonthames.org/optimal-stopping-in-pairs-trading-ornstein-uhlenbeck-model/ · https://arxiv.org/html/2309.16008v4 · https://arxiv.org/abs/2308.15135v2
- Kalman: https://www.quantstart.com/articles/Dynamic-Hedge-Ratio-Between-ETF-Pairs-Using-the-Kalman-Filter · https://hudson-and-thames-arbitragelab.readthedocs-hosted.com/en/latest/other_approaches/kalman_filter.html · https://substack.com/@navnoorbawa/note/c-231952638
- Wavelets: https://ftp.fau.de/cran/web/packages/rLifting/vignettes/benchmark_causal.html · https://in.tradingview.com/script/xjlA5XGY-Wavelet-Trend-Filter/
- Fractional differencing: https://mlfinpy.readthedocs.io/en/latest/FractionalDifferentiated.html
- RMT and correlations: https://arxiv.org/pdf/physics/0507111 · https://arxiv.org/pdf/2305.08241 · https://arxiv.org/pdf/cond-mat/0104305
- Volatility: https://arxiv.org/abs/1410.3394 · https://www.sfi.ch/en/publications/n-24-70-hard-to-beat-the-overlooked-impact-of-rolling-windows-in-the-era-of-machine-learning · https://www.federalreserve.gov/econres/feds/linear-and-nonlinear-econometric-models-against-machine-learning-models.htm
- Triple barrier and meta-labelling: https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy/
- Conformal Kelly: https://ideas.repec.org/p/arx/papers/2608.01494.html · https://arxiv.org/pdf/2608.01494
- Validation: https://papers.ssrn.com/abstract=2460551 · https://papers.ssrn.com/abstract=2326253 · https://www.cxoadvisory.com/big-ideas/backtest-overfitting-the-movies/ · https://www.luxalgo.com/library/concept/probability-of-backtest-overfitting.md
