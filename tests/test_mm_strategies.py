"""
Tests for BaseMMStrategy and SymmetricMM.

Run with: python tests/test_mm_strategies.py
"""
from decimal import Decimal
from datetime import datetime
from typing import Optional, Tuple

from src.execution.order import (
    Fill, Order, OrderRequest, OrderSide, OrderStatus, OrderType,
)
from src.replay.engine import CancelRequest
from src.replay.orderbook import Orderbook
from src.replay.trade_parser import TradeEvent
from src.strategies.base_mm import BaseMMStrategy
from src.strategies.symmetric_mm import SymmetricMM


# --- helpers ---

def make_book(bids=None, asks=None):
    book = Orderbook()
    book.apply_snapshot(
        bids=bids or [("100.00", "5.0"), ("99.00", "10.0")],
        asks=asks or [("101.00", "3.0"), ("102.00", "8.0")],
        last_update_id=1000,
    )
    return book


def make_order(order_id, side, price, status=OrderStatus.ACTIVE):
    return Order(
        order_id=order_id,
        side=side,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.01"),
        placed_time_ms=1000,
        arrival_time_ms=1010,
        price=price,
        status=status,
    )


def make_fill(order_id, side, price, qty, is_maker=True, fee=Decimal("0")):
    return Fill(
        fill_id="fill-1",
        order_id=order_id,
        side=side,
        price=Decimal(price),
        quantity=Decimal(qty),
        is_maker=is_maker,
        timestamp_ms=2000,
        fee=fee,
    )


# --- SymmetricMM quote tests ---

def test_symmetric_quotes_around_mid():
    strat = SymmetricMM(
        half_spread=Decimal("0.50"),
        order_qty=Decimal("0.01"),
        max_position=Decimal("1.0"),
    )
    # mid = (100 + 101) / 2 = 100.50
    book = make_book()
    actions = strat.on_book_update(book, timestamp_ms=1000)

    # Should place a buy and a sell
    orders = [a for a in actions if isinstance(a, OrderRequest)]
    assert len(orders) == 2

    buy = next(o for o in orders if o.side == OrderSide.BUY)
    sell = next(o for o in orders if o.side == OrderSide.SELL)

    assert buy.price == Decimal("100.00")   # mid(100.50) - 0.50
    assert sell.price == Decimal("101.00")   # mid(100.50) + 0.50
    assert buy.quantity == Decimal("0.01")
    assert sell.quantity == Decimal("0.01")
    print("PASS: symmetric quotes placed at mid +/- half_spread")


def test_no_requote_when_prices_unchanged():
    strat = SymmetricMM(
        half_spread=Decimal("0.50"),
        order_qty=Decimal("0.01"),
        max_position=Decimal("1.0"),
    )
    book = make_book()

    # First call: places orders
    actions1 = strat.on_book_update(book, timestamp_ms=1000)
    orders = [a for a in actions1 if isinstance(a, OrderRequest)]
    assert len(orders) == 2

    # Simulate engine calling on_order_placed
    for req in orders:
        order = make_order(
            f"ord-{req.side.value}", req.side, req.price,
        )
        strat.on_order_placed(req, order)

    # Second call with same book: no actions needed
    actions2 = strat.on_book_update(book, timestamp_ms=2000)
    assert len(actions2) == 0
    print("PASS: no requote when desired prices match current orders")


def test_requote_when_mid_changes():
    strat = SymmetricMM(
        half_spread=Decimal("0.50"),
        order_qty=Decimal("0.01"),
        max_position=Decimal("1.0"),
    )
    book = make_book()

    # Initial quote
    actions1 = strat.on_book_update(book, timestamp_ms=1000)
    for req in [a for a in actions1 if isinstance(a, OrderRequest)]:
        strat.on_order_placed(req, make_order(
            f"ord-{req.side.value}", req.side, req.price,
        ))

    # Mid shifts: new book has mid = (99 + 100) / 2 = 99.50
    book2 = make_book(bids=[("99.00", "5.0")], asks=[("100.00", "3.0")])
    actions2 = strat.on_book_update(book2, timestamp_ms=2000)

    cancels = [a for a in actions2 if isinstance(a, CancelRequest)]
    new_orders = [a for a in actions2 if isinstance(a, OrderRequest)]

    # Should cancel both old orders and place two new ones
    assert len(cancels) == 2
    assert len(new_orders) == 2

    buy = next(o for o in new_orders if o.side == OrderSide.BUY)
    sell = next(o for o in new_orders if o.side == OrderSide.SELL)
    assert buy.price == Decimal("99.00")    # 99.50 - 0.50
    assert sell.price == Decimal("100.00")  # 99.50 + 0.50
    print("PASS: requotes with cancel/replace when mid changes")


