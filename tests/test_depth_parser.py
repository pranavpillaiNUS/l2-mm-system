"""
Tests for DepthParser.

Run with: python tests/test_depth_parser.py
"""
import gzip
import json
import tempfile
from datetime import datetime
from pathlib import Path

from src.replay.depth_parser import DepthParser, DepthEvent


# --- helpers ---

def make_diff(U: int, u: int, E: int = None) -> dict:
    """Build a minimal diff record."""
    if E is None:
        E = U * 100  # arbitrary exchange timestamp
    return {
        "recv_time": "2026-04-21T10:00:00.000000",
        "data": {
            "e": "depthUpdate",
            "E": E,
            "s": "BTCUSDT",
            "U": U,
            "u": u,
            "b": [["83500.00", "1.0"]],
            "a": [["83501.00", "0.5"]],
        },
    }


def make_snapshot(last_update_id: int) -> dict:
    """Build a minimal snapshot record."""
    return {
        "recv_time": "2026-04-21T10:00:00.000000",
        "type": "snapshot",
        "data": {
            "lastUpdateId": last_update_id,
            "bids": [["83500.00", "1.0"]],
            "asks": [["83501.00", "0.5"]],
        },
    }


def write_gz(records: list, path: Path) -> None:
    """Write records as newline-delimited JSON into a .jsonl.gz file."""
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


# --- tests ---

def test_parse_single_diff():
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "depth.jsonl.gz"
        write_gz([make_diff(U=100, u=105, E=9999)], p)

        events = list(DepthParser([p]).events())
        assert len(events) == 1

        e = events[0]
        assert e.event_type == "diff"
        assert e.first_update_id == 100
        assert e.last_update_id == 105
        assert e.exchange_time_ms == 9999
        assert e.has_gap is False
        assert isinstance(e.recv_time, datetime)
        assert e.bids == [["83500.00", "1.0"]]
        assert e.asks == [["83501.00", "0.5"]]
    print("PASS: single diff parsed correctly")


def test_parse_snapshot():
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "depth.jsonl.gz"
        write_gz([make_snapshot(last_update_id=999)], p)

        events = list(DepthParser([p]).events())
        assert len(events) == 1

        e = events[0]
        assert e.event_type == "snapshot"
        assert e.first_update_id is None
        assert e.last_update_id == 999
        assert e.has_gap is False
        assert e.bids == [["83500.00", "1.0"]]
        assert e.asks == [["83501.00", "0.5"]]
    print("PASS: snapshot parsed correctly")


def test_no_gap_in_continuous_sequence():
    # u=105, then next U=106 — continuous, no gap
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "depth.jsonl.gz"
        write_gz([
            make_diff(U=100, u=105),
            make_diff(U=106, u=110),
            make_diff(U=111, u=115),
        ], p)

        events = list(DepthParser([p]).events())
        assert all(not e.has_gap for e in events)
    print("PASS: continuous sequence has no gaps")


def test_gap_detected_between_diffs():
    # u=105, then next U=108 — gap (106 and 107 are missing)
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "depth.jsonl.gz"
        write_gz([
            make_diff(U=100, u=105),
            make_diff(U=108, u=112),  # gap: expected U=106
        ], p)

        events = list(DepthParser([p]).events())
        assert events[0].has_gap is False
        assert events[1].has_gap is True
    print("PASS: gap detected when U != prev_u + 1")


def test_first_diff_never_flagged_as_gap():
    # first diff in a file has no predecessor — can't be a gap
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "depth.jsonl.gz"
        write_gz([make_diff(U=500, u=510)], p)

        events = list(DepthParser([p]).events())
        assert events[0].has_gap is False
    print("PASS: first diff is never flagged as a gap")


