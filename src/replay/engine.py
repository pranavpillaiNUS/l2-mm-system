"""
Main replay loop.

Connects the event pipeline (parsers -> merger), the orderbook, the execution
simulator, and a trading strategy into a single deterministic replay.

The engine processes events in timestamp order:
  1. Depth events update the orderbook
  2. Trade events go to the execution simulator for queue draining
  3. Strategy callbacks fire after each event
  4. Fills from the simulator are dispatched to the strategy

Gap handling: the engine starts in gap state (book uninitialized). A snapshot
exits gap state and resyncs the book. A sequence gap in depth diffs re-enters
gap state. During a gap, events are counted but not dispatched -- the book is
unreliable and strategy callbacks would see stale data. All active orders are
cancelled on gap entry because queue positions are invalidated.

Order management: strategies return Actions (OrderRequests or CancelRequests)
from callbacks. The engine submits orders through the simulator and notifies
the strategy via on_order_placed so it can track order IDs for later
cancellation. The strategy owns its cancel/replace logic.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Protocol, Tuple, Union

from src.replay.orderbook import Orderbook
from src.replay.depth_parser import DepthParser, DepthEvent
from src.replay.trade_parser import TradeParser, TradeEvent
from src.replay.event_merger import EventMerger
from src.execution.simulator import ExecutionSimulator, SimConfig
from src.execution.order import OrderRequest, Order, Fill, OrderEvent


@dataclass
class CancelRequest:
    """Strategy intent to cancel an existing order by ID."""
    order_id: str


Action = Union[OrderRequest, CancelRequest]


class Strategy(Protocol):
    """
    Interface the replay engine expects from a trading strategy.

    Each callback returns a list of Actions -- either OrderRequests to place
    new orders, or CancelRequests to cancel existing ones. Return an empty
    list for no action.

    on_order_placed is called after each OrderRequest is submitted, giving
    the strategy the Order object so it can track the ID for later
    cancellation.
    """

    def on_book_update(self, book: Orderbook, timestamp_ms: int) -> List[Action]:
        ...

    def on_trade(self, trade: TradeEvent, book: Orderbook) -> List[Action]:
        ...

    def on_fill(self, fill: Fill) -> List[Action]:
        ...

    def on_order_placed(self, request: OrderRequest, order: Order) -> None:
        ...


@dataclass
class ReplayConfig:
    depth_files: List[Path]
    trade_files: List[Path]
    sim_config: SimConfig
    checkpoint_interval: int = 1000  # book state hash every N events


@dataclass
class ReplayStats:
    total_events: int = 0
    depth_diffs: int = 0
    snapshots: int = 0
    trade_events: int = 0
    gaps_detected: int = 0
    events_during_gap: int = 0
    orders_submitted: int = 0
    orders_cancelled: int = 0
    fills: int = 0


@dataclass
class ReplayResult:
    fills: List[Fill]
    events: List[OrderEvent]
    stats: ReplayStats
    checkpoints: List[Tuple[int, int, str]]  # (event_idx, timestamp_ms, book_hash)


class ReplayEngine:
    """
    Drives a deterministic replay of recorded L2 data.

    Usage:
        config = ReplayConfig(
            depth_files=sorted(Path("data/raw/btcusdt").glob("*.jsonl.gz")),
            trade_files=sorted(Path("data/raw/btcusdt_trades").glob("*.jsonl.gz")),
            sim_config=SimConfig(base_latency_ms=10, jitter_ms=2,
                                 maker_bps=2, taker_bps=5),
        )
        engine = ReplayEngine(config)
        result = engine.run(my_strategy)
    """

    def __init__(self, config: ReplayConfig):
        self.config = config
        self.book = Orderbook()
        self.sim = ExecutionSimulator(config.sim_config)
        self._in_gap = True  # no book state until first snapshot
        self._stats = ReplayStats()

    def run(self, strategy: Strategy) -> ReplayResult:
        """
        Run the full replay and return results.

        Iterates every depth and trade event in timestamp order, updating
        the book and simulator, and dispatching to the strategy.
        """
        depth_parser = DepthParser(self.config.depth_files)
        trade_parser = TradeParser(self.config.trade_files)
        merger = EventMerger(depth_parser.events(), trade_parser.events())

        checkpoints: List[Tuple[int, int, str]] = []

        for event in merger.events():
            self._stats.total_events += 1

            if isinstance(event, DepthEvent):
                self._on_depth(event, strategy)
            else:
                self._on_trade(event, strategy)

            if self._stats.total_events % self.config.checkpoint_interval == 0:
                checkpoints.append((
                    self._stats.total_events,
                    event.exchange_time_ms,
                    self.book.state_hash(),
                ))

        return ReplayResult(
            fills=self.sim.fills,
            events=self.sim.events,
            stats=self._stats,
            checkpoints=checkpoints,
        )

    # --- event handlers ---

    def _on_depth(self, event: DepthEvent, strategy: Strategy) -> None:
        if event.event_type == "snapshot":
            self._stats.snapshots += 1
            self.book.apply_snapshot(event.bids, event.asks, event.last_update_id)

            if self._in_gap:
                self._in_gap = False
                # Cancel active orders -- queue positions are invalid after
                # a gap. The strategy will re-quote on the next callback.
                self._cancel_all(event.exchange_time_ms)

            fills = self.sim.on_book_update(self.book, event.exchange_time_ms)
            self._dispatch_fills(fills, strategy)
            actions = strategy.on_book_update(self.book, event.exchange_time_ms)
            self._apply_actions(actions, event.exchange_time_ms, strategy)
            return

        # Diff
        self._stats.depth_diffs += 1

        if event.has_gap and not self._in_gap:
            self._stats.gaps_detected += 1
            self._in_gap = True
            self._cancel_all(event.exchange_time_ms)

        if self._in_gap:
            self._stats.events_during_gap += 1
            return

        self.book.apply_diff(event.bids, event.asks, event.last_update_id)
        fills = self.sim.on_book_update(self.book, event.exchange_time_ms)
        self._dispatch_fills(fills, strategy)
        actions = strategy.on_book_update(self.book, event.exchange_time_ms)
        self._apply_actions(actions, event.exchange_time_ms, strategy)

    def _on_trade(self, event: TradeEvent, strategy: Strategy) -> None:
        self._stats.trade_events += 1

        if self._in_gap:
            self._stats.events_during_gap += 1
            return

        fills = self.sim.on_trade(event, self.book)
        self._dispatch_fills(fills, strategy)
        actions = strategy.on_trade(event, self.book)
        self._apply_actions(actions, event.exchange_time_ms, strategy)

    # --- internal helpers ---

    def _dispatch_fills(self, fills: List[Fill], strategy: Strategy) -> None:
        for fill in fills:
            self._stats.fills += 1
            actions = strategy.on_fill(fill)
            self._apply_actions(actions, fill.timestamp_ms, strategy)

    def _apply_actions(
        self,
        actions: List[Action],
        timestamp_ms: int,
        strategy: Strategy,
    ) -> None:
        for action in actions:
            if isinstance(action, CancelRequest):
                if self.sim.cancel(action.order_id, timestamp_ms):
                    self._stats.orders_cancelled += 1
            elif isinstance(action, OrderRequest):
                self._stats.orders_submitted += 1
                order = self.sim.submit(action, timestamp_ms)
                strategy.on_order_placed(action, order)

    def _cancel_all(self, timestamp_ms: int) -> None:
        """Cancel all active orders -- used on gap entry and gap recovery."""
        for order in self.sim.active_orders:
            self.sim.cancel(order.order_id, timestamp_ms)
            self._stats.orders_cancelled += 1
