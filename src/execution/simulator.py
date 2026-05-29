"""
Execution simulator for L2 replay.

Takes OrderRequests from strategies, applies latency, models FIFO queue
position, and produces Fills as trades happen in the replayed data.

Key design decisions (all in design notes):
  - Latency uses seeded PRNG - determinism over realism
  - queue_ahead set at order arrival = book qty at that price level
  - Trades drain queue_ahead from the front
  - Book qty decreases without a trade -> proportional queue improvement
  - Market orders walk levels greedily; unfilled remainder is cancelled
  - Aggressive limit orders: post_only=True cancels them (default),
    post_only=False executes them as takers
"""
import random
from collections import defaultdict
from dataclasses import InitVar, dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Set

from src.execution.order import (
    Fill, Order, OrderEvent, OrderRequest,
    OrderSide, OrderStatus, OrderType,
)
from src.execution.queue_credit import credit_from_legacy_mode, parse_queue_credit
from src.replay.orderbook import Orderbook
from src.replay.trade_parser import TradeEvent


@dataclass
class SimConfig:
    base_latency_ms: int   # baseline one-way latency
    jitter_ms: int         # arrival = base_latency +/- uniform(jitter_ms)
    maker_bps: int         # fee rate for resting limit fills
    taker_bps: int         # fee rate for market orders and aggressive limits
    seed: int = 42
    post_only: bool = True  # cancel limit orders that would cross the spread
    queue_cancellation_credit: Decimal | str | float | None = None
    queue_cancellation_mode: InitVar[str | None] = None

    def __post_init__(self, queue_cancellation_mode: str | None) -> None:
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


