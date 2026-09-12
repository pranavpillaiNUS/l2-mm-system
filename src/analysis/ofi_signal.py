"""Order-flow imbalance diagnostics for top-of-book L2 samples."""

from __future__ import annotations

import statistics
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, Sequence

from src.analysis.microprice_signal import (
    _default_hac_lags,
    _mean_float,
    _ols_hac,
    _std_float,
)
from src.execution.order import Fill, OrderSide
from src.replay.engine import BookSample


DEFAULT_OFI_BUCKET_EDGES = (-1.0, -0.25, 0.0, 0.25, 1.0)
DEFAULT_ALIGNED_OFI_BUCKET_EDGES = (-1.0, -0.25, 0.0, 0.25, 1.0)
DEFAULT_OFI_HORIZONS_MS: Dict[str, int] = {
    "1s": 1_000,
    "10s": 10_000,
    "1m": 60_000,
    "5m": 300_000,
}
DEFAULT_OFI_FILL_HORIZONS_MS: Dict[str, int] = {
    "1s": 1_000,
    "10s": 10_000,
    "30s": 30_000,
    "1m": 60_000,
    "5m": 300_000,
}


@dataclass(frozen=True)
class OFISignalSample:
    timestamp_ms: int
    interval_start_ms: int
    horizon: str
    horizon_ms: int
    future_timestamp_ms: int
    mid: Decimal
    future_mid: Decimal
    raw_ofi: Decimal
    normalized_ofi: Decimal
    forward_drift_bps: Decimal


@dataclass(frozen=True)
class OFIRegression:
    horizon: str
    horizon_ms: int
    signal: str
    n: int
    alpha: float | None
    beta: float | None
    r2: float | None
    t_stat: float | None
    hac_lags: int
    x_mean: float | None
    y_mean_bps: float | None
    x_std: float | None
    y_std_bps: float | None
    predicted_drift_1std_bps: float | None


@dataclass(frozen=True)
class OFIBucket:
    horizon: str
    signal: str
    label: str
    n: int
    avg_signal: Decimal | None
    avg_forward_drift_bps: Decimal | None
    median_forward_drift_bps: Decimal | None


@dataclass(frozen=True)
class OFIFillToxicityRow:
    fill_id: str
    order_id: str
    side: OrderSide
    fill_timestamp_ms: int
    fill_price: Decimal
    quantity: Decimal
    is_maker: bool
    horizon: str
    horizon_ms: int
    interval_start_ms: int
    book_timestamp_ms: int
    future_timestamp_ms: int
    mid_at_fill: Decimal
    future_mid: Decimal
    raw_ofi: Decimal
    normalized_ofi: Decimal
    side_aligned_ofi: Decimal
    side_normalized_mid_move_bps: Decimal


@dataclass(frozen=True)
class OFIFillToxicityBucket:
    horizon: str
    side: str
    bucket: str
    n: int
    avg_side_aligned_ofi: Decimal | None
    avg_mid_move_bps: Decimal | None
    median_mid_move_bps: Decimal | None


@dataclass(frozen=True)
class OFIGateResult:
    stable_sign_windows: int
    total_windows: int
    stable_sign_share: Decimal
    stable_sign_pass: bool
    pooled_abs_t_stat: float | None
    pooled_t_stat_pass: bool
    pooled_abs_predicted_drift_1std_bps: float | None
    pooled_effect_pass: bool
    conditional_separation_bps: Decimal | None
    conditional_bucket_count_min: int
    conditional_status: str
    conditional_pass: bool
    unconditional_pass: bool
    overall_verdict: str
    all_pass: bool
    marginal_5s_fallback: bool


