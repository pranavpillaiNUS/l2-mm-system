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
exits gap state and resyncs the book. A sequence gap in depth diffs, or an
aggTrade gap under the strict policy, re-enters gap state. During a gap, events
are counted but not dispatched -- the book is unreliable and strategy
callbacks would see stale data. All open orders are cancelled on gap entry
because queue positions and pending intent are invalid.

Order management: strategies return Actions (OrderRequests or CancelRequests)
from callbacks. The engine submits orders through the simulator and notifies
the strategy via on_order_placed so it can track order IDs for later
cancellation. The strategy owns its cancel/replace logic.
"""
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Protocol, Tuple, Union

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
    record_book_samples: bool = False
    trade_gap_policy: str = "pause_until_snapshot"

    def __post_init__(self) -> None:
        if self.trade_gap_policy not in {"ignore", "pause_until_snapshot"}:
            raise ValueError(
                "trade_gap_policy must be 'ignore' or 'pause_until_snapshot'"
            )


@dataclass
class ReplayStats:
    total_events: int = 0
    depth_diffs: int = 0
    snapshots: int = 0
    trade_events: int = 0
    gaps_detected: int = 0
    depth_gaps_detected: int = 0
    trade_gaps_detected: int = 0
    events_during_gap: int = 0
    orders_submitted: int = 0
    orders_cancelled: int = 0
    fills: int = 0


@dataclass
class BookSample:
    timestamp_ms: int
    best_bid: Decimal
    best_ask: Decimal
    mid: Decimal
    microprice: Optional[Decimal]
    spread: Decimal
    best_bid_qty: Optional[Decimal] = None
    best_ask_qty: Optional[Decimal] = None


@dataclass
class ReplayResult:
    fills: List[Fill]
    events: List[OrderEvent]
    stats: ReplayStats
    checkpoints: List[Tuple[int, int, str]]  # (event_idx, timestamp_ms, book_hash)
    book_samples: List[BookSample]


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
        self._book_samples: List[BookSample] = []

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
            book_samples=list(self._book_samples),
        )

    # --- event handlers ---

    def _on_depth(self, event: DepthEvent, strategy: Strategy) -> None:
        if event.event_type == "snapshot":
            self._stats.snapshots += 1
            self.book.apply_snapshot(event.bids, event.asks, event.last_update_id)

            if self._in_gap:
                self._in_gap = False
                # Cancel open orders -- queue positions and pending intent
                # are invalid after a gap. The strategy will re-quote on the
                # next callback.
                self._cancel_all(event.exchange_time_ms)

            self._record_book_sample(event.exchange_time_ms)
            fills = self.sim.on_book_update(self.book, event.exchange_time_ms)
            self._dispatch_fills(fills, strategy)
            actions = strategy.on_book_update(self.book, event.exchange_time_ms)
            self._apply_actions(actions, event.exchange_time_ms, strategy)
            return

        # Diff
        self._stats.depth_diffs += 1

        if event.has_gap and not self._in_gap:
            self._stats.gaps_detected += 1
            self._stats.depth_gaps_detected += 1
            self._in_gap = True
            self._cancel_all(event.exchange_time_ms)

        if self._in_gap:
            self._stats.events_during_gap += 1
            return

        self.book.apply_diff(event.bids, event.asks, event.last_update_id)
        self._record_book_sample(event.exchange_time_ms)
        fills = self.sim.on_book_update(self.book, event.exchange_time_ms)
        self._dispatch_fills(fills, strategy)
        actions = strategy.on_book_update(self.book, event.exchange_time_ms)
        self._apply_actions(actions, event.exchange_time_ms, strategy)

    def _on_trade(self, event: TradeEvent, strategy: Strategy) -> None:
        self._stats.trade_events += 1

        if event.has_gap:
            self._stats.trade_gaps_detected += 1
            if self.config.trade_gap_policy == "pause_until_snapshot":
                self._stats.gaps_detected += 1
                if not self._in_gap:
                    self._in_gap = True
                    self._cancel_all(event.exchange_time_ms)

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
        """Cancel all open orders -- used on gap entry and gap recovery."""
        for order in self.sim.open_orders:
            if self.sim.cancel(order.order_id, timestamp_ms):
                self._stats.orders_cancelled += 1

    def _record_book_sample(self, timestamp_ms: int) -> None:
        if not self.config.record_book_samples:
            return

        best_bid = self.book.best_bid
        best_ask = self.book.best_ask
        mid = self.book.mid
        spread = self.book.spread
        if best_bid is None or best_ask is None or mid is None or spread is None:
            return

        self._book_samples.append(BookSample(
            timestamp_ms=timestamp_ms,
            best_bid=best_bid,
            best_ask=best_ask,
            mid=mid,
            microprice=self.book.microprice,
            spread=spread,
            best_bid_qty=self.book.best_bid_qty,
            best_ask_qty=self.book.best_ask_qty,
        ))
