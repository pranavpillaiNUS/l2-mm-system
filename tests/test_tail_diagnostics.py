"""
Tests for market-making tail diagnostics.
"""
import csv
from decimal import Decimal
from pathlib import Path
import tempfile

from src.analysis.tail_diagnostics import (
    DEFAULT_CLUSTER_GAPS_MS,
    build_tail_diagnostics,
    flag_tail_rows,
    nearest_session_boundary,
    summarize_tail_contributions,
    tail_count,
    cluster_tail_rows,
)
from scripts.analyze_mm_tail_diagnostics import _write_dict_csv


def test_tail_count_uses_nearest_integer_with_minimum_one():
    assert tail_count(921, Decimal("0.01")) == 9
    assert tail_count(696, Decimal("0.01")) == 7
    assert tail_count(696, Decimal("0.05")) == 35
    assert tail_count(0, Decimal("0.05")) == 0
    assert tail_count(3, Decimal("0.01")) == 1
    print("PASS: tail count matches small-N policy")


def test_tail_flags_use_worst_values_by_metric():
    rows = [
        {"row_id": "a", "metric": Decimal("-5"), "timestamp_ms": 1},
        {"row_id": "b", "metric": Decimal("2"), "timestamp_ms": 2},
        {"row_id": "c", "metric": Decimal("-3"), "timestamp_ms": 3},
        {"row_id": "d", "metric": Decimal("1"), "timestamp_ms": 4},
    ]

    flagged = flag_tail_rows(
        rows,
        "metric",
        scope_prefix="pooled",
        fractions=(Decimal("0.25"),),
        tie_break_keys=("timestamp_ms", "row_id"),
    )

    tail_rows = [row for row in flagged if row["is_pooled_tail_25pct"]]
    assert [row["row_id"] for row in tail_rows] == ["a"]
    assert tail_rows[0]["pooled_tail_rank"] == 1
    print("PASS: tail flags select the worst metric values")


def test_tail_contribution_math_reports_total_and_negative_shares():
    rows = [
        {"metric": Decimal("-10")},
        {"metric": Decimal("-5")},
        {"metric": Decimal("3")},
        {"metric": Decimal("2")},
    ]

    summary = summarize_tail_contributions(
        rows,
        "metric",
        fractions=(Decimal("0.25"), Decimal("0.50")),
    )
    by_fraction = {row["tail_fraction"]: row for row in summary}

    assert by_fraction["0.25"]["tail_n"] == 1
    assert by_fraction["0.25"]["tail_metric_sum"] == Decimal("-10")
    assert by_fraction["0.25"]["metric_sum"] == Decimal("-10")
    assert by_fraction["0.25"]["negative_metric_sum"] == Decimal("-15")
    assert by_fraction["0.25"]["tail_share_of_total_metric_sum"] == Decimal("1")
    assert by_fraction["0.25"]["tail_share_of_negative_metric_sum"] == (
        Decimal("-10") / Decimal("-15")
    )
    assert by_fraction["0.50"]["tail_metric_sum"] == Decimal("-15")
    print("PASS: tail contribution math separates total and adverse-only shares")


def test_cluster_boundaries_are_inclusive_for_all_default_gaps():
    for gap_ms in DEFAULT_CLUSTER_GAPS_MS:
        rows = [
            {"row_id": "a", "metric": Decimal("-1"), "timestamp_ms": 1_000},
            {
                "row_id": "b",
                "metric": Decimal("-2"),
                "timestamp_ms": 1_000 + gap_ms,
            },
            {
                "row_id": "c",
                "metric": Decimal("-3"),
                "timestamp_ms": 1_000 + gap_ms + gap_ms + 1,
            },
        ]

        clusters = cluster_tail_rows(
            rows,
            metric_key="metric",
            timestamp_key="timestamp_ms",
            source="fill",
            scope="pooled",
            cluster_gap_ms=gap_ms,
        )

        assert len(clusters) == 2
        assert clusters[0]["rows"] == 2
        assert clusters[0]["metric_sum"] == Decimal("-3")
        assert clusters[1]["rows"] == 1
    print("PASS: cluster gaps use <= at the threshold")


def test_nearest_session_boundary_uses_two_sided_utc_window():
    thirty_min_ms = 30 * 60 * 1000

    after_tokyo = nearest_session_boundary(thirty_min_ms, proximity_ms=thirty_min_ms)
    before_tokyo = nearest_session_boundary(
        (23 * 60 + 40) * 60 * 1000,
        proximity_ms=thirty_min_ms,
    )
    outside = nearest_session_boundary(3 * 60 * 60 * 1000, proximity_ms=thirty_min_ms)

    assert after_tokyo.boundary_name == "Tokyo"
    assert after_tokyo.signed_delta_ms == thirty_min_ms
    assert after_tokyo.within_proximity

    assert before_tokyo.boundary_name == "Tokyo"
    assert before_tokyo.signed_delta_ms == -20 * 60 * 1000
    assert before_tokyo.within_proximity

    assert outside.boundary_name == "Singapore/Hong Kong"
    assert outside.signed_delta_ms == 2 * 60 * 60 * 1000
    assert not outside.within_proximity
    print("PASS: boundary tagging is two-sided around UTC session marks")


