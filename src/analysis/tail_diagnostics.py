"""
Tail diagnostics for passive market-making failures.

This module is deliberately descriptive. It answers whether losses are broad
based or concentrated in adverse tails; it does not propose a trading filter.
"""
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable, Sequence

from src.analysis.bootstrap import mean_decimal, percentile


DEFAULT_TAIL_FRACTIONS = (Decimal("0.01"), Decimal("0.05"), Decimal("0.10"))
DEFAULT_CLUSTER_GAPS_MS = (60_000, 120_000, 300_000)
DEFAULT_PROXIMITY_MS = 30 * 60 * 1_000
DAY_MS = 24 * 60 * 60 * 1_000


@dataclass(frozen=True)
class SessionBoundary:
    name: str
    minute_of_day: int

    @property
    def hhmm(self) -> str:
        hour = self.minute_of_day // 60
        minute = self.minute_of_day % 60
        return f"{hour:02d}:{minute:02d}"


@dataclass(frozen=True)
class BoundaryTag:
    boundary_name: str
    boundary_hhmm_utc: str
    signed_delta_ms: int
    abs_delta_ms: int
    within_proximity: bool


@dataclass(frozen=True)
class TailDiagnosticsResult:
    summary: dict
    window_summary_rows: list[dict]
    fill_tail_rows: list[dict]
    matched_lot_tail_rows: list[dict]
    cluster_summary_rows: list[dict]
    cluster_rows: list[dict]


DEFAULT_SESSION_BOUNDARIES = (
    SessionBoundary("Tokyo", 0),
    SessionBoundary("Singapore/Hong Kong", 60),
    SessionBoundary("London", 7 * 60),
    SessionBoundary("US cash open", 13 * 60 + 30),
    SessionBoundary("US cash close", 20 * 60),
)


def tail_count(n: int, fraction: Decimal) -> int:
    """Return the nearest-integer tail count, with at least one row if n > 0."""
    if n <= 0:
        return 0
    count = (Decimal(n) * Decimal(fraction)).to_integral_value(
        rounding=ROUND_HALF_UP
    )
    return max(1, int(count))


def flag_tail_rows(
    rows: Sequence[dict],
    metric_key: str,
    *,
    scope_prefix: str,
    group_key: str | None = None,
    fractions: Sequence[Decimal] = DEFAULT_TAIL_FRACTIONS,
    tie_break_keys: Sequence[str] = (),
) -> list[dict]:
    """Return row copies with exact-count worst-tail flags added."""
    output = [dict(row) for row in rows]
    labels = [_fraction_label(fraction) for fraction in fractions]
    rank_key = f"{scope_prefix}_tail_rank"

    for row in output:
        row[rank_key] = ""
        for label in labels:
            row[f"is_{scope_prefix}_tail_{label}"] = False

    groups: dict[str, list[int]] = defaultdict(list)
    for idx, row in enumerate(output):
        if row.get(metric_key) is None:
            continue
        group = str(row.get(group_key, "pooled")) if group_key else "pooled"
        groups[group].append(idx)

    for indexes in groups.values():
        sorted_indexes = sorted(
            indexes,
            key=lambda idx: _row_sort_key(output[idx], metric_key, tie_break_keys),
        )
        for rank, idx in enumerate(sorted_indexes, start=1):
            output[idx][rank_key] = rank

        for fraction, label in zip(fractions, labels):
            cutoff = tail_count(len(sorted_indexes), fraction)
            flag_key = f"is_{scope_prefix}_tail_{label}"
            for idx in sorted_indexes[:cutoff]:
                output[idx][flag_key] = True

    return output


def summarize_distribution(rows: Sequence[dict], metric_key: str) -> dict:
    values = [row[metric_key] for row in rows if row.get(metric_key) is not None]
    return {
        "n": len(values),
        "mean": mean_decimal(values),
        "median": percentile(values, Decimal("0.50")),
        "p01": percentile(values, Decimal("0.01")),
        "p05": percentile(values, Decimal("0.05")),
        "p10": percentile(values, Decimal("0.10")),
        "p25": percentile(values, Decimal("0.25")),
        "p75": percentile(values, Decimal("0.75")),
        "p90": percentile(values, Decimal("0.90")),
        "p95": percentile(values, Decimal("0.95")),
        "p99": percentile(values, Decimal("0.99")),
        "metric_sum": sum(values, Decimal("0")),
        "negative_metric_sum": sum(
            (value for value in values if value < Decimal("0")),
            Decimal("0"),
        ),
    }


