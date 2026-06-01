"""Tests for paired per-BTC candidate advancement gates."""

from decimal import Decimal

from src.analysis.strategy_gate import (
    WindowStrategyMetrics,
    evaluate_endpoint_strategy_gate,
)


def row(window, *, fills, qty, matched, full="-2", residual="0.01", makers=None):
    return WindowStrategyMetrics(
        window=window,
        fills=fills,
        maker_fills=fills if makers is None else makers,
        matched_quantity=Decimal(qty),
        matched_net_pnl=Decimal(matched),
        full_strategy_net_pnl=Decimal(full),
        average_abs_residual_inventory=Decimal(residual),
    )


def test_strategy_gate_uses_per_btc_delta_not_total_matched_pnl_delta():
    baseline = [row("a", fills=10, qty="1", matched="-2")]
    candidate = [row("a", fills=5, qty="0.5", matched="-1")]

    gate = evaluate_endpoint_strategy_gate(
        baseline,
        candidate,
        queue_cancellation_credit=Decimal("1"),
        iterations=10,
    )

    assert candidate[0].matched_net_pnl > baseline[0].matched_net_pnl
    assert gate.paired_per_btc_delta_ci.mean == Decimal("0")
    assert not gate.advance


def test_strategy_gate_advances_only_when_economics_and_guardrails_pass():
    baseline = [
        row("a", fills=10, qty="1", matched="-2"),
        row("b", fills=10, qty="1", matched="-1"),
    ]
    candidate = [
        row("a", fills=8, qty="1", matched="-1", full="-1"),
        row("b", fills=8, qty="1", matched="0", full="-1"),
    ]

    gate = evaluate_endpoint_strategy_gate(
        baseline,
        candidate,
        queue_cancellation_credit=Decimal("0"),
        iterations=20,
    )

    assert gate.paired_per_btc_delta_ci.ci_low == Decimal("1")
    assert gate.advance


def test_strategy_gate_requires_positive_quantity_in_every_window():
    baseline = [
        row("a", fills=10, qty="1", matched="-2"),
        row("b", fills=10, qty="1", matched="-2"),
    ]
    candidate = [
        row("a", fills=10, qty="1", matched="0"),
        row("b", fills=0, qty="0", matched="0"),
    ]

    gate = evaluate_endpoint_strategy_gate(
        baseline,
        candidate,
        queue_cancellation_credit=Decimal("1"),
        iterations=10,
    )

    assert not gate.positive_matched_quantity_every_window
    assert not gate.advance
