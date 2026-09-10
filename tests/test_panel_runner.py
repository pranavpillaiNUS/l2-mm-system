"""Tests for resumable panel-runner status markers."""

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import scripts.run_l2_panel as panel_runner
from scripts.compare_execution_model_baseline import FROZEN_DEVELOPMENT_PANEL_SHA256
from scripts.run_l2_panel import (
    _is_complete,
    _run_step,
    _write_artifact_manifest,
    _write_status,
    main,
)


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

    assert _is_complete(
        status_dir, step, command, expected_outputs=[artifact]
    )
    assert not _is_complete(
        status_dir,
        step,
        [*command, "--changed"],
        expected_outputs=[artifact],
    )
    artifact.unlink()
    assert not _is_complete(
        status_dir, step, command, expected_outputs=[artifact]
    )


def test_panel_status_marker_binds_input_and_output_content(tmp_path: Path):
    status_dir = tmp_path / "status"
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    input_path.write_text('{"value": 1}', encoding="utf-8")
    output_path.write_text('{"result": 1}', encoding="utf-8")
    command = ["python", "derive.py"]
    _write_status(
        status_dir,
        "derive",
        "completed",
        command,
        [output_path],
        [input_path],
    )

    assert _is_complete(
        status_dir,
        "derive",
        command,
        [input_path],
        [output_path],
    )
    input_path.write_text('{"value": 2}', encoding="utf-8")
    assert not _is_complete(
        status_dir,
        "derive",
        command,
        [input_path],
        [output_path],
    )
    input_path.write_text('{"value": 1}', encoding="utf-8")
    output_path.write_text('{"result": 2}', encoding="utf-8")
    assert not _is_complete(
        status_dir,
        "derive",
        command,
        [input_path],
        [output_path],
    )


def test_panel_status_cannot_hide_new_required_outputs(tmp_path: Path, capsys):
    args = SimpleNamespace(status_dir=tmp_path / "status", dry_run=True)
    command = ["python", "script.py"]
    _write_status(args.status_dir, "step", "completed", command, [])
    required = tmp_path / "required.json"

    _run_step(args, "step", command, [required])

    assert "[run] step" in capsys.readouterr().out


def test_panel_dry_run_does_not_write_status_marker(tmp_path: Path):
    args = SimpleNamespace(status_dir=tmp_path / "status", dry_run=True)

    _run_step(args, "dry_run", ["python", "script.py"])

    assert not (tmp_path / "status").exists()


