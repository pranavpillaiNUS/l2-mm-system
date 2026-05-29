"""Deterministic selection helpers for L2 panel windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable, Sequence


UTC_BUCKETS = (
    ("00-06", range(0, 7)),
    ("07-12", range(7, 13)),
    ("13-16", range(13, 17)),
    ("17-23", range(17, 24)),
)


@dataclass(frozen=True)
class WindowCandidate:
    start: datetime
    hours: int
    realized_vol_bps: Decimal
    mid_drift_bps: Decimal
    source: str = "candidate"

    @property
    def end(self) -> datetime:
        return self.start + timedelta(hours=self.hours)

    @property
    def utc_bucket(self) -> str:
        return utc_bucket(self.start.hour)


@dataclass(frozen=True)
class SelectedWindow:
    start: datetime
    hours: int
    source: str
    utc_bucket: str
    vol_tercile: str
    realized_vol_bps: Decimal
    mid_drift_bps: Decimal


def utc_bucket(hour: int) -> str:
    for label, hours in UTC_BUCKETS:
        if hour in hours:
            return label
    raise ValueError(f"hour must be in 0..23, got {hour}")


def assign_vol_terciles(candidates: Sequence[WindowCandidate]) -> dict[datetime, str]:
    """Assign low/mid/high terciles with deterministic tie ordering."""
    ordered = sorted(candidates, key=lambda row: (row.realized_vol_bps, row.start))
    n = len(ordered)
    out: dict[datetime, str] = {}
    for idx, row in enumerate(ordered):
        if idx < n / 3:
            tercile = "low"
        elif idx < 2 * n / 3:
            tercile = "mid"
        else:
            tercile = "high"
        out[row.start] = tercile
    return out


def select_balanced_windows(
    candidates: Sequence[WindowCandidate],
    *,
    anchor_starts: Iterable[datetime],
    total_windows: int = 24,
) -> list[SelectedWindow]:
    """Keep anchors and fill a balanced UTC-bucket/vol-tercile panel."""
    if total_windows <= 0:
        raise ValueError("total_windows must be positive")

    by_start = {candidate.start: candidate for candidate in candidates}
    missing = [start for start in anchor_starts if start not in by_start]
    if missing:
        missing_str = ", ".join(start.isoformat() for start in missing)
        raise ValueError(f"Anchor windows missing from candidates: {missing_str}")

    terciles = assign_vol_terciles(candidates)
    selected: list[SelectedWindow] = []
    selected_intervals: list[tuple[datetime, datetime]] = []

    def add(candidate: WindowCandidate, source: str) -> bool:
        if len(selected) >= total_windows:
            return False
        if _overlaps_any(candidate, selected_intervals):
            return False
        selected.append(_to_selected(candidate, source, terciles[candidate.start]))
        selected_intervals.append((candidate.start, candidate.end))
        return True

    for start in sorted(anchor_starts):
        add(by_start[start], "anchor")

    cell_targets = _cell_targets(total_windows)
    ordered_candidates = sorted(candidates, key=lambda row: row.start)
    for bucket in [label for label, _ in UTC_BUCKETS]:
        for tercile in ("low", "mid", "high"):
            while _cell_count(selected, bucket, tercile) < cell_targets:
                if len(selected) >= total_windows:
                    break
                next_candidate = _next_candidate(
                    ordered_candidates,
                    selected_intervals,
                    terciles,
                    bucket=bucket,
                    tercile=tercile,
                )
                if next_candidate is None:
                    break
                add(next_candidate, "balanced_fill")

    while len(selected) < total_windows:
        next_candidate = _next_underrepresented_candidate(
            ordered_candidates,
            selected,
            selected_intervals,
            terciles,
        )
        if next_candidate is None:
            break
        add(next_candidate, "topup")

    return sorted(selected, key=lambda row: row.start)


def _cell_targets(total_windows: int) -> int:
    cells = len(UTC_BUCKETS) * 3
    return (total_windows + cells - 1) // cells


def _to_selected(
    candidate: WindowCandidate,
    source: str,
    tercile: str,
) -> SelectedWindow:
    return SelectedWindow(
        start=candidate.start,
        hours=candidate.hours,
        source=source,
        utc_bucket=candidate.utc_bucket,
        vol_tercile=tercile,
        realized_vol_bps=candidate.realized_vol_bps,
        mid_drift_bps=candidate.mid_drift_bps,
    )


def _overlaps_any(
    candidate: WindowCandidate,
    intervals: Sequence[tuple[datetime, datetime]],
) -> bool:
    return any(candidate.start < end and candidate.end > start for start, end in intervals)


def _cell_count(
    selected: Sequence[SelectedWindow],
    bucket: str,
    tercile: str,
) -> int:
    return sum(
        1 for row in selected
        if row.utc_bucket == bucket and row.vol_tercile == tercile
    )


def _next_candidate(
    candidates: Sequence[WindowCandidate],
    intervals: Sequence[tuple[datetime, datetime]],
    terciles: dict[datetime, str],
    *,
    bucket: str,
    tercile: str,
) -> WindowCandidate | None:
    for candidate in candidates:
        if candidate.utc_bucket != bucket:
            continue
        if terciles[candidate.start] != tercile:
            continue
        if _overlaps_any(candidate, intervals):
            continue
        return candidate
    return None


def _next_underrepresented_candidate(
    candidates: Sequence[WindowCandidate],
    selected: Sequence[SelectedWindow],
    intervals: Sequence[tuple[datetime, datetime]],
    terciles: dict[datetime, str],
) -> WindowCandidate | None:
    cell_counts = {
        (bucket, tercile): _cell_count(selected, bucket, tercile)
        for bucket, _ in UTC_BUCKETS
        for tercile in ("low", "mid", "high")
    }
    available = [
        candidate for candidate in candidates
        if not _overlaps_any(candidate, intervals)
    ]
    if not available:
        return None
    return min(
        available,
        key=lambda candidate: (
            cell_counts[(candidate.utc_bucket, terciles[candidate.start])],
            candidate.start,
        ),
    )
