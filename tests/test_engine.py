"""
Tests for ReplayEngine.

Run with: python tests/test_engine.py
"""
import gzip
import json
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List

from src.execution.order import (
    Fill, Order, OrderRequest, OrderSide, OrderStatus, OrderType,
)
from src.execution.simulator import SimConfig
from src.replay.engine import (
    Action, CancelRequest, ReplayConfig, ReplayEngine, ReplayResult,
)
from src.replay.orderbook import Orderbook
from src.replay.trade_parser import TradeEvent


# --- file builders ---

# Snapshot exchange_time_ms is derived from recv_time. Use a fixed UTC
# timestamp so the test is timezone-independent.
_BASE_RECV = "2026-04-21T00:00:00+00:00"
_BASE_MS = int(datetime.fromisoformat(_BASE_RECV).timestamp() * 1000)


def _write_gz(path, records):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _snapshot(recv_time, last_update_id, bids, asks):
    return {
        "recv_time": recv_time,
        "type": "snapshot",
        "data": {"lastUpdateId": last_update_id, "bids": bids, "asks": asks},
    }


def _diff(recv_time, E, U, u, bids, asks):
    return {
        "recv_time": recv_time,
        "data": {"E": E, "U": U, "u": u, "b": bids, "a": asks},
    }


def _trade(recv_time, T, agg_id, price, qty, m):
    return {
        "recv_time": recv_time,
        "data": {"T": T, "a": agg_id, "p": price, "q": qty, "m": m},
    }


def _sim_config(**overrides):
    defaults = dict(base_latency_ms=10, jitter_ms=0, maker_bps=2, taker_bps=5, seed=42)
    defaults.update(overrides)
    return SimConfig(**defaults)


# --- test strategies ---

class NullStrategy:
    """Does nothing. For testing engine mechanics without strategy logic."""

    def on_book_update(self, book, timestamp_ms):
        return []

    def on_trade(self, trade, book):
        return []

    def on_fill(self, fill):
        return []

    def on_order_placed(self, request, order):
        pass


class QuoteOnceStrategy:
    """Places a bid and ask on the first book update, then holds."""

    def __init__(self):
        self.orders = {}           # order_id -> Order
        self.fills_received = []
        self.book_update_count = 0
        self._quoted = False

    def on_book_update(self, book, timestamp_ms):
        self.book_update_count += 1
        if self._quoted or book.mid is None:
            return []
        self._quoted = True
        return [
            OrderRequest(
                side=OrderSide.BUY, order_type=OrderType.LIMIT,
                quantity=Decimal("0.1"), price=Decimal("100.00"),
            ),
            OrderRequest(
                side=OrderSide.SELL, order_type=OrderType.LIMIT,
                quantity=Decimal("0.1"), price=Decimal("101.00"),
            ),
        ]

    def on_trade(self, trade, book):
        return []

    def on_fill(self, fill):
        self.fills_received.append(fill)
        return []

    def on_order_placed(self, request, order):
        self.orders[order.order_id] = order


class CancelOnFillStrategy:
    """After a fill, cancels the other side."""

    def __init__(self):
        self.orders = {}
        self.fills_received = []
        self._quoted = False

    def on_book_update(self, book, timestamp_ms):
        if self._quoted or book.mid is None:
            return []
        self._quoted = True
        return [
            OrderRequest(
                side=OrderSide.BUY, order_type=OrderType.LIMIT,
                quantity=Decimal("0.1"), price=Decimal("100.00"),
            ),
            OrderRequest(
                side=OrderSide.SELL, order_type=OrderType.LIMIT,
                quantity=Decimal("0.1"), price=Decimal("101.00"),
            ),
        ]

    def on_trade(self, trade, book):
        return []

    def on_fill(self, fill):
        self.fills_received.append(fill)
        # Cancel the unfilled side
        cancels = []
        for order in self.orders.values():
            if order.order_id != fill.order_id and not order.is_done:
                cancels.append(CancelRequest(order_id=order.order_id))
        return cancels

    def on_order_placed(self, request, order):
        self.orders[order.order_id] = order


# --- tests ---