def test_requote_after_fill():
    """After a fill, the filled side's order is done. Next book update replaces it."""
    strat = SymmetricMM(
        half_spread=Decimal("0.50"),
        order_qty=Decimal("0.01"),
        max_position=Decimal("1.0"),
    )
    book = make_book()

    # Place initial quotes
    actions = strat.on_book_update(book, timestamp_ms=1000)
    for req in [a for a in actions if isinstance(a, OrderRequest)]:
        order = make_order(f"ord-{req.side.value}", req.side, req.price)
        strat.on_order_placed(req, order)

    # Simulate buy fill -- mark the order as done
    strat._bid_order.status = OrderStatus.FILLED
    fill = make_fill("ord-buy", OrderSide.BUY, "100.00", "0.01")
    strat.on_fill(fill)

    # Next book update: bid order is done, needs replacement.
    # Ask order still active at same price, no change needed.
    actions2 = strat.on_book_update(book, timestamp_ms=2000)
    new_orders = [a for a in actions2 if isinstance(a, OrderRequest)]
    cancels = [a for a in actions2 if isinstance(a, CancelRequest)]

    assert len(new_orders) == 1
    assert new_orders[0].side == OrderSide.BUY
    assert new_orders[0].price == Decimal("100.00")
    assert len(cancels) == 0  # ask is still live at correct price
    print("PASS: only the filled side is replaced on next book update")


# --- inventory tracking ---

def test_inventory_tracks_buys_and_sells():
    strat = SymmetricMM(
        half_spread=Decimal("0.50"),
        order_qty=Decimal("0.01"),
        max_position=Decimal("1.0"),
    )
    assert strat.position == Decimal("0")

    strat.on_fill(make_fill("o1", OrderSide.BUY, "100", "0.5"))
    assert strat.position == Decimal("0.5")

    strat.on_fill(make_fill("o2", OrderSide.BUY, "99", "0.3"))
    assert strat.position == Decimal("0.8")

    strat.on_fill(make_fill("o3", OrderSide.SELL, "101", "0.6"))
    assert strat.position == Decimal("0.2")

    assert strat.fill_count == 3
    print("PASS: position tracks correctly across buys and sells")


def test_realized_pnl_and_fees():
    strat = SymmetricMM(
        half_spread=Decimal("0.50"),
        order_qty=Decimal("0.01"),
        max_position=Decimal("1.0"),
    )
    # Buy 1.0 @ 100 then sell 1.0 @ 101 -> realized PnL = 1.0
    strat.on_fill(make_fill("o1", OrderSide.BUY, "100", "1.0", fee=Decimal("0.02")))
    strat.on_fill(make_fill("o2", OrderSide.SELL, "101", "1.0", fee=Decimal("0.02")))

    assert strat.position == Decimal("0")
    assert strat.realized_pnl == Decimal("1.0")   # sold 101 - bought 100
    assert strat.total_fees == Decimal("0.04")
    print("PASS: realized PnL and fee accumulation correct")


# --- position limits ---

def test_position_limit_blocks_buy_side():
    strat = SymmetricMM(
        half_spread=Decimal("0.50"),
        order_qty=Decimal("0.01"),
        max_position=Decimal("0.5"),
    )
    # Fill to max long
    strat.position = Decimal("0.5")

    book = make_book()
    actions = strat.on_book_update(book, timestamp_ms=1000)

    orders = [a for a in actions if isinstance(a, OrderRequest)]
    sides = {o.side for o in orders}
    assert OrderSide.BUY not in sides      # blocked at max long
    assert OrderSide.SELL in sides          # still quoting
    print("PASS: buy side blocked at max long position")


