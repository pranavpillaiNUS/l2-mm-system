"""Artifact-boundary tests for the queue-credit sweep summarizer."""

import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.summarize_queue_credit_sweep import (
    _build_output_payload,
    _load_runs,
    _load_sweep_inputs,
)
from src.execution.provenance import LEGACY_EXECUTION_MODEL_VERSION
from src.execution.simulator import EQUAL_TIMESTAMP_POLICY, EXECUTION_MODEL_VERSION


def _summary_payload(
    *,
    start: str,
    execution_model_version: str | None = EXECUTION_MODEL_VERSION,
    credit: str = "0.0",
    latency_ms: int = 10,
    cancel_latency_ms: int | None = None,
    latency_seed: int = 42,
    **param_overrides,
) -> dict:
    if cancel_latency_ms is None:
        cancel_latency_ms = latency_ms
    params = {
        "symbol": "btcusdt",
        "strategy": "microprice",
        "start": start,
        "hours": 5,
        "sessions": 5,
        "session_hours": 1,
        "order_qty": "0.001",
        "max_position": "0.01",
        "half_spread": "2.00",
        "requote_interval_ms": 5000,
        "maker_bps": 2,
        "taker_bps": 5,
        "queue_cancellation_credit": credit,
        "trade_gap_policy": "pause_until_snapshot",
        "latency_ms": latency_ms,
        "jitter_ms": 0,
        "cancel_latency_ms": cancel_latency_ms,
        "cancel_jitter_ms": 0,
    }
    params.update(param_overrides)
    summary = {
        "params": params,
        "aggregate": {
            "fills": 1,
            "orders_submitted": 2,
            "matched_qty": "0.001",
            "matched_net_pnl": "-0.01",
            "matched_realized_pnl": "0.01",
            "matched_fees": "0.02",
            "residual_inventory_pnl": "0",
            "net_pnl": "-0.01",
            "fees": "0.02",
        },
    }
    if execution_model_version is not None:
        summary["execution_provenance"] = {
            "execution_model_version": execution_model_version,
            "equal_timestamp_policy": EQUAL_TIMESTAMP_POLICY,
            "trade_gap_policy": "pause_until_snapshot",
            "entry_latency_ms": latency_ms,
            "entry_jitter_ms": 0,
            "cancel_latency_ms": cancel_latency_ms,
            "cancel_jitter_ms": 0,
            "latency_seed": latency_seed,
            "post_only": True,
            "queue_cancellation_credit": credit,
        }
    return summary


