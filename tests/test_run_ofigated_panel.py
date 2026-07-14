"""Tests for the OFIGatedMM pre-replay OFI support guard."""

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

import scripts.run_ofigated_panel as ofi_runner
from scripts.run_ofigated_panel import _validate_ofi_support, main
from src.analysis.ofi_signal import (
    DEFAULT_OFI_FILL_HORIZONS_MS,
    DEFAULT_OFI_HORIZONS_MS,
)
from src.execution.provenance import execution_provenance_for_replay
from src.execution.simulator import SimConfig


STARTS = [
    datetime.fromisoformat("2026-04-12T09:00:00"),
    datetime.fromisoformat("2026-04-12T15:00:00"),
]


def write_summary(
    path: Path,
    credit: str,
    verdict: str,
    *,
    starts: list[datetime] | None = None,
) -> Path:
    starts = STARTS if starts is None else starts
    supported = verdict != "blocked"
    conditional_pass = verdict == "supported"
    conditional_status = (
        "pass" if conditional_pass else "inconclusive_power"
    )
    path.write_text(json.dumps({
        "execution_provenance": execution_provenance_for_replay(
            SimConfig(
                base_latency_ms=10,
                jitter_ms=0,
                maker_bps=2,
                taker_bps=5,
                queue_cancellation_credit=credit,
            ),
            trade_gap_policy="pause_until_snapshot",
        ),
        "params": {
            "symbol": "btcusdt",
            "strategy": "microprice",
            "starts": [start.strftime("%Y-%m-%dT%H") for start in starts],
            "hours": 5,
            "session_hours": 1,
            "half_spread": "2.00",
            "order_qty": "0.001",
            "max_position": "0.01",
            "requote_interval_ms": 5000,
            "latency_ms": 10,
            "jitter_ms": 0,
            "maker_bps": 2,
            "taker_bps": 5,
            "queue_cancellation_credit": credit,
            "sample_interval_ms": 1000,
            "ofi_interval_ms": 1000,
            "horizons_ms": DEFAULT_OFI_HORIZONS_MS,
            "fill_horizons_ms": DEFAULT_OFI_FILL_HORIZONS_MS,
        },
        "counts": {"windows": len(starts)},
        "gates": {
            "stable_sign_windows": len(starts) if supported else 0,
            "total_windows": len(starts),
            "stable_sign_share": "1" if supported else "0",
            "stable_sign_pass": supported,
            "pooled_abs_t_stat": 3.0,
            "pooled_t_stat_pass": True,
            "pooled_abs_predicted_drift_1std_bps": 0.1,
            "pooled_effect_pass": True,
            "conditional_separation_bps": "2" if conditional_pass else None,
            "conditional_bucket_count_min": 50 if conditional_pass else 0,
            "conditional_status": conditional_status,
            "conditional_pass": conditional_pass,
            "unconditional_pass": supported,
            "overall_verdict": verdict,
            "all_pass": supported,
            "marginal_5s_fallback": False,
        },
    }), encoding="utf-8")
    return path


def test_ofigated_runner_accepts_supported_and_power_limited_endpoints(tmp_path: Path):
    paths = [
        write_summary(tmp_path / "zero.json", "0.0", "supported"),
        write_summary(
            tmp_path / "one.json",
            "1.0",
            "supported_with_conditional_power_limit",
        ),
    ]

    verdicts = _validate_ofi_support(paths, ["0.0", "1.0"], STARTS)

    assert set(verdicts) == {"0", "1"}


def test_ofigated_runner_blocks_failed_ofi_endpoint(tmp_path: Path):
    paths = [
        write_summary(tmp_path / "zero.json", "0.0", "supported"),
        write_summary(tmp_path / "one.json", "1.0", "blocked"),
    ]

    try:
        _validate_ofi_support(paths, ["0.0", "1.0"], STARTS)
    except ValueError as exc:
        assert "blocked" in str(exc)
    else:
        raise AssertionError("blocked OFI endpoint should prevent candidate replay")


