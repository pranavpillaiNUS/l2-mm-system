"""
Base class for market-making strategies.

Handles the plumbing that every MM strategy needs:
  - Order tracking (which orders are mine, are they still active)
  - Cancel/replace logic (cancel stale quotes, place new ones)
  - Inventory tracking (net position from fills)
  - Quoting throttle (don't requote if desired prices haven't changed)
  - Post-only enforcement (never place a quote that would cross the spread)

Subclasses only implement one method: compute_quotes(book, timestamp_ms),
which returns the desired bid and ask prices. Everything else - deciding
when to cancel, building OrderRequests, tracking fills - lives here.
"""
from abc import ABC, abstractmethod
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import List, Optional, Tuple

from src.execution.order import Fill, Order, OrderRequest, OrderSide, OrderType
from src.replay.engine import Action, CancelRequest
from src.replay.orderbook import Orderbook
from src.replay.trade_parser import TradeEvent


class BaseMMStrategy(ABC):
    """
    Abstract base for market-making strategies.

    Subclasses implement compute_quotes() to return desired bid/ask prices.
    The base class decides when to cancel and replace, tracks inventory,
    and builds the Action lists the engine expects.

    Parameters:
        order_qty: size of each quote (in base currency, e.g. BTC)
        max_position: absolute position limit - stop quoting the side
                      that would increase exposure beyond this
        tick_size: minimum price increment for rounding quotes
    """

    def __init__(
        self,
        order_qty: Decimal,
        max_position: Decimal,
        tick_size: Decimal = Decimal("0.01"),
        requote_interval_ms: int = 0,
    ):
        self.order_qty = order_qty
        self.max_position = max_position
        self.tick_size = tick_size
        self.requote_interval_ms = requote_interval_ms

        # Order tracking - shared refs with the simulator, so status
        # updates (filled, cancelled) are visible here automatically.
        self._bid_order: Optional[Order] = None
        self._ask_order: Optional[Order] = None
        self._last_requote_ms: Optional[int] = None

        # Inventory: positive = long, negative = short
        self.position: Decimal = Decimal("0")
        self.realized_pnl: Decimal = Decimal("0")
        self.total_fees: Decimal = Decimal("0")
        self.fill_count: int = 0

        # diagnostics - track how many quotes got suppressed by post-only
        self.postonly_suppressed: int = 0

    # --- interface for subclasses ---

    @abstractmethod
    def compute_quotes(
        self, book: Orderbook, timestamp_ms: int,
    ) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """
        Return (bid_price, ask_price) for the desired quotes.

        Return None for either side to skip quoting that side (e.g. when
        at position limit). Prices will be rounded to tick_size by the
        base class before placement.
        """
        ...

    # --- engine callbacks ---

    def on_book_update(self, book: Orderbook, timestamp_ms: int) -> List[Action]:
        if book.best_bid is None or book.best_ask is None:
            return []

        desired_bid, desired_ask = self.compute_quotes(book, timestamp_ms)

        # Round bids down and asks up so tick normalization never makes
        # quotes more aggressive than the strategy intended.
        desired_bid = self._round_bid(desired_bid) if desired_bid is not None else None
        desired_ask = self._round_ask(desired_ask) if desired_ask is not None else None

        # Position limits: don't quote the side that would increase exposure
        if self.position >= self.max_position:
            desired_bid = None  # already max long, don't buy more
        if self.position <= -self.max_position:
            desired_ask = None  # already max short, don't sell more

        # Post-only: never place a quote that would cross the spread.
        # A real exchange would reject these (post-only / maker-only flag).
        # Without this check, the order arrives ~100ms later (next depth event),
        # the book may have moved, and the simulator executes it as a taker.
        # We prevent that by checking against the CURRENT book before submission.
        if desired_bid is not None and desired_bid >= book.best_ask:
            self.postonly_suppressed += 1
            desired_bid = None
        if desired_ask is not None and desired_ask <= book.best_bid:
            self.postonly_suppressed += 1
            desired_ask = None

        current_bid = self._live_price(self._bid_order)
        current_ask = self._live_price(self._ask_order)
        if self._should_hold_quotes(
            desired_bid, desired_ask, current_bid, current_ask, timestamp_ms,
        ):
            return []

        actions = self._requote(desired_bid, desired_ask, timestamp_ms)
        if actions:
            self._last_requote_ms = timestamp_ms
        return actions

    def on_trade(self, trade: TradeEvent, book: Orderbook) -> List[Action]:
        return []

    def on_fill(self, fill: Fill) -> List[Action]:
        # Update inventory
        if fill.side == OrderSide.BUY:
            self.position += fill.quantity
            self.realized_pnl -= fill.notional
        else:
            self.position -= fill.quantity
            self.realized_pnl += fill.notional

        self.total_fees += fill.fee
        self.fill_count += 1
        return []

    def on_order_placed(self, request: OrderRequest, order: Order) -> None:
        if order.side == OrderSide.BUY:
            self._bid_order = order
        else:
            self._ask_order = order

    # --- internal logic ---

    def _requote(
        self,
        desired_bid: Optional[Decimal],
        desired_ask: Optional[Decimal],
        timestamp_ms: int,
    ) -> List[Action]:
        """
        Compare desired quotes to current orders. Cancel stale ones,
        place new ones. Skip if prices haven't changed.
        """
        actions: List[Action] = []

        # --- bid side ---
        current_bid = self._live_price(self._bid_order)

        if desired_bid != current_bid:
            # Cancel old bid if still active
            if self._bid_order is not None and not self._bid_order.is_done:
                actions.append(CancelRequest(self._bid_order.order_id))
                self._bid_order = None

            # Place new bid
            if desired_bid is not None:
                actions.append(OrderRequest(
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    quantity=self.order_qty,
                    price=desired_bid,
                ))

        # --- ask side ---
        current_ask = self._live_price(self._ask_order)

        if desired_ask != current_ask:
            if self._ask_order is not None and not self._ask_order.is_done:
                actions.append(CancelRequest(self._ask_order.order_id))
                self._ask_order = None

            if desired_ask is not None:
                actions.append(OrderRequest(
                    side=OrderSide.SELL,
                    order_type=OrderType.LIMIT,
                    quantity=self.order_qty,
                    price=desired_ask,
                ))

        return actions

    def _live_price(self, order: Optional[Order]) -> Optional[Decimal]:
        """Price of an order if it's still live, else None."""
        if order is None or order.is_done:
            return None
        return order.price

    def _should_hold_quotes(
        self,
        desired_bid: Optional[Decimal],
        desired_ask: Optional[Decimal],
        current_bid: Optional[Decimal],
        current_ask: Optional[Decimal],
        timestamp_ms: int,
    ) -> bool:
        """
        Hold existing quotes during the requote interval.

        This only throttles pure price refreshes. It does not block placing a
        missing side after a fill, and it does not delay risk-reducing cancels
        when position limits suppress one side.
        """
        if self.requote_interval_ms <= 0 or self._last_requote_ms is None:
            return False
        if timestamp_ms - self._last_requote_ms >= self.requote_interval_ms:
            return False

        return (
            self._side_can_hold(desired_bid, current_bid)
            and self._side_can_hold(desired_ask, current_ask)
        )

    def _side_can_hold(
        self,
        desired_price: Optional[Decimal],
        current_price: Optional[Decimal],
    ) -> bool:
        if desired_price is None:
            return current_price is None
        return current_price is not None

    def _round_bid(self, price: Decimal) -> Decimal:
        """Round bid price down to the nearest tick."""
        return (price / self.tick_size).to_integral_value(ROUND_DOWN) * self.tick_size

    def _round_ask(self, price: Decimal) -> Decimal:
        """Round ask price up to the nearest tick."""
        return (price / self.tick_size).to_integral_value(ROUND_UP) * self.tick_size