from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime
from enum import Enum
import pandas as pd

class Side(Enum):
    BUY = "buy"
    SELL = "sell"
    def __str__(self) -> str:
        return self.value

@dataclass
class Fill:
    timestamp: datetime
    side: Side
    price: float
    quantity: float
    fee: float
    slippage_cost: float
    is_maker: bool = True
    order_id: Optional[str] = None
    reason: Optional[str] = None

    @property
    def notional(self) -> float:
        return self.price * self.quantity

    @property
    def total_cost(self) -> float:
        gross = self.price * self.quantity
        costs = self.fee + self.slippage_cost
        if self.side == Side.BUY:
            return -(gross + costs)
        else:
            return gross - costs
        
    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "side": str(self.side),
            "price": self.price,
            "quantity": self.quantity,
            "notional": self.notional,
            "fee": self.fee,
            "slippage_cost": self.slippage_cost,
            "total_cost": self.total_cost,
            "is_maker": self.is_maker,
            "reason": self.reason,
        }

@dataclass
class EquitySnapshot:
    timestamp: datetime
    mark_price: float
    cash: float
    position: float
    equity: float
    unrealized_pnl: float

@dataclass
class Portfolio:
    initial_cash: float

    # current state
    cash: float = field(init=False)
    position: float = 0.0
    avg_entry_price: float = 0.0

    # History
    fills: List[Fill] = field(default_factory=list)
    equity_curve: List[EquitySnapshot] = field(default_factory=list)

    # PnL tracking
    _realized_pnl: float = field(default=0.0, init=False)

    def __post_init__(self):
        self.cash = self.initial_cash
    
    def execute(self, fill: Fill) -> None:
        self.fills.append(fill)
        self.cash += fill.total_cost
        
        old_position = self.position
        
        if fill.side == Side.BUY:
            self._handle_buy(fill, old_position)
        else:
            self._handle_sell(fill, old_position)

    def _handle_buy(self, fill: Fill, old_position: float) -> None:
        """Update position and average entry after a buy fill."""
        if old_position >= 0:
            # Adding to long or opening new long
            total_cost = self.avg_entry_price * old_position + fill.price * fill.quantity
            self.position = old_position + fill.quantity
            if self.position > 0:
                self.avg_entry_price = total_cost / self.position
        else:
            # Covering an existing short position.
            cover_qty = min(fill.quantity, abs(old_position))
            self._realized_pnl += (self.avg_entry_price - fill.price) * cover_qty
            self.position = old_position + fill.quantity
            
            if self.position > 0:
                self.avg_entry_price = fill.price
            elif self.position == 0:
                self.avg_entry_price = 0.0

    def _handle_sell(self, fill: Fill, old_position: float) -> None:
        """Update position and average entry after a sell fill."""
        if old_position <= 0:
            total_cost = abs(self.avg_entry_price * old_position) + fill.price * fill.quantity
            self.position = old_position - fill.quantity
            if self.position < 0:
                self.avg_entry_price = total_cost / abs(self.position)
        else:
            close_qty = min(fill.quantity, old_position)
            self._realized_pnl += (fill.price - self.avg_entry_price) * close_qty
            self.position = old_position - fill.quantity

            if self.position < 0:
                self.avg_entry_price = fill.price
            elif self.position == 0:
                self.avg_entry_price = 0.0
    
    def mark_to_market(self, timestamp: datetime, price: float) -> EquitySnapshot:
        if self.position > 0:
            unrealized = (price - self.avg_entry_price) * self.position
        elif self.position < 0:
            unrealized = (self.avg_entry_price - price) * abs(self.position)
        else:
            unrealized = 0.0
        
        equity = self.cash + self.position * price
        
        snapshot = EquitySnapshot(
            timestamp=timestamp,
            mark_price=price,
            cash=self.cash,
            position=self.position,
            equity=equity,
            unrealized_pnl=unrealized,
        )
        
        self.equity_curve.append(snapshot)
        return snapshot
    
    @property
    def num_trades(self) -> int:
        return len(self.fills)
    
    @property
    def total_fees(self) -> float:
        return sum(f.fee for f in self.fills)
    
    @property
    def total_slippage(self) -> float:
        return sum(f.slippage_cost for f in self.fills)
    
    @property
    def total_volume(self) -> float:
        return sum(f.notional for f in self.fills)
    
    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl
    
    def get_equity_series(self) -> pd.Series:
        """Return equity curve as pandas Series."""
        if not self.equity_curve:
            return pd.Series(dtype=float)
        
        return pd.Series(
            data=[s.equity for s in self.equity_curve],
            index=pd.DatetimeIndex([s.timestamp for s in self.equity_curve]),
            name="equity",
        )
    
    def get_fills_df(self) -> pd.DataFrame:
        if not self.fills:
            return pd.DataFrame()
        return pd.DataFrame([f.to_dict() for f in self.fills])
    
    def summary(self) -> str:
        equity = self.equity_curve[-1].equity if self.equity_curve else self.initial_cash
        pnl = equity - self.initial_cash
        return f"Portfolio(equity=${equity:,.2f}, pnl=${pnl:,.2f}, pos={self.position:.4f}, trades={self.num_trades})"