def _write_reconciliation(
    root: Path,
    run_name: str,
    *,
    start: str,
    execution_model_version: str | None = EXECUTION_MODEL_VERSION,
    credit: str = "0.0",
    latency_ms: int = 10,
    cancel_latency_ms: int | None = None,
    latency_seed: int = 42,
    **param_overrides,
) -> Path:
    run_dir = root / run_name
    run_dir.mkdir(parents=True)
    summary = _summary_payload(
        start=start,
        execution_model_version=execution_model_version,
        credit=credit,
        latency_ms=latency_ms,
        cancel_latency_ms=cancel_latency_ms,
        latency_seed=latency_seed,
        **param_overrides,
    )
    (run_dir / "summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )
    (run_dir / "matched_lots.csv").write_text("", encoding="utf-8")
    (run_dir / "open_lots.csv").write_text("", encoding="utf-8")
    return run_dir


def _manifest_row(
    root: Path,
    start: str,
    *,
    credit: str = "0.0",
    latency_ms: int = 10,
    cancel_latency_ms: int | None = None,
    latency_seed: int = 42,
) -> dict[str, object]:
    if cancel_latency_ms is None:
        cancel_latency_ms = latency_ms
    return {
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "equal_timestamp_policy": EQUAL_TIMESTAMP_POLICY,
        "trade_gap_policy": "pause_until_snapshot",
        "queue_cancellation_credit": credit,
        "latency_ms": latency_ms,
        "entry_jitter_ms": 0,
        "cancel_latency_ms": cancel_latency_ms,
        "cancel_jitter_ms": 0,
        "latency_seed": latency_seed,
        "post_only": True,
        "start": start,
        "reconciliation_root": str(root),
    }


def _write_manifest(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_load_runs_accepts_exact_event_driven_summaries(tmp_path: Path):
    root = tmp_path / "reconciliation"
    start = "2026-04-13T12:00:00"
    _write_reconciliation(root, "current", start=start)
    manifest = tmp_path / "runs.csv"
    _write_manifest(manifest, [_manifest_row(root, start)])

    runs = _load_runs(manifest)

    assert len(runs) == 1
    assert runs[0].reconciliation.summary["execution_provenance"][
        "execution_model_version"
    ] == EXECUTION_MODEL_VERSION


def test_load_runs_rejects_legacy_execution_summary(tmp_path: Path):
    root = tmp_path / "reconciliation"
    start = "2026-04-13T12:00:00"
    _write_reconciliation(
        root,
        "legacy",
        start=start,
        execution_model_version=LEGACY_EXECUTION_MODEL_VERSION,
    )
    manifest = tmp_path / "runs.csv"
    _write_manifest(manifest, [_manifest_row(root, start)])

    with pytest.raises(ValueError, match="expected exactly one"):
        _load_runs(manifest)


def test_load_runs_rejects_mixed_fixed_experiment_params(tmp_path: Path):
    root = tmp_path / "reconciliation"
    starts = ["2026-04-13T12:00:00", "2026-04-14T12:00:00"]
    _write_reconciliation(root, "first", start=starts[0])
    _write_reconciliation(
        root, "second", start=starts[1], order_qty="0.002"
    )
    manifest = tmp_path / "runs.csv"
    _write_manifest(
        manifest, [_manifest_row(root, start) for start in starts]
    )

    with pytest.raises(ValueError, match="mixes fixed experiment params: order_qty"):
        _load_runs(manifest)


def test_load_runs_rejects_mixed_fixed_execution_provenance(tmp_path: Path):
    root = tmp_path / "reconciliation"
    starts = ["2026-04-13T12:00:00", "2026-04-14T12:00:00"]
    _write_reconciliation(root, "first", start=starts[0])
    _write_reconciliation(
        root, "second", start=starts[1], latency_seed=99
    )
    manifest = tmp_path / "runs.csv"
    _write_manifest(manifest, [
        _manifest_row(root, starts[0]),
        _manifest_row(root, starts[1], latency_seed=99),
    ])

    with pytest.raises(
        ValueError, match="mixes fixed execution provenance: latency_seed"
    ):
        _load_runs(manifest)


def test_load_runs_rejects_summary_params_that_contradict_provenance(
    tmp_path: Path,
):
    root = tmp_path / "reconciliation"
    start = "2026-04-13T12:00:00"
    run_dir = _write_reconciliation(root, "current", start=start)
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["params"]["latency_ms"] = 50
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    manifest = tmp_path / "runs.csv"
    _write_manifest(manifest, [_manifest_row(root, start)])

    with pytest.raises(ValueError, match="expected exactly one"):
        _load_runs(manifest)


def test_load_runs_rejects_duplicate_exact_summaries(tmp_path: Path):
    root = tmp_path / "reconciliation"
    start = "2026-04-13T12:00:00"
    _write_reconciliation(root, "first", start=start)
    _write_reconciliation(root, "duplicate", start=start)
    manifest = tmp_path / "runs.csv"
    _write_manifest(manifest, [_manifest_row(root, start)])

    with pytest.raises(ValueError, match="found 2"):
        _load_runs(manifest)


def test_load_runs_rejects_duplicate_manifest_endpoint(tmp_path: Path):
    root = tmp_path / "reconciliation"
    start = "2026-04-13T12:00:00"
    _write_reconciliation(root, "current", start=start)
    row = _manifest_row(root, start)
    manifest = tmp_path / "runs.csv"
    _write_manifest(manifest, [row, dict(row)])

    with pytest.raises(ValueError, match="duplicate sweep manifest row"):
        _load_runs(manifest)


def test_load_runs_rejects_symlinked_selected_artifact(tmp_path: Path):
    root = tmp_path / "reconciliation"
    start = "2026-04-13T12:00:00"
    run_dir = _write_reconciliation(root, "current", start=start)
    outside = tmp_path / "outside_summary.json"
    outside.write_text((run_dir / "summary.json").read_text(), encoding="utf-8")
    (run_dir / "summary.json").unlink()
    (run_dir / "summary.json").symlink_to(outside)
    manifest = tmp_path / "runs.csv"
    _write_manifest(manifest, [_manifest_row(root, start)])

    with pytest.raises(ValueError, match="cannot be a symlink"):
        _load_runs(manifest)


def test_load_runs_rejects_symlinked_required_lot_file(tmp_path: Path):
    root = tmp_path / "reconciliation"
    start = "2026-04-13T12:00:00"
    run_dir = _write_reconciliation(root, "current", start=start)
    outside = tmp_path / "outside_lots.csv"
    outside.write_text("", encoding="utf-8")
    (run_dir / "matched_lots.csv").unlink()
    (run_dir / "matched_lots.csv").symlink_to(outside)
    manifest = tmp_path / "runs.csv"
    _write_manifest(manifest, [_manifest_row(root, start)])

    with pytest.raises(ValueError, match="cannot be a symlink"):
        _load_runs(manifest)


def test_output_discloses_fixed_and_varied_experiment_fields(tmp_path: Path):
    root = tmp_path / "reconciliation"
    starts = ["2026-04-13T12:00:00", "2026-04-14T12:00:00"]
    _write_reconciliation(root, "lat10", start=starts[0], latency_ms=10)
    _write_reconciliation(
        root,
        "lat50",
        start=starts[1],
        credit="1.0",
        latency_ms=50,
    )
    manifest = tmp_path / "runs.csv"
    _write_manifest(manifest, [
        _manifest_row(root, starts[0], latency_ms=10),
        _manifest_row(root, starts[1], credit="1.0", latency_ms=50),
    ])

    payload = _build_output_payload(_load_sweep_inputs(manifest))

    assert payload["params"]["order_qty"] == Decimal("0.001")
    assert payload["params"]["fixed_fields"] == [
        "symbol",
        "strategy",
        "hours",
        "sessions",
        "session_hours",
        "order_qty",
        "max_position",
        "half_spread",
        "requote_interval_ms",
        "maker_bps",
        "taker_bps",
        "trade_gap_policy",
    ]
    assert payload["params"]["varied_values"]["start"] == starts
    assert payload["execution_provenance"]["varied_values"][
        "entry_latency_ms"
    ] == [10, 50]
    assert payload["execution_provenance"]["varied_values"][
        "queue_cancellation_credit"
    ] == ["0", "1"]
    assert {row["cancel_latency_ms"] for row in payload["rows"]} == {10, 50}
    assert all(
        row["trade_gap_policy"] == "pause_until_snapshot"
        and row["post_only"] is True
        and row["latency_seed"] == 42
        for row in payload["rows"]
    )
