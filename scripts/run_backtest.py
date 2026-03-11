"""
A backtest; end-to-end
Data -> Strategy -> Portfolio -> Metrics -> Plots
"""
import pandas as pd
import numpy as np
from pathlib import Path

from src.backtester.config import BacktestConfig
from src.backtester.data_loader import DataLoader
from src.backtester.portfolio import Portfolio, Fill, Side
from src.backtester.metrics import calculate_metrics
from src.backtester.plotting import plot_backtest_results, plot_metrics_table


def simple_momentum_strategy(df: pd.DataFrame, portfolio: Portfolio):
    """
    Basic momentum strat:
    - Price up >2% in last 24 hours == Buy
    - Price down >2% in last 24 hours == Sell
    
    FYI!!! This is NOT a good strategy - just testing the pipeline!
    """
    lookback = 24  # hours
    threshold = 0.02  # 2%
    
    closes = df["close"].values
    timestamps = df.index
    
    for i in range(lookback, len(df)):
        current_price = closes[i]
        past_price = closes[i - lookback]
        returns = (current_price - past_price) / past_price
        timestamp = timestamps[i]
        
        if returns > threshold and portfolio.position < 1: # Buy signal
            fill = Fill(
                timestamp=timestamp,
                side=Side.BUY,
                price=current_price,
                quantity=0.5,
                fee=current_price * 0.5 * 0.001,
                slippage_cost=current_price * 0.5 * 0.0005,
                reason="momentum_buy",
            )
            portfolio.execute(fill)
            
        elif returns < -threshold and portfolio.position > 0: # Sell signal
            fill = Fill(
                timestamp=timestamp,
                side=Side.SELL,
                price=current_price,
                quantity=portfolio.position,
                fee=current_price * portfolio.position * 0.001,
                slippage_cost=current_price * portfolio.position * 0.0005,
                reason="momentum_sell",
            )
            portfolio.execute(fill)
        
        # Mark to market every bar
        portfolio.mark_to_market(timestamp, current_price)


def run_backtest():
    print("=" * 60)
    print("BACKTEST: Simple Momentum Strategy")
    print("=" * 60)
    
    output_dir = Path("results/momentum_test")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. load data
    print("\n1. Loading data")
    loader = DataLoader(Path("data/bars"))
    df = loader.load("BTCUSDT")
    print(f"   Loaded {len(df):,} bars")
    
    # 2. create portfolio
    print("\n2. Initializing portfolio")
    portfolio = Portfolio(initial_cash=100_000)
    print(f"   Starting cash: $100,000")
    
    # 3. run strategy
    print("\n3. Running strategy")
    simple_momentum_strategy(df, portfolio)
    print(f"   Total trades: {portfolio.num_trades}")
    
    # 4. calculate metrics
    print("\n4. Calculating metrics")
    equity = portfolio.get_equity_series()
    
    metrics = calculate_metrics(
        equity_series=equity,
        fills=portfolio.fills,
        initial_cash=portfolio.initial_cash,
        total_fees=portfolio.total_fees,
        total_slippage=portfolio.total_slippage,
        total_volume=portfolio.total_volume,
    )
    
    # 5. print results
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for key, value in metrics.to_dict().items():
        print(f"   {key}: {value}")
    
    print(f"\nSummary: {metrics.summary()}")
    
    # 6. create plots
    print("\n5. Generating plots")
    
    # Benchmark = buy and hold
    benchmark = df["close"] / df["close"].iloc[0] * 100_000
    benchmark = benchmark.reindex(equity.index, method="ffill")
    
    plot_backtest_results(
        equity_series=equity,
        fills_df=portfolio.get_fills_df(),
        benchmark=benchmark,
        title="Momentum Strategy vs Buy & Hold",
        save_path=output_dir / "equity_curve.png",
    )
    
    plot_metrics_table(
        metrics_dict=metrics.to_dict(),
        title="Momentum Strategy Metrics",
        save_path=output_dir / "metrics_table.png",
    )
    
    print(f"\nResults saved to {output_dir}/")
    
    return metrics


if __name__ == "__main__":
    run_backtest()