def compute_ofi_signal_samples(
    book_samples: Sequence[BookSample],
    *,
    sample_interval_ms: int = 1_000,
    ofi_interval_ms: int = 1_000,
    horizons_ms: Dict[str, int] | None = None,
    max_staleness_ms: int | None = 1_000,
    max_future_lag_ms: int | None = 1_000,
) -> list[OFISignalSample]:
    """Sample regular timestamps and compute OFI over the previous interval."""
    if sample_interval_ms <= 0:
        raise ValueError("sample_interval_ms must be positive")
    if ofi_interval_ms <= 0:
        raise ValueError("ofi_interval_ms must be positive")

    horizons = horizons_ms or DEFAULT_OFI_HORIZONS_MS
    samples = _valid_samples(book_samples)
    if len(samples) < 2:
        return []

    times = [sample.timestamp_ms for sample in samples]
    start = _ceil_to_interval(times[0] + ofi_interval_ms, sample_interval_ms)
    end = times[-1]

    rows: list[OFISignalSample] = []
    for target_ms in range(start, end + 1, sample_interval_ms):
        current_idx = bisect_right(times, target_ms) - 1
        if current_idx <= 0:
            continue
        current = samples[current_idx]
        if (
            max_staleness_ms is not None
            and target_ms - current.timestamp_ms > max_staleness_ms
        ):
            continue

        interval_start_ms = target_ms - ofi_interval_ms
        raw_ofi, normalized_ofi = _ofi_between(samples, times, interval_start_ms, target_ms)

        for horizon, horizon_ms in horizons.items():
            future_target_ms = target_ms + horizon_ms
            future_idx = bisect_left(times, future_target_ms)
            if future_idx >= len(samples):
                continue
            future = samples[future_idx]
            if (
                max_future_lag_ms is not None
                and future.timestamp_ms - future_target_ms > max_future_lag_ms
            ):
                continue

            forward_bps = ((future.mid - current.mid) / current.mid) * Decimal("10000")
            rows.append(OFISignalSample(
                timestamp_ms=target_ms,
                interval_start_ms=interval_start_ms,
                horizon=horizon,
                horizon_ms=horizon_ms,
                future_timestamp_ms=future.timestamp_ms,
                mid=current.mid,
                future_mid=future.mid,
                raw_ofi=raw_ofi,
                normalized_ofi=normalized_ofi,
                forward_drift_bps=forward_bps,
            ))

    return rows


def regress_ofi_signal(
    rows: Sequence[OFISignalSample],
    *,
    signal: str = "normalized_ofi",
    sample_interval_ms: int = 1_000,
    hac_lags: int | None = None,
) -> list[OFIRegression]:
    """Regress forward drift on OFI for each horizon."""
    if signal not in {"normalized_ofi", "raw_ofi"}:
        raise ValueError("signal must be normalized_ofi or raw_ofi")

    by_horizon: dict[str, list[OFISignalSample]] = {}
    for row in rows:
        by_horizon.setdefault(row.horizon, []).append(row)

    out: list[OFIRegression] = []
    for horizon, horizon_rows in sorted(
        by_horizon.items(), key=lambda item: item[1][0].horizon_ms
    ):
        first = horizon_rows[0]
        x = [float(getattr(row, signal)) for row in horizon_rows]
        y = [float(row.forward_drift_bps) for row in horizon_rows]
        lags = (
            hac_lags
            if hac_lags is not None
            else _default_hac_lags(len(horizon_rows), first.horizon_ms, sample_interval_ms)
        )
        alpha, beta, r2, t_stat = _ols_hac(x, y, lags)
        x_std = _std_float(x)
        out.append(OFIRegression(
            horizon=horizon,
            horizon_ms=first.horizon_ms,
            signal=signal,
            n=len(horizon_rows),
            alpha=alpha,
            beta=beta,
            r2=r2,
            t_stat=t_stat,
            hac_lags=lags,
            x_mean=_mean_float(x),
            y_mean_bps=_mean_float(y),
            x_std=x_std,
            y_std_bps=_std_float(y),
            predicted_drift_1std_bps=(
                beta * x_std if beta is not None and x_std is not None else None
            ),
        ))
    return out


