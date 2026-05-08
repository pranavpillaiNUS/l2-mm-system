"""
Inventory hold-time and markout reconciliation diagnostics.

The key idea is to separate two things that are easy to mix up:
  - spread capture at the fill timestamps
  - inventory PnL from the mid moving while we hold the position

FIFO matching gives us the actual hold time for each filled quantity. That
lets us test whether a fixed 30s markout is too long for an active MM strategy.
"""
import math
import statistics
from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence

from src.analysis.markout import Markout
from src.analysis.pnl import PnLDecomposition
from src.execution.order import Fill, OrderEvent, OrderSide
from src.replay.engine import BookSample, ReplayResult


VOL_BUCKET_EDGES_BPS = (0, 1, 2, 5, 10)


@dataclass
class OpenInventoryLot:
    fill_id: str
    order_id: str
    side: OrderSide
    open_time_ms: int
    open_price: Decimal
    quantity: Decimal
    open_mid: Optional[Decimal]


@dataclass
class MatchedLot:
    open_fill_id: str
    close_fill_id: str
    open_order_id: str
    close_order_id: str
    open_side: OrderSide
    close_side: OrderSide
    open_time_ms: int
    close_time_ms: int
    hold_time_ms: int
    open_price: Decimal
    close_price: Decimal
    quantity: Decimal
    open_mid: Optional[Decimal]
    close_mid: Optional[Decimal]
    realized_pnl: Decimal
    open_spread_capture: Optional[Decimal]
    close_spread_capture: Optional[Decimal]
    inventory_pnl: Optional[Decimal]


@dataclass
class HoldTimeSummary:
    matched_lots: List[MatchedLot]
    open_lots: List[OpenInventoryLot]
    total_matched_qty: Decimal
    realized_pnl: Decimal
    matched_spread_capture: Optional[Decimal]
    matched_inventory_pnl: Optional[Decimal]
    residual_inventory: Decimal
    avg_hold_time_ms: Optional[Decimal]
    p25_hold_time_ms: Optional[int]
    p50_hold_time_ms: Optional[int]
    p75_hold_time_ms: Optional[int]
    p90_hold_time_ms: Optional[int]
    max_hold_time_ms: Optional[int]


@dataclass
class HorizonReconciliation:
    horizon: str
    horizon_ms: int
    covered_qty: Decimal
    proxy_inventory_pnl: Decimal
    proxy_net_pnl: Decimal
    net_error: Decimal
    adverse_selection_cost: Decimal


@dataclass
class ReconciliationSummary:
    actual_net_from_components: Decimal
    reported_net_pnl: Decimal
    component_error: Decimal
    actual_inventory_pnl: Decimal
    matched_inventory_pnl: Optional[Decimal]
    matched_realized_pnl: Decimal
    median_hold_time_ms: Optional[int]
    closest_horizon_to_median: Optional[str]
    best_reconciling_horizon: Optional[str]
    horizons: List[HorizonReconciliation]


@dataclass
class PreFillDrift:
    fill_id: str
    order_id: str
    side: OrderSide
    price: Decimal
    quantity: Decimal
    placed_time_ms: Optional[int]
    fill_time_ms: int
    time_from_place_to_fill_ms: Optional[int]
    mid_at_placed: Optional[Decimal]
    mid_at_fill: Optional[Decimal]
    quoted_distance: Optional[Decimal]
    quoted_distance_bps: Optional[Decimal]
    fill_edge: Optional[Decimal]
    fill_edge_bps: Optional[Decimal]
    pre_fill_mid_move: Optional[Decimal]
    pre_fill_mid_move_bps: Optional[Decimal]
    book_spread_at_fill: Optional[Decimal]
    book_spread_bps_at_fill: Optional[Decimal]
    volatility_bps_at_fill: Optional[Decimal]
    quote_position_at_fill: str


