"""A matching tree hash must not certify an incomplete research workflow."""

import copy
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scripts.run_l2_panel import _command_sha256, _file_sha256
from scripts.verify_v3_artifacts import _verify_completed_steps
import scripts.run_l2_panel as panel_runner
import scripts.verify_v3_artifacts as artifact_verifier
from scripts.verify_v3_artifacts import (
    _revision_source_fingerprint, _verify_source, verify,
)


@pytest.fixture
def completed_workflow(tmp_path):
    starts = [datetime(2026, 4, 12, 9)]
    names = ["microprice_signal", "execution_model_comparison", "fee_break_even"]
    for credit in ("0", "1"):
        names.append(f"reconcile_qc{credit}_2026-04-12T09:00:00")
        names.extend(f"{name}_qc{credit}" for name in (
            "bootstrap_ci", "microprice_fill_toxicity", "same_ms_audit",
            "tail_diagnostics", "ofi_signal",
        ))
    payload = {"completed_steps": [], "artifacts": {}}
    for index, name in enumerate(names):
        path = tmp_path / f"result_{index}.json"
        path.write_text('{"result": true}')
        digest = _file_sha256(path)
        command = ["python", "research.py", "--step", name]
        payload["completed_steps"].append({
            "step": name,
            "command": command,
            "command_sha256": _command_sha256(command),
            "expected_outputs": [str(path)],
            "output_fingerprints": {str(path): f"file:{digest}"},
        })
        payload["artifacts"][path.name] = digest
    return payload, tmp_path, starts


def test_accepts_complete_derivations(completed_workflow):
    _verify_completed_steps(*completed_workflow)


@pytest.mark.parametrize("mutation", ["empty", "partial", "duplicate", "command", "outputs", "hash"])
def test_rejects_incomplete_or_inconsistent_derivations(completed_workflow, mutation):
    payload, root, starts = completed_workflow
    payload = copy.deepcopy(payload)
    steps = payload["completed_steps"]
    if mutation == "empty":
        payload["completed_steps"] = []
        payload["artifacts"] = {}
    elif mutation == "partial":
        steps.pop()
    elif mutation == "duplicate":
        steps.append(steps[0])
    elif mutation == "command":
        steps[0]["command"].append("--changed")
    elif mutation == "outputs":
        steps[0]["expected_outputs"] = []
        steps[0]["output_fingerprints"] = {}
    else:
        steps[0]["output_fingerprints"][steps[0]["expected_outputs"][0]] = "file:wrong"
    with pytest.raises(ValueError, match="V3"):
        _verify_completed_steps(payload, root, starts)


def test_rejects_redirected_output_parent(completed_workflow, tmp_path):
    payload, root, starts = completed_workflow
    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    output = root / "link" / "result.json"
    output.write_text('{}')
    digest = _file_sha256(output)
    step = payload["completed_steps"][0]
    step["expected_outputs"] = [str(output)]
    step["output_fingerprints"] = {str(output): f"file:{digest}"}
    payload["artifacts"]["link/result.json"] = digest
    with pytest.raises(ValueError, match="redirected parent"):
        _verify_completed_steps(payload, root, starts)


