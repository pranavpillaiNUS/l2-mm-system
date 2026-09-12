"""
Execution simulator for L2 replay.

Takes OrderRequests from strategies, applies latency, models FIFO queue
position, and produces Fills as trades happen in the replayed data.

Key design decisions:
  - Latency uses seeded PRNG - determinism over realism
  - queue_ahead set at order arrival = book qty at that price level
  - Trades drain queue_ahead from the front
  - Book qty decreases without a trade -> proportional queue improvement
  - Market orders walk levels greedily, unfilled remainder is cancelled
  - Aggressive limit orders: post_only=True cancels them (default),
    post_only=False executes them as takers
"""
import heapq
import random
from collections import defaultdict
from dataclasses import InitVar, dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple

from src.execution.order import (
    Fill, Order, OrderEvent, OrderRequest,
    OrderSide, OrderStatus, OrderType,
)
from src.execution.queue_credit import credit_from_legacy_mode, parse_queue_credit
from src.replay.orderbook import Orderbook
from src.replay.trade_parser import TradeEvent


EXECUTION_MODEL_VERSION = "event_driven_v2"
EQUAL_TIMESTAMP_POLICY = "market_data_before_private_actions_v1"


@dataclass
class SimConfig:
    base_latency_ms: int   # baseline one-way latency
    jitter_ms: int         # arrival = base_latency +/- uniform(jitter_ms)
    maker_bps: int         # fee rate for resting limit fills
    taker_bps: int         # fee rate for market orders and aggressive limits
    seed: int = 42
    post_only: bool = True  # cancel limit orders that would cross the spread
    queue_cancellation_credit: Decimal | str | float | None = None
    cancel_latency_ms: int | None = None
    cancel_jitter_ms: int | None = None
    queue_cancellation_mode: InitVar[str | None] = None

    def __post_init__(self, queue_cancellation_mode: str | None) -> None:
        integer_fields = {
            "base_latency_ms": self.base_latency_ms,
            "jitter_ms": self.jitter_ms,
            "maker_bps": self.maker_bps,
            "taker_bps": self.taker_bps,
            "seed": self.seed,
        }
        if any(type(value) is not int for value in integer_fields.values()):
            raise ValueError(
                "latency, fee-bps, and seed configuration must use exact integers"
            )
        if type(self.post_only) is not bool:
            raise ValueError("post_only must be a boolean")
        if self.seed < 0:
            raise ValueError("latency seed must be non-negative")
        if self.base_latency_ms < 0 or self.jitter_ms < 0:
            raise ValueError("entry latency and jitter must be non-negative")
        if self.jitter_ms > self.base_latency_ms:
            raise ValueError("entry jitter cannot exceed base latency")

        if self.cancel_latency_ms is None:
            self.cancel_latency_ms = self.base_latency_ms
        if self.cancel_jitter_ms is None:
            self.cancel_jitter_ms = self.jitter_ms
        if (
            type(self.cancel_latency_ms) is not int
            or type(self.cancel_jitter_ms) is not int
        ):
            raise ValueError("cancel latency and jitter must use exact integers")
        if self.cancel_latency_ms < 0 or self.cancel_jitter_ms < 0:
            raise ValueError("cancel latency and jitter must be non-negative")
        if self.cancel_jitter_ms > self.cancel_latency_ms:
            raise ValueError("cancel jitter cannot exceed cancel latency")

        if self.queue_cancellation_credit is None:
            self.queue_cancellation_credit = (
                credit_from_legacy_mode(queue_cancellation_mode)
                if queue_cancellation_mode is not None
                else Decimal("1.0")
            )
            return

        credit = parse_queue_credit(self.queue_cancellation_credit)
        if queue_cancellation_mode is not None:
            legacy_credit = credit_from_legacy_mode(queue_cancellation_mode)
            if credit != legacy_credit:
                raise ValueError(
                    "queue_cancellation_credit conflicts with legacy "
                    "queue_cancellation_mode"
                )
        self.queue_cancellation_credit = credit

    @property
    def maker_rate(self) -> Decimal:
        return Decimal(self.maker_bps) / Decimal(10000)

    @property
    def taker_rate(self) -> Decimal:
        return Decimal(self.taker_bps) / Decimal(10000)

    @property
    def provenance(self) -> dict:
        """Canonical metadata required on execution-derived artifacts."""
        return {
            "execution_model_version": EXECUTION_MODEL_VERSION,
            "equal_timestamp_policy": EQUAL_TIMESTAMP_POLICY,
            "entry_latency_ms": self.base_latency_ms,
            "entry_jitter_ms": self.jitter_ms,
            "cancel_latency_ms": self.cancel_latency_ms,
            "cancel_jitter_ms": self.cancel_jitter_ms,
            "latency_seed": self.seed,
            "post_only": self.post_only,
            "queue_cancellation_credit": format(
                self.queue_cancellation_credit.normalize(), "f"
            ),
        }


