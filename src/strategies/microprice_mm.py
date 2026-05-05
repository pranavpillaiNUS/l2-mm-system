"""
Microprice market-making: quote a fixed spread around book microprice.

This strategy is the first adverse-selection-aware variant of SymmetricMM.
When one side of the top of book is thin relative to the other, microprice
shifts toward the side more likely to trade next. The quote mechanics remain
identical to the baseline; only the reference price changes.
"""
from decimal import Decimal
from typing import Optional, Tuple

from src.replay.orderbook import Orderbook
from src.strategies.base_mm import BaseMMStrategy


class MicropriceMM(BaseMMStrategy):
    """
    Parameters:
        half_spread: distance from microprice to each quote.
        order_qty: size of each quote in base currency.
        max_position: absolute position limit.
        tick_size: price rounding increment.
    """

    def __init__(
        self,
        half_spread: Decimal,
        order_qty: Decimal,
        max_position: Decimal,
        tick_size: Decimal = Decimal("0.01"),
    ):
        super().__init__(order_qty=order_qty, max_position=max_position,
                         tick_size=tick_size)
        self.half_spread = half_spread

    def compute_quotes(
        self, book: Orderbook, timestamp_ms: int,
    ) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        reference = book.microprice
        if reference is None:
            return None, None

        bid = reference - self.half_spread
        ask = reference + self.half_spread
        return bid, ask
