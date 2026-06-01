"""Tests for resumable panel-runner status markers."""

from pathlib import Path
from types import SimpleNamespace

from scripts.run_l2_panel import _is_complete, _run_step, _write_status


def test_panel_status_marker_controls_resume_skip(tmp_path: Path):
    status_dir = tmp_path / "status"
    step = "reconcile_2026-04-13T12:00:00"

    assert not _is_complete(status_dir, step)
    _write_status(status_dir, step, "completed", ["python", "script.py"])
    assert _is_complete(status_dir, step)


def test_panel_status_marker_rejects_changed_command_and_missing_nested_artifact(tmp_path: Path):
    status_dir = tmp_path / "status"
    step = "nested_artifact"
    command = ["python", "script.py", "--credit", "1.0"]
    artifact = tmp_path / "nested" / "artifact" / "summary.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")

    _write_status(status_dir, step, "completed", command, [artifact])

    assert _is_complete(status_dir, step, command)
    assert not _is_complete(status_dir, step, [*command, "--changed"])
    artifact.unlink()
    assert not _is_complete(status_dir, step, command)


def test_panel_dry_run_does_not_write_status_marker(tmp_path: Path):
    args = SimpleNamespace(status_dir=tmp_path / "status", dry_run=True)

    _run_step(args, "dry_run", ["python", "script.py"])

    assert not (tmp_path / "status").exists()
