"""Tests for resumable panel-runner status markers."""

from pathlib import Path

from scripts.run_l2_panel import _is_complete, _write_status


def test_panel_status_marker_controls_resume_skip(tmp_path: Path):
    status_dir = tmp_path / "status"
    step = "reconcile_2026-04-13T12:00:00"

    assert not _is_complete(status_dir, step)
    _write_status(status_dir, step, "completed", ["python", "script.py"])
    assert _is_complete(status_dir, step)
