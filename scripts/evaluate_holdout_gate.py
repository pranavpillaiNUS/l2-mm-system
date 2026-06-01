"""Evaluate the locked one-shot V2 candidate holdout protocol."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path

from scripts.evaluate_strategy_gate import _load_metrics
from src.analysis.holdout_gate import evaluate_holdout, evaluate_holdout_endpoint
from src.execution.queue_credit import parse_queue_credit


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
        nargs=4,
        action="append",
        metavar=("CREDIT", "LOCKED_DEV_LOW", "BASELINE_CSV", "CANDIDATE_CSV"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    endpoints = {}
    for credit, locked_low, baseline_path, candidate_path in args.endpoint:
        normalized = str(parse_queue_credit(credit))
        endpoints[normalized] = evaluate_holdout_endpoint(
            _load_metrics(Path(baseline_path)),
            _load_metrics(Path(candidate_path)),
            queue_cancellation_credit=parse_queue_credit(credit),
            locked_development_lower_ci_bound=Decimal(locked_low),
        )
    report = evaluate_holdout(endpoints)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=_jsonable)
    print(f"Holdout status: {report['status']}")
    print(f"Wrote holdout report to {args.output}")


if __name__ == "__main__":
    main()
