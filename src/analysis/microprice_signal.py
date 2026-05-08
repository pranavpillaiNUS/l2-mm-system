"""
Microprice predictiveness diagnostics.

This module tests the premise behind microprice market making directly:
does microprice deviation from mid predict future mid drift?

It is intentionally strategy-independent. It only needs recorded book samples.
"""
import math
import statistics
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, Sequence

from src.replay.engine import BookSample


DEFAULT_HORIZONS_MS: Dict[str, int] = {
    "1s": 1_000,
    "10s": 10_000,
    "1m": 60_000,
    "5m": 300_000,
}

DEFAULT_SIGNAL_BUCKET_EDGES_BPS = (-0.5, -0.1, 0.1, 0.5)


@dataclass(frozen=True)
class MicropriceSignalSample:
    timestamp_ms: int
    source_timestamp_ms: int
    horizon: str
    horizon_ms: int
    future_timestamp_ms: int
    mid: Decimal
    microprice: Decimal
    future_mid: Decimal
    microprice_deviation_bps: Decimal
    forward_drift_bps: Decimal


@dataclass(frozen=True)
class MicropriceRegression:
    horizon: str
    horizon_ms: int
    n: int
    alpha: float | None
    beta: float | None
    r2: float | None
    t_stat: float | None
    hac_lags: int
    x_mean_bps: float | None
    y_mean_bps: float | None
    x_std_bps: float | None
    y_std_bps: float | None
    predicted_drift_1std_bps: float | None


@dataclass(frozen=True)
class SignalBucket:
    horizon: str
    label: str
    n: int
    avg_microprice_deviation_bps: Decimal | None
    avg_forward_drift_bps: Decimal | None
    median_forward_drift_bps: Decimal | None


def compute_microprice_signal_samples(
    book_samples: Sequence[BookSample],
    *,
    sample_interval_ms: int = 1_000,
    horizons_ms: Dict[str, int] | None = None,
    max_staleness_ms: int | None = 1_000,
    max_future_lag_ms: int | None = 1_000,
) -> list[MicropriceSignalSample]:
    """
    Sample the book at regular intervals and compute forward mid drift.

    At each target timestamp, the current book is the latest sample at or
    before the target, so the diagnostic does not peek forward. The future mid
    is the first sample at or after target + horizon.
    """
    if sample_interval_ms <= 0:
        raise ValueError("sample_interval_ms must be positive")

    horizons = horizons_ms or DEFAULT_HORIZONS_MS
    samples = sorted(
        (
            sample for sample in book_samples
            if sample.mid > 0 and sample.microprice is not None
        ),
        key=lambda sample: sample.timestamp_ms,
    )
    if not samples:
        return []

    sample_times = [sample.timestamp_ms for sample in samples]
    start = _ceil_to_interval(sample_times[0], sample_interval_ms)
    end = sample_times[-1]

    rows: list[MicropriceSignalSample] = []
    for target_ms in range(start, end + 1, sample_interval_ms):
        current_idx = bisect_right(sample_times, target_ms) - 1
        if current_idx < 0:
            continue

        current = samples[current_idx]
        if (
            max_staleness_ms is not None
            and target_ms - current.timestamp_ms > max_staleness_ms
        ):
            continue

        microprice = current.microprice
        if microprice is None:
            continue

        signal_bps = ((microprice - current.mid) / current.mid) * Decimal("10000")

        for horizon, horizon_ms in horizons.items():
            future_target_ms = target_ms + horizon_ms
            future_idx = bisect_left(sample_times, future_target_ms)
            if future_idx >= len(samples):
                continue

            future = samples[future_idx]
            if (
                max_future_lag_ms is not None
                and future.timestamp_ms - future_target_ms > max_future_lag_ms
            ):
                continue

            forward_bps = ((future.mid - current.mid) / current.mid) * Decimal("10000")
            rows.append(MicropriceSignalSample(
                timestamp_ms=target_ms,
                source_timestamp_ms=current.timestamp_ms,
                horizon=horizon,
                horizon_ms=horizon_ms,
                future_timestamp_ms=future.timestamp_ms,
                mid=current.mid,
                microprice=microprice,
                future_mid=future.mid,
                microprice_deviation_bps=signal_bps,
                forward_drift_bps=forward_bps,
            ))

    return rows


def regress_microprice_signal(
    rows: Sequence[MicropriceSignalSample],
    *,
    sample_interval_ms: int = 1_000,
    hac_lags: int | None = None,
) -> list[MicropriceRegression]:
    """Regress forward drift on microprice deviation for each horizon."""
    by_horizon: dict[str, list[MicropriceSignalSample]] = {}
    for row in rows:
        by_horizon.setdefault(row.horizon, []).append(row)

    results = []
    for horizon, horizon_rows in sorted(
        by_horizon.items(), key=lambda item: item[1][0].horizon_ms
    ):
        first = horizon_rows[0]
        x = [float(row.microprice_deviation_bps) for row in horizon_rows]
        y = [float(row.forward_drift_bps) for row in horizon_rows]
        lags = (
            hac_lags
            if hac_lags is not None
            else _default_hac_lags(len(horizon_rows), first.horizon_ms, sample_interval_ms)
        )
        alpha, beta, r2, t_stat = _ols_hac(x, y, lags)
        x_std = _std_float(x)
        results.append(MicropriceRegression(
            horizon=horizon,
            horizon_ms=first.horizon_ms,
            n=len(horizon_rows),
            alpha=alpha,
            beta=beta,
            r2=r2,
            t_stat=t_stat,
            hac_lags=lags,
            x_mean_bps=_mean_float(x),
            y_mean_bps=_mean_float(y),
            x_std_bps=x_std,
            y_std_bps=_std_float(y),
            predicted_drift_1std_bps=(
                beta * x_std if beta is not None and x_std is not None else None
            ),
        ))

    return results


