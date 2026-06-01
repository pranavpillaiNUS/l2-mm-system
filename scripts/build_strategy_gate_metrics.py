"""Extract per-window strategy-gate metrics from reconciliation summaries."""

from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal
from pathlib import Path

from src.execution.queue_credit import credit_from_legacy_mode, parse_queue_credit


def _summary_credit(params: dict) -> Decimal:
    if "queue_cancellation_credit" in params:
        return parse_queue_credit(params["queue_cancellation_credit"])
    return credit_from_legacy_mode(params["queue_cancellation_mode"])


def _metric_row(summary: dict) -> dict:
    params = summary["params"]
    aggregate = summary["aggregate"]
    return {
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
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    expected_credit = parse_queue_credit(args.queue_credit)
    rows = []
    for path in sorted(args.reconciliation_root.glob("*/summary.json")):
        with path.open("r", encoding="utf-8") as f:
            summary = json.load(f)
        params = summary["params"]
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
