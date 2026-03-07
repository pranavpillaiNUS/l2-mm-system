"""
Downloading historical OHLCV bars from Binance.
"""
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime
import time


def download_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    start_date: str = "2024-01-01",
    end_date: str = "2024-12-31",
    output_dir: Path = Path("data/bars"),
) -> pd.DataFrame:
    """
    Download historical klines from Binance public API.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    url = "https://api.binance.com/api/v3/klines"
    
    # Converting dates to milliseconds
    start_ts = int(datetime.fromisoformat(start_date).timestamp() * 1000)
    end_ts = int(datetime.fromisoformat(end_date).timestamp() * 1000)
    
    # Interval to milliseconds (for pagination)
    interval_ms = {
        "1m": 60_000,
        "5m": 300_000,
        "15m": 900_000,
        "1h": 3_600_000,
        "4h": 14_400_000,
        "1d": 86_400_000,
    }[interval]
    
    all_klines = []
    current_start = start_ts
    
    print(f"Downloading {symbol} {interval} from {start_date} to {end_date}...")
    
    while current_start < end_ts:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ts,
            "limit": 1000,
        }
        
        response = requests.get(url, params=params)
        
        if response.status_code != 200:
            print(f"Error: {response.status_code} - {response.text}")
            break
            
        klines = response.json()
        
        if not klines:
            break
        
        all_klines.extend(klines)
        current_start = klines[-1][0] + interval_ms
        
        print(f"  Downloaded {len(all_klines):,} bars...")
        time.sleep(0.2)
    
    # Convert to DataFrame
    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore"
    ])
    
    # Convert types
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    
    df["trades"] = df["trades"].astype(int)
    df = df.set_index("open_time")
    
    # Save to parquet
    output_path = output_dir / f"{symbol}_{interval}_{start_date}_{end_date}.parquet"
    df.to_parquet(output_path)
    
    print(f"\n Saved to {output_path}")
    print(f"   Total bars: {len(df):,}")
    print(f"   Date range: {df.index[0]} to {df.index[-1]}")
    
    return df


if __name__ == "__main__":
    for symbol in ["BTCUSDT", "ETHUSDT"]:
        print(f"\n{'='*50}")
        download_klines(symbol=symbol)
        time.sleep(1)
    
    print("\n" + "="*50)
    print("All downloads complete!")