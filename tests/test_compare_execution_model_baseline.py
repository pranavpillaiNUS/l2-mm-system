"""Tests for the descriptive V3-versus-frozen-V2 comparison boundary."""

import json
import hashlib
from pathlib import Path
from typing import Mapping

import pytest

from scripts.compare_execution_model_baseline import build_report as _build_report
from src.execution.provenance import execution_provenance_for_replay
from src.execution.simulator import (
    EQUAL_TIMESTAMP_POLICY,
    EXECUTION_MODEL_VERSION,
    SimConfig,
)
from src.replay.depth_parser import SNAPSHOT_TIME_POLICY


def _write_panel(path: Path, *, starts: list[str] | None = None) -> None:
    starts = starts or ["2026-04-12T09:00:00", "2026-04-12T15:00:00"]
    path.write_text(
        "start,hours\n" + "".join(f"{start},5\n" for start in starts),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_report(
    current_paths: Mapping[str, Path],
    legacy_paths: Mapping[str, Path],
    panel: Path,
):
    """Use fixture hashes while production defaults stay frozen and pinned."""
    return _build_report(
        current_paths,
        legacy_paths,
        panel,
        expected_development_panel_sha256=_sha256(panel),
        expected_legacy_endpoint_sha256={
            credit: _sha256(path) for credit, path in legacy_paths.items()
        },
    )


def _write_baseline(
    path: Path,
    *,
    credit: str,
    mean: str,
    low: str,
    high: str,
    current: bool,
    run_dirs: list[str] | None = None,
) -> None:
    params = {
        "symbol": "btcusdt",
        "strategy": "microprice",
        "half_spread": "2.00",
        "requote_interval_ms": 5000,
        "latency_ms": 10,
        "jitter_ms": 0,
        "maker_bps": 2,
        "taker_bps": 5,
        "queue_cancellation_credit": credit,
        "session_hours": 1,
        "iterations": 10_000,
        "seed": 7,
    }
    payload = {
        "params": params,
        "run_dirs": run_dirs or [
            "btcusdt_microprice_20260412_09_5h_5sessions_hs2.00_rq5000",
            "btcusdt_microprice_20260412_15_5h_5sessions_hs2.00_rq5000",
        ],
        "counts": {"windows": 2},
        "ci": {
            "window": {
                "net_pnl": {
                    "n": 2,
                    "mean": mean,
                    "ci_low": low,
                    "ci_high": high,
                }
            }
        },
    }
    if current:
        params.update({
            "hours": 5,
            "order_qty": "0.001",
            "max_position": "0.01",
            "cancel_latency_ms": 10,
            "cancel_jitter_ms": 0,
            "latency_seed": 42,
            "execution_model_version": EXECUTION_MODEL_VERSION,
            "equal_timestamp_policy": EQUAL_TIMESTAMP_POLICY,
            "snapshot_time_policy": SNAPSHOT_TIME_POLICY,
            "trade_gap_policy": "pause_until_snapshot",
        })
        payload["execution_provenance"] = execution_provenance_for_replay(
            SimConfig(
                base_latency_ms=10,
                jitter_ms=0,
                maker_bps=2,
                taker_bps=5,
                queue_cancellation_credit=credit,
            ),
            trade_gap_policy="pause_until_snapshot",
        )
        payload["input_selection"] = {
            "mode": "expected_run_allowlist",
            "expected_run_dirs": list(payload["run_dirs"]),
            "expected_starts": [
                "2026-04-12T09", "2026-04-12T15"
            ],
        }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_report_is_descriptive_and_provenance_checked(tmp_path: Path):
    current = tmp_path / "current.json"
    legacy = tmp_path / "legacy.json"
    panel = tmp_path / "development_windows.csv"
    _write_panel(panel)
    _write_baseline(
        current, credit="1", mean="-2.0", low="-3", high="-1", current=True
    )
    _write_baseline(
        legacy, credit="1.0", mean="-1.25", low="-2", high="0.5", current=False
    )

    report = build_report({"1": current}, {"1": legacy}, panel)

    assert report["automatic_strategy_verdict"] is None
    assert report["comparison_scope"] == "same frozen development-window set"
    endpoint = report["endpoints"]["1"]
    assert endpoint["descriptive_mean_delta_current_minus_legacy"] == "-0.75"
    assert (
        endpoint["current"]["window_net_pnl_ci"]["relation_to_zero"]
        == "entirely_negative"
    )
    assert len(endpoint["current"]["sha256"]) == 64
    assert report["development_panel"]["row_count"] == 2
    assert len(report["development_panel"]["sha256"]) == 64


def test_production_comparison_rejects_unpinned_fixture_inputs(tmp_path: Path):
    current = tmp_path / "current.json"
    legacy = tmp_path / "legacy.json"
    panel = tmp_path / "development_windows.csv"
    _write_panel(panel)
    _write_baseline(
        current, credit="1", mean="0", low="-1", high="1", current=True
    )
    _write_baseline(
        legacy, credit="1", mean="0", low="-1", high="1", current=False
    )

    with pytest.raises(ValueError, match="frozen reference hash"):
        _build_report({"1": current}, {"1": legacy}, panel)


def test_report_rejects_legacy_artifact_as_current(tmp_path: Path):
    current = tmp_path / "not_current.json"
    legacy = tmp_path / "legacy.json"
    panel = tmp_path / "development_windows.csv"
    _write_panel(panel)
    _write_baseline(
        current, credit="0", mean="0", low="-1", high="1", current=False
    )
    _write_baseline(
        legacy, credit="0", mean="0", low="-1", high="1", current=False
    )

    with pytest.raises(ValueError, match="expected event_driven_v2"):
        build_report({"0": current}, {"0": legacy}, panel)


def test_report_rejects_different_development_windows(tmp_path: Path):
    current = tmp_path / "current.json"
    legacy = tmp_path / "legacy.json"
    panel = tmp_path / "development_windows.csv"
    _write_panel(panel)
    _write_baseline(
        current, credit="0", mean="0", low="-1", high="1", current=True
    )
    _write_baseline(
        legacy,
        credit="0",
        mean="0",
        low="-1",
        high="1",
        current=False,
        run_dirs=[
            "btcusdt_microprice_20260412_09_5h_5sessions_hs2.00_rq5000",
            "btcusdt_microprice_20260413_02_5h_5sessions_hs2.00_rq5000",
        ],
    )

    with pytest.raises(ValueError, match="development windows differ"):
        build_report({"0": current}, {"0": legacy}, panel)


def test_report_rejects_non_allowlisted_current_ci(tmp_path: Path):
    current = tmp_path / "current.json"
    legacy = tmp_path / "legacy.json"
    panel = tmp_path / "development_windows.csv"
    _write_panel(panel)
    _write_baseline(
        current, credit="1", mean="0", low="-1", high="1", current=True
    )
    _write_baseline(
        legacy, credit="1", mean="0", low="-1", high="1", current=False
    )
    payload = json.loads(current.read_text(encoding="utf-8"))
    payload["input_selection"]["mode"] = "compatible_parameter_scan"
    current.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="not built from an exact run allowlist"):
        build_report({"1": current}, {"1": legacy}, panel)


