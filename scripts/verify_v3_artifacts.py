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
    _command_sha256,
    _source_fingerprint,
)
from src.execution.provenance import guard_event_driven_output_path
from src.execution.simulator import EQUAL_TIMESTAMP_POLICY, EXECUTION_MODEL_VERSION
from src.replay.depth_parser import SNAPSHOT_TIME_POLICY


def _verify_completed_steps(payload: dict, output_root: Path, starts: list[datetime]) -> None:
    """A self-consistent empty or partial tree is not a completed panel."""
    required = {"microprice_signal", "execution_model_comparison", "fee_break_even"}
    for credit in ("0", "1"):
        required.update(
            f"reconcile_qc{credit}_{start.isoformat()}" for start in starts
        )
        required.update(
            f"{name}_qc{credit}" for name in (
                "bootstrap_ci", "microprice_fill_toxicity", "same_ms_audit",
                "tail_diagnostics", "ofi_signal",
            )
        )
    steps = payload.get("completed_steps")
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
        raise ValueError("V3 manifest has no complete derivation list")
    names = [step.get("step") for step in steps]
    if any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
        raise ValueError("V3 manifest has invalid or duplicate derivation steps")
    if set(names) != required:
        raise ValueError("V3 manifest does not contain the complete development workflow")
    root = output_root.resolve()
    artifacts = payload.get("artifacts", {})
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("V3 manifest contains no durable artifacts")
    for step in steps:
        command = step.get("command")
        outputs = step.get("expected_outputs")
        fingerprints = step.get("output_fingerprints")
        if (
            not isinstance(command, list) or not command
            or any(not isinstance(part, str) for part in command)
            or step.get("command_sha256") != _command_sha256(command)
            or not isinstance(outputs, list) or not outputs
            or any(not isinstance(path, str) for path in outputs)
            or len(set(outputs)) != len(outputs)
            or not isinstance(fingerprints, dict)
            or set(fingerprints) != set(outputs)
        ):
            raise ValueError(f"V3 derivation is incomplete: {step['step']}")
        for name in outputs:
            path = Path(name)
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"V3 derived output is absent or redirected: {path}")
            try:
                relative = path.absolute().relative_to(root)
            except ValueError as exc:
                raise ValueError(f"V3 derived output escapes its result root: {path}") from exc
            if path.resolve() != root / relative:
                raise ValueError(f"V3 derived output has a redirected parent: {path}")
            digest = _file_sha256(path)
            if artifacts.get(str(relative)) != digest or fingerprints[name] != f"file:{digest}":
                raise ValueError(f"V3 derivation hash does not match its artifact: {path}")


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
    with integrity_path.open("r", encoding="utf-8") as f:
        inventory = json.load(f)
    inventory_rows = {row["start"]: row for row in inventory["hours"]}

    raw_inputs = payload.get("raw_inputs")
    if not isinstance(raw_inputs, list) or not raw_inputs:
        raise ValueError("V3 manifest contains no selected raw inputs")
    seen_starts = set()
    for row in raw_inputs:
        if not isinstance(row, dict) or row.get("start") in seen_starts:
            raise ValueError("V3 manifest has invalid or duplicate raw-input rows")
        seen_starts.add(row["start"])
        frozen = inventory_rows.get(row["start"])
        if frozen is None or not frozen.get("valid"):
            raise ValueError("V3 raw input is absent or invalid in frozen inventory")
        for prefix in ("depth", "trade"):
            path = Path(str(row.get(f"{prefix}_path", "")))
            expected = row.get(f"{prefix}_sha256")
            if expected != frozen.get(f"{prefix}_sha256"):
                raise ValueError("V3 raw-input hash differs from frozen inventory")
            if path.is_symlink() or not path.is_file() or _file_sha256(path) != expected:
                raise ValueError(f"V3 raw input differs from manifest: {path}")
    expected_raw_starts = {
        (start + timedelta(hours=offset)).isoformat()
        for start in panel_starts
        for offset in range(experiment["hours"])
    }
    if seen_starts != expected_raw_starts:
        raise ValueError("V3 manifest raw-input set differs from the frozen panel")

    _verify_completed_steps(payload, output_root, panel_starts)

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
