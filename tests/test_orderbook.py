"""
Tests for Orderbook.

Run with: python tests/test_orderbook.py
"""
from decimal import Decimal
from src.replay.orderbook import Orderbook


def test_empty_book():
    book = Orderbook()
    assert book.best_bid is None
    assert book.best_ask is None
    assert book.mid is None
    assert book.spread is None
    assert book.microprice is None
    assert not book.is_crossed()
    assert len(book) == 0
    print("PASS: empty book returns None for all prices")


def test_apply_snapshot_basic():
    book = Orderbook()
    book.apply_snapshot(
        bids=[("83500.00", "1.0"), ("83499.00", "2.0")],
        asks=[("83501.00", "0.5"), ("83502.00", "1.5")],
        last_update_id=1000,
    )
    assert book.best_bid == Decimal("83500.00")
    assert book.best_ask == Decimal("83501.00")
    assert book.spread == Decimal("1.00")
    assert book.mid == Decimal("83500.50")
    assert book.last_update_id == 1000
    assert book.sequence == 0
    assert len(book._bids) == 2
    assert len(book._asks) == 2
    print("PASS: apply_snapshot sets correct best bid/ask, spread, mid")


def test_apply_snapshot_drops_zero_qty():
    # qty "0" in a snapshot means that level doesn't exist - skip it
    book = Orderbook()
    book.apply_snapshot(
        bids=[("83500.00", "1.0"), ("83499.00", "0.0")],
        asks=[("83501.00", "0.5")],
        last_update_id=1000,
    )
    assert len(book._bids) == 1
    assert Decimal("83499.00") not in book._bids
    print("PASS: apply_snapshot ignores zero-quantity levels")


def test_apply_snapshot_resets_sequence():
    book = Orderbook()
    book.apply_snapshot(
        bids=[("100.00", "1.0")],
        asks=[("101.00", "1.0")],
        last_update_id=500,
    )
    book.apply_diff(bids=[("100.00", "2.0")], asks=[], last_update_id=501)
    book.apply_diff(bids=[("100.00", "3.0")], asks=[], last_update_id=502)
    assert book.sequence == 2

    # new snapshot resets sequence to 0
    book.apply_snapshot(
        bids=[("100.00", "1.0")],
        asks=[("101.00", "1.0")],
        last_update_id=600,
    )
    assert book.sequence == 0
    assert book.last_update_id == 600
    print("PASS: apply_snapshot resets sequence counter")


def test_apply_diff_update_level():
    book = Orderbook()
    book.apply_snapshot(
        bids=[("83500.00", "1.0")],
        asks=[("83501.00", "0.5")],
        last_update_id=1000,
    )
    book.apply_diff(
        bids=[("83500.00", "2.5"), ("83499.00", "1.0")],
        asks=[],
        last_update_id=1001,
    )
    assert book._bids[Decimal("83500.00")] == Decimal("2.5")
    assert Decimal("83499.00") in book._bids
    assert book.sequence == 1
    print("PASS: apply_diff updates existing level and adds new level")


def test_apply_diff_remove_level():
    book = Orderbook()
    book.apply_snapshot(
        bids=[("83500.00", "1.0"), ("83499.00", "2.0")],
        asks=[("83501.00", "0.5")],
        last_update_id=1000,
    )
    # qty "0" means remove this level
    book.apply_diff(
        bids=[("83500.00", "0")],
        asks=[],
        last_update_id=1001,
    )
    assert Decimal("83500.00") not in book._bids
    assert book.best_bid == Decimal("83499.00")
    print("PASS: apply_diff removes level when qty is zero")


def test_bid_ask_level_ordering():
    book = Orderbook()
    book.apply_snapshot(
        bids=[("100.00", "1.0"), ("99.00", "2.0"), ("98.00", "3.0")],
        asks=[("101.00", "1.0"), ("102.00", "2.0"), ("103.00", "3.0")],
        last_update_id=1,
    )
    bids = book.bid_levels(3)
    asks = book.ask_levels(3)

    # bid levels should come out best-first (descending price)
    assert bids[0][0] == Decimal("100.00")
    assert bids[1][0] == Decimal("99.00")
    assert bids[2][0] == Decimal("98.00")

    # ask levels should come out best-first (ascending price)
    assert asks[0][0] == Decimal("101.00")
    assert asks[1][0] == Decimal("102.00")
    assert asks[2][0] == Decimal("103.00")
    print("PASS: bid_levels descending, ask_levels ascending")


