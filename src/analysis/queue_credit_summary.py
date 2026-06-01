"""Queue-credit and latency stress summaries for reconciliation artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from src.analysis.fee_break_even import ReconciliationRun


@dataclass(frozen=True)
class QueueCreditRun:
    latency_ms: int
    reconciliation: ReconciliationRun


def build_queue_credit_summary_rows(runs: Sequence[QueueCreditRun]) -> list[dict]:
    """Build per-window and pooled V2 queue-credit stress rows."""
    rows = [
        _summarize_group([run], window=run.reconciliation.window)
        for run in runs
    ]
    keys = sorted({(run.reconciliation.queue_credit, run.latency_ms) for run in runs})
    for credit, latency_ms in keys:
        group = [
            run for run in runs
            if run.reconciliation.queue_credit == credit and run.latency_ms == latency_ms
        ]
        rows.append(_summarize_group(group, window="pooled"))
    return rows


def _summarize_group(runs: Sequence[QueueCreditRun], *, window: str) -> dict:
    if not runs:
        raise ValueError("cannot summarize an empty queue-credit group")
    credits = {run.reconciliation.queue_credit for run in runs}
    latencies = {run.latency_ms for run in runs}
    if len(credits) != 1 or len(latencies) != 1:
        raise ValueError("queue-credit summary groups must share credit and latency")
    aggregates = [run.reconciliation.summary["aggregate"] for run in runs]
    params = [run.reconciliation.summary["params"] for run in runs]
    fills = sum(int(row["fills"]) for row in aggregates)
    orders = sum(int(row["orders_submitted"]) for row in aggregates)
    matched_quantity = sum((_d(row["matched_qty"]) for row in aggregates), Decimal("0"))
    matched_net = sum((_d(row["matched_net_pnl"]) for row in aggregates), Decimal("0"))
    full_net = sum((_d(row["net_pnl"]) for row in aggregates), Decimal("0"))
    full_fees = sum((_d(row["fees"]) for row in aggregates), Decimal("0"))
    matched_gross = sum(
        (_d(row["matched_realized_pnl"]) for row in aggregates),
        Decimal("0"),
    )
    matched_fees = sum((_d(row["matched_fees"]) for row in aggregates), Decimal("0"))
    maker_bps_values = {int(row["maker_bps"]) for row in params}
    if len(maker_bps_values) != 1:
        raise ValueError("queue-credit summary groups must share maker_bps")
    maker_bps = maker_bps_values.pop()
    return {
        "queue_cancellation_credit": credits.pop(),
        "latency_ms": latencies.pop(),
        "window": window,
        "fills": fills,
        "orders_submitted": orders,
        "orders_per_fill": Decimal(orders) / Decimal(fills) if fills else None,
        "matched_quantity": matched_quantity,
        "matched_net_pnl": matched_net,
        "quantity_weighted_matched_net_pnl_per_btc": (
            matched_net / matched_quantity if matched_quantity > Decimal("0") else None
        ),
        "residual_inventory_pnl": sum(
            (_d(row.get("residual_inventory_pnl") or "0") for row in aggregates),
            Decimal("0"),
        ),
        "full_strategy_net_pnl": full_net,
        "full_strategy_break_even_maker_fee_bps": _break_even_bps(
            full_net + full_fees, full_fees, maker_bps
        ),
        "matched_break_even_maker_fee_bps": _break_even_bps(
            matched_gross, matched_fees, maker_bps
        ),
    }


def _break_even_bps(gross: Decimal, current_fees: Decimal, maker_bps: int):
    if maker_bps == 0 or current_fees <= Decimal("0"):
        return None
    fee_notional = current_fees / (Decimal(maker_bps) / Decimal("10000"))
    return gross / fee_notional * Decimal("10000")


def _d(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))