@dataclass
class QueueOrderDiagnostic:
    order_id: str
    side: Optional[str]
    price: Optional[Decimal]
    queued_time_ms: Optional[int]
    initial_queue_ahead: Optional[Decimal]
    total_trade_queue_drained: Decimal
    total_cancel_queue_drained: Decimal
    first_fill_time_ms: Optional[int]
    first_queue_ahead_before_trade: Optional[Decimal]
    first_queue_ahead_before_fill: Optional[Decimal]
    filled_quantity: Decimal
    final_status: str


@dataclass
class BookSpreadBucket:
    label: str
    n_fills: int
    avg_spread: Optional[Decimal]
    median_spread: Optional[Decimal]
    avg_spread_bps: Optional[Decimal]
    inside_spread: int
    at_best: int
    behind_best: int
    crossed: int
    unknown: int


def compute_hold_time_summary(
    fills: Sequence[Fill],
    book_samples: Sequence[BookSample] | None = None,
) -> HoldTimeSummary:
    """FIFO-match fills and return hold-time and realized-PnL diagnostics."""
    samples = sorted(book_samples or [], key=lambda s: s.timestamp_ms)
    sample_times = [s.timestamp_ms for s in samples]

    longs: deque[OpenInventoryLot] = deque()
    shorts: deque[OpenInventoryLot] = deque()
    matched: List[MatchedLot] = []

    indexed_fills = list(enumerate(fills))
    indexed_fills.sort(key=lambda item: (item[1].timestamp_ms, item[0]))

    for _, fill in indexed_fills:
        remaining = fill.quantity
        fill_mid = _mid_at(samples, sample_times, fill.timestamp_ms)

        if fill.side == OrderSide.BUY:
            remaining = _close_lots(
                open_lots=shorts,
                fill=fill,
                fill_mid=fill_mid,
                remaining=remaining,
                matched=matched,
            )
            if remaining > Decimal("0"):
                longs.append(_open_lot(fill, remaining, fill_mid))
        else:
            remaining = _close_lots(
                open_lots=longs,
                fill=fill,
                fill_mid=fill_mid,
                remaining=remaining,
                matched=matched,
            )
            if remaining > Decimal("0"):
                shorts.append(_open_lot(fill, remaining, fill_mid))

    open_lots = list(longs) + list(shorts)
    total_matched_qty = sum((lot.quantity for lot in matched), Decimal("0"))
    realized_pnl = sum((lot.realized_pnl for lot in matched), Decimal("0"))
    residual_inventory = (
        sum((lot.quantity for lot in longs), Decimal("0"))
        - sum((lot.quantity for lot in shorts), Decimal("0"))
    )

    spread_values = [
        lot.open_spread_capture + lot.close_spread_capture
        for lot in matched
        if lot.open_spread_capture is not None and lot.close_spread_capture is not None
    ]
    inventory_values = [
        lot.inventory_pnl for lot in matched if lot.inventory_pnl is not None
    ]

    avg_hold_time_ms = _weighted_average_hold_time(matched)

    return HoldTimeSummary(
        matched_lots=matched,
        open_lots=open_lots,
        total_matched_qty=total_matched_qty,
        realized_pnl=realized_pnl,
        matched_spread_capture=(
            sum(spread_values, Decimal("0")) if len(spread_values) == len(matched) else None
        ),
        matched_inventory_pnl=(
            sum(inventory_values, Decimal("0")) if len(inventory_values) == len(matched) else None
        ),
        residual_inventory=residual_inventory,
        avg_hold_time_ms=avg_hold_time_ms,
        p25_hold_time_ms=_weighted_percentile_ms(matched, Decimal("0.25")),
        p50_hold_time_ms=_weighted_percentile_ms(matched, Decimal("0.50")),
        p75_hold_time_ms=_weighted_percentile_ms(matched, Decimal("0.75")),
        p90_hold_time_ms=_weighted_percentile_ms(matched, Decimal("0.90")),
        max_hold_time_ms=max((lot.hold_time_ms for lot in matched), default=None),
    )