def bucket_forward_drift_by_ofi(
    rows: Sequence[OFISignalSample],
    *,
    signal: str = "normalized_ofi",
    edges: Sequence[float] = DEFAULT_OFI_BUCKET_EDGES,
) -> list[OFIBucket]:
    """Bucket forward drift by OFI value."""
    by_horizon: dict[str, list[OFISignalSample]] = {}
    for row in rows:
        by_horizon.setdefault(row.horizon, []).append(row)

    buckets: list[OFIBucket] = []
    for horizon, horizon_rows in sorted(
        by_horizon.items(), key=lambda item: item[1][0].horizon_ms
    ):
        for label, lo, hi in _bucket_ranges(edges):
            in_bucket = [
                row for row in horizon_rows
                if _in_range(float(getattr(row, signal)), lo, hi)
            ]
            buckets.append(OFIBucket(
                horizon=horizon,
                signal=signal,
                label=label,
                n=len(in_bucket),
                avg_signal=_mean_decimal(getattr(row, signal) for row in in_bucket),
                avg_forward_drift_bps=_mean_decimal(
                    row.forward_drift_bps for row in in_bucket
                ),
                median_forward_drift_bps=_median_decimal(
                    row.forward_drift_bps for row in in_bucket
                ),
            ))
    return buckets


def compute_ofi_fill_toxicity(
    fills: Iterable[Fill],
    book_samples: Sequence[BookSample],
    horizons_ms: dict[str, int] | None = None,
    *,
    ofi_interval_ms: int = 1_000,
    maker_only: bool = True,
    max_staleness_ms: int | None = 1_000,
    max_future_lag_ms: int | None = 1_000,
) -> list[OFIFillToxicityRow]:
    """Compute prior OFI and future side-normalized movement for maker fills."""
    horizons = horizons_ms or DEFAULT_OFI_FILL_HORIZONS_MS
    samples = _valid_samples(book_samples)
    if len(samples) < 2:
        return []

    times = [sample.timestamp_ms for sample in samples]
    rows: list[OFIFillToxicityRow] = []
    for fill in fills:
        if maker_only and not fill.is_maker:
            continue

        # Strictly-pre-fill anchor: the most recent book sample STRICTLY before
        # the fill. A sample stamped exactly at fill_ts can encode the same-ms
        # market move associated with the simulated fill. Including it would
        # leak that move into both the OFI window and the reference mid, biasing
        # the conditional toxicity test toward a spurious pass. bisect_left
        # excludes the same-ms sample, bisect_right (the old behaviour) would
        # have included it.
        current_idx = bisect_left(times, fill.timestamp_ms) - 1
        if current_idx <= 0:
            continue
        current = samples[current_idx]
        if (
            max_staleness_ms is not None
            and fill.timestamp_ms - current.timestamp_ms > max_staleness_ms
        ):
            continue

        interval_start_ms = fill.timestamp_ms - ofi_interval_ms
        # end_inclusive=False keeps the OFI window strictly before the fill, so
        # the forward (post-fill) window and the OFI window share only the
        # pre-fill anchor point and never a common increment.
        raw_ofi, normalized_ofi = _ofi_between(
            samples, times, interval_start_ms, fill.timestamp_ms, end_inclusive=False
        )
        side_sign = _side_sign(fill.side)
        side_aligned = side_sign * normalized_ofi

        for horizon, horizon_ms in horizons.items():
            future_target_ms = fill.timestamp_ms + horizon_ms
            future_idx = bisect_left(times, future_target_ms)
            if future_idx >= len(samples):
                continue
            future = samples[future_idx]
            if (
                max_future_lag_ms is not None
                and future.timestamp_ms - future_target_ms > max_future_lag_ms
            ):
                continue
            mid_move_bps = side_sign * ((future.mid - current.mid) / current.mid) * Decimal("10000")
            rows.append(OFIFillToxicityRow(
                fill_id=fill.fill_id,
                order_id=fill.order_id,
                side=fill.side,
                fill_timestamp_ms=fill.timestamp_ms,
                fill_price=fill.price,
                quantity=fill.quantity,
                is_maker=fill.is_maker,
                horizon=horizon,
                horizon_ms=horizon_ms,
                interval_start_ms=interval_start_ms,
                book_timestamp_ms=current.timestamp_ms,
                future_timestamp_ms=future.timestamp_ms,
                mid_at_fill=current.mid,
                future_mid=future.mid,
                raw_ofi=raw_ofi,
                normalized_ofi=normalized_ofi,
                side_aligned_ofi=side_aligned,
                side_normalized_mid_move_bps=mid_move_bps,
            ))
    return rows