def test_matched_lot_rows_keep_open_time_but_use_close_time_primary():
    rows = [
        {
            "row_id": "lot-1",
            "open_time_ms": 10_000,
            "close_time_ms": 70_000,
            "net_pnl_per_btc": Decimal("-1"),
        }
    ]
    flagged = flag_tail_rows(
        rows,
        "net_pnl_per_btc",
        scope_prefix="pooled",
        fractions=(Decimal("1.0"),),
        tie_break_keys=("close_time_ms", "row_id"),
    )

    assert flagged[0]["open_time_ms"] == 10_000
    assert flagged[0]["close_time_ms"] == 70_000
    assert flagged[0]["is_pooled_tail_100pct"]
    print("PASS: matched lots retain both timestamps for downstream tagging")


def test_cluster_share_columns_include_negative_and_tail_denominators():
    window_rows = [{
        "run_dir": "run",
        "window": "2026-04-14 12:00",
        "net_pnl": Decimal("1"),
        "matched_net_pnl": Decimal("1"),
        "residual_inventory_pnl": Decimal("0"),
        "fills": 2,
        "matched_lots": 2,
        "orders_submitted": 10,
    }]
    fill_rows = [
        {
            "window": "2026-04-14 12:00",
            "session": "2026-04-14 12:00",
            "row_id": "fill-bad",
            "primary_timestamp_ms": 1_000,
            "side_normalized_mid_move_bps": Decimal("-10"),
        },
        {
            "window": "2026-04-14 12:00",
            "session": "2026-04-14 12:00",
            "row_id": "fill-good",
            "primary_timestamp_ms": 2_000,
            "side_normalized_mid_move_bps": Decimal("20"),
        },
    ]
    matched_rows = [
        {
            "window": "2026-04-14 12:00",
            "session": "2026-04-14 12:00",
            "row_id": "lot-bad",
            "open_time_ms": 500,
            "close_time_ms": 1_000,
            "primary_timestamp_ms": 1_000,
            "net_pnl_per_btc": Decimal("-10"),
        },
        {
            "window": "2026-04-14 12:00",
            "session": "2026-04-14 12:00",
            "row_id": "lot-good",
            "open_time_ms": 1_500,
            "close_time_ms": 2_000,
            "primary_timestamp_ms": 2_000,
            "net_pnl_per_btc": Decimal("20"),
        },
    ]

    result = build_tail_diagnostics(
        fill_rows=fill_rows,
        matched_lot_rows=matched_rows,
        window_rows=window_rows,
        cluster_gaps_ms=(60_000,),
    )

    cluster = [
        row for row in result.cluster_rows
        if row["source"] == "matched_lot" and row["scope"] == "pooled"
    ][0]
    rollup = [
        row for row in result.cluster_summary_rows
        if row["source"] == "matched_lot" and row["scope"] == "pooled"
    ][0]

    assert cluster["cluster_share_of_total_metric_sum"] == Decimal("-1")
    assert cluster["cluster_share_of_negative_metric_sum"] == Decimal("1")
    assert cluster["cluster_share_of_tail_metric_sum"] == Decimal("1")
    assert rollup["worst_cluster_share_of_total_metric_sum"] == Decimal("-1")
    assert rollup["worst_cluster_share_of_negative_metric_sum"] == Decimal("1")
    assert rollup["worst_cluster_share_of_tail_metric_sum"] == Decimal("1")
    print("PASS: cluster shares expose total, negative, and tail denominators")


def test_cluster_summary_csv_preserves_negative_share_column():
    rows = [{
        "source": "matched_lot",
        "scope": "pooled",
        "worst_cluster_id": "cluster-1",
        "worst_cluster_share_of_total_metric_sum": Decimal("0.25"),
        "worst_cluster_share_of_negative_metric_sum": Decimal("0.50"),
        "worst_cluster_share_of_tail_metric_sum": Decimal("0.75"),
    }]

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cluster_summary.csv"
        _write_dict_csv(path, rows)
        with path.open("r", encoding="utf-8", newline="") as f:
            parsed = list(csv.DictReader(f))

    assert "worst_cluster_share_of_negative_metric_sum" in parsed[0]
    assert parsed[0]["worst_cluster_share_of_negative_metric_sum"] == "0.50"
    print("PASS: cluster summary CSV preserves negative-share column")
