"""
Tests for maker-fee break-even diagnostics.
"""
from decimal import Decimal
from pathlib import Path

from src.analysis.fee_break_even import (
    ReconciliationRun,
    build_fee_break_even_rows,
)


def _run(matched_lots):
    return ReconciliationRun(
        run_dir=Path("synthetic_run"),
        summary={
            "params": {
                "start": "2026-04-16T12:00:00",
                "maker_bps": 2,
                "queue_cancellation_mode": "proportional",
            },
            "aggregate": {
                "net_pnl": "-1",
                "fees": "2",
            },
        },
        matched_lots=matched_lots,
        open_lots=[{
            "quantity": "0.5",
        }],
    )


def _lot(net_pnl, realized_pnl, fees, quantity="1"):
    return {
        "open_fill_id": "open",
        "close_fill_id": "close",
        "close_time_ms": "1000",
        "quantity": quantity,
        "realized_pnl": realized_pnl,
        "total_fees": fees,
        "net_pnl": net_pnl,
    }


def test_break_even_fee_uses_gross_and_fee_notional():
    rows = build_fee_break_even_rows([
        _run([_lot(net_pnl="-1", realized_pnl="1", fees="2")])
    ])
    full_matched = [
        row for row in rows
        if row["window"] != "pooled" and row["row_type"] == "full_matched_lots"
    ][0]

    assert full_matched["gross_before_fees"] == Decimal("1")
    assert full_matched["current_fees"] == Decimal("2")
    assert full_matched["fee_notional"] == Decimal("10000")
    assert full_matched["break_even_maker_fee_bps"] == Decimal("1")
    assert full_matched["required_rebate_bps"] == Decimal("0")
    assert full_matched["quantity_weighted_net_per_btc"] == Decimal("-1")
    assert full_matched["queue_cancellation_credit"] == Decimal("1.0")
    print("PASS: break-even fee uses gross before fees and implied notional")


def test_negative_gross_requires_rebate():
    rows = build_fee_break_even_rows([
        _run([_lot(net_pnl="-3", realized_pnl="-1", fees="2")])
    ])
    full_matched = [
        row for row in rows
        if row["window"] != "pooled" and row["row_type"] == "full_matched_lots"
    ][0]

    assert full_matched["break_even_maker_fee_bps"] == Decimal("-1")
    assert full_matched["required_rebate_bps"] == Decimal("1")
    print("PASS: negative gross PnL is reported as required maker rebate")


def test_tail_excluded_rows_drop_worst_and_body_drops_both_tails():
    lots = [
        _lot(net_pnl="-100", realized_pnl="-98", fees="2"),
        *[
            _lot(net_pnl=str(i), realized_pnl=str(i + 2), fees="2")
            for i in range(1, 20)
        ],
    ]
    rows = build_fee_break_even_rows([_run(lots)])
    per_window = [row for row in rows if row["window"] != "pooled"]
    by_type = {row["row_type"]: row for row in per_window}

    assert by_type["full_matched_lots"]["lot_count"] == 20
    assert by_type["matched_tail_excluded_worst_5pct"]["lot_count"] == 19
    assert by_type["matched_body_only_ex_worst_best_5pct"]["lot_count"] == 18
    assert by_type["matched_tail_excluded_worst_5pct"]["net_pnl"] == Decimal("190")
    assert by_type["matched_body_only_ex_worst_best_5pct"]["net_pnl"] == Decimal("171")
    print("PASS: fee break-even tail rows use exact-count 5pct exclusions")
