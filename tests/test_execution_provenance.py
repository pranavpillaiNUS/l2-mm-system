import json
from pathlib import Path

import pytest

from src.execution.provenance import (
    LEGACY_EXECUTION_MODEL_VERSION,
    artifact_execution_model,
    execution_provenance_for_replay,
    guard_event_driven_output_path,
    require_event_driven_provenance,
    require_safe_path_component,
)
from src.execution.simulator import EXECUTION_MODEL_VERSION, SimConfig


def test_frozen_v2_output_path_is_rejected():
    with pytest.raises(ValueError, match="frozen btcusdt_l2_panel_v2"):
        guard_event_driven_output_path(
            Path("results/panels/btcusdt_l2_panel_v2/markout_reconciliation")
        )


def test_nonlegacy_output_path_is_allowed():
    guard_event_driven_output_path(
        Path("results/panels/btcusdt_l2_panel_v3_event_driven")
    )


def test_symlink_alias_into_frozen_v2_output_path_is_rejected(tmp_path: Path):
    frozen = tmp_path / "btcusdt_l2_panel_v2"
    frozen.mkdir()
    alias = tmp_path / "current_output"
    alias.symlink_to(frozen, target_is_directory=True)

    with pytest.raises(ValueError, match="frozen btcusdt_l2_panel_v2"):
        guard_event_driven_output_path(alias / "markout_reconciliation")


def test_existing_output_file_symlink_is_rejected(tmp_path: Path):
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    alias = tmp_path / "report.json"
    alias.symlink_to(target)

    with pytest.raises(ValueError, match="cannot traverse a symlink"):
        guard_event_driven_output_path(alias)


def test_existing_output_tree_with_nested_symlink_is_rejected(tmp_path: Path):
    output_root = tmp_path / "v3"
    output_root.mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    (output_root / "redirected_run").symlink_to(
        target, target_is_directory=True
    )

    with pytest.raises(ValueError, match="contains a symlink write target"):
        guard_event_driven_output_path(output_root)


@pytest.mark.parametrize(
    "value",
    ["../btcusdt_l2_panel_v2", "nested/run", "nested\\run", ".", ""],
)
def test_user_controlled_artifact_name_must_be_one_safe_component(value: str):
    with pytest.raises(ValueError, match="single safe path component"):
        require_safe_path_component(value, label="--run-id")


def test_missing_artifact_model_metadata_means_legacy():
    assert artifact_execution_model({"params": {"latency_ms": 10}}) == (
        LEGACY_EXECUTION_MODEL_VERSION
    )


def test_explicit_artifact_model_metadata_is_used():
    payload = {
        "execution_provenance": {
            "execution_model_version": EXECUTION_MODEL_VERSION,
        }
    }
    assert artifact_execution_model(payload) == EXECUTION_MODEL_VERSION


def test_complete_event_driven_provenance_is_required_for_derived_artifacts():
    provenance = execution_provenance_for_replay(
        SimConfig(
            base_latency_ms=10,
            jitter_ms=0,
            maker_bps=2,
            taker_bps=5,
        ),
        trade_gap_policy="pause_until_snapshot",
    )
    assert require_event_driven_provenance({
        "execution_provenance": provenance,
    }) == provenance

    with pytest.raises(ValueError, match="incomplete execution provenance"):
        require_event_driven_provenance({
            "execution_provenance": {
                "execution_model_version": EXECUTION_MODEL_VERSION,
            }
        })


def test_queue_credit_provenance_is_canonical_across_decimal_spellings():
    def provenance(credit: str) -> dict:
        return SimConfig(
            base_latency_ms=10,
            jitter_ms=0,
            maker_bps=2,
            taker_bps=5,
            queue_cancellation_credit=credit,
        ).provenance

    assert provenance("1.0") == provenance("1")
    assert provenance("0.50") == provenance("0.5")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("trade_gap_policy", "invented", "invalid trade-gap policy"),
        ("post_only", False, "must be post-only"),
        ("entry_latency_ms", 10.5, "invalid latency or seed"),
    ],
)
def test_invalid_event_driven_provenance_domain_is_rejected(
    field: str,
    value,
    message: str,
):
    provenance = execution_provenance_for_replay(
        SimConfig(
            base_latency_ms=10,
            jitter_ms=0,
            maker_bps=2,
            taker_bps=5,
        ),
        trade_gap_policy="pause_until_snapshot",
    )
    provenance[field] = value

    with pytest.raises(ValueError, match=message):
        require_event_driven_provenance({"execution_provenance": provenance})


def test_frozen_v2_marker_declares_legacy_model():
    marker = (
        Path(__file__).resolve().parents[1]
        / "results/panels/btcusdt_l2_panel_v2/EXECUTION_MODEL.json"
    )
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["execution_model_version"] == LEGACY_EXECUTION_MODEL_VERSION
    assert payload["status"] == "frozen_historical_artifacts"
