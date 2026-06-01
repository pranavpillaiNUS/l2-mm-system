"""Analyze OFI predictiveness and conditional-on-fill toxicity on L2 panels."""

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
from src.analysis.ofi_signal import (
    DEFAULT_OFI_FILL_HORIZONS_MS,
    DEFAULT_OFI_HORIZONS_MS,
    OFIBucket,
    OFIFillToxicityBucket,
    OFIFillToxicityRow,
    OFIRegression,
    OFISignalSample,
    bucket_forward_drift_by_ofi,
    bucket_ofi_fill_toxicity,
    compute_ofi_fill_toxicity,
    compute_ofi_signal_samples,
    evaluate_ofi_gates,
    regress_ofi_signal,
)
from src.execution.queue_credit import (
    credit_from_legacy_mode,
    parse_queue_credit,
    queue_credit_suffix,
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


def _block_windows(args) -> list[SessionWindow]:
    return [SessionWindow(start=_parse_start(value), hours=args.hours) for value in args.starts]


def _session_windows(args) -> list[tuple[str, SessionWindow]]:
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


def _run_depth_window(args, window: SessionWindow, ofi_interval_ms: int) -> dict:
    depth_files, _ = _select_files(args.data_root, args.symbol, window)
    config = ReplayConfig(
        depth_files=depth_files,
        trade_files=[],
        sim_config=SimConfig(
            base_latency_ms=0,
            jitter_ms=0,
            maker_bps=0,
            taker_bps=0,
            queue_cancellation_credit=args.queue_cancellation_credit,
        ),
        record_book_samples=True,
    )
    result = ReplayEngine(config).run(NoopStrategy())
    rows = compute_ofi_signal_samples(
        result.book_samples,
        sample_interval_ms=args.sample_interval_ms,
        ofi_interval_ms=ofi_interval_ms,
        horizons_ms=DEFAULT_OFI_HORIZONS_MS,
        max_staleness_ms=args.max_staleness_ms,
        max_future_lag_ms=args.max_future_lag_ms,
    )
    return _signal_window(
        window.label,
        len(result.book_samples),
        rows,
        sample_interval_ms=args.sample_interval_ms,
    )


def _signal_window(
    label: str,
    book_samples: int,
    rows: list[OFISignalSample],
    *,
    sample_interval_ms: int,
) -> dict:
    regressions = [
        *regress_ofi_signal(rows, signal="normalized_ofi",
                            sample_interval_ms=sample_interval_ms),
        *regress_ofi_signal(rows, signal="raw_ofi",
                            sample_interval_ms=sample_interval_ms),
    ]
    buckets = bucket_forward_drift_by_ofi(rows, signal="normalized_ofi")
    return {
        "label": label,
        "book_samples": book_samples,
        "signal_samples": rows,
        "regressions": regressions,
        "buckets": buckets,
    }


def _run_fill_session(args, block_label: str, window: SessionWindow,
                      ofi_interval_ms: int) -> list[dict]:
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
            queue_cancellation_credit=args.queue_cancellation_credit,
        ),
        record_book_samples=True,
    )
    result = ReplayEngine(config).run(strategy)
    rows = compute_ofi_fill_toxicity(
        result.fills,
        result.book_samples,
        horizons_ms=DEFAULT_OFI_FILL_HORIZONS_MS,
        ofi_interval_ms=ofi_interval_ms,
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


def _load_or_build_signal_windows(args, *, ofi_interval_ms: int) -> list[dict]:
    if args.unconditional_cache is None:
        return _scan_signal_windows(args, ofi_interval_ms=ofi_interval_ms)

    payload = {"analyses": {}}
    if args.unconditional_cache.exists():
        with args.unconditional_cache.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    key = str(ofi_interval_ms)
    expected_params = {
        "symbol": args.symbol.lower(),
        "starts": args.starts,
        "hours": args.hours,
        "sample_interval_ms": args.sample_interval_ms,
        "ofi_interval_ms": ofi_interval_ms,
        "max_staleness_ms": args.max_staleness_ms,
        "max_future_lag_ms": args.max_future_lag_ms,
        "data_root": str(args.data_root),
    }
    cached = payload["analyses"].get(key)
    if cached is not None:
        if cached["params"] != expected_params:
            raise ValueError("OFI unconditional cache does not match requested analysis")
        return [
            _signal_window(
                row["label"],
                int(row["book_samples"]),
                [_signal_sample_from_dict(sample) for sample in row["signal_samples"]],
                sample_interval_ms=args.sample_interval_ms,
            )
            for row in cached["windows"]
        ]

    windows = _scan_signal_windows(args, ofi_interval_ms=ofi_interval_ms)
    payload["analyses"][key] = {
        "params": expected_params,
        "windows": [
            {
                "label": row["label"],
                "book_samples": row["book_samples"],
                "signal_samples": row["signal_samples"],
            }
            for row in windows
        ],
    }
    args.unconditional_cache.parent.mkdir(parents=True, exist_ok=True)
    with args.unconditional_cache.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=_jsonable)
    return windows


