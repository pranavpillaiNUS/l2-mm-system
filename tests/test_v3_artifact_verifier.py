"""A matching tree hash must not certify an incomplete research workflow."""

import copy
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from scripts.run_l2_panel import _command_sha256, _file_sha256
from scripts.verify_v3_artifacts import _verify_completed_steps
import scripts.run_l2_panel as panel_runner
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
    # still-required panel check; it does not waive the rest of verification.
    with pytest.raises(ValueError, match="frozen development panel"):
        verify(output_root, output_root / "status", source_revision="recorded")
