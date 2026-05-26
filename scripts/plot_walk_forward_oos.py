"""
Plot out-of-sample equity curves from walk-forward results.

Reads the winning params from walk_forward_results.csv, re-runs those
out-of-sample backtests, and stitches the monthly equity curves together.
"""
import ast
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from src.backtester.config import BacktestConfig, FeeConfig, SlippageConfig
from src.backtester.engine import BacktestEngine
from src.backtester.strategy import (
    BollingerBandStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    SMACrossoverStrategy,
)


STRATEGY_CLASSES = {
    "Momentum": MomentumStrategy,
    "MeanReversion": MeanReversionStrategy,
    "SMA": SMACrossoverStrategy,
    "Bollinger": BollingerBandStrategy,
}

WINDOWS = [
    ("2024-04-01", "2024-04-30"),
    ("2024-05-01", "2024-05-31"),
    ("2024-06-01", "2024-06-30"),
    ("2024-07-01", "2024-07-31"),
    ("2024-08-01", "2024-08-31"),
    ("2024-09-01", "2024-09-30"),
    ("2024-10-01", "2024-10-31"),
    ("2024-11-01", "2024-11-30"),
    ("2024-12-01", "2024-12-31"),
]


def stitch_equity_curves(equity_list, initial_cash):
    """Chain independent monthly equity series into one compounded OOS curve."""
    stitched = pd.Series(dtype=float)
    cumulative_multiplier = 1.0

    for equity in equity_list:
        normalized = equity / initial_cash
        scaled = normalized * cumulative_multiplier * initial_cash
        stitched = pd.concat([stitched, scaled])
        cumulative_multiplier *= normalized.iloc[-1]

    return stitched


def main():
    config = BacktestConfig(
        data_path=Path("data/bars"),
        symbol="BTCUSDT",
        start_date="2024-01-01",
        end_date="2024-12-31",
        initial_cash=100_000,
        fees=FeeConfig(maker_bps=2, taker_bps=4),
        slippage=SlippageConfig(spread_bps=1, impact_bps_per_unit=0),
    )
    engine = BacktestEngine(config)
    results_df = pd.read_csv("results/walk_forward/walk_forward_results.csv")

    strategy_curves = {}
    for strategy_name, strategy_cls in STRATEGY_CLASSES.items():
        rows = results_df[results_df["strategy"] == strategy_name].sort_values("window")
        equity_list = []

        for _, row in rows.iterrows():
            params = ast.literal_eval(row["best_params"])
            window_start, window_end = WINDOWS[row["window"] - 1]
            result = engine.run(
                strategy_cls(**params),
                start=window_start,
                end=window_end,
                verbose=False,
            )
            equity_list.append(result.equity_series)

        stitched = stitch_equity_curves(equity_list, config.initial_cash)
        strategy_curves[strategy_name] = stitched
        print(
            f"{strategy_name}: {len(stitched)} bars, "
            f"${stitched.iloc[0]:,.0f} -> ${stitched.iloc[-1]:,.0f}"
        )

    df = engine.loader.load("BTCUSDT", start="2024-04-01", end="2024-12-31")
    buy_hold_equity = (df["close"] / df["close"].iloc[0]) * config.initial_cash

    output_dir = Path("results/walk_forward")
    output_dir.mkdir(parents=True, exist_ok=True)

    colors = {
        "Momentum": "#e74c3c",
        "MeanReversion": "#2ecc71",
        "SMA": "#3498db",
        "Bollinger": "#9b59b6",
    }

    fig, ax = plt.subplots(figsize=(12, 6))
    for name, curve in strategy_curves.items():
        ax.plot(curve.index, curve.values, label=name, color=colors[name], linewidth=1.5)
    ax.plot(
        buy_hold_equity.index,
        buy_hold_equity.values,
        label="Buy & Hold",
        color="gray",
        linewidth=1,
        linestyle="--",
        alpha=0.7,
    )
    ax.set_title("Out-of-Sample Equity Curves (Walk-Forward, Apr-Dec 2024)")
    ax.set_ylabel("Portfolio Value ($)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_dir / "oos_equity_curves.png", dpi=150)

    months = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    fig, ax = plt.subplots(figsize=(12, 6))
    x = np.arange(len(months))
    width = 0.2

    for idx, strategy_name in enumerate(STRATEGY_CLASSES):
        rows = results_df[results_df["strategy"] == strategy_name].sort_values("window")
        ax.bar(
            x + idx * width,
            rows["test_return_pct"].values,
            width,
            label=strategy_name,
            color=colors[strategy_name],
        )

    ax.set_xticks(x + width * 1.5)
    ax.set_xticklabels(months)
    ax.set_ylabel("Monthly Return (%)")
    ax.set_title("Out-of-Sample Monthly Returns by Strategy")
    ax.legend()
    ax.axhline(y=0, color="black", linewidth=0.5)
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(output_dir / "oos_monthly_returns.png", dpi=150)
    print(f"Saved plots to {output_dir}")


if __name__ == "__main__":
    main()
