"""
Initial exploratory analysis for BTCUSDT bar data.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from src.backtester.data_loader import DataLoader


def analyze_btcusdt():
    print("=" * 60)
    print("BTCUSDT 2024 ANALYSIS")
    print("=" * 60)

    loader = DataLoader(Path("data/bars"))
    df = loader.load("BTCUSDT")
    
    # Basic Stats
    print("\nBASIC STATISTICS")
    print("-" * 40)
    print(f"Date range: {df.index[0].date()} to {df.index[-1].date()}")
    print(f"Total bars: {len(df):,}")
    
    # Price Stats
    print("\nPRICE STATISTICS")
    print("-" * 40)
    print(f"Starting price: ${df['close'].iloc[0]:,.2f}")
    print(f"Ending price: ${df['close'].iloc[-1]:,.2f}")
    print(f"Year return: {(df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100:.1f}%")
    print(f"Min price: ${df['low'].min():,.2f}")
    print(f"Max price: ${df['high'].max():,.2f}")
    
    # Returns
    print("\nRETURN STATISTICS")
    print("-" * 40)
    df["returns"] = df["close"].pct_change()
    
    hourly_std = df["returns"].std()
    annual_vol = hourly_std * np.sqrt(24 * 365) * 100
    
    print(f"Mean hourly return: {df['returns'].mean() * 100:.4f}%")
    print(f"Std hourly return: {hourly_std * 100:.4f}%")
    print(f"Annualized volatility: {annual_vol:.1f}%")
    print(f"Skewness: {df['returns'].skew():.2f}")
    print(f"Kurtosis: {df['returns'].kurtosis():.2f}")
    
    # Volume
    print("\nVOLUME STATISTICS")
    print("-" * 40)
    print(f"Mean hourly volume: {df['volume'].mean():,.0f} BTC")
    print(f"Max hourly volume: {df['volume'].max():,.0f} BTC")
    
    # === Create Plots ===
    print("\n Plots take time, wait a while")
    
    output_dir = Path("results/analysis")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    # 1. Price chart
    axes[0, 0].plot(df.index, df["close"], linewidth=0.8)
    axes[0, 0].set_title("BTCUSDT Price (2024)")
    axes[0, 0].set_ylabel("Price ($)")
    
    # 2. Returns distribution
    axes[0, 1].hist(df["returns"].dropna() * 100, bins=100, edgecolor="black", alpha=0.7)
    axes[0, 1].axvline(0, color="red", linestyle="--")
    axes[0, 1].set_title("Hourly Returns Distribution")
    axes[0, 1].set_xlabel("Return (%)")
    
    # 3. Volume
    axes[1, 0].plot(df.index, df["volume"], linewidth=0.5, alpha=0.7)
    axes[1, 0].set_title("Hourly Volume")
    axes[1, 0].set_ylabel("Volume (BTC)")
    
    # 4. Rolling volatility
    rolling_vol = df["returns"].rolling(24 * 7).std() * np.sqrt(24 * 365) * 100
    axes[1, 1].plot(df.index, rolling_vol, linewidth=0.8)
    axes[1, 1].set_title("7-Day Rolling Volatility (%)")
    axes[1, 1].set_ylabel("Annualized Vol (%)")
    
    plt.tight_layout()
    plt.savefig(output_dir / "btcusdt_2024_analysis.png", dpi=150)
    print(f"Saved plot to {output_dir}/btcusdt_2024_analysis.png")
    plt.close()


if __name__ == "__main__":
    analyze_btcusdt()
