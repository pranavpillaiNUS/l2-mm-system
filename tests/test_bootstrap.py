"""
Tests for bootstrap CI helpers.

Run with: python tests/test_bootstrap.py
"""
from decimal import Decimal

from src.analysis.bootstrap import bootstrap_mean_ci, mean_decimal, percentile


def test_mean_decimal_handles_empty_and_values():
    assert mean_decimal([]) is None
    assert mean_decimal([Decimal("1"), Decimal("2"), Decimal("3")]) == Decimal("2")
    print("PASS: Decimal mean handles empty and populated inputs")


def test_percentile_interpolates():
    values = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")]

    assert percentile(values, Decimal("0")) == Decimal("1")
    assert percentile(values, Decimal("0.50")) == Decimal("2.5")
    assert percentile(values, Decimal("1")) == Decimal("4")
    print("PASS: percentile uses linear interpolation")


def test_single_observation_ci_is_degenerate():
    result = bootstrap_mean_ci(
        [Decimal("1.25")],
        metric="matched_net_pnl",
        unit="window",
        iterations=100,
    )

    assert result.n == 1
    assert result.mean == Decimal("1.25")
    assert result.ci_low == Decimal("1.25")
    assert result.ci_high == Decimal("1.25")
    print("PASS: single-observation bootstrap CI is degenerate")


def test_bootstrap_mean_ci_is_deterministic():
    values = [Decimal("1"), Decimal("2"), Decimal("3")]
    first = bootstrap_mean_ci(
        values,
        metric="x",
        unit="session",
        iterations=200,
        seed=11,
    )
    second = bootstrap_mean_ci(
        values,
        metric="x",
        unit="session",
        iterations=200,
        seed=11,
    )

    assert first.mean == Decimal("2")
    assert first.ci_low == second.ci_low
    assert first.ci_high == second.ci_high
    assert first.ci_low <= first.mean <= first.ci_high
    print("PASS: bootstrap CI is deterministic with a seed")


if __name__ == "__main__":
    test_mean_decimal_handles_empty_and_values()
    test_percentile_interpolates()
    test_single_observation_ci_is_degenerate()
    test_bootstrap_mean_ci_is_deterministic()
    print("\nAll tests passed.")
