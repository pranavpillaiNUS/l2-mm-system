"""Run the educational Phase 1 anchored walk-forward experiment.

This runner reconstructs the four-strategy optimizer from the parameter grids
recorded in the research log. Historical CSVs are retained as project history,
reruns should be written to a new directory because the bar engine still uses
same-close signal/execution timing and is not part of the L2 evidence base.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Type

import numpy as np
import pandas as pd

from src.backtester.config import BacktestConfig, FeeConfig, SlippageConfig
from src.backtester.engine import BacktestEngine
from src.backtester.strategy import (
    BaseStrategy,
    BollingerBandStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    SMACrossoverStrategy,
)


@dataclass(frozen=True)
class StrategyGrid:
    strategy_cls: Type[BaseStrategy]
    param_names: tuple[str, ...]
    combinations: tuple[tuple[object, ...], ...]


PARAM_GRIDS = {
    "Momentum": StrategyGrid(
        MomentumStrategy,
        ("lookback", "threshold"),
        tuple(product((12, 24, 48), (0.01, 0.02, 0.03))),
    ),
    "MeanReversion": StrategyGrid(
        MeanReversionStrategy,
        ("lookback", "threshold"),
        tuple(product((12, 24, 48), (0.01, 0.02, 0.03))),
    ),
    "SMA": StrategyGrid(
        SMACrossoverStrategy,
        ("fast_period", "slow_period"),
        tuple(
            (fast, slow)
            for fast, slow in product((12, 24, 48), (48, 72, 96, 120))
            if fast < slow
        ),
    ),
    "Bollinger": StrategyGrid(
        BollingerBandStrategy,
        ("period", "num_std"),
        tuple(product((12, 24, 36, 48), (1.0, 1.5, 2.0, 2.5, 3.0))),
    ),
}

WINDOWS = (
    ("2024-01-01", "2024-03-31", "2024-04-01", "2024-04-30"),
    ("2024-01-01", "2024-04-30", "2024-05-01", "2024-05-31"),
    ("2024-01-01", "2024-05-31", "2024-06-01", "2024-06-30"),
    ("2024-01-01", "2024-06-30", "2024-07-01", "2024-07-31"),
    ("2024-01-01", "2024-07-31", "2024-08-01", "2024-08-31"),
    ("2024-01-01", "2024-08-31", "2024-09-01", "2024-09-30"),
    ("2024-01-01", "2024-09-30", "2024-10-01", "2024-10-31"),
    ("2024-01-01", "2024-10-31", "2024-11-01", "2024-11-30"),
    ("2024-01-01", "2024-11-30", "2024-12-01", "2024-12-31"),
)


def _params(grid: StrategyGrid, combination: tuple[object, ...]) -> dict:
    return dict(zip(grid.param_names, combination))


def sweep_training_window(
    engine: BacktestEngine,
    grid: StrategyGrid,
    start: str,
    end: str,
) -> list[dict]:
    """Evaluate one fresh strategy instance for every logged grid point."""
    rows = []
    for combination in grid.combinations:
        params = _params(grid, combination)
        result = engine.run(grid.strategy_cls(**params), start=start, end=end, verbose=False)
        sharpe = result.metrics.sharpe_ratio
        rows.append(
            {
                "params": params,
                "sharpe": sharpe if math.isfinite(sharpe) else float("-inf"),
                "return_pct": result.metrics.total_return_pct,
                "trades": result.metrics.num_trades,
            }
        )
    return sorted(rows, key=lambda row: row["sharpe"], reverse=True)


def stitched_sharpe(monthly_equity: list[pd.Series]) -> float:
    """Compute one Sharpe from the concatenated hourly OOS return series."""
    returns = pd.concat([equity.pct_change().dropna() for equity in monthly_equity])
    volatility = returns.std()
    if returns.empty or not np.isfinite(volatility) or volatility <= 0:
        return 0.0
    return float(returns.mean() / volatility * np.sqrt(365.25 * 24))


def run_strategy(
    engine: BacktestEngine,
    strategy_name: str,
    grid: StrategyGrid,
) -> tuple[list[dict], dict]:
    """Optimize on each expanding training interval and evaluate the next month."""
    window_rows = []
    monthly_equity = []

    for index, (train_start, train_end, test_start, test_end) in enumerate(WINDOWS, 1):
        best = sweep_training_window(engine, grid, train_start, train_end)[0]
        test_result = engine.run(
            grid.strategy_cls(**best["params"]),
            start=test_start,
            end=test_end,
            verbose=False,
        )
        monthly_equity.append(test_result.equity_series)
        month = pd.Timestamp(test_start).strftime("%b")
        print(
            f"{strategy_name:13s} {month}: params={best['params']} "
            f"train_sharpe={best['sharpe']:.3f} "
            f"test_return={test_result.metrics.total_return_pct:.2f}%"
        )
        window_rows.append(
            {
                "strategy": strategy_name,
                "window": index,
                "test_month": month,
                "train_end": train_end,
                "best_params": str(best["params"]),
                "train_sharpe": best["sharpe"],
                "test_sharpe": test_result.metrics.sharpe_ratio,
                "test_return_pct": test_result.metrics.total_return_pct,
                "test_max_dd": test_result.metrics.max_drawdown,
                "test_trades": test_result.metrics.num_trades,
            }
        )

    chosen = [row["best_params"] for row in window_rows]
    summary = {
        "strategy": strategy_name,
        "stitched_oos_sharpe": stitched_sharpe(monthly_equity),
        "mean_monthly_sharpe": float(np.mean([row["test_sharpe"] for row in window_rows])),
        "mean_monthly_return_pct": float(
            np.mean([row["test_return_pct"] for row in window_rows])
        ),
        "positive_month_share": float(
            np.mean([row["test_return_pct"] > 0 for row in window_rows])
        ),
        "unique_selected_params": len(set(chosen)),
    }
    return window_rows, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-path", type=Path, default=Path("data/bars"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/walk_forward_reconstructed"),
        help="Use a new namespace; historical result files are not overwritten",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = BacktestConfig(
        data_path=args.data_path,
        symbol="BTCUSDT",
        start_date="2024-01-01",
        end_date="2024-12-31",
        initial_cash=100_000,
        fees=FeeConfig(maker_bps=2, taker_bps=4),
        slippage=SlippageConfig(spread_bps=1, impact_bps_per_unit=0),
    )
    engine = BacktestEngine(config)
    rows = []
    summaries = []
    for strategy_name, grid in PARAM_GRIDS.items():
        strategy_rows, summary = run_strategy(engine, strategy_name, grid)
        rows.extend(strategy_rows)
        summaries.append(summary)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.output_dir / "walk_forward_results.csv", index=False)
    pd.DataFrame(summaries).to_csv(args.output_dir / "walk_forward_summary.csv", index=False)
    print(f"Wrote reconstructed educational results to {args.output_dir}")


if __name__ == "__main__":
    main()