def _scan_signal_windows(args, *, ofi_interval_ms: int) -> list[dict]:
    windows = []
    for idx, window in enumerate(_block_windows(args), start=1):
        print(f"[OFI {ofi_interval_ms}ms signal {idx}/{len(args.starts)}] {window.label}")
        windows.append(_run_depth_window(args, window, ofi_interval_ms))
    return windows


def _signal_sample_from_dict(row: dict) -> OFISignalSample:
    values = dict(row)
    for field in (
        "mid",
        "future_mid",
        "raw_ofi",
        "normalized_ofi",
        "forward_drift_bps",
    ):
        values[field] = Decimal(values[field])
    return OFISignalSample(**values)


def _run_analysis(args, *, ofi_interval_ms: int) -> dict:
    windows = _load_or_build_signal_windows(args, ofi_interval_ms=ofi_interval_ms)

    pooled_samples = [row for window in windows for row in window["signal_samples"]]
    pooled_regressions = [
        *regress_ofi_signal(pooled_samples, signal="normalized_ofi",
                            sample_interval_ms=args.sample_interval_ms),
        *regress_ofi_signal(pooled_samples, signal="raw_ofi",
                            sample_interval_ms=args.sample_interval_ms),
    ]
    pooled_buckets = bucket_forward_drift_by_ofi(pooled_samples, signal="normalized_ofi")

    fill_rows: list[dict] = []
    sessions = _session_windows(args)
    for idx, (block_label, window) in enumerate(sessions, start=1):
        print(f"[OFI {ofi_interval_ms}ms fills {idx}/{len(sessions)}] {window.label}")
        fill_rows.extend(_run_fill_session(args, block_label, window, ofi_interval_ms))
    toxicity_rows = [
        OFIFillToxicityRow(
            **{field.name: row[field.name] for field in fields(OFIFillToxicityRow)}
        )
        for row in fill_rows
    ]
    fill_buckets = bucket_ofi_fill_toxicity(toxicity_rows)
    window_regressions = [
        regression
        for window in windows
        for regression in window["regressions"]
    ]
    gates = evaluate_ofi_gates(
        window_regressions=window_regressions,
        pooled_regressions=pooled_regressions,
        fill_buckets=fill_buckets,
    )
    return {
        "ofi_interval_ms": ofi_interval_ms,
        "windows": windows,
        "pooled_samples": pooled_samples,
        "pooled_regressions": pooled_regressions,
        "pooled_buckets": pooled_buckets,
        "fill_rows": fill_rows,
        "fill_buckets": fill_buckets,
        "gates": gates,
    }


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
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    return value


def _write_regression_csv(path: Path, analysis: dict) -> None:
    fieldnames = ["window"] + [field.name for field in fields(OFIRegression)]
    rows = []
    for window in analysis["windows"]:
        for regression in window["regressions"]:
            row = asdict(regression)
            row["window"] = window["label"]
            rows.append(row)
    for regression in analysis["pooled_regressions"]:
        row = asdict(regression)
        row["window"] = "pooled"
        rows.append(row)
    _write_dict_csv(path, rows, fieldnames)


def _write_bucket_csv(path: Path, buckets: Sequence[OFIBucket]) -> None:
    _write_dataclass_csv(path, buckets, OFIBucket)


def _write_fill_rows_csv(path: Path, rows: Sequence[dict]) -> None:
    fieldnames = [
        "block_start",
        "session_start",
        *[field.name for field in fields(OFIFillToxicityRow)],
    ]
    _write_dict_csv(path, rows, fieldnames)


def _write_fill_bucket_csv(path: Path, buckets: Sequence[OFIFillToxicityBucket]) -> None:
    _write_dataclass_csv(path, buckets, OFIFillToxicityBucket)


