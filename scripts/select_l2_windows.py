"""Select frozen development and holdout L2 panels from an integrity manifest."""

import argparse
import csv
import hashlib
import json
import os
from bisect import bisect_right
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from scripts.compare_mm import _parse_start
from src.analysis.data_integrity import hourly_paths
from src.analysis.window_selection import (
    WindowCandidate,
    WindowDescriptor,
    build_window_descriptors,
    compare_regime_descriptors,
    jump_threshold,
    non_overlapping_capacity,
    select_balanced_windows,
    with_jump_count,
)
from src.execution.simulator import SimConfig
from src.replay.engine import BookSample, ReplayConfig, ReplayEngine


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


def _parse_hour(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _load_manifest(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _valid_hours(manifest: dict, *, cutoff: datetime) -> list[datetime]:
    return sorted(
        datetime.fromisoformat(row["start"])
        for row in manifest["hours"]
        if row["valid"] and datetime.fromisoformat(row["start"]) < cutoff
    )


def _regular_second_mids(
    samples: list[BookSample],
    *,
    start: datetime,
    max_staleness_ms: int = 1_000,
) -> list[tuple[int, Decimal]]:
    if not samples:
        return []
    ordered = sorted(samples, key=lambda row: row.timestamp_ms)
    times = [row.timestamp_ms for row in ordered]
    start_ms = _epoch_ms(start)
    end_ms = _epoch_ms(start + timedelta(hours=1))
    out = []
    for target_ms in range(start_ms, end_ms, 1_000):
        idx = bisect_right(times, target_ms) - 1
        if idx < 0:
            continue
        sample = ordered[idx]
        if target_ms - sample.timestamp_ms > max_staleness_ms:
            continue
        out.append((target_ms, sample.mid))
    return out


def _scan_depth_hour(args, start: datetime) -> list[tuple[int, Decimal]]:
    return _scan_depth_hour_values(args.data_root, args.symbol.lower(), start)


def _scan_depth_hour_values(
    data_root: Path,
    symbol: str,
    start: datetime,
) -> list[tuple[int, Decimal]]:
    depth_path, _ = hourly_paths(data_root, symbol, start)
    result = ReplayEngine(ReplayConfig(
        depth_files=[depth_path],
        trade_files=[],
        sim_config=SimConfig(
            base_latency_ms=0,
            jitter_ms=0,
            maker_bps=0,
            taker_bps=0,
        ),
        record_book_samples=True,
    )).run(NoopStrategy())
    if result.stats.gaps_detected:
        raise ValueError(f"Manifest accepted depth-gap hour: {start.isoformat()}")
    return _regular_second_mids(result.book_samples, start=start)


def _scan_depth_hour_task(task) -> tuple[datetime, list[tuple[int, Decimal]]]:
    data_root, symbol, hour = task
    return hour, _scan_depth_hour_values(data_root, symbol, hour)


def _load_or_build_hourly_cache(
    args,
    *,
    manifest_sha256: str,
    valid_hours: list[datetime],
) -> dict[datetime, list[tuple[int, Decimal]]]:
    cache_path = args.output_dir / "hourly_depth_descriptors.json"
    payload = {"manifest_sha256": manifest_sha256, "hours": []}
    if cache_path.exists():
        with cache_path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        if loaded.get("manifest_sha256") == manifest_sha256:
            payload = loaded

    cached = {
        datetime.fromisoformat(row["start"]): [
            (int(timestamp_ms), Decimal(mid))
            for timestamp_ms, mid in row["second_mids"]
        ]
        for row in payload["hours"]
    }
    missing = [hour for hour in valid_hours if hour not in cached]
    tasks = [
        (args.data_root, args.symbol.lower(), hour)
        for hour in missing
    ]
    with ProcessPoolExecutor(max_workers=args.scan_workers) as executor:
        for idx, (hour, mids) in enumerate(
            executor.map(_scan_depth_hour_task, tasks),
            start=1,
        ):
            print(f"[{idx}/{len(missing)}] scanned depth hour {hour:%Y-%m-%d %H:00}")
            cached[hour] = mids

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = {
        "manifest_sha256": manifest_sha256,
        "hours": [
            {
                "start": hour.isoformat(),
                "second_mids": [
                    [timestamp_ms, format(mid, "f")]
                    for timestamp_ms, mid in cached[hour]
                ],
            }
            for hour in sorted(cached)
        ],
    }
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump(out, f)
    return cached


def _to_candidates(descriptors: list[WindowDescriptor]) -> list[WindowCandidate]:
    return [
        WindowCandidate(
            start=row.start,
            hours=row.hours,
            realized_vol_bps=row.realized_vol_1s_bps,
            mid_drift_bps=row.mid_drift_bps,
        )
        for row in descriptors
    ]


def _select_panel(
    descriptors: list[WindowDescriptor],
    *,
    anchors: list[datetime],
    requested: int,
    minimum: int,
) -> tuple[list, int]:
    candidates = _to_candidates(descriptors)
    capacity = non_overlapping_capacity(candidates)
    if capacity < minimum:
        raise ValueError(
            f"Clean non-overlapping capacity {capacity} is below minimum {minimum}"
        )
    selected = select_balanced_windows(
        candidates,
        anchor_starts=anchors,
        total_windows=min(requested, capacity),
    )
    return selected, capacity


def _assert_disjoint_panels(development, holdout) -> None:
    for dev in development:
        for out in holdout:
            if dev.start < out.start + timedelta(hours=out.hours) and out.start < (
                dev.start + timedelta(hours=dev.hours)
            ):
                raise ValueError(
                    f"Development and holdout windows overlap: {dev.start} and {out.start}"
                )


def _panel_rows(
    selected,
    descriptors_by_start: dict[datetime, WindowDescriptor],
    hourly_mids: dict[datetime, list[tuple[int, Decimal]]],
    *,
    threshold_bps: Decimal,
) -> tuple[list[dict], list[WindowDescriptor]]:
    rows = []
    enriched = []
    for selected_row in selected:
        descriptor = with_jump_count(
            descriptors_by_start[selected_row.start],
            hourly_mids,
            threshold_bps=threshold_bps,
        )
        enriched.append(descriptor)
        rows.append({
            "start": selected_row.start.isoformat(),
            "hours": selected_row.hours,
            "source": selected_row.source,
            "utc_bucket": selected_row.utc_bucket,
            "vol_tercile": selected_row.vol_tercile,
            "mid_drift_bps": descriptor.mid_drift_bps,
            "abs_mid_drift_bps": descriptor.abs_mid_drift_bps,
            "realized_vol_1s_bps": descriptor.realized_vol_1s_bps,
            "realized_vol_10s_bps": descriptor.realized_vol_10s_bps,
            "realized_vol_1m_bps": descriptor.realized_vol_1m_bps,
            "jump_count": descriptor.jump_count,
        })
    return rows, enriched


def _utc_distribution(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[row["utc_bucket"]] = out.get(row["utc_bucket"], 0) + 1
    return out


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})


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
    return "" if value is None else str(value)


def _sha256_json(value) -> str:
    encoded = json.dumps(value, sort_keys=True, default=_jsonable).encode()
    return hashlib.sha256(encoded).hexdigest()


def _epoch_ms(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def parse_args():
    parser = argparse.ArgumentParser(description="Select frozen L2 panel windows")
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--development-target", type=int, default=24)
    parser.add_argument("--development-minimum", type=int, default=18)
    parser.add_argument("--holdout-target", type=int, default=12)
    parser.add_argument("--holdout-minimum", type=int, default=8)
    parser.add_argument("--anchors", nargs="+", default=DEFAULT_ANCHORS)
    parser.add_argument("--cutoff", type=_parse_hour,
                        default=datetime.fromisoformat("2026-06-01T00:00"))
    parser.add_argument("--development-end", type=_parse_hour,
                        default=datetime.fromisoformat("2026-05-20T00:00"))
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--scan-workers", type=int,
                        default=min(4, os.cpu_count() or 1))
    parser.add_argument("--integrity-manifest", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2/"
                                     "integrity_manifest.json"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2"))
    return parser.parse_args()


def main():
    args = parse_args()
    if args.scan_workers <= 0:
        raise ValueError("--scan-workers must be positive")
    manifest = _load_manifest(args.integrity_manifest)
    valid_hours = _valid_hours(manifest, cutoff=args.cutoff)
    hourly_mids = _load_or_build_hourly_cache(
        args,
        manifest_sha256=manifest["manifest_sha256"],
        valid_hours=valid_hours,
    )
    descriptors = build_window_descriptors(hourly_mids, hours=args.hours)
    development = [
        row for row in descriptors
        if row.start + timedelta(hours=args.hours) <= args.development_end
    ]
    holdout = [
        row for row in descriptors
        if args.development_end <= row.start
        and row.start + timedelta(hours=args.hours) <= args.cutoff
    ]
    selected_dev, development_capacity = _select_panel(
        development,
        anchors=[_parse_start(value) for value in args.anchors],
        requested=args.development_target,
        minimum=args.development_minimum,
    )
    selected_holdout, holdout_capacity = _select_panel(
        holdout,
        anchors=[],
        requested=args.holdout_target,
        minimum=args.holdout_minimum,
    )
    _assert_disjoint_panels(selected_dev, selected_holdout)

    by_start = {row.start: row for row in descriptors}
    base_dev_descriptors = [by_start[row.start] for row in selected_dev]
    threshold = jump_threshold(base_dev_descriptors, hourly_mids)
    development_rows, development_descriptors = _panel_rows(
        selected_dev, by_start, hourly_mids, threshold_bps=threshold
    )
    holdout_rows, holdout_descriptors = _panel_rows(
        selected_holdout, by_start, hourly_mids, threshold_bps=threshold
    )
    comparison = compare_regime_descriptors(
        development_descriptors, holdout_descriptors
    )

    _write_csv(args.output_dir / "development_windows.csv", development_rows)
    _write_csv(args.output_dir / "holdout_windows.csv", holdout_rows)
    _write_csv(args.output_dir / "windows.csv", development_rows)
    summary = {
        "params": {
            "symbol": args.symbol.lower(),
            "hours": args.hours,
            "cutoff": args.cutoff,
            "development_end": args.development_end,
            "selection_rule": (
                "manifest-valid non-overlapping windows only; retain development "
                "anchors; balance UTC buckets and one-second realized-vol terciles; "
                "earliest start wins ties"
            ),
        },
        "integrity_manifest_sha256": manifest["manifest_sha256"],
        "jump_threshold_1s_bps": threshold,
        "counts": {
            "valid_hours": len(valid_hours),
            "development_candidate_starts": len(development),
            "development_nonoverlap_capacity": development_capacity,
            "development_selected": len(development_rows),
            "holdout_candidate_starts": len(holdout),
            "holdout_nonoverlap_capacity": holdout_capacity,
            "holdout_selected": len(holdout_rows),
        },
        "utc_distribution": {
            "development": _utc_distribution(development_rows),
            "holdout": _utc_distribution(holdout_rows),
        },
        "regime_comparison": comparison,
        "development_windows": development_rows,
        "holdout_windows": holdout_rows,
    }
    summary["panel_sha256"] = _sha256_json(summary)
    with (args.output_dir / "window_selection_summary.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(summary, f, indent=2, default=_jsonable)
    print(f"Wrote development and holdout panels to {args.output_dir}")
    print(f"Panel SHA-256: {summary['panel_sha256']}")
    print(f"Holdout regime label: {comparison['label']}")


if __name__ == "__main__":
    main()
