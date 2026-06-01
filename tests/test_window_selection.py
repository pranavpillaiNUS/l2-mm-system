"""Tests for deterministic V2 panel window selection."""

from datetime import datetime, timedelta
from decimal import Decimal

from scripts.select_l2_windows import _assert_disjoint_panels, _valid_hours
from src.analysis.window_selection import (
    WindowCandidate,
    WindowDescriptor,
    build_window_descriptors,
    compare_regime_descriptors,
    non_overlapping_capacity,
    select_balanced_windows,
)


def candidate(hour_offset, vol):
    start = datetime(2026, 4, 12) + timedelta(hours=hour_offset)
    return WindowCandidate(
        start=start,
        hours=5,
        realized_vol_bps=Decimal(str(vol)),
        mid_drift_bps=Decimal("0"),
    )


def test_select_balanced_windows_keeps_anchors_and_is_deterministic():
    candidates = [candidate(i * 5, i) for i in range(40)]
    anchors = [candidates[2].start, candidates[5].start]

    first = select_balanced_windows(candidates, anchor_starts=anchors, total_windows=8)
    second = select_balanced_windows(list(reversed(candidates)), anchor_starts=anchors, total_windows=8)

    assert [row.start for row in first] == [row.start for row in second]
    assert {row.start for row in first if row.source == "anchor"} == set(anchors)
    assert len(first) == 8


def test_select_balanced_windows_uses_earliest_tiebreaker():
    candidates = [candidate(i * 5, 1) for i in range(12)]
    selected = select_balanced_windows(
        candidates,
        anchor_starts=[],
        total_windows=3,
    )

    assert selected[0].start == candidates[0].start


def test_build_window_descriptors_uses_contiguous_cached_hours_only():
    start = datetime(2026, 4, 12)
    hourly = {
        start: [(0, Decimal("100")), (1_000, Decimal("101"))],
        start + timedelta(hours=1): [(3_600_000, Decimal("102"))],
        start + timedelta(hours=3): [(10_800_000, Decimal("103"))],
    }

    descriptors = build_window_descriptors(hourly, hours=2)

    assert [row.start for row in descriptors] == [start]


def test_non_overlapping_capacity_uses_fixed_width_intervals():
    candidates = [candidate(offset, 1) for offset in range(10)]

    assert non_overlapping_capacity(candidates) == 2


def descriptor(start, value):
    value = Decimal(str(value))
    return WindowDescriptor(
        start=start,
        hours=5,
        mid_drift_bps=value,
        abs_mid_drift_bps=abs(value),
        realized_vol_1s_bps=value,
        realized_vol_10s_bps=value,
        realized_vol_1m_bps=value,
        jump_count=int(value),
    )


def test_regime_comparison_treats_correlated_descriptors_as_one_screen():
    start = datetime(2026, 4, 12)
    development = [descriptor(start + timedelta(hours=5 * i), i) for i in (1, 2, 3)]
    comparable = [descriptor(start + timedelta(days=10, hours=5 * i), i) for i in (1, 2, 3)]
    shifted = [descriptor(start + timedelta(days=20, hours=5 * i), i) for i in (10, 11, 12)]

    assert compare_regime_descriptors(development, comparable)["label"] == "regime-comparable"
    assert compare_regime_descriptors(development, shifted)["label"] == "regime-shifted"


def test_selector_cutoff_filter_is_end_exclusive():
    cutoff = datetime(2026, 6, 1)
    manifest = {
        "hours": [
            {"start": (cutoff - timedelta(hours=1)).isoformat(), "valid": True},
            {"start": cutoff.isoformat(), "valid": True},
        ]
    }

    assert _valid_hours(manifest, cutoff=cutoff) == [cutoff - timedelta(hours=1)]


def test_selector_rejects_overlapping_development_and_holdout_panels():
    dev = [candidate(0, 1)]
    holdout = [candidate(4, 1)]

    try:
        _assert_disjoint_panels(dev, holdout)
    except ValueError as exc:
        assert "overlap" in str(exc)
    else:
        raise AssertionError("overlapping panels should fail")
