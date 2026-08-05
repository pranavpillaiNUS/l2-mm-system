"""Verify a completed event-driven V3 artifact tree against its manifest."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from scripts.run_l2_panel import (
    ARTIFACT_MANIFEST_VERSION,
    DEVELOPMENT_PANEL_SHA256,
    INTEGRITY_MANIFEST_FILE_SHA256,
    INTEGRITY_MANIFEST_SHA256,
    _file_sha256,
    _source_fingerprint,
)
from src.execution.provenance import guard_event_driven_output_path
from src.execution.simulator import EQUAL_TIMESTAMP_POLICY, EXECUTION_MODEL_VERSION
from src.replay.depth_parser import SNAPSHOT_TIME_POLICY


def _decimal_equals(value: object, expected: str) -> bool:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    return parsed.is_finite() and parsed == Decimal(expected)


def _artifact_fingerprints(
    output_root: Path,
    status_dir: Path,
    manifest_path: Path,
) -> dict[str, str]:
    status_root = status_dir.resolve(strict=False)
    fingerprints: dict[str, str] = {}
    for path in sorted(output_root.rglob("*")):
        if (
            not path.is_file()
            or path.is_symlink()
            or path == manifest_path
            or path.name.endswith("_cache.json")
        ):
            continue
        resolved = path.resolve(strict=False)
        if resolved == status_root or status_root in resolved.parents:
            continue
        fingerprints[str(path.relative_to(output_root))] = _file_sha256(path)
    return fingerprints


def verify(output_root: Path, status_dir: Path) -> dict:
    guard_event_driven_output_path(output_root)
    manifest_path = output_root / "ARTIFACT_MANIFEST.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError(f"V3 artifact manifest is missing or redirected: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if payload.get("manifest_version") != ARTIFACT_MANIFEST_VERSION:
        raise ValueError("unsupported V3 artifact manifest version")
    if payload.get("execution_model_version") != EXECUTION_MODEL_VERSION:
        raise ValueError("V3 artifact manifest has the wrong execution model")
    if payload.get("equal_timestamp_policy") != EQUAL_TIMESTAMP_POLICY:
        raise ValueError("V3 artifact manifest has the wrong timestamp policy")
    if payload.get("snapshot_time_policy") != SNAPSHOT_TIME_POLICY:
        raise ValueError("V3 artifact manifest has the wrong snapshot-time policy")
    if payload.get("trade_gap_policy") != "pause_until_snapshot":
        raise ValueError("V3 artifact manifest has the wrong trade-gap policy")
    if payload.get("source_fingerprint") != _source_fingerprint():
        raise ValueError("current Python source differs from the V3 artifact manifest")

    panel = payload.get("development_panel")
    if not isinstance(panel, dict) or panel.get("sha256") != DEVELOPMENT_PANEL_SHA256:
        raise ValueError("V3 manifest does not identify the frozen development panel")
    panel_path = Path(str(panel.get("path", "")))
    if not panel_path.is_file() or _file_sha256(panel_path) != DEVELOPMENT_PANEL_SHA256:
        raise ValueError("frozen development panel file no longer matches V3 manifest")
    with panel_path.open("r", encoding="utf-8", newline="") as f:
        panel_rows = list(csv.DictReader(f))
    panel_starts = [datetime.fromisoformat(row["start"]) for row in panel_rows]
    if panel.get("starts") != [start.isoformat() for start in panel_starts]:
        raise ValueError("V3 manifest start set differs from the frozen panel")

    experiment = payload.get("experiment")
    if (
        not isinstance(experiment, dict)
        or experiment.get("symbol") != "btcusdt"
        or experiment.get("strategy") != "microprice"
        or type(experiment.get("hours")) is not int
        or experiment["hours"] != 5
        or type(experiment.get("session_hours")) is not int
        or experiment["session_hours"] != 1
        or not _decimal_equals(experiment.get("half_spread"), "2.00")
        or not _decimal_equals(experiment.get("order_qty"), "0.001")
        or not _decimal_equals(experiment.get("max_position"), "0.01")
        or experiment.get("requote_interval_ms") != 5000
        or experiment.get("latency_ms") != 10
        or experiment.get("jitter_ms") != 0
        or experiment.get("cancel_latency_ms") != 10
        or experiment.get("cancel_jitter_ms") != 0
        or experiment.get("maker_bps") != 2
        or experiment.get("taker_bps") != 5
        or experiment.get("queue_credits") != ["0", "1"]
    ):
        raise ValueError("V3 manifest does not describe the canonical experiment")

    integrity = payload.get("raw_integrity_manifest")
    if (
        not isinstance(integrity, dict)
        or integrity.get("file_sha256") != INTEGRITY_MANIFEST_FILE_SHA256
        or integrity.get("identity_sha256") != INTEGRITY_MANIFEST_SHA256
    ):
        raise ValueError("V3 manifest has the wrong raw-integrity identity")
    integrity_path = Path(str(integrity.get("path", "")))
    if (
        not integrity_path.is_file()
        or _file_sha256(integrity_path) != INTEGRITY_MANIFEST_FILE_SHA256
    ):
        raise ValueError("raw-integrity manifest file no longer matches V3 manifest")

    raw_inputs = payload.get("raw_inputs")
    if not isinstance(raw_inputs, list) or not raw_inputs:
        raise ValueError("V3 manifest contains no selected raw inputs")
    seen_starts = set()
    for row in raw_inputs:
        if not isinstance(row, dict) or row.get("start") in seen_starts:
            raise ValueError("V3 manifest has invalid or duplicate raw-input rows")
        seen_starts.add(row["start"])
        for prefix in ("depth", "trade"):
            path = Path(str(row.get(f"{prefix}_path", "")))
            expected = row.get(f"{prefix}_sha256")
            if path.is_symlink() or not path.is_file() or _file_sha256(path) != expected:
                raise ValueError(f"V3 raw input differs from manifest: {path}")
    expected_raw_starts = {
        (start + timedelta(hours=offset)).isoformat()
        for start in panel_starts
        for offset in range(experiment["hours"])
    }
    if seen_starts != expected_raw_starts:
        raise ValueError("V3 manifest raw-input set differs from the frozen panel")

    expected_artifacts = payload.get("artifacts")
    if not isinstance(expected_artifacts, dict):
        raise ValueError("V3 manifest has no artifact fingerprint map")
    actual_artifacts = _artifact_fingerprints(
        output_root, status_dir, manifest_path
    )
    if actual_artifacts != expected_artifacts:
        raise ValueError("V3 artifact tree differs from its committed manifest")
    return payload


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("results/panels/btcusdt_l2_panel_v3_event_driven"),
    )
    parser.add_argument(
        "--status-dir",
        type=Path,
        default=Path("results/panels/btcusdt_l2_panel_v3_event_driven/status"),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    payload = verify(args.output_root, args.status_dir)
    print(
        "OK: V3 artifacts verified at source fingerprint "
        f"{payload['source_fingerprint']}"
    )


if __name__ == "__main__":
    main()
