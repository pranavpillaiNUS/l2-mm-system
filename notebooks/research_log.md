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