def compute_reconciliation_summary(
    hold_summary: HoldTimeSummary,
    markouts: Sequence[Markout],
    decomp: PnLDecomposition,
    horizons_ms: Dict[str, int],
) -> ReconciliationSummary:
    """
    Compare actual accounting PnL to markout proxies at several horizons.

    Markout rows are from fill price to future mid. For inventory PnL we need
    fill-time mid to future mid, so we subtract the fill-time spread capture
    from each opening fill markout before comparing to realized inventory PnL.
    """
    actual_net = decomp.spread_capture + decomp.inventory_pnl - decomp.total_fees
    component_error = actual_net - decomp.net_pnl
    markout_by_key = {
        (m.fill_id, m.horizon): m for m in markouts
    }

    horizon_rows: List[HorizonReconciliation] = []
    for horizon, horizon_ms in horizons_ms.items():
        proxy_inventory_pnl = Decimal("0")
        covered_qty = Decimal("0")

        for lot in hold_summary.matched_lots:
            markout = markout_by_key.get((lot.open_fill_id, horizon))
            if markout is None or lot.open_spread_capture is None:
                continue

            open_spread_per_unit = lot.open_spread_capture / lot.quantity
            proxy_inventory_per_unit = markout.markout - open_spread_per_unit
            proxy_inventory_pnl += proxy_inventory_per_unit * lot.quantity
            covered_qty += lot.quantity

        proxy_net = decomp.spread_capture + proxy_inventory_pnl - decomp.total_fees
        adverse_selection_cost = -sum(
            (m.markout * m.quantity for m in markouts if m.horizon == horizon),
            Decimal("0"),
        )
        horizon_rows.append(HorizonReconciliation(
            horizon=horizon,
            horizon_ms=horizon_ms,
            covered_qty=covered_qty,
            proxy_inventory_pnl=proxy_inventory_pnl,
            proxy_net_pnl=proxy_net,
            net_error=proxy_net - decomp.net_pnl,
            adverse_selection_cost=adverse_selection_cost,
        ))

    median_hold = hold_summary.p50_hold_time_ms
    closest = None
    if median_hold is not None and horizons_ms:
        closest = min(
            horizons_ms,
            key=lambda h: abs(horizons_ms[h] - median_hold),
        )

    best = None
    if horizon_rows:
        best = min(horizon_rows, key=lambda row: abs(row.net_error)).horizon

    return ReconciliationSummary(
        actual_net_from_components=actual_net,
        reported_net_pnl=decomp.net_pnl,
        component_error=component_error,
        actual_inventory_pnl=decomp.inventory_pnl,
        matched_inventory_pnl=hold_summary.matched_inventory_pnl,
        matched_realized_pnl=hold_summary.realized_pnl,
        median_hold_time_ms=median_hold,
        closest_horizon_to_median=closest,
        best_reconciling_horizon=best,
        horizons=horizon_rows,
    )