def bucket_forward_drift_by_signal(
    rows: Sequence[MicropriceSignalSample],
    *,
    edges_bps: Sequence[float] = DEFAULT_SIGNAL_BUCKET_EDGES_BPS,
) -> list[SignalBucket]:
    """Bucket forward drift by microprice deviation bucket."""
    by_horizon: dict[str, list[MicropriceSignalSample]] = {}
    for row in rows:
        by_horizon.setdefault(row.horizon, []).append(row)

    buckets: list[SignalBucket] = []
    for horizon, horizon_rows in sorted(
        by_horizon.items(), key=lambda item: item[1][0].horizon_ms
    ):
        for label, lo, hi in _bucket_ranges(edges_bps):
            in_bucket = [
                row for row in horizon_rows
                if _in_range(float(row.microprice_deviation_bps), lo, hi)
            ]
            drift_values = [row.forward_drift_bps for row in in_bucket]
            signal_values = [row.microprice_deviation_bps for row in in_bucket]
            buckets.append(SignalBucket(
                horizon=horizon,
                label=label,
                n=len(in_bucket),
                avg_microprice_deviation_bps=_mean_decimal(signal_values),
                avg_forward_drift_bps=_mean_decimal(drift_values),
                median_forward_drift_bps=_median_decimal(drift_values),
            ))

    return buckets


def _ceil_to_interval(value: int, interval: int) -> int:
    return ((value + interval - 1) // interval) * interval


def _default_hac_lags(n: int, horizon_ms: int, sample_interval_ms: int) -> int:
    if n <= 2:
        return 0
    overlap_lags = math.ceil(horizon_ms / sample_interval_ms)
    nw_lags = int(4 * (n / 100) ** (2 / 9))
    return min(n - 1, max(1, overlap_lags, nw_lags))


def _ols_hac(
    x: Sequence[float],
    y: Sequence[float],
    hac_lags: int,
) -> tuple[float | None, float | None, float | None, float | None]:
    n = len(x)
    if n < 3:
        return None, None, None, None

    sum_x = sum(x)
    sum_y = sum(y)
    sum_xx = sum(val * val for val in x)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    det = n * sum_xx - sum_x * sum_x
    if abs(det) < 1e-18:
        return None, None, None, None

    beta = (n * sum_xy - sum_x * sum_y) / det
    alpha = (sum_y - beta * sum_x) / n
    residuals = [yi - alpha - beta * xi for xi, yi in zip(x, y)]

    y_mean = sum_y / n
    sse = sum(err * err for err in residuals)
    tss = sum((yi - y_mean) ** 2 for yi in y)
    r2 = None if tss <= 0 else 1 - sse / tss

    lags = min(max(hac_lags, 0), n - 1)
    s00 = 0.0
    s01 = 0.0
    s11 = 0.0

    for xt, et in zip(x, residuals):
        e2 = et * et
        s00 += e2
        s01 += e2 * xt
        s11 += e2 * xt * xt

    for lag in range(1, lags + 1):
        weight = 1 - lag / (lags + 1)
        for idx in range(lag, n):
            xt = x[idx]
            xl = x[idx - lag]
            prod = weight * residuals[idx] * residuals[idx - lag]
            s00 += 2 * prod
            s01 += prod * (xt + xl)
            s11 += 2 * prod * xt * xl

    inv00 = sum_xx / det
    inv01 = -sum_x / det
    inv11 = n / det

    var_beta = inv01 * inv01 * s00 + 2 * inv01 * inv11 * s01 + inv11 * inv11 * s11
    if var_beta <= 0:
        t_stat = None
    else:
        se_beta = math.sqrt(var_beta)
        t_stat = beta / se_beta if se_beta > 0 else None

    return alpha, beta, r2, t_stat


def _bucket_ranges(edges_bps: Sequence[float]) -> Iterable[tuple[str, float | None, float | None]]:
    edges = sorted(edges_bps)
    if not edges:
        yield "all", None, None
        return

    yield f"<{edges[0]}bps", None, edges[0]
    for lo, hi in zip(edges, edges[1:]):
        yield f"[{lo},{hi})bps", lo, hi
    yield f">={edges[-1]}bps", edges[-1], None


def _in_range(value: float, lo: float | None, hi: float | None) -> bool:
    if lo is not None and value < lo:
        return False
    if hi is not None and value >= hi:
        return False
    return True


def _mean_decimal(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _median_decimal(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return Decimal(str(statistics.median(values)))


def _mean_float(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _std_float(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    return statistics.stdev(values)