@dataclass
class SimulatorStep:
    """Outcome of one scheduled exchange-side action."""

    timestamp_ms: int
    fills: List[Fill] = field(default_factory=list)
    order_arrivals: int = 0
    effective_cancels: int = 0
    cancels_too_late: int = 0


@dataclass(frozen=True)
class _MakerTradePlan:
    """One order's fully validated state transition for a recorded trade."""

    order: Order
    external_drain: Decimal
    own_drain: Decimal
    fill_qty: Decimal

    @property
    def queue_after(self) -> Decimal:
        return (
            self.order.external_queue_ahead
            - self.external_drain
            + self.order.own_queue_ahead
            - self.own_drain
        )

    @property
    def remaining_after(self) -> Decimal:
        return self.order.remaining_quantity - self.fill_qty


_ACTIVATE = "activate"
_CANCEL = "cancel"
_FIFO_RELATIVE_TOLERANCE = Decimal("1e-24")


class ExecutionSimulator:
    def __init__(self, config: SimConfig):
        self.config = config
        self._rng = random.Random(config.seed)
        # Keep cancellation jitter independent: adding a cancel must not alter
        # the future entry-latency sequence for otherwise identical orders.
        self._cancel_rng = random.Random(config.seed ^ 0xC0FFEE)

        self._orders: Dict[str, Order] = {}
        self._fills: List[Fill] = []
        self._events: List[OrderEvent] = []

        self._order_seq = 0
        self._fill_seq = 0
        self._action_seq = 0
        self._queue_seq = 0
        self._last_direct_market_time_ms: Optional[int] = None

        # (modeled arrival time, stable insertion sequence, kind, order_id)
        self._scheduled: List[Tuple[int, int, str, str]] = []

        # cancellation tracking: book qty at each price from the last depth event
        self._prev_bid_qty: Dict[Decimal, Decimal] = {}
        self._prev_ask_qty: Dict[Decimal, Decimal] = {}

        # qty traded at each price since the last on_book_update call
        # cleared in on_book_update, accumulated in on_trade
        self._traded_since_depth: Dict[
            Tuple[OrderSide, Decimal], Decimal
        ] = defaultdict(Decimal)

        # Counterfactual taker orders share displayed liquidity until the next
        # observed depth update.  The historical book itself is not mutated.
        self._taker_consumed_bids: Dict[Decimal, Decimal] = defaultdict(Decimal)
        self._taker_consumed_asks: Dict[Decimal, Decimal] = defaultdict(Decimal)

        # diagnostics
        self.postonly_rejects: int = 0

    # --- public interface ---

    def submit(self, request: OrderRequest, current_time_ms: int) -> Order:
        """
        Convert an OrderRequest into a tracked Order with latency applied.

        Returns the Order so the strategy can track its own order IDs.
        """
        if type(current_time_ms) is not int:
            raise ValueError("current_time_ms must be an exact integer")
        if not isinstance(request.side, OrderSide):
            raise ValueError("order side must be an OrderSide")
        if not isinstance(request.order_type, OrderType):
            raise ValueError("order type must be an OrderType")
        if (
            not isinstance(request.quantity, Decimal)
            or not request.quantity.is_finite()
            or request.quantity <= Decimal("0")
        ):
            raise ValueError("order quantity must be finite and positive")
        if request.order_type == OrderType.LIMIT:
            if (
                request.price is None
                or not isinstance(request.price, Decimal)
                or not request.price.is_finite()
                or request.price <= Decimal("0")
            ):
                raise ValueError("limit order price must be finite and positive")
        elif request.order_type == OrderType.MARKET and request.price is not None:
            raise ValueError("market order price must be None")

        jitter = self._rng.randint(-self.config.jitter_ms, self.config.jitter_ms)
        arrival_time_ms = current_time_ms + self.config.base_latency_ms + jitter

        self._order_seq += 1
        order = Order(
            order_id=f"ord-{self._order_seq:06d}",
            side=request.side,
            order_type=request.order_type,
            quantity=request.quantity,
            placed_time_ms=current_time_ms,
            arrival_time_ms=arrival_time_ms,
            price=request.price,
        )
        self._orders[order.order_id] = order
        self._log("placed", order.order_id, current_time_ms, {
            "side": order.side.value,
            "type": order.order_type.value,
            "price": str(order.price),
            "qty": str(order.quantity),
            "arrival_ms": arrival_time_ms,
        })
        self._schedule(arrival_time_ms, _ACTIVATE, order.order_id)
        return order

    def cancel(self, order_id: str, current_time_ms: int) -> bool:
        """
        Request cancellation with exchange-bound latency.

        ``True`` means the request was accepted for delivery, not that the
        order is already cancelled.  The order remains fillable until the
        scheduled cancellation arrival is processed.
        """
        if type(current_time_ms) is not int:
            raise ValueError("current_time_ms must be an exact integer")
        order = self._orders.get(order_id)
        if order is None or order.is_done or order.cancel_pending:
            return False

        jitter = self._cancel_rng.randint(
            -self.config.cancel_jitter_ms, self.config.cancel_jitter_ms
        )
        arrival_time_ms = current_time_ms + self.config.cancel_latency_ms + jitter
        # The exchange cannot process a cancel before it has processed the
        # corresponding new order.  Stable heap insertion resolves an exact
        # tie in favour of the earlier new-order message.
        arrival_time_ms = max(arrival_time_ms, order.arrival_time_ms)
        order.cancel_requested_time_ms = current_time_ms
        order.cancel_arrival_time_ms = arrival_time_ms
        self._log("cancel_requested", order.order_id, current_time_ms, {
            "arrival_ms": arrival_time_ms,
        })
        self._schedule(arrival_time_ms, _CANCEL, order.order_id)
        return True

    @property
    def next_scheduled_time_ms(self) -> Optional[int]:
        """Exchange time of the next private action, if any."""
        return self._scheduled[0][0] if self._scheduled else None

    @property
    def pending_scheduled_actions(self) -> int:
        return sum(
            1 for timestamp_ms, _, kind, order_id in self._scheduled
            if self._scheduled_action_is_live(timestamp_ms, kind, order_id)
        )

    @property
    def pending_cancel_actions(self) -> int:
        return sum(
            1 for timestamp_ms, _, kind, order_id in self._scheduled
            if kind == _CANCEL
            and self._scheduled_action_is_live(timestamp_ms, kind, order_id)
        )

    def process_next_scheduled(self, book: Orderbook) -> SimulatorStep:
        """Apply exactly one scheduled new-order or cancel arrival."""
        if not self._scheduled:
            raise RuntimeError("no scheduled simulator action")

        timestamp_ms, _, kind, order_id = heapq.heappop(self._scheduled)
        order = self._orders[order_id]
        step = SimulatorStep(timestamp_ms=timestamp_ms)

        if kind == _ACTIVATE:
            if order.status != OrderStatus.PENDING:
                return step
            step.order_arrivals = 1
            step.fills.extend(self._activate_order(order, book, timestamp_ms))
            return step

        if kind != _CANCEL:
            raise RuntimeError(f"unknown scheduled action: {kind}")

        # Immediate gap invalidation clears this marker and deliberately turns
        # the old heap item into a no-op.
        if order.cancel_arrival_time_ms != timestamp_ms:
            return step

        order.cancel_arrival_time_ms = None
        if order.is_done:
            step.cancels_too_late = 1
            self._log("cancel_too_late", order.order_id, timestamp_ms, {
                "status": order.status.value,
            })
            return step

        self._release_own_queue(order, timestamp_ms)
        order.status = OrderStatus.CANCELLED
        step.effective_cancels = 1
        self._log("cancelled", order.order_id, timestamp_ms, {
            "requested_ms": order.cancel_requested_time_ms,
        })
        self._prune_price_cache(order.side, order.price)
        return step

    def invalidate_all(self, timestamp_ms: int, reason: str) -> int:
        """
        Immediately invalidate local execution state after a data gap.

        This is fail-closed research bookkeeping, not a zero-latency exchange
        cancellation assumption.  Scheduled arrivals become harmless no-ops.
        """
        invalidated = 0
        terminal_status = (
            OrderStatus.EXPIRED
            if reason == "replay_end"
            else OrderStatus.INVALIDATED
        )
        event_type = "expired" if reason == "replay_end" else "invalidated"
        for order in list(self._orders.values()):
            if order.is_done:
                continue
            self._release_own_queue(order, timestamp_ms)
            order.status = terminal_status
            order.cancel_arrival_time_ms = None
            self._log(event_type, order.order_id, timestamp_ms, {
                "reason": reason,
            })
            invalidated += 1

        self._prev_bid_qty.clear()
        self._prev_ask_qty.clear()
        self._traded_since_depth.clear()
        self._taker_consumed_bids.clear()
        self._taker_consumed_asks.clear()
        return invalidated

    def observe_book_update(self, book: Orderbook, timestamp_ms: int) -> None:
        """Attribute depth decreases after all market events at this ms."""
        self._process_cancellations(book, timestamp_ms)
        self._traded_since_depth.clear()
        self._taker_consumed_bids.clear()
        self._taker_consumed_asks.clear()

    def on_book_update(self, book: Orderbook, timestamp_ms: int) -> List[Fill]:
        """
        Direct-call convenience wrapper.

        ReplayEngine is the reference API because it groups every market event
        at a timestamp before private arrivals. This helper is restricted to
        isolated tests with at most one market event per millisecond and fails
        closed if callers try to compose a same-ms batch themselves.
        """
        self._register_direct_market_event(timestamp_ms)
        fills = self._drain_scheduled(book, timestamp_ms, inclusive=False)
        self.observe_book_update(book, timestamp_ms)
        fills.extend(self._drain_scheduled(book, timestamp_ms, inclusive=True))
        return fills

    def on_trade(self, trade: TradeEvent, book: Orderbook) -> List[Fill]:
        """
        Called for each trade event.

        Accumulates the trade qty for cancellation detection, then drains
        the queue and fills any limit orders at the traded price.

          is_buyer_maker=True  -> market sell -> bids hit -> our BUY limits fill
          is_buyer_maker=False -> market buy  -> asks hit -> our SELL limits fill
        """
        self._register_direct_market_event(trade.exchange_time_ms)
        fills = self._drain_scheduled(
            book, trade.exchange_time_ms, inclusive=False
        )
        fills.extend(self.observe_trade(trade, book))
        fills.extend(self._drain_scheduled(
            book, trade.exchange_time_ms, inclusive=True
        ))
        return fills

    def observe_trade(self, trade: TradeEvent, book: Orderbook) -> List[Fill]:
        """Apply one recorded trade without advancing private actions."""
        if not trade.quantity.is_finite() or trade.quantity <= Decimal("0"):
            raise ValueError("trade quantity must be finite and positive")
        if not trade.price.is_finite() or trade.price <= Decimal("0"):
            raise ValueError("trade price must be finite and positive")
        fills = []

        target_side = OrderSide.BUY if trade.is_buyer_maker else OrderSide.SELL
        eligible = [
            order for order in self._orders.values()
            if (order.is_active
                and order.order_type == OrderType.LIMIT
                and order.side == target_side
                and order.price == trade.price)
        ]
        eligible.sort(key=lambda order: order.queue_sequence or 0)
        self._validate_fifo_positions(eligible)
        plans = self._plan_maker_trade(eligible, trade.quantity)
        self._traded_since_depth[(target_side, trade.price)] += trade.quantity
        for plan in plans:
            fills.extend(self._apply_maker_trade_plan(
                plan,
                trade_qty=trade.quantity,
                price=trade.price,
                timestamp_ms=trade.exchange_time_ms,
            ))

        self._validate_fifo_positions([
            order for order in eligible if order.is_active
        ])

        return fills

    # --- read-only access ---

    @property
    def fills(self) -> List[Fill]:
        return list(self._fills)

    @property
    def events(self) -> List[OrderEvent]:
        return list(self._events)

    @property
    def active_orders(self) -> List[Order]:
        return [o for o in self._orders.values() if o.is_active]

    @property
    def open_orders(self) -> List[Order]:
        return [o for o in self._orders.values() if not o.is_done]

    # --- private helpers ---

    def _schedule(self, timestamp_ms: int, kind: str, order_id: str) -> None:
        self._action_seq += 1
        heapq.heappush(
            self._scheduled,
            (timestamp_ms, self._action_seq, kind, order_id),
        )

    def _scheduled_action_is_live(
        self,
        timestamp_ms: int,
        kind: str,
        order_id: str,
    ) -> bool:
        order = self._orders[order_id]
        if kind == _ACTIVATE:
            return order.status == OrderStatus.PENDING
        return (
            kind == _CANCEL
            and order.cancel_arrival_time_ms == timestamp_ms
        )

    def _register_direct_market_event(self, timestamp_ms: int) -> None:
        if self._last_direct_market_time_ms == timestamp_ms:
            raise RuntimeError(
                "same-timestamp market batches require ReplayEngine"
            )
        if (
            self._last_direct_market_time_ms is not None
            and timestamp_ms < self._last_direct_market_time_ms
        ):
            raise RuntimeError("direct market-event timestamps must be monotonic")
        self._last_direct_market_time_ms = timestamp_ms

    def _drain_scheduled(
        self,
        book: Orderbook,
        timestamp_ms: int,
        *,
        inclusive: bool,
    ) -> List[Fill]:
        fills: List[Fill] = []
        while self._scheduled:
            next_time = self._scheduled[0][0]
            if next_time > timestamp_ms or (
                next_time == timestamp_ms and not inclusive
            ):
                break
            fills.extend(self.process_next_scheduled(book).fills)
        return fills

    def _activate_order(
        self,
        order: Order,
        book: Orderbook,
        timestamp_ms: int,
    ) -> List[Fill]:
        """Activate one order at its scheduled modeled arrival."""
        fills: List[Fill] = []
        self._log("arrived", order.order_id, timestamp_ms, {})

        if order.order_type == OrderType.MARKET:
            order.status = OrderStatus.ACTIVE
            fills.extend(self._execute_taker(order, book, timestamp_ms))
            return fills

        order.status = OrderStatus.ACTIVE
        if self._is_aggressive(order, book):
            if self.config.post_only:
                order.status = OrderStatus.CANCELLED
                self.postonly_rejects += 1
                self._log("cancelled", order.order_id, timestamp_ms, {
                    "reason": "post_only_would_cross",
                    "price": str(order.price),
                    "best_bid": str(book.best_bid),
                    "best_ask": str(book.best_ask),
                })
            else:
                fills.extend(self._execute_taker(order, book, timestamp_ms))
            return fills

        self._activate_limit(order, book, timestamp_ms)
        return fills

    def _activate_limit(self, order: Order, book: Orderbook, timestamp_ms: int) -> None:
        """Set initial queue position for a resting limit order."""
        earlier_orders = [
            other
            for other in self._orders.values()
            if (other.is_active
                and other.order_type == OrderType.LIMIT
                and other.side == order.side
                and other.price == order.price
                and other.order_id != order.order_id)
        ]
        own_ahead = sum((
            other.remaining_quantity
            for other in earlier_orders
        ), Decimal("0"))

        if order.side == OrderSide.BUY:
            external_ahead = book.bid_quantity(order.price)
            self._prev_bid_qty[order.price] = external_ahead
        else:
            external_ahead = book.ask_quantity(order.price)
            self._prev_ask_qty[order.price] = external_ahead

        # A later own order can never overtake an earlier live own order, even
        # when conservative queue credit leaves the earlier order behind more
        # modeled public volume than remains displayed.  Carry that residual
        # queue forward as a FIFO floor.
        predecessor_tail = max((
            other.queue_ahead + other.remaining_quantity
            for other in earlier_orders
            if other.queue_ahead is not None
        ), default=Decimal("0"))
        visible_external = external_ahead
        fifo_floor_adjustment = max(
            Decimal("0"),
            predecessor_tail - (external_ahead + own_ahead),
        )
        external_ahead += fifo_floor_adjustment

        self._queue_seq += 1
        order.queue_sequence = self._queue_seq
        order.external_queue_ahead = external_ahead
        order.own_queue_ahead = own_ahead
        order.visible_queue_at_arrival = visible_external
        order.fifo_floor_adjustment = fifo_floor_adjustment
        order.queue_ahead = external_ahead + own_ahead

        self._log("queued", order.order_id, timestamp_ms, {
            "price": str(order.price),
            "queue_ahead": str(order.queue_ahead),
            "external_queue_ahead": str(order.external_queue_ahead),
            "own_queue_ahead": str(order.own_queue_ahead),
            "visible_queue_at_arrival": str(order.visible_queue_at_arrival),
            "fifo_floor_adjustment": str(order.fifo_floor_adjustment),
        })

    def _is_aggressive(self, order: Order, book: Orderbook) -> bool:
        """True if this limit order crosses the spread and should fill as a taker."""
        if order.side == OrderSide.BUY:
            ask = book.best_ask
            return ask is not None and order.price >= ask
        else:
            bid = book.best_bid
            return bid is not None and order.price <= bid

    def _execute_taker(self, order: Order, book: Orderbook, timestamp_ms: int) -> List[Fill]:
        """
        Fill an order as a taker, walking levels until filled or book runs out.

        Used for market orders and aggressive limits. Fills at book prices
        (the resting price), not at the order's limit price.
        """
        fills = []
        levels = (
            book.ask_levels(book.ask_count)
            if order.side == OrderSide.BUY
            else book.bid_levels(book.bid_count)
        )
        consumed = (
            self._taker_consumed_asks
            if order.side == OrderSide.BUY
            else self._taker_consumed_bids
        )

        for level_price, level_qty in levels:
            if order.remaining_quantity <= 0:
                break
            if order.order_type == OrderType.LIMIT:
                if order.side == OrderSide.BUY and level_price > order.price:
                    break
                if order.side == OrderSide.SELL and level_price < order.price:
                    break
            available_qty = max(
                Decimal("0"), level_qty - consumed[level_price]
            )
            if available_qty <= Decimal("0"):
                continue
            fill_qty = min(order.remaining_quantity, available_qty)
            fee = fill_qty * level_price * self.config.taker_rate
            fills.append(self._create_fill(
                order_id=order.order_id,
                side=order.side,
                price=level_price,
                quantity=fill_qty,
                is_maker=False,
                timestamp_ms=timestamp_ms,
                fee=fee,
            ))
            order.filled_quantity += fill_qty
            consumed[level_price] += fill_qty

        if order.remaining_quantity <= 0:
            order.status = OrderStatus.FILLED
            self._log("filled", order.order_id, timestamp_ms, {"fills": len(fills)})
        else:
            # Book ran out of liquidity - cancel the unfilled remainder
            order.status = OrderStatus.CANCELLED
            self._log("cancelled", order.order_id, timestamp_ms, {
                "reason": "insufficient_liquidity",
                "filled_qty": str(order.filled_quantity),
            })

        return fills

    @staticmethod
    def _plan_maker_trade(
        orders: List[Order],
        trade_qty: Decimal,
    ) -> List[_MakerTradePlan]:
        """Build a conservation-safe maker-fill plan before mutating orders."""
        plans: List[_MakerTradePlan] = []
        earlier_own_filled = Decimal("0")
        aggregate_fill = Decimal("0")

        for order in orders:
            queue_before = order.queue_ahead
            if queue_before is None:
                raise RuntimeError("active maker order has no queue position")

            total_drain = min(trade_qty, queue_before)
            # When this trade fills an earlier simulated order, that filled
            # quantity is the portion of this later order's own-order segment
            # consumed by the same trade.  Allocate it there before applying
            # the remaining advancement to modeled public volume.
            own_drain = min(
                earlier_own_filled,
                order.own_queue_ahead,
                total_drain,
            )
            external_drain = total_drain - own_drain
            if external_drain > order.external_queue_ahead:
                deficit = external_drain - order.external_queue_ahead
                tolerance = ExecutionSimulator._fifo_tolerance(
                    trade_qty,
                    queue_before,
                    order.external_queue_ahead,
                    order.own_queue_ahead,
                )
                own_capacity = order.own_queue_ahead - own_drain
                if deficit > tolerance or own_capacity + tolerance < deficit:
                    raise RuntimeError(
                        "inconsistent simulated FIFO decomposition: "
                        f"{order.order_id} external drain={external_drain}, "
                        f"external ahead={order.external_queue_ahead}"
                    )
                # Decimal division in proportional queue credit can leave an
                # ulp-sized mismatch between otherwise identical ranks. Move
                # that residue to the own segment without changing total drain.
                external_drain = order.external_queue_ahead
                own_drain = min(
                    order.own_queue_ahead,
                    own_drain + deficit,
                )

            available_fill = max(
                Decimal("0"), trade_qty - aggregate_fill
            )
            fill_qty = min(
                trade_qty - total_drain,
                order.remaining_quantity,
                available_fill,
            )
            plans.append(_MakerTradePlan(
                order=order,
                external_drain=external_drain,
                own_drain=own_drain,
                fill_qty=fill_qty,
            ))
            earlier_own_filled += fill_qty
            aggregate_fill += fill_qty

        if aggregate_fill > trade_qty:
            raise RuntimeError(
                "simulated maker fills exceeded recorded trade quantity: "
                f"fills={aggregate_fill}, trade={trade_qty}"
            )
        ExecutionSimulator._validate_planned_fifo_positions(plans)
        return plans

    @staticmethod
    def _validate_planned_fifo_positions(
        plans: List[_MakerTradePlan],
    ) -> None:
        """Validate every predicted post-trade rank before any fill is logged."""
        live = [plan for plan in plans if plan.remaining_after > Decimal("0")]
        for earlier, later in zip(live, live[1:]):
            minimum_later_ahead = (
                earlier.queue_after + earlier.remaining_after
            )
            tolerance = ExecutionSimulator._fifo_tolerance(
                earlier.queue_after,
                earlier.remaining_after,
                later.queue_after,
            )
            if later.queue_after + tolerance < minimum_later_ahead:
                raise RuntimeError(
                    "planned trade would overlap simulated FIFO positions: "
                    f"{earlier.order.order_id} tail={minimum_later_ahead}, "
                    f"{later.order.order_id} ahead={later.queue_after}"
                )

    def _apply_maker_trade_plan(
        self,
        plan: _MakerTradePlan,
        *,
        trade_qty: Decimal,
        price: Decimal,
        timestamp_ms: int,
    ) -> List[Fill]:
        """Apply one prevalidated FIFO plan for a recorded maker-side trade."""
        fills = []
        order = plan.order
        external_drain = plan.external_drain
        own_drain = plan.own_drain
        fill_qty = plan.fill_qty
        queue_ahead_before_trade = order.queue_ahead
        drained = external_drain + own_drain
        order.external_queue_ahead -= external_drain
        order.own_queue_ahead -= own_drain
        order.queue_ahead = (
            order.external_queue_ahead + order.own_queue_ahead
        )
        if drained > Decimal("0"):
            self._log("queue_drain", order.order_id, timestamp_ms, {
                "reason": "trade",
                "price": str(price),
                "trade_qty": str(trade_qty),
                "drained_qty": str(drained),
                "external_drained_qty": str(external_drain),
                "own_drained_qty": str(own_drain),
                "queue_before": str(queue_ahead_before_trade),
                "queue_after": str(order.queue_ahead),
            })

        if fill_qty > Decimal("0"):
            queue_ahead_before_fill = order.queue_ahead
            fee = fill_qty * price * self.config.maker_rate
            fills.append(self._create_fill(
                order_id=order.order_id,
                side=order.side,
                price=price,
                quantity=fill_qty,
                is_maker=True,
                timestamp_ms=timestamp_ms,
                fee=fee,
            ))
            order.filled_quantity += fill_qty

            if order.remaining_quantity <= Decimal("0"):
                order.status = OrderStatus.FILLED
                self._log("filled", order.order_id, timestamp_ms, {
                    "fill_qty": str(fill_qty),
                    "fill_price": str(price),
                    "trade_qty": str(trade_qty),
                    "queue_ahead_before_trade": str(queue_ahead_before_trade),
                    "queue_ahead_before_fill": str(queue_ahead_before_fill),
                })
                self._prune_price_cache(order.side, order.price)
            else:
                order.status = OrderStatus.PARTIAL
                self._log("partial_fill", order.order_id, timestamp_ms, {
                    "filled": str(order.filled_quantity),
                    "remaining": str(order.remaining_quantity),
                    "fill_qty": str(fill_qty),
                    "fill_price": str(price),
                    "trade_qty": str(trade_qty),
                    "queue_ahead_before_trade": str(queue_ahead_before_trade),
                    "queue_ahead_before_fill": str(queue_ahead_before_fill),
                })

        return fills

    def _process_cancellations(self, book: Orderbook, timestamp_ms: int) -> None:
        """
        Proportional queue improvement when book qty drops without a trade.

        If the book qty at our limit price decreases by X, and trades only
        account for Y of that decrease, the remaining (X - Y) is cancellations.
        We assume cancellations are uniformly distributed in the queue, so our
        queue_ahead shrinks proportionally.

        ReplayEngine calls this only after all same-millisecond depth and trade
        events have been observed, so tied trade quantity cannot also be
        counted as cancellation-driven improvement.  Direct callers must use
        the engine for multi-event timestamp batches.
        """
        # Collect unique (side, price) pairs from active limit orders
        bid_prices: Set[Decimal] = set()
        ask_prices: Set[Decimal] = set()
        for order in self._orders.values():
            if (order.is_active
                    and order.order_type == OrderType.LIMIT
                    and order.queue_ahead is not None):
                if order.side == OrderSide.BUY:
                    bid_prices.add(order.price)
                else:
                    ask_prices.add(order.price)

        for price in bid_prices:
            new_qty = book.bid_quantity(price)
            prev_qty = self._prev_bid_qty.get(price, new_qty)
            cancel_frac = self._cancel_fraction(
                OrderSide.BUY, price, prev_qty, new_qty
            )
            if cancel_frac > Decimal("0"):
                for order in self._orders.values():
                    if (order.is_active
                            and order.order_type == OrderType.LIMIT
                            and order.side == OrderSide.BUY
                            and order.price == price
                            and order.queue_ahead is not None
                            and order.external_queue_ahead > Decimal("0")):
                        queue_before = order.queue_ahead
                        order.external_queue_ahead = max(
                            Decimal("0"),
                            order.external_queue_ahead
                            * (Decimal("1") - cancel_frac),
                        )
                        order.queue_ahead = (
                            order.external_queue_ahead + order.own_queue_ahead
                        )
                        drained = queue_before - order.queue_ahead
                        if drained > Decimal("0"):
                            self._log("queue_drain", order.order_id, timestamp_ms, {
                                "reason": "cancellation",
                                "price": str(price),
                                "prev_book_qty": str(prev_qty),
                                "new_book_qty": str(new_qty),
                                "cancel_frac": str(cancel_frac),
                                "drained_qty": str(drained),
                                "queue_before": str(queue_before),
                                "queue_after": str(order.queue_ahead),
                            })
            self._prev_bid_qty[price] = new_qty

        for price in ask_prices:
            new_qty = book.ask_quantity(price)
            prev_qty = self._prev_ask_qty.get(price, new_qty)
            cancel_frac = self._cancel_fraction(
                OrderSide.SELL, price, prev_qty, new_qty
            )
            if cancel_frac > Decimal("0"):
                for order in self._orders.values():
                    if (order.is_active
                            and order.order_type == OrderType.LIMIT
                            and order.side == OrderSide.SELL
                            and order.price == price
                            and order.queue_ahead is not None
                            and order.external_queue_ahead > Decimal("0")):
                        queue_before = order.queue_ahead
                        order.external_queue_ahead = max(
                            Decimal("0"),
                            order.external_queue_ahead
                            * (Decimal("1") - cancel_frac),
                        )
                        order.queue_ahead = (
                            order.external_queue_ahead + order.own_queue_ahead
                        )
                        drained = queue_before - order.queue_ahead
                        if drained > Decimal("0"):
                            self._log("queue_drain", order.order_id, timestamp_ms, {
                                "reason": "cancellation",
                                "price": str(price),
                                "prev_book_qty": str(prev_qty),
                                "new_book_qty": str(new_qty),
                                "cancel_frac": str(cancel_frac),
                                "drained_qty": str(drained),
                                "queue_before": str(queue_before),
                                "queue_after": str(order.queue_ahead),
                            })
            self._prev_ask_qty[price] = new_qty

        self._prev_bid_qty = {
            price: qty for price, qty in self._prev_bid_qty.items()
            if price in bid_prices
        }
        self._prev_ask_qty = {
            price: qty for price, qty in self._prev_ask_qty.items()
            if price in ask_prices
        }

    def _cancel_fraction(
        self,
        side: OrderSide,
        price: Decimal,
        prev_qty: Decimal,
        new_qty: Decimal,
    ) -> Decimal:
        """Fraction of prev_qty that was cancelled (not traded)."""
        if self.config.queue_cancellation_credit == Decimal("0"):
            return Decimal("0")
        if new_qty >= prev_qty or prev_qty <= Decimal("0"):
            return Decimal("0")
        decrease = prev_qty - new_qty
        traded = self._traded_since_depth.get((side, price), Decimal("0"))
        cancellation = max(Decimal("0"), decrease - traded)
        return (cancellation / prev_qty) * self.config.queue_cancellation_credit

    def _release_own_queue(self, order: Order, timestamp_ms: int) -> None:
        """Release this order's remaining size from later own FIFO queues."""
        if order.queue_sequence is None or not order.is_active:
            return

        released = order.remaining_quantity
        if released <= Decimal("0"):
            return

        for later in self._orders.values():
            if not (
                later.is_active
                and later.order_type == OrderType.LIMIT
                and later.side == order.side
                and later.price == order.price
                and later.queue_sequence is not None
                and later.queue_sequence > order.queue_sequence
            ):
                continue
            drained = min(released, later.own_queue_ahead)
            if drained <= Decimal("0"):
                continue
            queue_before = later.queue_ahead
            later.own_queue_ahead -= drained
            later.queue_ahead = (
                later.external_queue_ahead + later.own_queue_ahead
            )
            self._log("queue_drain", later.order_id, timestamp_ms, {
                "reason": "own_order_cancel",
                "ahead_order_id": order.order_id,
                "drained_qty": str(drained),
                "queue_before": str(queue_before),
                "queue_after": str(later.queue_ahead),
            })

    @staticmethod
    def _validate_fifo_positions(orders: List[Order]) -> None:
        """Assert non-overlapping own FIFO positions before mutating fills."""
        ordered = sorted(orders, key=lambda order: order.queue_sequence or 0)
        for earlier, later in zip(ordered, ordered[1:]):
            minimum_later_ahead = (
                earlier.queue_ahead + earlier.remaining_quantity
            )
            tolerance = ExecutionSimulator._fifo_tolerance(
                earlier.queue_ahead,
                earlier.remaining_quantity,
                later.queue_ahead,
            )
            if later.queue_ahead + tolerance < minimum_later_ahead:
                raise RuntimeError(
                    "overlapping simulated FIFO positions: "
                    f"{earlier.order_id} tail={minimum_later_ahead}, "
                    f"{later.order_id} ahead={later.queue_ahead}"
                )

    @staticmethod
    def _fifo_tolerance(*values: Decimal) -> Decimal:
        """Scale-aware tolerance only for Decimal arithmetic round-off."""
        scale = max(
            (abs(value) for value in values),
            default=Decimal("1"),
        )
        return max(Decimal("1"), scale) * _FIFO_RELATIVE_TOLERANCE

    def _prune_price_cache(
        self,
        side: OrderSide,
        price: Optional[Decimal],
    ) -> None:
        if price is None:
            return
        has_active = any(
            order.is_active
            and order.order_type == OrderType.LIMIT
            and order.side == side
            and order.price == price
            for order in self._orders.values()
        )
        if has_active:
            return
        if side == OrderSide.BUY:
            self._prev_bid_qty.pop(price, None)
        else:
            self._prev_ask_qty.pop(price, None)

    def _create_fill(
        self,
        order_id: str,
        side: OrderSide,
        price: Decimal,
        quantity: Decimal,
        is_maker: bool,
        timestamp_ms: int,
        fee: Decimal,
    ) -> Fill:
        self._fill_seq += 1
        fill = Fill(
            fill_id=f"fill-{self._fill_seq:06d}",
            order_id=order_id,
            side=side,
            price=price,
            quantity=quantity,
            is_maker=is_maker,
            timestamp_ms=timestamp_ms,
            fee=fee,
        )
        self._fills.append(fill)
        return fill

    def _log(self, event_type: str, order_id: str, timestamp_ms: int, detail: dict) -> None:
        self._events.append(OrderEvent(
            order_id=order_id,
            timestamp_ms=timestamp_ms,
            event_type=event_type,
            detail=detail,
        ))
