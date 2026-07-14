"""
Fill-rate and quote-quality analysis.

Decomposes P&L into the factoring:
  session_pnl approx (orders submitted) x P(fill) x E[pnl_per_fill | filled]

The aggregate "we lost X dollars" answer hides which factor matters. This
module reconstructs per-order context (distance from mid at submission, quote
age, volatility regime) from the replay event log, then bins fill rate and
markout-given-fill by context.

The lens this is built for: at wide spreads, do the fills we get pay enough
spread capture to offset adverse selection, or are the fills we do get
disproportionately informed (Glosten-Milgrom toxicity)?

Joins by order_id across:
  - result.events     order lifecycle (placed -> queued -> filled/cancelled)
  - result.fills      one record per fill
  - result.book_samples  mid at any timestamp
  - markouts          per-fill markout at each horizon
"""
import math
import statistics
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.analysis.markout import Markout
from src.execution.order import Fill, OrderEvent, OrderSide
from src.replay.engine import BookSample, ReplayResult


@dataclass
class OrderContext:
    """One row per order, with context at submission and final outcome."""
    order_id: str
    side: OrderSide
    price: Decimal
    quantity: Decimal
    placed_time_ms: int

    # Lifecycle
    queued_time_ms: Optional[int]
    terminal_time_ms: Optional[int]
    final_status: str   # filled/cancelled/invalidated/expired/open classification

    # Context at submission
    mid_at_placed: Optional[Decimal]
    distance_from_mid_bps: Optional[Decimal]
    volatility_bps: Optional[Decimal]

    # Resting time in book (queued -> terminal). None if never queued.
    quote_age_ms: Optional[int]

    # Linked fill (for filled / partial_then_cancelled orders)
    fill_id: Optional[str]
    fill_markout_bps: Optional[Decimal]

    # Optional: tag for pooling across sessions in sweeps. None for one-shot use.
    session_id: Optional[str] = None

    @property
    def filled(self) -> bool:
        return self.final_status in (
            "filled",
            "partial_then_cancelled",
            "partial_then_invalidated",
            "partial_then_expired",
        )


@dataclass
class FillRateBin:
    label: str
    n_orders: int
    n_filled: int
    fill_rate: float
    avg_markout_bps_given_filled: Optional[Decimal]
    median_markout_bps_given_filled: Optional[Decimal]


