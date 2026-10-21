# Strategy families and small-trader edges

Bottom line: trend following, short-term reversal, stat arb and ML ranking
dominate hourly-to-daily trading, nearly always with volatility-scaled sizing.
A small trader's real edge is capacity, mandates and forced flows, not better
versions of what big CTAs already do.

Researched 2026-10-20; reviewed 2026-10-20.

Reported Sharpe ratios in papers are usually gross of costs or in-sample.
Expect them to shrink live.

## Families at a glance

| Family | Typical holding | Evidence | Main failure mode |
| --- | --- | --- | --- |
| Trend following (time-series momentum) | Weeks to months, rebalanced daily | Strong, long history | Whipsaw in V-shaped markets |
| Intraday / hourly momentum | Hours | Good; strongest on news days | Costs; few trades per day |
| Short-term reversal | Days | Weak raw, better refined | ~100% daily turnover |
| Stat arb / pairs | Days | Good; moving to deep learning | Spread breakdown; model risk |
| ML return ranking | Daily | Good; trees a strong baseline | Better forecast, no better trading |
| LLM news / sentiment | Days | Promising but contaminated | Look-ahead via training data |
| Crypto trend following | Hours to days | Good, mostly recent | Short samples; venue risk |
| Volatility targeting (overlay) | n/a | Strong across 60+ assets | Lags volatility jumps |

## Trend following / time-series momentum

- Long markets with positive recent returns, short negative ones. Diversified
  futures across asset classes.
- A 2018-era study of 82 unexamined assets: 12-month signal gross Sharpe ~1.17;
  ~1.60 diversified across asset groups.
- Positions adjust daily even when lookbacks span weeks or months.
- 2025: CTAs lost 9.6% in H1, on pace for the worst year since at least 1998.
- Cause was whipsaw. Trend pays in drawn-out selloffs, not a flash crash with
  quick rebound.
- One commentator blamed the industry's drift toward shorter lookbacks.
- 2026: SG CTA Index +15.7% in nine months to Q3, versus +11.7% for the S&P 500
  (bond rout, oil surge).
- BofA 2026 monitors: speed drove dispersion in July 2026; slow models clearly
  beat fast ones.
- BofA: universe and speed matter most; asset-class risk allocation and
  volatility reactivity are secondary.
- Zakamulin (March 2026): 2 or 4 regimes (bull/correction/bear/rebound),
  exposure sized by each regime's historical Sharpe.
- He shrinks weights toward zero where signals are noisy.

## Intraday / hourly momentum

- Gao, Han, Li and Zhou: trade the first half-hour return's direction. Sharpe
  1.08 vs 0.29 buy-and-hold.
- Much stronger on macro news days, especially FOMC releases.
- Zarattini, Barbon and Aziz (2025, SPY): enter on abnormal supply/demand
  imbalance; dynamic trailing stops.
- They report 19.6% annualised net of costs, Sharpe 1.33, 2007 to early 2024.
- Late 2025: momentum and reversal also appear in half-day option returns.
  Newly explored.

## Short-term reversal

- Buy last week's biggest losers, short recent winners, hold a few days.
- Plain monthly reversal is likely unprofitable.
- Residual reversal (factor-model residuals) beat conventional: 1.34% vs 0.97%
  monthly gross.
- Reversal within industries concentrates the effect.
- Della Corte et al.: trade overnight returns for the next intraday move. Far
  better than close-to-close.
- Daily turnover is about 100%, so costs decide the outcome.

## Stat arb, ML ranking, LLM signals

- Stat arb trend: end-to-end deep learning replaces two-step pipelines (PCA
  factors, then a mean-reversion rule).
- Attention factors (Stanford, Oct 2025) learn factors, mispricing and policy
  jointly, with costs. Substantially beat two-step.
- A 2025 survey covers the move from cointegration toward ML, deep learning and
  reinforcement learning.
- May 2026 TSX paper: a pre-trained transformer had lower error, but XGBoost
  and an ensemble earned higher daily returns.
- Gradient-boosted trees remain a strong baseline.
- Kirtac and Germano: OPT sentiment Sharpe 3.05 vs 1.23 for the
  Loughran-McDonald dictionary.
- Caution: their training data may overlap the test period. Treat 3.05 as
  possibly look-ahead inflated.
- Oct 2025: an LLM scores whether news supports continuation; the score adjusts
  momentum weights. Best on high-conviction names.

Look-ahead bias is the main LLM caveat:

- Models may "know" outcomes from training data. One test found memorisation
  substantially inflates predictive power.
- DatedGPT: twelve models trained from scratch, each with a strict annual
  cutoff, 2013 to 2024.
- FinCAD suppresses outcome memory at inference. It cut in-sample returns by up
  to 67% on memorised dates.
- Counterpoint: the ChronoGPT authors found the bias modest in news-based
  return forecasting.

## Crypto trend and volatility targeting

- Zarattini, Pagani and Barbon: Donchian ensemble across lookbacks, volatility
  sizing, top-20 rotation. Sharpe above 1.5 net.
- Feb 2026 preprint: 6-hour bars beat 1-hour and daily on signal quality versus
  cost.