def compute_pre_fill_drifts(
    result: ReplayResult,
    vol_window_ms: int = 60_000,
) -> List[PreFillDrift]:
    """Measure how much the mid moved between order placement and fill."""
    samples = sorted(result.book_samples, key=lambda s: s.timestamp_ms)
    sample_times = [s.timestamp_ms for s in samples]
    sample_mids_float = [float(s.mid) for s in samples]
    placed_by_order = {
        ev.order_id: ev for ev in result.events if ev.event_type == "placed"
    }

    rows: List[PreFillDrift] = []
    for fill in result.fills:
        placed = placed_by_order.get(fill.order_id)
        placed_time = placed.timestamp_ms if placed else None
        placed_sample = (
            _sample_at_or_before(samples, sample_times, placed_time)
            if placed_time is not None else None
        )
        fill_sample = _sample_at_or_before(samples, sample_times, fill.timestamp_ms)
        mid_at_placed = placed_sample.mid if placed_sample else None
        mid_at_fill = fill_sample.mid if fill_sample else None

        sign = _side_sign(fill.side)
        quoted_distance = None
        quoted_distance_bps = None
        fill_edge = None
        fill_edge_bps = None
        pre_fill_mid_move = None
        pre_fill_mid_move_bps = None

        if mid_at_placed is not None:
            quoted_distance = sign * (mid_at_placed - fill.price)
            quoted_distance_bps = _bps(quoted_distance, mid_at_placed)

        if mid_at_fill is not None:
            fill_edge = sign * (mid_at_fill - fill.price)
            fill_edge_bps = _bps(fill_edge, mid_at_fill)

        if mid_at_placed is not None and mid_at_fill is not None:
            pre_fill_mid_move = sign * (mid_at_fill - mid_at_placed)
            pre_fill_mid_move_bps = _bps(pre_fill_mid_move, mid_at_placed)

        book_spread = fill_sample.spread if fill_sample else None
        book_spread_bps = (
            _bps(book_spread, fill_sample.mid)
            if fill_sample and book_spread is not None else None
        )

        rows.append(PreFillDrift(
            fill_id=fill.fill_id,
            order_id=fill.order_id,
            side=fill.side,
            price=fill.price,
            quantity=fill.quantity,
            placed_time_ms=placed_time,
            fill_time_ms=fill.timestamp_ms,
            time_from_place_to_fill_ms=(
                fill.timestamp_ms - placed_time if placed_time is not None else None
            ),
            mid_at_placed=mid_at_placed,
            mid_at_fill=mid_at_fill,
            quoted_distance=quoted_distance,
            quoted_distance_bps=quoted_distance_bps,
            fill_edge=fill_edge,
            fill_edge_bps=fill_edge_bps,
            pre_fill_mid_move=pre_fill_mid_move,
            pre_fill_mid_move_bps=pre_fill_mid_move_bps,
            book_spread_at_fill=book_spread,
            book_spread_bps_at_fill=book_spread_bps,
            volatility_bps_at_fill=_rolling_vol_bps(
                sample_times, sample_mids_float, fill.timestamp_ms, vol_window_ms,
            ),
            quote_position_at_fill=_quote_position(fill, fill_sample),
        ))

    return rows


def extract_queue_diagnostics(events: Sequence[OrderEvent]) -> List[QueueOrderDiagnostic]:
    """Summarize queue movement per order from simulator event details."""
    by_order: Dict[str, List[OrderEvent]] = {}
    for ev in events:
        by_order.setdefault(ev.order_id, []).append(ev)

    rows: List[QueueOrderDiagnostic] = []
    for order_id, order_events in by_order.items():
        placed = next((e for e in order_events if e.event_type == "placed"), None)
        queued = next((e for e in order_events if e.event_type == "queued"), None)

        trade_drained = Decimal("0")
        cancel_drained = Decimal("0")
        for ev in order_events:
            if ev.event_type != "queue_drain":
                continue
            drained = Decimal(ev.detail.get("drained_qty", "0"))
            if ev.detail.get("reason") == "trade":
                trade_drained += drained
            elif ev.detail.get("reason") == "cancellation":
                cancel_drained += drained

        fill_events = [
            e for e in order_events if e.event_type in ("partial_fill", "filled")
        ]
        first_fill = fill_events[0] if fill_events else None
        final_event = next(
            (
                e for e in reversed(order_events)
                if e.event_type in ("filled", "cancelled", "partial_fill", "queued", "placed")
            ),
            None,
        )

        rows.append(QueueOrderDiagnostic(
            order_id=order_id,
            side=placed.detail.get("side") if placed else None,
            price=Decimal(placed.detail["price"]) if placed and placed.detail.get("price") else None,
            queued_time_ms=queued.timestamp_ms if queued else None,
            initial_queue_ahead=(
                Decimal(queued.detail["queue_ahead"])
                if queued and queued.detail.get("queue_ahead") is not None else None
            ),
            total_trade_queue_drained=trade_drained,
            total_cancel_queue_drained=cancel_drained,
            first_fill_time_ms=first_fill.timestamp_ms if first_fill else None,
            first_queue_ahead_before_trade=(
                Decimal(first_fill.detail["queue_ahead_before_trade"])
                if first_fill and first_fill.detail.get("queue_ahead_before_trade") is not None
                else None
            ),
            first_queue_ahead_before_fill=(
                Decimal(first_fill.detail["queue_ahead_before_fill"])
                if first_fill and first_fill.detail.get("queue_ahead_before_fill") is not None
                else None
            ),
            filled_quantity=sum(
                (
                    Decimal(e.detail.get("fill_qty", "0"))
                    for e in fill_events
                    if e.detail.get("fill_qty") is not None
                ),
                Decimal("0"),
            ),
            final_status=final_event.event_type if final_event else "unknown",
        ))

    return rows


