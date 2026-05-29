"""
Merges a depth event stream and a trade event stream into a single
time-sorted sequence.

Both streams must already be sorted by exchange timestamp (which they are,
since parsers yield in file order). This is a standard 2-way sorted merge
using heapq.merge - O(log 2) = O(1) per event.

Tiebreaker: when a depth event and a trade event share the same millisecond
timestamp, depth comes first. The book update reflects the state after the
matching engine processed that order - if you process the trade first, your
execution simulator sees stale liquidity.
"""
import heapq
from typing import Iterable, Iterator, Tuple, Union

from src.replay.depth_parser import DepthEvent
from src.replay.trade_parser import TradeEvent

Event = Union[DepthEvent, TradeEvent]

# tiebreaker priority: lower number = comes first on equal timestamp
_DEPTH_PRIORITY = 0
_TRADE_PRIORITY = 1


def _tagged_depth(it: Iterable[DepthEvent]) -> Iterator[Tuple]:
    for event in it:
        yield (event.exchange_time_ms, _DEPTH_PRIORITY, event)


def _tagged_trade(it: Iterable[TradeEvent]) -> Iterator[Tuple]:
    for event in it:
        yield (event.exchange_time_ms, _TRADE_PRIORITY, event)


class EventMerger:
    """
    Merges depth and trade event streams by exchange timestamp.

    Usage:
        depth_parser = DepthParser(depth_files)
        trade_parser = TradeParser(trade_files)
        merger = EventMerger(depth_parser.events(), trade_parser.events())
        for event in merger.events():
            if isinstance(event, DepthEvent):
                ...
            elif isinstance(event, TradeEvent):
                ...
    """

    def __init__(
        self,
        depth_events: Iterable[DepthEvent],
        trade_events: Iterable[TradeEvent],
    ):
        self._depth = depth_events
        self._trade = trade_events

    def events(self) -> Iterator[Event]:
        merged = heapq.merge(
            _tagged_depth(self._depth),
            _tagged_trade(self._trade),
        )
        for _, _, event in merged:
            yield event
