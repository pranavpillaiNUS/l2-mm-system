"""Tests for descriptive regime-table artifact resolution."""

import csv
from pathlib import Path

from scripts.analyze_mm_regimes import _load_signal_metric


def write_regression(path: Path, *, beta: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["window", "horizon", "signal", "beta"])
        writer.writeheader()
        writer.writerow({
            "window": "2026-04-13 12:00",
            "horizon": "1s",
            "signal": "normalized_ofi",
            "beta": beta,
        })


def test_regime_loader_resolves_nested_endpoint_artifact_paths(tmp_path: Path):
    write_regression(tmp_path / "ofi_panel_qc0" / "regressions.csv", beta="0")
    write_regression(tmp_path / "ofi_panel" / "regressions.csv", beta="1")

    endpoint_zero = _load_signal_metric(tmp_path, signal="ofi", artifact_suffix="_qc0")
    endpoint_one = _load_signal_metric(tmp_path, signal="ofi")

    assert endpoint_zero["2026-04-13 12:00"]["beta"] == "0"
    assert endpoint_one["2026-04-13 12:00"]["beta"] == "1"
