"""
L2 orderbook backed by SortedDict.

Stores aggregated volume per price level — not individual orders. That's what
"L2" means. We see "83500.00: 2.3 BTC on the bid" but not who placed those
orders or how many separate orders make up that quantity.

Two sides:
  bids: sorted ascending by price, best bid = highest = last key
  asks: sorted ascending by price, best ask = lowest = first key

All prices and quantities use Decimal, never float. Binance sends them as
strings ("83500.10") because float can't represent them exactly.
"""
import hashlib
import json
from decimal import Decimal
from typing import List, Optional, Tuple

from sortedcontainers import SortedDict


class Orderbook:
    def __init__(self):
        # Both dicts are sorted ascending by Decimal price.
        # Best bid = _bids.keys()[-1], best ask = _asks.keys()[0].
        self._bids: SortedDict = SortedDict()
        self._asks: SortedDict = SortedDict()
        self.last_update_id: Optional[int] = None
        self.sequence: int = 0  # number of diffs applied since last snapshot

    def apply_snapshot(
        self,
        bids: List[Tuple[str, str]],
        asks: List[Tuple[str, str]],
        last_update_id: int,
    ) -> None:
        """
        Replace the entire book with a REST snapshot.

        Called at the start of each hourly file and after reconnects. The
        replay engine will drop all diffs where u <= last_update_id, then
        resume from the next diff.
        """
        self._bids.clear()
        self._asks.clear()

        for price_str, qty_str in bids:
            qty = Decimal(qty_str)
            if qty > 0:
                self._bids[Decimal(price_str)] = qty

        for price_str, qty_str in asks:
            qty = Decimal(qty_str)
            if qty > 0:
                self._asks[Decimal(price_str)] = qty

        self.last_update_id = last_update_id
        self.sequence = 0

    def apply_diff(
        self,
        bids: List[Tuple[str, str]],
        asks: List[Tuple[str, str]],
        last_update_id: int,
    ) -> None:
        """
        Apply a single depth diff update.

        qty == "0" means remove that level entirely. Any other qty is a full
        replacement of whatever was at that price before — Binance has already
        aggregated all individual orders at that level for us.
        """
        for price_str, qty_str in bids:
            price = Decimal(price_str)
            qty = Decimal(qty_str)
            if qty == 0:
                self._bids.pop(price, None)
            else:
                self._bids[price] = qty

        for price_str, qty_str in asks:
            price = Decimal(price_str)
            qty = Decimal(qty_str)
            if qty == 0:
                self._asks.pop(price, None)
            else:
                self._asks[price] = qty

        self.last_update_id = last_update_id
        self.sequence += 1

    # --- best prices ---

    @property
    def best_bid(self) -> Optional[Decimal]:
        if not self._bids:
            return None
        return self._bids.keys()[-1]

    @property
    def best_ask(self) -> Optional[Decimal]:
        if not self._asks:
            return None
        return self._asks.keys()[0]

    @property
    def best_bid_qty(self) -> Optional[Decimal]:
        bid = self.best_bid
        return self._bids[bid] if bid is not None else None

    @property
    def best_ask_qty(self) -> Optional[Decimal]:
        ask = self.best_ask
        return self._asks[ask] if ask is not None else None

    # --- derived prices ---

    @property
    def mid(self) -> Optional[Decimal]:
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2

    @property
    def spread(self) -> Optional[Decimal]:
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return ask - bid

    @property
    def microprice(self) -> Optional[Decimal]:
        """
        Volume-weighted mid. Skews toward whichever side has less resting size.

        If 3 BTC is on the bid and only 0.1 BTC on the ask, the ask is much
        more likely to get hit next, so price will probably tick up. Microprice
        captures this: more bid size → closer to ask price.

            microprice = (bid_qty × ask + ask_qty × bid) / (bid_qty + ask_qty)

        This is a better short-term price predictor than arithmetic mid, and
        it's what we'll use as the quoting reference in the MM strategies.
        """
        bid, ask = self.best_bid, self.best_ask
        bq, aq = self.best_bid_qty, self.best_ask_qty
        if any(x is None for x in [bid, ask, bq, aq]):
            return None
        total = bq + aq
        if total == 0:
            return None
        return (bq * ask + aq * bid) / total

    # --- book depth ---

    def bid_levels(self, n: int = 10) -> List[Tuple[Decimal, Decimal]]:
        """Top n bid levels, best first (descending price)."""
        keys = self._bids.keys()
        count = min(n, len(keys))
        return [(keys[-(i + 1)], self._bids[keys[-(i + 1)]]) for i in range(count)]

    def ask_levels(self, n: int = 10) -> List[Tuple[Decimal, Decimal]]:
        """Top n ask levels, best first (ascending price)."""
        keys = self._asks.keys()
        count = min(n, len(keys))
        return [(keys[i], self._asks[keys[i]]) for i in range(count)]

    # --- sanity checks ---

    def is_crossed(self) -> bool:
        """Best bid >= best ask — should never happen in clean data."""
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return False
        return bid >= ask

    # --- determinism ---

    def state_hash(self) -> str:
        """
        SHA-256 of the full book state. Two replays of identical input must
        produce the same hash at every checkpoint. If they don't, there's a
        bug in diff application or event ordering.
        """
        data = {
            "bids": [[str(p), str(q)] for p, q in self.bid_levels(len(self._bids))],
            "asks": [[str(p), str(q)] for p, q in self.ask_levels(len(self._asks))],
            "last_update_id": self.last_update_id,
        }
        canonical = json.dumps(data, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def __len__(self) -> int:
        return len(self._bids) + len(self._asks)

    def __repr__(self) -> str:
        return (
            f"Orderbook(bid={self.best_bid}, ask={self.best_ask}, "
            f"spread={self.spread}, levels={len(self._bids)}b/{len(self._asks)}a)"
        )