def test_report_rejects_params_provenance_contradiction(tmp_path: Path):
    current = tmp_path / "current.json"
    legacy = tmp_path / "legacy.json"
    panel = tmp_path / "development_windows.csv"
    _write_panel(panel)
    _write_baseline(
        current, credit="1", mean="0", low="-1", high="1", current=True
    )
    _write_baseline(
        legacy, credit="1", mean="0", low="-1", high="1", current=False
    )
    payload = json.loads(current.read_text(encoding="utf-8"))
    payload["execution_provenance"]["entry_latency_ms"] = 999
    current.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="contradict execution provenance"):
        build_report({"1": current}, {"1": legacy}, panel)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("hours", 4, "canonical 5-hour windows"),
        ("hours", 5.0, "canonical 5-hour windows"),
        ("order_qty", "0.002", "canonical order_qty=0.001"),
        ("max_position", "0.02", "canonical max_position=0.01"),
    ],
)
def test_report_rejects_noncanonical_current_experiment(
    tmp_path: Path,
    field: str,
    value,
    message: str,
):
    current = tmp_path / "current.json"
    legacy = tmp_path / "legacy.json"
    panel = tmp_path / "development_windows.csv"
    _write_panel(panel)
    _write_baseline(
        current, credit="1", mean="0", low="-1", high="1", current=True
    )
    _write_baseline(
        legacy, credit="1", mean="0", low="-1", high="1", current=False
    )
    payload = json.loads(current.read_text(encoding="utf-8"))
    payload["params"][field] = value
    current.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        build_report({"1": current}, {"1": legacy}, panel)


