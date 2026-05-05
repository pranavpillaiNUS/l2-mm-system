"""
Base class for market-making strategies.

Handles the plumbing that every MM strategy needs:
  - Order tracking (which orders are mine, are they still active)
  - Cancel/replace logic (cancel stale quotes, place new ones)
  - Inventory tracking (net position from fills)
  - Quoting throttle (don't requote if desired prices haven't changed)

Subclasses only implement one method: compute_quotes(book, timestamp_ms),
which returns the desired bid and ask prices. Everything else — deciding
when to cancel, building OrderRequests, tracking fills — lives here.
"""
from abc import ABC, abstractmethod
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional, Tuple

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
        max_position: absolute position limit — stop quoting the side
                      that would increase exposure beyond this
        tick_size: minimum price increment for rounding quotes
    """

    def __init__(
        self,
        order_qty: Decimal,
        max_position: Decimal,
        tick_size: Decimal = Decimal("0.01"),
    ):
        self.order_qty = order_qty
        self.max_position = max_position
        self.tick_size = tick_size

        # Order tracking — shared refs with the simulator, so status
        # updates (filled, cancelled) are visible here automatically.
        self._bid_order: Optional[Order] = None
        self._ask_order: Optional[Order] = None

        # Inventory: positive = long, negative = short
        self.position: Decimal = Decimal("0")
        self.realized_pnl: Decimal = Decimal("0")
        self.total_fees: Decimal = Decimal("0")
        self.fill_count: int = 0

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

        # Round to tick
        desired_bid = self._round_to_tick(desired_bid) if desired_bid is not None else None
        desired_ask = self._round_to_tick(desired_ask) if desired_ask is not None else None

        # Position limits: don't quote the side that would increase exposure
        if self.position >= self.max_position:
            desired_bid = None  # already max long, don't buy more
        if self.position <= -self.max_position:
            desired_ask = None  # already max short, don't sell more

        return self._requote(desired_bid, desired_ask, timestamp_ms)

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

    def _round_to_tick(self, price: Decimal) -> Decimal:
        """Round price down to the nearest tick."""
        return (price / self.tick_size).to_integral_value(ROUND_DOWN) * self.tick_size