def test_microprice_skews_toward_thin_side():
    # bid: 1 BTC @ 100, ask: 3 BTC @ 101
    # More supply on ask -> price more likely to move DOWN -> microprice < mid
    # mid = 100.5
    # microprice = (1 * 101 + 3 * 100) / (1 + 3) = 401 / 4 = 100.25 < 100.5
    book = Orderbook()
    book.apply_snapshot(
        bids=[("100.00", "1.0")],
        asks=[("101.00", "3.0")],
        last_update_id=1,
    )
    expected = (Decimal("1.0") * Decimal("101.00") + Decimal("3.0") * Decimal("100.00")) / Decimal("4.0")
    assert book.microprice == expected
    assert book.microprice < book.mid  # more ask size -> microprice below mid
    print(f"PASS: microprice = {book.microprice} (mid = {book.mid}, more ask size -> below mid)")


def test_microprice_equal_sizes_equals_mid():
    # equal sizes on both sides -> microprice == mid
    book = Orderbook()
    book.apply_snapshot(
        bids=[("100.00", "2.0")],
        asks=[("102.00", "2.0")],
        last_update_id=1,
    )
    assert book.microprice == book.mid
    print(f"PASS: microprice == mid when sizes are equal ({book.microprice})")


def test_is_crossed():
    book = Orderbook()
    # manually insert a crossed book (would never happen in clean data)
    book._bids[Decimal("101.00")] = Decimal("1.0")
    book._asks[Decimal("100.00")] = Decimal("1.0")
    assert book.is_crossed()
    print("PASS: is_crossed detects bid >= ask")


def test_not_crossed_normal():
    book = Orderbook()
    book.apply_snapshot(
        bids=[("100.00", "1.0")],
        asks=[("101.00", "1.0")],
        last_update_id=1,
    )
    assert not book.is_crossed()
    print("PASS: normal book is not crossed")


def test_state_hash_determinism():
    # two books built identically must produce the same hash
    bids = [("83500.00", "1.0"), ("83499.00", "2.0")]
    asks = [("83501.00", "0.5"), ("83502.00", "1.5")]

    book1 = Orderbook()
    book2 = Orderbook()
    book1.apply_snapshot(bids, asks, last_update_id=1000)
    book2.apply_snapshot(bids, asks, last_update_id=1000)

    assert book1.state_hash() == book2.state_hash()
    print("PASS: identical books produce identical state hash")


def test_state_hash_changes_on_update():
    book = Orderbook()
    book.apply_snapshot(
        bids=[("83500.00", "1.0")],
        asks=[("83501.00", "0.5")],
        last_update_id=1000,
    )
    h1 = book.state_hash()
    book.apply_diff(bids=[("83500.00", "2.0")], asks=[], last_update_id=1001)
    h2 = book.state_hash()
    assert h1 != h2
    print("PASS: state hash changes after diff is applied")


def test_repr():
    book = Orderbook()
    book.apply_snapshot(
        bids=[("100.00", "1.0")],
        asks=[("101.00", "1.0")],
        last_update_id=1,
    )
    r = repr(book)
    assert "100.00" in r and "101.00" in r
    print(f"PASS: repr = {r}")


if __name__ == "__main__":
    test_empty_book()
    test_apply_snapshot_basic()
    test_apply_snapshot_drops_zero_qty()
    test_apply_snapshot_resets_sequence()
    test_apply_diff_update_level()
    test_apply_diff_remove_level()
    test_bid_ask_level_ordering()
    test_microprice_skews_toward_thin_side()
    test_microprice_equal_sizes_equals_mid()
    test_is_crossed()
    test_not_crossed_normal()
    test_state_hash_determinism()
    test_state_hash_changes_on_update()
    test_repr()
    print("\nAll tests passed.")
