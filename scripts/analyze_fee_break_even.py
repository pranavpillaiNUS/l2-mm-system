"""
Compute maker-fee break-even economics for the anchor MM baseline.

Example:
    env PYTHONPATH=. python scripts/analyze_fee_break_even.py
"""
import argparse
import csv
import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from scripts.compare_mm import _parse_start
from src.analysis.fee_break_even import (
    build_fee_break_even_rows,
    load_reconciliation_run,
)


DEFAULT_STARTS = [
    "2026-04-13T12",
    "2026-04-14T12",
    "2026-04-15T12",
    "2026-04-16T12",
    "2026-04-16T17",
    "2026-04-17T12",
]
DEFAULT_OUTPUT_ROOT = Path("results/fee_break_even")
DEFAULT_RUN_ID = "btcusdt_microprice_hs2.00_rq5000_anchor6"


def _run_dir_name(args, start_value: str, queue_mode: str) -> str:
    start = _parse_start(start_value)
    run_id = (
        f"{args.symbol.lower()}_{args.strategy}_"
        f"{start.strftime('%Y%m%d_%H')}_{args.hours}h_"
        f"{args.hours // args.session_hours}sessions_"
        f"hs{args.half_spread}_rq{args.requote_interval_ms}"
    )
    if queue_mode != "proportional":
        run_id = f"{run_id}_q{queue_mode}"
    return run_id


def _load_runs(args):
    runs = []
    for queue_mode in args.queue_modes:
        for start_value in args.starts:
            run_dir = args.reconciliation_root / _run_dir_name(args, start_value, queue_mode)
            if not run_dir.exists():
                raise FileNotFoundError(
                    f"Missing reconciliation artifact: {run_dir}. "
                    "Run scripts/analyze_markout_reconciliation.py for that "
                    "start and queue mode first."
                )
            run = load_reconciliation_run(run_dir)
            actual_mode = run.summary["params"].get("queue_cancellation_mode")
            if actual_mode != queue_mode:
                raise ValueError(
                    f"{run_dir} reports queue_cancellation_mode={actual_mode}; "
                    f"expected {queue_mode}"
                )
            runs.append(run)
    return runs


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


def _write_rows(path: Path, rows: Sequence[dict]) -> None:
    fieldnames = [
        "queue_cancellation_mode",
        "window",
        "run_dir",
        "row_type",
        "endpoint_sensitive",
        "current_maker_fee_bps",
        "net_pnl",
        "gross_before_fees",
        "current_fees",
        "fee_notional",
        "break_even_maker_fee_bps",
        "required_rebate_bps",
        "lot_count",
        "total_quantity",
        "quantity_weighted_net_per_btc",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})


def _print_pooled(rows: Sequence[dict]) -> None:
    pooled = [row for row in rows if row["window"] == "pooled"]
    print("Fee break-even, pooled")
    print(f"{'queue':<14} {'row_type':<35} {'net':>14} {'be_fee_bps':>12} {'rebate_bps':>12}")
    for row in pooled:
        print(
            f"{row['queue_cancellation_mode']:<14} "
            f"{row['row_type']:<35} "
            f"{_fmt(row['net_pnl']):>14} "
            f"{_fmt(row['break_even_maker_fee_bps']):>12} "
            f"{_fmt(row['required_rebate_bps']):>12}"
        )


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, Decimal):
        return f"{value:.4f}"
    return str(value)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compute maker-fee break-even diagnostics by queue mode"
    )
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--strategy", default="microprice")
    parser.add_argument("--starts", nargs="+", default=DEFAULT_STARTS)
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--session-hours", type=int, default=1)
    parser.add_argument("--half-spread", default="2.00")
    parser.add_argument("--requote-interval-ms", type=int, default=5000)
    parser.add_argument("--maker-bps", type=int, default=2)
    parser.add_argument("--queue-modes", nargs="+",
                        choices=["proportional", "none"],
                        default=["proportional", "none"])
    parser.add_argument("--reconciliation-root", type=Path,
                        default=Path("results/markout_reconciliation"))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    return parser.parse_args()


def main():
    args = parse_args()
    runs = _load_runs(args)
    rows = build_fee_break_even_rows(runs, expected_maker_bps=args.maker_bps)

    run_dir = args.output_root / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_rows(run_dir / "fee_break_even.csv", rows)

    summary = {
        "params": {
            "symbol": args.symbol.lower(),
            "strategy": args.strategy,
            "starts": args.starts,
            "hours": args.hours,
            "session_hours": args.session_hours,
            "half_spread": args.half_spread,
            "requote_interval_ms": args.requote_interval_ms,
            "maker_bps": args.maker_bps,
            "queue_modes": args.queue_modes,
            "row_types": sorted({row["row_type"] for row in rows}),
            "economic_measure": "quantity_weighted_net_per_btc",
            "full_strategy_endpoint_note": (
                "full_strategy rows are endpoint-sensitive because residual "
                "inventory is marked at the window-close mid"
            ),
        },
        "inputs": [run.run_dir.name for run in runs],
        "counts": {
            "runs": len(runs),
            "rows": len(rows),
        },
        "pooled_rows": [
            row for row in rows if row["window"] == "pooled"
        ],
    }
    with (run_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=_jsonable)

    _print_pooled(rows)
    print(f"\nWrote fee break-even artifacts to {run_dir}")


if __name__ == "__main__":
    main()
