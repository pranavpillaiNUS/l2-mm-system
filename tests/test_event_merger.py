"""
Tests for EventMerger.

Run with: python tests/test_event_merger.py
"""
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.replay.depth_parser import DepthEvent, DepthParser
from src.replay.trade_parser import TradeEvent, TradeParser
from src.replay.event_merger import EventMerger


# --- helpers ---

def make_depth(exchange_time_ms: int, last_update_id: int = 1) -> DepthEvent:
    return DepthEvent(
        recv_time=datetime(2026, 4, 21, 19, 0, 0),
        exchange_time_ms=exchange_time_ms,
        event_type="diff",
        first_update_id=last_update_id,
        last_update_id=last_update_id,
        bids=[["100.00", "1.0"]],
        asks=[["101.00", "1.0"]],
    )


def make_trade(exchange_time_ms: int, agg_trade_id: int = 1) -> TradeEvent:
    return TradeEvent(
        recv_time=datetime(2026, 4, 21, 19, 0, 0),
        exchange_time_ms=exchange_time_ms,
        agg_trade_id=agg_trade_id,
        price=Decimal("100.50"),
        quantity=Decimal("0.1"),
        is_buyer_maker=False,
    )


# --- tests ---

def test_disjoint_streams_sorted_by_timestamp():
    # depth at 100, 300, trades at 200, 400 - interleaved by time
    depth = [make_depth(100), make_depth(300)]
    trades = [make_trade(200), make_trade(400)]

    events = list(EventMerger(iter(depth), iter(trades)).events())

    assert events[0].exchange_time_ms == 100
    assert events[1].exchange_time_ms == 200
    assert events[2].exchange_time_ms == 300
    assert events[3].exchange_time_ms == 400
    print("PASS: disjoint streams interleaved correctly by timestamp")


def test_depth_before_trade_on_equal_timestamp():
    # both at t=500 - depth must come first
    depth = [make_depth(500)]
    trades = [make_trade(500)]

    events = list(EventMerger(iter(depth), iter(trades)).events())

    assert len(events) == 2
    assert isinstance(events[0], DepthEvent)
    assert isinstance(events[1], TradeEvent)
    print("PASS: depth comes before trade when timestamps are equal")


def test_multiple_ties_at_same_timestamp():
    # two depths and two trades all at t=1000
    depth = [make_depth(1000, last_update_id=1), make_depth(1000, last_update_id=2)]
    trades = [make_trade(1000, agg_trade_id=1), make_trade(1000, agg_trade_id=2)]

    events = list(EventMerger(iter(depth), iter(trades)).events())

    assert len(events) == 4
    # all depths must appear before all trades
    assert all(isinstance(e, DepthEvent) for e in events[:2])
    assert all(isinstance(e, TradeEvent) for e in events[2:])
    print("PASS: all depth events before all trade events when timestamps all equal")


def test_empty_trade_stream():
    depth = [make_depth(100), make_depth(200)]
    events = list(EventMerger(iter(depth), iter([])).events())

    assert len(events) == 2
    assert all(isinstance(e, DepthEvent) for e in events)
    print("PASS: empty trade stream - only depth events emitted")


def test_empty_depth_stream():
    trades = [make_trade(100), make_trade(200)]
    events = list(EventMerger(iter([]), iter(trades)).events())

    assert len(events) == 2
    assert all(isinstance(e, TradeEvent) for e in events)
    print("PASS: empty depth stream - only trade events emitted")


def test_both_streams_empty():
    events = list(EventMerger(iter([]), iter([])).events())
    assert events == []
    print("PASS: both streams empty - no events emitted")


def test_output_is_globally_sorted():
    # stress test: arbitrary timestamps, verify output is monotonically non-decreasing
    depth = [make_depth(t) for t in [10, 30, 50, 70, 90]]
    trades = [make_trade(t) for t in [20, 40, 60, 80, 100]]

    events = list(EventMerger(iter(depth), iter(trades)).events())

    timestamps = [e.exchange_time_ms for e in events]
    assert timestamps == sorted(timestamps)
    print(f"PASS: output is globally sorted - {timestamps}")


def test_event_types_preserved():
    depth = [make_depth(100)]
    trades = [make_trade(200)]

    events = list(EventMerger(iter(depth), iter(trades)).events())

    assert isinstance(events[0], DepthEvent)
    assert isinstance(events[1], TradeEvent)
    print("PASS: event types preserved through merge")


def test_rejects_nonmonotone_depth_stream():
    depth = [make_depth(200), make_depth(100)]

    with pytest.raises(ValueError, match="depth stream is not monotone"):
        list(EventMerger(iter(depth), iter([])).events())


def test_rejects_nonmonotone_trade_stream():
    trades = [make_trade(200), make_trade(100)]

    with pytest.raises(ValueError, match="trade stream is not monotone"):
        list(EventMerger(iter([]), iter(trades)).events())


def test_smoke_on_real_files():
    depth_file = Path("data/raw/btcusdt/btcusdt_depth_20260421_1900.jsonl.gz")
    trade_file = Path("data/raw/btcusdt_trades/btcusdt_trades_20260421_1900.jsonl.gz")

    if not depth_file.exists() or not trade_file.exists():
        pytest.skip("raw merger smoke-test files are not available")

    depth_parser = DepthParser([depth_file])
    trade_parser = TradeParser([trade_file])
    merger = EventMerger(depth_parser.events(), trade_parser.events())

    events = list(merger.events())
    timestamps = [e.exchange_time_ms for e in events]

    # globally sorted
    assert timestamps == sorted(timestamps), "merged stream is not sorted"

    # count types
    n_depth = sum(1 for e in events if isinstance(e, DepthEvent))
    n_trade = sum(1 for e in events if isinstance(e, TradeEvent))

    # check tiebreaker held: whenever depth and trade share a timestamp,
    # the depth should appear first
    violations = 0
    for i in range(len(events) - 1):
        if (isinstance(events[i], TradeEvent)
                and isinstance(events[i + 1], DepthEvent)
                and events[i].exchange_time_ms == events[i + 1].exchange_time_ms):
            violations += 1

    assert violations == 0, f"{violations} tiebreaker violation(s) found"
    print(f"PASS: real files - {len(events)} events ({n_depth} depth, {n_trade} trade), "
          f"sorted, 0 tiebreaker violations")


if __name__ == "__main__":
    test_disjoint_streams_sorted_by_timestamp()
    test_depth_before_trade_on_equal_timestamp()
    test_multiple_ties_at_same_timestamp()
    test_empty_trade_stream()
    test_empty_depth_stream()
    test_both_streams_empty()
    test_output_is_globally_sorted()
    test_event_types_preserved()
    test_rejects_nonmonotone_depth_stream()
    test_rejects_nonmonotone_trade_stream()
    test_smoke_on_real_files()
    print("\nAll tests passed.")
