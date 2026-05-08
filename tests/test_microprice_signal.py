"""
Tests for microprice predictiveness diagnostics.

Run with: python tests/test_microprice_signal.py
"""
from decimal import Decimal

from src.analysis.microprice_signal import (
    bucket_forward_drift_by_signal,
    compute_microprice_signal_samples,
    regress_microprice_signal,
)
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


def test_regular_sampling_uses_latest_book_at_or_before_target():
    samples = [
        make_sample(900, "100.00", 1.0),
        make_sample(1_900, "100.01", -1.0),
        make_sample(2_900, "100.02", 0.0),
    ]

    rows = compute_microprice_signal_samples(
        samples,
        sample_interval_ms=1_000,
        horizons_ms={"1s": 1_000},
        max_staleness_ms=200,
        max_future_lag_ms=1_000,
    )

    assert len(rows) == 1
    assert rows[0].timestamp_ms == 1_000
    assert rows[0].source_timestamp_ms == 900
    assert rows[0].future_timestamp_ms == 2_900
    assert rows[0].microprice_deviation_bps == Decimal("1.0000")
    print("PASS: regular sampling uses latest available book without peeking")


def test_regression_recovers_positive_microprice_beta():
    mids = [Decimal("100.00")]
    signals = [2, -1, 3, -2, 1, -3, 2, -1]
    beta = Decimal("0.50")

    for signal in signals:
        next_mid = mids[-1] * (Decimal("1") + beta * Decimal(signal) / Decimal("10000"))
        mids.append(next_mid)

    samples = [
        make_sample(idx * 1_000, mid, signals[idx] if idx < len(signals) else 0)
        for idx, mid in enumerate(mids)
    ]
    rows = compute_microprice_signal_samples(
        samples,
        sample_interval_ms=1_000,
        horizons_ms={"1s": 1_000},
        max_staleness_ms=0,
        max_future_lag_ms=0,
    )
    regression = regress_microprice_signal(rows, sample_interval_ms=1_000, hac_lags=1)[0]

    assert regression.n == len(signals)
    assert regression.beta is not None
    assert abs(regression.beta - 0.5) < 1e-10
    assert regression.r2 is not None
    assert regression.r2 > 0.999999
    print("PASS: regression recovers known positive signal beta")


def test_signal_bucket_summary_orders_drift_by_signal():
    samples = [
        make_sample(0, "100.00", -1.0),
        make_sample(1_000, "99.99", 1.0),
        make_sample(2_000, "100.01", 0.0),
    ]
    rows = compute_microprice_signal_samples(
        samples,
        sample_interval_ms=1_000,
        horizons_ms={"1s": 1_000},
        max_staleness_ms=0,
        max_future_lag_ms=0,
    )
    buckets = bucket_forward_drift_by_signal(rows, edges_bps=[0])

    assert buckets[0].label == "<0bps"
    assert buckets[0].n == 1
    assert buckets[0].avg_forward_drift_bps < 0
    assert buckets[1].label == ">=0bps"
    assert buckets[1].n == 1
    assert buckets[1].avg_forward_drift_bps > 0
    print("PASS: bucket summary preserves signal direction")


if __name__ == "__main__":
    test_regular_sampling_uses_latest_book_at_or_before_target()
    test_regression_recovers_positive_microprice_beta()
    test_signal_bucket_summary_orders_drift_by_signal()
    print("\nAll tests passed.")