def test_position_limit_blocks_sell_side():
    strat = SymmetricMM(
        half_spread=Decimal("0.50"),
        order_qty=Decimal("0.01"),
        max_position=Decimal("0.5"),
    )
    # Fill to max short
    strat.position = Decimal("-0.5")

    book = make_book()
    actions = strat.on_book_update(book, timestamp_ms=1000)

    orders = [a for a in actions if isinstance(a, OrderRequest)]
    sides = {o.side for o in orders}
    assert OrderSide.SELL not in sides     # blocked at max short
    assert OrderSide.BUY in sides          # still quoting
    print("PASS: sell side blocked at max short position")


# --- tick rounding ---

def test_tick_rounding():
    strat = SymmetricMM(
        half_spread=Decimal("0.75"),
        order_qty=Decimal("0.01"),
        max_position=Decimal("1.0"),
        tick_size=Decimal("0.10"),
    )
    # mid = 100.50, bid = 100.50 - 0.75 = 99.75 -> round to 99.70
    #                ask = 100.50 + 0.75 = 101.25 -> round to 101.20
    book = make_book()
    actions = strat.on_book_update(book, timestamp_ms=1000)

    orders = [a for a in actions if isinstance(a, OrderRequest)]
    buy = next(o for o in orders if o.side == OrderSide.BUY)
    sell = next(o for o in orders if o.side == OrderSide.SELL)

    assert buy.price == Decimal("99.7") or buy.price == Decimal("99.70")
    assert sell.price == Decimal("101.2") or sell.price == Decimal("101.20")
    print(f"PASS: prices rounded to tick_size=0.10 (bid={buy.price}, ask={sell.price})")


# --- edge cases ---

def test_empty_book_returns_no_actions():
    strat = SymmetricMM(
        half_spread=Decimal("0.50"),
        order_qty=Decimal("0.01"),
        max_position=Decimal("1.0"),
    )
    book = Orderbook()  # empty, no bids or asks
    actions = strat.on_book_update(book, timestamp_ms=1000)
    assert len(actions) == 0
    print("PASS: empty book produces no actions")


def test_on_trade_returns_empty():
    strat = SymmetricMM(
        half_spread=Decimal("0.50"),
        order_qty=Decimal("0.01"),
        max_position=Decimal("1.0"),
    )
    trade = TradeEvent(
        recv_time=datetime(2026, 4, 21),
        exchange_time_ms=1000,
        agg_trade_id=1,
        price=Decimal("100"),
        quantity=Decimal("1.0"),
        is_buyer_maker=True,
    )
    book = make_book()
    actions = strat.on_trade(trade, book)
    assert len(actions) == 0
    print("PASS: on_trade returns no actions (symmetric MM is passive)")


def test_cancel_stale_order_when_side_blocked():
    """If position hits limit and there's still an active order on that side, cancel it."""
    strat = SymmetricMM(
        half_spread=Decimal("0.50"),
        order_qty=Decimal("0.01"),
        max_position=Decimal("0.5"),
    )
    book = make_book()

    # Place initial quotes at neutral position
    actions = strat.on_book_update(book, timestamp_ms=1000)
    for req in [a for a in actions if isinstance(a, OrderRequest)]:
        strat.on_order_placed(req, make_order(
            f"ord-{req.side.value}", req.side, req.price,
        ))

    # Position hits max long -> buy side should be cancelled
    strat.position = Decimal("0.5")
    actions2 = strat.on_book_update(book, timestamp_ms=2000)

    cancels = [a for a in actions2 if isinstance(a, CancelRequest)]
    assert len(cancels) == 1
    assert cancels[0].order_id == "ord-buy"

    # No new buy order placed
    new_orders = [a for a in actions2 if isinstance(a, OrderRequest)]
    buy_orders = [o for o in new_orders if o.side == OrderSide.BUY]
    assert len(buy_orders) == 0
    print("PASS: stale buy order cancelled when position hits max long")


if __name__ == "__main__":
    test_symmetric_quotes_around_mid()
    test_no_requote_when_prices_unchanged()
    test_requote_when_mid_changes()
    test_requote_after_fill()
    test_inventory_tracks_buys_and_sells()
    test_realized_pnl_and_fees()
    test_position_limit_blocks_buy_side()
    test_position_limit_blocks_sell_side()
    test_tick_rounding()
    test_empty_book_returns_no_actions()
    test_on_trade_returns_empty()
    test_cancel_stale_order_when_side_blocked()
    print("\nAll tests passed.")
