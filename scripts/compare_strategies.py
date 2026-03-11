"""
Compare multiple strategies and save results.
"""
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
from src.backtester.config import BacktestConfig
from src.backtester.engine import BacktestEngine
from src.backtester.strategy import MomentumStrategy, MeanReversionStrategy
from src.backtester.plotting import plot_backtest_results


def compare_strategies():
    print("=" * 60)
    print("STRATEGY COMPARISON")
    print("=" * 60)
    
    output_dir = Path("results/strategy_comparison")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    config = BacktestConfig(
        data_path=Path("data/bars"),
        symbol="BTCUSDT",
        initial_cash=100_000,
    )
    
    engine = BacktestEngine(config)
    
    # Define strategies to test
    # Testing 3 lookback × 3 threshold combos per strategy = 18 runs
    # This gives enough coverage to spot trends without a full grid search
    strategies = [
        # Momentum: short, medium, long lookback × tight, medium, wide threshold
        MomentumStrategy(lookback=12, threshold=0.01),
        MomentumStrategy(lookback=12, threshold=0.02),
        MomentumStrategy(lookback=12, threshold=0.03),
        MomentumStrategy(lookback=24, threshold=0.01),
        MomentumStrategy(lookback=24, threshold=0.02),
        MomentumStrategy(lookback=24, threshold=0.03),
        MomentumStrategy(lookback=48, threshold=0.01),
        MomentumStrategy(lookback=48, threshold=0.02),
        MomentumStrategy(lookback=48, threshold=0.03),
        # Mean Reversion: same parameter grid
        MeanReversionStrategy(lookback=12, threshold=0.01),
        MeanReversionStrategy(lookback=12, threshold=0.02),
        MeanReversionStrategy(lookback=12, threshold=0.03),
        MeanReversionStrategy(lookback=24, threshold=0.01),
        MeanReversionStrategy(lookback=24, threshold=0.02),
        MeanReversionStrategy(lookback=24, threshold=0.03),
        MeanReversionStrategy(lookback=48, threshold=0.01),
        MeanReversionStrategy(lookback=48, threshold=0.02),
        MeanReversionStrategy(lookback=48, threshold=0.03),
    ]
    
    # Run all backtests
    results = []
    for i, strategy in enumerate(strategies, 1):
        print(f"\n[{i}/{len(strategies)}] Running {strategy.name}...")
        result = engine.run(strategy, verbose=True)
        results.append(result)
    
    # Create comparison table
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    
    rows = []
    for r in results:
        rows.append({
            "Strategy": r.strategy_name,
            "Return (%)": f"{r.metrics.total_return_pct:.1f}",
            "Sharpe": f"{r.metrics.sharpe_ratio:.2f}",
            "Sortino": f"{r.metrics.sortino_ratio:.2f}",
            "Max DD (%)": f"{r.metrics.max_drawdown * 100:.1f}",
            "Trades": r.metrics.num_trades,
            "Win Rate (%)": f"{r.metrics.win_rate * 100:.1f}",
            "Fees ($)": f"{r.metrics.total_fees:.0f}",
        })
    
    df_results = pd.DataFrame(rows)
    print("\n" + df_results.to_string(index=False))
    
    # Save to CSV
    df_results.to_csv(output_dir / "comparison.csv", index=False)
    print(f"\nSaved comparison to {output_dir}/comparison.csv")
    
    # Plot equity curves
    fig, ax = plt.subplots(figsize=(12, 6))
    
    for r in results:
        ax.plot(r.equity_series.index, r.equity_series.values, 
                label=r.strategy_name, linewidth=1)
    
    # Add buy & hold benchmark
    from src.backtester.data_loader import DataLoader
    loader = DataLoader(Path("data/bars"))
    df = loader.load("BTCUSDT")
    benchmark = df["close"] / df["close"].iloc[0] * 100_000
    ax.plot(benchmark.index, benchmark.values, 
            label="Buy & Hold", linewidth=2, linestyle="--", color="black")
    
    ax.set_title("Strategy Comparison: Equity Curves")
    ax.set_ylabel("Equity ($)")
    ax.set_xlabel("Date")
    ax.legend(loc="upper left", fontsize=7)  # Smaller font since we have 18 strategies now
    ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(output_dir / "equity_comparison.png", dpi=150)
    print(f"Saved plot to {output_dir}/equity_comparison.png")
    plt.close()
    
    # === Find best strategy ===
    best = max(results, key=lambda r: r.metrics.sharpe_ratio)
    print(f"\nBest Sharpe: {best.strategy_name} ({best.metrics.sharpe_ratio:.2f})")
    
    return results


if __name__ == "__main__":
    compare_strategies()