def test_ofigated_runner_rejects_foreign_window_set(tmp_path: Path):
    foreign = [datetime.fromisoformat("2026-06-01T00:00:00")]
    paths = [
        write_summary(tmp_path / "zero.json", "0.0", "supported", starts=foreign),
        write_summary(tmp_path / "one.json", "1.0", "supported", starts=foreign),
    ]

    with pytest.raises(ValueError, match="run set does not match"):
        _validate_ofi_support(paths, ["0.0", "1.0"], STARTS)


def test_ofigated_runner_rejects_noncanonical_ofi_experiment(tmp_path: Path):
    paths = [
        write_summary(tmp_path / "zero.json", "0.0", "supported"),
        write_summary(tmp_path / "one.json", "1.0", "supported"),
    ]
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    payload["params"]["order_qty"] = "0.002"
    paths[0].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="canonical development experiment"):
        _validate_ofi_support(paths, ["0.0", "1.0"], STARTS)


def test_ofigated_runner_rejects_noncanonical_execution_provenance(
    tmp_path: Path,
):
    paths = [
        write_summary(tmp_path / "zero.json", "0.0", "supported"),
        write_summary(tmp_path / "one.json", "1.0", "supported"),
    ]
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    payload["execution_provenance"]["cancel_latency_ms"] = 50
    paths[0].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="provenance is not the canonical"):
        _validate_ofi_support(paths, ["0.0", "1.0"], STARTS)


def test_ofigated_runner_recomputes_persisted_gate_verdict(tmp_path: Path):
    paths = [
        write_summary(tmp_path / "zero.json", "0.0", "supported"),
        write_summary(tmp_path / "one.json", "1.0", "supported"),
    ]
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    payload["gates"]["pooled_abs_t_stat"] = 0.5
    paths[0].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="inconsistent gate result"):
        _validate_ofi_support(paths, ["0.0", "1.0"], STARTS)


def test_ofigated_runner_requires_both_canonical_queue_endpoints(tmp_path: Path):
    path = write_summary(tmp_path / "zero.json", "0.0", "supported")

    with pytest.raises(ValueError, match="exactly queue credits 0 and 1"):
        _validate_ofi_support([path], ["0.0"], STARTS)


def test_ofigated_main_verifies_raw_inputs_before_replay(
    tmp_path: Path,
    monkeypatch,
):
    starts = [datetime.fromisoformat("2026-04-12T09:00:00")]
    windows = tmp_path / "development_windows.csv"
    windows.write_text(
        "start,hours\n2026-04-12T09:00:00,5\n",
        encoding="utf-8",
    )
    zero = write_summary(tmp_path / "zero.json", "0.0", "supported", starts=starts)
    one = write_summary(tmp_path / "one.json", "1.0", "supported", starts=starts)
    monkeypatch.setattr(
        ofi_runner,
        "DEVELOPMENT_PANEL_SHA256",
        hashlib.sha256(windows.read_bytes()).hexdigest(),
    )
    verified = []

    def fake_verify(args, selected_starts):
        verified.append((args.data_root, args.integrity_manifest, selected_starts))

    monkeypatch.setattr(ofi_runner, "_verify_raw_inputs", fake_verify)
    monkeypatch.setattr(ofi_runner, "_run_step", lambda *args, **kwargs: None)
    manifest = tmp_path / "integrity_manifest.json"
    monkeypatch.setattr(sys, "argv", [
        "run_ofigated_panel.py",
        "--ofi-summary", str(zero), str(one),
        "--windows-csv", str(windows),
        "--integrity-manifest", str(manifest),
        "--data-root", str(tmp_path / "data"),
        "--output-root", str(tmp_path / "v3"),
        "--status-dir", str(tmp_path / "v3" / "status"),
        "--dry-run",
    ])

    main()

    assert verified == [(tmp_path / "data", manifest, starts)]