class ExecutionSimulator:
    def __init__(self, config: SimConfig):
        self.config = config
        self._rng = random.Random(config.seed)

        self._orders: Dict[str, Order] = {}
        self._fills: List[Fill] = []
        self._events: List[OrderEvent] = []

        self._order_seq = 0
        self._fill_seq = 0

        # cancellation tracking: book qty at each price from the last depth event
        self._prev_bid_qty: Dict[Decimal, Decimal] = {}
        self._prev_ask_qty: Dict[Decimal, Decimal] = {}

        # qty traded at each price since the last on_book_update call
        # cleared in on_book_update, accumulated in on_trade
        self._traded_since_depth: Dict[Decimal, Decimal] = defaultdict(Decimal)

        # diagnostics
        self.postonly_rejects: int = 0

    # --- public interface ---

    def submit(self, request: OrderRequest, current_time_ms: int) -> Order:
        """
        Convert an OrderRequest into a tracked Order with latency applied.

        Returns the Order so the strategy can track its own order IDs.
        """
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
        return order

    def cancel(self, order_id: str, current_time_ms: int) -> bool:
        """Cancel an active order. Returns False if not found or already done."""
        order = self._orders.get(order_id)
        if order is None or order.is_done:
            return False
        order.status = OrderStatus.CANCELLED
        self._log("cancelled", order.order_id, current_time_ms, {})
        return True

    def on_book_update(self, book: Orderbook, timestamp_ms: int) -> List[Fill]:
        """
        Called after each depth event is applied to the book.

        1. Activates any pending orders that have now arrived.
        2. Detects cancellations at active limit order price levels and
           applies proportional queue improvement.
        3. Resets the traded-since-depth accumulator for the next period.
        """
        fills = []

        for order in list(self._orders.values()):
            if order.status != OrderStatus.PENDING:
                continue
            if order.arrival_time_ms > timestamp_ms:
                continue

            self._log("arrived", order.order_id, timestamp_ms, {})

            if order.order_type == OrderType.MARKET:
                order.status = OrderStatus.ACTIVE
                fills.extend(self._execute_taker(order, book, timestamp_ms))

            elif order.order_type == OrderType.LIMIT:
                order.status = OrderStatus.ACTIVE
                if self._is_aggressive(order, book):
                    if self.config.post_only:
                        # post-only: reject instead of crossing the spread
                        order.status = OrderStatus.CANCELLED
                        self.postonly_rejects += 1
                        self._log("cancelled", order.order_id, timestamp_ms, {
                            "reason": "post_only_would_cross",
                        })
                    else:
                        fills.extend(self._execute_taker(order, book, timestamp_ms))
                else:
                    self._activate_limit(order, book, timestamp_ms)

        self._process_cancellations(book, timestamp_ms)
        self._traded_since_depth.clear()

        return fills

    def on_trade(self, trade: TradeEvent, book: Orderbook) -> List[Fill]:
        """
        Called for each trade event.

        Accumulates the trade qty for cancellation detection, then drains
        the queue and fills any limit orders at the traded price.

          is_buyer_maker=True  -> market sell -> bids hit -> our BUY limits fill
          is_buyer_maker=False -> market buy  -> asks hit -> our SELL limits fill
        """
        fills = []
        self._traded_since_depth[trade.price] += trade.quantity

        target_side = OrderSide.BUY if trade.is_buyer_maker else OrderSide.SELL

        for order in list(self._orders.values()):
            if (order.is_active
                    and order.order_type == OrderType.LIMIT
                    and order.side == target_side
                    and order.price == trade.price):
                fills.extend(
                    self._drain_and_fill(
                        order, trade.quantity, trade.price, trade.exchange_time_ms
                    )
                )

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

    def _activate_limit(self, order: Order, book: Orderbook, timestamp_ms: int) -> None:
        """Set initial queue position for a resting limit order."""
        if order.side == OrderSide.BUY:
            order.queue_ahead = book._bids.get(order.price, Decimal("0"))
            self._prev_bid_qty.setdefault(order.price, order.queue_ahead)
        else:
            order.queue_ahead = book._asks.get(order.price, Decimal("0"))
            self._prev_ask_qty.setdefault(order.price, order.queue_ahead)

        self._log("queued", order.order_id, timestamp_ms, {
            "price": str(order.price),
            "queue_ahead": str(order.queue_ahead),
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
            book.ask_levels(len(book._asks))
            if order.side == OrderSide.BUY
            else book.bid_levels(len(book._bids))
        )

        for level_price, level_qty in levels:
            if order.remaining_quantity <= 0:
                break
            fill_qty = min(order.remaining_quantity, level_qty)
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

    def _drain_and_fill(
        self,
        order: Order,
        trade_qty: Decimal,
        price: Decimal,
        timestamp_ms: int,
    ) -> List[Fill]:
        """
        FIFO queue logic for a trade at our limit order's price.

        The trade volume drains from the front of the queue:
          - First it consumes queue_ahead (volume ahead of us)
          - Whatever remains after that fills our order
        """
        fills = []
        remaining = trade_qty
        queue_ahead_before_trade = order.queue_ahead

        if order.queue_ahead > Decimal("0"):
            drained = min(remaining, order.queue_ahead)
            order.queue_ahead -= drained
            remaining -= drained
            if drained > Decimal("0"):
                self._log("queue_drain", order.order_id, timestamp_ms, {
                    "reason": "trade",
                    "price": str(price),
                    "trade_qty": str(trade_qty),
                    "drained_qty": str(drained),
                    "queue_before": str(queue_ahead_before_trade),
                    "queue_after": str(order.queue_ahead),
                })

        if remaining > Decimal("0") and order.queue_ahead <= Decimal("0"):
            queue_ahead_before_fill = order.queue_ahead
            fill_qty = min(remaining, order.remaining_quantity)
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

        Known approximation: at same-millisecond depth+trade ties, the depth
        event is processed first, so _traded_since_depth may not yet include
        the same-ms trade. This slightly overestimates cancellations at those
        ties - a conservative bias (we think we move up the queue a bit faster
        than we actually do).
        """
        # Collect unique (side, price) pairs from active limit orders
        bid_prices: Set[Decimal] = set()
        ask_prices: Set[Decimal] = set()
        for order in self._orders.values():
            if (order.is_active
                    and order.order_type == OrderType.LIMIT
                    and order.queue_ahead is not None
                    and order.queue_ahead > Decimal("0")):
                if order.side == OrderSide.BUY:
                    bid_prices.add(order.price)
                else:
                    ask_prices.add(order.price)

        for price in bid_prices:
            new_qty = book._bids.get(price, Decimal("0"))
            prev_qty = self._prev_bid_qty.get(price, new_qty)
            cancel_frac = self._cancel_fraction(price, prev_qty, new_qty)
            if cancel_frac > Decimal("0"):
                for order in self._orders.values():
                    if (order.is_active
                            and order.order_type == OrderType.LIMIT
                            and order.side == OrderSide.BUY
                            and order.price == price
                            and order.queue_ahead is not None):
                        queue_before = order.queue_ahead
                        order.queue_ahead = max(
                            Decimal("0"),
                            order.queue_ahead * (Decimal("1") - cancel_frac),
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
            new_qty = book._asks.get(price, Decimal("0"))
            prev_qty = self._prev_ask_qty.get(price, new_qty)
            cancel_frac = self._cancel_fraction(price, prev_qty, new_qty)
            if cancel_frac > Decimal("0"):
                for order in self._orders.values():
                    if (order.is_active
                            and order.order_type == OrderType.LIMIT
                            and order.side == OrderSide.SELL
                            and order.price == price
                            and order.queue_ahead is not None):
                        queue_before = order.queue_ahead
                        order.queue_ahead = max(
                            Decimal("0"),
                            order.queue_ahead * (Decimal("1") - cancel_frac),
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

    def _cancel_fraction(
        self, price: Decimal, prev_qty: Decimal, new_qty: Decimal
    ) -> Decimal:
        """Fraction of prev_qty that was cancelled (not traded)."""
        if self.config.queue_cancellation_credit == Decimal("0"):
            return Decimal("0")
        if new_qty >= prev_qty or prev_qty <= Decimal("0"):
            return Decimal("0")
        decrease = prev_qty - new_qty
        traded = self._traded_since_depth.get(price, Decimal("0"))
        cancellation = max(Decimal("0"), decrease - traded)
        return (cancellation / prev_qty) * self.config.queue_cancellation_credit

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
