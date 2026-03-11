# Research Log
---
## 2026-02-28: Initial Data Exploration
### Question
What are the basic properties of BTCUSDT 2024 hourly data?
### Findings
- **Total bars:** 8,761
- **Date range:** 2023-12-31 to 2024-12-30
- **Price range:** $38,555 to $108,353
- **Year return:** +116.3%
- **Annualized volatility:** 52.5%
- **Mean hourly volume:** 1,474 BTC
- **Skewness:** -0.13 (slight left tail)
- **Kurtosis:** 7.63 (fat tails)
### Implications
1. **High volatility (52%)** — Lots of trading opportunity, but need careful position sizing.
2. **Fat tails (kurtosis 7.6)** — Normal distribution assumptions are wrong. Extreme moves happen ~3x more often than expected. Stop losses may get blown through.
3. **Slight negative skew** — Crashes are slightly sharper than rallies. Shorts can be profitable but risky:(
4. **Strong uptrend (+116%)** — Buy-and-hold was very profitable in 2024. Any strategy needs to beat this benchmark.
---
---
## 2026-03-XX: First Backtest - Simple Momentum
### Strategy
- Buy 0.5 BTC when price up >2% over last 24 hours
- Sell all when price down >2% over last 24 hours
### Results
- **Total Return:** 17.7%
- **Sharpe Ratio:** 0.18
- **Max Drawdown:** 15.9%
- **Trades:** 203
- **Win Rate:** 38%
- **Total Costs:** $13,325 (fees + slippage)
### vs Benchmark
- Buy & Hold returned +116%
- Strategy significantly underperformed
### Lessons
1. Simple momentum doesn't work well in strong uptrends — you're constantly getting out and missing gains.
2. Transaction costs ($13k) are significant — need fewer, higher-quality trades.
3. 38% win rate can still be profitable if winners > losers.
### Next ideas
- Try mean reversion instead?
- Longer holding periods to reduce costs?
- Only trade when volatility is high?
---
---
## 2026-03-11: Strategy Comparison — Momentum vs Mean Reversion
### Question
How do Momentum and Mean Reversion compare across different parameter combinations? Which parameters matter most?

### Setup
- **Data:** BTCUSDT 2024 hourly bars (8,761 bars)
- **Capital:** $100,000
- **Fees:** 10 bps taker (Binance spot)
- **Slippage:** 5 bps half-spread
- **Parameters tested:** 3 lookbacks (12, 24, 48h) × 3 thresholds (1%, 2%, 3%) = 9 combos per strategy, 18 total

### Results — Momentum

| Lookback | Threshold | Return (%) | Sharpe | Max DD (%) | Trades | Win Rate (%) | Fees ($) |
|----------|-----------|------------|--------|------------|--------|--------------|----------|
| 12h      | 1%        | -2.0       | 0.01   | 19.9       | 508    | 33.4         | 22,053   |
| 12h      | 2%        | 27.5       | 0.26   | 15.1       | 235    | 40.3         | 10,344   |
| 12h      | 3%        | 15.4       | 0.15   | 23.1       | 125    | 42.7         | 5,451    |
| 24h      | 1%        | -0.5       | 0.02   | 25.7       | 388    | 35.8         | 16,577   |
| 24h      | 2%        | 13.4       | 0.14   | 19.4       | 207    | 38.0         | 9,044    |
| **24h**  | **3%**    | **40.7**   | **0.36**| **10.6**   | **119**| **43.6**     | **5,240**|
| 48h      | 1%        | 25.8       | 0.23   | 23.5       | 248    | 36.6         | 10,458   |
| 48h      | 2%        | 39.8       | 0.35   | 14.5       | 146    | 44.8         | 6,382    |
| 48h      | 3%        | 33.6       | 0.30   | 12.7       | 113    | 43.2         | 4,997    |

### Results — Mean Reversion

| Lookback | Threshold | Return (%) | Sharpe | Max DD (%) | Trades | Win Rate (%) | Fees ($) |
|----------|-----------|------------|--------|------------|--------|--------------|----------|
| 12h      | 1%        | -6.6       | -0.03  | 23.1       | 321    | 61.4         | 13,946   |
| 12h      | 2%        | 12.8       | 0.15   | 14.9       | 116    | 64.9         | 4,905    |
| **12h**  | **3%**    | **36.0**   | **0.39**| **10.6**   | **47** | **86.7**     | **1,894**|
| 24h      | 1%        | -11.1      | -0.07  | 23.8       | 293    | 60.3         | 12,418   |
| 24h      | 2%        | 1.1        | 0.03   | 22.0       | 136    | 60.9         | 5,820    |
| 24h      | 3%        | 12.9       | 0.14   | 13.1       | 62     | 65.0         | 2,626    |
| 48h      | 1%        | -5.3       | -0.02  | 17.6       | 210    | 62.5         | 8,910    |
| 48h      | 2%        | 0.4        | 0.03   | 19.3       | 119    | 63.6         | 5,153    |
| 48h      | 3%        | 8.6        | 0.10   | 21.8       | 64     | 57.1         | 2,761    |

### Best Per Strategy
- **Momentum:** 24h lookback, 3% threshold → 40.7% return, 0.36 Sharpe, 119 trades
- **Mean Reversion:** 12h lookback, 3% threshold → 36.0% return, 0.39 Sharpe, 47 trades

### vs Benchmark
- Buy & Hold returned +116%
- Best Momentum (40.7%) captured ~35% of the benchmark return
- Best Mean Reversion (36.0%) captured ~31% of the benchmark return
- **No strategy beats buy & hold** — expected in a strong trending year

### Key Findings

1. **Threshold matters more than lookback.** Across both strategies, 1% threshold consistently loses money or barely breaks even. 3% threshold consistently does best. The pattern: tighter threshold → more trades → more fees → worse results. This holds for both strategy types.

2. **Fees are the silent killer.** Momentum(12h, 1%) paid $22k in fees on a $100k account and lost money. Momentum(24h, 3%) paid $5.2k and returned 40.7%. Transaction costs dominated the results — roughly 4× difference in fees between the worst and best combos.

3. **MeanReversion(12h, 3%) has best Sharpe but only 47 trades.** 87% win rate looks amazing, but 47 trades is thin. Standard error of Sharpe ≈ 1/√47 ≈ 0.15, so true Sharpe could plausibly be 0.24–0.54. Momentum(24h, 3%) with 119 trades is more trustworthy (SE ≈ 0.09).

4. **Mean Reversion has higher win rates but lower returns.** MeanRev win rates: 57–87%. Momentum win rates: 33–45%. But Momentum's best return (40.7%) beats MeanRev's best (36.0%). MeanRev wins many small trades but misses the big trending moves.

5. **Both strategies struggle in trends.** BTC 2024 was a +116% year. Momentum keeps exiting and re-entering. Mean Reversion actively fights the trend (sells into rallies). Neither captures sustained directional moves well.

### Next Steps
- Add SMA crossover and Bollinger band strategies (Weeks 5-6) — these may handle trends better
- Walk-forward testing (Weeks 7-8) — split data into train/test to check if best params are overfit
- Consider: would these results hold in a sideways or bearish market?
---