def test_starts_in_gap_skips_diffs_until_snapshot():
    """Diffs before the first snapshot are skipped; snapshot exits gap."""
    with tempfile.TemporaryDirectory() as tmpdir:
        depth_file = Path(tmpdir) / "depth.jsonl.gz"
        _write_gz(depth_file, [
            # Diff before any snapshot -- should be skipped
            _diff(_BASE_RECV, _BASE_MS, U=1, u=1,
                  bids=[["100.00", "5.0"]], asks=[["101.00", "3.0"]]),
            # Snapshot -- exits gap
            _snapshot(_BASE_RECV, last_update_id=100,
                      bids=[["100.00", "5.0"]], asks=[["101.00", "3.0"]]),
        ])

        config = ReplayConfig(
            depth_files=[depth_file], trade_files=[], sim_config=_sim_config(),
        )
        strategy = NullStrategy()
        result = ReplayEngine(config).run(strategy)

        assert result.stats.total_events == 2
        assert result.stats.events_during_gap == 1  # the diff
        assert result.stats.snapshots == 1
        print("PASS: diffs before first snapshot are skipped")


def test_basic_replay_with_fill():
    """Snapshot + diff + trade produces a fill on the strategy's limit order."""
    with tempfile.TemporaryDirectory() as tmpdir:
        t_snap = _BASE_MS
        t_diff = _BASE_MS + 1000
        t_trade = _BASE_MS + 2000

        depth_file = Path(tmpdir) / "depth.jsonl.gz"
        _write_gz(depth_file, [
            _snapshot(_BASE_RECV, last_update_id=100,
                      bids=[["100.00", "5.0"], ["99.00", "10.0"]],
                      asks=[["101.00", "3.0"], ["102.00", "8.0"]]),
            _diff("2026-04-21T00:00:01+00:00", E=t_diff, U=101, u=101,
                  bids=[["99.50", "2.0"]], asks=[]),
        ])

        trade_file = Path(tmpdir) / "trades.jsonl.gz"
        _write_gz(trade_file, [
            # Market sell (m=True) hits bids at 100.00, qty 6.0
            # Drains 5.0 queue, fills our 0.1 buy limit
            _trade("2026-04-21T00:00:02+00:00", T=t_trade,
                   agg_id=1, price="100.00", qty="6.0", m=True),
        ])

        config = ReplayConfig(
            depth_files=[depth_file], trade_files=[trade_file],
            sim_config=_sim_config(base_latency_ms=10, jitter_ms=0),
        )
        strategy = QuoteOnceStrategy()
        result = ReplayEngine(config).run(strategy)

        # Strategy should have been called on both depth events
        assert strategy.book_update_count == 2

        # One fill: our buy at 100 filled by the market sell
        assert result.stats.fills == 1
        assert len(strategy.fills_received) == 1
        fill = strategy.fills_received[0]
        assert fill.price == Decimal("100.00")
        assert fill.quantity == Decimal("0.1")
        assert fill.is_maker is True
        assert fill.side == OrderSide.BUY

        # Fee: 0.1 * 100 * (2/10000) = 0.002
        expected_fee = Decimal("0.1") * Decimal("100") * (Decimal(2) / Decimal(10000))
        assert fill.fee == expected_fee

        # Stats
        assert result.stats.snapshots == 1
        assert result.stats.depth_diffs == 1
        assert result.stats.trade_events == 1
        assert result.stats.orders_submitted == 2  # buy + sell
        print(f"PASS: basic replay produces correct fill (price={fill.price}, "
              f"qty={fill.quantity}, fee={fill.fee})")


def test_gap_detection_cancels_active_orders():
    """A sequence gap cancels all active orders."""
    with tempfile.TemporaryDirectory() as tmpdir:
        t_snap = _BASE_MS
        t_diff1 = _BASE_MS + 1000
        t_gap = _BASE_MS + 2000

        depth_file = Path(tmpdir) / "depth.jsonl.gz"
        _write_gz(depth_file, [
            _snapshot(_BASE_RECV, last_update_id=100,
                      bids=[["100.00", "5.0"]], asks=[["101.00", "3.0"]]),
            # Normal diff -- orders activate here
            _diff("2026-04-21T00:00:01+00:00", E=t_diff1, U=101, u=101,
                  bids=[], asks=[]),
            # Gap: U=200 but expected 102 -> gap detected
            _diff("2026-04-21T00:00:02+00:00", E=t_gap, U=200, u=200,
                  bids=[["100.00", "4.0"]], asks=[["101.00", "2.0"]]),
        ])

        config = ReplayConfig(
            depth_files=[depth_file], trade_files=[], sim_config=_sim_config(),
        )
        strategy = QuoteOnceStrategy()
        result = ReplayEngine(config).run(strategy)

        assert result.stats.gaps_detected == 1
        assert result.stats.events_during_gap == 1  # the gap diff itself is skipped

        # Both orders should be cancelled (gap cancels all active)
        all_cancelled = all(o.status == OrderStatus.CANCELLED
                           for o in strategy.orders.values())
        assert all_cancelled
        assert result.stats.orders_cancelled == 2
        print("PASS: gap detection cancels all active orders")


