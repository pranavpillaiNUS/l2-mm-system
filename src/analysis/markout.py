"""
Fill markout analysis.

For each fill, find the first recorded book mid at or after each requested
horizon and compute a side-normalized price move:
  BUY:  future_mid - fill_price
  SELL: fill_price - future_mid

Positive values are favorable. Negative values indicate adverse selection.
"""
import statistics
from bisect import bisect_left
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence

from src.execution.order import Fill, OrderSide
from src.replay.engine import BookSample


DEFAULT_HORIZONS_MS: Dict[str, int] = {
    "1s": 1_000,
    "5s": 5_000,
    "30s": 30_000,
    "1m": 60_000,
    "5m": 300_000,
}


@dataclass
class Markout:
    fill_id: str
    order_id: str
    side: OrderSide
    fill_timestamp_ms: int
    fill_price: Decimal
    quantity: Decimal
    horizon: str
    horizon_ms: int
    sample_timestamp_ms: int
    future_mid: Decimal
    markout: Decimal
    markout_bps: Decimal


def compute_markouts(
    fills: Iterable[Fill],
    book_samples: Sequence[BookSample],
    horizons_ms: Dict[str, int] | None = None,
) -> List[Markout]:
    """Compute available side-normalized markouts for all fills."""
    horizons = horizons_ms or DEFAULT_HORIZONS_MS
    samples = sorted(book_samples, key=lambda sample: sample.timestamp_ms)
    sample_times = [sample.timestamp_ms for sample in samples]

    results: List[Markout] = []
    for fill in fills:
        side_sign = Decimal("1") if fill.side == OrderSide.BUY else Decimal("-1")
        for label, horizon_ms in horizons.items():
            target_time = fill.timestamp_ms + horizon_ms
            idx = bisect_left(sample_times, target_time)
            if idx >= len(samples):
                continue

            sample = samples[idx]
            markout = side_sign * (sample.mid - fill.price)
            markout_bps = (markout / fill.price) * Decimal("10000")
            results.append(Markout(
                fill_id=fill.fill_id,
                order_id=fill.order_id,
                side=fill.side,
                fill_timestamp_ms=fill.timestamp_ms,
                fill_price=fill.price,
                quantity=fill.quantity,
                horizon=label,
                horizon_ms=horizon_ms,
                sample_timestamp_ms=sample.timestamp_ms,
                future_mid=sample.mid,
                markout=markout,
                markout_bps=markout_bps,
            ))

    return results


def summarize_markouts(markouts: Iterable[Markout]) -> Dict[str, Dict]:
    """Aggregate markouts by horizon, including p25/p50/p75 when count >= 4."""
    grouped: Dict[str, List[Markout]] = {}
    for markout in markouts:
        grouped.setdefault(markout.horizon, []).append(markout)

    summary: Dict[str, Dict] = {}
    for horizon, rows in grouped.items():
        count = len(rows)
        total = sum((row.markout for row in rows), Decimal("0"))
        total_bps = sum((row.markout_bps for row in rows), Decimal("0"))

        row_summary: Dict = {
            "count": count,
            "avg_markout": total / count,
            "avg_markout_bps": total_bps / count,
        }

        if count >= 4:
            bps_values = [float(row.markout_bps) for row in rows]
            q25, q50, q75 = statistics.quantiles(bps_values, n=4)
            row_summary["p25_markout_bps"] = Decimal(str(round(q25, 4)))
            row_summary["p50_markout_bps"] = Decimal(str(round(q50, 4)))
            row_summary["p75_markout_bps"] = Decimal(str(round(q75, 4)))
        else:
            row_summary["p25_markout_bps"] = None
            row_summary["p50_markout_bps"] = None
            row_summary["p75_markout_bps"] = None

        summary[horizon] = row_summary

    return summary
