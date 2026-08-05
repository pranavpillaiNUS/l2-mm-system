# Lessons Learned - Phase 1 Backtester

What I know now that I didn't know at the start. Written after completing the backtesting framework, 4 strategies, parameter sweeps, and walk-forward validation on BTCUSDT 2024 hourly data.

> **Publication note (2026-08-04):** this is a contemporaneous learning record,
> not current strategy evidence. A later audit found same-close signal/execution
> timing, incorrect hourly annualization, an average of monthly Sharpe estimates
> labeled as OOS Sharpe, and optimizer source that was later reconstructed from
> the logged grids. Treat the numerical interpretations below as historical
> observations and hypotheses. The current metrics helper corrects the
> frequency inference, but the historical CSVs were not rewritten. The L2 system
> was implemented independently and is assessed separately in the technical
> report.


## Quantitative Findings

**Transaction costs dominate at high frequency.** Before building this, I vaguely knew "fees matter." Now I can quantify it: on hourly BTC bars with 10 bps taker fees, any strategy trading more than ~150 times per year struggles to overcome cost drag. The threshold isn't a fuzzy "trade less" - it's a hard breakeven calculation. If your average trade captures X basis points of edge, you need X > fee_bps + slippage_bps just to break even, and you need X >> costs to have a meaningful Sharpe. Most of my 1%-threshold configs had gross edge of 5-20 bps per trade but paid 10+ bps in costs, leaving almost nothing.

**Parameter stability predicts OOS performance better than in-sample Sharpe.** This was the most important finding from walk-forward. SMA Crossover tied for the highest in-sample Sharpe (0.39) but had the worst OOS performance because its optimal parameters kept shifting across windows (4 unique combos in 9 windows). MeanReversion had the same in-sample Sharpe but picked identical parameters in all 9 windows and actually improved OOS (0.51). If I were building a strategy selection framework, parameter stability across rolling windows would be the first filter, before looking at any return metric.

**Trend-following and mean-reversion fail in opposite regimes, and neither knows which regime it's in.** Momentum lost money in 4 of 9 OOS months (all choppy). MeanReversion never lost but had zero trades in 2 of 9 months (both trending). Combining them reduces variance, but a regime detector would be better. I didn't build one, but the ingredients are there: if realized volatility is above some percentile and returns are persistent (positive autocorrelation), it's a trend regime. If vol is high but returns are mean-reverting (negative autocorrelation), it's a chop regime. This is worth testing in the L2 phase.

**Nothing beat buy-and-hold in 2024.** Best OOS strategy (Momentum) reached $128k vs buy-and-hold's ~$148k. This isn't surprising in hindsight - 2024 was a +116% year and any strategy that ever goes flat or short will underperform. The strategies aren't designed to beat buy-and-hold in a bull market. They're designed to have better risk-adjusted returns (lower drawdowns, more consistent). MeanReversion achieved this: 0 losing months, 2.6% monthly return std vs Momentum's 7.4%. Whether that tradeoff is worth it depends on your objective function.

**Bollinger Bands added complexity without adding value.** BB and MeanReversion are fundamentally the same idea (buy below a band, sell above), but BB has two parameters (period, num_std) vs MeanReversion's two (lookback, threshold). Same number of knobs, same core logic, nearly identical OOS results ($125k vs $126k), but BB needed 3.7x more trades. The adaptive bands don't help because the "adaptation" (wider bands when vol is high) actually makes the strategy less responsive exactly when the biggest mean-reversion opportunities appear. This taught me that adding parameters doesn't add value unless the extra degrees of freedom capture genuinely different market dynamics.


## Engineering and Design Lessons

**The strategy interface was the best early decision.** Defining BaseStrategy with generate_signal() before writing any strategies meant I could add new strategies in 30 minutes without touching the engine, portfolio, or metrics code. All 4 strategies, the parameter sweeps, and the walk-forward analysis work with the same engine. If I'd hardcoded the first momentum strategy into the engine (which is what I almost did), every subsequent strategy would have required refactoring.