def test_snapshot_resets_gap_tracking():
    # diff → gap → snapshot → diff: the diff after the snapshot should NOT be flagged
    # even though its U doesn't follow from the pre-snapshot diff
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "depth.jsonl.gz"
        write_gz([
            make_diff(U=100, u=105),
            make_snapshot(last_update_id=200),   # reconnect, resync
            make_diff(U=201, u=210),              # continues from snapshot — not a gap
        ], p)

        events = list(DepthParser([p]).events())
        assert events[0].has_gap is False   # first diff
        assert events[1].has_gap is False   # snapshot
        assert events[2].has_gap is False   # diff after snapshot — reset, not flagged
    print("PASS: snapshot resets gap tracker, first diff after snapshot not flagged")


def test_gap_after_snapshot_detected():
    # snapshot → diff → gap → diff: gap between diffs is still detected
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "depth.jsonl.gz"
        write_gz([
            make_snapshot(last_update_id=200),
            make_diff(U=201, u=210),
            make_diff(U=215, u=220),  # gap: expected U=211
        ], p)

        events = list(DepthParser([p]).events())
        assert events[1].has_gap is False
        assert events[2].has_gap is True
    print("PASS: gap between diffs after snapshot still detected")


def test_multiple_files_gap_tracked_across():
    # gap across the file boundary (last diff in file1 → first diff in file2)
    with tempfile.TemporaryDirectory() as tmpdir:
        p1 = Path(tmpdir) / "depth_0100.jsonl.gz"
        p2 = Path(tmpdir) / "depth_0200.jsonl.gz"
        write_gz([make_diff(U=100, u=105)], p1)
        write_gz([make_diff(U=110, u=115)], p2)  # gap: expected U=106

        events = list(DepthParser([p1, p2]).events())
        assert events[0].has_gap is False
        assert events[1].has_gap is True
    print("PASS: gap detected across file boundary")


def test_multiple_files_continuous_no_gap():
    with tempfile.TemporaryDirectory() as tmpdir:
        p1 = Path(tmpdir) / "depth_0100.jsonl.gz"
        p2 = Path(tmpdir) / "depth_0200.jsonl.gz"
        write_gz([make_diff(U=100, u=105)], p1)
        write_gz([make_diff(U=106, u=110)], p2)  # continuous

        events = list(DepthParser([p1, p2]).events())
        assert all(not e.has_gap for e in events)
    print("PASS: continuous sequence across files has no gap")


def test_snapshot_at_file_start_then_diffs():
    # the normal post-Apr-12 pattern: snapshot at line 0, then diffs
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "depth.jsonl.gz"
        write_gz([
            make_snapshot(last_update_id=1000),
            make_diff(U=1001, u=1005),
            make_diff(U=1006, u=1010),
        ], p)

        events = list(DepthParser([p]).events())
        assert len(events) == 3
        assert events[0].event_type == "snapshot"
        assert events[1].event_type == "diff"
        assert events[1].has_gap is False
        assert events[2].has_gap is False
    print("PASS: snapshot-then-diffs pattern (post-Apr-12 hourly file)")


def test_events_on_real_file():
    # smoke test against an actual recorded file — just check it doesn't crash
    # and yields reasonable-looking events
    real_file = Path("data/raw/btcusdt/btcusdt_depth_20260421_1900.jsonl.gz")
    if not real_file.exists():
        print("SKIP: test_events_on_real_file (no real data file found)")
        return

    events = list(DepthParser([real_file]).events())
    assert len(events) > 0
    # first event should be a snapshot (post-Apr-12 file)
    assert events[0].event_type == "snapshot"
    # all events have valid types
    assert all(e.event_type in ("snapshot", "diff") for e in events)
    # count gaps
    gaps = sum(1 for e in events if e.has_gap)
    print(f"PASS: real file — {len(events)} events, {gaps} gap(s)")


if __name__ == "__main__":
    test_parse_single_diff()
    test_parse_snapshot()
    test_no_gap_in_continuous_sequence()
    test_gap_detected_between_diffs()
    test_first_diff_never_flagged_as_gap()
    test_snapshot_resets_gap_tracking()
    test_gap_after_snapshot_detected()
    test_multiple_files_gap_tracked_across()
    test_multiple_files_continuous_no_gap()
    test_snapshot_at_file_start_then_diffs()
    test_events_on_real_file()
    print("\nAll tests passed.")
