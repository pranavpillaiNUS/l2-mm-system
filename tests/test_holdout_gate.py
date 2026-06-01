"""Tests for locked V2 holdout pass, mixed, and fail outcomes."""

from decimal import Decimal

from src.analysis.holdout_gate import evaluate_holdout, evaluate_holdout_endpoint
from src.analysis.strategy_gate import WindowStrategyMetrics


def row(*, matched, full="-1", fills=10, makers=10):
    return WindowStrategyMetrics(
        window="a",
        fills=fills,
        maker_fills=makers,
        matched_quantity=Decimal("1"),
        matched_net_pnl=Decimal(matched),
        full_strategy_net_pnl=Decimal(full),
        average_abs_residual_inventory=Decimal("0.01"),
    )


def endpoint(*, candidate_matched, candidate_full="-1", makers=10):
    return evaluate_holdout_endpoint(
        [row(matched="-2")],
        [row(matched=candidate_matched, full=candidate_full, makers=makers)],
        queue_cancellation_credit=Decimal("1"),
        locked_development_lower_ci_bound=Decimal("0.5"),
    )


def test_holdout_pass_requires_positive_improvement_bound_and_guardrails():
    assert endpoint(candidate_matched="-1").status == "pass"


def test_holdout_mixed_when_economics_improve_but_guardrail_fails():
    assert endpoint(candidate_matched="-1", makers=9).status == "mixed"


def test_holdout_fail_has_precedence_when_either_endpoint_is_non_positive():
    report = evaluate_holdout({
        "0.0": endpoint(candidate_matched="-1"),
        "1.0": endpoint(candidate_matched="-2"),
    })

    assert report["status"] == "fail"
