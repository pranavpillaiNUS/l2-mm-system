"""
Tests for conditional-on-fill microprice toxicity diagnostics.
"""
from decimal import Decimal

from src.analysis.microprice_fill_toxicity import (
    bucket_microprice_fill_toxicity,
    compute_microprice_fill_toxicity,
)
from src.execution.order import Fill, OrderSide
from src.replay.engine import BookSample


def make_sample(t_ms, mid, signal_bps):
    mid = Decimal(mid)
    signal = Decimal(str(signal_bps))
    microprice = mid * (Decimal("1") + signal / Decimal("10000"))
    return BookSample(
        timestamp_ms=t_ms,
        best_bid=mid - Decimal("0.50"),
        best_ask=mid + Decimal("0.50"),
        mid=mid,
        microprice=microprice,
        spread=Decimal("1.00"),
    )


def make_fill(fill_id, side, t_ms, price="100.00", maker=True):
    return Fill(
        fill_id=fill_id,
        order_id=f"order-{fill_id}",
        side=side,
        price=Decimal(price),
        quantity=Decimal("0.1"),
        is_maker=maker,
        timestamp_ms=t_ms,
        fee=Decimal("0"),
    )


def test_buy_fill_positive_skew_and_up_move_is_favorable():
    fill = make_fill("f1", OrderSide.BUY, 1_000, price="99.50")
    samples = [
        make_sample(1_000, "100.00", 0.05),
        make_sample(2_000, "101.00", 0.00),
    ]

    rows = compute_microprice_fill_toxicity(
        [fill], samples, {"1s": 1_000}, max_staleness_ms=0, max_future_lag_ms=0,
    )

    assert len(rows) == 1
    assert rows[0].microprice_deviation_bps == Decimal("0.050000")
    assert rows[0].side_aligned_skew_bps == Decimal("0.050000")
    assert rows[0].side_normalized_mid_move == Decimal("1.00")
    assert rows[0].side_normalized_mid_move_bps == Decimal("100.00")
    assert rows[0].fill_edge_bps == Decimal("50.00")
    print("PASS: buy fill treats positive skew and upward mid move as favorable")


def test_sell_fill_positive_skew_and_up_move_is_adverse():
    fill = make_fill("f1", OrderSide.SELL, 1_000, price="100.50")
    samples = [
        make_sample(1_000, "100.00", 0.05),
        make_sample(2_000, "101.00", 0.00),
    ]

    rows = compute_microprice_fill_toxicity(
        [fill], samples, {"1s": 1_000}, max_staleness_ms=0, max_future_lag_ms=0,
    )

    assert len(rows) == 1
    assert rows[0].microprice_deviation_bps == Decimal("0.050000")
    assert rows[0].side_aligned_skew_bps == Decimal("-0.050000")
    assert rows[0].side_normalized_mid_move == Decimal("-1.00")
    assert rows[0].side_normalized_mid_move_bps == Decimal("-100.00")
    assert rows[0].fill_edge_bps == Decimal("50.00")
    print("PASS: sell fill treats positive skew and upward mid move as adverse")


def test_stale_current_book_sample_is_skipped():
    fill = make_fill("f1", OrderSide.BUY, 2_500)
    samples = [
        make_sample(1_000, "100.00", 0.05),
        make_sample(3_500, "101.00", 0.00),
    ]

    rows = compute_microprice_fill_toxicity(
        [fill], samples, {"1s": 1_000}, max_staleness_ms=1_000, max_future_lag_ms=0,
    )

    assert rows == []
    print("PASS: stale fill-time book sample is skipped")


def test_bucket_summary_splits_by_side_aligned_skew():
    fills = [
        make_fill("buy_good", OrderSide.BUY, 1_000, price="99.50"),
        make_fill("sell_bad", OrderSide.SELL, 1_000, price="100.50"),
    ]
    samples = [
        make_sample(1_000, "100.00", 0.05),
        make_sample(2_000, "101.00", 0.00),
    ]

    rows = compute_microprice_fill_toxicity(
        fills, samples, {"1s": 1_000}, max_staleness_ms=0, max_future_lag_ms=0,
    )
    buckets = bucket_microprice_fill_toxicity(rows, edges_bps=[0])

    all_negative = [
        row for row in buckets
        if row.side == "all" and row.bucket == "<0bps"
    ][0]
    all_positive = [
        row for row in buckets
        if row.side == "all" and row.bucket == ">=0bps"
    ][0]

    assert all_negative.n == 1
    assert all_negative.avg_mid_move_bps == Decimal("-100.00")
    assert all_positive.n == 1
    assert all_positive.avg_mid_move_bps == Decimal("100.00")
    print("PASS: buckets split conditional toxicity by side-aligned skew")