def summarize_book_spread_by_volatility(
    rows: Sequence[PreFillDrift],
    edges_bps: Sequence[int] = VOL_BUCKET_EDGES_BPS,
) -> List[BookSpreadBucket]:
    """Summarize fill-time book spread inside each rolling-volatility bucket."""
    buckets: List[List[PreFillDrift]] = [[] for _ in range(len(edges_bps))]
    for row in rows:
        if row.volatility_bps_at_fill is None:
            continue
        idx = bisect_right(edges_bps, float(row.volatility_bps_at_fill)) - 1
        if idx >= 0:
            buckets[idx].append(row)

    out: List[BookSpreadBucket] = []
    for i, bucket in enumerate(buckets):
        lo = edges_bps[i]
        hi = edges_bps[i + 1] if i + 1 < len(edges_bps) else None
        label = f"[{lo},{hi})bps" if hi is not None else f"[{lo},inf)bps"
        spreads = [row.book_spread_at_fill for row in bucket if row.book_spread_at_fill is not None]
        spread_bps = [
            row.book_spread_bps_at_fill
            for row in bucket if row.book_spread_bps_at_fill is not None
        ]
        positions = [row.quote_position_at_fill for row in bucket]

        out.append(BookSpreadBucket(
            label=label,
            n_fills=len(bucket),
            avg_spread=_mean_or_none(spreads),
            median_spread=_median_or_none(spreads),
            avg_spread_bps=_mean_or_none(spread_bps),
            inside_spread=positions.count("inside_spread"),
            at_best=positions.count("at_best"),
            behind_best=positions.count("behind_best"),
            crossed=positions.count("crossed"),
            unknown=positions.count("unknown"),
        ))
    return out


def _close_lots(
    open_lots: deque[OpenInventoryLot],
    fill: Fill,
    fill_mid: Optional[Decimal],
    remaining: Decimal,
    matched: List[MatchedLot],
) -> Decimal:
    while remaining > Decimal("0") and open_lots:
        lot = open_lots[0]
        qty = min(remaining, lot.quantity)
        matched.append(_match_lot(lot, fill, qty, fill_mid))
        lot.quantity -= qty
        remaining -= qty
        if lot.quantity <= Decimal("0"):
            open_lots.popleft()
    return remaining


def _open_lot(fill: Fill, quantity: Decimal, open_mid: Optional[Decimal]) -> OpenInventoryLot:
    return OpenInventoryLot(
        fill_id=fill.fill_id,
        order_id=fill.order_id,
        side=fill.side,
        open_time_ms=fill.timestamp_ms,
        open_price=fill.price,
        quantity=quantity,
        open_mid=open_mid,
    )


def _match_lot(
    lot: OpenInventoryLot,
    close_fill: Fill,
    quantity: Decimal,
    close_mid: Optional[Decimal],
) -> MatchedLot:
    if lot.side == OrderSide.BUY:
        realized_pnl = (close_fill.price - lot.open_price) * quantity
    else:
        realized_pnl = (lot.open_price - close_fill.price) * quantity

    open_spread = None
    close_spread = None
    inventory_pnl = None
    if lot.open_mid is not None:
        open_spread = _side_sign(lot.side) * (lot.open_mid - lot.open_price) * quantity
    if close_mid is not None:
        close_spread = _side_sign(close_fill.side) * (close_mid - close_fill.price) * quantity
    if open_spread is not None and close_spread is not None:
        inventory_pnl = realized_pnl - open_spread - close_spread

    return MatchedLot(
        open_fill_id=lot.fill_id,
        close_fill_id=close_fill.fill_id,
        open_order_id=lot.order_id,
        close_order_id=close_fill.order_id,
        open_side=lot.side,
        close_side=close_fill.side,
        open_time_ms=lot.open_time_ms,
        close_time_ms=close_fill.timestamp_ms,
        hold_time_ms=close_fill.timestamp_ms - lot.open_time_ms,
        open_price=lot.open_price,
        close_price=close_fill.price,
        quantity=quantity,
        open_mid=lot.open_mid,
        close_mid=close_mid,
        realized_pnl=realized_pnl,
        open_spread_capture=open_spread,
        close_spread_capture=close_spread,
        inventory_pnl=inventory_pnl,
    )


