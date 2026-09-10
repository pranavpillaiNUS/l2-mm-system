"""A matching tree hash must not certify an incomplete research workflow."""

import copy
from datetime import datetime
from pathlib import Path

import pytest

from scripts.run_l2_panel import _command_sha256, _file_sha256
from scripts.verify_v3_artifacts import _verify_completed_steps


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
