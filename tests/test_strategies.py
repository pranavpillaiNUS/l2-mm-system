"""
Smoke test for the original bar-based strategies.

This file must not construct a BacktestEngine at import time. CI does not ship
the local `data/bars` directory, so data-dependent work has to happen inside a
test body or manual entry point.
"""
from pathlib import Path

from src.backtester.config import BacktestConfig
from src.backtester.engine import BacktestEngine
from src.backtester.strategy import (
    BollingerBandStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    SMACrossoverStrategy,
)


def _strategy_instances():
    return [
        MomentumStrategy(lookback=24, threshold=0.02),
        MeanReversionStrategy(lookback=24, threshold=0.02),
        SMACrossoverStrategy(fast_period=12, slow_period=48),
        BollingerBandStrategy(period=24, num_std=2.0),
    ]


def _run_strategy_comparison(data_path: Path, *, verbose: bool):
    config = BacktestConfig(data_path=data_path, symbol="BTCUSDT", initial_cash=100_000)
    engine = BacktestEngine(config)

    results = []
    for strategy in _strategy_instances():
        if verbose:
            print(f"running {strategy.name}...")
        result = engine.run(strategy, verbose=False)
        results.append(result)
        if verbose:
            metrics = result.metrics
            print(
                f"  return={metrics.total_return_pct:.1f}% "
                f"sharpe={metrics.sharpe_ratio:.2f} "
                f"maxdd={metrics.max_drawdown * 100:.1f}% "
                f"trades={metrics.num_trades}\n"
            )
    return results


def _print_summary(results) -> None:
    print(f"{'Strategy':<30} {'Return':>10} {'Sharpe':>10} {'MaxDD':>10} {'Trades':>10}")
    print("-" * 70)
    for result in results:
        metrics = result.metrics
        print(
            f"{result.strategy_name:<30} "
            f"{metrics.total_return_pct:>9.1f}% "
            f"{metrics.sharpe_ratio:>10.2f} "
            f"{metrics.max_drawdown * 100:>9.1f}% "
            f"{metrics.num_trades:>10}"
        )


def test_strategy_comparison_smoke(bar_data_dir: Path) -> None:
    results = _run_strategy_comparison(bar_data_dir, verbose=False)

    assert len(results) == 4
    assert all(not result.equity_series.empty for result in results)
    assert {result.strategy_name for result in results} == {
        "Momentum(24h, 2.0%)",
        "MeanReversion(24h, 2.0%)",
        "SMA(12/48)",
        "Bollinger(24, 2.0 std)",
    }


if __name__ == "__main__":
    local_data_path = Path("data/bars")
    if not local_data_path.exists():
        raise SystemExit("data/bars not present; cannot run strategy comparison")
    _print_summary(_run_strategy_comparison(local_data_path, verbose=True))
