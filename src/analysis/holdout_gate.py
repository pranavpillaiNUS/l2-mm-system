"""Locked one-shot holdout evaluation for V2 candidate strategies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Mapping, Sequence

from src.analysis.strategy_gate import (
    WindowStrategyMetrics,
    evaluate_endpoint_strategy_gate,
)


@dataclass(frozen=True)
class EndpointHoldoutGate:
    queue_cancellation_credit: Decimal
    mean_paired_per_btc_improvement: Decimal | None
    locked_development_lower_ci_bound: Decimal
    directionally_positive: bool
    meets_locked_development_bound: bool
    positive_matched_quantity_every_window: bool
    fill_activity_pass: bool
    maker_only_pass: bool
    full_strategy_pnl_pass: bool
    residual_inventory_pass: bool
    status: str


def evaluate_holdout_endpoint(
    baseline: Sequence[WindowStrategyMetrics],
    candidate: Sequence[WindowStrategyMetrics],
    *,
    queue_cancellation_credit: Decimal,
    locked_development_lower_ci_bound: Decimal,
) -> EndpointHoldoutGate:
    """Evaluate locked economics and guardrails at one holdout endpoint."""
    gate = evaluate_endpoint_strategy_gate(
        baseline,
        candidate,
        queue_cancellation_credit=queue_cancellation_credit,
        iterations=1,
    )
    mean = gate.paired_per_btc_delta_ci.mean
    positive = mean is not None and mean > Decimal("0")
    meets_bound = mean is not None and mean >= locked_development_lower_ci_bound
    guardrails = [
        gate.positive_matched_quantity_every_window,
        gate.fill_activity_pass,
        gate.maker_only_pass,
        gate.full_strategy_pnl_pass,
        gate.residual_inventory_pass,
    ]
    if not positive:
        status = "fail"
    elif meets_bound and all(guardrails):
        status = "pass"
    else:
        status = "mixed"
    return EndpointHoldoutGate(
        queue_cancellation_credit=queue_cancellation_credit,
        mean_paired_per_btc_improvement=mean,
        locked_development_lower_ci_bound=locked_development_lower_ci_bound,
        directionally_positive=positive,
        meets_locked_development_bound=meets_bound,
        positive_matched_quantity_every_window=gate.positive_matched_quantity_every_window,
        fill_activity_pass=gate.fill_activity_pass,
        maker_only_pass=gate.maker_only_pass,
        full_strategy_pnl_pass=gate.full_strategy_pnl_pass,
        residual_inventory_pass=gate.residual_inventory_pass,
        status=status,
    )


def evaluate_holdout(endpoints: Mapping[str, EndpointHoldoutGate]) -> dict:
    """Use fail precedence, then mixed, for the two-endpoint holdout headline."""
    if not endpoints:
        raise ValueError("at least one holdout endpoint is required")
    statuses = {row.status for row in endpoints.values()}
    if "fail" in statuses:
        headline = "fail"
    elif "mixed" in statuses:
        headline = "mixed"
    else:
        headline = "pass"
    return {
        "status": headline,
        "endpoints": {key: asdict(value) for key, value in endpoints.items()},
    }
