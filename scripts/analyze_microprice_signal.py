"""
Test whether microprice deviation predicts future mid drift.

Example:
    env PYTHONPATH=. python scripts/analyze_microprice_signal.py
"""
import argparse
import csv
import json
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from src.analysis.microprice_signal import (
    DEFAULT_HORIZONS_MS,
    MicropriceRegression,
    SignalBucket,
    bucket_forward_drift_by_signal,
    compute_microprice_signal_samples,
    regress_microprice_signal,
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


class NoopStrategy:
    def on_book_update(self, book, timestamp_ms):
        return []

    def on_trade(self, trade, book):
        return []

    def on_fill(self, fill):
        return []

    def on_order_placed(self, request, order):
        return None


def _parse_start(value: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H", "%Y-%m-%d %H", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(
        "Expected timestamp like 2026-04-16T12 or '2026-04-16 12'"
    )


def _select_depth_files(data_root: Path, symbol: str, start: datetime, hours: int) -> list[Path]:
    symbol = symbol.lower()
    depth_dir = data_root / "raw" / symbol
    files = []
    missing = []

    for offset in range(hours):
        ts = start + timedelta(hours=offset)
        stamp = ts.strftime("%Y%m%d_%H")
        path = depth_dir / f"{symbol}_depth_{stamp}00.jsonl.gz"
        if path.exists():
            files.append(path)
        else:
            missing.append(path)

    if missing:
        missing_list = "\n".join(f"  {path}" for path in missing)
        raise FileNotFoundError(f"Missing depth input files:\n{missing_list}")

    return files


def _run_window(args, start: datetime) -> dict:
    depth_files = _select_depth_files(args.data_root, args.symbol, start, args.hours)
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
    engine = ReplayEngine(config)
    result = engine.run(NoopStrategy())

    rows = compute_microprice_signal_samples(
        result.book_samples,
        sample_interval_ms=args.sample_interval_ms,
        horizons_ms=DEFAULT_HORIZONS_MS,
        max_staleness_ms=args.max_staleness_ms,
        max_future_lag_ms=args.max_future_lag_ms,
    )
    regressions = regress_microprice_signal(
        rows,
        sample_interval_ms=args.sample_interval_ms,
    )
    buckets = bucket_forward_drift_by_signal(rows)
    return {
        "start": start,
        "label": start.strftime("%Y-%m-%d %H:00"),
        "book_samples": len(result.book_samples),
        "signal_samples": rows,
        "regressions": regressions,
        "buckets": buckets,
    }


def _decimal_json(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, Decimal):
        return f"{value:.6f}"
    return str(value)


def _write_regression_csv(path: Path, window_rows: Sequence[dict],
                          pooled: Sequence[MicropriceRegression]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["window"] + [field.name for field in fields(MicropriceRegression)]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for window in window_rows:
            for row in window["regressions"]:
                out = asdict(row)
                out["window"] = window["label"]
                writer.writerow(out)
        for row in pooled:
            out = asdict(row)
            out["window"] = "pooled"
            writer.writerow(out)


def _write_bucket_csv(path: Path, window_rows: Sequence[dict],
                      pooled: Sequence[SignalBucket]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["window"] + [field.name for field in fields(SignalBucket)]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for window in window_rows:
            for row in window["buckets"]:
                out = asdict(row)
                out["window"] = window["label"]
                writer.writerow(out)
        for row in pooled:
            out = asdict(row)
            out["window"] = "pooled"
            writer.writerow(out)


def _print_regressions(title: str, rows: Sequence[MicropriceRegression]) -> None:
    print(title)
    print(
        f"{'horizon':<8} {'n':>7} {'beta':>12} {'t_hac':>12} "
        f"{'r2':>12} {'x_std':>12} {'beta*xstd':>12}"
    )
    for row in rows:
        print(
            f"{row.horizon:<8} {row.n:>7} {_fmt(row.beta):>12} "
            f"{_fmt(row.t_stat):>12} {_fmt(row.r2):>12} "
            f"{_fmt(row.x_std_bps):>12} {_fmt(row.predicted_drift_1std_bps):>12}"
        )
    print()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Regress future mid drift on microprice deviation"
    )
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--starts", nargs="+", default=DEFAULT_STARTS)
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--sample-interval-ms", type=int, default=1_000)
    parser.add_argument("--max-staleness-ms", type=int, default=1_000)
    parser.add_argument("--max-future-lag-ms", type=int, default=1_000)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path("results/microprice_signal"))
    return parser.parse_args()


def main():
    args = parse_args()
    starts = [_parse_start(value) for value in args.starts]

    windows = []
    for idx, start in enumerate(starts, start=1):
        print(f"[{idx}/{len(starts)}] {start.strftime('%Y-%m-%d %H:00')}")
        window = _run_window(args, start)
        windows.append(window)
        _print_regressions(window["label"], window["regressions"])

    pooled_samples = [
        row for window in windows for row in window["signal_samples"]
    ]
    pooled_regressions = regress_microprice_signal(
        pooled_samples,
        sample_interval_ms=args.sample_interval_ms,
    )
    pooled_buckets = bucket_forward_drift_by_signal(pooled_samples)
    _print_regressions("pooled", pooled_regressions)

    first = starts[0].strftime("%Y%m%d_%H")
    last = starts[-1].strftime("%Y%m%d_%H")
    run_id = (
        f"{args.symbol.lower()}_{first}_to_{last}_"
        f"{len(starts)}windows_{args.sample_interval_ms}ms"
    )
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_regression_csv(run_dir / "regressions.csv", windows, pooled_regressions)
    _write_bucket_csv(run_dir / "signal_buckets.csv", windows, pooled_buckets)

    summary = {
        "params": {
            "symbol": args.symbol.lower(),
            "starts": [start.isoformat() for start in starts],
            "hours": args.hours,
            "sample_interval_ms": args.sample_interval_ms,
            "max_staleness_ms": args.max_staleness_ms,
            "max_future_lag_ms": args.max_future_lag_ms,
            "horizons_ms": DEFAULT_HORIZONS_MS,
        },
        "windows": [
            {
                "label": window["label"],
                "book_samples": window["book_samples"],
                "signal_rows": len(window["signal_samples"]),
                "regressions": window["regressions"],
                "buckets": window["buckets"],
            }
            for window in windows
        ],
        "pooled": {
            "signal_rows": len(pooled_samples),
            "regressions": pooled_regressions,
            "buckets": pooled_buckets,
        },
    }
    with (run_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=_decimal_json)

    print(f"Wrote microprice signal artifacts to {run_dir}")


if __name__ == "__main__":
    main()