def _weighted_average_hold_time(lots: Sequence[MatchedLot]) -> Optional[Decimal]:
    total_qty = sum((lot.quantity for lot in lots), Decimal("0"))
    if total_qty <= Decimal("0"):
        return None
    weighted = sum(
        (Decimal(lot.hold_time_ms) * lot.quantity for lot in lots),
        Decimal("0"),
    )
    return weighted / total_qty


def _weighted_percentile_ms(
    lots: Sequence[MatchedLot],
    percentile: Decimal,
) -> Optional[int]:
    total_qty = sum((lot.quantity for lot in lots), Decimal("0"))
    if total_qty <= Decimal("0"):
        return None

    target = total_qty * percentile
    cumulative = Decimal("0")
    for lot in sorted(lots, key=lambda row: row.hold_time_ms):
        cumulative += lot.quantity
        if cumulative >= target:
            return lot.hold_time_ms
    return lots[-1].hold_time_ms


def _sample_at_or_before(
    samples: Sequence[BookSample],
    sample_times: Sequence[int],
    target_ms: int,
) -> Optional[BookSample]:
    if not samples:
        return None
    idx = bisect_right(sample_times, target_ms) - 1
    if idx < 0:
        return None
    return samples[idx]


def _mid_at(
    samples: Sequence[BookSample],
    sample_times: Sequence[int],
    target_ms: int,
) -> Optional[Decimal]:
    sample = _sample_at_or_before(samples, sample_times, target_ms)
    return sample.mid if sample is not None else None


def _side_sign(side: OrderSide) -> Decimal:
    return Decimal("1") if side == OrderSide.BUY else Decimal("-1")


def _bps(value: Optional[Decimal], denominator: Optional[Decimal]) -> Optional[Decimal]:
    if value is None or denominator is None or denominator == Decimal("0"):
        return None
    return (value / denominator) * Decimal("10000")


def _rolling_vol_bps(
    sample_times: Sequence[int],
    sample_mids_float: Sequence[float],
    target_ms: int,
    window_ms: int,
) -> Optional[Decimal]:
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
    variance = sum((mid - mean) ** 2 for mid in window) / (len(window) - 1)
    return Decimal(str(round((math.sqrt(variance) / mean) * 10_000, 6)))


def _quote_position(fill: Fill, sample: Optional[BookSample]) -> str:
    if sample is None:
        return "unknown"
    if fill.side == OrderSide.BUY:
        if fill.price >= sample.best_ask:
            return "crossed"
        if fill.price > sample.best_bid:
            return "inside_spread"
        if fill.price == sample.best_bid:
            return "at_best"
        return "behind_best"

    if fill.price <= sample.best_bid:
        return "crossed"
    if fill.price < sample.best_ask:
        return "inside_spread"
    if fill.price == sample.best_ask:
        return "at_best"
    return "behind_best"


def _mean_or_none(values: Iterable[Decimal]) -> Optional[Decimal]:
    vals = list(values)
    if not vals:
        return None
    return sum(vals, Decimal("0")) / Decimal(len(vals))


def _median_or_none(values: Iterable[Decimal]) -> Optional[Decimal]:
    vals = list(values)
    if not vals:
        return None
    return Decimal(str(statistics.median(float(v) for v in vals)))
