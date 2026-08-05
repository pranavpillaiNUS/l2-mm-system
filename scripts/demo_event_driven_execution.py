"""Run a deterministic event-driven fill/cancel race without external data."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.execution.order import OrderRequest, OrderSide, OrderType
from src.execution.simulator import ExecutionSimulator, SimConfig
from src.replay.orderbook import Orderbook
from src.replay.trade_parser import TradeEvent


def run_demo() -> dict:
    """Return a compact, reproducible lifecycle for documentation and smoke use."""
    book = Orderbook()
    book.apply_snapshot(
        bids=[("100.00", "1.000")],
        asks=[("100.10", "1.000")],
        last_update_id=1,
    )
    simulator = ExecutionSimulator(
        SimConfig(
            base_latency_ms=5,
            jitter_ms=0,
            cancel_latency_ms=5,
            cancel_jitter_ms=0,
            maker_bps=2,
            taker_bps=5,
            queue_cancellation_credit="0",
            seed=7,
        )
    )

    order = simulator.submit(
        OrderRequest(
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("100.00"),
            quantity=Decimal("0.001"),
        ),
        current_time_ms=0,
    )
    arrival = simulator.process_next_scheduled(book)
    if arrival.timestamp_ms != 5:
        raise RuntimeError("unexpected synthetic order-arrival time")

    if not simulator.cancel(order.order_id, current_time_ms=6):
        raise RuntimeError("synthetic cancellation request was rejected")

    fills = simulator.on_trade(
        TradeEvent(
            recv_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            exchange_time_ms=10,
            agg_trade_id=1,
            price=Decimal("100.00"),
            quantity=Decimal("1.001"),
            is_buyer_maker=True,
        ),
        book,
    )
    cancellation = simulator.process_next_scheduled(book)

    return {
        "execution_model": simulator.config.provenance["execution_model_version"],
        "equal_timestamp_policy": simulator.config.provenance["equal_timestamp_policy"],
        "order_id": order.order_id,
        "final_status": order.status.value,
        "fill_count": len(fills),
        "fill_quantity": str(sum((fill.quantity for fill in fills), Decimal("0"))),
        "maker_fee": format(
            sum((fill.fee for fill in fills), Decimal("0")).normalize(), "f"
        ),
        "cancel_too_late": cancellation.cancels_too_late,
        "lifecycle": [
            f"{event.timestamp_ms}ms:{event.event_type}"
            for event in simulator.events
        ],
    }


def main() -> None:
    result = run_demo()
    print(f"Execution model: {result['execution_model']}")
    print(f"Timestamp policy: {result['equal_timestamp_policy']}")
    print(f"Order: {result['order_id']} -> {result['final_status']}")
    print(f"Maker fill: {result['fill_quantity']} BTC; fee={result['maker_fee']} USDT")
    print(f"Cancel too late: {result['cancel_too_late']}")
    print("Lifecycle:")
    for event in result["lifecycle"]:
        print(f"  {event}")


if __name__ == "__main__":
    main()