def summarize_tail_contributions(
    rows: Sequence[dict],
    metric_key: str,
    *,
    fractions: Sequence[Decimal] = DEFAULT_TAIL_FRACTIONS,
) -> list[dict]:
    values = [row[metric_key] for row in rows if row.get(metric_key) is not None]
    ordered = sorted(values)
    metric_sum = sum(values, Decimal("0"))
    negative_metric_sum = sum(
        (value for value in values if value < Decimal("0")),
        Decimal("0"),
    )

    summaries = []
    for fraction in fractions:
        n_tail = tail_count(len(ordered), fraction)
        tail_values = ordered[:n_tail]
        tail_sum = sum(tail_values, Decimal("0"))
        summaries.append({
            "tail_fraction": format(Decimal(fraction), "f"),
            "tail_label": _fraction_label(Decimal(fraction)),
            "tail_n": n_tail,
            "metric_sum": metric_sum,
            "negative_metric_sum": negative_metric_sum,
            "tail_metric_sum": tail_sum,
            "tail_share_of_total_metric_sum": (
                tail_sum / metric_sum if metric_sum != Decimal("0") else None
            ),
            "tail_share_of_negative_metric_sum": (
                tail_sum / negative_metric_sum
                if negative_metric_sum != Decimal("0") else None
            ),
            "small_n_note": (
                "illustrative_only" if Decimal(fraction) == Decimal("0.01") else ""
            ),
        })
    return summaries


