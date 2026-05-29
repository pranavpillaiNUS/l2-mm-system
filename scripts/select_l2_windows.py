"""Select a deterministic 24-window L2 research panel."""

import argparse
import csv
import json
import statistics
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from scripts.compare_mm import SessionWindow, _parse_start, _select_files
from src.analysis.window_selection import WindowCandidate, select_balanced_windows
from src.execution.simulator import SimConfig
from src.replay.engine import ReplayConfig, ReplayEngine


DEFAULT_ANCHORS = [
    "2026-04-13T12",
    "2026-04-14T12",
    "2026-04-15T12",
    "2026-04-16T12",
    "2026-04-16T17",
    "2026-04-17T12",
]


class NoopStrategy:
    def on_book_update(self, book, timestamp_ms):
        return []

    def on_trade(self, trade, book):
        return []

    def on_fill(self, fill):
        return []

    def on_order_placed(self, request, order):
        return None


def _available_hour_starts(data_root: Path, symbol: str) -> list[datetime]:
    symbol = symbol.lower()
    depth_dir = data_root / "raw" / symbol
    trade_dir = data_root / "raw" / f"{symbol}_trades"
    starts = []
    for depth_path in sorted(depth_dir.glob(f"{symbol}_depth_*.jsonl.gz")):
        stamp = depth_path.name.removeprefix(f"{symbol}_depth_").removesuffix(".jsonl.gz")
        trade_path = trade_dir / f"{symbol}_trades_{stamp}.jsonl.gz"
        if not trade_path.exists():
            continue
        starts.append(datetime.strptime(stamp, "%Y%m%d_%H%M"))
    return starts


def _candidate_starts(hour_starts: list[datetime], hours: int) -> list[datetime]:
    available = set(hour_starts)
    out = []
    for start in sorted(available):
        if all(start + timedelta(hours=offset) in available for offset in range(hours)):
            out.append(start)
    return out


def _window_metrics(args, start: datetime) -> tuple[WindowCandidate | None, dict]:
    window = SessionWindow(start=start, hours=args.hours)
    depth_files, _ = _select_files(args.data_root, args.symbol, window)
    config = ReplayConfig(
        depth_files=depth_files,
        trade_files=[],
        sim_config=SimConfig(
            base_latency_ms=0,
            jitter_ms=0,
            maker_bps=0,
            taker_bps=0,
        ),
        record_book_samples=True,
    )
    result = ReplayEngine(config).run(NoopStrategy())
    samples = result.book_samples
    reject_reason = ""
    if not samples:
        reject_reason = "no_book_samples"
    elif result.stats.snapshots == 0:
        reject_reason = "no_snapshot"
    elif result.stats.gaps_detected > 0:
        reject_reason = "depth_gap"

    if reject_reason:
        return None, {
            "start": start.isoformat(),
            "reject_reason": reject_reason,
            "snapshots": result.stats.snapshots,
            "gaps_detected": result.stats.gaps_detected,
            "book_samples": len(samples),
        }

    mids = [sample.mid for sample in samples]
    returns = [
        ((mids[idx] - mids[idx - 1]) / mids[idx - 1]) * Decimal("10000")
        for idx in range(1, len(mids))
        if mids[idx - 1] > 0
    ]
    realized_vol = Decimal(str(statistics.fmean(float(r * r) for r in returns) ** 0.5)) if returns else Decimal("0")
    drift = ((mids[-1] - mids[0]) / mids[0]) * Decimal("10000") if mids[0] > 0 else Decimal("0")
    return WindowCandidate(
        start=start,
        hours=args.hours,
        realized_vol_bps=realized_vol,
        mid_drift_bps=drift,
    ), {
        "start": start.isoformat(),
        "reject_reason": "",
        "snapshots": result.stats.snapshots,
        "gaps_detected": result.stats.gaps_detected,
        "book_samples": len(samples),
        "realized_vol_bps": realized_vol,
        "mid_drift_bps": drift,
    }


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    return value


def _csv_value(value) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    return "" if value is None else str(value)


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})


def parse_args():
    parser = argparse.ArgumentParser(description="Select deterministic L2 panel windows")
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--total-windows", type=int, default=24)
    parser.add_argument("--anchors", nargs="+", default=DEFAULT_ANCHORS)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2"))
    return parser.parse_args()


def main():
    args = parse_args()
    starts = _candidate_starts(_available_hour_starts(args.data_root, args.symbol), args.hours)
    candidates: list[WindowCandidate] = []
    audit_rows: list[dict] = []
    for idx, start in enumerate(starts, start=1):
        print(f"[{idx}/{len(starts)}] scanning {start:%Y-%m-%d %H:00}")
        candidate, audit = _window_metrics(args, start)
        audit_rows.append(audit)
        if candidate is not None:
            candidates.append(candidate)

    anchor_starts = [_parse_start(value) for value in args.anchors]
    selected = select_balanced_windows(
        candidates,
        anchor_starts=anchor_starts,
        total_windows=args.total_windows,
    )
    selected_rows = [
        {
            "start": row.start.isoformat(),
            "hours": row.hours,
            "source": row.source,
            "utc_bucket": row.utc_bucket,
            "vol_tercile": row.vol_tercile,
            "realized_vol_bps": row.realized_vol_bps,
            "mid_drift_bps": row.mid_drift_bps,
        }
        for row in selected
    ]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output_dir / "windows.csv", selected_rows)
    _write_csv(args.output_dir / "window_selection_audit.csv", audit_rows)
    summary = {
        "params": {
            "symbol": args.symbol.lower(),
            "hours": args.hours,
            "total_windows": args.total_windows,
            "anchors": args.anchors,
            "selection_rule": (
                "keep anchors; require complete depth/trade files, snapshot replay, "
                "and no depth gaps; balance UTC buckets and realized-vol terciles; "
                "earliest start wins ties"
            ),
        },
        "counts": {
            "candidate_starts": len(starts),
            "accepted_candidates": len(candidates),
            "selected_windows": len(selected),
        },
        "windows": selected_rows,
    }
    with (args.output_dir / "window_selection_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=_jsonable)
    print(f"Wrote selected windows to {args.output_dir / 'windows.csv'}")


if __name__ == "__main__":
    main()
