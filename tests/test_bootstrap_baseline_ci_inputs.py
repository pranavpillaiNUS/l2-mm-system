"""Tests for exact V3 baseline-bootstrap input selection."""

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.bootstrap_baseline_ci import _load_runs
from src.execution.provenance import execution_provenance_for_replay
from src.execution.simulator import EXECUTION_MODEL_VERSION, SimConfig


def _args(root: Path, expected: list[str]):
    return SimpleNamespace(
        results_root=root,
        expected_run_dirs=expected,
        expected_starts=["2026-04-12T09"] * len(expected),
        execution_model_version=EXECUTION_MODEL_VERSION,
        latency_ms=10,
        jitter_ms=0,
        cancel_latency_ms=10,
        cancel_jitter_ms=0,
        latency_seed=42,
        queue_cancellation_credit=Decimal("1"),
        symbol="btcusdt",
        strategy="microprice",
        hours=5,
        order_qty="0.001",
        max_position="0.01",
        half_spread="2.00",
        requote_interval_ms=5000,
        session_hours=1,
        trade_gap_policy="pause_until_snapshot",
        maker_bps=2,
        taker_bps=5,
    )


def _write_summary(
    root: Path,
    name: str,
    *,
    provenance_overrides: dict | None = None,
    **param_overrides,
) -> None:
    run_dir = root / name
    run_dir.mkdir(parents=True)
    payload = {
        "execution_provenance": execution_provenance_for_replay(
            SimConfig(
                base_latency_ms=10,
                jitter_ms=0,
                cancel_latency_ms=10,
                cancel_jitter_ms=0,
                maker_bps=2,
                taker_bps=5,
                queue_cancellation_credit="1",
            ),
            trade_gap_policy="pause_until_snapshot",
        ),
        "params": {
            "symbol": "btcusdt",
            "strategy": "microprice",
            "start": "2026-04-12T09:00:00",
            "hours": 5,
            "sessions": 5,
            "session_hours": 1,
            "half_spread": "2.00",
            "order_qty": "0.001",
            "max_position": "0.01",
            "requote_interval_ms": 5000,
            "latency_ms": 10,
            "jitter_ms": 0,
            "cancel_latency_ms": 10,
            "cancel_jitter_ms": 0,
            "maker_bps": 2,
            "taker_bps": 5,
            "queue_cancellation_credit": "1",
            "trade_gap_policy": "pause_until_snapshot",
        },
    }
    if provenance_overrides:
        payload["execution_provenance"].update(provenance_overrides)
    payload["params"].update(param_overrides)
    (run_dir / "summary.json").write_text(json.dumps(payload), encoding="utf-8")
    (run_dir / "matched_lots.csv").write_text(
        "session,quantity,net_pnl,realized_pnl,total_fees,hold_time_ms\n",
        encoding="utf-8",
    )


def test_expected_run_allowlist_excludes_compatible_stale_runs(tmp_path: Path):
    _write_summary(tmp_path, "expected")
    _write_summary(tmp_path, "stale_but_compatible")

    runs = _load_runs(_args(tmp_path, ["expected"]))

    assert [run["run_dir"].name for run in runs] == ["expected"]


def test_expected_run_allowlist_fails_closed_on_missing_run(tmp_path: Path):
    with pytest.raises(ValueError, match="reconciliation artifact is missing"):
        _load_runs(_args(tmp_path, ["missing"]))


def test_expected_run_allowlist_requires_matched_lots(tmp_path: Path):
    _write_summary(tmp_path, "expected")
    (tmp_path / "expected" / "matched_lots.csv").unlink()

    with pytest.raises(ValueError, match="matched_lots.csv"):
        _load_runs(_args(tmp_path, ["expected"]))


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("start", "2099-01-01T00:00:00"),
        ("hours", 99),
        ("order_qty", "999"),
        ("max_position", "999"),
        ("trade_gap_policy", "ignore"),
    ],
)
def test_expected_run_allowlist_rejects_wrong_experiment_params(
    tmp_path: Path,
    field: str,
    wrong_value,
):
    _write_summary(tmp_path, "expected", **{field: wrong_value})

    with pytest.raises(ValueError, match="incompatible provenance or parameters"):
        _load_runs(_args(tmp_path, ["expected"]))


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("hours", 5.9),
        ("sessions", "5"),
        ("session_hours", True),
        ("requote_interval_ms", 5000.0),
        ("latency_ms", "10"),
        ("jitter_ms", False),
        ("cancel_latency_ms", 10.0),
        ("cancel_jitter_ms", "0"),
        ("maker_bps", 2.0),
        ("taker_bps", "5"),
    ],
)
def test_expected_run_allowlist_rejects_coercible_non_integer_params(
    tmp_path: Path,
    field: str,
    wrong_value,
):
    _write_summary(tmp_path, "expected", **{field: wrong_value})

    with pytest.raises(ValueError, match="incompatible provenance or parameters"):
        _load_runs(_args(tmp_path, ["expected"]))


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("post_only", False),
        ("entry_latency_ms", 10.0),
        ("trade_gap_policy", "unknown"),
    ],
)
def test_expected_run_allowlist_requires_validated_execution_provenance(
    tmp_path: Path,
    field: str,
    wrong_value,
):
    _write_summary(
        tmp_path,
        "expected",
        provenance_overrides={field: wrong_value},
    )

    with pytest.raises(ValueError, match="incompatible provenance or parameters"):
        _load_runs(_args(tmp_path, ["expected"]))


def test_expected_run_allowlist_rejects_normalized_duplicate_starts(
    tmp_path: Path,
):
    args = _args(tmp_path, ["first", "second"])
    args.expected_starts = ["2026-04-12T09", "2026-04-12T09:00:00"]

    with pytest.raises(ValueError, match="start allowlist contains duplicates"):
        _load_runs(args)


@pytest.mark.parametrize("filename", ["summary.json", "matched_lots.csv"])
def test_expected_run_allowlist_rejects_symlinked_artifacts(
    tmp_path: Path,
    filename: str,
):
    _write_summary(tmp_path, "expected")
    source = tmp_path / "expected" / filename
    escaped = tmp_path / f"escaped_{filename}"
    source.rename(escaped)
    source.symlink_to(escaped)

    with pytest.raises(ValueError, match="cannot be a symlink"):
        _load_runs(_args(tmp_path, ["expected"]))


@pytest.mark.parametrize("component", [".", "..", "nested/run"])
def test_expected_run_allowlist_rejects_path_components(
    tmp_path: Path,
    component: str,
):
    with pytest.raises(ValueError, match="single directory name"):
        _load_runs(_args(tmp_path, [component]))
