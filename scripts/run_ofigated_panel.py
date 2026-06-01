"""Run OFIGatedMM only after both queue endpoints pass the OFI support gate."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from scripts.run_l2_panel import _run_step
from src.execution.queue_credit import parse_queue_credit, queue_credit_suffix


ALLOWED_OFI_VERDICTS = {
    "supported",
    "supported_with_conditional_power_limit",
}


def _validate_ofi_support(paths: list[Path], expected_credits: list[str]) -> dict[str, str]:
    verdicts = {}
    for path in paths:
        with path.open("r", encoding="utf-8") as f:
            summary = json.load(f)
        credit = str(parse_queue_credit(summary["params"]["queue_cancellation_credit"]))
        verdict = summary["gates"]["overall_verdict"]
        verdicts[credit] = verdict
    expected = {str(parse_queue_credit(value)) for value in expected_credits}
    if verdicts.keys() != expected:
        raise ValueError("OFI summaries must cover exactly the requested queue endpoints")
    blocked = {
        credit: verdict for credit, verdict in verdicts.items()
        if verdict not in ALLOWED_OFI_VERDICTS
    }
    if blocked:
        raise ValueError(f"OFIGatedMM is blocked by OFI verdicts: {blocked}")
    return verdicts


def _load_starts(path: Path) -> list[datetime]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return [datetime.fromisoformat(row["start"]) for row in csv.DictReader(f)]


def _summary_path(args, start: datetime, credit: str) -> Path:
    run_id = (
        f"{args.symbol.lower()}_ofi_gated_{start.strftime('%Y%m%d_%H')}_"
        f"{args.hours}h_{args.hours // args.session_hours}sessions_"
        f"hs{args.half_spread}_rq{args.requote_interval_ms}"
    )
    return args.output_root / "markout_reconciliation" / (
        f"{run_id}{queue_credit_suffix(credit)}"
    ) / "summary.json"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ofi-summary", nargs="+", type=Path, required=True)
    parser.add_argument("--windows-csv", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--status-dir", type=Path, required=True)
    parser.add_argument("--queue-credits", nargs="+", default=["0.0", "1.0"])
    parser.add_argument("--symbol", default="btcusdt")
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
    parser.add_argument("--ofi-interval-ms", type=int, default=1000)
    parser.add_argument("--ofi-threshold", default="0.25")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    args.queue_credits = [
        str(parse_queue_credit(value)) for value in dict.fromkeys(args.queue_credits)
    ]
    verdicts = _validate_ofi_support(args.ofi_summary, args.queue_credits)
    print(f"OFI support gate: {verdicts}")
    for credit in args.queue_credits:
        for start in _load_starts(args.windows_csv):
            end = start + timedelta(hours=args.hours)
            command = [
                sys.executable, "scripts/analyze_markout_reconciliation.py",
                "--strategy", "ofi_gated",
                "--start", start.strftime("%Y-%m-%dT%H"),
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
                "--queue-cancellation-credit", credit,
                "--ofi-interval-ms", str(args.ofi_interval_ms),
                "--ofi-threshold", args.ofi_threshold,
                "--data-root", str(args.data_root),
                "--output-dir", str(args.output_root / "markout_reconciliation"),
            ]
            _run_step(
                args,
                f"ofi_gated_qc{Decimal(credit).normalize()}_{start.isoformat()}",
                command,
                [_summary_path(args, start, credit)],
            )


if __name__ == "__main__":
    main()
