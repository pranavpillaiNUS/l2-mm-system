# L2 Market Microstructure System

Quantitative trading research project in two phases: an execution-aware backtesting framework (Phase 1, complete), and an L2 orderbook replay engine with market-making simulation (Phase 2, in progress).

Phase 1 backtests systematic strategies on BTC hourly bars with realistic transaction cost modeling and validates robustness using walk-forward analysis. Phase 2 replays recorded L2 depth data through a deterministic execution simulator to test market-making strategies with FIFO queue-position modeling and latency simulation.


## Key Findings

Tested 4 strategies (Momentum, Mean Reversion, SMA Crossover, Bollinger Bands) across 60+ parameter combinations on BTCUSDT 2024 hourly data, then validated with 9-window walk-forward analysis.

**Transaction costs are the dominant source of lost alpha.** Every configuration that lost money shared the same root cause: trading too frequently. Momentum with a 1% threshold paid 22% of capital in fees and lost money despite having positive gross returns. The same strategy with a 3% threshold paid 5% in fees and returned 40.7%. Correlation between trade count and net return was -0.50 across all 18 configurations.

**Parameter stability predicts out-of-sample performance better than in-sample Sharpe.** Walk-forward testing completely reshuffled the strategy ranking. SMA Crossover tied for the best in-sample Sharpe (0.39) but had the worst OOS performance — its optimal parameters shifted across 4 different combinations in 9 windows. Mean Reversion selected identical parameters in all 9 windows and its OOS Sharpe (0.51) actually exceeded in-sample (0.39).

**Trend-following and mean-reversion fail in opposite regimes.** Momentum lost money in 4 of 9 OOS months (all choppy markets). Mean Reversion never lost money but had zero trades in 2 months (both trending markets). Neither strategy detects which regime it's in.

Walk-forward OOS results (Apr–Dec 2024):

| Strategy | OOS Sharpe | Final Equity | Losing Months | Param Stability |
|---|---|---|---|---|
| Mean Reversion (12h, 3%) | 0.51 | $126k | 0/9 | 1 unique combo |
| Bollinger (24, 2.5σ) | 0.33 | $125k | 1/9 | 3 unique combos |
| Momentum (24h, 3%) | 0.24 | $128k | 4/9 | 2 unique combos |
| SMA (24/72) | 0.22 | $117k | 4/9 | 4 unique combos |
| Buy & Hold | — | ~$148k | — | — |

No strategy beat buy-and-hold in a +116% bull market year. That's expected — the strategies are designed for better risk-adjusted returns, not maximum return in a trending market.


## Architecture

### Phase 1 — Backtesting Framework

```
src/backtester/
├── config.py        # BacktestConfig, FeeConfig (maker/taker bps), SlippageConfig
├── data_loader.py   # Loads OHLCV bars from parquet/csv, provides Bar iterator
├── strategy.py      # BaseStrategy ABC + 4 implementations
├── engine.py        # BacktestEngine — runs any strategy through the data
├── portfolio.py     # Portfolio tracking, Fill execution, equity snapshots
├── metrics.py       # Sharpe, Sortino, Calmar, drawdown, trade P&L matching
└── plotting.py      # Equity curves, drawdown, return distributions
```

The engine is event-driven: it iterates through bars, passes each to the strategy's `generate_signal()` method, executes fills through the portfolio, and marks to market. Strategies inherit from `BaseStrategy` and only need to implement `generate_signal()` — the engine handles execution, cost modeling, and metric calculation.

Fee model uses basis points (configurable maker/taker). Slippage model applies spread and impact costs. Both are applied per-fill.

### Phase 1 — Analysis Pipeline

```
scripts/
├── download_bars.py        # Fetches OHLCV from Binance API
├── compare_strategies.py   # 18-combo parameter sweep (Momentum vs MeanReversion)
├── sweep_sma.py            # 11-combo SMA parameter sweep
├── sweep_bollinger.py      # 20-combo Bollinger parameter sweep
├── walk_forward.py         # 9-window anchored walk-forward, all 4 strategies
└── plot_walk_forward.py    # OOS equity curves and monthly return charts
```

Walk-forward uses anchored expanding windows: training always starts from January and grows by one month each step. The test month is strictly out-of-sample. Total: ~880 backtests across all windows and parameter combinations.

### Phase 2 — L2 Replay Engine (in progress)

```
src/recorder/
└── simple_recorder.py   # WebSocket recorder for Binance L2 depth (running 24/7 since Feb 2026)
```

The recorder captures L2 orderbook snapshots via Binance WebSocket and writes gzipped JSONL files, rotating hourly. As of April 2026, ~5.4GB of raw depth data has been collected. This data feeds the replay engine being built in summer 2026.

Planned components:
- Deterministic L2 orderbook replay from recorded depth data
- Execution simulator with FIFO queue-position modeling, latency/jitter, partial fills
- Market-making strategies (microprice/imbalance signals, volatility-adaptive spreads, inventory skew)


## Setup

```bash
# clone
git clone https://github.com/pranavpillaiNUS/l2-mm-system.git
cd l2-mm-system

# environment
conda create -n l2mm python=3.11
conda activate l2mm
pip install -r requirements.txt

# download data
python scripts/download_bars.py

# run a backtest
python scripts/compare_strategies.py

# run walk-forward analysis
python scripts/walk_forward.py
python scripts/plot_walk_forward.py
```


## Documentation

- `notebooks/research_log.md` — 5 entries covering data exploration, strategy comparison, walk-forward analysis
- `notebooks/error_analysis.md` — systematic analysis of failure modes: cost drag, regime mismatch, structural limitations
- `notebooks/lessons_learned.md` — what worked, what didn't, what carries forward to Phase 2