"""
Tests for ExecutionSimulator.

Run with: python tests/test_execution_simulator.py
"""
from decimal import Decimal
from datetime import datetime

from src.execution.order import OrderRequest, OrderSide, OrderStatus, OrderType
from src.execution.queue_credit import (
    credit_from_legacy_mode,
    legacy_mode_from_credit,
)
from src.execution.simulator import ExecutionSimulator, SimConfig
from src.replay.orderbook import Orderbook
from src.replay.trade_parser import TradeEvent


# --- helpers ---

def make_sim(
    base_latency_ms=10,
    jitter_ms=0,
    maker_bps=2,
    taker_bps=5,
    seed=42,
    post_only=True,
    queue_cancellation_mode="proportional",
):
    return ExecutionSimulator(SimConfig(
        base_latency_ms=base_latency_ms,
        jitter_ms=jitter_ms,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        seed=seed,
        post_only=post_only,
        queue_cancellation_mode=queue_cancellation_mode,
    ))


def make_book(bids=None, asks=None):
    book = Orderbook()
    book.apply_snapshot(
        bids=bids or [("100.00", "5.0"), ("99.00", "10.0")],
        asks=asks or [("101.00", "3.0"), ("102.00", "8.0")],
        last_update_id=1000,
    )
    return book


def make_trade(price, qty, is_buyer_maker, t=2000):
    return TradeEvent(
        recv_time=datetime(2026, 4, 21),
        exchange_time_ms=t,
        agg_trade_id=1,
        price=Decimal(price),
        quantity=Decimal(qty),
        is_buyer_maker=is_buyer_maker,
    )


def buy_limit(price, qty="0.5"):
    return OrderRequest(
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal(qty),
        price=Decimal(price),
    )


def sell_limit(price, qty="0.5"):
    return OrderRequest(
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        quantity=Decimal(qty),
        price=Decimal(price),
    )


def buy_market(qty="0.5"):
    return OrderRequest(side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal(qty))


def sell_market(qty="0.5"):
    return OrderRequest(side=OrderSide.SELL, order_type=OrderType.MARKET, quantity=Decimal(qty))


# --- tests ---

def test_submit_creates_pending_order():
    sim = make_sim(base_latency_ms=10, jitter_ms=0)
    book = make_book()
    order = sim.submit(buy_limit("100"), current_time_ms=1000)

    assert order.status == OrderStatus.PENDING
    assert order.placed_time_ms == 1000
    assert order.arrival_time_ms == 1010
    assert order.side == OrderSide.BUY
    assert order.order_type == OrderType.LIMIT
    assert order.price == Decimal("100")
    print("PASS: submit creates PENDING order with correct arrival time")


def test_order_not_activated_before_arrival():
    sim = make_sim(base_latency_ms=10, jitter_ms=0)
    book = make_book()
    order = sim.submit(buy_limit("100"), current_time_ms=1000)

    sim.on_book_update(book, timestamp_ms=1005)  # before arrival at 1010
    assert order.status == OrderStatus.PENDING
    print("PASS: order stays PENDING before arrival time")


def test_limit_order_activates_at_arrival():
    sim = make_sim(base_latency_ms=10, jitter_ms=0)
    book = make_book(bids=[("100.00", "5.0")])
    order = sim.submit(buy_limit("100"), current_time_ms=1000)

    sim.on_book_update(book, timestamp_ms=1010)
    assert order.status == OrderStatus.ACTIVE
    assert order.queue_ahead == Decimal("5.0")
    print("PASS: limit order activates at arrival, queue_ahead = book qty at price")


def test_jitter_is_deterministic():
    sim1 = make_sim(base_latency_ms=10, jitter_ms=5, seed=42)
    sim2 = make_sim(base_latency_ms=10, jitter_ms=5, seed=42)

    order1 = sim1.submit(buy_limit("100"), current_time_ms=1000)
    order2 = sim2.submit(buy_limit("100"), current_time_ms=1000)

    assert order1.arrival_time_ms == order2.arrival_time_ms
    print(f"PASS: same seed → same arrival time ({order1.arrival_time_ms}ms)")


def test_jitter_different_seeds():
    sim1 = make_sim(jitter_ms=100, seed=1)
    sim2 = make_sim(jitter_ms=100, seed=999)

    arrivals1 = [sim1.submit(buy_limit("100"), t).arrival_time_ms for t in range(0, 500, 10)]
    arrivals2 = [sim2.submit(buy_limit("100"), t).arrival_time_ms for t in range(0, 500, 10)]

    assert arrivals1 != arrivals2
    print("PASS: different seeds → different jitter sequences")