def nearest_session_boundary(
    timestamp_ms: int,
    *,
    boundaries: Sequence[SessionBoundary] = DEFAULT_SESSION_BOUNDARIES,
    proximity_ms: int = DEFAULT_PROXIMITY_MS,
) -> BoundaryTag:
    """
    Return the nearest UTC session boundary using a two-sided proximity window.
    """
    day_start_ms = (timestamp_ms // DAY_MS) * DAY_MS
    candidates = []
    for day_offset in (-1, 0, 1):
        base_ms = day_start_ms + day_offset * DAY_MS
        for order, boundary in enumerate(boundaries):
            boundary_ms = base_ms + boundary.minute_of_day * 60 * 1_000
            signed_delta = timestamp_ms - boundary_ms
            # Tie-break toward the boundary already crossed, then stable order.
            candidates.append((
                abs(signed_delta),
                0 if signed_delta >= 0 else 1,
                order,
                boundary,
                signed_delta,
            ))

    abs_delta, _, _, boundary, signed_delta = min(candidates, key=lambda item: item[:3])
    return BoundaryTag(
        boundary_name=boundary.name,
        boundary_hhmm_utc=boundary.hhmm,
        signed_delta_ms=signed_delta,
        abs_delta_ms=abs_delta,
        within_proximity=abs_delta <= proximity_ms,
    )


def cluster_tail_rows(
    rows: Sequence[dict],
    *,
    metric_key: str,
    timestamp_key: str,
    source: str,
    scope: str,
    cluster_gap_ms: int,
    scope_value: str = "pooled",
) -> list[dict]:
    """Cluster already-selected tail rows with an inclusive gap rule."""
    ordered = sorted(
        rows,
        key=lambda row: (
            int(row[timestamp_key]),
            str(row.get("row_id", "")),
        ),
    )
    if not ordered:
        return []

    clusters: list[list[dict]] = [[ordered[0]]]
    for row in ordered[1:]:
        previous = clusters[-1][-1]
        gap_ms = int(row[timestamp_key]) - int(previous[timestamp_key])
        if gap_ms <= cluster_gap_ms:
            clusters[-1].append(row)
        else:
            clusters.append([row])

    output = []
    for idx, cluster in enumerate(clusters, start=1):
        start_ms = int(cluster[0][timestamp_key])
        end_ms = int(cluster[-1][timestamp_key])
        metric_sum = sum((row[metric_key] for row in cluster), Decimal("0"))
        negative_metric_sum = sum(
            (row[metric_key] for row in cluster if row[metric_key] < Decimal("0")),
            Decimal("0"),
        )
        boundary = nearest_session_boundary(start_ms)
        output.append({
            "source": source,
            "scope": scope,
            "scope_value": scope_value,
            "cluster_gap_ms": cluster_gap_ms,
            "cluster_id": f"{source}_{scope}_{_safe_label(scope_value)}_{cluster_gap_ms}_{idx}",
            "rows": len(cluster),
            "start_timestamp_ms": start_ms,
            "end_timestamp_ms": end_ms,
            "duration_ms": end_ms - start_ms,
            "metric_sum": metric_sum,
            "negative_metric_sum": negative_metric_sum,
            "min_metric": min(row[metric_key] for row in cluster),
            "max_metric": max(row[metric_key] for row in cluster),
            "first_row_id": cluster[0].get("row_id", ""),
            "last_row_id": cluster[-1].get("row_id", ""),
            "nearest_boundary": boundary.boundary_name,
            "nearest_boundary_hhmm_utc": boundary.boundary_hhmm_utc,
            "boundary_signed_delta_ms": boundary.signed_delta_ms,
            "boundary_abs_delta_ms": boundary.abs_delta_ms,
            "within_30m_boundary": boundary.within_proximity,
        })
    return output


def load_anchor_run_dirs(
    baseline_ci_path: Path,
    reconciliation_root: Path,
) -> list[Path]:
    with baseline_ci_path.open("r", encoding="utf-8") as f:
        report = json.load(f)
    return [reconciliation_root / run_dir for run_dir in report["run_dirs"]]


def load_window_summaries(run_dirs: Sequence[Path]) -> list[dict]:
    rows = []
    for run_dir in run_dirs:
        summary = _load_json(run_dir / "summary.json")
        params = summary["params"]
        aggregate = summary["aggregate"]
        rows.append({
            "run_dir": run_dir.name,
            "window": _window_label(params["start"]),
            "net_pnl": _d(aggregate["net_pnl"]),
            "matched_net_pnl": _d(aggregate["matched_net_pnl"]),
            "residual_inventory_pnl": _maybe_d(aggregate.get("residual_inventory_pnl")),
            "fills": int(aggregate["fills"]),
            "matched_lots": _matched_lot_count(run_dir),
            "orders_submitted": int(aggregate["orders_submitted"]),
        })
    return rows


def load_matched_lot_rows(run_dirs: Sequence[Path]) -> list[dict]:
    rows = []
    for run_dir in run_dirs:
        summary = _load_json(run_dir / "summary.json")
        window = _window_label(summary["params"]["start"])
        path = run_dir / "matched_lots.csv"
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=1):
                quantity = _d(row["quantity"])
                net_pnl = _d(row["net_pnl"])
                open_time_ms = int(row["open_time_ms"])
                close_time_ms = int(row["close_time_ms"])
                rows.append({
                    "source": "matched_lot",
                    "run_dir": run_dir.name,
                    "window": window,
                    "session": row["session"],
                    "row_id": (
                        f"{run_dir.name}:{row['session']}:"
                        f"{row['open_fill_id']}:{row['close_fill_id']}:{idx}"
                    ),
                    "open_fill_id": row["open_fill_id"],
                    "close_fill_id": row["close_fill_id"],
                    "open_side": row["open_side"],
                    "close_side": row["close_side"],
                    "open_time_ms": open_time_ms,
                    "close_time_ms": close_time_ms,
                    "primary_timestamp_ms": close_time_ms,
                    "hold_time_ms": int(row["hold_time_ms"]),
                    "quantity": quantity,
                    "net_pnl": net_pnl,
                    "realized_pnl": _d(row["realized_pnl"]),
                    "total_fees": _d(row["total_fees"]),
                    "inventory_pnl": _maybe_d(row.get("inventory_pnl")),
                    "net_pnl_per_btc": net_pnl / quantity if quantity else None,
                })
    return rows


