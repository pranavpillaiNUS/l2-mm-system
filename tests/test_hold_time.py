"""
Tests for inventory hold-time and reconciliation analysis.

Run with: python tests/test_hold_time.py
"""
from decimal import Decimal

from src.analysis.hold_time import (
    compute_hold_time_summary,
    compute_pre_fill_drifts,
    compute_reconciliation_summary,
)
from src.analysis.markout import compute_markouts
from src.analysis.pnl import compute_pnl_decomposition
from src.execution.order import Fill, OrderEvent, OrderSide
from src.replay.engine import BookSample, ReplayResult, ReplayStats


def make_fill(fill_id, order_id, side, price, qty, t_ms, fee="0"):
    return Fill(
        fill_id=fill_id,
        order_id=order_id,
        side=side,
        price=Decimal(price),
        quantity=Decimal(qty),
        is_maker=True,
        timestamp_ms=t_ms,
        fee=Decimal(fee),
    )


def make_sample(t_ms, mid):
    mid = Decimal(mid)
    return BookSample(
        timestamp_ms=t_ms,
        best_bid=mid - Decimal("0.50"),
        best_ask=mid + Decimal("0.50"),
        mid=mid,
        microprice=mid,
        spread=Decimal("1.00"),
    )


def make_placed(order_id, t_ms, side, price, qty="1"):
    return OrderEvent(
        order_id=order_id,
        timestamp_ms=t_ms,
        event_type="placed",
        detail={
            "side": side,
            "type": "limit",
            "price": price,
            "qty": qty,
            "arrival_ms": t_ms,
        },
    )


def make_result(fills, events, samples):
    return ReplayResult(
        fills=fills,
        events=events,
        stats=ReplayStats(),
        checkpoints=[],
        book_samples=samples,
    )


def test_one_buy_closed_by_one_sell():
    fills = [
        make_fill("f1", "o1", OrderSide.BUY, "100", "1", 0),
        make_fill("f2", "o2", OrderSide.SELL, "102", "1", 1000),
    ]

    summary = compute_hold_time_summary(fills)

    assert len(summary.matched_lots) == 1
    assert summary.realized_pnl == Decimal("2")
    assert summary.matched_fees == Decimal("0")
    assert summary.matched_net_pnl == Decimal("2")
    assert summary.residual_inventory == Decimal("0")
    assert summary.p50_hold_time_ms == 1000
    print("PASS: one buy closes against one sell")


def test_one_buy_closed_by_multiple_sells():
    fills = [
        make_fill("f1", "o1", OrderSide.BUY, "100", "2", 0),
        make_fill("f2", "o2", OrderSide.SELL, "101", "0.5", 1000),
        make_fill("f3", "o3", OrderSide.SELL, "103", "1.5", 2000),
    ]

    summary = compute_hold_time_summary(fills)

    assert len(summary.matched_lots) == 2
    assert summary.total_matched_qty == Decimal("2.0")
    assert summary.realized_pnl == Decimal("5.0")
    assert summary.residual_inventory == Decimal("0")
    print("PASS: one buy can close across multiple sells")


def test_multiple_buys_closed_by_one_sell():
    fills = [
        make_fill("f1", "o1", OrderSide.BUY, "100", "1", 0),
        make_fill("f2", "o2", OrderSide.BUY, "101", "1", 1000),
        make_fill("f3", "o3", OrderSide.SELL, "102", "2", 2000),
    ]

    summary = compute_hold_time_summary(fills)

    assert len(summary.matched_lots) == 2
    assert summary.realized_pnl == Decimal("3")
    assert [lot.open_fill_id for lot in summary.matched_lots] == ["f1", "f2"]
    print("PASS: multiple buys close FIFO against one sell")


def test_partial_leftover_inventory():
    fills = [
        make_fill("f1", "o1", OrderSide.BUY, "100", "2", 0),
        make_fill("f2", "o2", OrderSide.SELL, "101", "0.5", 1000),
    ]

    summary = compute_hold_time_summary(fills)

    assert len(summary.matched_lots) == 1
    assert summary.realized_pnl == Decimal("0.5")
    assert summary.residual_inventory == Decimal("1.5")
    assert len(summary.open_lots) == 1
    assert summary.open_lots[0].quantity == Decimal("1.5")
    print("PASS: leftover long inventory is retained")


