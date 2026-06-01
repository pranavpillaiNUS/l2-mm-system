"""Tests for the OFIGatedMM pre-replay OFI support guard."""

import json
from pathlib import Path

from scripts.run_ofigated_panel import _validate_ofi_support


def write_summary(path: Path, credit: str, verdict: str) -> Path:
    path.write_text(json.dumps({
        "params": {"queue_cancellation_credit": credit},
        "gates": {"overall_verdict": verdict},
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

    verdicts = _validate_ofi_support(paths, ["0.0", "1.0"])

    assert set(verdicts) == {"0.0", "1.0"}


def test_ofigated_runner_blocks_failed_ofi_endpoint(tmp_path: Path):
    paths = [
        write_summary(tmp_path / "zero.json", "0.0", "supported"),
        write_summary(tmp_path / "one.json", "1.0", "blocked"),
    ]

    try:
        _validate_ofi_support(paths, ["0.0", "1.0"])
    except ValueError as exc:
        assert "blocked" in str(exc)
    else:
        raise AssertionError("blocked OFI endpoint should prevent candidate replay")
