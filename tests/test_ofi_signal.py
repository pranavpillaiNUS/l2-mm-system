"""Tests for OFI signal and fill-toxicity diagnostics."""

from decimal import Decimal

from src.analysis.ofi_signal import (
    bucket_ofi_fill_toxicity,
    compute_ofi_fill_toxicity,
    compute_ofi_signal_samples,
    regress_ofi_signal,
)
from src.execution.order import Fill, OrderSide
from src.replay.engine import BookSample


def sample(t, bid, bid_qty, ask, ask_qty):
    bid = Decimal(bid)
    ask = Decimal(ask)
    bid_qty = Decimal(bid_qty)
    ask_qty = Decimal(ask_qty)
    mid = (bid + ask) / Decimal("2")
    microprice = (bid_qty * ask + ask_qty * bid) / (bid_qty + ask_qty)
    return BookSample(
        timestamp_ms=t,
        best_bid=bid,
        best_ask=ask,
        mid=mid,
        microprice=microprice,
        spread=ask - bid,
        best_bid_qty=bid_qty,
        best_ask_qty=ask_qty,
    )


def fill(fill_id, side, t, price="100.00"):
    return Fill(
        fill_id=fill_id,
        order_id=f"order-{fill_id}",
        side=side,
        price=Decimal(price),
        quantity=Decimal("0.1"),
        is_maker=True,
        timestamp_ms=t,
        fee=Decimal("0"),
    )


def test_ofi_increment_positive_when_bid_steps_up():
    samples = [
        sample(0, "100.00", "5", "101.00", "3"),
        sample(1_000, "100.50", "4", "101.00", "3"),
        sample(2_000, "101.00", "4", "102.00", "3"),
    ]
    rows = compute_ofi_signal_samples(
        samples,
        sample_interval_ms=1_000,
        ofi_interval_ms=1_000,
        horizons_ms={"1s": 1_000},
        max_staleness_ms=0,
        max_future_lag_ms=0,
    )

    assert len(rows) == 1
    assert rows[0].raw_ofi == Decimal("4")
    assert rows[0].normalized_ofi == Decimal("4") / Decimal("7")
    assert rows[0].forward_drift_bps > 0


def test_ofi_regression_recovers_positive_relationship():
    samples = [
        sample(0, "100.00", "5", "101.00", "5"),
        sample(1_000, "100.50", "6", "101.00", "5"),
        sample(2_000, "101.00", "6", "101.50", "5"),
        sample(3_000, "100.50", "5", "101.00", "6"),
        sample(4_000, "100.00", "5", "100.50", "6"),
    ]
    rows = compute_ofi_signal_samples(
        samples,
        sample_interval_ms=1_000,
        ofi_interval_ms=1_000,
        horizons_ms={"1s": 1_000},
        max_staleness_ms=0,
        max_future_lag_ms=0,
    )
    regression = regress_ofi_signal(rows, hac_lags=0)[0]

    assert regression.beta is not None
    assert regression.beta > 0


def test_ofi_fill_toxicity_side_aligns_signal():
    fills = [
        fill("buy", OrderSide.BUY, 1_000, price="100.00"),
        fill("sell", OrderSide.SELL, 1_000, price="101.00"),
    ]
    samples = [
        sample(0, "100.00", "5", "101.00", "5"),
        sample(1_000, "100.50", "6", "101.00", "5"),
        sample(2_000, "101.00", "6", "101.50", "5"),
    ]

    rows = compute_ofi_fill_toxicity(
        fills,
        samples,
        {"1s": 1_000},
        ofi_interval_ms=1_000,
        max_staleness_ms=0,
        max_future_lag_ms=0,
    )
    buckets = bucket_ofi_fill_toxicity(rows, edges=[0])

    buy_row = [row for row in rows if row.side == OrderSide.BUY][0]
    sell_row = [row for row in rows if row.side == OrderSide.SELL][0]
    assert buy_row.side_aligned_ofi > 0
    assert sell_row.side_aligned_ofi < 0
    assert any(row.side == "all" and row.bucket == ">=0" for row in buckets)