def test_v3_dry_run_uses_allowlist_and_descriptive_comparison(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    windows = tmp_path / "development_windows.csv"
    windows.write_text(
        "start,hours\n2026-04-12T09:00:00,5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        panel_runner,
        "DEVELOPMENT_PANEL_SHA256",
        hashlib.sha256(windows.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(panel_runner, "_verify_raw_inputs", lambda args, starts: None)
    monkeypatch.setattr(sys, "argv", [
        "run_l2_panel.py",
        "--windows-csv", str(windows),
        "--output-root", str(tmp_path / "v3"),
        "--status-dir", str(tmp_path / "v3" / "status"),
        "--phase", "a",
        "--dry-run",
    ])

    main()

    output = capsys.readouterr().out
    assert "--expected-run-dirs" in output
    assert "compare_execution_model_baseline.py" in output
    assert "--development-windows-csv" in output
    assert "classify_v2_baseline.py" not in output
    assert "frozen-v1-mean" not in output


def test_v3_runner_rejects_noncanonical_or_holdout_panel(
    tmp_path: Path,
    monkeypatch,
):
    windows = tmp_path / "holdout_windows.csv"
    windows.write_text(
        "start,hours\n2026-06-01T00:00:00,5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", [
        "run_l2_panel.py",
        "--windows-csv", str(windows),
        "--output-root", str(tmp_path / "v3"),
        "--status-dir", str(tmp_path / "v3" / "status"),
        "--phase", "b",
        "--dry-run",
    ])

    try:
        main()
    except ValueError as exc:
        assert "strategy-sealed holdout is not authorized" in str(exc)
    else:
        raise AssertionError("noncanonical development panel should be rejected")


def test_v3_dry_run_accepts_committed_development_panel(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    """Exercise the shipped CSV and pins rather than a patched fixture hash."""
    windows = Path(
        "results/panels/btcusdt_l2_panel_v2/development_windows.csv"
    )
    panel_sha256 = hashlib.sha256(windows.read_bytes()).hexdigest()
    assert panel_sha256 == FROZEN_DEVELOPMENT_PANEL_SHA256
    checked_starts = []
    monkeypatch.setattr(
        panel_runner,
        "_verify_raw_inputs",
        lambda args, starts: checked_starts.extend(starts),
    )
    output_root = tmp_path / "v3"
    monkeypatch.setattr(sys, "argv", [
        "run_l2_panel.py",
        "--output-root", str(output_root),
        "--status-dir", str(output_root / "status"),
        "--phase", "all",
        "--dry-run",
    ])

    main()

    output = capsys.readouterr().out
    assert len(checked_starts) == 24
    assert output.count("[run] reconcile_") == 48
    assert output.count("[run] ofi_signal_") == 2
    assert "[run] execution_model_comparison" in output
    assert not output_root.exists()


def test_committed_artifact_manifest_captures_code_raw_and_outputs(
    tmp_path: Path,
    monkeypatch,
):
    output_root = tmp_path / "v3"
    status_dir = output_root / "status"
    data_root = tmp_path / "data"
    windows = tmp_path / "development_windows.csv"
    integrity = tmp_path / "integrity_manifest.json"
    output = output_root / "result.json"
    output.parent.mkdir(parents=True)
    output.write_text('{"result": 1}', encoding="utf-8")
    (output_root / "derivation_cache.json").write_text(
        '{"ephemeral": true}', encoding="utf-8"
    )
    windows.write_text("start,hours\n2026-04-12T09:00:00,1\n", encoding="utf-8")
    integrity.write_text("{}", encoding="utf-8")
    depth = data_root / "raw/btcusdt/btcusdt_depth_20260412_0900.jsonl.gz"
    trade = (
        data_root
        / "raw/btcusdt_trades/btcusdt_trades_20260412_0900.jsonl.gz"
    )
    depth.parent.mkdir(parents=True)
    trade.parent.mkdir(parents=True)
    depth.write_bytes(b"depth")
    trade.write_bytes(b"trade")
    status_dir.mkdir(parents=True)
    (status_dir / "step.json").write_text(
        '{"status":"completed","source_fingerprint":"source",'
        '"step":"step","command":[],"command_sha256":"command",'
        '"input_fingerprints":{},"expected_outputs":[],'
        '"output_fingerprints":{}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(panel_runner, "_source_fingerprint", lambda: "source")
    args = SimpleNamespace(
        output_root=output_root,
        status_dir=status_dir,
        data_root=data_root,
        symbol="btcusdt",
        windows_csv=windows,
        integrity_manifest=integrity,
        strategy="microprice",
        hours=1,
        session_hours=1,
        half_spread="2.00",
        order_qty="0.001",
        max_position="0.01",
        requote_interval_ms=5000,
        latency_ms=10,
        jitter_ms=0,
        cancel_latency_ms=10,
        cancel_jitter_ms=0,
        maker_bps=2,
        taker_bps=5,
        queue_credits=["0", "1"],
    )

    manifest = _write_artifact_manifest(
        args, [datetime.fromisoformat("2026-04-12T09:00:00")]
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert payload["source_fingerprint"] == "source"
    assert payload["raw_inputs"][0]["depth_sha256"] == hashlib.sha256(
        b"depth"
    ).hexdigest()
    assert payload["artifacts"] == {
        "result.json": hashlib.sha256(b'{"result": 1}').hexdigest()
    }
    assert payload["completed_steps"][0]["step"] == "step"
