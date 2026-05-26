"""
Analyze microprice skew conditional on passive fills.

This tests the question a market maker actually cares about:
after a maker fill, does microprice skew at fill time distinguish favorable
from adverse subsequent mid moves?

Example:
    env PYTHONPATH=. python scripts/analyze_microprice_fill_toxicity.py
"""
import argparse
import csv
import json
from dataclasses import asdict, fields
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Sequence

from scripts.compare_mm import SessionWindow, _build_strategy, _parse_start, _select_files
from src.analysis.microprice_fill_toxicity import (
    MicropriceFillToxicityBucket,
    MicropriceFillToxicityRow,
    bucket_microprice_fill_toxicity,
    compute_microprice_fill_toxicity,
)
from src.execution.simulator import SimConfig
from src.replay.engine import ReplayConfig, ReplayEngine


DEFAULT_STARTS = [
    "2026-04-13T12",
    "2026-04-14T12",
    "2026-04-15T12",
    "2026-04-16T12",
    "2026-04-16T17",
    "2026-04-17T12",
]


def _windows_from_starts(args) -> list[tuple[str, SessionWindow]]:
    windows: list[tuple[str, SessionWindow]] = []
    for start_value in args.starts:
        block_start = _parse_start(start_value)
        block_label = block_start.strftime("%Y-%m-%d %H:00")
        for offset in range(0, args.hours, args.session_hours):
            windows.append((
                block_label,
                SessionWindow(
                    start=block_start + timedelta(hours=offset),
                    hours=args.session_hours,
                ),
            ))
    return windows


def _run_session(args, block_label: str, window: SessionWindow) -> list[dict]:
    depth_files, trade_files = _select_files(args.data_root, args.symbol, window)
    strategy = _build_strategy(args.strategy, args)

    config = ReplayConfig(
        depth_files=depth_files,
        trade_files=trade_files,
        sim_config=SimConfig(
            base_latency_ms=args.latency_ms,
            jitter_ms=args.jitter_ms,
            maker_bps=args.maker_bps,
            taker_bps=args.taker_bps,
            queue_cancellation_mode=args.queue_cancellation_mode,
        ),
        record_book_samples=True,
    )
    engine = ReplayEngine(config)
    result = engine.run(strategy)
    rows = compute_microprice_fill_toxicity(
        result.fills,
        result.book_samples,
        maker_only=True,
        max_staleness_ms=args.max_staleness_ms,
        max_future_lag_ms=args.max_future_lag_ms,
    )

    out = []
    for row in rows:
        record = asdict(row)
        record["block_start"] = block_label
        record["session_start"] = window.label
        out.append(record)
    return out


def _decimal_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    return str(value)


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    return value


def _write_rows_csv(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "block_start",
        "session_start",
        *[field.name for field in fields(MicropriceFillToxicityRow)],
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _decimal_str(row.get(key)) for key in fieldnames})


def _write_bucket_csv(path: Path, buckets: Sequence[MicropriceFillToxicityBucket]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [field.name for field in fields(MicropriceFillToxicityBucket)]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in buckets:
            record = asdict(row)
            writer.writerow({key: _decimal_str(record.get(key)) for key in fieldnames})


def _print_bucket_table(buckets: Sequence[MicropriceFillToxicityBucket], horizon: str) -> None:
    rows = [
        row for row in buckets
        if row.horizon == horizon and row.side == "all" and row.n > 0
    ]
    print(f"Conditional fill toxicity, horizon={horizon}, side=all")
    print(f"{'bucket':>18} {'n':>7} {'avg skew':>12} {'avg move':>12} {'med move':>12}")
    for row in rows:
        print(
            f"{row.bucket:>18} {row.n:>7} "
            f"{_decimal_str(row.avg_side_aligned_skew_bps):>12} "
            f"{_decimal_str(row.avg_mid_move_bps):>12} "
            f"{_decimal_str(row.median_mid_move_bps):>12}"
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyze microprice skew conditional on maker fills"
    )
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--strategy", choices=["symmetric", "microprice"],
                        default="microprice")
    parser.add_argument("--starts", nargs="+", default=DEFAULT_STARTS)
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--session-hours", type=int, default=1)
    parser.add_argument("--half-spread", default="2.00")
    parser.add_argument("--order-qty", default="0.001")
    parser.add_argument("--max-position", default="0.01")
    parser.add_argument("--tick-size", default="0.01")
    parser.add_argument("--requote-interval-ms", type=int, default=5000)
    parser.add_argument("--latency-ms", type=int, default=10)
    parser.add_argument("--jitter-ms", type=int, default=0)
    parser.add_argument("--maker-bps", type=int, default=2)
    parser.add_argument("--taker-bps", type=int, default=5)
    parser.add_argument("--queue-cancellation-mode",
                        choices=["proportional", "none"],
                        default="proportional")
    parser.add_argument("--max-staleness-ms", type=int, default=1_000)
    parser.add_argument("--max-future-lag-ms", type=int, default=1_000)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path("results/microprice_fill_toxicity"))
    parser.add_argument("--print-horizon", default="30s")
    return parser.parse_args()


def main():
    args = parse_args()
    windows = _windows_from_starts(args)

    rows: list[dict] = []
    for idx, (block_label, window) in enumerate(windows, start=1):
        print(f"[{idx}/{len(windows)}] {window.label}")
        rows.extend(_run_session(args, block_label, window))

    toxicity_rows = [
        MicropriceFillToxicityRow(
            **{field.name: row[field.name] for field in fields(MicropriceFillToxicityRow)}
        )
        for row in rows
    ]
    buckets = bucket_microprice_fill_toxicity(toxicity_rows)

    first = _parse_start(args.starts[0]).strftime("%Y%m%d_%H")
    last = _parse_start(args.starts[-1]).strftime("%Y%m%d_%H")
    run_id = (
        f"{args.symbol.lower()}_{args.strategy}_hs{args.half_spread}_"
        f"rq{args.requote_interval_ms}_{first}_to_{last}_"
        f"{len(args.starts)}blocks"
    )
    if args.queue_cancellation_mode != "proportional":
        run_id = f"{run_id}_q{args.queue_cancellation_mode}"
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_rows_csv(run_dir / "fill_toxicity_rows.csv", rows)
    _write_bucket_csv(run_dir / "buckets.csv", buckets)

    summary = {
        "params": {
            "symbol": args.symbol.lower(),
            "strategy": args.strategy,
            "starts": args.starts,
            "hours": args.hours,
            "session_hours": args.session_hours,
            "half_spread": args.half_spread,
            "order_qty": args.order_qty,
            "max_position": args.max_position,
            "requote_interval_ms": args.requote_interval_ms,
            "latency_ms": args.latency_ms,
            "jitter_ms": args.jitter_ms,
            "maker_bps": args.maker_bps,
            "taker_bps": args.taker_bps,
            "queue_cancellation_mode": args.queue_cancellation_mode,
            "max_staleness_ms": args.max_staleness_ms,
            "max_future_lag_ms": args.max_future_lag_ms,
        },
        "counts": {
            "sessions": len(windows),
            "fill_horizon_rows": len(rows),
        },
        "buckets": buckets,
    }
    with (run_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=_jsonable)

    _print_bucket_table(buckets, args.print_horizon)
    print(f"\nWrote conditional microprice fill toxicity artifacts to {run_dir}")


if __name__ == "__main__":
    main()