def bucket_ofi_fill_toxicity(
    rows: Sequence[OFIFillToxicityRow],
    *,
    edges: Sequence[float] = DEFAULT_ALIGNED_OFI_BUCKET_EDGES,
) -> list[OFIFillToxicityBucket]:
    """Bucket conditional fill toxicity by side-aligned OFI."""
    out: list[OFIFillToxicityBucket] = []
    horizons = sorted({row.horizon for row in rows}, key=lambda h: _horizon_ms(rows, h))
    for horizon in horizons:
        horizon_rows = [row for row in rows if row.horizon == horizon]
        for side_label, side_rows in _side_groups(horizon_rows):
            for label, lo, hi in _bucket_ranges(edges):
                bucket_rows = [
                    row for row in side_rows
                    if _in_range(float(row.side_aligned_ofi), lo, hi)
                ]
                out.append(OFIFillToxicityBucket(
                    horizon=horizon,
                    side=side_label,
                    bucket=label,
                    n=len(bucket_rows),
                    avg_side_aligned_ofi=_mean_decimal(
                        row.side_aligned_ofi for row in bucket_rows
                    ),
                    avg_mid_move_bps=_mean_decimal(
                        row.side_normalized_mid_move_bps for row in bucket_rows
                    ),
                    median_mid_move_bps=_median_decimal(
                        row.side_normalized_mid_move_bps for row in bucket_rows
                    ),
                ))
    return out


def evaluate_ofi_gates(
    *,
    window_regressions: Sequence[OFIRegression],
    pooled_regressions: Sequence[OFIRegression],
    fill_buckets: Sequence[OFIFillToxicityBucket],
    primary_horizon: str = "1s",
    stable_sign_threshold: Decimal = Decimal("0.75"),
    t_stat_threshold: float = 2.0,
    effect_threshold_bps: float = 0.05,
    conditional_separation_threshold_bps: Decimal = Decimal("1.0"),
    min_conditional_bucket_count: int = 30,
) -> OFIGateResult:
    """Evaluate the Phase B OFI decision gates mechanically."""
    window_rows = [
        row for row in window_regressions
        if row.horizon == primary_horizon and row.signal == "normalized_ofi"
    ]
    signed_rows = [row for row in window_rows if row.beta is not None]
    positive = sum(1 for row in signed_rows if row.beta > 0)
    negative = sum(1 for row in signed_rows if row.beta < 0)
    stable = max(positive, negative)
    total = len(signed_rows)
    stable_share = Decimal(stable) / Decimal(total) if total else Decimal("0")
    stable_pass = total > 0 and stable_share >= stable_sign_threshold

    pooled = next(
        (
            row for row in pooled_regressions
            if row.horizon == primary_horizon and row.signal == "normalized_ofi"
        ),
        None,
    )
    pooled_abs_t = abs(pooled.t_stat) if pooled and pooled.t_stat is not None else None
    pooled_abs_effect = (
        abs(pooled.predicted_drift_1std_bps)
        if pooled and pooled.predicted_drift_1std_bps is not None else None
    )
    t_pass = pooled_abs_t is not None and pooled_abs_t >= t_stat_threshold
    effect_pass = (
        pooled_abs_effect is not None
        and pooled_abs_effect >= effect_threshold_bps
    )

    conditional_sep, min_bucket = _conditional_separation(
        fill_buckets, horizon="30s"
    )
    if conditional_sep is None or min_bucket < min_conditional_bucket_count:
        conditional_status = "inconclusive_power"
    elif conditional_sep >= conditional_separation_threshold_bps:
        conditional_status = "pass"
    else:
        conditional_status = "fail_signal"
    conditional_pass = conditional_status == "pass"
    unconditional_pass = stable_pass and t_pass and effect_pass
    if not unconditional_pass or conditional_status == "fail_signal":
        overall_verdict = "blocked"
    elif conditional_pass:
        overall_verdict = "supported"
    else:
        overall_verdict = "supported_with_conditional_power_limit"
    all_pass = overall_verdict != "blocked"
    numerical_gate_values = [stable_pass, t_pass, effect_pass]
    if conditional_status != "inconclusive_power":
        numerical_gate_values.append(conditional_pass)
    marginal = overall_verdict == "blocked" and any(numerical_gate_values) and _misses_are_marginal(
        stable_share=stable_share,
        stable_threshold=stable_sign_threshold,
        pooled_abs_t=pooled_abs_t,
        t_threshold=t_stat_threshold,
        pooled_abs_effect=pooled_abs_effect,
        effect_threshold=effect_threshold_bps,
        conditional_sep=conditional_sep,
        conditional_threshold=conditional_separation_threshold_bps,
        min_bucket=min_bucket,
        min_bucket_threshold=min_conditional_bucket_count,
        conditional_status=conditional_status,
    )
    return OFIGateResult(
        stable_sign_windows=stable,
        total_windows=total,
        stable_sign_share=stable_share,
        stable_sign_pass=stable_pass,
        pooled_abs_t_stat=pooled_abs_t,
        pooled_t_stat_pass=t_pass,
        pooled_abs_predicted_drift_1std_bps=pooled_abs_effect,
        pooled_effect_pass=effect_pass,
        conditional_separation_bps=conditional_sep,
        conditional_bucket_count_min=min_bucket,
        conditional_status=conditional_status,
        conditional_pass=conditional_pass,
        unconditional_pass=unconditional_pass,
        overall_verdict=overall_verdict,
        all_pass=all_pass,
        marginal_5s_fallback=marginal,
    )


