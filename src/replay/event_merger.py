"""
Merges a depth event stream and a trade event stream into a single
time-sorted sequence.

Both streams must already be sorted by their modeled event-time field. Ordinary
depth diffs use Binance E and trades use T. Snapshot recovery emits a fail-closed
barrier at the recorded local request time, then places the recovered snapshot
and its buffered reconstruction at the selected sequence-valid boundary diff's
E because the snapshot has no exchange event timestamp. The monotonicity guards
below reject out-of-order modeled timestamps before heapq.merge can silently
produce an invalid global sequence. The merge is O(log 2) = O(1) per event.

Tiebreaker: when a depth event and a trade event share the same millisecond,
depth comes first. The feed does not expose enough information to reconstruct
true within-millisecond matching-engine order, so this is an explicit model
policy. ReplayEngine additionally batches all market data at the timestamp
before equal-time simulated private arrivals.
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
    previous_time = None
    for event in it:
        if previous_time is not None and event.exchange_time_ms < previous_time:
            raise ValueError("depth stream is not monotone in modeled event time")
        previous_time = event.exchange_time_ms
        yield (event.exchange_time_ms, _DEPTH_PRIORITY, event)


def _tagged_trade(it: Iterable[TradeEvent]) -> Iterator[Tuple]:
    previous_time = None
    for event in it:
        if previous_time is not None and event.exchange_time_ms < previous_time:
            raise ValueError("trade stream is not monotone in modeled event time")
        previous_time = event.exchange_time_ms
        yield (event.exchange_time_ms, _TRADE_PRIORITY, event)


class EventMerger:
    """
    Merges depth and trade streams by their modeled event timestamp.

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
