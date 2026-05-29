"""
Parses hourly aggTrade .jsonl.gz files into TradeEvent objects.

The m field is counterintuitive: m=True means the buyer was the market maker
(i.e. the buyer's limit order was resting), which means the seller was the
aggressor - a market sell. m=False means the buyer was the aggressor - a
market buy.

Gap detection: agg_trade_id (field "a") is sequential. If
current.agg_trade_id != prev.agg_trade_id + 1, some trades were missed.

Use field T (trade timestamp) for event ordering, not E or recv_time.
T is the Binance server time when the trade matched. E is when the WebSocket
message was sent - slightly later.
"""
import gzip
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator, List, Optional


@dataclass
class TradeEvent:
    recv_time: datetime
    exchange_time_ms: int   # T field - use this for ordering with depth events
    agg_trade_id: int       # a field - sequential, used for gap detection
    price: Decimal
    quantity: Decimal
    is_buyer_maker: bool    # m field. True = seller was aggressor (market sell)
                            #          False = buyer was aggressor (market buy)
    has_gap: bool = False


class TradeParser:
    """
    Yields TradeEvents from one or more hourly trade files in order.

    Files should be passed in chronological order. Gap-detection state is
    maintained across files.

    Usage:
        files = sorted(Path("data/raw/btcusdt_trades").glob("*.jsonl.gz"))
        parser = TradeParser(files)
        for event in parser.events():
            ...
    """

    def __init__(self, files: List[Path]):
        self.files = files

    def events(self) -> Iterator[TradeEvent]:
        prev_agg_id: Optional[int] = None

        for filepath in self.files:
            with gzip.open(filepath, "rt", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue

                    record = json.loads(line)
                    data = record["data"]
                    agg_id = data["a"]

                    has_gap = (
                        prev_agg_id is not None
                        and agg_id != prev_agg_id + 1
                    )

                    yield TradeEvent(
                        recv_time=datetime.fromisoformat(record["recv_time"]),
                        exchange_time_ms=data["T"],
                        agg_trade_id=agg_id,
                        price=Decimal(data["p"]),
                        quantity=Decimal(data["q"]),
                        is_buyer_maker=data["m"],
                        has_gap=has_gap,
                    )
                    prev_agg_id = agg_id