def compute_order_contexts(
    result: ReplayResult,
    markouts: Sequence[Markout],
    vol_window_ms: int = 60_000,
    markout_horizon: str = "30s",
    session_id: Optional[str] = None,
) -> List[OrderContext]:
    """
    Reconstruct one OrderContext per order from the replay event log.

    Walks result.events to build per-order lifecycle, looks up mid at
    placement from book_samples, computes rolling vol over the window
    ending at placement, and joins fill markouts at the chosen horizon.
    """
    samples = sorted(result.book_samples, key=lambda s: s.timestamp_ms)
    sample_times = [s.timestamp_ms for s in samples]
    sample_mids_float = [float(s.mid) for s in samples]

    # First fill per order_id (used for terminal_time and markout join).
    first_fill: Dict[str, Fill] = {}
    for fill in result.fills:
        if fill.order_id not in first_fill:
            first_fill[fill.order_id] = fill

    # Markout per fill_id at the chosen horizon.
    markout_by_fill: Dict[str, Decimal] = {
        m.fill_id: m.markout_bps for m in markouts if m.horizon == markout_horizon
    }

    # Group events by order_id, preserving order.
    by_order: Dict[str, List[OrderEvent]] = {}
    for ev in result.events:
        by_order.setdefault(ev.order_id, []).append(ev)

    contexts: List[OrderContext] = []
    for order_id, events in by_order.items():
        placed = next((e for e in events if e.event_type == "placed"), None)
        if placed is None:
            continue
        # Only limit orders make sense for fill-rate analysis. Market orders
        # don't really "rest" - they fill immediately or fail.
        if placed.detail.get("type") != "limit":
            continue

        side = OrderSide.BUY if placed.detail["side"] == "buy" else OrderSide.SELL
        price = Decimal(placed.detail["price"])
        quantity = Decimal(placed.detail["qty"])
        placed_time_ms = placed.timestamp_ms

        queued = next((e for e in events if e.event_type == "queued"), None)
        cancelled = next((e for e in events if e.event_type == "cancelled"), None)
        invalidated = next((e for e in events if e.event_type == "invalidated"), None)
        expired = next((e for e in events if e.event_type == "expired"), None)
        filled_evt = next((e for e in events if e.event_type == "filled"), None)

        queued_time_ms = queued.timestamp_ms if queued else None

        if filled_evt is not None:
            final_status = "filled"
            terminal_time_ms = filled_evt.timestamp_ms
        elif cancelled is not None:
            if cancelled.detail.get("reason") == "post_only_would_cross":
                final_status = "post_only_rejected"
            elif order_id in first_fill:
                final_status = "partial_then_cancelled"
            else:
                final_status = "cancelled_unfilled"
            terminal_time_ms = cancelled.timestamp_ms
        elif invalidated is not None:
            final_status = (
                "partial_then_invalidated"
                if order_id in first_fill
                else "gap_invalidated"
            )
            terminal_time_ms = invalidated.timestamp_ms
        elif expired is not None:
            final_status = (
                "partial_then_expired"
                if order_id in first_fill
                else "replay_expired"
            )
            terminal_time_ms = expired.timestamp_ms
        else:
            final_status = "open"
            terminal_time_ms = None

        # Mid at placement (latest sample at or before placed_time_ms).
        mid_at_placed = _mid_at(samples, sample_times, placed_time_ms)
        distance_from_mid_bps: Optional[Decimal] = None
        if mid_at_placed is not None and mid_at_placed > 0:
            # Side-normalize so distance is "how far from mid in the
            # passive direction": positive = away from mid (good), negative
            # = crossed/aggressive.
            if side == OrderSide.BUY:
                signed = mid_at_placed - price       # bid below mid -> positive
            else:
                signed = price - mid_at_placed       # ask above mid -> positive
            distance_from_mid_bps = (signed / mid_at_placed) * Decimal("10000")

        volatility_bps = _rolling_vol_bps(
            sample_times, sample_mids_float, placed_time_ms, vol_window_ms
        )

        # Quote age: queued -> terminal (or session end). Skip if never queued.
        if queued_time_ms is not None and terminal_time_ms is not None:
            quote_age_ms = max(0, terminal_time_ms - queued_time_ms)
        else:
            quote_age_ms = None

        # Linked fill markout.
        fill = first_fill.get(order_id)
        fill_id = fill.fill_id if fill else None
        fill_markout_bps = markout_by_fill.get(fill_id) if fill_id else None

        contexts.append(OrderContext(
            order_id=order_id,
            side=side,
            price=price,
            quantity=quantity,
            placed_time_ms=placed_time_ms,
            queued_time_ms=queued_time_ms,
            terminal_time_ms=terminal_time_ms,
            final_status=final_status,
            mid_at_placed=mid_at_placed,
            distance_from_mid_bps=distance_from_mid_bps,
            volatility_bps=volatility_bps,
            quote_age_ms=quote_age_ms,
            fill_id=fill_id,
            fill_markout_bps=fill_markout_bps,
            session_id=session_id,
        ))

    return contexts


def fill_rate_by_distance_bps(
    contexts: Iterable[OrderContext],
    edges_bps: Sequence[float],
) -> List[FillRateBin]:
    """Bin orders by distance-from-mid at submission (in bps)."""
    return _bin_by(
        contexts,
        key=lambda c: float(c.distance_from_mid_bps) if c.distance_from_mid_bps is not None else None,
        edges=edges_bps,
        unit="bps",
    )


def fill_rate_by_quote_age_ms(
    contexts: Iterable[OrderContext],
    edges_ms: Sequence[int],
) -> List[FillRateBin]:
    """Bin orders by terminal quote age (queued -> filled/cancelled)."""
    return _bin_by(
        contexts,
        key=lambda c: float(c.quote_age_ms) if c.quote_age_ms is not None else None,
        edges=edges_ms,
        unit="ms",
    )