@pytest.mark.parametrize("field", ["run_dirs", "expected_run_dirs"])
def test_report_rejects_unsafe_current_run_dir_components(
    tmp_path: Path,
    field: str,
):
    current = tmp_path / "current.json"
    legacy = tmp_path / "legacy.json"
    panel = tmp_path / "development_windows.csv"
    _write_panel(panel)
    _write_baseline(
        current, credit="1", mean="0", low="-1", high="1", current=True
    )
    _write_baseline(
        legacy, credit="1", mean="0", low="-1", high="1", current=False
    )
    payload = json.loads(current.read_text(encoding="utf-8"))
    target = (
        payload["run_dirs"]
        if field == "run_dirs"
        else payload["input_selection"]["expected_run_dirs"]
    )
    target[0] = "../btcusdt_microprice_20260412_09_5h_5sessions_hs2.00_rq5000"
    current.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=f"{field} contains an unsafe"):
        build_report({"1": current}, {"1": legacy}, panel)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("trade_gap_policy", "invented", "invalid trade-gap policy"),
        ("post_only", False, "must be post-only"),
        ("entry_latency_ms", 10.0, "invalid latency or seed provenance"),
    ],
)
def test_report_rejects_invalid_current_provenance_domain(
    tmp_path: Path,
    field: str,
    value,
    message: str,
):
    current = tmp_path / "current.json"
    legacy = tmp_path / "legacy.json"
    panel = tmp_path / "development_windows.csv"
    _write_panel(panel)
    _write_baseline(
        current, credit="1", mean="0", low="-1", high="1", current=True
    )
    _write_baseline(
        legacy, credit="1", mean="0", low="-1", high="1", current=False
    )
    payload = json.loads(current.read_text(encoding="utf-8"))
    payload["execution_provenance"][field] = value
    current.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        build_report({"1": current}, {"1": legacy}, panel)


@pytest.mark.parametrize("value", [2.0, "2", True])
def test_report_rejects_coercible_noninteger_ci_sample_size(
    tmp_path: Path,
    value,
):
    current = tmp_path / "current.json"
    legacy = tmp_path / "legacy.json"
    panel = tmp_path / "development_windows.csv"
    _write_panel(panel)
    _write_baseline(
        current, credit="1", mean="0", low="-1", high="1", current=True
    )
    _write_baseline(
        legacy, credit="1", mean="0", low="-1", high="1", current=False
    )
    payload = json.loads(current.read_text(encoding="utf-8"))
    payload["ci"]["window"]["net_pnl"]["n"] = value
    current.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid window net-PnL CI"):
        build_report({"1": current}, {"1": legacy}, panel)


def test_report_rejects_noncanonical_panel_duration(tmp_path: Path):
    current = tmp_path / "current.json"
    legacy = tmp_path / "legacy.json"
    panel = tmp_path / "development_windows.csv"
    _write_panel(panel)
    panel.write_text(
        panel.read_text(encoding="utf-8").replace(",5\n", ",5.0\n"),
        encoding="utf-8",
    )
    _write_baseline(
        current, credit="1", mean="0", low="-1", high="1", current=True
    )
    _write_baseline(
        legacy, credit="1", mean="0", low="-1", high="1", current=False
    )

    with pytest.raises(ValueError, match="canonical five-hour rows"):
        build_report({"1": current}, {"1": legacy}, panel)
