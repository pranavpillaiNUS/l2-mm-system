"""Classify Phase A endpoint CIs against frozen V1 endpoint means."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path

from src.analysis.v2_classification import classify_endpoint, classify_phase_a
from src.execution.queue_credit import parse_queue_credit


def _pairs(values: list[str]) -> dict[str, str]:
    out = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator:
            raise ValueError(f"expected CREDIT=VALUE, got {value!r}")
        normalized = str(parse_queue_credit(key))
        out[normalized] = item
    return out


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
    parser.add_argument("--endpoint-ci", nargs="+", required=True,
                        help="CREDIT=baseline_ci.json")
    parser.add_argument("--frozen-v1-mean", nargs="+", required=True,
                        help="CREDIT=mean full-strategy net PnL")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    ci_paths = _pairs(args.endpoint_ci)
    frozen_means = _pairs(args.frozen_v1_mean)
    if ci_paths.keys() != frozen_means.keys():
        raise ValueError("endpoint CI and frozen V1 mean credits must match")
    endpoints = {}
    for credit, path in ci_paths.items():
        with Path(path).open("r", encoding="utf-8") as f:
            report = json.load(f)
        ci = report["ci"]["window"]["net_pnl"]
        endpoints[credit] = classify_endpoint(
            queue_cancellation_credit=parse_queue_credit(credit),
            mean_net_pnl=Decimal(ci["mean"]),
            ci_low=Decimal(ci["ci_low"]),
            ci_high=Decimal(ci["ci_high"]),
            frozen_v1_mean_net_pnl=Decimal(frozen_means[credit]),
        )
    result = classify_phase_a(endpoints)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=_jsonable)
    print(result["headline"])
    print(f"Wrote Phase A verdict to {args.output}")


if __name__ == "__main__":
    main()
