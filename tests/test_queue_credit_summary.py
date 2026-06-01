"""Tests for the V2 queue-credit stress summarizer."""

from decimal import Decimal
from pathlib import Path

from src.analysis.fee_break_even import ReconciliationRun
from src.analysis.queue_credit_summary import QueueCreditRun, build_queue_credit_summary_rows


def run(window, *, credit, latency, fills, matched_qty, matched_net):
    recon = ReconciliationRun(
        run_dir=Path(window),
        summary={
            "params": {
                "start": window,
                "maker_bps": 2,
                "queue_cancellation_credit": credit,
            },
            "aggregate": {
                "fills": fills,
                "orders_submitted": fills * 2,
                "matched_qty": matched_qty,
                "matched_net_pnl": matched_net,
                "matched_realized_pnl": str(Decimal(matched_net) + Decimal("0.2")),
                "matched_fees": "0.2",
                "residual_inventory_pnl": "-0.1",
                "net_pnl": "-1",
                "fees": "0.4",
            },
        },
        matched_lots=[],
        open_lots=[],
    )
    return QueueCreditRun(latency_ms=latency, reconciliation=recon)


def test_queue_credit_summary_reports_per_btc_economics_and_pooled_rows():
    rows = build_queue_credit_summary_rows([
        run("a", credit="0", latency=10, fills=4, matched_qty="1", matched_net="-1"),
        run("b", credit="0", latency=10, fills=6, matched_qty="3", matched_net="0"),
    ])
    pooled = [row for row in rows if row["window"] == "pooled"][0]

    assert pooled["queue_cancellation_credit"] == Decimal("0")
    assert pooled["fills"] == 10
    assert pooled["orders_per_fill"] == Decimal("2")
    assert pooled["matched_quantity"] == Decimal("4")
    assert pooled["quantity_weighted_matched_net_pnl_per_btc"] == Decimal("-0.25")
