import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

from src.backtester.portfolio import Portfolio, Fill, Side
from src.backtester.metrics import calculate_metrics
from src.backtester.plotting import (
    plot_backtest_results, 
    plot_returns_distribution,
    plot_metrics_table,
)

def create_sample_backtest():
    portfolio = Portfolio(initial_cash=100_000)
    start = datetime(2024, 1, 1)
    
    np.random.seed(42)
    price = 50_000
    prices = [price]
    
    for day in range(90):
        price *= (1 + np.random.normal(0.0005, 0.02))
        prices.append(price)    
        if day > 0 and day % 12 == 0:
            if portfolio.position < 1.5:
                fill = Fill(
                    timestamp=start + timedelta(days=day),
                    side=Side.BUY,
                    price=price,
                    quantity=0.5,
                    fee=price * 0.5 * 0.001,
                    slippage_cost=price * 0.5 * 0.0005,
                )
                portfolio.execute(fill)
            elif portfolio.position > 0:
                fill = Fill(
                    timestamp=start + timedelta(days=day),
                    side=Side.SELL,
                    price=price,
                    quantity=0.5,
                    fee=price * 0.5 * 0.001,
                    slippage_cost=price * 0.5 * 0.0005,
                )
                portfolio.execute(fill)
        
        portfolio.mark_to_market(start + timedelta(days=day), price)
    
    return portfolio, prices


def test_all_plots():
    print("=" * 60)
    print("TEST: Plotting Module")
    print("=" * 60)
    
    # Create output directory
    output_dir = Path("results/test_plots")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Get sample data
    portfolio, prices = create_sample_backtest()
    equity = portfolio.get_equity_series()
    fills_df = portfolio.get_fills_df()
    
    # Create benchmark (buy and hold)
    benchmark = pd.Series(
        data=[p / prices[1] * 100_000 for p in prices[1:]],
        index=equity.index,
    )
    
    # Calculate metrics
    metrics = calculate_metrics(
        equity_series=equity,
        fills=portfolio.fills,
        initial_cash=portfolio.initial_cash,
        total_fees=portfolio.total_fees,
        total_slippage=portfolio.total_slippage,
        total_volume=portfolio.total_volume,
    )
    
    print("\n1. Generating equity curve plot...")
    plot_backtest_results(
        equity_series=equity,
        fills_df=fills_df,
        benchmark=benchmark,
        title="Sample Backtest Results",
        save_path=output_dir / "equity_curve.png",
    )
    
    print("2. Generating returns distribution...")
    plot_returns_distribution(
        equity_series=equity,
        title="Daily Returns Distribution",
        save_path=output_dir / "returns_dist.png",
    )
    
    print("3. Generating metrics table...")
    plot_metrics_table(
        metrics_dict=metrics.to_dict(),
        title="Performance Metrics",
        save_path=output_dir / "metrics_table.png",
    )
    
    print(f"\nAll plots saved to: {output_dir.absolute()}")
    print("\nPlotting tests passed!")


if __name__ == "__main__":
    test_all_plots()