def _ofi_between(
    samples: Sequence[BookSample],
    times: Sequence[int],
    start_ms: int,
    end_ms: int,
    *,
    end_inclusive: bool = True,
) -> tuple[Decimal, Decimal]:
    start_idx = bisect_right(times, start_ms) - 1
    # end_inclusive=True (the unconditional grid path) includes a sample stamped
    # exactly at end_ms. end_inclusive=False (the conditional fill path) excludes
    # it, so a book sample at the fill timestamp cannot leak into the OFI window.
    end_idx = (bisect_right(times, end_ms) if end_inclusive else bisect_left(times, end_ms)) - 1
    if start_idx < 0 or end_idx <= start_idx:
        return Decimal("0"), Decimal("0")

    raw = Decimal("0")
    depth_sum = Decimal("0")
    depth_count = 0
    previous = samples[start_idx]
    for idx in range(start_idx + 1, end_idx + 1):
        current = samples[idx]
        raw += _ofi_increment(previous, current)
        depth = current.best_bid_qty + current.best_ask_qty
        if depth > Decimal("0"):
            depth_sum += depth
            depth_count += 1
        previous = current

    avg_depth = depth_sum / Decimal(depth_count) if depth_count else Decimal("0")
    normalized = raw / avg_depth if avg_depth > Decimal("0") else Decimal("0")
    return raw, normalized


def _ofi_increment(prev: BookSample, cur: BookSample) -> Decimal:
    bid_part = Decimal("0")
    if cur.best_bid >= prev.best_bid:
        bid_part += cur.best_bid_qty
    if cur.best_bid <= prev.best_bid:
        bid_part -= prev.best_bid_qty

    ask_part = Decimal("0")
    if cur.best_ask <= prev.best_ask:
        ask_part -= cur.best_ask_qty
    if cur.best_ask >= prev.best_ask:
        ask_part += prev.best_ask_qty
    return bid_part + ask_part