def _write_dataclass_csv(path: Path, rows: Sequence[object], cls) -> None:
    fieldnames = [field.name for field in fields(cls)]
    _write_dict_csv(path, [asdict(row) for row in rows], fieldnames)


def _write_dict_csv(path: Path, rows: Sequence[dict], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _decimal_str(row.get(key)) for key in fieldnames})


def _write_analysis(run_dir: Path, analysis: dict, prefix: str = "") -> None:
    stem = f"{prefix}_" if prefix else ""
    _write_regression_csv(run_dir / f"{stem}regressions.csv", analysis)
    _write_bucket_csv(run_dir / f"{stem}signal_buckets.csv", analysis["pooled_buckets"])
    _write_fill_rows_csv(run_dir / f"{stem}fill_toxicity_rows.csv", analysis["fill_rows"])
    _write_fill_bucket_csv(run_dir / f"{stem}fill_buckets.csv", analysis["fill_buckets"])


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run unconditional and conditional OFI diagnostics"
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
    parser.add_argument("--queue-cancellation-credit", default="1.0")
    parser.add_argument("--queue-cancellation-mode",
                        choices=["proportional", "none"],
                        help=argparse.SUPPRESS)
    parser.add_argument("--sample-interval-ms", type=int, default=1_000)
    parser.add_argument("--ofi-interval-ms", type=int, default=1_000)
    parser.add_argument("--max-staleness-ms", type=int, default=1_000)
    parser.add_argument("--max-future-lag-ms", type=int, default=1_000)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/ofi_signal"))
    parser.add_argument("--unconditional-cache", type=Path)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.queue_cancellation_mode is not None:
        args.queue_cancellation_credit = credit_from_legacy_mode(
            args.queue_cancellation_mode
        )
    else:
        args.queue_cancellation_credit = parse_queue_credit(
            args.queue_cancellation_credit
        )

    analysis = _run_analysis(args, ofi_interval_ms=args.ofi_interval_ms)

    first = _parse_start(args.starts[0]).strftime("%Y%m%d_%H")
    last = _parse_start(args.starts[-1]).strftime("%Y%m%d_%H")
    run_id = (
        f"{args.symbol.lower()}_{args.strategy}_ofi_"
        f"{first}_to_{last}_{len(args.starts)}blocks_"
        f"{args.ofi_interval_ms}ms{queue_credit_suffix(args.queue_cancellation_credit)}"
    )
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_analysis(run_dir, analysis)

    fallback = None
    if analysis["gates"].marginal_5s_fallback and args.ofi_interval_ms != 5_000:
        fallback = _run_analysis(args, ofi_interval_ms=5_000)
        _write_analysis(run_dir, fallback, prefix="ofi_5000ms")

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
            "queue_cancellation_credit": args.queue_cancellation_credit,
            "sample_interval_ms": args.sample_interval_ms,
            "ofi_interval_ms": args.ofi_interval_ms,
            "horizons_ms": DEFAULT_OFI_HORIZONS_MS,
            "fill_horizons_ms": DEFAULT_OFI_FILL_HORIZONS_MS,
            "five_second_fallback_rule": (
                "run if at least one gate passes and every failed numeric gate "
                "misses by less than 25%; exclude conditional inconclusive_power "
                "from numerical miss calculations"
            ),
        },
        "counts": {
            "windows": len(analysis["windows"]),
            "signal_rows": len(analysis["pooled_samples"]),
            "fill_horizon_rows": len(analysis["fill_rows"]),
        },
        "pooled_regressions": analysis["pooled_regressions"],
        "pooled_buckets": analysis["pooled_buckets"],
        "fill_buckets": analysis["fill_buckets"],
        "gates": analysis["gates"],
        "fallback_5s": None if fallback is None else {
            "counts": {
                "signal_rows": len(fallback["pooled_samples"]),
                "fill_horizon_rows": len(fallback["fill_rows"]),
            },
            "pooled_regressions": fallback["pooled_regressions"],
            "gates": fallback["gates"],
        },
    }
    with (run_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=_jsonable)

    print("OFI diagnostics")
    print(f"  Output: {run_dir}")
    print(f"  Verdict: {analysis['gates'].overall_verdict}")
    print(f"  Conditional status: {analysis['gates'].conditional_status}")
    print(f"  5s fallback run: {fallback is not None}")


if __name__ == "__main__":
    main()