def test_gap_detection_cancels_pending_orders():
    """A sequence gap cancels orders that were placed but have not arrived."""
    with tempfile.TemporaryDirectory() as tmpdir:
        t_diff = _BASE_MS + 1000
        t_gap = _BASE_MS + 1500
        t_after_recovery = _BASE_MS + 3000

        depth_file = Path(tmpdir) / "depth.jsonl.gz"
        _write_gz(depth_file, [
            _snapshot(_BASE_RECV, last_update_id=100,
                      bids=[["100.00", "5.0"]], asks=[["101.00", "3.0"]]),
            _diff("2026-04-21T00:00:01+00:00", E=t_diff, U=101, u=101,
                  bids=[], asks=[]),
            # Gap occurs before the snapshot-submitted orders can arrive.
            _diff("2026-04-21T00:00:01.500000+00:00", E=t_gap, U=200, u=200,
                  bids=[], asks=[]),
            _snapshot("2026-04-21T00:00:02+00:00", last_update_id=300,
                      bids=[["100.00", "5.0"]], asks=[["101.00", "3.0"]]),
            # If the pending orders survived the gap, they would arrive here.
            _diff("2026-04-21T00:00:03+00:00", E=t_after_recovery, U=301, u=301,
                  bids=[], asks=[]),
        ])

        config = ReplayConfig(
            depth_files=[depth_file],
            trade_files=[],
            sim_config=_sim_config(base_latency_ms=3000, jitter_ms=0),
        )
        strategy = QuoteOnceStrategy()
        result = ReplayEngine(config).run(strategy)

        assert result.stats.gaps_detected == 1
        assert result.stats.orders_cancelled == 2
        assert len(strategy.orders) == 2
        assert all(o.status == OrderStatus.CANCELLED
                   for o in strategy.orders.values())
        assert not any(e.event_type == "arrived" for e in result.events)
        print("PASS: gap detection cancels pending orders before arrival")


