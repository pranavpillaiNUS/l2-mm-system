"""
Handles:
- Loading parquet/CSV files
- Date filtering
- Bar iteration
"""
import pandas as pd
from pathlib import Path
from typing import Optional, List, Iterator
from dataclasses import dataclass


@dataclass
class Bar:
    """
    A single OHLCV bar
    
    OHLCV = Open, High, Low, Close, Volume
    """
    timestamp: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    
    @property
    def mid(self) -> float:
        """lambda = 1/2"""
        return (self.high + self.low) / 2
    
    @property
    def bar_range(self) -> float:
        return self.high - self.low
    
    @property
    def is_bullish(self) -> bool:
        """True if close > open"""
        return self.close > self.open


class DataLoader:
    """
    Load OHLCV data for backtesting.
    
    Usage:
        loader = DataLoader(Path("data/bars"))
        df = loader.load("BTCUSDT")
        
        for bar in loader.iter_bars("BTCUSDT"):
            print(bar.close)
    """
    
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        if not self.data_dir.exists():
            raise FileNotFoundError(f"Data directory not found: {data_dir}")
    
    def list_available(self) -> List[str]:
        files = list(self.data_dir.glob("*.parquet")) + list(self.data_dir.glob("*.csv"))
        return [f.stem for f in files]
    
    def find_file(self, symbol: str) -> Optional[Path]:
        """Find data file for a symbol."""
        for ext in [".parquet", ".csv"]:
            matches = list(self.data_dir.glob(f"{symbol}*{ext}"))
            if matches:
                return matches[0]
        return None
    
    def load(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Load OHLCV data for a symbol.
        
        Args:
            symbol: trading pair (e.g., "BTCUSDT")
            start: Start date filter (e.g., "2024-06-01")
            end: End date filter (e.g., "2024-12-31")
        
        Returns:
            DataFrame with columns: open, high, low, close, volume
        """
        file_path = self.find_file(symbol)
        
        if file_path is None:
            available = self.list_available()
            raise FileNotFoundError(
                f"No data file found for {symbol}. Available: {available}"
            )
        
        if file_path.suffix == ".parquet":
            df = pd.read_parquet(file_path)
        else:
            df = pd.read_csv(file_path, parse_dates=["open_time"])
            df = df.set_index("open_time")
        
        # Filter by date
        if start:
            df = df[df.index >= start]
        if end:
            df = df[df.index <= end]
        
        return df.sort_index()
    
    def iter_bars(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Iterator[Bar]:
        """Iterate over data bar by bar."""
        df = self.load(symbol, start, end)
        
        for timestamp, row in df.iterrows():
            yield Bar(
                timestamp=timestamp,
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )
    
    def get_summary(self, symbol: str) -> dict:
        """Get summary statistics for a symbol."""
        df = self.load(symbol)
        return {
            "symbol": symbol,
            "start": df.index[0],
            "end": df.index[-1],
            "bars": len(df),
            "min_price": df["low"].min(),
            "max_price": df["high"].max(),
        }