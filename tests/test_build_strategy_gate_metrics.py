"""Tests for strategy-gate metric extraction."""

from scripts.build_strategy_gate_metrics import _metric_row


def test_strategy_gate_metric_adapter_uses_per_window_reconciliation_fields():
    row = _metric_row({
        "params": {"start": "2026-04-13T12:00:00"},
        "aggregate": {
            "fills": 10,
            "maker_fills": 10,
            "matched_qty": "0.5",
            "matched_net_pnl": "-1",
            "net_pnl": "-2",
            "residual_inventory_abs_avg": "0.01",
        },
    })

    assert row["window"] == "2026-04-13T12:00:00"
    assert row["matched_quantity"] == "0.5"
    assert row["full_strategy_net_pnl"] == "-2"