def fill_rate_by_volatility_bps(
    contexts: Iterable[OrderContext],
    edges_bps: Sequence[float],
) -> List[FillRateBin]:
    """Bin orders by rolling-window mid volatility at submission (in bps)."""
    return _bin_by(
        contexts,
        key=lambda c: float(c.volatility_bps) if c.volatility_bps is not None else None,
        edges=edges_bps,
        unit="bps",
    )


def summarize_pooled_contexts(
    contexts: Sequence[OrderContext],
    distance_edges_bps: Sequence[float] = (0, 0.5, 1, 2, 3, 5, 8, 12),
    age_edges_ms: Sequence[int] = (0, 100, 500, 1_000, 5_000, 30_000),
    vol_edges_bps: Sequence[float] = (0, 1, 2, 5, 10),
) -> Dict[str, object]:
    """
    Run the three binnings on a pre-computed list of contexts.

    Use this to pool contexts across multiple sessions: collect each session's
    contexts via compute_order_contexts(..., session_id=...), concatenate them,
    pass the combined list here.
    """
    overall_filled = [c for c in contexts if c.filled]
    overall_markouts = [
        c.fill_markout_bps for c in overall_filled
        if c.fill_markout_bps is not None
    ]
    overall = FillRateBin(
        label="overall",
        n_orders=len(contexts),
        n_filled=len(overall_filled),
        fill_rate=(len(overall_filled) / len(contexts)) if contexts else 0.0,
        avg_markout_bps_given_filled=_mean_or_none(overall_markouts),
        median_markout_bps_given_filled=_median_or_none(overall_markouts),
    )
    return {
        "overall": overall,
        "by_distance_bps": fill_rate_by_distance_bps(contexts, distance_edges_bps),
        "by_quote_age_ms": fill_rate_by_quote_age_ms(contexts, age_edges_ms),
        "by_volatility_bps": fill_rate_by_volatility_bps(contexts, vol_edges_bps),
    }


def summarize_fill_rate(
    result: ReplayResult,
    markouts: Sequence[Markout],
    distance_edges_bps: Sequence[float] = (0, 0.5, 1, 2, 3, 5, 8, 12),
    age_edges_ms: Sequence[int] = (0, 100, 500, 1_000, 5_000, 30_000),
    vol_edges_bps: Sequence[float] = (0, 1, 2, 5, 10),
    vol_window_ms: int = 60_000,
    markout_horizon: str = "30s",
) -> Dict[str, object]:
    """
    Top-level entry point. Returns the full breakdown plus an overall row.

    Structure:
      {
        "overall": FillRateBin,
        "by_distance_bps": [FillRateBin, ...],
        "by_quote_age_ms": [FillRateBin, ...],
        "by_volatility_bps": [FillRateBin, ...],
        "contexts": [OrderContext, ...],     # included for downstream use
      }
    """
    contexts = compute_order_contexts(
        result, markouts,
        vol_window_ms=vol_window_ms,
        markout_horizon=markout_horizon,
    )
    summary = summarize_pooled_contexts(
        contexts,
        distance_edges_bps=distance_edges_bps,
        age_edges_ms=age_edges_ms,
        vol_edges_bps=vol_edges_bps,
    )
    summary["contexts"] = contexts
    return summary


def format_fill_rate_summary(summary: Dict[str, object]) -> str:
    """Pretty-print the summary for run_replay-style output."""
    lines = ["Fill-rate breakdown"]

    overall: FillRateBin = summary["overall"]  # type: ignore[assignment]
    lines.append(
        f"  overall: {overall.n_filled}/{overall.n_orders} filled "
        f"({overall.fill_rate * 100:.2f}%)  "
        f"markout|filled = {_fmt_bps(overall.avg_markout_bps_given_filled)} "
        f"(median {_fmt_bps(overall.median_markout_bps_given_filled)})"
    )

    for label, key in [
        ("by distance from mid (bps)", "by_distance_bps"),
        ("by quote age (ms)", "by_quote_age_ms"),
        ("by volatility (bps, rolling)", "by_volatility_bps"),
    ]:
        rows: List[FillRateBin] = summary[key]  # type: ignore[assignment]
        lines.append(f"  {label}:")
        for row in rows:
            lines.append(
                f"    {row.label:>14}  n={row.n_orders:>5}  "
                f"filled={row.n_filled:>5}  rate={row.fill_rate * 100:>6.2f}%  "
                f"markout|filled={_fmt_bps(row.avg_markout_bps_given_filled)}"
            )

    return "\n".join(lines)


