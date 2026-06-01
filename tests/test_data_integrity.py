"""Tests for hourly L2 raw-data integrity manifests."""

import gzip
import json
from datetime import datetime, timedelta
from pathlib import Path

from src.analysis.data_integrity import build_integrity_manifest, hourly_paths


START = datetime.fromisoformat("2026-04-12T07:00")


def _write_gz(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for record in records:
            if isinstance(record, str):
                f.write(record + "\n")
            else:
                f.write(json.dumps(record) + "\n")


def _snapshot(uid: int) -> dict:
    return {
        "recv_time": "2026-04-12T07:00:00",
        "type": "snapshot",
        "data": {"lastUpdateId": uid, "bids": [], "asks": []},
    }


def _diff(first: int, last: int) -> dict:
    return {
        "recv_time": "2026-04-12T07:00:00",
        "data": {"E": 1, "U": first, "u": last, "b": [], "a": []},
    }


def _trade(agg_id: int) -> dict:
    return {
        "recv_time": "2026-04-12T07:00:00",
        "data": {"T": 1, "a": agg_id, "p": "100", "q": "1", "m": True},
    }


def _write_valid_hour(data_root: Path, start: datetime, uid: int, agg_ids) -> None:
    depth, trades = hourly_paths(data_root, "btcusdt", start)
    _write_gz(depth, [_snapshot(uid), _diff(uid + 1, uid + 1)])
    _write_gz(trades, [_trade(agg_id) for agg_id in agg_ids])


def test_manifest_accepts_valid_hours_and_applies_cutoff(tmp_path: Path):
    _write_valid_hour(tmp_path, START, 100, [1, 2])
    _write_valid_hour(tmp_path, START + timedelta(hours=1), 200, [3, 4])

    rows = build_integrity_manifest(
        data_root=tmp_path,
        symbol="btcusdt",
        start=START,
        cutoff=START + timedelta(hours=2),
    )

    assert len(rows) == 2
    assert all(row.valid for row in rows)
    assert rows[0].depth_sha256
    assert rows[0].trade_sha256


def test_manifest_rejects_corrupt_gzip_and_malformed_json(tmp_path: Path):
    depth, trades = hourly_paths(tmp_path, "btcusdt", START)
    depth.parent.mkdir(parents=True, exist_ok=True)
    depth.write_bytes(b"not-a-gzip")
    _write_gz(trades, ["{not-json"])

    row = build_integrity_manifest(
        data_root=tmp_path,
        symbol="btcusdt",
        start=START,
        cutoff=START + timedelta(hours=1),
    )[0]

    assert not row.valid
    assert "depth_decode_error" in row.rejection_reasons
    assert "trade_decode_error" in row.rejection_reasons


def test_manifest_rejects_trade_gap_within_and_across_hours(tmp_path: Path):
    _write_valid_hour(tmp_path, START, 100, [1, 3])
    _write_valid_hour(tmp_path, START + timedelta(hours=1), 200, [6, 7])

    first, second = build_integrity_manifest(
        data_root=tmp_path,
        symbol="btcusdt",
        start=START,
        cutoff=START + timedelta(hours=2),
    )

    assert first.trade_sequence_gaps == 1
    assert "trade_sequence_gap" in first.rejection_reasons
    assert second.trade_cross_hour_gaps == 1
    assert "trade_cross_hour_gap" in second.rejection_reasons


def test_manifest_rejects_snapshot_bridge_gap(tmp_path: Path):
    depth, trades = hourly_paths(tmp_path, "btcusdt", START)
    _write_gz(depth, [_snapshot(100), _diff(103, 103)])
    _write_gz(trades, [_trade(1)])

    row = build_integrity_manifest(
        data_root=tmp_path,
        symbol="btcusdt",
        start=START,
        cutoff=START + timedelta(hours=1),
    )[0]

    assert row.depth_snapshot_bridge_gaps == 1
    assert "depth_snapshot_bridge_gap" in row.rejection_reasons
