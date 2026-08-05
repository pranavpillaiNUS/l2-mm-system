"""
Backtest engine that runs my different strategies.

Usage:
    engine = BacktestEngine(config)
    results = engine.run(MomentumStrategy())
    results = engine.run(MeanReversionStrategy())
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import pandas as pd

from .config import BacktestConfig
from .data_loader import DataLoader
from .portfolio import Portfolio, Fill, Side
from .metrics import calculate_metrics, PerformanceMetrics
from .strategy import BaseStrategy, Signal, TradeSignal


@dataclass
class BacktestResult:
    """Container for backtest results."""
    strategy_name: str
    metrics: PerformanceMetrics
    equity_series: pd.Series
    fills_df: pd.DataFrame
    signals: list
    config: BacktestConfig


class BacktestEngine:
    """
    Runs backtests for any strategy.
    
    Usage:
        engine = BacktestEngine(config)
        result = engine.run(MomentumStrategy(lookback=24))
    """
    
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.loader = DataLoader(config.data_path)
    
    def run(
        self,
        strategy: BaseStrategy,
        start: Optional[str] = None,
        end: Optional[str] = None,
        verbose: bool = True,
    ) -> BacktestResult:
        """
        Run backtest for a strategy.
        
        Args:
            strategy: Strategy instance to test
            start: Start date (optional)
            end: End date (optional)
            verbose: Print progress
        
        Returns:
            BacktestResult with metrics, equity curve, fills
        """
        if verbose:
            print(f"\n{'='*60}")
            print(f"BACKTEST: {strategy.name}")
            print(f"{'='*60}")
        
        # Load data
        df = self.loader.load(
            self.config.symbol,
            start=start or self.config.start_date,
            end=end or self.config.end_date,
        )
        if verbose:
            print(f"\nData: {len(df):,} bars ({df.index[0].date()} to {df.index[-1].date()})")
        
        # Initialize portfolio
        portfolio = Portfolio(initial_cash=self.config.initial_cash)
        
        # Run through each bar
        signals = []
        for i in range(len(df)):
            timestamp = df.index[i]
            row = df.iloc[i]
            history = df.iloc[:i+1]
            
            # Get signal from strategy
            signal = strategy.generate_signal(
                timestamp=timestamp,
                row=row,
                position=portfolio.position,
                history=history,
            )
            signals.append(signal)
            
            # Execute trades based on signal
            if signal.signal == Signal.BUY and portfolio.position < self.config.max_position:
                self._execute_buy(portfolio, signal, row)
                strategy.on_fill(portfolio.fills[-1])
                
            elif signal.signal == Signal.SELL and portfolio.position > 0:
                self._execute_sell(portfolio, signal, row)
                strategy.on_fill(portfolio.fills[-1])
            
            # Mark to market
            portfolio.mark_to_market(timestamp, row["close"])
        
        # Notify strategy backtest ended
        strategy.on_backtest_end()
        
        # Calculate metrics
        equity = portfolio.get_equity_series()
        metrics = calculate_metrics(
            equity_series=equity,
            fills=portfolio.fills,
            initial_cash=self.config.initial_cash,
            total_fees=portfolio.total_fees,
            total_slippage=portfolio.total_slippage,
            total_volume=portfolio.total_volume,
        )
        
        if verbose:
            print(f"\nTrades: {portfolio.num_trades}")
            print(f"Result: {metrics.summary()}")
        
        return BacktestResult(
            strategy_name=strategy.name,
            metrics=metrics,
            equity_series=equity,
            fills_df=portfolio.get_fills_df(),
            signals=signals,
            config=self.config,
        )
    
    def _execute_buy(self, portfolio: Portfolio, signal: TradeSignal, row: pd.Series):
        """Execute a buy order."""
        price = row["close"]
        quantity = signal.target_quantity or 0.5
        
        fill = Fill(
            timestamp=signal.timestamp,
            side=Side.BUY,
            price=price,
            quantity=quantity,
            fee=price * quantity * self.config.fees.taker_rate,
            slippage_cost=price * quantity * self.config.slippage.spread_bps / 10000,
            reason=signal.reason,
        )
        portfolio.execute(fill)
    
    def _execute_sell(self, portfolio: Portfolio, signal: TradeSignal, row: pd.Series):
        """Execute a sell order."""
        price = row["close"]
        quantity = signal.target_quantity or portfolio.position
        
        fill = Fill(
            timestamp=signal.timestamp,
            side=Side.SELL,
            price=price,
            quantity=quantity,
            fee=price * quantity * self.config.fees.taker_rate,
            slippage_cost=price * quantity * self.config.slippage.spread_bps / 10000,
            reason=signal.reason,
        )
        portfolio.execute(fill)
