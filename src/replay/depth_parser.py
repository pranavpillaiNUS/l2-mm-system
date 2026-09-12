"""
Parses hourly depth .jsonl.gz files into DepthEvent objects.

Two record types live in the same stream:
  - diff records: no "type" key in the outer object
  - snapshot records: "type": "snapshot" in the outer object

Check for the presence of the "type" key, not its value, to distinguish them.

Gap detection: consecutive diffs must satisfy current.U == prev.u + 1.
If not, sequence numbers are missing and the book is unrecoverable until
the next snapshot. Affected events are flagged with has_gap=True.
"""
import gzip
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Tuple


SNAPSHOT_TIME_POLICY = "post_response_proxy_depth_boundary_v1"


@dataclass
class DepthEvent:
    recv_time: datetime
    exchange_time_ms: int       # local request for resync, E for depth state
    event_type: str             # "resync", "snapshot", or "diff"
    first_update_id: Optional[int]  # U field (diffs only, None for snapshots)
    last_update_id: int         # u field (diffs) or lastUpdateId (snapshots)
    bids: List[Tuple[str, str]] # raw strings from JSON, Decimal conversion happens in orderbook
    asks: List[Tuple[str, str]]
    has_gap: bool = False       # True if U != prev_u + 1 (diffs only)
    source_exchange_time_ms: Optional[int] = None
    dispatch_strategy: bool = True


