"""Paired per-window economics gates for V2 candidate strategies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Mapping, Sequence

from src.analysis.bootstrap import BootstrapResult, bootstrap_mean_ci


@dataclass(frozen=True)
class WindowStrategyMetrics:
    window: str
    fills: int
    maker_fills: int
    matched_quantity: Decimal
    matched_net_pnl: Decimal
    full_strategy_net_pnl: Decimal
    average_abs_residual_inventory: Decimal

    @property
    def quantity_weighted_matched_net_pnl_per_btc(self) -> Decimal | None:
        if self.matched_quantity <= Decimal("0"):
            return None
        return self.matched_net_pnl / self.matched_quantity


@dataclass(frozen=True)
class EndpointStrategyGate:
    queue_cancellation_credit: Decimal
    paired_per_btc_delta_ci: BootstrapResult
    baseline_fills: int
    candidate_fills: int
    candidate_fill_share: Decimal | None
    positive_matched_quantity_every_window: bool
    fill_activity_pass: bool
    maker_only_pass: bool
    full_strategy_pnl_pass: bool
    residual_inventory_pass: bool
    advance: bool


def evaluate_endpoint_strategy_gate(
    baseline: Sequence[WindowStrategyMetrics],
    candidate: Sequence[WindowStrategyMetrics],
    *,
    queue_cancellation_credit: Decimal,
    iterations: int = 10_000,
    seed: int = 7,
) -> EndpointStrategyGate:
    """Evaluate OFIGatedMM advancement at one queue-credit endpoint."""
    baseline_by_window = _index_windows(baseline)
    candidate_by_window = _index_windows(candidate)
    if baseline_by_window.keys() != candidate_by_window.keys():
        raise ValueError("baseline and candidate windows must match exactly")

    positive_quantity = all(
        row.matched_quantity > Decimal("0")
        for row in candidate_by_window.values()
    )
    deltas: list[Decimal] = []
    for window in sorted(baseline_by_window):
        baseline_value = baseline_by_window[
            window
        ].quantity_weighted_matched_net_pnl_per_btc
        candidate_value = candidate_by_window[
            window
        ].quantity_weighted_matched_net_pnl_per_btc
        if baseline_value is not None and candidate_value is not None:
            deltas.append(candidate_value - baseline_value)

    ci = bootstrap_mean_ci(
        deltas,
        metric="quantity_weighted_matched_net_pnl_per_btc_delta",
        unit="paired_5h_window",
        iterations=iterations,
        seed=seed,
    )
    baseline_fills = sum(row.fills for row in baseline_by_window.values())
    candidate_fills = sum(row.fills for row in candidate_by_window.values())
    fill_share = (
        Decimal(candidate_fills) / Decimal(baseline_fills)
        if baseline_fills else None
    )
    fill_activity_pass = fill_share is not None and fill_share >= Decimal("0.50")
    maker_only_pass = candidate_fills > 0 and all(
        row.maker_fills == row.fills for row in candidate_by_window.values()
    )
    full_strategy_pnl_pass = sum(
        (row.full_strategy_net_pnl for row in candidate_by_window.values()),
        Decimal("0"),
    ) >= sum(
        (row.full_strategy_net_pnl for row in baseline_by_window.values()),
        Decimal("0"),
    )
    baseline_residual = _mean_residual_inventory(baseline_by_window.values())
    candidate_residual = _mean_residual_inventory(candidate_by_window.values())
    residual_inventory_pass = (
        candidate_residual <= baseline_residual * Decimal("1.10")
    )
    ci_pass = ci.ci_low is not None and ci.ci_low > Decimal("0")
    advance = all([
        ci_pass,
        positive_quantity,
        fill_activity_pass,
        maker_only_pass,
        full_strategy_pnl_pass,
        residual_inventory_pass,
    ])
    return EndpointStrategyGate(
        queue_cancellation_credit=queue_cancellation_credit,
        paired_per_btc_delta_ci=ci,
        baseline_fills=baseline_fills,
        candidate_fills=candidate_fills,
        candidate_fill_share=fill_share,
        positive_matched_quantity_every_window=positive_quantity,
        fill_activity_pass=fill_activity_pass,
        maker_only_pass=maker_only_pass,
        full_strategy_pnl_pass=full_strategy_pnl_pass,
        residual_inventory_pass=residual_inventory_pass,
        advance=advance,
    )


def evaluate_strategy_gate(endpoints: Mapping[str, EndpointStrategyGate]) -> dict:
    """Require candidate advancement at both queue-credit endpoints."""
    if not endpoints:
        raise ValueError("at least one endpoint gate is required")
    return {
        "advance": all(row.advance for row in endpoints.values()),
        "endpoints": {key: asdict(value) for key, value in endpoints.items()},
    }


def _index_windows(rows: Sequence[WindowStrategyMetrics]) -> dict[str, WindowStrategyMetrics]:
    out = {row.window: row for row in rows}
    if len(out) != len(rows):
        raise ValueError("window metrics must have unique window labels")
    return out


def _mean_residual_inventory(rows) -> Decimal:
    values = [row.average_abs_residual_inventory for row in rows]
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")
