"""Evaluate paired per-BTC OFIGatedMM advancement from endpoint CSVs."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path

from src.analysis.strategy_gate import (
    WindowStrategyMetrics,
    evaluate_endpoint_strategy_gate,
    evaluate_strategy_gate,
)
from src.execution.queue_credit import parse_queue_credit


def _load_metrics(path: Path) -> list[WindowStrategyMetrics]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [
        WindowStrategyMetrics(
            window=row["window"],
            fills=int(row["fills"]),
            maker_fills=int(row["maker_fills"]),
            matched_quantity=Decimal(row["matched_quantity"]),
            matched_net_pnl=Decimal(row["matched_net_pnl"]),
            full_strategy_net_pnl=Decimal(row["full_strategy_net_pnl"]),
            average_abs_residual_inventory=Decimal(
                row["average_abs_residual_inventory"]
            ),
        )
        for row in rows
    ]


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    return value


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        nargs=3,
        action="append",
        metavar=("CREDIT", "BASELINE_CSV", "CANDIDATE_CSV"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=7)
    return parser.parse_args()


def main():
    args = parse_args()
    endpoints = {}
    for credit, baseline_path, candidate_path in args.endpoint:
        normalized = str(parse_queue_credit(credit))
        endpoints[normalized] = evaluate_endpoint_strategy_gate(
            _load_metrics(Path(baseline_path)),
            _load_metrics(Path(candidate_path)),
            queue_cancellation_credit=parse_queue_credit(credit),
            iterations=args.iterations,
            seed=args.seed,
        )
    report = evaluate_strategy_gate(endpoints)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=_jsonable)
    print(f"Advance: {report['advance']}")
    print(f"Wrote candidate gate report to {args.output}")


if __name__ == "__main__":
    main()
