"""
PnL decomposition for market-making replay sessions.

Breaks net PnL into four components:
  spread_capture    — gross edge captured relative to mid at fill time
  total_fees        — all maker/taker fees paid
  adverse_selection — proxy from markouts at a chosen horizon (negative = cost)
  inventory_pnl     — mark-to-market on the residual position at session end

Identity (approximate):
  net_pnl ≈ spread_capture - total_fees + inventory_pnl
  adverse_selection is a separate diagnostic, not a component of net_pnl
"""
from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Sequence

from src.analysis.markout import Markout
from src.execution.order import Fill, OrderSide
from src.replay.engine import BookSample


@dataclass
class PnLDecomposition:
    fill_count: int
    maker_fill_count: int
    total_notional: Decimal

    # Gross edge relative to mid at the moment of each fill.
    # Almost always positive for a resting maker.
    spread_capture: Decimal
    spread_capture_bps: Decimal

    total_fees: Decimal

    # Adverse selection proxy: -sum(markout × qty) at the chosen horizon.
    # Positive value = fills that moved against us afterward (a cost).
    adverse_selection_horizon: str
    adverse_selection_cost: Decimal
    adverse_selection_bps: Decimal

    # Residual inventory mark-to-market at session end.
    final_position: Decimal
    avg_entry_price: Decimal
    final_mid: Decimal
    inventory_pnl: Decimal

    # Summary lines
    gross_pnl: Decimal      # spread_capture + inventory_pnl
    net_pnl: Decimal        # gross_pnl - total_fees


def compute_pnl_decomposition(
    fills: Sequence[Fill],
    book_samples: Sequence[BookSample],
    markouts: Sequence[Markout],
    final_mid: Optional[Decimal],
    adverse_selection_horizon: str = "30s",
) -> PnLDecomposition:
    """Decompose session PnL into spread capture, fees, adverse selection, inventory."""
    zero = Decimal("0")

    if not fills:
        fm = final_mid or zero
        return PnLDecomposition(
            fill_count=0, maker_fill_count=0, total_notional=zero,
            spread_capture=zero, spread_capture_bps=zero,
            total_fees=zero,
            adverse_selection_horizon=adverse_selection_horizon,
            adverse_selection_cost=zero, adverse_selection_bps=zero,
            final_position=zero, avg_entry_price=zero,
            final_mid=fm, inventory_pnl=zero,
            gross_pnl=zero, net_pnl=zero,
        )

    # Sort samples once for bisect lookups
    sorted_samples = sorted(book_samples, key=lambda s: s.timestamp_ms)
    sorted_times = [s.timestamp_ms for s in sorted_samples]

    spread_capture = zero
    total_notional = zero
    total_fees = zero
    maker_fill_count = 0
    running_qty = zero   # signed: positive = net long
    running_cost = zero  # signed: tracks cost basis

    for fill in fills:
        total_notional += fill.notional
        total_fees += fill.fee
        if fill.is_maker:
            maker_fill_count += 1

        # Mid at fill time: latest sample at or before fill timestamp
        idx = bisect_right(sorted_times, fill.timestamp_ms) - 1
        if idx >= 0:
            mid = sorted_samples[idx].mid
            if fill.side == OrderSide.BUY:
                spread_capture += (mid - fill.price) * fill.quantity
            else:
                spread_capture += (fill.price - mid) * fill.quantity

        # Running cost basis for inventory PnL
        if fill.side == OrderSide.BUY:
            running_qty += fill.quantity
            running_cost += fill.price * fill.quantity
        else:
            running_qty -= fill.quantity
            running_cost -= fill.price * fill.quantity

    # Adverse selection via markouts at the chosen horizon
    horizon_markouts = [m for m in markouts if m.horizon == adverse_selection_horizon]
    adverse_selection_cost = -sum(
        (m.markout * m.quantity for m in horizon_markouts), zero
    )

    # Inventory PnL: residual position marked to final mid
    final_position = running_qty
    fm = final_mid or zero
    if final_position != zero:
        avg_entry_price = running_cost / final_position
        inventory_pnl = (fm - avg_entry_price) * final_position
    else:
        avg_entry_price = zero
        inventory_pnl = zero

    # bps denominators
    if total_notional > zero:
        spread_capture_bps = (spread_capture / total_notional) * Decimal("10000")
        adverse_selection_bps = (adverse_selection_cost / total_notional) * Decimal("10000")
    else:
        spread_capture_bps = zero
        adverse_selection_bps = zero

    gross_pnl = spread_capture + inventory_pnl
    net_pnl = gross_pnl - total_fees

    return PnLDecomposition(
        fill_count=len(fills),
        maker_fill_count=maker_fill_count,
        total_notional=total_notional,
        spread_capture=spread_capture,
        spread_capture_bps=spread_capture_bps,
        total_fees=total_fees,
        adverse_selection_horizon=adverse_selection_horizon,
        adverse_selection_cost=adverse_selection_cost,
        adverse_selection_bps=adverse_selection_bps,
        final_position=final_position,
        avg_entry_price=avg_entry_price,
        final_mid=fm,
        inventory_pnl=inventory_pnl,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
    )


def format_pnl_summary(decomp: PnLDecomposition) -> str:
    def _f(val: Decimal) -> str:
        return format(val, "f")

    def _bps(val: Decimal) -> str:
        return f"{float(val):+.2f} bps"

    lines = [
        "PnL Decomposition",
        f"  Spread capture:     {_f(decomp.spread_capture):>16}  ({_bps(decomp.spread_capture_bps)})",
        f"  Fees:               {_f(-decomp.total_fees):>16}",
        f"  Adverse sel ({decomp.adverse_selection_horizon}):  "
        f"{_f(-decomp.adverse_selection_cost):>16}  ({_bps(-decomp.adverse_selection_bps)})",
        f"  Inventory PnL:      {_f(decomp.inventory_pnl):>16}",
        f"  {'─' * 36}",
        f"  Net PnL:            {_f(decomp.net_pnl):>16}",
    ]
    if decomp.final_position != Decimal("0"):
        lines.append(
            f"  Avg entry:          {_f(decomp.avg_entry_price):>16}"
            f"  (pos: {_f(decomp.final_position)})"
        )
    return "\n".join(lines)
