"""Hourly raw-data integrity checks for L2 research panels."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable


@dataclass
class HourIntegrity:
    start: datetime
    cutoff_eligible: bool
    depth_path: str
    trade_path: str
    depth_exists: bool
    trade_exists: bool
    depth_sha256: str = ""
    trade_sha256: str = ""
    depth_rows: int = 0
    depth_snapshots: int = 0
    depth_diffs: int = 0
    depth_snapshot_bridge_gaps: int = 0
    depth_sequence_gaps: int = 0
    depth_cross_hour_gaps: int = 0
    depth_first_update_id: int | None = None
    depth_last_update_id: int | None = None
    trade_rows: int = 0
    trade_sequence_gaps: int = 0
    trade_cross_hour_gaps: int = 0
    trade_first_agg_id: int | None = None
    trade_last_agg_id: int | None = None
    depth_error: str = ""
    trade_error: str = ""
    valid: bool = False
    rejection_reasons: list[str] | None = None

    def to_dict(self) -> dict:
        row = asdict(self)
        row["start"] = self.start.isoformat()
        row["rejection_reasons"] = list(self.rejection_reasons or [])
        return row


def build_integrity_manifest(
    *,
    data_root: Path,
    symbol: str,
    cutoff: datetime,
    start: datetime | None = None,
) -> list[HourIntegrity]:
    """Inspect each hour before the cutoff and return deterministic rows."""
    symbol = symbol.lower()
    starts = _manifest_hours(data_root, symbol, cutoff, start=start)
    rows: list[HourIntegrity] = []
    previous_depth: HourIntegrity | None = None
    previous_trade: HourIntegrity | None = None

    for hour_start in starts:
        depth_path, trade_path = hourly_paths(data_root, symbol, hour_start)
        row = HourIntegrity(
            start=hour_start,
            cutoff_eligible=hour_start < cutoff,
            depth_path=str(depth_path),
            trade_path=str(trade_path),
            depth_exists=depth_path.exists(),
            trade_exists=trade_path.exists(),
        )
        if row.depth_exists:
            _inspect_depth(depth_path, row)
        if row.trade_exists:
            _inspect_trades(trade_path, row)

        if (
            previous_depth is not None
            and previous_depth.start + timedelta(hours=1) == row.start
            and not row.depth_error
            and row.depth_snapshots == 0
            and previous_depth.depth_last_update_id is not None
            and row.depth_first_update_id is not None
            and row.depth_first_update_id != previous_depth.depth_last_update_id + 1
        ):
            row.depth_cross_hour_gaps += 1

        if (
            previous_trade is not None
            and previous_trade.start + timedelta(hours=1) == row.start
            and not previous_trade.trade_error
            and not row.trade_error
            and previous_trade.trade_last_agg_id is not None
            and row.trade_first_agg_id is not None
            and row.trade_first_agg_id != previous_trade.trade_last_agg_id + 1
        ):
            row.trade_cross_hour_gaps += 1

        row.rejection_reasons = _rejection_reasons(row)
        row.valid = not row.rejection_reasons
        rows.append(row)
        previous_depth = row
        previous_trade = row

    return rows


def hourly_paths(data_root: Path, symbol: str, start: datetime) -> tuple[Path, Path]:
    stamp = start.strftime("%Y%m%d_%H00")
    return (
        data_root / "raw" / symbol / f"{symbol}_depth_{stamp}.jsonl.gz",
        data_root / "raw" / f"{symbol}_trades" / f"{symbol}_trades_{stamp}.jsonl.gz",
    )


def manifest_sha256(rows: Iterable[HourIntegrity | dict]) -> str:
    payload = []
    for row in rows:
        payload.append(row.to_dict() if isinstance(row, HourIntegrity) else row)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _manifest_hours(
    data_root: Path,
    symbol: str,
    cutoff: datetime,
    *,
    start: datetime | None,
) -> list[datetime]:
    if start is None:
        trade_dir = data_root / "raw" / f"{symbol}_trades"
        available = [
            _timestamp_from_name(path.name, f"{symbol}_trades_")
            for path in trade_dir.glob(f"{symbol}_trades_*.jsonl.gz")
        ]
        available = [value for value in available if value is not None and value < cutoff]
        if not available:
            return []
        start = min(available)

    out = []
    current = start
    while current < cutoff:
        out.append(current)
        current += timedelta(hours=1)
    return out


def _timestamp_from_name(name: str, prefix: str) -> datetime | None:
    if not name.startswith(prefix) or not name.endswith(".jsonl.gz"):
        return None
    stamp = name.removeprefix(prefix).removesuffix(".jsonl.gz")
    try:
        return datetime.strptime(stamp, "%Y%m%d_%H%M")
    except ValueError:
        return None


def _inspect_depth(path: Path, row: HourIntegrity) -> None:
    row.depth_sha256 = _sha256(path)
    previous_last_uid: int | None = None
    snapshot_last_uid: int | None = None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for raw_line in f:
                record = json.loads(raw_line)
                row.depth_rows += 1
                if "type" in record:
                    row.depth_snapshots += 1
                    snapshot_last_uid = int(record["data"]["lastUpdateId"])
                    previous_last_uid = None
                    continue

                data = record["data"]
                first_uid = int(data["U"])
                last_uid = int(data["u"])
                row.depth_diffs += 1
                if row.depth_first_update_id is None:
                    row.depth_first_update_id = first_uid
                row.depth_last_update_id = last_uid

                if snapshot_last_uid is not None:
                    if last_uid <= snapshot_last_uid:
                        continue
                    if not (first_uid <= snapshot_last_uid + 1 <= last_uid):
                        row.depth_snapshot_bridge_gaps += 1
                    snapshot_last_uid = None
                elif (
                    previous_last_uid is not None
                    and first_uid != previous_last_uid + 1
                ):
                    row.depth_sequence_gaps += 1
                previous_last_uid = last_uid
    except Exception as exc:
        row.depth_error = f"{type(exc).__name__}: {exc}"


def _inspect_trades(path: Path, row: HourIntegrity) -> None:
    row.trade_sha256 = _sha256(path)
    previous_agg_id: int | None = None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for raw_line in f:
                record = json.loads(raw_line)
                agg_id = int(record["data"]["a"])
                row.trade_rows += 1
                if row.trade_first_agg_id is None:
                    row.trade_first_agg_id = agg_id
                row.trade_last_agg_id = agg_id
                if previous_agg_id is not None and agg_id != previous_agg_id + 1:
                    row.trade_sequence_gaps += 1
                previous_agg_id = agg_id
    except Exception as exc:
        row.trade_error = f"{type(exc).__name__}: {exc}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rejection_reasons(row: HourIntegrity) -> list[str]:
    reasons = []
    if not row.cutoff_eligible:
        reasons.append("at_or_after_cutoff")
    if not row.depth_exists:
        reasons.append("missing_depth_file")
    if not row.trade_exists:
        reasons.append("missing_trade_file")
    if row.depth_error:
        reasons.append("depth_decode_error")
    if row.trade_error:
        reasons.append("trade_decode_error")
    if row.depth_exists and not row.depth_error and row.depth_snapshots == 0:
        reasons.append("no_depth_snapshot")
    if row.depth_exists and not row.depth_error and row.depth_diffs == 0:
        reasons.append("no_depth_diffs")
    if row.trade_exists and not row.trade_error and row.trade_rows == 0:
        reasons.append("no_trade_rows")
    if row.depth_snapshot_bridge_gaps:
        reasons.append("depth_snapshot_bridge_gap")
    if row.depth_sequence_gaps:
        reasons.append("depth_sequence_gap")
    if row.depth_cross_hour_gaps:
        reasons.append("depth_cross_hour_gap")
    if row.trade_sequence_gaps:
        reasons.append("trade_sequence_gap")
    if row.trade_cross_hour_gaps:
        reasons.append("trade_cross_hour_gap")
    return reasons
