"""
Tests for markout analysis.

Run with: python tests/test_markout.py
"""
from decimal import Decimal

from src.analysis.markout import compute_markouts, summarize_markouts
from src.execution.order import Fill, OrderSide
from src.replay.engine import BookSample


def make_fill(fill_id, side, timestamp_ms, price="100.00", qty="0.1"):
    return Fill(
        fill_id=fill_id,
        order_id=f"order-{fill_id}",
        side=side,
        price=Decimal(price),
        quantity=Decimal(qty),
        is_maker=True,
        timestamp_ms=timestamp_ms,
        fee=Decimal("0"),
    )


def make_sample(timestamp_ms, mid):
    mid = Decimal(mid)
    return BookSample(
        timestamp_ms=timestamp_ms,
        best_bid=mid - Decimal("0.50"),
        best_ask=mid + Decimal("0.50"),
        mid=mid,
        microprice=mid,
        spread=Decimal("1.00"),
    )


def test_fill_matches_first_sample_at_or_after_horizon():
    fill = make_fill("f1", OrderSide.BUY, 1000)
    samples = [
        make_sample(1999, "101.00"),
        make_sample(2000, "102.00"),
        make_sample(2500, "103.00"),
    ]

    rows = compute_markouts([fill], samples, {"1s": 1000})

    assert len(rows) == 1
    assert rows[0].sample_timestamp_ms == 2000
    assert rows[0].future_mid == Decimal("102.00")
    assert rows[0].markout == Decimal("2.00")
    print("PASS: fill matches first sample at or after horizon")


def test_missing_future_horizon_is_skipped():
    fill = make_fill("f1", OrderSide.BUY, 1000)
    samples = [make_sample(1500, "101.00")]

    rows = compute_markouts([fill], samples, {"1s": 1000})

    assert rows == []
    print("PASS: missing future horizon is skipped")


def test_buy_and_sell_sign_convention():
    buy = make_fill("buy", OrderSide.BUY, 1000, price="100.00")
    sell = make_fill("sell", OrderSide.SELL, 1000, price="100.00")
    samples = [make_sample(2000, "101.00")]

    rows = compute_markouts([buy, sell], samples, {"1s": 1000})
    by_fill = {row.fill_id: row for row in rows}

    assert by_fill["buy"].markout == Decimal("1.00")
    assert by_fill["sell"].markout == Decimal("-1.00")
    assert by_fill["buy"].markout_bps == Decimal("100.00")
    print("PASS: buy/sell sign convention makes favorable moves positive")


def test_summary_groups_by_horizon():
    rows = compute_markouts(
        [make_fill("f1", OrderSide.BUY, 1000), make_fill("f2", OrderSide.BUY, 1000)],
        [make_sample(2000, "101.00"), make_sample(6000, "102.00")],
        {"1s": 1000, "5s": 5000},
    )

    summary = summarize_markouts(rows)

    assert summary["1s"]["count"] == 2
    assert summary["1s"]["avg_markout"] == Decimal("1.00")
    assert summary["5s"]["avg_markout"] == Decimal("2.00")
    print("PASS: summary aggregates by horizon")


def test_percentiles_none_when_fewer_than_4_fills():
    rows = compute_markouts(
        [make_fill("f1", OrderSide.BUY, 1000), make_fill("f2", OrderSide.BUY, 1000)],
        [make_sample(2000, "101.00")],
        {"1s": 1000},
    )

    summary = summarize_markouts(rows)

    assert summary["1s"]["p25_markout_bps"] is None
    assert summary["1s"]["p50_markout_bps"] is None
    assert summary["1s"]["p75_markout_bps"] is None
    print("PASS: percentiles are None when fewer than 4 fills")


def test_percentiles_present_with_enough_fills():
    fills = [make_fill(f"f{i}", OrderSide.BUY, 1000) for i in range(8)]
    # mid moves by different amounts: 100, 101, 102, 103, 104, 105, 106, 107
    samples = [make_sample(2000 + i * 10, str(100 + i)) for i in range(8)]

    rows = compute_markouts(fills, samples, {"1s": 1000})
    summary = summarize_markouts(rows)

    assert summary["1s"]["count"] == 8
    assert summary["1s"]["p25_markout_bps"] is not None
    assert summary["1s"]["p50_markout_bps"] is not None
    assert summary["1s"]["p75_markout_bps"] is not None
    # p25 <= p50 <= p75
    assert summary["1s"]["p25_markout_bps"] <= summary["1s"]["p50_markout_bps"]
    assert summary["1s"]["p50_markout_bps"] <= summary["1s"]["p75_markout_bps"]
    print("PASS: percentiles present and ordered with enough fills")


if __name__ == "__main__":
    test_fill_matches_first_sample_at_or_after_horizon()
    test_missing_future_horizon_is_skipped()
    test_buy_and_sell_sign_convention()
    test_summary_groups_by_horizon()
    test_percentiles_none_when_fewer_than_4_fills()
    test_percentiles_present_with_enough_fills()
    print("\nAll tests passed.")
