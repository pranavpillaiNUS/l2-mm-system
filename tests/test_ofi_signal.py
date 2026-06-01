"""Tests for OFI signal and fill-toxicity diagnostics."""

from decimal import Decimal

from src.analysis.ofi_signal import (
    OFIFillToxicityBucket,
    OFIRegression,
    bucket_ofi_fill_toxicity,
    compute_ofi_fill_toxicity,
    compute_ofi_signal_samples,
    evaluate_ofi_gates,
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


def regression(beta, *, t_stat=2.1, effect=0.06):
    return OFIRegression(
        horizon="1s",
        horizon_ms=1_000,
        signal="normalized_ofi",
        n=100,
        alpha=0.0,
        beta=beta,
        r2=0.1,
        t_stat=t_stat,
        hac_lags=1,
        x_mean=0.0,
        y_mean_bps=0.0,
        x_std=1.0,
        y_std_bps=1.0,
        predicted_drift_1std_bps=effect,
    )


def toxicity_bucket(signal, move, n):
    return OFIFillToxicityBucket(
        horizon="30s",
        side="all",
        bucket=str(signal),
        n=n,
        avg_side_aligned_ofi=Decimal(signal),
        avg_mid_move_bps=Decimal(move),
        median_mid_move_bps=Decimal(move),
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


def test_ofi_gate_requires_75_percent_window_sign_stability():
    gates = evaluate_ofi_gates(
        window_regressions=[
            regression(1.0),
            regression(1.0),
            regression(1.0),
            regression(-1.0),
            regression(-1.0),
        ],
        pooled_regressions=[regression(1.0)],
        fill_buckets=[
            toxicity_bucket("-0.5", "-1.0", 30),
            toxicity_bucket("0.5", "0.5", 30),
        ],
    )

    assert gates.stable_sign_share == Decimal("0.6")
    assert not gates.stable_sign_pass
    assert gates.overall_verdict == "blocked"


def test_ofi_gate_marks_thin_conditional_buckets_inconclusive_not_failed():
    gates = evaluate_ofi_gates(
        window_regressions=[regression(1.0)] * 3 + [regression(-1.0)],
        pooled_regressions=[regression(1.0)],
        fill_buckets=[
            toxicity_bucket("-0.5", "-1.0", 29),
            toxicity_bucket("0.5", "0.5", 40),
        ],
    )

    assert gates.conditional_status == "inconclusive_power"
    assert gates.overall_verdict == "supported_with_conditional_power_limit"
    assert gates.all_pass


def test_ofi_gate_marks_powered_weak_separation_as_signal_failure():
    gates = evaluate_ofi_gates(
        window_regressions=[regression(1.0)] * 3 + [regression(-1.0)],
        pooled_regressions=[regression(1.0)],
        fill_buckets=[
            toxicity_bucket("-0.5", "-0.2", 30),
            toxicity_bucket("0.5", "0.2", 30),
        ],
    )

    assert gates.conditional_status == "fail_signal"
    assert gates.overall_verdict == "blocked"


def test_ofi_fallback_ignores_missing_conditional_power_as_numeric_miss():
    gates = evaluate_ofi_gates(
        window_regressions=[regression(1.0)] * 3 + [regression(-1.0)],
        pooled_regressions=[regression(1.0, t_stat=1.8, effect=0.045)],
        fill_buckets=[
            toxicity_bucket("-0.5", "-1.0", 2),
            toxicity_bucket("0.5", "0.5", 3),
        ],
    )

    assert gates.conditional_status == "inconclusive_power"
    assert gates.marginal_5s_fallback
