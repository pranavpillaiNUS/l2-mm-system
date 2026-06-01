"""Summarize Phase C queue-credit and latency stress reconciliation runs."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path

from src.analysis.fee_break_even import load_reconciliation_run
from src.analysis.queue_credit_summary import QueueCreditRun, build_queue_credit_summary_rows
from src.execution.queue_credit import parse_queue_credit


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


def _write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = list(rows[0]) if rows else []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _jsonable(value) for key, value in row.items()})


def _load_runs(path: Path) -> list[QueueCreditRun]:
    with path.open("r", encoding="utf-8", newline="") as f:
        manifest_rows = list(csv.DictReader(f))
    combos = {
        (
            parse_queue_credit(row["queue_cancellation_credit"]),
            int(row["latency_ms"]),
            Path(row["reconciliation_root"]),
        )
        for row in manifest_rows
    }
    runs = []
    for expected_credit, latency_ms, root in sorted(
        combos, key=lambda row: (row[0], row[1], str(row[2]))
    ):
        for summary_path in sorted(root.glob("*/summary.json")):
            reconciliation = load_reconciliation_run(summary_path.parent)
            if reconciliation.queue_credit != expected_credit:
                raise ValueError(
                    f"{summary_path} credit {reconciliation.queue_credit} "
                    f"does not match manifest credit {expected_credit}"
                )
            runs.append(QueueCreditRun(
                latency_ms=latency_ms,
                reconciliation=reconciliation,
            ))
    return runs


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-csv", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    rows = build_queue_credit_summary_rows(_load_runs(args.runs_csv))
    args.output_root.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output_root / "queue_credit_summary.csv", rows)
    with (args.output_root / "summary.json").open("w", encoding="utf-8") as f:
        json.dump({"rows": rows}, f, indent=2, default=_jsonable)
    print(f"Wrote queue-credit summary to {args.output_root}")


if __name__ == "__main__":
    main()
