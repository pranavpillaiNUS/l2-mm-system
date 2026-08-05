"""Quantify legacy REST-snapshot timestamp exposure on a selected panel.

The historical recorder tagged a snapshot with request-start time, then made a
blocking REST request. This audit measures the first later local depth receipt,
the first valid bridge, and the sequence-valid post-response proxy boundary
used by current replay. Local and exchange clocks remain distinct.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.replay.depth_parser import SNAPSHOT_TIME_POLICY


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WINDOWS = Path(
    "results/panels/btcusdt_l2_panel_v2/development_windows.csv"
)
DEFAULT_INTEGRITY = Path(
    "results/panels/btcusdt_l2_panel_v2/integrity_manifest.json"
)
DEFAULT_RECONCILIATION = Path(
    "results/panels/btcusdt_l2_panel_v2/markout_reconciliation"
)
DEFAULT_OUTPUT = Path(
    "results/replay_correctness/snapshot_timing_panel24.json"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _epoch_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _artifact_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(path)


def _quantile(values: list[int], probability: float) -> float:
    if not values:
        raise ValueError("cannot summarize an empty timing sample")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _summary(values: list[int]) -> dict[str, float | int]:
    return {
        "n": len(values),
        "min": min(values),
        "median": round(_quantile(values, 0.5), 3),
        "mean": round(sum(values) / len(values), 3),
        "p90": round(_quantile(values, 0.9), 3),
        "p95": round(_quantile(values, 0.95), 3),
        "max": max(values),
    }


def _selected_hours(path: Path) -> list[datetime]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    starts = []
    for row in rows:
        start = datetime.fromisoformat(row["start"])
        hours = int(row["hours"])
        starts.extend(start + timedelta(hours=offset) for offset in range(hours))
    if len(starts) != len(set(starts)):
        raise ValueError("selected development windows overlap")
    return sorted(starts)


def _audit_depth_file(path: Path, *, hour: datetime, expected_sha: str) -> list[dict]:
    if _sha256(path) != expected_sha:
        raise ValueError(f"depth file hash differs from integrity manifest: {path}")

    observations: list[dict] = []
    pending: dict | None = None
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            record = json.loads(raw_line)
            if "type" in record:
                if pending is not None:
                    observations.append(pending)
                tag_field = "request_time" if "request_time" in record else "recv_time"
                tag_ms = _epoch_ms(record[tag_field])
                pending = {
                    "hour": hour.isoformat(),
                    "depth_path": str(path),
                    "depth_sha256": expected_sha,
                    "snapshot_line": line_number,
                    "last_update_id": int(record["data"]["lastUpdateId"]),
                    "legacy_pre_fetch_tag": "request_time" not in record,
                    "request_tag_ms": tag_ms,
                    "response_receipt_ms": (
                        _epoch_ms(record["recv_time"])
                        if "request_time" in record else None
                    ),
                    "first_later_depth_receipt_ms": None,
                    "first_later_receipt_delay_ms": None,
                    "bridge_event_time_ms": None,
                    "bridge_event_delay_ms": None,
                    "bridge_recv_time_ms": None,
                    "bridge_valid": False,
                    "policy_boundary_event_time_ms": None,
                    "policy_boundary_recv_time_ms": None,
                    "policy_boundary_delay_ms": None,
                    "policy_boundary_valid": False,
                    "buffered_retained_diffs": 0,
                }
                continue

            if pending is None:
                continue
            recv_ms = _epoch_ms(record["recv_time"])
            if (
                pending["first_later_depth_receipt_ms"] is None
                and recv_ms > pending["request_tag_ms"]
            ):
                pending["first_later_depth_receipt_ms"] = recv_ms
                pending["first_later_receipt_delay_ms"] = (
                    recv_ms - pending["request_tag_ms"]
                )

            data = record["data"]
            first_update_id = int(data["U"])
            last_update_id = int(data["u"])
            snapshot_id = pending["last_update_id"]
            if last_update_id <= snapshot_id:
                continue

            if pending["bridge_event_time_ms"] is None:
                bridge_ms = int(data["E"])
                pending["bridge_event_time_ms"] = bridge_ms
                pending["bridge_event_delay_ms"] = (
                    bridge_ms - pending["request_tag_ms"]
                )
                pending["bridge_recv_time_ms"] = recv_ms
                pending["bridge_valid"] = (
                    first_update_id <= snapshot_id + 1 <= last_update_id
                )
                pending["last_retained_update_id"] = last_update_id
                if not pending["bridge_valid"]:
                    observations.append(pending)
                    pending = None
                    continue
            else:
                pending["bridge_valid"] = (
                    pending["bridge_valid"]
                    and first_update_id == pending["last_retained_update_id"] + 1
                )
                pending["last_retained_update_id"] = last_update_id
                if not pending["bridge_valid"]:
                    observations.append(pending)
                    pending = None
                    continue

            pending["buffered_retained_diffs"] += 1
            cutoff_ms = (
                pending["response_receipt_ms"]
                if pending["response_receipt_ms"] is not None
                else pending["request_tag_ms"]
            )
            post_response = (
                recv_ms >= cutoff_ms
                if pending["response_receipt_ms"] is not None
                else recv_ms > cutoff_ms
            )
            if post_response:
                policy_ms = int(data["E"])
                pending["policy_boundary_event_time_ms"] = policy_ms
                pending["policy_boundary_recv_time_ms"] = recv_ms
                pending["policy_boundary_delay_ms"] = (
                    policy_ms - pending["request_tag_ms"]
                )
                pending["policy_boundary_valid"] = True
                pending.pop("last_retained_update_id", None)
                observations.append(pending)
                pending = None

    if pending is not None:
        observations.append(pending)
    return observations


def _fill_exposure(
    reconciliation_root: Path,
    observations: list[dict],
) -> tuple[dict, list[dict]]:
    by_session: dict[str, list[dict]] = {}
    for row in observations:
        by_session.setdefault(row["hour"][:13], []).append(row)

    endpoints = {}
    inputs = []
    for label, suffix_is_qc0 in (("1", False), ("0", True)):
        total = 0
        within_five_seconds = 0
        placed_before_bridge = 0
        minimum_fill_delay_ms: int | None = None
        for path in sorted(reconciliation_root.glob("*/pre_fill_drift.csv")):
            is_qc0 = path.parent.name.endswith("_qc0")
            if is_qc0 != suffix_is_qc0:
                continue
            input_rows = 0
            with path.open("r", encoding="utf-8", newline="") as handle:
                for fill in csv.DictReader(handle):
                    input_rows += 1
                    total += 1
                    session = datetime.strptime(
                        fill["session"], "%Y-%m-%d %H:%M"
                    ).isoformat()[:13]
                    snapshots = by_session.get(session, [])
                    fill_ms = int(fill["fill_time_ms"])
                    placed_ms = int(fill["placed_time_ms"])
                    for snapshot in snapshots:
                        tag_ms = snapshot["request_tag_ms"]
                        bridge_ms = snapshot["bridge_event_time_ms"]
                        delay = fill_ms - tag_ms
                        if 0 <= delay <= 5_000:
                            within_five_seconds += 1
                            minimum_fill_delay_ms = (
                                delay if minimum_fill_delay_ms is None
                                else min(minimum_fill_delay_ms, delay)
                            )
                            break
                    if any(
                        snapshot["policy_boundary_event_time_ms"] is not None
                        and snapshot["request_tag_ms"] <= placed_ms
                        < snapshot["policy_boundary_event_time_ms"]
                        for snapshot in snapshots
                    ):
                        placed_before_bridge += 1
            inputs.append(
                {
                    "path": _artifact_path(path),
                    "sha256": _sha256(path),
                    "queue_credit": label,
                    "rows": input_rows,
                }
            )
        endpoints[label] = {
            "fills": total,
            "fills_within_5s_of_legacy_snapshot_tag": within_five_seconds,
            "minimum_fill_delay_from_tag_ms": minimum_fill_delay_ms,
            "fills_from_orders_placed_before_policy_boundary": placed_before_bridge,
        }
    return endpoints, sorted(inputs, key=lambda row: row["path"])


def build_audit(
    *,
    windows_csv: Path,
    integrity_manifest: Path,
    data_root: Path,
    reconciliation_root: Path,
) -> dict:
    with integrity_manifest.open("r", encoding="utf-8") as handle:
        integrity = json.load(handle)
    inventory = {
        datetime.fromisoformat(row["start"]): row
        for row in integrity["hours"]
    }

    observations = []
    selected_hours = _selected_hours(windows_csv)
    for hour in selected_hours:
        row = inventory.get(hour)
        if row is None or not row.get("valid"):
            raise ValueError(f"selected hour is absent or invalid: {hour}")
        path = data_root / "raw" / "btcusdt" / Path(row["depth_path"]).name
        observations.extend(
            _audit_depth_file(
                path,
                hour=hour,
                expected_sha=row["depth_sha256"],
            )
        )

    bridged = [row for row in observations if row["bridge_event_time_ms"] is not None]
    policy_rows = [row for row in observations if row["policy_boundary_valid"]]
    later_receipt_delays = [
        row["first_later_receipt_delay_ms"]
        for row in observations
        if row["first_later_receipt_delay_ms"] is not None
    ]
    bridge_delays = [row["bridge_event_delay_ms"] for row in bridged]
    policy_delays = [row["policy_boundary_delay_ms"] for row in policy_rows]
    fill_exposure, reconciliation_inputs = _fill_exposure(
        reconciliation_root, observations
    )
    return {
        "schema_version": 1,
        "generator": {
            "path": _artifact_path(Path(__file__)),
            "sha256": _sha256(Path(__file__)),
        },
        "scope": {
            "panel": "frozen V2 development selection",
            "development_windows": 24,
            "selected_hours": len(selected_hours),
            "windows_csv": str(windows_csv),
            "windows_csv_sha256": _sha256(windows_csv),
            "integrity_manifest": str(integrity_manifest),
            "integrity_manifest_file_sha256": _sha256(integrity_manifest),
            "integrity_manifest_identity": integrity["manifest_sha256"],
        },
        "finding": {
            "legacy_recorder_semantics": (
                "snapshot recv_time was captured before the blocking REST fetch"
            ),
            "risk": (
                "legacy replay applied snapshot state before response completion; "
                "queue age and event eligibility can therefore be affected"
            ),
            "current_snapshot_time_policy": SNAPSHOT_TIME_POLICY,
            "current_policy_interpretation": (
                "pause replay at snapshot request, gate on the first sequence-valid "
                "retained depth receipt after response completion (or the legacy "
                "upper-bound proxy), then release reconstruction at that diff's "
                "exchange event time"
            ),
        },
        "counts": {
            "snapshots": len(observations),
            "legacy_pre_fetch_tags": sum(
                bool(row["legacy_pre_fetch_tag"]) for row in observations
            ),
            "valid_bridges": sum(bool(row["bridge_valid"]) for row in observations),
            "unbridged": len(observations) - len(bridged),
            "later_receipt_proxies": len(later_receipt_delays),
            "valid_policy_boundaries": len(policy_rows),
            "unreleased_snapshots": len(observations) - len(policy_rows),
        },
        "request_tag_to_first_later_depth_receipt_ms": _summary(
            later_receipt_delays
        ),
        "request_tag_to_bridge_event_time_ms": _summary(bridge_delays),
        "request_tag_to_policy_boundary_event_time_ms": _summary(policy_delays),
        "frozen_fill_proximity": fill_exposure,
        "reconciliation_inputs": reconciliation_inputs,
        "interpretation": (
            "The local-receipt statistic is an upper-bound proxy for REST response "
            "completion, not a measured round trip. The release gate uses local receipt "
            "order, but its replay timestamp remains exchange event time, so this is not "
            "a calibrated client-observation clock. Fill proximity is a bounded proxy "
            "diagnostic and does not exclude changed queue age or later eligibility. V3 "
            "requires a before/after development rerun under the versioned policy."
        ),
        "rows": observations,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-csv", type=Path, default=DEFAULT_WINDOWS)
    parser.add_argument("--integrity-manifest", type=Path, default=DEFAULT_INTEGRITY)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument(
        "--reconciliation-root", type=Path, default=DEFAULT_RECONCILIATION
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_audit(
        windows_csv=args.windows_csv,
        integrity_manifest=args.integrity_manifest,
        data_root=args.data_root,
        reconciliation_root=args.reconciliation_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote snapshot timing audit to {args.output}")


if __name__ == "__main__":
    main()
