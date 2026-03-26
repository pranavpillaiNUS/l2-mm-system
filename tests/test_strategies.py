"""
Test strategy interface and compare strategies.
"""
from pathlib import Path
from src.backtester.config import BacktestConfig
from src.backtester.engine import BacktestEngine
from src.backtester.strategy import MomentumStrategy, MeanReversionStrategy, SMACrossoverStrategy

def test_momentum():
    print("Testing Momentum...")
    config = BacktestConfig(
        data_path=Path("data/bars"),
        symbol="BTCUSDT",
        initial_cash=100_000,
    )
    engine = BacktestEngine(config)
    result = engine.run(MomentumStrategy(lookback=24, threshold=0.02))
    print("Momentum passed!\n")
    return result

def test_mean_reversion():
    print("Testing Mean Reversion...")
    config = BacktestConfig(
        data_path=Path("data/bars"),
        symbol="BTCUSDT",
        initial_cash=100_000,
    )
    engine = BacktestEngine(config)
    result = engine.run(MeanReversionStrategy(lookback=24, threshold=0.02))
    print("Mean reversion passed!\n")
    return result

def test_sma_crossover():
    print("Testing SMA Crossover...")
    config = BacktestConfig(
        data_path=Path("data/bars"),
        symbol="BTCUSDT",
        initial_cash=100_000,
    )
    engine = BacktestEngine(config)
    result = engine.run(SMACrossoverStrategy(fast_period=12, slow_period=48))
    print("SMA crossover passed!\n")
    return result

if __name__ == "__main__":
    mom_result = test_momentum()
    mr_result = test_mean_reversion()
    sma_result = test_sma_crossover()
    
    print("\nCOMPARISON")
    print(f"{'Strategy':<30} {'Return':>10} {'Sharpe':>10} {'MaxDD':>10} {'Trades':>10}")
    print("-" * 70)
    for result in [mom_result, mr_result, sma_result]:
        m = result.metrics
        print(f"{result.strategy_name:<30} {m.total_return_pct:>9.1f}% {m.sharpe_ratio:>10.2f} {m.max_drawdown*100:>9.1f}% {m.num_trades:>10}")