def load_fill_toxicity_rows(path: Path, *, horizon: str = "30s") -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, start=1):
            if row["horizon"] != horizon:
                continue
            fill_timestamp_ms = int(row["fill_timestamp_ms"])
            rows.append({
                "source": "fill",
                "block_start": row["block_start"],
                "window": row["block_start"],
                "session": row["session_start"],
                "row_id": f"{row['block_start']}:{row['session_start']}:{row['fill_id']}:{idx}",
                "fill_id": row["fill_id"],
                "order_id": row["order_id"],
                "side": row["side"],
                "fill_timestamp_ms": fill_timestamp_ms,
                "primary_timestamp_ms": fill_timestamp_ms,
                "quantity": _d(row["quantity"]),
                "horizon": row["horizon"],
                "side_aligned_skew_bps": _d(row["side_aligned_skew_bps"]),
                "fill_edge_bps": _d(row["fill_edge_bps"]),
                "side_normalized_mid_move_bps": _d(row["side_normalized_mid_move_bps"]),
            })
    return rows


def build_tail_diagnostics(
    *,
    fill_rows: Sequence[dict],
    matched_lot_rows: Sequence[dict],
    window_rows: Sequence[dict],
    tail_fractions: Sequence[Decimal] = DEFAULT_TAIL_FRACTIONS,
    cluster_gaps_ms: Sequence[int] = DEFAULT_CLUSTER_GAPS_MS,
) -> TailDiagnosticsResult:
    fill_prepared = _prepare_fill_rows(fill_rows, tail_fractions)
    lot_prepared = _prepare_matched_lot_rows(matched_lot_rows, tail_fractions)

    cluster_rows = []
    cluster_rollups = []
    for source, rows, metric_key in (
        ("fill", fill_prepared, "side_normalized_mid_move_bps"),
        ("matched_lot", lot_prepared, "net_pnl_per_btc"),
    ):
        for gap_ms in cluster_gaps_ms:
            pooled_tail = [row for row in rows if row["is_pooled_tail_5pct"]]
            pooled_clusters = cluster_tail_rows(
                pooled_tail,
                metric_key=metric_key,
                timestamp_key="primary_timestamp_ms",
                source=source,
                scope="pooled",
                scope_value="pooled",
                cluster_gap_ms=gap_ms,
            )
            pooled_clusters = _with_cluster_share_columns(
                pooled_clusters,
                all_rows=rows,
                tail_rows=pooled_tail,
                metric_key=metric_key,
            )
            cluster_rows.extend(pooled_clusters)
            cluster_rollups.append(_cluster_rollup(
                source=source,
                scope="pooled",
                scope_value="pooled",
                cluster_gap_ms=gap_ms,
                clusters=pooled_clusters,
                all_rows=rows,
                tail_rows=pooled_tail,
                metric_key=metric_key,
            ))

            for window, window_group in _group_by(rows, "window").items():
                local_tail = [
                    row for row in window_group if row["is_window_tail_5pct"]
                ]
                window_clusters = cluster_tail_rows(
                    local_tail,
                    metric_key=metric_key,
                    timestamp_key="primary_timestamp_ms",
                    source=source,
                    scope="window",
                    scope_value=window,
                    cluster_gap_ms=gap_ms,
                )
                window_clusters = _with_cluster_share_columns(
                    window_clusters,
                    all_rows=window_group,
                    tail_rows=local_tail,
                    metric_key=metric_key,
                )
                cluster_rows.extend(window_clusters)
                cluster_rollups.append(_cluster_rollup(
                    source=source,
                    scope="window",
                    scope_value=window,
                    cluster_gap_ms=gap_ms,
                    clusters=window_clusters,
                    all_rows=window_group,
                    tail_rows=local_tail,
                    metric_key=metric_key,
                ))

    window_summary_rows = _build_window_summary_rows(
        window_rows=window_rows,
        fill_rows=fill_prepared,
        matched_lot_rows=lot_prepared,
        cluster_rollups=cluster_rollups,
    )

    summary = {
        "params": {
            "fill_metric": "side_normalized_mid_move_bps",
            "matched_lot_metric": "net_pnl_per_btc",
            "canonical_tail_fraction": "0.05",
            "tail_fractions": [format(fraction, "f") for fraction in tail_fractions],
            "cluster_gaps_ms": list(cluster_gaps_ms),
            "matched_lot_primary_timestamp": "close_time_ms",
            "boundary_proximity_ms": DEFAULT_PROXIMITY_MS,
            "boundary_proximity_rule": "two_sided_abs_delta_lte_30m",
            "session_boundaries": [
                {
                    "name": boundary.name,
                    "hhmm_utc": boundary.hhmm,
                }
                for boundary in DEFAULT_SESSION_BOUNDARIES
            ],
        },
        "counts": {
            "windows": len(window_rows),
            "fill_rows_30s": len(fill_prepared),
            "matched_lots": len(lot_prepared),
            "clusters": len(cluster_rows),
        },
        "pooled": {
            "fill": _pooled_summary(fill_prepared, "side_normalized_mid_move_bps"),
            "matched_lot": _pooled_summary(lot_prepared, "net_pnl_per_btc"),
        },
        "cluster_rollups": cluster_rollups,
        "windows": window_summary_rows,
    }

    return TailDiagnosticsResult(
        summary=summary,
        window_summary_rows=window_summary_rows,
        fill_tail_rows=fill_prepared,
        matched_lot_tail_rows=lot_prepared,
        cluster_summary_rows=cluster_rollups,
        cluster_rows=cluster_rows,
    )


