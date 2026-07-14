"""Build the frozen hourly integrity manifest for L2 panel selection."""

import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

from src.analysis.data_integrity import (
    HourIntegrity,
    build_integrity_manifest,
    manifest_sha256,
)
from src.execution.provenance import guard_frozen_v2_output_path


def _parse_hour(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _candidate_starts(
    rows: list[HourIntegrity],
    *,
    start: datetime,
    end: datetime,
    hours: int,
) -> list[datetime]:
    valid = {row.start for row in rows if row.valid and start <= row.start < end}
    return [
        hour
        for hour in sorted(valid)
        if hour + timedelta(hours=hours) <= end
        and all(hour + timedelta(hours=offset) in valid for offset in range(hours))
    ]


def _non_overlapping_capacity(starts: list[datetime], *, hours: int) -> int:
    selected: list[datetime] = []
    for start in sorted(starts):
        if not selected or start >= selected[-1] + timedelta(hours=hours):
            selected.append(start)
    return len(selected)


def _range_summary(
    rows: list[HourIntegrity],
    *,
    label: str,
    start: datetime,
    end: datetime,
    hours: int,
) -> dict:
    in_range = [row for row in rows if start <= row.start < end]
    candidates = _candidate_starts(rows, start=start, end=end, hours=hours)
    return {
        "label": label,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "hours": len(in_range),
        "valid_hours": sum(row.valid for row in in_range),
        "invalid_hours": sum(not row.valid for row in in_range),
        "candidate_window_starts": len(candidates),
        "clean_non_overlapping_capacity": _non_overlapping_capacity(
            candidates, hours=hours
        ),
    }


def _csv_value(value) -> str:
    if isinstance(value, list):
        return "|".join(value)
    if value is None:
        return ""
    return str(value)


def _write_csv(path: Path, rows: list[HourIntegrity]) -> None:
    values = [row.to_dict() for row in rows]
    fieldnames = list(values[0]) if values else []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in values:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})


def parse_args():
    parser = argparse.ArgumentParser(description="Build hourly L2 integrity manifest")
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--start", type=_parse_hour, default=None)
    parser.add_argument("--cutoff", type=_parse_hour,
                        default=datetime.fromisoformat("2026-06-01T00:00"))
    parser.add_argument("--development-end", type=_parse_hour,
                        default=datetime.fromisoformat("2026-05-20T00:00"))
    parser.add_argument("--window-hours", type=int, default=5)
    parser.add_argument("--output-dir", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2"))
    return parser.parse_args()


def main():
    args = parse_args()
    guard_frozen_v2_output_path(args.output_dir)
    rows = build_integrity_manifest(
        data_root=args.data_root,
        symbol=args.symbol,
        cutoff=args.cutoff,
        start=args.start,
    )
    row_dicts = [row.to_dict() for row in rows]
    digest = manifest_sha256(row_dicts)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output_dir / "integrity_manifest.csv", rows)

    summary = {
        "params": {
            "symbol": args.symbol.lower(),
            "data_root": str(args.data_root),
            "start": None if args.start is None else args.start.isoformat(),
            "cutoff": args.cutoff.isoformat(),
            "development_end": args.development_end.isoformat(),
            "window_hours": args.window_hours,
        },
        "manifest_sha256": digest,
        "counts": {
            "hours": len(rows),
            "valid_hours": sum(row.valid for row in rows),
            "invalid_hours": sum(not row.valid for row in rows),
        },
        "ranges": [
            _range_summary(
                rows,
                label="development_inventory",
                start=rows[0].start if rows else args.development_end,
                end=args.development_end,
                hours=args.window_hours,
            ),
            _range_summary(
                rows,
                label="late_may_holdout_inventory",
                start=args.development_end,
                end=args.cutoff,
                hours=args.window_hours,
            ),
        ],
        "hours": row_dicts,
    }
    with (args.output_dir / "integrity_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"Wrote integrity manifest to {args.output_dir}")
    print(f"Manifest SHA-256: {digest}")
    for row in summary["ranges"]:
        print(
            f"{row['label']}: valid_hours={row['valid_hours']} "
            f"candidate_starts={row['candidate_window_starts']} "
            f"nonoverlap_capacity={row['clean_non_overlapping_capacity']}"
        )


if __name__ == "__main__":
    main()
