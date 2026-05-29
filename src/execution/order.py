"""
Data types for the execution simulator.

Two separate types for orders:
  OrderRequest - what a strategy submits (intent only, no lifecycle state)
  Order        - what the simulator tracks (full lifecycle, mutable state)

This separation keeps strategy code clean: strategies don't know about latency
or queue position, they just express what they want to do.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    LIMIT = "limit"
    MARKET = "market"


class OrderStatus(Enum):
    PENDING = "pending"       # placed by strategy, not yet arrived at exchange
    ACTIVE = "active"         # arrived, resting in book (limit) or executing (market)
    PARTIAL = "partial"       # partially filled, still active
    FILLED = "filled"         # completely filled
    CANCELLED = "cancelled"   # cancelled before full fill


@dataclass
class OrderRequest:
    """
    What a strategy submits when it wants to place an order.

    The simulator receives this, applies latency, and creates a tracked Order.
    Strategies never set arrival time or queue position - they only express
    intent: side, type, price, quantity.
    """
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Optional[Decimal] = None   # None for market orders


@dataclass
class Order:
    """
    A fully tracked order in the simulator.

    Created from an OrderRequest when the simulator receives it. The simulator
    updates status, filled_quantity, and queue_ahead as the order progresses.
    """
    order_id: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal            # original ordered quantity
    placed_time_ms: int          # when the strategy decided to place it
    arrival_time_ms: int         # placed_time_ms + latency (when it reaches exchange)
    price: Optional[Decimal] = None

    # mutable - updated by the simulator
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Decimal = field(default=Decimal("0"))
    queue_ahead: Optional[Decimal] = None   # volume ahead in FIFO queue at arrival

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity

    @property
    def is_active(self) -> bool:
        return self.status in (OrderStatus.ACTIVE, OrderStatus.PARTIAL)

    @property
    def is_done(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.CANCELLED)


@dataclass
class Fill:
    """
    One fill event - either a full fill or one partial fill of an order.

    A single order may produce multiple Fill records as it works through the
    queue. All fills for an order share the same order_id.

    fee is in quote currency (USDT). is_maker determines which rate applies:
    limit orders that rest get the maker rate, everything else gets taker.
    """
    fill_id: str
    order_id: str
    side: OrderSide
    price: Decimal
    quantity: Decimal       # this fill's quantity, not the total order quantity
    is_maker: bool
    timestamp_ms: int
    fee: Decimal            # in USDT

    @property
    def notional(self) -> Decimal:
        """Gross value of this fill in USDT."""
        return self.price * self.quantity


@dataclass
class OrderEvent:
    """
    One record per state transition in an order's lifecycle.

    The full sequence for a limit order looks like:
        placed -> arrived -> queued (queue_ahead=X) -> [partial_fill, ...] -> filled
    Or:
        placed -> arrived -> queued -> cancelled

    Used for audit logging and P&L decomposition.
    """
    order_id: str
    timestamp_ms: int
    event_type: str         # "placed", "arrived", "queued", "partial_fill",
                            # "filled", "cancelled"
    detail: Dict[str, Any] = field(default_factory=dict)
