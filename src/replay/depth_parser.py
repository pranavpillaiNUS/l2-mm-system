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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional, Tuple


@dataclass
class DepthEvent:
    recv_time: datetime
    exchange_time_ms: int       # E for diffs; recv_time as ms for snapshots
    event_type: str             # "snapshot" or "diff"
    first_update_id: Optional[int]  # U field (diffs only, None for snapshots)
    last_update_id: int         # u field (diffs) or lastUpdateId (snapshots)
    bids: List[Tuple[str, str]] # raw strings from JSON, Decimal conversion happens in orderbook
    asks: List[Tuple[str, str]]
    has_gap: bool = False       # True if U != prev_u + 1 (diffs only)


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

        for filepath in self.files:
            with gzip.open(filepath, "rt", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue

                    record = json.loads(line)
                    recv_time = datetime.fromisoformat(record["recv_time"])

                    if "type" in record:
                        # snapshot — REST API response, no exchange timestamp
                        data = record["data"]
                        yield DepthEvent(
                            recv_time=recv_time,
                            exchange_time_ms=int(recv_time.timestamp() * 1000),
                            event_type="snapshot",
                            first_update_id=None,
                            last_update_id=data["lastUpdateId"],
                            bids=data["bids"],
                            asks=data["asks"],
                            has_gap=False,
                        )
                        # reset so the first diff after a snapshot isn't
                        # incorrectly flagged as a gap
                        prev_last_uid = None

                    else:
                        # diff — WebSocket depthUpdate message
                        data = record["data"]
                        first_uid = data["U"]
                        last_uid = data["u"]

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
                        )
                        prev_last_uid = last_uid
