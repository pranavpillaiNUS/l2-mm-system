"""
Symmetric market-making: quote a fixed spread around the arithmetic mid.

This is the baseline strategy. It makes no attempt to predict short-term
price direction — it just posts a bid at mid - half_spread and an ask at
mid + half_spread. The spread must be wide enough to cover adverse
selection and fees; if it isn't, the strategy bleeds money.

Exists primarily as a control. The microprice and inventory-skew strategies
should beat it on adverse selection and inventory risk respectively.
"""
from decimal import Decimal
from typing import Optional, Tuple

from src.replay.orderbook import Orderbook
from src.strategies.base_mm import BaseMMStrategy


class SymmetricMM(BaseMMStrategy):
    """
    Parameters:
        half_spread: distance from mid to each quote (in price units).
                     e.g. half_spread=0.50 on BTCUSDT quotes $0.50
                     below mid (bid) and $0.50 above mid (ask).
        order_qty:   size of each quote in base currency.
        max_position: absolute position limit.
        tick_size:   price rounding increment.
    """

    def __init__(
        self,
        half_spread: Decimal,
        order_qty: Decimal,
        max_position: Decimal,
        tick_size: Decimal = Decimal("0.01"),
        requote_interval_ms: int = 0,
    ):
        super().__init__(order_qty=order_qty, max_position=max_position,
                         tick_size=tick_size,
                         requote_interval_ms=requote_interval_ms)
        self.half_spread = half_spread

    def compute_quotes(
        self, book: Orderbook, timestamp_ms: int,
    ) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        mid = book.mid
        if mid is None:
            return None, None

        bid = mid - self.half_spread
        ask = mid + self.half_spread
        return bid, ask
