"""Publish the six-anchor replay delta for the trade-gap policy correction."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path

from scripts.compare_mm import _parse_start


DEFAULT_STARTS = [
    "2026-04-13T12",
    "2026-04-14T12",
    "2026-04-15T12",
    "2026-04-16T12",
    "2026-04-16T17",
    "2026-04-17T12",
]
OLD_POLICY = "ignore"
NEW_POLICY = "pause_until_snapshot"
DELTA_METRICS = (
    "fills",
    "matched_net_pnl",
    "residual_inventory_pnl",
    "net_pnl",
    "gaps_detected",
    "depth_gaps_detected",
    "trade_gaps_detected",
)


def _d(value) -> Decimal:
    return Decimal(str(value or "0"))


def _delta_row(start: str, old: dict, new: dict) -> dict:
    old_aggregate = old["aggregate"]
    new_aggregate = new["aggregate"]
    delta = {
        metric: _d(new_aggregate.get(metric)) - _d(old_aggregate.get(metric))
        for metric in DELTA_METRICS
    }
    return {
        "start": start,
        "hours": int(old["params"]["hours"]),
        OLD_POLICY: {metric: old_aggregate.get(metric) for metric in DELTA_METRICS},
        NEW_POLICY: {metric: new_aggregate.get(metric) for metric in DELTA_METRICS},
        "delta_new_minus_old": delta,
        "exact_zero_delta": all(value == Decimal("0") for value in delta.values()),
    }


def _run_anchor(args, start: str, policy: str, output_dir: Path) -> dict:
    end = _parse_start(start) + timedelta(hours=args.hours)
    command = [
        sys.executable,
        "scripts/analyze_markout_reconciliation.py",
        "--start", start,
        "--end", end.strftime("%Y-%m-%dT%H"),
        "--session-hours", str(args.session_hours),
        "--half-spread", args.half_spread,
        "--order-qty", args.order_qty,
        "--max-position", args.max_position,
        "--requote-interval-ms", str(args.requote_interval_ms),
        "--latency-ms", str(args.latency_ms),
        "--jitter-ms", str(args.jitter_ms),
        "--maker-bps", str(args.maker_bps),
        "--taker-bps", str(args.taker_bps),
        "--queue-cancellation-credit", args.queue_cancellation_credit,
        "--trade-gap-policy", policy,
        "--data-root", str(args.data_root),
        "--output-dir", str(output_dir),
    ]
    subprocess.run(command, check=True)
    summaries = list(output_dir.glob("*/summary.json"))
    if len(summaries) != 1:
        raise ValueError(f"expected one summary in {output_dir}, found {len(summaries)}")
    with summaries[0].open("r", encoding="utf-8") as f:
        return json.load(f)


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    return value


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--starts", nargs="+", default=DEFAULT_STARTS)
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--session-hours", type=int, default=1)
    parser.add_argument("--half-spread", default="2.00")
    parser.add_argument("--order-qty", default="0.001")
    parser.add_argument("--max-position", default="0.01")
    parser.add_argument("--requote-interval-ms", type=int, default=5000)
    parser.add_argument("--latency-ms", type=int, default=10)
    parser.add_argument("--jitter-ms", type=int, default=0)
    parser.add_argument("--maker-bps", type=int, default=2)
    parser.add_argument("--taker-bps", type=int, default=5)
    parser.add_argument("--queue-cancellation-credit", default="1.0")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output-root", type=Path,
                        default=Path("results/replay_correctness"))
    return parser.parse_args()


def main():
    args = parse_args()
    rows = []
    with tempfile.TemporaryDirectory(prefix="trade-gap-anchor6-") as tmp:
        temp_root = Path(tmp)
        for idx, start in enumerate(args.starts, start=1):
            print(f"[{idx}/{len(args.starts)}] {start}", flush=True)
            old = _run_anchor(args, start, OLD_POLICY, temp_root / start / OLD_POLICY)
            new = _run_anchor(args, start, NEW_POLICY, temp_root / start / NEW_POLICY)
            rows.append(_delta_row(start, old, new))

    exact_zero = all(row["exact_zero_delta"] for row in rows)
    no_anchor_trade_gaps = all(
        _d(row[NEW_POLICY]["trade_gaps_detected"]) == Decimal("0")
        for row in rows
    )
    report = {
        "protocol": {
            "old_policy": OLD_POLICY,
            "new_policy": NEW_POLICY,
            "metric_delta": "new_minus_old",
            "starts": args.starts,
            "hours": args.hours,
            "queue_cancellation_credit": args.queue_cancellation_credit,
        },
        "windows": rows,
        "aggregate": {
            "windows": len(rows),
            "exact_zero_delta": exact_zero,
            "anchors_trade_gap_clean": no_anchor_trade_gaps,
            "v1_was_not_a_trade_gap_artifact": exact_zero and no_anchor_trade_gaps,
        },
        "finding": (
            "V1 was not a trade-gap artifact: all six anchors are trade-gap-clean "
            "and replay identically under ignore and pause_until_snapshot."
            if exact_zero and no_anchor_trade_gaps
            else "The trade-gap correction changes at least one V1 anchor; inspect deltas."
        ),
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    output_path = args.output_root / "trade_gap_anchor6_delta.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=_jsonable)
    print(f"Wrote trade-gap replay delta to {output_path}")


if __name__ == "__main__":
    main()