def _prepare_fill_rows(rows: Sequence[dict], fractions: Sequence[Decimal]) -> list[dict]:
    tagged = [_add_boundary_tags(dict(row), "primary_timestamp_ms", "primary")
              for row in rows]
    tagged = flag_tail_rows(
        tagged,
        "side_normalized_mid_move_bps",
        scope_prefix="pooled",
        fractions=fractions,
        tie_break_keys=("primary_timestamp_ms", "row_id"),
    )
    return flag_tail_rows(
        tagged,
        "side_normalized_mid_move_bps",
        scope_prefix="window",
        group_key="window",
        fractions=fractions,
        tie_break_keys=("primary_timestamp_ms", "row_id"),
    )


def _prepare_matched_lot_rows(
    rows: Sequence[dict],
    fractions: Sequence[Decimal],
) -> list[dict]:
    tagged = []
    for row in rows:
        out = dict(row)
        out = _add_boundary_tags(out, "primary_timestamp_ms", "primary")
        out = _add_boundary_tags(out, "open_time_ms", "open")
        out = _add_boundary_tags(out, "close_time_ms", "close")
        tagged.append(out)

    tagged = flag_tail_rows(
        tagged,
        "net_pnl_per_btc",
        scope_prefix="pooled",
        fractions=fractions,
        tie_break_keys=("primary_timestamp_ms", "row_id"),
    )
    return flag_tail_rows(
        tagged,
        "net_pnl_per_btc",
        scope_prefix="window",
        group_key="window",
        fractions=fractions,
        tie_break_keys=("primary_timestamp_ms", "row_id"),
    )


def _pooled_summary(rows: Sequence[dict], metric_key: str) -> dict:
    return {
        "distribution": summarize_distribution(rows, metric_key),
        "tail_contributions": summarize_tail_contributions(rows, metric_key),
    }


