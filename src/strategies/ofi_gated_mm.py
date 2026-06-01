"""Microprice MM with one adverse-side quote suppressed by recent OFI."""

from __future__ import annotations

from collections import deque
from decimal import Decimal
from typing import Optional, Tuple

from src.replay.orderbook import Orderbook
from src.strategies.microprice_mm import MicropriceMM


class OFIGatedMM(MicropriceMM):
    """Gate the quote side most exposed to an OFI-predicted short-term move."""

    def __init__(
        self,
        half_spread: Decimal,
        order_qty: Decimal,
        max_position: Decimal,
        tick_size: Decimal = Decimal("0.01"),
        requote_interval_ms: int = 0,
        ofi_interval_ms: int = 1_000,
        ofi_threshold: Decimal = Decimal("0.25"),
    ):
        super().__init__(
            half_spread=half_spread,
            order_qty=order_qty,
            max_position=max_position,
            tick_size=tick_size,
            requote_interval_ms=requote_interval_ms,
        )
        if ofi_interval_ms <= 0:
            raise ValueError("ofi_interval_ms must be positive")
        if ofi_threshold <= Decimal("0"):
            raise ValueError("ofi_threshold must be positive")
        self.ofi_interval_ms = ofi_interval_ms
        self.ofi_threshold = ofi_threshold
        self.normalized_ofi = Decimal("0")
        self._previous_top: tuple[Decimal, Decimal, Decimal, Decimal] | None = None
        self._increments: deque[tuple[int, Decimal, Decimal]] = deque()

    def compute_quotes(
        self,
        book: Orderbook,
        timestamp_ms: int,
    ) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        self._update_ofi(book, timestamp_ms)
        bid, ask = super().compute_quotes(book, timestamp_ms)
        if self.normalized_ofi >= self.ofi_threshold:
            ask = None
        elif self.normalized_ofi <= -self.ofi_threshold:
            bid = None
        return bid, ask

    def _update_ofi(self, book: Orderbook, timestamp_ms: int) -> None:
        top = _top(book)
        if top is None:
            return
        if self._previous_top is not None:
            increment = _ofi_increment(self._previous_top, top)
            depth = top[1] + top[3]
            if depth > Decimal("0"):
                self._increments.append((timestamp_ms, increment, depth))
        self._previous_top = top

        cutoff = timestamp_ms - self.ofi_interval_ms
        while self._increments and self._increments[0][0] <= cutoff:
            self._increments.popleft()
        if not self._increments:
            self.normalized_ofi = Decimal("0")
            return
        raw = sum((row[1] for row in self._increments), Decimal("0"))
        average_depth = sum(
            (row[2] for row in self._increments), Decimal("0")
        ) / Decimal(len(self._increments))
        self.normalized_ofi = raw / average_depth


def _top(book: Orderbook) -> tuple[Decimal, Decimal, Decimal, Decimal] | None:
    if (
        book.best_bid is None
        or book.best_bid_qty is None
        or book.best_ask is None
        or book.best_ask_qty is None
    ):
        return None
    return book.best_bid, book.best_bid_qty, book.best_ask, book.best_ask_qty


def _ofi_increment(
    previous: tuple[Decimal, Decimal, Decimal, Decimal],
    current: tuple[Decimal, Decimal, Decimal, Decimal],
) -> Decimal:
    previous_bid, previous_bid_qty, previous_ask, previous_ask_qty = previous
    current_bid, current_bid_qty, current_ask, current_ask_qty = current
    bid_part = Decimal("0")
    if current_bid >= previous_bid:
        bid_part += current_bid_qty
    if current_bid <= previous_bid:
        bid_part -= previous_bid_qty
    ask_part = Decimal("0")
    if current_ask <= previous_ask:
        ask_part -= current_ask_qty
    if current_ask >= previous_ask:
        ask_part += previous_ask_qty
    return bid_part + ask_part
