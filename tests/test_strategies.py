"""
Test all strategies and compare them.
"""
from pathlib import Path
from src.backtester.config import BacktestConfig
from src.backtester.engine import BacktestEngine
from src.backtester.strategy import (
    MomentumStrategy, MeanReversionStrategy,
    SMACrossoverStrategy, BollingerBandStrategy,
)

config = BacktestConfig(data_path=Path("data/bars"), symbol="BTCUSDT", initial_cash=100_000)
engine = BacktestEngine(config)

results = []
strategies = [
    MomentumStrategy(lookback=24, threshold=0.02),
    MeanReversionStrategy(lookback=24, threshold=0.02),
    SMACrossoverStrategy(fast_period=12, slow_period=48),
    BollingerBandStrategy(period=24, num_std=2.0),
]

for s in strategies:
    print(f"running {s.name}...")
    result = engine.run(s, verbose=False)
    results.append(result)
    m = result.metrics
    print(f"  return={m.total_return_pct:.1f}% sharpe={m.sharpe_ratio:.2f} maxdd={m.max_drawdown*100:.1f}% trades={m.num_trades}\n")

print(f"{'Strategy':<30} {'Return':>10} {'Sharpe':>10} {'MaxDD':>10} {'Trades':>10}")
print("-" * 70)
for r in results:
    m = r.metrics
    print(f"{r.strategy_name:<30} {m.total_return_pct:>9.1f}% {m.sharpe_ratio:>10.2f} {m.max_drawdown*100:>9.1f}% {m.num_trades:>10}")