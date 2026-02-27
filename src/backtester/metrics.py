import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class PerformanceMetrics:
    # Returns
    total_return: float
    total_return_pct: float
    annualized_return: float
    
    # Risk
    annualized_volatility: float
    max_drawdown: float
    max_drawdown_duration_days: float
    
    # Risk-adjusted
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    
    # Trading
    num_trades: int
    win_rate: float
    profit_factor: float
    avg_trade_pnl: float
    
    # Costs
    total_fees: float
    total_slippage: float
    total_volume: float
    
    def to_dict(self) -> dict:
        return {
            "Total Return (%)": f"{self.total_return_pct:.2f}",
            "Annualized Return (%)": f"{self.annualized_return * 100:.2f}",
            "Annualized Volatility (%)": f"{self.annualized_volatility * 100:.2f}",
            "Max Drawdown (%)": f"{self.max_drawdown * 100:.2f}",
            "Max DD Duration (days)": f"{self.max_drawdown_duration_days:.0f}",
            "Sharpe Ratio": f"{self.sharpe_ratio:.2f}",
            "Sortino Ratio": f"{self.sortino_ratio:.2f}",
            "Calmar Ratio": f"{self.calmar_ratio:.2f}",
            "Number of Trades": f"{self.num_trades}",
            "Win Rate (%)": f"{self.win_rate * 100:.1f}",
            "Profit Factor": f"{self.profit_factor:.2f}",
            "Avg Trade P&L ($)": f"{self.avg_trade_pnl:.2f}",
            "Total Fees ($)": f"{self.total_fees:.2f}",
            "Total Slippage ($)": f"{self.total_slippage:.2f}",
        }
    
    def summary(self) -> str:
        return (
            f"Return: {self.total_return_pct:.1f}% | "
            f"Sharpe: {self.sharpe_ratio:.2f} | "
            f"MaxDD: {self.max_drawdown * 100:.1f}% | "
            f"WinRate: {self.win_rate * 100:.0f}%"
        )


def calculate_drawdown_series(equity_series: pd.Series) -> pd.Series:
    cummax = equity_series.cummax()
    drawdown = (equity_series - cummax) / cummax
    return drawdown


def calculate_max_drawdown_duration(equity_series: pd.Series) -> float:
    cummax = equity_series.cummax()
    drawdown = (equity_series - cummax) / cummax
    
    if not (drawdown < 0).any():
        return 0.0
    
    # Find max drawdown point
    dd_end_idx = drawdown.idxmin()
    
    # Find when it started (last peak before)
    before_dd = equity_series.loc[:dd_end_idx]
    dd_start_idx = before_dd.idxmax()
    
    # Find recovery (if any)
    after_dd = equity_series.loc[dd_end_idx:]
    peak_value = equity_series.loc[dd_start_idx]
    recovered = after_dd >= peak_value
    
    if recovered.any():
        dd_recovery_idx = recovered.idxmax()
        duration = (dd_recovery_idx - dd_start_idx).days
    else:
        duration = (equity_series.index[-1] - dd_start_idx).days
    
    return float(duration)


def calculate_trade_pnls(fills: List) -> List[float]:
    from src.backtester.portfolio import Side
    
    pnls = []
    open_buys = []  # (price, qty, costs)
    
    for fill in fills:
        if fill.side == Side.BUY:
            open_buys.append((fill.price, fill.quantity, fill.fee + fill.slippage_cost))
        elif fill.side == Side.SELL and open_buys:
            # Match against oldest buy
            sell_remaining = fill.quantity
            sell_costs = fill.fee + fill.slippage_cost
            
            while sell_remaining > 0 and open_buys:
                buy_price, buy_qty, buy_costs = open_buys[0]
                matched_qty = min(sell_remaining, buy_qty)
                
                # P&L = (sell - buy) × qty - costs
                cost_fraction = matched_qty / fill.quantity
                pnl = (fill.price - buy_price) * matched_qty
                pnl -= buy_costs * (matched_qty / buy_qty)
                pnl -= sell_costs * cost_fraction
                pnls.append(pnl)
                
                sell_remaining -= matched_qty
                if matched_qty >= buy_qty:
                    open_buys.pop(0)
                else:
                    remaining_buy = buy_qty - matched_qty
                    remaining_costs = buy_costs * (remaining_buy / buy_qty)
                    open_buys[0] = (buy_price, remaining_buy, remaining_costs)
    
    return pnls


def calculate_metrics(
    equity_series: pd.Series,
    fills: List,
    initial_cash: float,
    total_fees: float,
    total_slippage: float,
    total_volume: float,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 365,
) -> PerformanceMetrics:
    if len(equity_series) < 2:
        raise ValueError("Need at least 2 equity points")
    
    # === Returns ===
    final_equity = equity_series.iloc[-1]
    total_return = (final_equity - initial_cash) / initial_cash
    total_return_pct = total_return * 100
    
    # Time period
    days = (equity_series.index[-1] - equity_series.index[0]).days
    years = max(days / 365, 1/365)
    
    # CAGR
    annualized_return = (1 + total_return) ** (1 / years) - 1
    
    # Daily returns
    returns = equity_series.pct_change().dropna()
    daily_vol = returns.std()
    annualized_volatility = daily_vol * np.sqrt(periods_per_year)
    
    # === Drawdown ===
    drawdown = calculate_drawdown_series(equity_series)
    max_drawdown = abs(drawdown.min())
    max_drawdown_duration_days = calculate_max_drawdown_duration(equity_series)
    
    # === Risk-Adjusted Ratios ===
    excess_daily = returns.mean() - (risk_free_rate / periods_per_year)
    
    # Sharpe
    sharpe_ratio = (excess_daily / daily_vol * np.sqrt(periods_per_year)) if daily_vol > 0 else 0.0
    
    # Sortino
    downside = returns[returns < 0]
    downside_std = downside.std() if len(downside) > 1 else daily_vol
    sortino_ratio = (excess_daily / downside_std * np.sqrt(periods_per_year)) if downside_std > 0 else 0.0
    
    # Calmar
    calmar_ratio = annualized_return / max_drawdown if max_drawdown > 0 else 0.0
    
    # === Trade Analysis ===
    num_trades = len(fills)
    trade_pnls = calculate_trade_pnls(fills)
    
    if trade_pnls:
        winners = [p for p in trade_pnls if p > 0]
        losers = [p for p in trade_pnls if p < 0]
        
        win_rate = len(winners) / len(trade_pnls)
        avg_trade_pnl = np.mean(trade_pnls)
        
        gross_profit = sum(winners) if winners else 0
        gross_loss = abs(sum(losers)) if losers else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    else:
        win_rate = 0.0
        avg_trade_pnl = 0.0
        profit_factor = 0.0
    
    return PerformanceMetrics(
        total_return=total_return,
        total_return_pct=total_return_pct,
        annualized_return=annualized_return,
        annualized_volatility=annualized_volatility,
        max_drawdown=max_drawdown,
        max_drawdown_duration_days=max_drawdown_duration_days,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        calmar_ratio=calmar_ratio,
        num_trades=num_trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_trade_pnl=avg_trade_pnl,
        total_fees=total_fees,
        total_slippage=total_slippage,
        total_volume=total_volume,
    )