**State leakage is easy to miss.** SMA Crossover tracks prev_fast and prev_slow internally to detect crossovers. When running parameter sweeps, I need fresh strategy instances for each backtest - reusing an instance carries over the previous run's SMA values into the next run's first few bars. This is obvious in retrospect but I only caught it because I was testing carefully. The fix is simple (new instance per run), but the class of bug (stateful objects reused across runs) is something to watch for in the L2 engine where there will be much more internal state.

**The walk-forward script was the hardest code to write.** Not because the logic is complex, but because it combines data loading, strategy instantiation, parameter grid iteration, train/test splitting, metric collection, and CSV output - lots of pieces that each need to be correct. I spent more time debugging off-by-one errors in window boundaries than on any strategy logic. The lesson is that analysis pipelines (code that orchestrates other code) are harder to test than the components they orchestrate, and they deserve their own tests.

**YAML configs were overkill for this phase.** BacktestConfig supports YAML save/load, but I never actually used it - every script just constructs the config in code. The YAML support isn't wasted (it'll be useful when running many configs in the L2 phase), but I spent time on it earlier than I needed to. Build what you need now, not what you think you'll need later.

**Force-adding files past .gitignore is annoying.** The .gitignore blocks *.png and *.csv, so result files need `git add -f`. This is fine for a few files but will get tedious with more results. A better approach would be to gitignore data/ but not results/, or use a results-specific allowlist in .gitignore.


## Process Lessons

**Research log entries should be written the same day.** The entries I wrote immediately after finishing work are detailed and useful. The ones I wrote days later are vague. "I'll remember what I did" is always wrong.

**Sweep scripts should save all results to CSV, not just print them.** I learned this after the first comparison script - having the raw CSV meant I could re-analyze results without re-running backtests (which take minutes each). The walk-forward script saves CSVs and that made the plotting script possible. Every analysis script should output structured data, not just console output.

**Testing strategies is different from testing infrastructure.** Portfolio, metrics, and data loader tests are straightforward: give input, check output. Strategy tests are harder because the "correct" output depends on the data, and you can't easily construct test data that exercises all code paths. I ended up testing strategies mostly through end-to-end backtests on real data rather than unit tests on synthetic data. This is fine for 4 strategies, but won't scale. For the L2 phase, I need deterministic replay tests where I know exactly what the orderbook looks like and can verify exact fill behavior.


## What Carries Forward

Things I'll apply directly to the L2 replay engine and market-making work:

1. **Cost modeling must be precise.** The backtester's flat taker fee showed how sensitive results are to cost assumptions. The L2 simulator needs maker vs taker distinction, realistic spread modeling, and queue-position-dependent fill probability. Getting costs wrong by even a few basis points can flip a strategy from profitable to unprofitable.

2. **Deterministic replay is non-negotiable.** The backtester processes bars in order and is deterministic, which made debugging possible. The L2 engine processes events (order inserts, cancels, trades) and must also be deterministic - same input, same output, every time. This is the foundation that everything else (parity tests, regression tests, C++ port verification) depends on.

3. **Test with walk-forward from the start.** I added walk-forward analysis in week 7, after I'd already picked "best" params from in-sample sweeps. The OOS results completely reshuffled the ranking. For the L2 strategies, I'll run walk-forward from the first experiment so I don't waste time optimizing params that won't generalize.

4. **Parameter stability as a first filter.** Before looking at any performance metric for a new market-making strategy, I'll check: do the optimal parameters change across windows? If yes, the strategy likely doesn't have a stable edge, regardless of how good the in-sample numbers look.

5. **Regime detection is worth building.** The complementary failure modes of trend-following vs mean-reversion suggest that a volatility/autocorrelation-based regime filter could improve both. This is something to prototype early in the summer.

6. **Save everything, print nothing.** Every experiment should output structured results to disk. Console output is for monitoring, not for analysis.
