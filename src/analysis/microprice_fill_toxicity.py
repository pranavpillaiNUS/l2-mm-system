"""
Conditional-on-fill microprice toxicity diagnostics.

Unconditional microprice regressions ask whether microprice predicts future
mid drift in general. A passive market maker needs a narrower object: after a
maker fill, did the mid move favorably or adversely, and did microprice skew at
the fill time distinguish those cases?
"""
import statistics
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from src.analysis.markout import DEFAULT_HORIZONS_MS
from src.execution.order import Fill, OrderSide
from src.replay.engine import BookSample


DEFAULT_ALIGNED_SKEW_BUCKET_EDGES_BPS = (-0.05, -0.01, 0.0, 0.01, 0.05)


@dataclass(frozen=True)
class MicropriceFillToxicityRow:
    fill_id: str
    order_id: str
    side: OrderSide
    fill_timestamp_ms: int
    fill_price: Decimal
    quantity: Decimal
    is_maker: bool
    horizon: str
    horizon_ms: int
    book_timestamp_ms: int
    future_timestamp_ms: int
    mid_at_fill: Decimal
    future_mid: Decimal
    microprice_deviation_bps: Decimal
    side_aligned_skew_bps: Decimal
    fill_edge_bps: Decimal
    side_normalized_mid_move: Decimal
    side_normalized_mid_move_bps: Decimal


@dataclass(frozen=True)
class MicropriceFillToxicityBucket:
    horizon: str
    side: str
    bucket: str
    n: int
    avg_side_aligned_skew_bps: Decimal | None
    avg_mid_move_bps: Decimal | None
    median_mid_move_bps: Decimal | None
    avg_fill_edge_bps: Decimal | None


def compute_microprice_fill_toxicity(
    fills: Iterable[Fill],
    book_samples: Sequence[BookSample],
    horizons_ms: dict[str, int] | None = None,
    *,
    maker_only: bool = True,
    max_staleness_ms: int | None = 1_000,
    max_future_lag_ms: int | None = 1_000,
) -> list[MicropriceFillToxicityRow]:
    """
    Compute side-normalized mid-to-mid movement after each fill.

    Positive side_normalized_mid_move_bps means the mid moved favorably after
    the fill: up after a buy, down after a sell. Positive side_aligned_skew_bps
    means microprice was tilted in favor of the filled side: microprice above
    mid for buys, below mid for sells.
    """
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
    rows: list[MicropriceFillToxicityRow] = []

    for fill in fills:
        if maker_only and not fill.is_maker:
            continue

        current_idx = bisect_right(sample_times, fill.timestamp_ms) - 1
        if current_idx < 0:
            continue

        current = samples[current_idx]
        if (
            max_staleness_ms is not None
            and fill.timestamp_ms - current.timestamp_ms > max_staleness_ms
        ):
            continue
        if current.microprice is None:
            continue

        side_sign = _side_sign(fill.side)
        signal_bps = ((current.microprice - current.mid) / current.mid) * Decimal("10000")
        aligned_skew_bps = side_sign * signal_bps
        fill_edge = side_sign * (current.mid - fill.price)
        fill_edge_bps = (fill_edge / current.mid) * Decimal("10000")

        for horizon, horizon_ms in horizons.items():
            future_target_ms = fill.timestamp_ms + horizon_ms
            future_idx = bisect_left(sample_times, future_target_ms)
            if future_idx >= len(samples):
                continue

            future = samples[future_idx]
            if (
                max_future_lag_ms is not None
                and future.timestamp_ms - future_target_ms > max_future_lag_ms
            ):
                continue

            mid_move = side_sign * (future.mid - current.mid)
            mid_move_bps = (mid_move / current.mid) * Decimal("10000")
            rows.append(MicropriceFillToxicityRow(
                fill_id=fill.fill_id,
                order_id=fill.order_id,
                side=fill.side,
                fill_timestamp_ms=fill.timestamp_ms,
                fill_price=fill.price,
                quantity=fill.quantity,
                is_maker=fill.is_maker,
                horizon=horizon,
                horizon_ms=horizon_ms,
                book_timestamp_ms=current.timestamp_ms,
                future_timestamp_ms=future.timestamp_ms,
                mid_at_fill=current.mid,
                future_mid=future.mid,
                microprice_deviation_bps=signal_bps,
                side_aligned_skew_bps=aligned_skew_bps,
                fill_edge_bps=fill_edge_bps,
                side_normalized_mid_move=mid_move,
                side_normalized_mid_move_bps=mid_move_bps,
            ))

    return rows


def bucket_microprice_fill_toxicity(
    rows: Sequence[MicropriceFillToxicityRow],
    *,
    edges_bps: Sequence[float] = DEFAULT_ALIGNED_SKEW_BUCKET_EDGES_BPS,
) -> list[MicropriceFillToxicityBucket]:
    """Bucket conditional fill toxicity by side-aligned microprice skew."""
    output: list[MicropriceFillToxicityBucket] = []
    horizons = sorted({row.horizon for row in rows}, key=lambda h: _horizon_ms(rows, h))

    for horizon in horizons:
        horizon_rows = [row for row in rows if row.horizon == horizon]
        for side_label, side_rows in _side_groups(horizon_rows):
            for bucket_label, lo, hi in _bucket_ranges(edges_bps):
                bucket_rows = [
                    row for row in side_rows
                    if _in_range(float(row.side_aligned_skew_bps), lo, hi)
                ]
                output.append(MicropriceFillToxicityBucket(
                    horizon=horizon,
                    side=side_label,
                    bucket=bucket_label,
                    n=len(bucket_rows),
                    avg_side_aligned_skew_bps=_mean_decimal(
                        row.side_aligned_skew_bps for row in bucket_rows
                    ),
                    avg_mid_move_bps=_mean_decimal(
                        row.side_normalized_mid_move_bps for row in bucket_rows
                    ),
                    median_mid_move_bps=_median_decimal(
                        row.side_normalized_mid_move_bps for row in bucket_rows
                    ),
                    avg_fill_edge_bps=_mean_decimal(
                        row.fill_edge_bps for row in bucket_rows
                    ),
                ))

    return output


def _side_groups(
    rows: Sequence[MicropriceFillToxicityRow],
) -> list[tuple[str, list[MicropriceFillToxicityRow]]]:
    return [
        ("all", list(rows)),
        ("buy", [row for row in rows if row.side == OrderSide.BUY]),
        ("sell", [row for row in rows if row.side == OrderSide.SELL]),
    ]


def _horizon_ms(rows: Sequence[MicropriceFillToxicityRow], horizon: str) -> int:
    for row in rows:
        if row.horizon == horizon:
            return row.horizon_ms
    return 0


def _bucket_ranges(edges_bps: Sequence[float]):
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


def _side_sign(side: OrderSide) -> Decimal:
    return Decimal("1") if side == OrderSide.BUY else Decimal("-1")


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
