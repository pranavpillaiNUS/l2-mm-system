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
from src.execution.provenance import (
    EXECUTION_PROVENANCE_FIELDS,
    guard_event_driven_output_path,
    require_event_driven_provenance,
    require_safe_path_component,
)
from src.execution.queue_credit import (
    credit_from_legacy_mode,
    legacy_mode_from_credit,
    parse_queue_credit,
    queue_credit_suffix,
)
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
DEFAULT_OUTPUT_ROOT = Path("results/event_driven_v2/fee_break_even")
DEFAULT_RUN_ID = "btcusdt_microprice_hs2.00_rq5000_anchor6"


def _run_dir_name(args, start_value: str, queue_credit: Decimal) -> str:
    start = _parse_start(start_value)
    run_id = (
        f"{args.symbol.lower()}_{args.strategy}_"
        f"{start.strftime('%Y%m%d_%H')}_{args.hours}h_"
        f"{args.hours // args.session_hours}sessions_"
        f"hs{args.half_spread}_rq{args.requote_interval_ms}"
    )
    return f"{run_id}{queue_credit_suffix(queue_credit)}"


def _load_runs(args):
    runs = []
    shared_signature = None
    for queue_credit in args.queue_credits:
        for start_value in args.starts:
            run_dir = args.reconciliation_root / _run_dir_name(args, start_value, queue_credit)
            if not run_dir.exists():
                raise FileNotFoundError(
                    f"Missing reconciliation artifact: {run_dir}. "
                    "Run scripts/analyze_markout_reconciliation.py for that "
                    "start and queue credit first."
                )
            run = load_reconciliation_run(run_dir)
            provenance = require_event_driven_provenance(run.summary)
            actual_credit = run.queue_credit
            if actual_credit != queue_credit:
                raise ValueError(
                    f"{run_dir} reports queue_cancellation_credit={actual_credit}; "
                    f"expected {queue_credit}"
                )
            if parse_queue_credit(
                provenance["queue_cancellation_credit"]
            ) != queue_credit:
                raise ValueError(
                    f"{run_dir} execution provenance reports a different "
                    "queue-cancellation credit"
                )
            signature = tuple(
                provenance[field]
                for field in EXECUTION_PROVENANCE_FIELDS
                if field != "queue_cancellation_credit"
            )
            if shared_signature is None:
                shared_signature = signature
            elif signature != shared_signature:
                raise ValueError(
                    "fee break-even inputs have incompatible execution provenance"
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
        "execution_model_version",
        "equal_timestamp_policy",
        "snapshot_time_policy",
        "trade_gap_policy",
        "entry_latency_ms",
        "entry_jitter_ms",
        "cancel_latency_ms",
        "cancel_jitter_ms",
        "latency_seed",
        "post_only",
        "queue_cancellation_credit",
        "legacy_queue_cancellation_mode",
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


def _annotate_legacy_labels(rows: Sequence[dict]) -> None:
    for row in rows:
        row["legacy_queue_cancellation_mode"] = legacy_mode_from_credit(
            row["queue_cancellation_credit"]
        )


def _annotate_execution_provenance(rows: Sequence[dict], provenance: dict) -> None:
    for row in rows:
        for field in EXECUTION_PROVENANCE_FIELDS:
            if field != "queue_cancellation_credit":
                row[field] = provenance[field]


def _print_pooled(rows: Sequence[dict]) -> None:
    pooled = [row for row in rows if row["window"] == "pooled"]
    print("Fee break-even, pooled")
    print(f"{'credit':<8} {'row_type':<35} {'net':>14} {'be_fee_bps':>12} {'rebate_bps':>12}")
    for row in pooled:
        print(
            f"{row['queue_cancellation_credit']:<8} "
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
    parser.add_argument("--queue-credits", nargs="+",
                        default=["1.0", "0.0"],
                        help="Queue cancellation credits to compare")
    parser.add_argument("--queue-modes", nargs="+",
                        choices=["proportional", "none"],
                        help=argparse.SUPPRESS)
    parser.add_argument("--reconciliation-root", type=Path,
                        default=Path(
                            "results/event_driven_v2/markout_reconciliation"
                        ))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args()
    if args.queue_modes:
        args.queue_credits = [credit_from_legacy_mode(mode) for mode in args.queue_modes]
    else:
        args.queue_credits = [parse_queue_credit(value) for value in args.queue_credits]
    return args


def main():
    args = parse_args()
    require_safe_path_component(args.run_id, label="--run-id")
    guard_event_driven_output_path(args.output_root)
    runs = _load_runs(args)
    rows = build_fee_break_even_rows(runs, expected_maker_bps=args.maker_bps)
    _annotate_legacy_labels(rows)
    first_provenance = require_event_driven_provenance(runs[0].summary)
    _annotate_execution_provenance(rows, first_provenance)

    run_dir = args.output_root / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_rows(run_dir / "fee_break_even.csv", rows)

    aggregate_provenance = dict(first_provenance)
    aggregate_provenance["queue_cancellation_credit"] = [
        str(credit) for credit in args.queue_credits
    ]
    aggregate_provenance["varied_fields"] = ["queue_cancellation_credit"]
    summary = {
        "execution_provenance": aggregate_provenance,
        "params": {
            "symbol": args.symbol.lower(),
            "strategy": args.strategy,
            "starts": args.starts,
            "hours": args.hours,
            "session_hours": args.session_hours,
            "half_spread": args.half_spread,
            "requote_interval_ms": args.requote_interval_ms,
            "maker_bps": args.maker_bps,
            "queue_credits": args.queue_credits,
            "legacy_queue_modes": [
                legacy_mode_from_credit(credit) for credit in args.queue_credits
            ],
            "row_types": sorted({row["row_type"] for row in rows}),
            "economic_measure": "quantity_weighted_net_per_btc",
            "full_strategy_endpoint_note": (
                "full_strategy rows are endpoint-sensitive because residual "
                "inventory is marked at each session-end mid before hourly reset"
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
