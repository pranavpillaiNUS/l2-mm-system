"""
Tests for TradeParser.

Run with: python tests/test_trade_parser.py
"""
import gzip
import json
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.replay.trade_parser import TradeParser, TradeEvent


# --- helpers ---

def make_trade(agg_id: int, price: str = "83500.50", qty: str = "0.123",
               is_buyer_maker: bool = False, T: int = None) -> dict:
    if T is None:
        T = agg_id * 1000
    return {
        "recv_time": "2026-04-21T19:00:00.094715",
        "data": {
            "e": "aggTrade",
            "E": T + 5,
            "s": "BTCUSDT",
            "a": agg_id,
            "p": price,
            "q": qty,
            "f": agg_id * 2,
            "l": agg_id * 2 + 1,
            "T": T,
            "m": is_buyer_maker,
            "M": True,
        },
    }


def write_gz(records: list, path: Path) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


# --- tests ---

def test_parse_single_trade():
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "trades.jsonl.gz"
        write_gz([make_trade(agg_id=1000, price="83500.50", qty="0.123",
                             is_buyer_maker=False, T=9999000)], p)

        events = list(TradeParser([p]).events())
        assert len(events) == 1

        e = events[0]
        assert e.agg_trade_id == 1000
        assert e.price == Decimal("83500.50")
        assert e.quantity == Decimal("0.123")
        assert e.is_buyer_maker is False
        assert e.exchange_time_ms == 9999000
        assert e.has_gap is False
        assert isinstance(e.recv_time, datetime)
    print("PASS: single trade parsed correctly")


def test_price_and_qty_are_decimal():
    # make sure we're not silently using float
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "trades.jsonl.gz"
        write_gz([make_trade(agg_id=1, price="83500.10", qty="0.00123")], p)

        e = list(TradeParser([p]).events())[0]
        assert isinstance(e.price, Decimal)
        assert isinstance(e.quantity, Decimal)
        assert e.price == Decimal("83500.10")
        assert e.quantity == Decimal("0.00123")
    print("PASS: price and quantity parsed as Decimal, not float")


def test_is_buyer_maker_true_means_market_sell():
    # m=True -> buyer was market maker -> seller was aggressor -> market sell
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "trades.jsonl.gz"
        write_gz([make_trade(agg_id=1, is_buyer_maker=True)], p)

        e = list(TradeParser([p]).events())[0]
        assert e.is_buyer_maker is True
    print("PASS: is_buyer_maker=True preserved (market sell - seller was aggressor)")


def test_is_buyer_maker_false_means_market_buy():
    # m=False -> buyer was aggressor -> market buy
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "trades.jsonl.gz"
        write_gz([make_trade(agg_id=1, is_buyer_maker=False)], p)

        e = list(TradeParser([p]).events())[0]
        assert e.is_buyer_maker is False
    print("PASS: is_buyer_maker=False preserved (market buy - buyer was aggressor)")


def test_no_gap_in_continuous_sequence():
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "trades.jsonl.gz"
        write_gz([make_trade(i) for i in range(100, 105)], p)

        events = list(TradeParser([p]).events())
        assert all(not e.has_gap for e in events)
    print("PASS: continuous agg_trade_id sequence has no gaps")


def test_gap_detected():
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "trades.jsonl.gz"
        write_gz([
            make_trade(agg_id=100),
            make_trade(agg_id=103),  # gap: 101 and 102 missing
        ], p)

        events = list(TradeParser([p]).events())
        assert events[0].has_gap is False
        assert events[1].has_gap is True
    print("PASS: gap detected when agg_trade_id skips")


def test_first_trade_never_flagged_as_gap():
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "trades.jsonl.gz"
        write_gz([make_trade(agg_id=9999)], p)

        events = list(TradeParser([p]).events())
        assert events[0].has_gap is False
    print("PASS: first trade is never flagged as a gap")


def test_gap_tracked_across_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        p1 = Path(tmpdir) / "trades_0100.jsonl.gz"
        p2 = Path(tmpdir) / "trades_0200.jsonl.gz"
        write_gz([make_trade(agg_id=100)], p1)
        write_gz([make_trade(agg_id=105)], p2)  # gap: 101-104 missing

        events = list(TradeParser([p1, p2]).events())
        assert events[0].has_gap is False
        assert events[1].has_gap is True
    print("PASS: gap detected across file boundary")


def test_continuous_across_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        p1 = Path(tmpdir) / "trades_0100.jsonl.gz"
        p2 = Path(tmpdir) / "trades_0200.jsonl.gz"
        write_gz([make_trade(agg_id=100)], p1)
        write_gz([make_trade(agg_id=101)], p2)

        events = list(TradeParser([p1, p2]).events())
        assert all(not e.has_gap for e in events)
    print("PASS: continuous sequence across files has no gap")


def test_exchange_time_is_T_not_E():
    # exchange_time_ms must be T (trade match time), not E (message send time)
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "trades.jsonl.gz"
        write_gz([make_trade(agg_id=1, T=1776798000040)], p)

        e = list(TradeParser([p]).events())[0]
        assert e.exchange_time_ms == 1776798000040
        # E is T+5 in our helper - make sure we didn't accidentally use E
        assert e.exchange_time_ms != 1776798000045
    print("PASS: exchange_time_ms is T (trade timestamp), not E (event timestamp)")


def test_events_on_real_file():
    real_file = Path("data/raw/btcusdt_trades/btcusdt_trades_20260421_1900.jsonl.gz")
    if not real_file.exists():
        print("SKIP: test_events_on_real_file (no real data file found)")
        return

    events = list(TradeParser([real_file]).events())
    assert len(events) > 0
    assert all(isinstance(e.price, Decimal) for e in events)
    assert all(isinstance(e.quantity, Decimal) for e in events)
    gaps = sum(1 for e in events if e.has_gap)
    buys = sum(1 for e in events if not e.is_buyer_maker)
    sells = sum(1 for e in events if e.is_buyer_maker)
    print(f"PASS: real file - {len(events)} trades, {gaps} gap(s), {buys} market buys, {sells} market sells")


if __name__ == "__main__":
    test_parse_single_trade()
    test_price_and_qty_are_decimal()
    test_is_buyer_maker_true_means_market_sell()
    test_is_buyer_maker_false_means_market_buy()
    test_no_gap_in_continuous_sequence()
    test_gap_detected()
    test_first_trade_never_flagged_as_gap()
    test_gap_tracked_across_files()
    test_continuous_across_files()
    test_exchange_time_is_T_not_E()
    test_events_on_real_file()
    print("\nAll tests passed.")
