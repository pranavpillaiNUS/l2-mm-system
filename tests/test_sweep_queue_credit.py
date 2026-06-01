"""Tests for Phase C reuse of Phase A queue-credit endpoint artifacts."""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from scripts.sweep_queue_credit import _can_reuse_phase_a, _run_dir_name


def test_phase_c_reuses_existing_phase_a_endpoint_at_canonical_latency(tmp_path: Path):
    args = SimpleNamespace(
        phase_a_latency_ms=10,
        phase_a_reconciliation_root=tmp_path,
        hours=5,
        session_hours=1,
        half_spread="2.00",
        requote_interval_ms=5000,
    )
    start = datetime.fromisoformat("2026-04-13T12:00")
    run_dir = tmp_path / _run_dir_name(args, start, "0.0")
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text("{}", encoding="utf-8")

    assert _can_reuse_phase_a(args, start, "0.0", 10)
    assert not _can_reuse_phase_a(args, start, "0.25", 10)
    assert not _can_reuse_phase_a(args, start, "0.0", 50)