def _valid_samples(book_samples: Sequence[BookSample]) -> list[BookSample]:
    return sorted(
        (
            sample for sample in book_samples
            if (
                sample.mid > 0
                and sample.best_bid_qty is not None
                and sample.best_ask_qty is not None
                and sample.best_bid_qty >= 0
                and sample.best_ask_qty >= 0
            )
        ),
        key=lambda sample: sample.timestamp_ms,
    )


def _conditional_separation(
    buckets: Sequence[OFIFillToxicityBucket],
    *,
    horizon: str,
) -> tuple[Decimal | None, int]:
    side_rows = [
        row for row in buckets
        if row.horizon == horizon and row.side == "all" and row.avg_mid_move_bps is not None
    ]
    adverse = [row for row in side_rows if row.avg_side_aligned_ofi is not None and row.avg_side_aligned_ofi < 0]
    favorable = [row for row in side_rows if row.avg_side_aligned_ofi is not None and row.avg_side_aligned_ofi >= 0]
    if not adverse or not favorable:
        return None, 0
    adverse_row = max(adverse, key=lambda row: row.n)
    favorable_row = max(favorable, key=lambda row: row.n)
    return (
        favorable_row.avg_mid_move_bps - adverse_row.avg_mid_move_bps,
        min(adverse_row.n, favorable_row.n),
    )


def _misses_are_marginal(
    *,
    stable_share: Decimal,
    stable_threshold: Decimal,
    pooled_abs_t: float | None,
    t_threshold: float,
    pooled_abs_effect: float | None,
    effect_threshold: float,
    conditional_sep: Decimal | None,
    conditional_threshold: Decimal,
    min_bucket: int,
    min_bucket_threshold: int,
    conditional_status: str,
) -> bool:
    checks = [
        stable_share >= stable_threshold * Decimal("0.75"),
        pooled_abs_t is not None and pooled_abs_t >= t_threshold * 0.75,
        pooled_abs_effect is not None and pooled_abs_effect >= effect_threshold * 0.75,
    ]
    if conditional_status != "inconclusive_power":
        checks.extend([
            conditional_sep is not None
            and conditional_sep >= conditional_threshold * Decimal("0.75"),
            min_bucket >= int(min_bucket_threshold * 0.75),
        ])
    return all(checks)


def _ceil_to_interval(value: int, interval: int) -> int:
    return ((value + interval - 1) // interval) * interval


def _side_groups(
    rows: Sequence[OFIFillToxicityRow],
) -> list[tuple[str, list[OFIFillToxicityRow]]]:
    return [
        ("all", list(rows)),
        ("buy", [row for row in rows if row.side == OrderSide.BUY]),
        ("sell", [row for row in rows if row.side == OrderSide.SELL]),
    ]


def _horizon_ms(rows: Sequence[OFIFillToxicityRow], horizon: str) -> int:
    for row in rows:
        if row.horizon == horizon:
            return row.horizon_ms
    return 0


def _side_sign(side: OrderSide) -> Decimal:
    return Decimal("1") if side == OrderSide.BUY else Decimal("-1")


def _bucket_ranges(edges: Sequence[float]):
    sorted_edges = sorted(edges)
    if not sorted_edges:
        yield "all", None, None
        return
    yield f"<{sorted_edges[0]}", None, sorted_edges[0]
    for lo, hi in zip(sorted_edges, sorted_edges[1:]):
        yield f"[{lo},{hi})", lo, hi
    yield f">={sorted_edges[-1]}", sorted_edges[-1], None


def _in_range(value: float, lo: float | None, hi: float | None) -> bool:
    if lo is not None and value < lo:
        return False
    if hi is not None and value >= hi:
        return False
    return True


def _mean_decimal(values: Iterable[Decimal]) -> Decimal | None:
    vals = list(values)
    if not vals:
        return None
    return sum(vals, Decimal("0")) / Decimal(len(vals))


def _median_decimal(values: Iterable[Decimal]) -> Decimal | None:
    vals = list(values)
    if not vals:
        return None
    return Decimal(str(statistics.median(vals)))
