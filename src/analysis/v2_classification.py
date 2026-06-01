"""Mechanical Phase A verdict classification for the V2 baseline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Mapping


VERDICT_ORDER = {
    "Strengthens V1": 0,
    "Confirms V1": 1,
    "Weakens V1": 2,
    "Overturns V1": 3,
}
CONDITIONAL_V2 = "Conditional V2: queue-model-dependent result"


@dataclass(frozen=True)
class EndpointVerdict:
    queue_cancellation_credit: Decimal
    mean_net_pnl: Decimal
    ci_low: Decimal
    ci_high: Decimal
    frozen_v1_mean_net_pnl: Decimal
    material_improvement_threshold: Decimal
    verdict: str


def classify_endpoint(
    *,
    queue_cancellation_credit: Decimal,
    mean_net_pnl: Decimal,
    ci_low: Decimal,
    ci_high: Decimal,
    frozen_v1_mean_net_pnl: Decimal,
    material_improvement_fraction: Decimal = Decimal("0.25"),
) -> EndpointVerdict:
    """Classify one queue-credit endpoint using the ordered V2 ladder."""
    threshold = abs(frozen_v1_mean_net_pnl) * material_improvement_fraction
    if ci_low > Decimal("0"):
        verdict = "Overturns V1"
    elif ci_high < Decimal("0"):
        verdict = "Strengthens V1"
    elif mean_net_pnl - frozen_v1_mean_net_pnl >= threshold:
        verdict = "Weakens V1"
    else:
        verdict = "Confirms V1"
    return EndpointVerdict(
        queue_cancellation_credit=queue_cancellation_credit,
        mean_net_pnl=mean_net_pnl,
        ci_low=ci_low,
        ci_high=ci_high,
        frozen_v1_mean_net_pnl=frozen_v1_mean_net_pnl,
        material_improvement_threshold=threshold,
        verdict=verdict,
    )


def classify_phase_a(endpoints: Mapping[str, EndpointVerdict]) -> dict:
    """Emit an endpoint-aware headline and conservative passive-edge verdict."""
    if not endpoints:
        raise ValueError("at least one endpoint verdict is required")
    verdicts = {row.verdict for row in endpoints.values()}
    conservative = min(
        endpoints.values(),
        key=lambda row: VERDICT_ORDER[row.verdict],
    )
    headline = conservative.verdict if len(verdicts) == 1 else CONDITIONAL_V2
    return {
        "headline": headline,
        "conservative_passive_edge_verdict": conservative.verdict,
        "endpoints": {key: asdict(value) for key, value in endpoints.items()},
    }
