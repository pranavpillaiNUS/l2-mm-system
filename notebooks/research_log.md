# Research Log

---
## 2026-02-28: Initial Data Exploration

### Question
What does BTCUSDT 2024 hourly data actually look like fr?

### Findings
- 8,761 bars from 2023-12-31 to 2024-12-30
- price ranged from $38,555 to $108,353
- year return: +116.3%
- annualized volatility: 52.5%
- mean hourly volume: 1,474 BTC
- skewness: -0.13 (slight left tail)
- kurtosis: 7.63 (fat tails)

### What this means
1. high volatility (52%) — lots of opportunity but need to be careful with position sizing
2. fat tails (kurtosis 7.6) — normal distribution assumptions don't hold. extreme moves happen ~3x more often than expected. stop losses can get blown through
3. slight negative skew — crashes are a bit sharper than rallies
4. strong uptrend (+116%) — buy and hold was very profitable in 2024. any strategy needs to beat this benchmark

---
## 2026-03-XX: First Backtest — Simple Momentum

### Strategy
- buy 0.5 BTC when price up >2% over last 24 hours
- sell all when price down >2% over last 24 hours

### Results
- return: 17.7%
- sharpe: 0.18
- max drawdown: 15.9%
- trades: 203
- win rate: 38%
- total costs: $13,325 (fees + slippage)

### vs benchmark
buy & hold returned +116%. strategy significantly underperformed.

### Lessons
1. simple momentum doesn't work well in strong uptrends — constantly getting out and missing gains
2. transaction costs ($13k) are significant — need fewer, higher quality trades
3. 38% win rate can still be profitable if winners are bigger than losers

### Ideas for next
- try mean reversion?
- longer holding periods to reduce costs?
- only trade when volatility is high?

---
## 2026-03-11: Strategy Comparison — Momentum vs Mean Reversion

### Question
How do Momentum and Mean Reversion compare across different parameters? Which params matter most?

### Setup
- BTCUSDT 2024 hourly bars (8,761 bars)
- $100,000 capital
- 10 bps taker fee (Binance spot)
- 5 bps half-spread slippage
- tested 3 lookbacks (12, 24, 48h) × 3 thresholds (1%, 2%, 3%) = 9 combos per strategy, 18 total

### Momentum results

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

### Mean Reversion results

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

### Best per strategy
- Momentum: 24h lookback, 3% threshold → 40.7% return, 0.36 Sharpe, 119 trades
- Mean Reversion: 12h lookback, 3% threshold → 36.0% return, 0.39 Sharpe, 47 trades

### vs benchmark
- buy & hold returned +116%
- best Momentum (40.7%) captured ~35% of that
- best MeanRev (36.0%) captured ~31%
- no strategy beats buy & hold — expected in a strong trending year

### Takeaways
1. threshold matters more than lookback. across both strategies, 1% threshold consistently loses money. 3% consistently does best. tighter threshold → more trades → more fees → worse results
2. fees are the silent killer. Momentum(12h, 1%) paid $22k in fees on a $100k account and lost money. Momentum(24h, 3%) paid $5.2k and returned 40.7%. roughly 4x difference in fees between worst and best combos
3. MeanReversion(12h, 3%) has best Sharpe but only 47 trades. 87% win rate looks amazing but 47 trades is thin. standard error of Sharpe ≈ 1/√47 ≈ 0.15, so true Sharpe could be anywhere from 0.24 to 0.54. Momentum(24h, 3%) with 119 trades is more trustworthy (SE ≈ 0.09)
4. mean reversion has higher win rates but lower returns. MeanRev: 57-87% win rate. Momentum: 33-45%. but Momentum's best return (40.7%) beats MeanRev's best (36.0%). MeanRev wins many small trades but misses the big trending moves
5. both strategies struggle in trends. BTC 2024 was a +116% year. Momentum keeps exiting and re-entering. Mean Reversion actively fights the trend

### Next
- add SMA crossover and Bollinger band strategies
- walk-forward testing to check if best params are overfit
- would these results hold in a sideways or bearish market?

---
## 2026-03-26: SMA Crossover + Bollinger Bands

### Question
Do SMA crossover and Bollinger bands do better than Momentum and Mean Reversion?

### SMA Crossover
Uses two moving averages — a fast one and a slow one. When the fast crosses above the slow, buy. When it crosses below, sell. Idea is it should stay in trades longer than raw Momentum.

Tested 11 fast/slow combos. Best was SMA(24/72) with Sharpe 0.39, 22.8% return, 10.4% maxDD, 132 trades. SMA(24/96) was close behind with the lowest drawdown I've seen so far — 7.7%.

Main finding: the wider the gap between fast and slow periods, the better. Small gaps like 6/24 or 12/24 just trade on noise and lose money.

### Bollinger Bands
SMA plus/minus N standard deviations. Bands get wider when volatility is high and tighter when its low. Buy when price drops below lower band, sell when it goes above upper band. Basically adaptive mean reversion.

Tested 20 period/std combos. Best was BB(24, 2.5σ) with Sharpe 0.20, 20.0% return, 13.0% maxDD. Worst strategy so far. BB(12, 1.0σ) lost 40.8% with 848 trades — tight bands in a bull market is a disaster. BB(12, 3.0σ) only triggered 4 trades total so thats useless.

### Ranking so far (best params for each)

| Strategy | Sharpe | Return | MaxDD | Trades |
|----------|--------|--------|-------|--------|
| SMA(24/72) | 0.39 | 22.8% | 10.4% | 132 |
| MeanRev(12h, 3%) | 0.39 | 36.0% | 10.6% | 47 |
| Momentum(24h, 3%) | 0.36 | 40.7% | 10.6% | 119 |
| Bollinger(24, 2.5σ) | 0.20 | 20.0% | 13.0% | 159 |

### Takeaways
- trend following (SMA, Momentum) beats mean reversion (Bollinger, MeanRev) in a bull market. makes sense — mean reversion keeps selling into rallies
- SMA crossover has the smoothest equity curves. best risk profile of everything tested
- Bollinger bands didnt really improve on basic MeanReversion despite being "adaptive". same core problem — fighting the trend
- still nothing beats buy & hold at 116%
- same pattern everywhere: wider filters, fewer trades, less fees = better results

### Next
- walk-forward testing — are these best params overfit to 2024?
- would mean reversion win in a flat or bearish year?
---
