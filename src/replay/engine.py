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
request emits a resynchronization barrier. The completed snapshot recovery
exits gap state. A sequence gap in depth diffs, or an aggTrade gap under the
strict policy, also enters gap state. During a gap, events are counted but not
dispatched -- the book is unreliable and strategy callbacks would see stale
data. All local open orders are explicitly invalidated on gap entry because
queue positions and pending intent are no longer trustworthy. This is not
counted as an exchange cancellation.

Order management: strategies return Actions (OrderRequests or CancelRequests)
from callbacks. The engine submits orders through the simulator and notifies
the strategy via on_order_placed so it can track order IDs for later
cancellation. The strategy owns its cancel/replace logic.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import groupby
from pathlib import Path
from typing import List, Optional, Protocol, Tuple, Union

from src.replay.orderbook import Orderbook
from src.replay.book_backend import create_orderbook, book_provenance
from src.replay.depth_parser import DepthParser, DepthEvent
from src.replay.trade_parser import TradeParser, TradeEvent
from src.replay.event_merger import EventMerger
from src.execution.simulator import (
    EQUAL_TIMESTAMP_POLICY,
    EXECUTION_MODEL_VERSION,
    ExecutionSimulator,
    SimConfig,
)
from src.execution.order import OrderRequest, Order, Fill, OrderEvent
from src.execution.provenance import execution_provenance_for_replay


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
    book_backend: str = "python"
    book_tick_size: Decimal = Decimal("0.01")
    book_qty_step: Decimal = Decimal("0.00000001")

    def __post_init__(self) -> None:
        if self.book_backend not in {"python", "cpp"}:
            raise ValueError("book_backend must be 'python' or 'cpp'")
        for name in ("book_tick_size", "book_qty_step"):
            value = Decimal(getattr(self, name))
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
            setattr(self, name, value)
        if self.trade_gap_policy not in {"ignore", "pause_until_snapshot"}:
            raise ValueError(
                "trade_gap_policy must be 'ignore' or 'pause_until_snapshot'"
            )


