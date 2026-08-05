"""Extract per-window strategy-gate metrics from reconciliation summaries."""

from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal
from pathlib import Path

from src.execution.provenance import (
    artifact_execution_model,
    guard_event_driven_output_path,
)
from src.execution.queue_credit import credit_from_legacy_mode, parse_queue_credit
from src.execution.simulator import EQUAL_TIMESTAMP_POLICY, EXECUTION_MODEL_VERSION
from src.replay.depth_parser import SNAPSHOT_TIME_POLICY


def _summary_credit(params: dict) -> Decimal:
    if "queue_cancellation_credit" in params:
        return parse_queue_credit(params["queue_cancellation_credit"])
    return credit_from_legacy_mode(params["queue_cancellation_mode"])


def _metric_row(summary: dict) -> dict:
    params = summary["params"]
    aggregate = summary["aggregate"]
    provenance = summary.get("execution_provenance", {})
    return {
        "execution_model_version": artifact_execution_model(summary),
        "equal_timestamp_policy": provenance.get("equal_timestamp_policy"),
        "snapshot_time_policy": provenance.get("snapshot_time_policy"),
        "trade_gap_policy": provenance.get("trade_gap_policy"),
        "entry_latency_ms": provenance.get("entry_latency_ms"),
        "entry_jitter_ms": provenance.get("entry_jitter_ms"),
        "cancel_latency_ms": provenance.get("cancel_latency_ms"),
        "cancel_jitter_ms": provenance.get("cancel_jitter_ms"),
        "latency_seed": provenance.get("latency_seed"),
        "post_only": provenance.get("post_only"),
        "queue_cancellation_credit": provenance.get(
            "queue_cancellation_credit"
        ),
        "window": params["start"],
        "fills": aggregate["fills"],
        "maker_fills": aggregate["maker_fills"],
        "matched_quantity": aggregate["matched_qty"],
        "matched_net_pnl": aggregate["matched_net_pnl"],
        "full_strategy_net_pnl": aggregate["net_pnl"],
        "average_abs_residual_inventory": aggregate["residual_inventory_abs_avg"],
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reconciliation-root", type=Path, required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--queue-credit", required=True)
    parser.add_argument("--latency-ms", type=int, default=10)
    parser.add_argument("--jitter-ms", type=int, default=0)
    parser.add_argument("--cancel-latency-ms", type=int)
    parser.add_argument("--cancel-jitter-ms", type=int)
    parser.add_argument("--latency-seed", type=int, default=42)
    parser.add_argument("--execution-model-version", default=EXECUTION_MODEL_VERSION)
    parser.add_argument(
        "--trade-gap-policy",
        choices=["ignore", "pause_until_snapshot"],
        default="pause_until_snapshot",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.cancel_latency_ms is None:
        args.cancel_latency_ms = args.latency_ms
    if args.cancel_jitter_ms is None:
        args.cancel_jitter_ms = args.jitter_ms
    return args


def _matches_execution_provenance(summary: dict, args, expected_credit: Decimal) -> bool:
    provenance = summary.get("execution_provenance")
    if not isinstance(provenance, dict):
        return False
    expected = {
        "execution_model_version": args.execution_model_version,
        "equal_timestamp_policy": EQUAL_TIMESTAMP_POLICY,
        "snapshot_time_policy": SNAPSHOT_TIME_POLICY,
        "trade_gap_policy": args.trade_gap_policy,
        "entry_latency_ms": args.latency_ms,
        "entry_jitter_ms": args.jitter_ms,
        "cancel_latency_ms": args.cancel_latency_ms,
        "cancel_jitter_ms": args.cancel_jitter_ms,
        "latency_seed": args.latency_seed,
        "post_only": True,
    }
    try:
        provenance_credit = parse_queue_credit(
            provenance["queue_cancellation_credit"]
        )
    except (KeyError, ValueError, ArithmeticError):
        return False
    return (
        all(provenance.get(key) == value for key, value in expected.items())
        and provenance_credit == expected_credit
    )


def main():
    args = parse_args()
    guard_event_driven_output_path(args.output)
    expected_credit = parse_queue_credit(args.queue_credit)
    rows = []
    for path in sorted(args.reconciliation_root.glob("*/summary.json")):
        with path.open("r", encoding="utf-8") as f:
            summary = json.load(f)
        params = summary["params"]
        if not _matches_execution_provenance(summary, args, expected_credit):
            continue
        if params["strategy"] != args.strategy:
            continue
        if int(params["latency_ms"]) != args.latency_ms:
            continue
        if _summary_credit(params) != expected_credit:
            continue
        rows.append(_metric_row(summary))
    if not rows:
        raise SystemExit("No matching reconciliation summaries found.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} strategy-gate rows to {args.output}")


if __name__ == "__main__":
    main()