- Its 2.41 Sharpe (2022–24) is a single preprint. Treat it cautiously.
- Volatility targeting cut extreme returns and maximum drawdowns across 60+
  assets.
- 2025 S&P 500 study: simple implied-vol plus past-return forecasts matched
  complex models, with lower turnover.

## Common failure modes

- Transaction costs eat short-horizon edges, especially reversal.
- Faster signals whipsaw, as in 2025.
- Look-ahead bias threatens any LLM backtest.
- In-sample and gross Sharpe ratios overstate live results.

## Small-trader edges

Four sources of real edge: capacity (only works small), mandates (big firms
barred), shorting limits, and forced flows.

### Why size matters

- Teo: growing a hedge fund from $20M to $1B cut alpha about 2.04% a year.
- Equity long/short, event-driven and macro suffered from size. Managed futures
  were largely unaffected.
- So liquid-futures trend following is not a small-trader edge.
- 2022 study: more money in a strategy across all funds means worse
  performance.
- Short holding periods use up capacity fastest. Hourly-to-daily on less liquid
  assets suits small accounts.

### Niches

| Niche | Why big firms stay out | How real |
| --- | --- | --- |
| Microcap / small-cap signals | Illiquid; prudent-man rules | Real but eaten by costs |
| Index deletions | Index funds must sell | Slow value trade, not an event trade |
| Spinoffs | Index funds must sell small units | Logic sound; numbers unverified |
| Month-end rebalancing flows | Pensions trade on fixed rules | Small but structural |
| Alternative / illiquid futures | Too small for $10B+ CTAs | Real diversification; liquidity risk |
| Crypto altcoins, funding arbitrage | Venue, custody, compliance | Shrinking; venue risk |
| Prediction markets | Regulation, tiny capacity | Only makers and specialists |
| Benchmark-free strategies | Career risk, tracking error | Durable, behavioural |

### Details by niche

- **Microcaps.** "Replicating Anomalies" tested 447 signals; with microcaps
  down-weighted, 64% became insignificant.
- Microcaps are 60% of listed stocks by count but only 3% of market value.
- Accruals persisted because stocks were too small for institutions and could
  breach prudent-man rules.
- **Post-earnings drift is disputed.** Martineau finds it gone for large stocks
  since 2006.
- Newer papers say drift remains a major factor. A rebuttal finds it vanishes
  without microcaps (t-stat 1.43).
- If drift survives anywhere, it is in microcaps, where costs eat most of it.
- Microcap edge today comes from low costs: patient limit orders, small sizes,
  uncovered companies.
- **Index deletions.** Research Affiliates: deleted stocks outperform by over
  5% a year for five years.
- That is a slow small-cap value recovery. It has not beaten the S&P 500 over
  the last decade.
- Announcement effects have essentially vanished (Greenwood and Sammon).
- Deletion announcement return was about 0.1% in 2010–2020. Addition pop fell
  from 7.4% (1990s) to under 1%.
- **Spinoffs.** Index funds that cannot hold small companies must sell. Source
  was educational; recent numbers unverified.
- **Rebalancing.** About $20 trillion in pension and target-date funds
  rebalance on calendar or threshold rules.
- Overweight equities (e.g. 65/35 vs 60/40) cut next-day equity returns about
  17 basis points. Strongest near month-end.
- **Alternative futures.** Most CTAs trade ~120 liquid futures. One programme
  trades ~50 others, nearly uncorrelated.
- CFM warns returns may come from inflows. If flows stop, impact unwinds.
  Exiting in stress is the risk.
- **Crypto.** A 2025 altcoin trend study required only $2M median daily volume.
- Funding arbitrage (2026): 17% of observations had meaningful spreads; only
  40% of the best paid after costs.
- Realistic net is mid-single digits annualised. Real risks: exchange failure
  and liquidation.
- On-chain arbitrage belongs to bots: about $233.8M from 7.2M+ Ethereum
  arbitrages.
- **Prediction markets.** Across 588 million Polymarket trades, the top 1%
  captured 76.5% of profits.
- Makers posting limit orders win; takers and long-shot bettors lose.
- Cross-platform fees ~5% wipe out spreads under ~6%. In 2024 two platforms
  settled a "same" event oppositely.
- Users do worse in sports, better in weather and technology. The edge is
  shrinking.
- **Benchmarks.** Low-volatility stocks beat high-volatility; benchmarked,
  unlevered institutions cannot exploit it.
- Many pensions, endowments and mutual funds cannot short.
- In 2025 trend funds' performance-fee revenue fell 61%. An individual can keep
  running through that.

### Avoid

- Liquid-futures trend following: big firms run it cheaply with no capacity
  problem.
- Latency-sensitive arbitrage and on-chain bot competition (MEV).
- Backtests whose edge lives in the smallest stocks without realistic costs.

Best fit: trend and reversal on small, illiquid universes, strict cost
modelling, plus calendar and forced-flow trades.

## Implications for Heliograph

- First example strategies: month-end rebalancing, volatility-scaled
  multi-lookback trend, small-cap pairs (Phase 2 onward).
- Volatility targeting and a "max % of average daily volume" cap belong in the
  sizer and risk gate.
