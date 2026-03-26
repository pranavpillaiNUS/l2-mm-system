"""
Strategy interface for backtester.

All strategies inherit from BaseStrategy and implement:
- generate_signal(): Look at data, decide buy/sell/hold

This makes it easier to:
1. Test different strategies without changing backtest code
2. Compare strategies fairly
3. Add new strategies quickly
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List
from enum import Enum
import pandas as pd


class Signal(Enum): # Trading signal
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class TradeSignal:
    timestamp: datetime
    signal: Signal
    strength: float = 1.0  # 0 to 1
    target_quantity: Optional[float] = None
    reason: str = ""


class BaseStrategy(ABC):
    """
    Abstract base class for all strategies.
    
    To create a new strategy:
    1. Inherit from BaseStrategy
    2. Implement generate_signal()
    3. Optionally override on_fill() for tracking
    
    Example/Template:
        class MyStrategy(BaseStrategy):
            def generate_signal(self, row, position):
                if row["close"] > row["open"]:
                    return TradeSignal(row.name, Signal.BUY, reason="bullish bar")
                return TradeSignal(row.name, Signal.HOLD)
    """
    
    def __init__(self, name: str = "BaseStrategy"):
        self.name = name
        self.signals_generated: List[TradeSignal] = []
    
    @abstractmethod
    def generate_signal(
        self,
        timestamp: datetime,
        row: pd.Series,
        position: float,
        history: pd.DataFrame,
    ) -> TradeSignal:
        """
        Generate a trading signal.
        
        Args:
            timestamp: Current bar timestamp
            row: Current bar data (open, high, low, close, volume)
            position: Current position size
            history: All bars up to and including current
        
        Returns:
            TradeSignal with BUY, SELL, or HOLD
        """
        pass
    
    def on_fill(self, fill) -> None:
        """Called when a trade executes. Override for custom tracking."""
        pass
    
    def on_backtest_end(self) -> None:
        """Called when backtest finishes. Override for cleanup/summary."""
        pass


class MomentumStrategy(BaseStrategy): # Strategy no. 1
    """
    Buy when price is trending up, sell when trending down.
    
    Parameters:
        lookback: How many bars to measure momentum (default 24)
        threshold: Minimum return to trigger signal (default 0.02 = 2%)
    """
    
    def __init__(self, lookback: int = 24, threshold: float = 0.02):
        super().__init__(name=f"Momentum({lookback}h, {threshold:.1%})")
        self.lookback = lookback
        self.threshold = threshold
    
    def generate_signal(
        self,
        timestamp: datetime,
        row: pd.Series,
        position: float,
        history: pd.DataFrame,
    ) -> TradeSignal: # Need enough history
        if len(history) < self.lookback:
            return TradeSignal(timestamp, Signal.HOLD, reason="insufficient_history")
        
        current_price = row["close"] # Calculate momentum
        past_price = history["close"].iloc[-self.lookback]
        returns = (current_price - past_price) / past_price
        
        if returns > self.threshold and position < 1: # Generate signal
            return TradeSignal(
                timestamp=timestamp,
                signal=Signal.BUY,
                strength=min(returns / self.threshold, 1.0),
                target_quantity=0.5,
                reason=f"momentum_up_{returns:.2%}",
            )
        elif returns < -self.threshold and position > 0:
            return TradeSignal(
                timestamp=timestamp,
                signal=Signal.SELL,
                strength=min(abs(returns) / self.threshold, 1.0),
                target_quantity=position,  # Sell all
                reason=f"momentum_down_{returns:.2%}",
            )
        
        return TradeSignal(timestamp, Signal.HOLD, reason="no_signal")


class MeanReversionStrategy(BaseStrategy): # Strategy no. 2
    """
    Buy when price drops (expecting bounce), sell when price rises.
    
    Opposite of momentum - assumes prices revert to average.
    
    Parameters:
        lookback: Period for moving average (default 24)
        threshold: Distance from MA to trigger (default 0.02 = 2%)
    """
    
    def __init__(self, lookback: int = 24, threshold: float = 0.02):
        super().__init__(name=f"MeanReversion({lookback}h, {threshold:.1%})")
        self.lookback = lookback
        self.threshold = threshold
    
    def generate_signal(
        self,
        timestamp: datetime,
        row: pd.Series,
        position: float,
        history: pd.DataFrame,
    ) -> TradeSignal: # Need enough history
        if len(history) < self.lookback:
            return TradeSignal(timestamp, Signal.HOLD, reason="insufficient_history")
        
        ma = history["close"].tail(self.lookback).mean() # Calculate moving average
        current_price = row["close"]
        deviation = (current_price - ma) / ma
        
        # Generate signal (OPPOSITE of momentum!)
        if deviation < -self.threshold and position < 1:
            # Price BELOW average → expect reversion UP → BUY
            return TradeSignal(
                timestamp=timestamp,
                signal=Signal.BUY,
                strength=min(abs(deviation) / self.threshold, 1.0),
                target_quantity=0.5,
                reason=f"below_ma_{deviation:.2%}",
            )
        elif deviation > self.threshold and position > 0:
            # Price ABOVE average → expect reversion DOWN → SELL
            return TradeSignal(
                timestamp=timestamp,
                signal=Signal.SELL,
                strength=min(deviation / self.threshold, 1.0),
                target_quantity=position,
                reason=f"above_ma_{deviation:.2%}",
            )
        
        return TradeSignal(timestamp, Signal.HOLD, reason="within_band")

class SMACrossoverStrategy(BaseStrategy): # Strategy no. 3
    """
    Buy when fast SMA crosses above slow SMA, sell when it crosses below.
    
    Parameters:
        fast_period: bars for fast moving average (default 12)
        slow_period: bars for slow moving average (default 48)
    """
    
    def __init__(self, fast_period: int = 12, slow_period: int = 48):
        super().__init__(name=f"SMA({fast_period}/{slow_period})")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.prev_fast = None # need previous bar's SMAs to detect crossover
        self.prev_slow = None
    
    def generate_signal(
        self,
        timestamp: datetime,
        row: pd.Series,
        position: float,
        history: pd.DataFrame,
    ) -> TradeSignal:
        if len(history) < self.slow_period:
            return TradeSignal(timestamp, Signal.HOLD, reason="insufficient_history")
        
        closes = history["close"]
        fast_sma = closes.tail(self.fast_period).mean()
        slow_sma = closes.tail(self.slow_period).mean()
        
        signal = TradeSignal(timestamp, Signal.HOLD, reason="no_crossover")
        
        if self.prev_fast is not None and self.prev_slow is not None:
            # golden cross: fast crosses above slow → buy
            if self.prev_fast <= self.prev_slow and fast_sma > slow_sma and position < 1:
                signal = TradeSignal(
                    timestamp=timestamp,
                    signal=Signal.BUY,
                    strength=min(abs(fast_sma - slow_sma) / slow_sma * 100, 1.0),
                    target_quantity=0.5,
                    reason=f"golden_cross_fast={fast_sma:.0f}_slow={slow_sma:.0f}",
                )
            # death cross: fast crosses below slow → sell
            elif self.prev_fast >= self.prev_slow and fast_sma < slow_sma and position > 0:
                signal = TradeSignal(
                    timestamp=timestamp,
                    signal=Signal.SELL,
                    strength=min(abs(fast_sma - slow_sma) / slow_sma * 100, 1.0),
                    target_quantity=position,
                    reason=f"death_cross_fast={fast_sma:.0f}_slow={slow_sma:.0f}",
                )
        
        self.prev_fast = fast_sma
        self.prev_slow = slow_sma
        
        return signal
    
class BollingerBandStrategy(BaseStrategy): # Strategy no. 4
    """
    Buy when price drops below lower band, sell when it rises above upper band.
    Bands = SMA ± (num_std x standard deviation), so they adapt to volatility.
    
    Parameters:
        period: bars for moving average + std dev (default 24)
        num_std: standard deviations for band width (default 2.0)
    """
    
    def __init__(self, period: int = 24, num_std: float = 2.0):
        super().__init__(name=f"Bollinger({period}, {num_std}σ)")
        self.period = period
        self.num_std = num_std
    
    def generate_signal(
        self,
        timestamp: datetime,
        row: pd.Series,
        position: float,
        history: pd.DataFrame,
    ) -> TradeSignal:
        if len(history) < self.period:
            return TradeSignal(timestamp, Signal.HOLD, reason="insufficient_history")
        
        closes = history["close"].tail(self.period)
        sma = closes.mean()
        std = closes.std()
        
        upper_band = sma + self.num_std * std
        lower_band = sma - self.num_std * std
        price = row["close"]
        
        # price below lower band -> oversold -> buy
        if price < lower_band and position < 1:
            distance = (lower_band - price) / std  # how far below in std devs
            return TradeSignal(
                timestamp=timestamp,
                signal=Signal.BUY,
                strength=min(distance, 1.0),
                target_quantity=0.5,
                reason=f"below_lower_{price:.0f}<{lower_band:.0f}",
            )
        # price above upper band -> overbought -> sell
        elif price > upper_band and position > 0:
            distance = (price - upper_band) / std
            return TradeSignal(
                timestamp=timestamp,
                signal=Signal.SELL,
                strength=min(distance, 1.0),
                target_quantity=position,
                reason=f"above_upper_{price:.0f}>{upper_band:.0f}",
            )
        
        return TradeSignal(timestamp, Signal.HOLD, reason="within_bands")