@pytest.fixture
def source_repository(tmp_path, monkeypatch):
    def git(*arguments):
        return subprocess.run(
            ["git", *arguments], cwd=tmp_path, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    git("init", "--quiet")
    git("config", "user.email", "parity-test@example.invalid")
    git("config", "user.name", "Provenance Test")
    files = {
        "src/a/nested.py": b"VALUE = 1\n",
        "src/a-file.py": b"VALUE = 2\r\n",
        "src/a.py": b"VALUE = 3\n",
        "scripts/runner.py": b"VALUE = 4\n",
        "scripts/excluded.txt": b"not Python source\n",
        "excluded.py": b"outside fingerprint scope\n",
    }
    for filename, contents in files.items():
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    git("add", ".")
    git("-c", "core.autocrlf=false", "commit", "--quiet", "-m", "frozen source")
    commit = git("rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(panel_runner, "_SOURCE_FINGERPRINT", None)
    monkeypatch.setattr(panel_runner, "_FILE_HASH_CACHE", {})
    fingerprint = panel_runner._source_fingerprint()
    payload = {"git_head": commit, "source_fingerprint": fingerprint}
    return tmp_path, git, payload


def test_committed_fingerprint_reproduces_exact_worktree_bytes_and_path_order(source_repository):
    _, _, payload = source_repository
    assert _revision_source_fingerprint(payload["git_head"]) == (
        payload["git_head"], payload["source_fingerprint"],
    )
    assert _verify_source(payload)["mode"] == "current_tree"


def test_recorded_revision_survives_source_drift_while_default_rejects_it(
    source_repository, monkeypatch,
):
    root, _, payload = source_repository
    (root / "scripts/runner.py").write_text("VALUE = 999\n")
    (root / "scripts/untracked.py").write_text("NEW = True\n")
    monkeypatch.setattr(panel_runner, "_SOURCE_FINGERPRINT", None)
    with pytest.raises(ValueError, match="current Python source differs"):
        _verify_source(payload)
    for revision in ("recorded", payload["git_head"][:12]):
        assert _verify_source(payload, revision) == {
            "mode": "git_revision", "revision": payload["git_head"],
            "source_fingerprint": payload["source_fingerprint"],
        }


def test_same_source_at_a_different_commit_does_not_match_recorded_identity(source_repository):
    root, git, payload = source_repository
    (root / "notes.md").write_text("a later documentation-only commit\n")
    git("add", "notes.md")
    git("commit", "--quiet", "-m", "later documentation")
    later, fingerprint = _revision_source_fingerprint("HEAD")
    assert later != payload["git_head"]
    assert fingerprint == payload["source_fingerprint"]
    with pytest.raises(ValueError, match="differs from the V3 recorded Git commit"):
        _verify_source(payload, "HEAD")


def test_recorded_commit_must_reproduce_manifest_source_hash(source_repository):
    _, _, payload = source_repository
    with pytest.raises(ValueError, match="recorded Git Python source differs"):
        _verify_source({**payload, "source_fingerprint": "0" * 64}, "recorded")


@pytest.mark.parametrize("revision", ["", " ", "absent-ref", "--help", "bad\0ref"])
def test_invalid_or_missing_source_revisions_fail_cleanly(source_repository, revision):
    _, _, payload = source_repository
    with pytest.raises(ValueError, match="V3 source revision"):
        _verify_source(payload, revision)


def test_recorded_mode_requires_a_full_recorded_commit_identity(source_repository):
    _, _, payload = source_repository
    with pytest.raises(ValueError, match="no recorded Git source revision"):
        _verify_source({"source_fingerprint": payload["source_fingerprint"]}, "recorded")
    with pytest.raises(ValueError, match="differs from the V3 recorded Git commit"):
        _verify_source({**payload, "git_head": "HEAD"}, "recorded")


def test_public_verifier_keeps_current_source_as_its_default(source_repository, monkeypatch):
    root, _, payload = source_repository
    (root / "scripts/runner.py").write_text("VALUE = 999\n")
    monkeypatch.setattr(panel_runner, "_SOURCE_FINGERPRINT", None)
    manifest = {
        **payload,
        "manifest_version": panel_runner.ARTIFACT_MANIFEST_VERSION,
        "execution_model_version": "event_driven_v2",
        "equal_timestamp_policy": "market_data_before_private_actions_v1",
        "snapshot_time_policy": "post_response_proxy_depth_boundary_v1",
        "trade_gap_policy": "pause_until_snapshot",
    }
    output_root = root / "results/current"
    output_root.mkdir(parents=True)
    (output_root / "ARTIFACT_MANIFEST.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="current Python source differs"):
        verify(output_root, output_root / "status")
    # Explicit historical verification proceeds past source validation to the
    # still-required panel check. It does not waive the rest of verification.
    with pytest.raises(ValueError, match="frozen development panel"):
        verify(output_root, output_root / "status", source_revision="recorded")


@pytest.fixture
def complete_artifact_tree(source_repository, monkeypatch):
    """A small 24-window publication with all 61 derivations and 240 inputs."""
    root, _, source = source_repository
    output = root / "results/current"
    output.mkdir(parents=True)
    starts = [datetime(2026, 4, 12, 9) + timedelta(hours=5 * i) for i in range(24)]
    panel = root / "development_windows.csv"
    panel.write_text("start\n" + "\n".join(start.isoformat() for start in starts) + "\n")
    panel_hash = _file_sha256(panel)
    monkeypatch.setattr(artifact_verifier, "DEVELOPMENT_PANEL_SHA256", panel_hash)
    raw_dir = root / "raw"
    raw_dir.mkdir()
    raw_inputs = []
    for start in starts:
        for offset in range(5):
            hour = start + timedelta(hours=offset)
            row = {"start": hour.isoformat()}
            for prefix in ("depth", "trade"):
                path = raw_dir / f"{prefix}_{hour:%Y%m%d_%H}.json"
                path.write_text(json.dumps({"kind": prefix, "hour": hour.isoformat()}))
                row[f"{prefix}_path"] = str(path)
                row[f"{prefix}_sha256"] = _file_sha256(path)
            raw_inputs.append(row)
    inventory = root / "integrity.json"
    inventory.write_text(json.dumps({"hours": [{**row, "valid": True} for row in raw_inputs]}))
    inventory_hash = _file_sha256(inventory)
    monkeypatch.setattr(artifact_verifier, "INTEGRITY_MANIFEST_FILE_SHA256", inventory_hash)
    payload = {
        **source,
        "manifest_version": panel_runner.ARTIFACT_MANIFEST_VERSION,
        "execution_model_version": "event_driven_v2",
        "equal_timestamp_policy": "market_data_before_private_actions_v1",
        "snapshot_time_policy": "post_response_proxy_depth_boundary_v1",
        "trade_gap_policy": "pause_until_snapshot",
        "development_panel": {"path": str(panel), "sha256": panel_hash,
                              "starts": [start.isoformat() for start in starts]},
        "raw_integrity_manifest": {
            "path": str(inventory), "file_sha256": inventory_hash,
            "identity_sha256": artifact_verifier.INTEGRITY_MANIFEST_SHA256,
        },
        "experiment": {
            "symbol": "btcusdt", "strategy": "microprice", "hours": 5,
            "session_hours": 1, "half_spread": "2.00", "order_qty": "0.001",
            "max_position": "0.01", "requote_interval_ms": 5000,
            "latency_ms": 10, "jitter_ms": 0, "cancel_latency_ms": 10,
            "cancel_jitter_ms": 0, "maker_bps": 2, "taker_bps": 5,
            "queue_credits": ["0", "1"],
        },
        "raw_inputs": raw_inputs, "completed_steps": [], "artifacts": {},
    }
    names = ["microprice_signal", "execution_model_comparison", "fee_break_even"]
    for credit in ("0", "1"):
        names.extend(f"reconcile_qc{credit}_{start.isoformat()}" for start in starts)
        names.extend(f"{name}_qc{credit}" for name in (
            "bootstrap_ci", "microprice_fill_toxicity", "same_ms_audit",
            "tail_diagnostics", "ofi_signal",
        ))
    for index, name in enumerate(names):
        path = output / f"result_{index}.json"
        path.write_text(json.dumps({"step": name}))
        digest = _file_sha256(path)
        command = ["python", "research.py", "--step", name]
        payload["completed_steps"].append({
            "step": name, "command": command,
            "command_sha256": _command_sha256(command),
            "expected_outputs": [str(path)],
            "output_fingerprints": {str(path): f"file:{digest}"},
        })
        payload["artifacts"][path.name] = digest
    (output / "ARTIFACT_MANIFEST.json").write_text(json.dumps(payload))
    return payload, output, raw_dir


def test_artifacts_only_verifies_complete_tree_without_reading_raw_captures(
    complete_artifact_tree, monkeypatch,
):
    payload, output, raw_dir = complete_artifact_tree
    manifest_before = (output / "ARTIFACT_MANIFEST.json").read_bytes()
    for path in raw_dir.iterdir():
        path.unlink()
    with pytest.raises(ValueError, match="V3 raw input differs from manifest"):
        verify(output, output / "status", source_revision="recorded")
    original_hash = artifact_verifier._file_sha256

    def no_raw_hash(path):
        assert raw_dir not in path.parents, "artifact-only mode read a raw capture"
        return original_hash(path)

    monkeypatch.setattr(artifact_verifier, "_file_sha256", no_raw_hash)
    checked = verify(output, output / "status", source_revision="recorded", artifacts_only=True)
    assert len(checked["completed_steps"]) == 61
    assert checked["raw_verification"] == {
        "mode": "inventory_identities_only", "selected_files": 240, "files_hashed": 0,
    }
    assert checked["source_verification"]["revision"] == payload["git_head"]
    assert (output / "ARTIFACT_MANIFEST.json").read_bytes() == manifest_before


def test_full_verification_reports_actual_raw_hash_checks(complete_artifact_tree):
    _, output, _ = complete_artifact_tree
    checked = verify(output, output / "status", source_revision="recorded")
    assert checked["raw_verification"] == {
        "mode": "raw_file_hashes", "selected_files": 240, "files_hashed": 240,
    }


@pytest.mark.parametrize("mutation, message", [
    ("raw_hash", "raw-input hash differs"),
    ("raw_path", "raw-input path differs"),
    ("missing_hour", "raw-input set differs"),
    ("duplicate_hour", "duplicate raw-input rows"),
    ("inventory_identity", "wrong raw-integrity identity"),
    ("inventory_bytes", "raw-integrity manifest file"),
    ("partial_workflow", "complete development workflow"),
    ("output_bytes", "derivation hash does not match"),
    ("extra_output", "artifact tree differs"),
])
def test_artifacts_only_still_rejects_invalid_publication(
    complete_artifact_tree, mutation, message,
):
    payload, output, _ = complete_artifact_tree
    if mutation == "raw_hash":
        payload["raw_inputs"][0]["depth_sha256"] = "0" * 64
    elif mutation == "raw_path":
        payload["raw_inputs"][0]["depth_path"] = "different_capture.json"
    elif mutation == "missing_hour":
        payload["raw_inputs"].pop()
    elif mutation == "duplicate_hour":
        payload["raw_inputs"].append(payload["raw_inputs"][0])
    elif mutation == "inventory_identity":
        payload["raw_integrity_manifest"]["identity_sha256"] = "0" * 64
    elif mutation == "inventory_bytes":
        Path(payload["raw_integrity_manifest"]["path"]).write_text("{}")
    elif mutation == "partial_workflow":
        payload["completed_steps"].pop()
    elif mutation == "output_bytes":
        (output / "result_0.json").write_text("{}")
    else:
        (output / "unlisted.json").write_text("{}")
    (output / "ARTIFACT_MANIFEST.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=message):
        verify(output, output / "status", source_revision="recorded", artifacts_only=True)


def test_artifacts_only_does_not_waive_default_source_verification(
    complete_artifact_tree, monkeypatch,
):
    _, output, _ = complete_artifact_tree
    Path("scripts/runner.py").write_text("CHANGED = True\n")
    monkeypatch.setattr(panel_runner, "_SOURCE_FINGERPRINT", None)
    with pytest.raises(ValueError, match="current Python source differs"):
        verify(output, output / "status", artifacts_only=True)
