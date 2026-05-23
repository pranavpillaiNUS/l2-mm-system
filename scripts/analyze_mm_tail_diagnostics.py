"""
Analyze whether passive MM losses are broad-based or tail-driven.

Example:
    env PYTHONPATH=. python scripts/analyze_mm_tail_diagnostics.py
"""
import argparse
import csv
import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from src.analysis.tail_diagnostics import (
    build_tail_diagnostics,
    load_anchor_run_dirs,
    load_fill_toxicity_rows,
    load_matched_lot_rows,
    load_window_summaries,
)


DEFAULT_BASELINE_CI = Path(
    "results/baseline_ci/btcusdt_microprice_hs2.00_rq5000_baseline_ci.json"
)
DEFAULT_FILL_TOXICITY_ROWS = Path(
    "results/microprice_fill_toxicity/"
    "btcusdt_microprice_hs2.00_rq5000_20260413_12_to_20260417_12_6blocks/"
    "fill_toxicity_rows.csv"
)
DEFAULT_RECONCILIATION_ROOT = Path("results/markout_reconciliation")
DEFAULT_OUTPUT_ROOT = Path("results/tail_diagnostics")
DEFAULT_RUN_ID = "btcusdt_microprice_hs2.00_rq5000_anchor6"


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    return value


def _csv_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _write_dict_csv(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, Decimal):
        return f"{value:.4f}"
    return str(value)


def _print_tail_table(title: str, rows: Sequence[dict]) -> None:
    print(title)
    print(f"{'tail':<8} {'n':>6} {'tail_sum':>14} {'share_total':>14} {'note':>18}")
    for row in rows:
        print(
            f"{row['tail_label']:<8} "
            f"{row['tail_n']:>6} "
            f"{_fmt(row['tail_metric_sum']):>14} "
            f"{_fmt(row['tail_share_of_total_metric_sum']):>14} "
            f"{row['small_n_note']:>18}"
        )
    print()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyze tail concentration in passive MM baseline losses"
    )
    parser.add_argument("--baseline-ci", type=Path, default=DEFAULT_BASELINE_CI)
    parser.add_argument("--reconciliation-root", type=Path,
                        default=DEFAULT_RECONCILIATION_ROOT)
    parser.add_argument("--fill-toxicity-rows", type=Path,
                        default=DEFAULT_FILL_TOXICITY_ROWS)
    parser.add_argument("--horizon", default="30s")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    return parser.parse_args()


def main():
    args = parse_args()

    run_dirs = load_anchor_run_dirs(args.baseline_ci, args.reconciliation_root)
    window_rows = load_window_summaries(run_dirs)
    matched_lot_rows = load_matched_lot_rows(run_dirs)
    fill_rows = load_fill_toxicity_rows(args.fill_toxicity_rows, horizon=args.horizon)

    result = build_tail_diagnostics(
        fill_rows=fill_rows,
        matched_lot_rows=matched_lot_rows,
        window_rows=window_rows,
    )
    result.summary["inputs"] = {
        "baseline_ci": str(args.baseline_ci),
        "reconciliation_root": str(args.reconciliation_root),
        "fill_toxicity_rows": str(args.fill_toxicity_rows),
        "horizon": args.horizon,
        "run_dirs": [path.name for path in run_dirs],
    }

    run_dir = args.output_root / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    with (run_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(result.summary, f, indent=2, default=_jsonable)
    _write_dict_csv(run_dir / "window_summary.csv", result.window_summary_rows)
    _write_dict_csv(run_dir / "fill_tail_rows.csv", result.fill_tail_rows)
    _write_dict_csv(run_dir / "matched_lot_tail_rows.csv",
                    result.matched_lot_tail_rows)
    _write_dict_csv(run_dir / "cluster_summary.csv", result.cluster_rows)

    print("Tail diagnostics V1")
    print(f"  Windows:       {len(window_rows)}")
    print(f"  Fill rows:     {len(fill_rows)} ({args.horizon})")
    print(f"  Matched lots:  {len(matched_lot_rows)}")
    print(f"  Output:        {run_dir}")
    print()

    fill_summary = result.summary["pooled"]["fill"]
    lot_summary = result.summary["pooled"]["matched_lot"]
    print("Pooled fill toxicity")
    print(f"  Mean / median: {_fmt(fill_summary['distribution']['mean'])} / "
          f"{_fmt(fill_summary['distribution']['median'])} bps")
    _print_tail_table("  Fill tail contribution", fill_summary["tail_contributions"])

    print("Pooled matched-lot net PnL per BTC")
    print(f"  Mean / median: {_fmt(lot_summary['distribution']['mean'])} / "
          f"{_fmt(lot_summary['distribution']['median'])}")
    _print_tail_table("  Matched-lot tail contribution",
                      lot_summary["tail_contributions"])

    print("Window failure labels")
    for row in result.window_summary_rows:
        print(
            f"  {row['window']}: {row['loss_source_label']} "
            f"net={_fmt(row['net_pnl'])} matched={_fmt(row['matched_net_pnl'])} "
            f"residual={_fmt(row['residual_inventory_pnl'])}"
        )


if __name__ == "__main__":
    main()