@dataclass
class ReplayStats:
    total_events: int = 0
    snapshot_resyncs: int = 0
    depth_diffs: int = 0
    snapshots: int = 0
    trade_events: int = 0
    gaps_detected: int = 0
    depth_gaps_detected: int = 0
    trade_gaps_detected: int = 0
    events_during_gap: int = 0
    orders_submitted: int = 0
    order_arrivals: int = 0
    cancel_requests: int = 0
    orders_cancelled: int = 0
    cancels_too_late: int = 0
    gap_invalidations: int = 0
    replay_end_invalidations: int = 0
    pending_actions_at_end: int = 0
    pending_cancels_at_end: int = 0
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
    execution_model_version: str = EXECUTION_MODEL_VERSION
    equal_timestamp_policy: str = EQUAL_TIMESTAMP_POLICY
    execution_provenance: dict = field(default_factory=dict)


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
        self.book = create_orderbook(config.book_backend, config.book_tick_size,
                                     config.book_qty_step)
        self._book_provenance = book_provenance(
            config.book_backend, config.book_tick_size, config.book_qty_step,
        )
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

        last_timestamp_ms: Optional[int] = None
        for timestamp_ms, timestamp_events in groupby(
            merger.events(), key=lambda event: event.exchange_time_ms
        ):
            last_timestamp_ms = timestamp_ms
            timestamp_events = list(timestamp_events)
            group_reports_gap = self._group_reports_gap(timestamp_events)
            # Recorded market data wins unresolved millisecond ties.  Private
            # actions strictly before this timestamp use the last observable
            # book, private actions exactly at it wait until every recorded
            # depth/trade event in the timestamp group has been processed.
            if group_reports_gap:
                # Sequence loss makes within-millisecond execution attribution
                # incomplete. Treat the whole timestamp as an atomic censored
                # group: invalidate before any event in it can fill an order or
                # invoke a strategy callback.
                if not self._in_gap:
                    self._in_gap = True
                    self._cancel_all(timestamp_ms)
                for event in timestamp_events:
                    self._count_censored_gap_event(event)
                    if self._stats.total_events % self.config.checkpoint_interval == 0:
                        checkpoints.append((
                            self._stats.total_events,
                            event.exchange_time_ms,
                            self.book.state_hash(),
                        ))
                self._process_scheduled(
                    timestamp_ms, inclusive=True, strategy=strategy
                )
                continue

            self._process_scheduled(
                timestamp_ms, inclusive=False, strategy=strategy
            )

            observed_depth = False
            for event in timestamp_events:
                self._stats.total_events += 1

                if isinstance(event, DepthEvent):
                    observed_depth = self._on_depth(event, strategy) or observed_depth
                else:
                    self._on_trade(event, strategy)

                if self._stats.total_events % self.config.checkpoint_interval == 0:
                    checkpoints.append((
                        self._stats.total_events,
                        event.exchange_time_ms,
                        self.book.state_hash(),
                    ))

            # Defer cancellation attribution until same-ms trades are known,
            # otherwise depth-before-trade ties misclassify traded volume as
            # cancellation-driven queue improvement.
            if observed_depth:
                self.sim.observe_book_update(self.book, timestamp_ms)

            self._process_scheduled(
                timestamp_ms, inclusive=True, strategy=strategy
            )

        if last_timestamp_ms is not None:
            self._stats.pending_actions_at_end = self.sim.pending_scheduled_actions
            self._stats.pending_cancels_at_end = self.sim.pending_cancel_actions
            self._stats.replay_end_invalidations += self.sim.invalidate_all(
                last_timestamp_ms, reason="replay_end"
            )

        return ReplayResult(
            fills=self.sim.fills,
            events=self.sim.events,
            stats=self._stats,
            checkpoints=checkpoints,
            book_samples=list(self._book_samples),
            execution_provenance=execution_provenance_for_replay(
                self.config.sim_config,
                trade_gap_policy=self.config.trade_gap_policy,
                orderbook=self._book_provenance,
            ),
        )

    # --- event handlers ---

    def _on_depth(self, event: DepthEvent, strategy: Strategy) -> bool:
        if event.event_type == "resync":
            self._stats.snapshot_resyncs += 1
            if not self._in_gap:
                self._in_gap = True
                self._cancel_all(event.exchange_time_ms)
            return False

        if event.event_type == "snapshot":
            self._stats.snapshots += 1
            self.book.apply_snapshot(event.bids, event.asks, event.last_update_id)

            if self._in_gap:
                self._in_gap = False
                # Cancel open orders -- queue positions and pending intent
                # are invalid after a gap. The strategy will re-quote on the
                # next callback.
                self._cancel_all(event.exchange_time_ms)

            if not event.dispatch_strategy:
                return False

            self._record_book_sample(event.exchange_time_ms)
            actions = strategy.on_book_update(self.book, event.exchange_time_ms)
            self._apply_actions(actions, event.exchange_time_ms, strategy)
            return True

        # Diff
        self._stats.depth_diffs += 1

        if event.has_gap:
            self._stats.gaps_detected += 1
            self._stats.depth_gaps_detected += 1
            if not self._in_gap:
                self._in_gap = True
                self._cancel_all(event.exchange_time_ms)

        if self._in_gap:
            self._stats.events_during_gap += 1
            return False

        self.book.apply_diff(event.bids, event.asks, event.last_update_id)
        if not event.dispatch_strategy:
            return False
        self._record_book_sample(event.exchange_time_ms)
        actions = strategy.on_book_update(self.book, event.exchange_time_ms)
        self._apply_actions(actions, event.exchange_time_ms, strategy)
        return True

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

        fills = self.sim.observe_trade(event, self.book)
        self._dispatch_fills(fills, strategy)
        actions = strategy.on_trade(event, self.book)
        self._apply_actions(actions, event.exchange_time_ms, strategy)

    # --- internal helpers ---

    def _group_reports_gap(self, events: List[object]) -> bool:
        """Whether missing data taints the timestamp as an atomic group."""
        for event in events:
            if isinstance(event, DepthEvent) and event.has_gap:
                return True
            if (
                isinstance(event, TradeEvent)
                and event.has_gap
                and self.config.trade_gap_policy == "pause_until_snapshot"
            ):
                return True
        return False

    def _count_censored_gap_event(self, event: object) -> None:
        """Count one event in a gap-tainted timestamp without dispatching it."""
        self._stats.total_events += 1
        self._stats.events_during_gap += 1
        if isinstance(event, DepthEvent):
            if event.event_type == "resync":
                self._stats.snapshot_resyncs += 1
            elif event.event_type == "snapshot":
                self._stats.snapshots += 1
            else:
                self._stats.depth_diffs += 1
            if event.has_gap:
                self._stats.depth_gaps_detected += 1
                self._stats.gaps_detected += 1
            return

        self._stats.trade_events += 1
        if event.has_gap:
            self._stats.trade_gaps_detected += 1
            # Only strict trade-gap policy reaches this censored path.
            self._stats.gaps_detected += 1

    def _process_scheduled(
        self,
        timestamp_ms: int,
        *,
        inclusive: bool,
        strategy: Strategy,
    ) -> None:
        """Process private arrivals on the selected side of a market timestamp."""
        while self.sim.next_scheduled_time_ms is not None:
            next_time = self.sim.next_scheduled_time_ms
            if next_time > timestamp_ms or (
                next_time == timestamp_ms and not inclusive
            ):
                return

            step = self.sim.process_next_scheduled(self.book)
            self._stats.order_arrivals += step.order_arrivals
            self._stats.orders_cancelled += step.effective_cancels
            self._stats.cancels_too_late += step.cancels_too_late
            self._dispatch_fills(step.fills, strategy)

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
                    self._stats.cancel_requests += 1
            elif isinstance(action, OrderRequest):
                self._stats.orders_submitted += 1
                order = self.sim.submit(action, timestamp_ms)
                strategy.on_order_placed(action, order)

    def _cancel_all(self, timestamp_ms: int) -> None:
        """Fail-closed invalidation used on data-gap entry and recovery."""
        self._stats.gap_invalidations += self.sim.invalidate_all(
            timestamp_ms, reason="data_gap"
        )

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