def _build_window_summary_rows(
    *,
    window_rows: Sequence[dict],
    fill_rows: Sequence[dict],
    matched_lot_rows: Sequence[dict],
    cluster_rollups: Sequence[dict],
) -> list[dict]:
    fills_by_window = _group_by(fill_rows, "window")
    lots_by_window = _group_by(matched_lot_rows, "window")
    rollup_by_key = {
        (
            row["source"],
            row["scope"],
            row["scope_value"],
            int(row["cluster_gap_ms"]),
        ): row
        for row in cluster_rollups
    }

    rows = []
    for window in window_rows:
        label = window["window"]
        fill_group = fills_by_window.get(label, [])
        lot_group = lots_by_window.get(label, [])
        fill_dist = summarize_distribution(fill_group, "side_normalized_mid_move_bps")
        lot_dist = summarize_distribution(lot_group, "net_pnl_per_btc")
        fill_tails = _tail_by_label(
            summarize_tail_contributions(fill_group, "side_normalized_mid_move_bps")
        )
        lot_tails = _tail_by_label(
            summarize_tail_contributions(lot_group, "net_pnl_per_btc")
        )
        fill_cluster = rollup_by_key.get(("fill", "window", label, 120_000), {})
        lot_cluster = rollup_by_key.get(("matched_lot", "window", label, 120_000), {})

        rows.append({
            "window": label,
            "run_dir": window["run_dir"],
            "net_pnl": window["net_pnl"],
            "matched_net_pnl": window["matched_net_pnl"],
            "residual_inventory_pnl": window["residual_inventory_pnl"],
            "loss_source_label": _loss_source_label(window),
            "fills": window["fills"],
            "matched_lots": window["matched_lots"],
            "fill_metric_mean": fill_dist["mean"],
            "fill_metric_median": fill_dist["median"],
            "fill_tail_5pct_n": _tail_value(fill_tails, "5pct", "tail_n"),
            "fill_tail_5pct_metric_sum": _tail_value(
                fill_tails, "5pct", "tail_metric_sum"
            ),
            "fill_tail_5pct_share_total": _tail_value(
                fill_tails, "5pct", "tail_share_of_total_metric_sum"
            ),
            "fill_tail_10pct_n": _tail_value(fill_tails, "10pct", "tail_n"),
            "fill_tail_10pct_metric_sum": _tail_value(
                fill_tails, "10pct", "tail_metric_sum"
            ),
            "fill_pooled_tail_5pct_rows": sum(
                1 for row in fill_group if row["is_pooled_tail_5pct"]
            ),
            "fill_tail_5pct_clusters_120s": fill_cluster.get("cluster_count", 0),
            "fill_tail_5pct_largest_cluster_rows_120s": fill_cluster.get(
                "largest_cluster_rows", 0
            ),
            "fill_tail_5pct_worst_cluster_share_total_120s": fill_cluster.get(
                "worst_cluster_share_of_total_metric_sum"
            ),
            "fill_tail_5pct_worst_cluster_share_negative_120s": fill_cluster.get(
                "worst_cluster_share_of_negative_metric_sum"
            ),
            "fill_tail_5pct_worst_cluster_share_tail_120s": fill_cluster.get(
                "worst_cluster_share_of_tail_metric_sum"
            ),
            "matched_metric_mean": lot_dist["mean"],
            "matched_metric_median": lot_dist["median"],
            "matched_tail_5pct_n": _tail_value(lot_tails, "5pct", "tail_n"),
            "matched_tail_5pct_metric_sum": _tail_value(
                lot_tails, "5pct", "tail_metric_sum"
            ),
            "matched_tail_5pct_share_total": _tail_value(
                lot_tails, "5pct", "tail_share_of_total_metric_sum"
            ),
            "matched_tail_10pct_n": _tail_value(lot_tails, "10pct", "tail_n"),
            "matched_tail_10pct_metric_sum": _tail_value(
                lot_tails, "10pct", "tail_metric_sum"
            ),
            "matched_pooled_tail_5pct_rows": sum(
                1 for row in lot_group if row["is_pooled_tail_5pct"]
            ),
            "matched_tail_5pct_clusters_120s": lot_cluster.get("cluster_count", 0),
            "matched_tail_5pct_largest_cluster_rows_120s": lot_cluster.get(
                "largest_cluster_rows", 0
            ),
            "matched_tail_5pct_worst_cluster_share_total_120s": lot_cluster.get(
                "worst_cluster_share_of_total_metric_sum"
            ),
            "matched_tail_5pct_worst_cluster_share_negative_120s": lot_cluster.get(
                "worst_cluster_share_of_negative_metric_sum"
            ),
            "matched_tail_5pct_worst_cluster_share_tail_120s": lot_cluster.get(
                "worst_cluster_share_of_tail_metric_sum"
            ),
        })
    return rows


def _with_cluster_share_columns(
    clusters: Sequence[dict],
    *,
    all_rows: Sequence[dict],
    tail_rows: Sequence[dict],
    metric_key: str,
) -> list[dict]:
    total_sum = sum((row[metric_key] for row in all_rows), Decimal("0"))
    negative_sum = sum(
        (row[metric_key] for row in all_rows if row[metric_key] < Decimal("0")),
        Decimal("0"),
    )
    tail_sum = sum((row[metric_key] for row in tail_rows), Decimal("0"))

    output = []
    for cluster in clusters:
        row = dict(cluster)
        metric_sum = row["metric_sum"]
        row["cluster_share_of_total_metric_sum"] = (
            metric_sum / total_sum if total_sum != Decimal("0") else None
        )
        row["cluster_share_of_negative_metric_sum"] = (
            metric_sum / negative_sum if negative_sum != Decimal("0") else None
        )
        row["cluster_share_of_tail_metric_sum"] = (
            metric_sum / tail_sum if tail_sum != Decimal("0") else None
        )
        output.append(row)
    return output