- The cost model must scale slippage with order size versus volume, pessimistic
  by default.
- LLM-derived signals need dated models or an explicit look-ahead check before
  trust.
- Trend plug-ins should expose speed and lookback as parameters; slow beat fast
  in 2026.

## Sources

- Trend: https://dailyspeculations.com/wordpress/?p=14427 · https://quantpedia.com/time-series-momentum-works-everywhere/ · https://www.cxoadvisory.com/technical-trading/regime-optimal-trend-following/
- CTA 2025–26: https://alpha-maven.com/story/managed-futures/a-wild-year-for-markets-hits-trend-following-hedge-funds-wsj · https://www.alternativeswatch.com/2025/06/02/trend-following-hedge-funds-endure-more-pain-societe-generale-indices/ · https://proactiveadvisormagazine.com/trend-following-is-dead/ · https://www.cnbc.com/2026/10/06/trend-following-hedge-funds-beat-stock-market.html · https://finvaulta.com/research/bank-of-america/cta-design-impact-monitor-2026-08-10
- Intraday: https://substack.com/@quantifiedstrategies/note/c-151224359 · https://www.alexandria.unisg.ch/handle/20.500.14171/122124 · https://cfr-cologne.de/download/workingpaper/cfr-26-02.pdf
- Reversal: https://www.cxoadvisory.com/technical-trading/overnightintraday-return-reversal-trading/ · https://www.cxoadvisory.com/technical-trading/purified-short-term-stock-reversal/ · https://www.cxoadvisory.com/momentum-investing/short-term-reversal-by-industry · https://stocksharp.com/store/strategies.0397_short_term_reversal_stocks/
- Stat arb and ML: https://alphaxiv.org/overview/2510.11616v1 · https://nep.repec.org/nep-cmp/2025-09-29 · https://arxiv.org/pdf/2605.23962
- LLM: https://eprints.lse.ac.uk/128664/ · https://arxiv.org/html/2510.26228v1 · https://arxiv.org/html/2512.23847v1 · https://sotaverified.org/papers/260311838 · https://arxiv.org/abs/2605.24564 · https://arxiv.org/html/2502.21206v2
- Crypto trend: https://abarbon.com/papers/catching-crypto-trends · https://arxiv.org/pdf/2602.11708 · https://www.cxoadvisory.com/technical-trading/crypto-asset-trend-following-strategies/
- Volatility targeting: https://alphaarchitect.com/volatility-targeting-improves-risk-adjusted-returns/ · https://joim.com/forecasting-and-managing-volatility-an-sp-500-case-study
- Capacity: https://ink.library.smu.edu.sg/bnp_research/21 · https://www.evidenceinvestor.com/post/generating-alpha-sows-the-seeds-of-its-own-destruction · https://caia.org/node/7181
- Microcaps and drift: https://www.stern.nyu.edu/experience-stern/news/professor-baruch-lev-s-research-accruals-anomaly-featured · https://www.nber.org/papers/w23394.pdf · https://causalclaims.trfetzer.com/paper/w23394.html · https://www.eventstudytools.com/earnings-announcements · https://cfr.ivo-welch.info/published/papers/martineau2021rest.pdf · https://nowpublishers.com/article/Details/CFR-0122 · https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again/
- Index events: https://www.aaii.com/investor-update/article/229877-profit-by-buying-the-sp-500s-castoffs · https://www.rafi.com/research/publications/1043-nixed-the-upside-of-getting-dumped · https://dash.harvard.edu/server/api/core/bitstreams/647cc3b7-4803-4748-b181-e5ec8b343de7/content · https://papers.ssrn.com/abstract=4294297 · https://ideas.repec.org/a/bla/jfinan/v80y2025i2p657-698.html · https://fffinstill.com/learning/concepts/spin-offs-divestitures
- Rebalancing: https://rpc.cfainstitute.org/blogs/enterprising-investor/2025/rebalancings-hidden-cost-how-predictable-trades-cost-pension-funds-billions · https://afajof.org/management/viewp.php?n=144452
- Alternative futures: https://hedgenordic.com/2020/06/alternative-markets-amidst-covid-turmoil/ · https://www.cfm.com/the-cautious-tale-of-ctas-trading-illiquid-alternative-markets/
- Funding arbitrage: https://www.mdpi.com/2227-7390/14/2/346 · https://www.alphaexcapital.com/cryptocurrencies/crypto-trading-and-investing-strategies/crypto-derivatives-and-leverage/funding-rate-arbitrage-basics
- Prediction markets: https://www.hec.edu/en/node/17306310 · https://econ.au.dk/research/finance/seminars/vis/artikel/finance-seminar-pat-akey-essec-business-school · https://gigazine.net/gsc_news/en/20260525-polymarket-win-or-lose/ · https://newyorkcityservers.com/blog/prediction-market-arbitrage-guide · https://www.cnbc.com/2026/09/14/prediction-markets-efficient-win-lose-beat.html
- Benchmarks: https://archivefda.dlib.nyu.edu/handle/2451/29593 · https://www.etf.com/sections/index-investor-corner/swedroe-why-some-anomalies-persist
