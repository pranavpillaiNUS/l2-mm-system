"""
Quick diagnostic: where are the taker fills coming from?
Run on 1 hour of data, print every taker fill with context.
"""
import sys
sys.path.insert(0, ".")

from pathlib import Path
from decimal import Decimal
from src.replay.engine import ReplayEngine, ReplayConfig
from src.execution.simulator import SimConfig
from src.strategies.symmetric_mm import SymmetricMM

# find files for a specific hour
depth_dir = Path("data/raw/btcusdt")
trade_dir = Path("data/raw/btcusdt_trades")

# use a date where we have both depth and trades
# adjust this to match your data
date_str = "20260416"
hour_str = "12"
pattern = f"*{date_str}_{hour_str}*"

depth_files = sorted(depth_dir.glob(pattern))
trade_files = sorted(trade_dir.glob(pattern))

print(f"Depth files: {len(depth_files)}")
print(f"Trade files: {len(trade_files)}")

if not depth_files:
    print("No depth files found, check the date/hour")
    sys.exit(1)

config = ReplayConfig(
    depth_files=depth_files,
    trade_files=trade_files,
    sim_config=SimConfig(
        base_latency_ms=10,
        jitter_ms=0,
        maker_bps=2,
        taker_bps=5,
        queue_cancellation_credit="1.0",
    ),
)

strategy = SymmetricMM(
    half_spread=Decimal("5.00"),
    order_qty=Decimal("0.001"),
    max_position=Decimal("0.01"),
    tick_size=Decimal("0.01"),
    requote_interval_ms=5000,
)

engine = ReplayEngine(config)
result = engine.run(strategy)

# Exact-arrival diagnostics come from lifecycle events. ReplayEngine no longer
# quantizes activation through the direct on_book_update convenience wrapper.
queued_events = [event for event in result.events if event.event_type == "queued"]
aggressive_events = [
    event for event in result.events
    if event.event_type == "cancelled"
    and event.detail.get("reason") == "post_only_would_cross"
]
resting_count = len(queued_events)
aggressive_count = len(aggressive_events)
aggressive_details = []
for event in aggressive_events:
    order = engine.sim._orders[event.order_id]
    aggressive_details.append({
        "order_id": order.order_id,
        "side": order.side.value,
        "price": str(order.price),
        "best_bid": event.detail.get("best_bid"),
        "best_ask": event.detail.get("best_ask"),
        "placed_ms": order.placed_time_ms,
        "arrived_ms": event.timestamp_ms,
    })

# summarize
maker_fills = [f for f in result.fills if f.is_maker]
taker_fills = [f for f in result.fills if not f.is_maker]

print(f"\n--- Fill Summary ---")
print(f"Total fills: {len(result.fills)}")
print(f"Maker fills: {len(maker_fills)}")
print(f"Taker fills: {len(taker_fills)}")
print(f"Maker %: {len(maker_fills)/len(result.fills)*100:.1f}%" if result.fills else "No fills")

print(f"\n--- Order Activation Summary ---")
print(f"Orders activated as resting: {resting_count}")
print(f"Orders activated as aggressive: {aggressive_count}")

if aggressive_details:
    print(f"\n--- First 20 Aggressive Orders ---")
    for d in aggressive_details[:20]:
        print(f"  {d['order_id']}: {d['side']} @ {d['price']}, "
              f"best_bid={d['best_bid']}, best_ask={d['best_ask']}, "
              f"placed={d['placed_ms']}, arrived={d['arrived_ms']}")
else:
    print("\nNo aggressive orders detected!")
    print("Taker fills must be coming from somewhere else.")
    
    if taker_fills:
        print(f"\n--- First 20 Taker Fills ---")
        for f in taker_fills[:20]:
            order = engine.sim._orders.get(f.order_id)
            print(f"  {f.fill_id}: {f.side.value} @ {f.price}, qty={f.quantity}, "
                  f"order={f.order_id}, ts={f.timestamp_ms}")
            if order:
                print(f"    order price={order.price}, status={order.status.value}, "
                      f"placed={order.placed_time_ms}, arrived={order.arrival_time_ms}")