class DepthParser:
    """
    Yields DepthEvents from one or more hourly depth files in order.

    Files should be passed in chronological order. The parser maintains
    gap-detection state across files, so passing them out of order will
    produce false gap flags.

    Usage:
        files = sorted(Path("data/raw/btcusdt").glob("*.jsonl.gz"))
        parser = DepthParser(files)
        for event in parser.events():
            ...
    """

    def __init__(self, files: List[Path]):
        self.files = files

    def events(self) -> Iterator[DepthEvent]:
        prev_last_uid: Optional[int] = None
        snapshot_last_uid: Optional[int] = None
        pending_snapshot: Optional[DepthEvent] = None
        pending_cutoff: Optional[datetime] = None
        pending_legacy_tag = False
        buffered_diffs: List[DepthEvent] = []
        previous_source_time: Optional[int] = None

        for filepath in self.files:
            with gzip.open(filepath, "rt", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue

                    record = json.loads(line)
                    recv_time = datetime.fromisoformat(record["recv_time"])

                    if "type" in record:
                        # Snapshot - REST API response, no exchange timestamp.
                        # Legacy captures stamped request start, before the
                        # blocking response completed. Keep the source tag in
                        # recv_time, but do not place the snapshot on the replay
                        # clock until a sequence-valid retained diff is known
                        # to have been received after the REST response. New
                        # captures record response completion directly. For
                        # legacy captures, the first strictly later depth
                        # receipt is a post-response upper-bound proxy because
                        # the recorder blocked while fetching the snapshot.
                        data = record["data"]
                        request_time = datetime.fromisoformat(
                            record.get("request_time", record["recv_time"])
                        )
                        # Pause replay at request start. This prevents an
                        # intra-file reconnect from leaving old-book orders
                        # live while snapshot reconstruction is withheld.
                        yield DepthEvent(
                            recv_time=request_time,
                            exchange_time_ms=_epoch_ms_utc(request_time),
                            event_type="resync",
                            first_update_id=None,
                            last_update_id=data["lastUpdateId"],
                            bids=[],
                            asks=[],
                            has_gap=False,
                            dispatch_strategy=False,
                        )
                        pending_snapshot = DepthEvent(
                            recv_time=recv_time,
                            exchange_time_ms=_epoch_ms_utc(recv_time),
                            event_type="snapshot",
                            first_update_id=None,
                            last_update_id=data["lastUpdateId"],
                            bids=data["bids"],
                            asks=data["asks"],
                            has_gap=False,
                            dispatch_strategy=False,
                        )
                        # Binance snapshot recovery rule: drop all following
                        # diffs with u <= lastUpdateId, then require the first
                        # retained diff to bridge lastUpdateId + 1.
                        prev_last_uid = None
                        snapshot_last_uid = data["lastUpdateId"]
                        pending_cutoff = recv_time
                        pending_legacy_tag = "request_time" not in record
                        buffered_diffs = []

                    else:
                        # diff - WebSocket depthUpdate message
                        data = record["data"]
                        first_uid = data["U"]
                        last_uid = data["u"]
                        source_time = data["E"]
                        if (
                            previous_source_time is not None
                            and source_time < previous_source_time
                        ):
                            raise ValueError(
                                "depth source E is not monotone in file order"
                            )
                        previous_source_time = source_time

                        if snapshot_last_uid is not None:
                            if last_uid <= snapshot_last_uid:
                                # Stale buffered diff already covered by the
                                # snapshot. Applying it would roll the book
                                # backwards.
                                continue

                            if buffered_diffs:
                                has_gap = first_uid != prev_last_uid + 1
                            else:
                                has_gap = not (
                                    first_uid <= snapshot_last_uid + 1 <= last_uid
                                )
                            if pending_snapshot is None:
                                raise ValueError(
                                    "snapshot bridge state has no pending snapshot"
                                )
                            diff_event = DepthEvent(
                                recv_time=recv_time,
                                exchange_time_ms=source_time,
                                event_type="diff",
                                first_update_id=first_uid,
                                last_update_id=last_uid,
                                bids=data["b"],
                                asks=data["a"],
                                has_gap=has_gap,
                                source_exchange_time_ms=source_time,
                            )
                            if has_gap:
                                # No usable synchronized state exists. Discard
                                # the snapshot/buffer and expose only the gap
                                # marker so replay remains fail-closed.
                                pending_snapshot = None
                                pending_cutoff = None
                                pending_legacy_tag = False
                                buffered_diffs = []
                                snapshot_last_uid = None
                                prev_last_uid = last_uid
                                yield diff_event
                                continue

                            buffered_diffs.append(diff_event)
                            prev_last_uid = last_uid
                            if pending_cutoff is None:
                                raise ValueError(
                                    "snapshot bridge state has no receipt cutoff"
                                )
                            post_response = (
                                recv_time > pending_cutoff
                                if pending_legacy_tag
                                else recv_time >= pending_cutoff
                            )
                            if not post_response:
                                continue

                            # Keep recovered depth on E while gating release with
                            # post-response local receipt order. The snapshot and
                            # buffered reconstruction events share the selected
                            # diff's E. Only the final reconstructed state is
                            # exposed to the strategy.
                            boundary_time = source_time
                            yield replace(
                                pending_snapshot,
                                exchange_time_ms=boundary_time,
                            )
                            for index, buffered in enumerate(buffered_diffs):
                                yield replace(
                                    buffered,
                                    exchange_time_ms=boundary_time,
                                    dispatch_strategy=(
                                        index == len(buffered_diffs) - 1
                                    ),
                                )
                            pending_snapshot = None
                            pending_cutoff = None
                            pending_legacy_tag = False
                            buffered_diffs = []
                            snapshot_last_uid = None
                            continue
                        else:
                            has_gap = (
                                prev_last_uid is not None
                                and first_uid != prev_last_uid + 1
                            )

                        yield DepthEvent(
                            recv_time=recv_time,
                            exchange_time_ms=data["E"],
                            event_type="diff",
                            first_update_id=first_uid,
                            last_update_id=last_uid,
                            bids=data["b"],
                            asks=data["a"],
                            has_gap=has_gap,
                            source_exchange_time_ms=source_time,
                        )
                        prev_last_uid = last_uid


def _epoch_ms_utc(value: datetime) -> int:
    """Epoch milliseconds, treating naive recorder timestamps as UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)
