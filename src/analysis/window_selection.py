"""Deterministic selection helpers for L2 panel windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import statistics
from typing import Iterable, Sequence

from src.analysis.bootstrap import percentile


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


@dataclass(frozen=True)
class WindowDescriptor:
    start: datetime
    hours: int
    mid_drift_bps: Decimal
    abs_mid_drift_bps: Decimal
    realized_vol_1s_bps: Decimal
    realized_vol_10s_bps: Decimal
    realized_vol_1m_bps: Decimal
    jump_count: int = 0

    @property
    def end(self) -> datetime:
        return self.start + timedelta(hours=self.hours)


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


def build_window_descriptors(
    hourly_second_mids: dict[datetime, Sequence[tuple[int, Decimal]]],
    *,
    hours: int,
) -> list[WindowDescriptor]:
    """Build rolling window descriptors from cached one-hour second samples."""
    available = set(hourly_second_mids)
    out = []
    for start in sorted(available):
        starts = [start + timedelta(hours=offset) for offset in range(hours)]
        if not all(hour in available for hour in starts):
            continue
        samples = [
            sample
            for hour in starts
            for sample in hourly_second_mids[hour]
        ]
        descriptor = describe_window(start, hours, samples)
        if descriptor is not None:
            out.append(descriptor)
    return out


def describe_window(
    start: datetime,
    hours: int,
    second_mids: Sequence[tuple[int, Decimal]],
    *,
    jump_threshold_bps: Decimal | None = None,
) -> WindowDescriptor | None:
    """Summarize regularly sampled mids for one candidate window."""
    mids = [mid for _, mid in sorted(second_mids)]
    if len(mids) < 2 or mids[0] <= 0:
        return None
    drift = ((mids[-1] - mids[0]) / mids[0]) * Decimal("10000")
    one_second_returns = _lag_returns(mids, 1)
    jumps = (
        sum(abs(value) > jump_threshold_bps for value in one_second_returns)
        if jump_threshold_bps is not None else 0
    )
    return WindowDescriptor(
        start=start,
        hours=hours,
        mid_drift_bps=drift,
        abs_mid_drift_bps=abs(drift),
        realized_vol_1s_bps=_rms(one_second_returns),
        realized_vol_10s_bps=_rms(_lag_returns(mids, 10)),
        realized_vol_1m_bps=_rms(_lag_returns(mids, 60)),
        jump_count=jumps,
    )


def jump_threshold(
    descriptors: Sequence[WindowDescriptor],
    hourly_second_mids: dict[datetime, Sequence[tuple[int, Decimal]]],
    *,
    quantile: Decimal = Decimal("0.99"),
) -> Decimal:
    """Development-panel threshold for counting large absolute one-second moves."""
    values = []
    for descriptor in descriptors:
        mids = [
            mid
            for offset in range(descriptor.hours)
            for _, mid in hourly_second_mids[descriptor.start + timedelta(hours=offset)]
        ]
        values.extend(abs(value) for value in _lag_returns(mids, 1))
    return percentile(values, quantile) or Decimal("0")


def with_jump_count(
    descriptor: WindowDescriptor,
    hourly_second_mids: dict[datetime, Sequence[tuple[int, Decimal]]],
    *,
    threshold_bps: Decimal,
) -> WindowDescriptor:
    samples = [
        sample
        for offset in range(descriptor.hours)
        for sample in hourly_second_mids[descriptor.start + timedelta(hours=offset)]
    ]
    updated = describe_window(
        descriptor.start,
        descriptor.hours,
        samples,
        jump_threshold_bps=threshold_bps,
    )
    if updated is None:
        raise ValueError(f"Unable to rebuild descriptor for {descriptor.start}")
    return updated


def non_overlapping_capacity(
    candidates: Sequence[WindowCandidate | WindowDescriptor],
) -> int:
    """Maximum capacity for fixed-width intervals using earliest-finish greediness."""
    selected = []
    for candidate in sorted(candidates, key=lambda row: row.start):
        if not selected or candidate.start >= selected[-1].end:
            selected.append(candidate)
    return len(selected)


def compare_regime_descriptors(
    development: Sequence[WindowDescriptor],
    holdout: Sequence[WindowDescriptor],
    *,
    max_abs_smd: Decimal = Decimal("0.5"),
) -> dict:
    """Compare holdout descriptors with a conservative development-band screen."""
    fields = (
        "mid_drift_bps",
        "abs_mid_drift_bps",
        "realized_vol_1s_bps",
        "realized_vol_10s_bps",
        "realized_vol_1m_bps",
        "jump_count",
    )
    rows = []
    for field in fields:
        dev = [Decimal(str(getattr(row, field))) for row in development]
        out = [Decimal(str(getattr(row, field))) for row in holdout]
        dev_p10 = percentile(dev, Decimal("0.10"))
        dev_p90 = percentile(dev, Decimal("0.90"))
        holdout_median = percentile(out, Decimal("0.50"))
        smd = _standardized_mean_difference(dev, out)
        median_in_band = (
            dev_p10 is not None
            and dev_p90 is not None
            and holdout_median is not None
            and dev_p10 <= holdout_median <= dev_p90
        )
        smd_pass = smd is not None and abs(smd) <= max_abs_smd
        rows.append({
            "descriptor": field,
            "development_p10": dev_p10,
            "development_p90": dev_p90,
            "holdout_median": holdout_median,
            "standardized_mean_difference": smd,
            "median_in_development_band": median_in_band,
            "abs_smd_within_limit": smd_pass,
            "passes": median_in_band and smd_pass,
        })
    return {
        "label": (
            "regime-comparable"
            if rows and all(row["passes"] for row in rows)
            else "regime-shifted"
        ),
        "max_abs_smd": max_abs_smd,
        "note": (
            "Descriptors are correlated context checks, not independent evidence "
            "and not a strategy-tuning input."
        ),
        "descriptors": rows,
    }


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


def _lag_returns(mids: Sequence[Decimal], lag: int) -> list[Decimal]:
    return [
        ((mids[idx] - mids[idx - lag]) / mids[idx - lag]) * Decimal("10000")
        for idx in range(lag, len(mids))
        if mids[idx - lag] > 0
    ]


def _rms(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return Decimal(str(statistics.fmean(float(value * value) for value in values) ** 0.5))


def _standardized_mean_difference(
    left: Sequence[Decimal],
    right: Sequence[Decimal],
) -> Decimal | None:
    if not left or not right:
        return None
    left_mean = statistics.fmean(float(value) for value in left)
    right_mean = statistics.fmean(float(value) for value in right)
    left_var = statistics.pvariance(float(value) for value in left) if len(left) > 1 else 0
    right_var = statistics.pvariance(float(value) for value in right) if len(right) > 1 else 0
    pooled_std = ((left_var + right_var) / 2) ** 0.5
    if pooled_std == 0:
        return Decimal("0") if left_mean == right_mean else None
    return Decimal(str((right_mean - left_mean) / pooled_std))