# --- internal helpers ---


def _mid_at(
    samples: Sequence[BookSample],
    sample_times: Sequence[int],
    target_ms: int,
) -> Optional[Decimal]:
    """Latest sample mid at or before target_ms. None if no prior sample."""
    if not samples:
        return None
    idx = bisect_right(sample_times, target_ms) - 1
    if idx < 0:
        return None
    return samples[idx].mid


def _rolling_vol_bps(
    sample_times: Sequence[int],
    sample_mids_float: Sequence[float],
    target_ms: int,
    window_ms: int,
) -> Optional[Decimal]:
    """
    Std of mid (as fraction of mean, in bps) over [target - window, target].

    Returns None if fewer than 2 samples in the window or mean is zero.
    Float arithmetic is fine here - vol is a diagnostic, not for accounting.
    """
    if not sample_times:
        return None
    lo_idx = bisect_left(sample_times, target_ms - window_ms)
    hi_idx = bisect_right(sample_times, target_ms)
    if hi_idx - lo_idx < 2:
        return None
    window = sample_mids_float[lo_idx:hi_idx]
    mean = sum(window) / len(window)
    if mean == 0:
        return None
    variance = sum((m - mean) ** 2 for m in window) / (len(window) - 1)
    std = math.sqrt(variance)
    return Decimal(str(round((std / mean) * 10_000, 6)))


def _bin_by(
    contexts: Iterable[OrderContext],
    key,
    edges: Sequence[float],
    unit: str,
) -> List[FillRateBin]:
    """
    Bucket contexts by a numeric key into half-open [edges[i], edges[i+1])
    intervals plus a final [edges[-1], inf) bucket. Skips contexts whose
    key value is None.
    """
    buckets: List[List[OrderContext]] = [[] for _ in range(len(edges))]
    for ctx in contexts:
        value = key(ctx)
        if value is None:
            continue
        idx = bisect_right(edges, value) - 1
        if idx < 0:
            continue   # below smallest edge; ignore
        buckets[idx].append(ctx)

    rows: List[FillRateBin] = []
    for i, bucket in enumerate(buckets):
        lo = edges[i]
        hi = edges[i + 1] if i + 1 < len(edges) else None
        label = f"[{_fmt_edge(lo)},{_fmt_edge(hi)}){unit}" if hi is not None \
            else f"[{_fmt_edge(lo)},inf){unit}"

        n_orders = len(bucket)
        filled = [c for c in bucket if c.filled]
        n_filled = len(filled)
        rate = (n_filled / n_orders) if n_orders > 0 else 0.0
        markout_vals = [
            c.fill_markout_bps for c in filled if c.fill_markout_bps is not None
        ]
        rows.append(FillRateBin(
            label=label,
            n_orders=n_orders,
            n_filled=n_filled,
            fill_rate=rate,
            avg_markout_bps_given_filled=_mean_or_none(markout_vals),
            median_markout_bps_given_filled=_median_or_none(markout_vals),
        ))
    return rows


def _mean_or_none(values: Sequence[Decimal]) -> Optional[Decimal]:
    if not values:
        return None
    total = sum(values, Decimal("0"))
    return total / Decimal(len(values))


def _median_or_none(values: Sequence[Decimal]) -> Optional[Decimal]:
    if not values:
        return None
    sorted_vals = sorted(float(v) for v in values)
    median = statistics.median(sorted_vals)
    return Decimal(str(round(median, 6)))


def _fmt_edge(edge) -> str:
    if edge is None:
        return "inf"
    if isinstance(edge, float) and edge.is_integer():
        return str(int(edge))
    return str(edge)


def _fmt_bps(val: Optional[Decimal]) -> str:
    if val is None:
        return "  n/a   "
    return f"{float(val):+.2f} bps"
