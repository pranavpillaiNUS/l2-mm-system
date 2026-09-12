"""
Tests for PnL decomposition.

Run with: python tests/test_pnl.py
"""
from decimal import Decimal

from src.analysis.markout import compute_markouts
from src.analysis.pnl import PnLDecomposition, compute_pnl_decomposition
from src.execution.order import Fill, OrderSide
from src.replay.engine import BookSample


def make_fill(fill_id, side, price, qty="0.001", timestamp_ms=1000, is_maker=True):
    price = Decimal(price)
    qty = Decimal(qty)
    return Fill(
        fill_id=fill_id,
        order_id=f"order-{fill_id}",
        side=side,
        price=price,
        quantity=qty,
        is_maker=is_maker,
        timestamp_ms=timestamp_ms,
        fee=price * qty * Decimal("0.0002"),  # 2 bps maker fee
    )


def make_sample(timestamp_ms, mid):
    mid = Decimal(mid)
    return BookSample(
        timestamp_ms=timestamp_ms,
        best_bid=mid - Decimal("0.50"),
        best_ask=mid + Decimal("0.50"),
        mid=mid,
        microprice=mid,
        spread=Decimal("1.00"),
    )


def test_zero_fills_returns_zero_decomp():
    decomp = compute_pnl_decomposition([], [], [], Decimal("50000"))
    assert decomp.fill_count == 0
    assert decomp.spread_capture == Decimal("0")
    assert decomp.net_pnl == Decimal("0")
    assert decomp.inventory_pnl == Decimal("0")
    print("PASS: zero fills returns all-zero decomp")


def test_spread_capture_buy_fill():
    # BUY at 99.50, mid at fill time = 100.00 -> spread capture = 0.50 x qty
    fill = make_fill("f1", OrderSide.BUY, "99.50", qty="1.0", timestamp_ms=500)
    samples = [make_sample(500, "100.00")]
    markouts = compute_markouts([fill], samples, {"30s": 30000})

    decomp = compute_pnl_decomposition([fill], samples, markouts, Decimal("100.00"))

    assert decomp.spread_capture == Decimal("0.50")
    print("PASS: spread capture correct for BUY fill below mid")


def test_spread_capture_sell_fill():
    # SELL at 100.50, mid at fill time = 100.00 -> spread capture = 0.50 x qty
    fill = make_fill("f1", OrderSide.SELL, "100.50", qty="1.0", timestamp_ms=500)
    samples = [make_sample(500, "100.00")]
    markouts = compute_markouts([fill], samples, {"30s": 30000})

    decomp = compute_pnl_decomposition([fill], samples, markouts, Decimal("100.00"))

    assert decomp.spread_capture == Decimal("0.50")
    print("PASS: spread capture correct for SELL fill above mid")


def test_inventory_pnl_long_position():
    # BUY 1 BTC at 99.50, session ends with mid at 100.50
    # spread_capture = (100.00 - 99.50) x 1 = 0.50
    # inventory_pnl = (100.50 - 100.00) x 1 = 0.50
    # gross_pnl = (100.50 - 99.50) x 1 = 1.00
    fill = make_fill("f1", OrderSide.BUY, "99.50", qty="1.0", timestamp_ms=500)
    samples = [make_sample(500, "100.00")]
    markouts = compute_markouts([fill], samples, {"30s": 30000})

    decomp = compute_pnl_decomposition([fill], samples, markouts, Decimal("100.50"))

    assert decomp.final_position == Decimal("1.0")
    assert decomp.avg_entry_price == Decimal("99.50")
    assert decomp.spread_capture == Decimal("0.50")
    assert decomp.inventory_pnl == Decimal("0.50")
    assert decomp.gross_pnl == Decimal("1.00")
    print("PASS: open inventory does not double-count spread capture")


def test_zero_inventory_pnl_when_flat():
    # BUY then SELL same qty - net position = 0
    buy = make_fill("f1", OrderSide.BUY, "99.50", qty="1.0", timestamp_ms=500)
    sell = make_fill("f2", OrderSide.SELL, "100.50", qty="1.0", timestamp_ms=600)
    samples = [make_sample(500, "100.00"), make_sample(600, "100.00")]
    markouts = compute_markouts([buy, sell], samples, {"30s": 30000})

    decomp = compute_pnl_decomposition([buy, sell], samples, markouts, Decimal("100.00"))

    assert decomp.final_position == Decimal("0")
    assert decomp.inventory_pnl == Decimal("0")
    print("PASS: inventory PnL is zero when position is flat")