def test_short_inventory_closed_by_later_buy():
    fills = [
        make_fill("f1", "o1", OrderSide.SELL, "105", "1", 0),
        make_fill("f2", "o2", OrderSide.BUY, "100", "1", 1000),
    ]

    summary = compute_hold_time_summary(fills)

    assert summary.realized_pnl == Decimal("5")
    assert summary.residual_inventory == Decimal("0")
    assert summary.matched_lots[0].open_side == OrderSide.SELL
    print("PASS: short inventory closes against later buys")


def test_weighted_hold_time_percentiles():
    fills = [
        make_fill("f1", "o1", OrderSide.BUY, "100", "4", 0),
        make_fill("f2", "o2", OrderSide.SELL, "101", "1", 1000),
        make_fill("f3", "o3", OrderSide.SELL, "101", "3", 5000),
    ]

    summary = compute_hold_time_summary(fills)

    assert summary.avg_hold_time_ms == Decimal("4000")
    assert summary.p25_hold_time_ms == 1000
    assert summary.p50_hold_time_ms == 5000
    assert summary.p75_hold_time_ms == 5000
    assert summary.p90_hold_time_ms == 5000
    assert summary.max_hold_time_ms == 5000
    print("PASS: hold-time percentiles are quantity-weighted")


def test_completed_round_trip_allocates_fees():
    fills = [
        make_fill("f1", "o1", OrderSide.BUY, "100", "2", 0, fee="0.20"),
        make_fill("f2", "o2", OrderSide.SELL, "103", "1", 1000, fee="0.15"),
    ]

    summary = compute_hold_time_summary(fills)
    lot = summary.matched_lots[0]

    assert summary.realized_pnl == Decimal("3")
    assert lot.open_fee == Decimal("0.10")
    assert lot.close_fee == Decimal("0.15")
    assert lot.total_fees == Decimal("0.25")
    assert lot.net_pnl == Decimal("2.75")
    assert summary.matched_fees == Decimal("0.25")
    assert summary.matched_net_pnl == Decimal("2.75")
    assert summary.open_lots[0].fee_remaining == Decimal("0.10")
    print("PASS: completed round-trip fees are allocated by matched quantity")


def test_reconciliation_uses_mid_to_mid_inventory_pnl():
    fills = [
        make_fill("f1", "o1", OrderSide.BUY, "99.50", "1", 0),
        make_fill("f2", "o2", OrderSide.SELL, "101.50", "1", 1000),
    ]
    samples = [
        make_sample(0, "100.00"),
        make_sample(1000, "101.00"),
        make_sample(2000, "101.00"),
    ]

    markouts = compute_markouts(fills, samples, {"1s": 1000})
    decomp = compute_pnl_decomposition(fills, samples, markouts, Decimal("101.00"))
    hold_summary = compute_hold_time_summary(fills, samples)
    recon = compute_reconciliation_summary(hold_summary, markouts, decomp, {"1s": 1000})

    assert decomp.spread_capture == Decimal("1.00")
    assert decomp.inventory_pnl == Decimal("1.00")
    assert recon.actual_net_from_components == Decimal("2.00")
    assert recon.matched_fees == Decimal("0")
    assert recon.matched_net_pnl == Decimal("2.00")
    assert recon.horizons[0].proxy_inventory_pnl == Decimal("1.00")
    assert recon.horizons[0].proxy_net_pnl == Decimal("2.00")
    assert recon.horizons[0].net_error == Decimal("0.00")
    print("PASS: reconciliation compares horizon markout to mid-to-mid inventory PnL")


def test_pre_fill_drift_decomposition():
    fills = [make_fill("f1", "o1", OrderSide.BUY, "99.00", "1", 1000)]
    events = [make_placed("o1", 0, "buy", "99.00")]
    samples = [make_sample(0, "100.00"), make_sample(1000, "99.00")]

    rows = compute_pre_fill_drifts(make_result(fills, events, samples))

    assert len(rows) == 1
    assert rows[0].quoted_distance == Decimal("1.00")
    assert rows[0].fill_edge == Decimal("0.00")
    assert rows[0].pre_fill_mid_move == Decimal("-1.00")
    print("PASS: pre-fill drift explains lost quoted edge")


if __name__ == "__main__":
    test_one_buy_closed_by_one_sell()
    test_one_buy_closed_by_multiple_sells()
    test_multiple_buys_closed_by_one_sell()
    test_partial_leftover_inventory()
    test_short_inventory_closed_by_later_buy()
    test_weighted_hold_time_percentiles()
    test_completed_round_trip_allocates_fees()
    test_reconciliation_uses_mid_to_mid_inventory_pnl()
    test_pre_fill_drift_decomposition()
    print("\nAll tests passed.")
