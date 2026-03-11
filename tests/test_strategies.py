"""
Test strategy interface and compare strategies.
"""
from pathlib import Path
from src.backtester.config import BacktestConfig
from src.backtester.engine import BacktestEngine
from src.backtester.strategy import MomentumStrategy, MeanReversionStrategy


def test_momentum():
    """Test momentum strategy."""
    print("=" * 60)
    print("TEST: Momentum Strategy")
    print("=" * 60)
    
    config = BacktestConfig(
        data_path=Path("data/bars"),
        symbol="BTCUSDT",
        initial_cash=100_000,
    )
    
    engine = BacktestEngine(config)
    result = engine.run(MomentumStrategy(lookback=24, threshold=0.02))
    
    print(f"\nMomentum test passed!")
    return result


def test_mean_reversion():
    """Test mean reversion strategy."""
    print("\n" + "=" * 60)
    print("TEST: Mean Reversion Strategy")
    print("=" * 60)
    
    config = BacktestConfig(
        data_path=Path("data/bars"),
        symbol="BTCUSDT",
        initial_cash=100_000,
    )
    
    engine = BacktestEngine(config)
    result = engine.run(MeanReversionStrategy(lookback=24, threshold=0.02))
    
    print(f"\nMean reversion test passed!")
    return result


if __name__ == "__main__": """
Test strategy interface and compare strategies.
"""
from pathlib import Path
from src.backtester.config import BacktestConfig
from src.backtester.engine import BacktestEngine
from src.backtester.strategy import MomentumStrategy, MeanReversionStrategy


def test_momentum():
    """Test momentum strategy."""
    print("=" * 60)
    print("TEST: Momentum Strategy")
    print("=" * 60)
    
    config = BacktestConfig(
        data_path=Path("data/bars"),
        symbol="BTCUSDT",
        initial_cash=100_000,
    )
    
    engine = BacktestEngine(config)
    result = engine.run(MomentumStrategy(lookback=24, threshold=0.02))
    
    print(f"\nMomentum test passed!")
    return result


def test_mean_reversion():
    """Test mean reversion strategy."""
    print("\n" + "=" * 60)
    print("TEST: Mean Reversion Strategy")
    print("=" * 60)
    
    config = BacktestConfig(
        data_path=Path("data/bars"),
        symbol="BTCUSDT",
        initial_cash=100_000,
    )
    
    engine = BacktestEngine(config)
    result = engine.run(MeanReversionStrategy(lookback=24, threshold=0.02))
    
    print(f"\nMean reversion test passed!")
    return result


if __name__ == "__main__":
    mom_result = test_momentum()
    mr_result = test_mean_reversion()
    
    print("\n" + "=" * 60)
    print("COMPARISON")
    print("=" * 60)
    print(f"\n{'Strategy':<30} {'Return':>10} {'Sharpe':>10} {'MaxDD':>10} {'Trades':>10}")
    print("-" * 70)
    
    for result in [mom_result, mr_result]:
        m = result.metrics
        print(f"{result.strategy_name:<30} {m.total_return_pct:>9.1f}% {m.sharpe_ratio:>10.2f} {m.max_drawdown*100:>9.1f}% {m.num_trades:>10}")
    mom_result = test_momentum()
    mr_result = test_mean_reversion()
    
    print("\n" + "=" * 60)
    print("COMPARISON")
    print("=" * 60)
    print(f"\n{'Strategy':<30} {'Return':>10} {'Sharpe':>10} {'MaxDD':>10} {'Trades':>10}")
    print("-" * 70)
    
    for result in [mom_result, mr_result]:
        m = result.metrics
        print(f"{result.strategy_name:<30} {m.total_return_pct:>9.1f}% {m.sharpe_ratio:>10.2f} {m.max_drawdown*100:>9.1f}% {m.num_trades:>10}")