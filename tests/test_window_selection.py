"""Tests for deterministic V2 panel window selection."""

from datetime import datetime, timedelta
from decimal import Decimal

from src.analysis.window_selection import WindowCandidate, select_balanced_windows


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