def test_trade_drains_queue_ahead():
    sim = make_sim()
    book = make_book(bids=[("100.00", "5.0")])
    order = sim.submit(buy_limit("100"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)
    assert order.queue_ahead == Decimal("5.0")

    # market sell hits our bid level: drains 2.0 from queue
    trade = make_trade("100", "2.0", is_buyer_maker=True, t=1020)
    sim.on_trade(trade, book)

    assert order.queue_ahead == Decimal("3.0")
    assert order.status == OrderStatus.ACTIVE  # not filled yet
    assert len(sim.fills) == 0
    print("PASS: trade at limit price drains queue_ahead")


def test_fill_when_queue_exhausted():
    sim = make_sim()
    book = make_book(bids=[("100.00", "2.0")])
    order = sim.submit(buy_limit("100", qty="0.5"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)
    assert order.queue_ahead == Decimal("2.0")

    # trade for 3.0: drains 2.0 queue, 1.0 left over fills our 0.5 order
    trade = make_trade("100", "3.0", is_buyer_maker=True, t=1020)
    sim.on_trade(trade, book)

    assert order.status == OrderStatus.FILLED
    assert len(sim.fills) == 1
    assert sim.fills[0].quantity == Decimal("0.5")
    assert sim.fills[0].price == Decimal("100")
    assert sim.fills[0].is_maker is True
    print("PASS: order fills when trade exhausts queue_ahead")


def test_queue_diagnostics_logged_without_changing_fill():
    sim = make_sim()
    book = make_book(bids=[("100.00", "2.0")])
    order = sim.submit(buy_limit("100", qty="0.5"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)

    fills = sim.on_trade(make_trade("100", "3.0", is_buyer_maker=True, t=1020), book)

    assert len(fills) == 1
    assert fills[0].quantity == Decimal("0.5")
    assert fills[0].price == Decimal("100")

    queue_events = [
        e for e in sim.events
        if e.order_id == order.order_id and e.event_type == "queue_drain"
    ]
    assert len(queue_events) == 1
    assert queue_events[0].detail["reason"] == "trade"
    assert queue_events[0].detail["drained_qty"] == "2.0"
    assert queue_events[0].detail["queue_before"] == "2.0"
    assert queue_events[0].detail["queue_after"] == "0.0"

    filled_event = [
        e for e in sim.events
        if e.order_id == order.order_id and e.event_type == "filled"
    ][0]
    assert filled_event.detail["queue_ahead_before_trade"] == "2.0"
    assert filled_event.detail["queue_ahead_before_fill"] == "0.0"
    assert filled_event.detail["fill_qty"] == "0.5"
    print("PASS: queue diagnostics are logged without changing fill behavior")


def test_partial_fill():
    sim = make_sim()
    book = make_book(bids=[("100.00", "0.0")])  # no queue ahead
    order = sim.submit(buy_limit("100", qty="1.0"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)
    assert order.queue_ahead == Decimal("0.0")

    # trade for 0.3 — only partially fills our 1.0 order
    sim.on_trade(make_trade("100", "0.3", is_buyer_maker=True, t=1020), book)
    assert order.status == OrderStatus.PARTIAL
    assert order.filled_quantity == Decimal("0.3")
    assert order.remaining_quantity == Decimal("0.7")
    assert len(sim.fills) == 1

    # trade for 0.7 — fills the rest
    sim.on_trade(make_trade("100", "0.7", is_buyer_maker=True, t=1030), book)
    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == Decimal("1.0")
    assert len(sim.fills) == 2
    print("PASS: partial fill then full fill across two trades")


def test_market_order_fills_at_best_ask():
    sim = make_sim()
    book = make_book(asks=[("101.00", "5.0")])
    order = sim.submit(buy_market("0.5"), current_time_ms=1000)

    fills = sim.on_book_update(book, timestamp_ms=1010)

    assert order.status == OrderStatus.FILLED
    assert len(fills) == 1
    assert fills[0].price == Decimal("101.00")
    assert fills[0].quantity == Decimal("0.5")
    assert fills[0].is_maker is False
    print("PASS: market buy fills at best ask as taker")


def test_market_order_walks_levels():
    sim = make_sim()
    # ask side: 0.3 @ 101, 0.5 @ 102 — need 0.7 total
    book = make_book(asks=[("101.00", "0.3"), ("102.00", "0.5")])
    order = sim.submit(buy_market("0.7"), current_time_ms=1000)

    fills = sim.on_book_update(book, timestamp_ms=1010)

    assert order.status == OrderStatus.FILLED
    assert len(fills) == 2
    assert fills[0].price == Decimal("101.00")
    assert fills[0].quantity == Decimal("0.3")
    assert fills[1].price == Decimal("102.00")
    assert fills[1].quantity == Decimal("0.4")
    print("PASS: market order walks levels to fill completely")


def test_market_order_cancelled_if_insufficient_liquidity():
    sim = make_sim()
    # only 0.2 available, order is for 1.0
    book = make_book(asks=[("101.00", "0.2")])
    order = sim.submit(buy_market("1.0"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)

    assert order.status == OrderStatus.CANCELLED
    assert order.filled_quantity == Decimal("0.2")
    print("PASS: market order cancelled when book has insufficient liquidity")


def test_post_only_aggressive_limit_is_cancelled():
    sim = make_sim()
    # best ask = 101, limit buy at 102 would cross spread.
    # With post_only=True (default), the exchange rejects it instead of
    # allowing a taker fill.
    book = make_book(asks=[("101.00", "5.0")])
    order = sim.submit(buy_limit("102", qty="0.5"), current_time_ms=1000)

    sim.on_book_update(book, timestamp_ms=1010)

    assert order.status == OrderStatus.CANCELLED
    assert len(sim.fills) == 0
    assert sim.postonly_rejects == 1
    print("PASS: post-only aggressive limit is cancelled, not filled as taker")


def test_aggressive_limit_can_fill_as_taker_when_post_only_disabled():
    sim = make_sim(post_only=False)
    # best ask = 101, limit buy at 102 → crosses spread → taker fill at 101
    book = make_book(asks=[("101.00", "5.0")])
    order = sim.submit(buy_limit("102", qty="0.5"), current_time_ms=1000)

    sim.on_book_update(book, timestamp_ms=1010)

    assert order.status == OrderStatus.FILLED
    assert len(sim.fills) == 1
    assert sim.fills[0].price == Decimal("101.00")   # filled at ask, not at 102
    assert sim.fills[0].is_maker is False              # taker fee applies
    print("PASS: aggressive limit can fill as taker when post-only is disabled")


def test_sell_limit_fills_on_market_buy():
    sim = make_sim()
    # our SELL limit at 101, a market buy comes in (is_buyer_maker=False)
    book = make_book(asks=[("101.00", "0.0")])  # no queue ahead
    order = sim.submit(sell_limit("101", qty="0.5"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)

    # market buy (is_buyer_maker=False) → hits asks → our SELL limit fills
    trade = make_trade("101", "1.0", is_buyer_maker=False, t=1020)
    sim.on_trade(trade, book)

    assert order.status == OrderStatus.FILLED
    assert sim.fills[0].side == OrderSide.SELL
    assert sim.fills[0].is_maker is True
    print("PASS: sell limit fills when market buy hits that price")


def test_wrong_side_trade_does_not_fill():
    sim = make_sim()
    # BUY limit at 100, but the trade is a market buy (is_buyer_maker=False → hits asks)
    book = make_book(bids=[("100.00", "0.0")])
    order = sim.submit(buy_limit("100", qty="0.5"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)

    # market buy hits asks, not bids — our BUY limit should NOT fill
    trade = make_trade("100", "1.0", is_buyer_maker=False, t=1020)
    sim.on_trade(trade, book)

    assert order.status == OrderStatus.ACTIVE
    assert len(sim.fills) == 0
    print("PASS: market buy does not fill a resting bid limit")


def test_cancel_pending_order():
    sim = make_sim()
    order = sim.submit(buy_limit("100"), current_time_ms=1000)
    assert order.status == OrderStatus.PENDING

    result = sim.cancel(order.order_id, current_time_ms=1005)
    assert result is True
    assert order.status == OrderStatus.CANCELLED
    print("PASS: can cancel a PENDING order before it arrives")


def test_cancel_active_order():
    sim = make_sim()
    book = make_book(bids=[("100.00", "5.0")])
    order = sim.submit(buy_limit("100"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)
    assert order.status == OrderStatus.ACTIVE

    result = sim.cancel(order.order_id, current_time_ms=1020)
    assert result is True
    assert order.status == OrderStatus.CANCELLED
    print("PASS: can cancel an ACTIVE order")


def test_cancel_filled_order_fails():
    sim = make_sim()
    book = make_book(bids=[("100.00", "0.0")])
    order = sim.submit(buy_limit("100", qty="0.5"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)
    sim.on_trade(make_trade("100", "1.0", is_buyer_maker=True, t=1020), book)
    assert order.status == OrderStatus.FILLED

    result = sim.cancel(order.order_id, current_time_ms=1030)
    assert result is False
    assert order.status == OrderStatus.FILLED  # unchanged
    print("PASS: cannot cancel a FILLED order")


def test_cancellation_reduces_queue_ahead():
    sim = make_sim()
    book = make_book(bids=[("100.00", "10.0")])
    order = sim.submit(buy_limit("100", qty="0.5"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)
    assert order.queue_ahead == Decimal("10.0")

    # Book qty at 100 drops from 10.0 to 7.0 with no trades — must be cancellations
    book.apply_diff(bids=[("100.00", "7.0")], asks=[], last_update_id=1001)
    sim.on_book_update(book, timestamp_ms=1020)

    # cancel_frac = 3.0 / 10.0 = 0.30 → queue_ahead = 10.0 * 0.70 = 7.0
    expected = Decimal("10.0") * Decimal("0.7")
    assert order.queue_ahead == expected

    queue_events = [
        e for e in sim.events
        if e.order_id == order.order_id and e.event_type == "queue_drain"
    ]
    assert len(queue_events) == 1
    assert queue_events[0].detail["reason"] == "cancellation"
    assert Decimal(queue_events[0].detail["drained_qty"]) == Decimal("3.00")
    assert Decimal(queue_events[0].detail["queue_after"]) == expected
    print(f"PASS: cancellation proportionally reduces queue_ahead ({order.queue_ahead})")


def test_cancellation_ignores_traded_volume():
    sim = make_sim()
    book = make_book(bids=[("100.00", "10.0")])
    order = sim.submit(buy_limit("100", qty="0.5"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)

    # Trade for 2.0 (drains queue by 2.0, tracked in _traded_since_depth)
    sim.on_trade(make_trade("100", "2.0", is_buyer_maker=True, t=1015), book)
    assert order.queue_ahead == Decimal("8.0")

    # Book qty drops from 10.0 to 5.0: decrease = 5.0, traded = 2.0, cancellation = 3.0
    # cancel_frac = 3.0 / 10.0 = 0.30
    # new queue_ahead = 8.0 * 0.70 = 5.6
    book.apply_diff(bids=[("100.00", "5.0")], asks=[], last_update_id=1001)
    sim.on_book_update(book, timestamp_ms=1020)

    expected = Decimal("8.0") * Decimal("0.7")
    assert order.queue_ahead == expected
    print(f"PASS: cancellation excludes already-traded volume (queue_ahead={order.queue_ahead})")


def test_no_cancellation_credit_mode_preserves_queue_ahead():
    sim = make_sim(queue_cancellation_mode="none")
    book = make_book(bids=[("100.00", "10.0")])
    order = sim.submit(buy_limit("100", qty="0.5"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)

    book.apply_diff(bids=[("100.00", "7.0")], asks=[], last_update_id=1001)
    sim.on_book_update(book, timestamp_ms=1020)

    assert order.queue_ahead == Decimal("10.0")
    queue_events = [
        e for e in sim.events
        if e.order_id == order.order_id and e.event_type == "queue_drain"
    ]
    assert queue_events == []
    print("PASS: none queue mode grants no cancellation-driven queue credit")


def test_no_cancellation_credit_does_not_improve_fills():
    def run(mode):
        sim = make_sim(queue_cancellation_mode=mode)
        book = make_book(bids=[("100.00", "1.0")])
        order = sim.submit(buy_limit("100", qty="0.5"), current_time_ms=1000)
        sim.on_book_update(book, timestamp_ms=1010)
        book.apply_diff(bids=[("100.00", "0.0")], asks=[], last_update_id=1001)
        sim.on_book_update(book, timestamp_ms=1020)
        sim.on_trade(make_trade("100", "0.5", is_buyer_maker=True, t=1030), book)
        return order, sim.fills

    proportional_order, proportional_fills = run("proportional")
    none_order, none_fills = run("none")

    assert proportional_order.status == OrderStatus.FILLED
    assert len(proportional_fills) == 1
    assert none_order.status == OrderStatus.ACTIVE
    assert len(none_fills) == 0
    assert len(none_fills) <= len(proportional_fills)
    print("PASS: conservative queue mode does not improve fills")


def test_partial_queue_cancellation_credit_scales_queue_improvement():
    sim = ExecutionSimulator(SimConfig(
        base_latency_ms=10,
        jitter_ms=0,
        maker_bps=2,
        taker_bps=5,
        queue_cancellation_credit=Decimal("0.5"),
    ))
    book = make_book(bids=[("100.00", "10.0")])
    order = sim.submit(buy_limit("100", qty="0.5"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)

    book.apply_diff(bids=[("100.00", "7.0")], asks=[], last_update_id=1001)
    sim.on_book_update(book, timestamp_ms=1020)

    # Full proportional credit would reduce queue from 10 to 7. Half credit
    # applies half the cancellation fraction, so queue becomes 8.5.
    assert order.queue_ahead == Decimal("8.50")
    print("PASS: queue_cancellation_credit scales cancellation-driven queue credit")


def test_legacy_queue_mode_mapping_is_explicit():
    assert credit_from_legacy_mode("proportional") == Decimal("1.0")
    assert credit_from_legacy_mode("none") == Decimal("0.0")
    assert legacy_mode_from_credit(Decimal("1.0")) == "proportional"
    assert legacy_mode_from_credit(Decimal("0.0")) == "none"
    assert legacy_mode_from_credit(Decimal("0.5")) is None
    print("PASS: legacy queue labels map only to endpoint credits")


def test_fill_produces_correct_maker_fee():
    sim = make_sim(maker_bps=2, taker_bps=5)
    book = make_book(bids=[("100.00", "0.0")])
    order = sim.submit(buy_limit("100", qty="1.0"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)
    sim.on_trade(make_trade("100", "1.0", is_buyer_maker=True, t=1020), book)

    fill = sim.fills[0]
    assert fill.is_maker is True
    # fee = qty * price * maker_rate = 1.0 * 100 * 0.0002 = 0.02
    expected_fee = Decimal("1.0") * Decimal("100") * (Decimal(2) / Decimal(10000))
    assert fill.fee == expected_fee
    print(f"PASS: maker fill fee = {fill.fee} (expected {expected_fee})")


def test_fill_produces_correct_taker_fee():
    sim = make_sim(maker_bps=2, taker_bps=5)
    book = make_book(asks=[("101.00", "1.0")])
    order = sim.submit(buy_market("1.0"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)

    fill = sim.fills[0]
    assert fill.is_maker is False
    # fee = 1.0 * 101 * 0.0005 = 0.0505
    expected_fee = Decimal("1.0") * Decimal("101") * (Decimal(5) / Decimal(10000))
    assert fill.fee == expected_fee
    print(f"PASS: taker fill fee = {fill.fee} (expected {expected_fee})")


def test_order_event_log():
    sim = make_sim()
    book = make_book(bids=[("100.00", "0.0")])
    order = sim.submit(buy_limit("100", qty="0.5"), current_time_ms=1000)
    sim.on_book_update(book, timestamp_ms=1010)
    sim.on_trade(make_trade("100", "1.0", is_buyer_maker=True, t=1020), book)

    event_types = [e.event_type for e in sim.events if e.order_id == order.order_id]
    assert event_types == ["placed", "arrived", "queued", "filled"]
    print(f"PASS: order lifecycle events in correct order: {event_types}")


def test_determinism():
    def run_scenario(seed):
        sim = make_sim(jitter_ms=5, seed=seed)
        book = make_book(bids=[("100.00", "2.0")])
        order = sim.submit(buy_limit("100", qty="0.5"), current_time_ms=1000)
        sim.on_book_update(book, timestamp_ms=1020)
        sim.on_trade(make_trade("100", "3.0", is_buyer_maker=True, t=1025), book)
        return [(f.fill_id, str(f.price), str(f.quantity), f.is_maker) for f in sim.fills]

    run1 = run_scenario(seed=42)
    run2 = run_scenario(seed=42)
    assert run1 == run2
    print("PASS: identical seed + inputs → identical fills (determinism)")


if __name__ == "__main__":
    test_submit_creates_pending_order()
    test_order_not_activated_before_arrival()
    test_limit_order_activates_at_arrival()
    test_jitter_is_deterministic()
    test_jitter_different_seeds()
    test_trade_drains_queue_ahead()
    test_fill_when_queue_exhausted()
    test_queue_diagnostics_logged_without_changing_fill()
    test_partial_fill()
    test_market_order_fills_at_best_ask()
    test_market_order_walks_levels()
    test_market_order_cancelled_if_insufficient_liquidity()
    test_post_only_aggressive_limit_is_cancelled()
    test_aggressive_limit_can_fill_as_taker_when_post_only_disabled()
    test_sell_limit_fills_on_market_buy()
    test_wrong_side_trade_does_not_fill()
    test_cancel_pending_order()
    test_cancel_active_order()
    test_cancel_filled_order_fails()
    test_cancellation_reduces_queue_ahead()
    test_cancellation_ignores_traded_volume()
    test_no_cancellation_credit_mode_preserves_queue_ahead()
    test_no_cancellation_credit_does_not_improve_fills()
    test_fill_produces_correct_maker_fee()
    test_fill_produces_correct_taker_fee()
    test_order_event_log()
    test_determinism()
    print("\nAll tests passed.")
