"""Tests for the frozen endpoint-aware Phase A baseline verdicts."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.classify_v2_baseline import _load_legacy_endpoint
from src.analysis.v2_classification import (
    CONDITIONAL_V2,
    classify_endpoint,
    classify_phase_a,
)
from src.execution.simulator import SimConfig


def endpoint(credit, mean, low, high, frozen="-2"):
    return classify_endpoint(
        queue_cancellation_credit=Decimal(credit),
        mean_net_pnl=Decimal(mean),
        ci_low=Decimal(low),
        ci_high=Decimal(high),
        frozen_v1_mean_net_pnl=Decimal(frozen),
    )


def test_endpoint_verdicts_form_an_ordered_non_overlapping_ladder():
    assert endpoint("0", "1", "0.1", "2").verdict == "Overturns V1"
    assert endpoint("0", "-2", "-3", "-0.1").verdict == "Strengthens V1"
    assert endpoint("0", "-1.4", "-2", "0.1").verdict == "Weakens V1"
    assert endpoint("0", "-1.6", "-2", "0.1").verdict == "Confirms V1"


def test_endpoint_disagreement_emits_conditional_v2_and_conservative_headline():
    report = classify_phase_a({
        "0.0": endpoint("0", "-2", "-3", "-0.1"),
        "1.0": endpoint("1", "1", "0.1", "2"),
    })

    assert report["headline"] == CONDITIONAL_V2
    assert report["conservative_passive_edge_verdict"] == "Strengthens V1"


def test_historical_classifier_rejects_event_driven_artifact(tmp_path: Path):
    path = tmp_path / "event_driven_ci.json"
    path.write_text(json.dumps({
        "execution_provenance": SimConfig(
            base_latency_ms=10,
            jitter_ms=0,
            maker_bps=2,
            taker_bps=5,
        ).provenance,
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="accepts only legacy execution artifacts"):
        _load_legacy_endpoint(path)


def test_historical_classifier_accepts_missing_model_as_legacy(tmp_path: Path):
    path = tmp_path / "legacy_ci.json"
    path.write_text(json.dumps({"params": {"latency_ms": 10}}), encoding="utf-8")

    assert _load_legacy_endpoint(path)["params"]["latency_ms"] == 10
