"""Tests for Phase C reuse of Phase A queue-credit endpoint artifacts."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from scripts.run_l2_panel import _reconciliation_output_paths
from scripts.sweep_queue_credit import _can_reuse_phase_a, _run_dir_name
from src.execution.simulator import EQUAL_TIMESTAMP_POLICY, EXECUTION_MODEL_VERSION
from src.replay.depth_parser import SNAPSHOT_TIME_POLICY


def _args(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        phase_a_latency_ms=10,
        phase_a_jitter_ms=0,
        phase_a_cancel_latency_ms=10,
        phase_a_cancel_jitter_ms=0,
        phase_a_latency_seed=42,
        phase_a_reconciliation_root=root,
        symbol="btcusdt",
        hours=5,
        session_hours=1,
        half_spread="2.00",
        order_qty="0.001",
        max_position="0.01",
        requote_interval_ms=5000,
        maker_bps=2,
        taker_bps=5,
    )


def _write_summary(
    args: SimpleNamespace,
    start: datetime,
    credit: str,
    payload: dict,
) -> None:
    run_dir = args.phase_a_reconciliation_root / _run_dir_name(
        args, start, credit
    )
    run_dir.mkdir(parents=True)
    summary_path = run_dir / "summary.json"
    summary_path.write_text(
        json.dumps(payload), encoding="utf-8"
    )
    for path in _reconciliation_output_paths(summary_path)[1:]:
        path.write_text("", encoding="utf-8")


def test_phase_c_reuses_existing_phase_a_endpoint_at_canonical_latency(tmp_path: Path):
    args = _args(tmp_path)
    start = datetime.fromisoformat("2026-04-13T12:00")
    _write_summary(
        args,
        start,
        "0.0",
        {
            "params": {
                "symbol": "btcusdt",
                "strategy": "microprice",
                "start": start.isoformat(),
                "hours": 5,
                "sessions": 5,
                "session_hours": 1,
                "half_spread": "2.00",
                "order_qty": "0.001",
                "max_position": "0.01",
                "requote_interval_ms": 5000,
                "maker_bps": 2,
                "taker_bps": 5,
                "trade_gap_policy": "pause_until_snapshot",
            },
            "execution_provenance": {
                "execution_model_version": EXECUTION_MODEL_VERSION,
                "equal_timestamp_policy": EQUAL_TIMESTAMP_POLICY,
                "snapshot_time_policy": SNAPSHOT_TIME_POLICY,
                "trade_gap_policy": "pause_until_snapshot",
                "entry_latency_ms": 10,
                "entry_jitter_ms": 0,
                "cancel_latency_ms": 10,
                "cancel_jitter_ms": 0,
                "latency_seed": 42,
                "post_only": True,
                "queue_cancellation_credit": "0.0",
            }
        },
    )

    assert _can_reuse_phase_a(args, start, "0.0", 10)
    assert not _can_reuse_phase_a(args, start, "0.25", 10)
    assert not _can_reuse_phase_a(args, start, "0.0", 50)


def test_phase_c_does_not_reuse_summary_without_execution_metadata(
    tmp_path: Path,
):
    args = _args(tmp_path)
    start = datetime.fromisoformat("2026-04-13T12:00")
    _write_summary(args, start, "0.0", {"params": {"latency_ms": 10}})

    assert not _can_reuse_phase_a(args, start, "0.0", 10)