def test_avg_entry_after_partial_close_tracks_residual_inventory():
    # Buy 1 @ 100, buy 1 @ 102 -> average entry 101.
    # Sell 1 @ 103 closes one unit. The remaining long should still have
    # average entry 101, not a cash-residual artifact.
    fills = [
        make_fill("f1", OrderSide.BUY, "100.00", qty="1.0", timestamp_ms=500),
        make_fill("f2", OrderSide.BUY, "102.00", qty="1.0", timestamp_ms=600),
        make_fill("f3", OrderSide.SELL, "103.00", qty="1.0", timestamp_ms=700),
    ]
    samples = [
        make_sample(500, "100.50"),
        make_sample(600, "102.50"),
        make_sample(700, "102.50"),
    ]
    markouts = compute_markouts(fills, samples, {"30s": 30000})

    decomp = compute_pnl_decomposition(fills, samples, markouts, Decimal("104.00"))

    assert decomp.final_position == Decimal("1.0")
    assert decomp.avg_entry_price == Decimal("101.00")
    assert decomp.gross_pnl == Decimal("5.000")
    print("PASS: avg entry remains correct after partial close")


def test_adverse_selection_cost_sign():
    # BUY at 100.00, 30s later mid drops to 99.00 -> markout = -1.00 (adverse)
    # adverse_selection_cost = -markout x qty = +1.00 x 1.0 = 1.00 (a cost)
    fill = make_fill("f1", OrderSide.BUY, "100.00", qty="1.0", timestamp_ms=1000)
    samples = [
        make_sample(1000, "100.00"),  # mid at fill time
        make_sample(32000, "99.00"),  # 31s later, mid dropped
    ]
    markouts = compute_markouts([fill], samples, {"30s": 30000})
    decomp = compute_pnl_decomposition([fill], samples, markouts, Decimal("99.00"),
                                       adverse_selection_horizon="30s")

    assert decomp.adverse_selection_cost == Decimal("1.00")
    print("PASS: adverse selection cost is positive when fills moved against us")


def test_net_pnl_identity():
    # net_pnl = spread_capture + inventory_pnl - fees (no adverse selection in identity)
    fill = make_fill("f1", OrderSide.BUY, "99.50", qty="1.0", timestamp_ms=500)
    samples = [make_sample(500, "100.00")]
    markouts = compute_markouts([fill], samples, {"30s": 30000})
    decomp = compute_pnl_decomposition([fill], samples, markouts, Decimal("100.00"))

    expected_net = decomp.spread_capture + decomp.inventory_pnl - decomp.total_fees
    assert decomp.net_pnl == expected_net
    print("PASS: net_pnl == spread_capture + inventory_pnl - fees")


def test_fill_before_any_sample_contributes_zero_spread():
    # Fill at t=100, first sample at t=500 -> no sample before fill -> spread = 0
    fill = make_fill("f1", OrderSide.BUY, "99.50", qty="1.0", timestamp_ms=100)
    samples = [make_sample(500, "100.00")]
    markouts = compute_markouts([fill], samples, {"30s": 30000})
    decomp = compute_pnl_decomposition([fill], samples, markouts, Decimal("100.00"))

    assert decomp.spread_capture == Decimal("0")
    assert decomp.fill_count == 1  # fill still counted
    print("PASS: fill before first sample contributes zero spread, not skipped")


if __name__ == "__main__":
    test_zero_fills_returns_zero_decomp()
    test_spread_capture_buy_fill()
    test_spread_capture_sell_fill()
    test_inventory_pnl_long_position()
    test_zero_inventory_pnl_when_flat()
    test_avg_entry_after_partial_close_tracks_residual_inventory()
    test_adverse_selection_cost_sign()
    test_net_pnl_identity()
    test_fill_before_any_sample_contributes_zero_spread()
    print("\nAll tests passed.")
