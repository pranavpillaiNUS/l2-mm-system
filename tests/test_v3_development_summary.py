"""The corrected screen preserves weak-support and uncertainty boundaries."""

from dataclasses import asdict
from decimal import Decimal as D

import pytest

from scripts.summarize_v3_development import (
    _jsonable, combined_screen, conditional_cluster_bootstrap, endpoint_screen,
)
from src.analysis.ofi_signal import OFIRegression, OFIFillToxicityBucket, evaluate_ofi_gates


STARTS = ["2026-04-12T09:00:00", "2026-04-13T09:00:00"]


def evidence(*, count=40, separation="2", t_stat=4.0):
    regression = OFIRegression(
        horizon="1s", horizon_ms=1000, signal="normalized_ofi", n=100,
        alpha=0.0, beta=0.1, r2=0.03, t_stat=t_stat, hac_lags=5,
        x_mean=0.0, y_mean_bps=0.0, x_std=1.0, y_std_bps=1.0,
        predicted_drift_1std_bps=0.1,
    )
    buckets = [
        OFIFillToxicityBucket("30s", "all", "[-0.25,0.0)", count,
                             D("-0.1"), D("-1"), D("-1")),
        OFIFillToxicityBucket("30s", "all", "[0.0,0.25)", count,
                             D("0.1"), D(separation) - 1, D(separation) - 1),
    ]
    gate = evaluate_ofi_gates(window_regressions=[regression] * 2,
                             pooled_regressions=[regression], fill_buckets=buckets)
    payload = _jsonable({
        "pooled_regressions": [asdict(regression)],
        "fill_buckets": [asdict(row) for row in buckets], "gates": asdict(gate),
    })
    regressions = [
        {**asdict(regression), "window": start.replace("T", " ")[:16]}
        for start in STARTS
    ]
    rows = []
    for start in STARTS:
        for bucket in buckets:
            rows.extend({
                "block_start": start.replace("T", " ")[:16],
                "horizon": "30s", "side_aligned_ofi": str(bucket.avg_side_aligned_ofi),
                "side_normalized_mid_move_bps": str(bucket.avg_mid_move_bps),
            } for _ in range(count // 2))
    return payload, regressions, buckets, rows


@pytest.mark.parametrize("count,separation,t_stat,expected", [
    (40, "2", 4.0, "supported"), (10, "2", 4.0, "inconclusive"),
    (40, "0.5", 4.0, "blocked"), (40, "2", 0.5, "blocked"),
    (40, "1", 4.0, "supported"),
])
def test_endpoint_screen_preserves_locked_thresholds(count, separation, t_stat, expected):
    payload, regressions, _, _ = evidence(count=count, separation=separation, t_stat=t_stat)
    screen = endpoint_screen(payload, regressions, STARTS)
    assert screen["status"] == expected
    assert screen["fallback_5s_role"] == "exploratory_excluded_from_decision"
    if count < 30:
        assert screen["conditional_below_1bps_hypothesis"] is None


def test_screen_rejects_missing_window_and_changed_upstream_gate():
    payload, regressions, _, _ = evidence()
    with pytest.raises(ValueError, match="exact development panel"):
        endpoint_screen(payload, regressions[:-1], STARTS)
    payload["gates"]["conditional_separation_bps"] = "100"
    with pytest.raises(ValueError, match="stored gates disagree"):
        endpoint_screen(payload, regressions, STARTS)


@pytest.mark.parametrize("left,right,expected", [
    ("supported", "supported", "supported"),
    ("supported", "inconclusive", "inconclusive"),
    ("blocked", "supported", "blocked"),
    ("inconclusive", "blocked", "blocked"),
])
def test_combined_screen_requires_both_endpoints(left, right, expected):
    assert combined_screen({"0": {"status": left}, "1": {"status": right}}) == expected
    with pytest.raises(ValueError, match="both queue-credit"):
        combined_screen({"0": {"status": left}})


def test_cluster_bootstrap_resamples_windows_reproducibly():
    _, _, buckets, rows = evidence()
    report = conditional_cluster_bootstrap(rows, buckets, STARTS, iterations=100)
    assert report == conditional_cluster_bootstrap(rows, buckets, STARTS, iterations=100)
    assert report["status"] == "available"
    assert D(report["mean_bps"]) == D(report["ci_low_bps"]) == D(report["ci_high_bps"]) == 2
    assert report["windows"] == 2
    assert report["role"] == "diagnostic_only_does_not_change_gate"


def test_cluster_bootstrap_keeps_empty_windows_and_withholds_undefined_interval():
    _, _, buckets, rows = evidence()
    for row in rows:
        row["block_start"] = "2026-04-12 09:00"
    report = conditional_cluster_bootstrap(rows, buckets, STARTS, iterations=100)
    assert report["windows"] == 2
    assert report["status"] == "inconclusive_undefined_resamples"
    assert report["undefined_resamples"] > 0
    assert report["ci_low_bps"] is report["ci_high_bps"] is None


def test_cluster_bootstrap_rejects_omitted_fills_and_non_panel_rows():
    _, _, buckets, rows = evidence()
    with pytest.raises(ValueError, match="counts disagree"):
        conditional_cluster_bootstrap(rows[:-1], buckets, STARTS, iterations=100)
    rows[0]["block_start"] = "2099-01-01 00:00"
    with pytest.raises(ValueError, match="outside the development panel"):
        conditional_cluster_bootstrap(rows, buckets, STARTS, iterations=100)