def test_gap_recovery_after_snapshot():
    """After a gap, a new snapshot restores normal operation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        t_diff1 = _BASE_MS + 1000
        t_gap = _BASE_MS + 2000
        t_gap_diff = _BASE_MS + 3000
        t_snap2_recv = "2026-04-21T00:00:04+00:00"
        t_diff2 = _BASE_MS + 5000

        depth_file = Path(tmpdir) / "depth.jsonl.gz"
        _write_gz(depth_file, [
            _snapshot(_BASE_RECV, last_update_id=100,
                      bids=[["100.00", "5.0"]], asks=[["101.00", "3.0"]]),
            _diff("2026-04-21T00:00:01+00:00", E=t_diff1, U=101, u=101,
                  bids=[], asks=[]),
            # Gap
            _diff("2026-04-21T00:00:02+00:00", E=t_gap, U=200, u=200,
                  bids=[], asks=[]),
            # Still in gap
            _diff("2026-04-21T00:00:03+00:00", E=t_gap_diff, U=201, u=201,
                  bids=[], asks=[]),
            # Recovery snapshot
            _snapshot(t_snap2_recv, last_update_id=300,
                      bids=[["99.00", "4.0"]], asks=[["100.00", "2.0"]]),
            # Normal diff after recovery
            _diff("2026-04-21T00:00:05+00:00", E=t_diff2, U=301, u=301,
                  bids=[], asks=[]),
        ])

        config = ReplayConfig(
            depth_files=[depth_file], trade_files=[], sim_config=_sim_config(),
        )
        strategy = NullStrategy()
        result = ReplayEngine(config).run(strategy)

        assert result.stats.snapshots == 2
        assert result.stats.gaps_detected == 1
        # Gap covers: gap diff + one more diff = 2 events during gap
        assert result.stats.events_during_gap == 2
        # Total depth diffs = 4 (diff1, gap, gap_diff, diff2)
        assert result.stats.depth_diffs == 4
        assert result.stats.total_events == 6
        print("PASS: snapshot after gap restores normal operation")


def test_trades_skipped_during_gap():
    """Trade events during a gap are counted but not dispatched."""
    with tempfile.TemporaryDirectory() as tmpdir:
        t_diff = _BASE_MS + 1000
        t_gap = _BASE_MS + 2000
        t_trade = _BASE_MS + 3000

        depth_file = Path(tmpdir) / "depth.jsonl.gz"
        _write_gz(depth_file, [
            _snapshot(_BASE_RECV, last_update_id=100,
                      bids=[["100.00", "5.0"]], asks=[["101.00", "3.0"]]),
            _diff("2026-04-21T00:00:01+00:00", E=t_diff, U=101, u=101,
                  bids=[], asks=[]),
            _diff("2026-04-21T00:00:02+00:00", E=t_gap, U=200, u=200,
                  bids=[], asks=[]),
        ])

        trade_file = Path(tmpdir) / "trades.jsonl.gz"
        _write_gz(trade_file, [
            _trade("2026-04-21T00:00:03+00:00", T=t_trade,
                   agg_id=1, price="100.00", qty="1.0", m=True),
        ])

        config = ReplayConfig(
            depth_files=[depth_file], trade_files=[trade_file],
            sim_config=_sim_config(),
        )
        strategy = QuoteOnceStrategy()
        result = ReplayEngine(config).run(strategy)

        assert result.stats.trade_events == 1
        # Gap diff + trade = 2 events during gap
        assert result.stats.events_during_gap == 2
        assert result.stats.fills == 0
        print("PASS: trades during gap are counted but not dispatched")


def test_cancel_request_from_strategy():
    """Strategy can cancel the other side after a fill."""
    with tempfile.TemporaryDirectory() as tmpdir:
        t_diff = _BASE_MS + 1000
        t_trade = _BASE_MS + 2000

        depth_file = Path(tmpdir) / "depth.jsonl.gz"
        _write_gz(depth_file, [
            _snapshot(_BASE_RECV, last_update_id=100,
                      bids=[["100.00", "5.0"]], asks=[["101.00", "3.0"]]),
            _diff("2026-04-21T00:00:01+00:00", E=t_diff, U=101, u=101,
                  bids=[], asks=[]),
        ])

        trade_file = Path(tmpdir) / "trades.jsonl.gz"
        _write_gz(trade_file, [
            _trade("2026-04-21T00:00:02+00:00", T=t_trade,
                   agg_id=1, price="100.00", qty="6.0", m=True),
        ])

        config = ReplayConfig(
            depth_files=[depth_file], trade_files=[trade_file],
            sim_config=_sim_config(),
        )
        strategy = CancelOnFillStrategy()
        result = ReplayEngine(config).run(strategy)

        # Buy filled by the trade, strategy cancels the sell
        assert len(strategy.fills_received) == 1
        assert strategy.fills_received[0].side == OrderSide.BUY

        # Both orders should be done: one filled, one cancelled by strategy
        statuses = {o.order_id: o.status for o in strategy.orders.values()}
        assert OrderStatus.FILLED in statuses.values()
        assert OrderStatus.CANCELLED in statuses.values()
        assert result.stats.orders_cancelled == 1
        print("PASS: strategy CancelRequest cancels the other side after fill")


def test_checkpoints():
    """State hash checkpoints are recorded at the configured interval."""
    with tempfile.TemporaryDirectory() as tmpdir:
        depth_file = Path(tmpdir) / "depth.jsonl.gz"
        _write_gz(depth_file, [
            _snapshot(_BASE_RECV, last_update_id=100,
                      bids=[["100.00", "5.0"]], asks=[["101.00", "3.0"]]),
            _diff("2026-04-21T00:00:01+00:00", E=_BASE_MS + 1000, U=101, u=101,
                  bids=[["100.00", "4.0"]], asks=[]),
            _diff("2026-04-21T00:00:02+00:00", E=_BASE_MS + 2000, U=102, u=102,
                  bids=[["100.00", "3.0"]], asks=[]),
            _diff("2026-04-21T00:00:03+00:00", E=_BASE_MS + 3000, U=103, u=103,
                  bids=[["100.00", "2.0"]], asks=[]),
        ])

        config = ReplayConfig(
            depth_files=[depth_file], trade_files=[],
            sim_config=_sim_config(), checkpoint_interval=2,
        )
        result = ReplayEngine(config).run(NullStrategy())

        # 4 events total, checkpoint every 2 -> checkpoints at event 2 and 4
        assert len(result.checkpoints) == 2
        assert result.checkpoints[0][0] == 2
        assert result.checkpoints[1][0] == 4

        # Hashes should differ (book changed between checkpoints)
        assert result.checkpoints[0][2] != result.checkpoints[1][2]
        print(f"PASS: {len(result.checkpoints)} checkpoints recorded at interval=2")


def test_determinism():
    """Identical inputs produce identical fills and checkpoints."""
    with tempfile.TemporaryDirectory() as tmpdir:
        t_diff = _BASE_MS + 1000
        t_trade = _BASE_MS + 2000

        depth_file = Path(tmpdir) / "depth.jsonl.gz"
        _write_gz(depth_file, [
            _snapshot(_BASE_RECV, last_update_id=100,
                      bids=[["100.00", "5.0"]], asks=[["101.00", "3.0"]]),
            _diff("2026-04-21T00:00:01+00:00", E=t_diff, U=101, u=101,
                  bids=[], asks=[]),
        ])

        trade_file = Path(tmpdir) / "trades.jsonl.gz"
        _write_gz(trade_file, [
            _trade("2026-04-21T00:00:02+00:00", T=t_trade,
                   agg_id=1, price="100.00", qty="6.0", m=True),
        ])

        config = ReplayConfig(
            depth_files=[depth_file], trade_files=[trade_file],
            sim_config=_sim_config(jitter_ms=5, seed=42),
            checkpoint_interval=2,
        )

        r1 = ReplayEngine(config).run(QuoteOnceStrategy())
        r2 = ReplayEngine(config).run(QuoteOnceStrategy())

        fills1 = [(f.fill_id, str(f.price), str(f.quantity)) for f in r1.fills]
        fills2 = [(f.fill_id, str(f.price), str(f.quantity)) for f in r2.fills]
        assert fills1 == fills2

        hashes1 = [c[2] for c in r1.checkpoints]
        hashes2 = [c[2] for c in r2.checkpoints]
        assert hashes1 == hashes2
        print("PASS: identical inputs -> identical fills and checkpoints")


def test_empty_replay():
    """No files produces empty results with zero stats."""
    config = ReplayConfig(
        depth_files=[], trade_files=[], sim_config=_sim_config(),
    )
    result = ReplayEngine(config).run(NullStrategy())

    assert result.stats.total_events == 0
    assert len(result.fills) == 0
    assert len(result.checkpoints) == 0
    print("PASS: empty replay produces zero stats")


def test_stats_add_up():
    """total_events = depth_diffs + snapshots + trade_events."""
    with tempfile.TemporaryDirectory() as tmpdir:
        depth_file = Path(tmpdir) / "depth.jsonl.gz"
        _write_gz(depth_file, [
            _snapshot(_BASE_RECV, last_update_id=100,
                      bids=[["100.00", "5.0"]], asks=[["101.00", "3.0"]]),
            _diff("2026-04-21T00:00:01+00:00", E=_BASE_MS + 1000, U=101, u=101,
                  bids=[], asks=[]),
            _diff("2026-04-21T00:00:02+00:00", E=_BASE_MS + 2000, U=102, u=102,
                  bids=[], asks=[]),
        ])

        trade_file = Path(tmpdir) / "trades.jsonl.gz"
        _write_gz(trade_file, [
            _trade("2026-04-21T00:00:01+00:00", T=_BASE_MS + 1500,
                   agg_id=1, price="100.00", qty="0.5", m=True),
            _trade("2026-04-21T00:00:02+00:00", T=_BASE_MS + 2500,
                   agg_id=2, price="100.00", qty="0.3", m=True),
        ])

        config = ReplayConfig(
            depth_files=[depth_file], trade_files=[trade_file],
            sim_config=_sim_config(),
        )
        result = ReplayEngine(config).run(NullStrategy())

        s = result.stats
        assert s.total_events == s.depth_diffs + s.snapshots + s.trade_events
        assert s.total_events == 5  # 1 snapshot + 2 diffs + 2 trades
        print(f"PASS: total_events ({s.total_events}) = "
              f"diffs ({s.depth_diffs}) + snapshots ({s.snapshots}) + "
              f"trades ({s.trade_events})")


if __name__ == "__main__":
    test_starts_in_gap_skips_diffs_until_snapshot()
    test_basic_replay_with_fill()
    test_gap_detection_cancels_active_orders()
    test_gap_detection_cancels_pending_orders()
    test_gap_recovery_after_snapshot()
    test_trades_skipped_during_gap()
    test_cancel_request_from_strategy()
    test_checkpoints()
    test_determinism()
    test_empty_replay()
    test_stats_add_up()
    print("\nAll tests passed.")