def _cluster_rollup(
    *,
    source: str,
    scope: str,
    scope_value: str,
    cluster_gap_ms: int,
    clusters: Sequence[dict],
    all_rows: Sequence[dict],
    tail_rows: Sequence[dict],
    metric_key: str,
) -> dict:
    total_sum = sum((row[metric_key] for row in all_rows), Decimal("0"))
    negative_sum = sum(
        (row[metric_key] for row in all_rows if row[metric_key] < Decimal("0")),
        Decimal("0"),
    )
    tail_sum = sum((row[metric_key] for row in tail_rows), Decimal("0"))
    worst_cluster = min(
        clusters,
        key=lambda row: row["metric_sum"],
        default=None,
    )
    largest_cluster = max(
        clusters,
        key=lambda row: row["rows"],
        default=None,
    )
    worst_sum = worst_cluster["metric_sum"] if worst_cluster else None

    return {
        "source": source,
        "scope": scope,
        "scope_value": scope_value,
        "cluster_gap_ms": cluster_gap_ms,
        "cluster_count": len(clusters),
        "tail_rows": len(tail_rows),
        "largest_cluster_rows": largest_cluster["rows"] if largest_cluster else 0,
        "worst_cluster_id": worst_cluster["cluster_id"] if worst_cluster else "",
        "worst_cluster_metric_sum": worst_sum,
        "worst_cluster_share_of_total_metric_sum": (
            worst_sum / total_sum
            if worst_sum is not None and total_sum != Decimal("0") else None
        ),
        "worst_cluster_share_of_negative_metric_sum": (
            worst_sum / negative_sum
            if worst_sum is not None and negative_sum != Decimal("0") else None
        ),
        "worst_cluster_share_of_tail_metric_sum": (
            worst_sum / tail_sum
            if worst_sum is not None and tail_sum != Decimal("0") else None
        ),
    }


def _add_boundary_tags(row: dict, timestamp_key: str, prefix: str) -> dict:
    tag = nearest_session_boundary(int(row[timestamp_key]))
    row[f"{prefix}_nearest_boundary"] = tag.boundary_name
    row[f"{prefix}_nearest_boundary_hhmm_utc"] = tag.boundary_hhmm_utc
    row[f"{prefix}_boundary_signed_delta_ms"] = tag.signed_delta_ms
    row[f"{prefix}_boundary_abs_delta_ms"] = tag.abs_delta_ms
    row[f"{prefix}_within_30m_boundary"] = tag.within_proximity
    return row


def _tail_by_label(rows: Sequence[dict]) -> dict[str, dict]:
    return {row["tail_label"]: row for row in rows}


def _tail_value(rows_by_label: dict[str, dict], label: str, key: str):
    row = rows_by_label.get(label)
    if row is None:
        return None
    return row.get(key)


def _loss_source_label(window: dict) -> str:
    net = window["net_pnl"]
    matched = window["matched_net_pnl"]
    residual = window["residual_inventory_pnl"]
    if net >= Decimal("0"):
        return "not_loss_window"
    if residual is not None and residual < Decimal("0") and matched < Decimal("0"):
        return "matched_and_residual_loss"
    if residual is not None and residual < Decimal("0"):
        return "residual_inventory_loss"
    if matched < Decimal("0"):
        return "matched_loss"
    return "other_loss"


def _group_by(rows: Sequence[dict], key: str) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row[key])].append(row)
    return dict(groups)


def _row_sort_key(row: dict, metric_key: str, tie_break_keys: Sequence[str]):
    return (
        row[metric_key],
        *[row.get(key, "") for key in tie_break_keys],
    )


def _fraction_label(fraction: Decimal) -> str:
    pct = Decimal(fraction) * Decimal("100")
    text = format(pct, "f").rstrip("0").rstrip(".")
    return f"{text.replace('.', 'p')}pct"


def _safe_label(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value)).strip("_")


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _matched_lot_count(run_dir: Path) -> int:
    path = run_dir / "matched_lots.csv"
    with path.open("r", encoding="utf-8", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def _window_label(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    return parsed.strftime("%Y-%m-%d %H:00")


def _d(value) -> Decimal:
    if value is None or value == "":
        raise ValueError("empty value cannot be converted to Decimal")
    return Decimal(str(value))


def _maybe_d(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return _d(value)
