"""
Walk-forward testing for Momentum and MeanReversion.
Optimize params on training window, test on next month.
Repeats across 2024 to get honest out-of-sample performance.
"""
import sys
sys.path.insert(0, ".")

import pandas as pd
import numpy as np
from pathlib import Path
from itertools import product

from src.backtester.config import BacktestConfig, FeeConfig, SlippageConfig
from src.backtester.engine import BacktestEngine
from src.backtester.strategy import MomentumStrategy, MeanReversionStrategy


# same param grids from the original sweeps
PARAM_GRIDS = {
    "Momentum": {
        "class": MomentumStrategy,
        "params": list(product([12, 24, 48], [0.01, 0.02, 0.03])),
        "param_names": ["lookback", "threshold"],
    },
    "MeanReversion": {
        "class": MeanReversionStrategy,
        "params": list(product([12, 24, 48], [0.01, 0.02, 0.03])),
        "param_names": ["lookback", "threshold"],
    },
}

# anchored windows - train always starts jan, test is next month
WINDOWS = [
    {"train_start": "2024-01-01", "train_end": "2024-03-31", "test_start": "2024-04-01", "test_end": "2024-04-30"},
    {"train_start": "2024-01-01", "train_end": "2024-04-30", "test_start": "2024-05-01", "test_end": "2024-05-31"},
    {"train_start": "2024-01-01", "train_end": "2024-05-31", "test_start": "2024-06-01", "test_end": "2024-06-30"},
    {"train_start": "2024-01-01", "train_end": "2024-06-30", "test_start": "2024-07-01", "test_end": "2024-07-31"},
    {"train_start": "2024-01-01", "train_end": "2024-07-31", "test_start": "2024-08-01", "test_end": "2024-08-31"},
    {"train_start": "2024-01-01", "train_end": "2024-08-31", "test_start": "2024-09-01", "test_end": "2024-09-30"},
    {"train_start": "2024-01-01", "train_end": "2024-09-30", "test_start": "2024-10-01", "test_end": "2024-10-31"},
    {"train_start": "2024-01-01", "train_end": "2024-10-31", "test_start": "2024-11-01", "test_end": "2024-11-30"},
    {"train_start": "2024-01-01", "train_end": "2024-11-30", "test_start": "2024-12-01", "test_end": "2024-12-31"},
]


def sweep_on_window(engine, strategy_class, param_combos, param_names, start, end):
    """Run all param combos on a date range, return sorted by sharpe."""
    results = []
    for combo in param_combos:
        params = dict(zip(param_names, combo))
        strategy = strategy_class(**params)
        try:
            result = engine.run(strategy, start=start, end=end, verbose=False)
            results.append({
                "params": params,
                "sharpe": result.metrics.sharpe_ratio,
                "return": result.metrics.total_return_pct,
                "trades": result.metrics.num_trades,
            })
        except Exception:
            results.append({"params": params, "sharpe": -999, "return": 0, "trades": 0})

    results.sort(key=lambda x: x["sharpe"], reverse=True)
    return results


def run_walk_forward(engine, strategy_name, grid_info):
    """Full walk-forward for one strategy."""
    print(f"\n--- {strategy_name} ---\n")
    window_results = []

    for i, w in enumerate(WINDOWS):
        # find best params on training data
        train_results = sweep_on_window(
            engine, grid_info["class"], grid_info["params"],
            grid_info["param_names"], w["train_start"], w["train_end"],
        )
        best = train_results[0]

        # test those params on unseen month
        test_strategy = grid_info["class"](**best["params"])
        test_result = engine.run(
            test_strategy, start=w["test_start"], end=w["test_end"], verbose=False,
        )

        month = pd.Timestamp(w["test_start"]).strftime("%b")
        print(f"  {month}: best={best['params']}  "
              f"train_sharpe={best['sharpe']:.3f}  "
              f"test_sharpe={test_result.metrics.sharpe_ratio:.3f}  "
              f"test_ret={test_result.metrics.total_return_pct:.1f}%")

        window_results.append({
            "window": i + 1,
            "test_month": month,
            "train_end": w["train_end"],
            "best_params": str(best["params"]),
            "train_sharpe": best["sharpe"],
            "test_sharpe": test_result.metrics.sharpe_ratio,
            "test_return_pct": test_result.metrics.total_return_pct,
            "test_max_dd": test_result.metrics.max_drawdown,
            "test_trades": test_result.metrics.num_trades,
        })

    return window_results


def print_summary(strategy_name, window_results, in_sample_sharpe):
    """Print the numbers that matter."""
    sharpes = [w["test_sharpe"] for w in window_results]
    returns = [w["test_return_pct"] for w in window_results]

    avg_sharpe = np.mean(sharpes)
    win_rate = sum(1 for s in sharpes if s > 0) / len(sharpes)
    overfit_ratio = avg_sharpe / in_sample_sharpe if in_sample_sharpe != 0 else 0

    # did the optimizer pick the same params each time, or jump around?
    chosen = [w["best_params"] for w in window_results]
    unique = len(set(chosen))

    print(f"\n{strategy_name} summary:")
    print(f"  avg OOS sharpe:    {avg_sharpe:.3f}")
    print(f"  avg OOS return:    {np.mean(returns):.1f}%/month")
    print(f"  window win rate:   {win_rate:.0%}")
    print(f"  in-sample sharpe:  {in_sample_sharpe:.3f}")
    print(f"  overfit ratio:     {overfit_ratio:.2f}  (1.0=no overfit, <0.5=heavy)")
    print(f"  param stability:   {unique} unique picks across {len(window_results)} windows")

    return {
        "strategy": strategy_name,
        "avg_oos_sharpe": avg_sharpe,
        "avg_oos_return": np.mean(returns),
        "window_win_rate": win_rate,
        "in_sample_sharpe": in_sample_sharpe,
        "overfit_ratio": overfit_ratio,
        "unique_params": unique,
    }


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

    # from the original sweeps
    in_sample_sharpes = {
        "Momentum": 0.36,
        "MeanReversion": 0.39,
    }

    all_results = {}
    summaries = []

    for name, grid in PARAM_GRIDS.items():
        window_results = run_walk_forward(engine, name, grid)
        all_results[name] = window_results
        summary = print_summary(name, window_results, in_sample_sharpes[name])
        summaries.append(summary)

    # save results
    output_dir = Path("results/walk_forward")
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for name, results in all_results.items():
        for w in results:
            rows.append({"strategy": name, **{k: v for k, v in w.items()}})

    pd.DataFrame(rows).to_csv(output_dir / "walk_forward_results.csv", index=False)
    pd.DataFrame(summaries).to_csv(output_dir / "walk_forward_summary.csv", index=False)
    print(f"\nSaved to {output_dir}/")


if __name__ == "__main__":
    main()