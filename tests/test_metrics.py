import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from src.backtester.portfolio import Portfolio, Fill, Side
from src.backtester.metrics import calculate_metrics, calculate_drawdown_series


def test_metrics_calculation():
    print("TEST: Metrics Calculation")
    
    # Create a backtest scenario
    portfolio = Portfolio(initial_cash=100_000)
    start = datetime(2024, 1, 1)
    
    # 60 days of trading
    np.random.seed(42)
    price = 50_000
    
    for day in range(60):
        # Random price movement
        price *= (1 + np.random.normal(0.001, 0.02))
        
        # Trade every 10 days
        if day > 0 and day % 10 == 0:
            if portfolio.position < 1:
                # Buy
                fill = Fill(
                    timestamp=start + timedelta(days=day),
                    side=Side.BUY,
                    price=price,
                    quantity=0.5,
                    fee=price * 0.5 * 0.001,
                    slippage_cost=price * 0.5 * 0.0005,
                )
                portfolio.execute(fill)
            else:
                # Sell
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
    
    # Calculate metrics
    equity = portfolio.get_equity_series()
    metrics = calculate_metrics(
        equity_series=equity,
        fills=portfolio.fills,
        initial_cash=portfolio.initial_cash,
        total_fees=portfolio.total_fees,
        total_slippage=portfolio.total_slippage,
        total_volume=portfolio.total_volume,
    )
    
    print("\nPERFORMANCE METRICS")
    print("-" * 40)
    for key, value in metrics.to_dict().items():
        print(f"  {key:.<35} {value}")
    
    print(f"\nSummary: {metrics.summary()}")
    
    # Verify calculations make sense
    assert metrics.num_trades == len(portfolio.fills), "Trade count mismatch"
    assert -1 <= metrics.max_drawdown <= 1, "Drawdown should be between 0 and 1"
    assert metrics.total_fees == portfolio.total_fees, "Fee mismatch"
    
    print("\nMetrics test passed!")


def test_drawdown_calculation():
    """Test drawdown series calculation."""
    print("TEST: Drawdown Calculation")
    
    # Create simple equity curve: 100 -> 120 -> 90 -> 110
    dates = pd.date_range('2024-01-01', periods=4, freq='D')
    equity = pd.Series([100, 120, 90, 110], index=dates)
    
    drawdown = calculate_drawdown_series(equity)
    
    print("\nEquity curve:")
    for date, val in equity.items():
        dd = drawdown.loc[date]
        print(f"  {date.date()}: ${val:.0f} (drawdown: {dd*100:.1f}%)")
    
    # At day 3 (value 90), drawdown from peak 120 = (90-120)/120 = -25%
    assert abs(drawdown.iloc[2] - (-0.25)) < 0.01, "Drawdown calculation wrong"
    
    print("\nDrawdown test passed!")


def test_metrics_infer_hourly_annualization_from_index():
    """Hourly observations should annualize volatility by sqrt(24) vs daily."""
    values = [100.0, 101.0, 100.5, 102.0]
    hourly = pd.Series(
        values,
        index=pd.date_range("2024-01-01", periods=len(values), freq="h"),
    )
    daily = pd.Series(
        values,
        index=pd.date_range("2024-01-01", periods=len(values), freq="D"),
    )
    common = {
        "fills": [],
        "initial_cash": 100.0,
        "total_fees": 0.0,
        "total_slippage": 0.0,
        "total_volume": 0.0,
    }

    hourly_metrics = calculate_metrics(equity_series=hourly, **common)
    daily_metrics = calculate_metrics(equity_series=daily, **common)

    assert np.isclose(
        hourly_metrics.annualized_volatility
        / daily_metrics.annualized_volatility,
        np.sqrt(24),
    )


if __name__ == "__main__":
    test_metrics_calculation()
    test_drawdown_calculation()
    
    print("\nALL METRICS TESTS PASSED!")
