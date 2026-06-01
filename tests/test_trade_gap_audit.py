"""Tests for the published old/new trade-gap replay delta."""

from scripts.audit_trade_gap_replay_delta import _delta_row


def summary(*, fills, matched, residual, total, trade_gaps):
    return {
        "params": {"hours": 5},
        "aggregate": {
            "fills": fills,
            "matched_net_pnl": matched,
            "residual_inventory_pnl": residual,
            "net_pnl": total,
            "gaps_detected": trade_gaps,
            "depth_gaps_detected": 0,
            "trade_gaps_detected": trade_gaps,
        },
    }


def test_trade_gap_anchor_delta_reports_exact_zero_for_clean_anchor():
    row = _delta_row(
        "2026-04-13T12",
        summary(fills=1, matched="-1", residual="0", total="-1", trade_gaps=0),
        summary(fills=1, matched="-1", residual="0", total="-1", trade_gaps=0),
    )

    assert row["exact_zero_delta"]


def test_trade_gap_anchor_delta_surfaces_replay_change():
    row = _delta_row(
        "2026-04-13T12",
        summary(fills=1, matched="-1", residual="0", total="-1", trade_gaps=0),
        summary(fills=0, matched="0", residual="0", total="0", trade_gaps=1),
    )

    assert not row["exact_zero_delta"]
    assert row["delta_new_minus_old"]["fills"] == -1
