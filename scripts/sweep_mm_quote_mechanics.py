"""
Mini-sweep quote mechanics for L2 market-making strategies.

This is intentionally narrower than a full parameter sweep. It varies only
quote width and requote cadence so the baseline passive MM behavior can be
calibrated before adding more strategy complexity.

Example:
    env PYTHONPATH=. python scripts/sweep_mm_quote_mechanics.py \
        --start 2026-04-16T12 \
        --end 2026-04-16T17
"""
import argparse
from copy import copy
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable, List, Tuple

from scripts.compare_mm import (
    _parse_start,
    _run_session,
    _session_windows,
    _sessions_from_end,
    _write_csv,
)
from src.analysis.fill_rate import FillRateBin, summarize_pooled_contexts
from src.execution.provenance import guard_event_driven_output_path
from src.execution.queue_credit import credit_from_legacy_mode, parse_queue_credit


@dataclass(frozen=True)
class SweepCombo:
    half_spread: str
    requote_interval_ms: int

    @property
    def label(self) -> str:
        return f"half_spread={self.half_spread}, requote={self.requote_interval_ms}ms"


def _combo_key(row: dict) -> Tuple[str, str, int, str]:
    return (
        row["strategy"],
        row["half_spread"],
        int(row["requote_interval_ms"]),
        str(row.get("queue_cancellation_credit", "1.0")),
    )


def _aggregate_sweep_rows(rows: Iterable[dict]) -> List[dict]:
    grouped: dict[Tuple[str, str, int, str], List[dict]] = {}
    for row in rows:
        grouped.setdefault(_combo_key(row), []).append(row)

    aggregates: List[dict] = []
    for (strategy, half_spread, requote_interval_ms, queue_credit), group_rows in grouped.items():
        total_fills = sum(row["fills"] for row in group_rows)
        maker_fills = sum(row["maker_fills"] for row in group_rows)
        total_notional = sum(
            (row["total_notional"] for row in group_rows),
            Decimal("0"),
        )
        total_markouts = sum(row["markout_count"] for row in group_rows)

        spread_capture = sum(
            (row["spread_capture"] for row in group_rows),
            Decimal("0"),
        )
        adverse_selection_cost = sum(
            (row["adverse_selection_cost"] for row in group_rows),
            Decimal("0"),
        )
        net_pnl = sum((row["net_pnl"] for row in group_rows), Decimal("0"))
        fees = sum((row["fees"] for row in group_rows), Decimal("0"))

        avg_markout_bps = _weighted_avg(
            ((row["avg_markout_bps"], row["markout_count"]) for row in group_rows)
        )
        # Approximation: weighted average of per-session medians. Exact pooled
        # median would require carrying all markout rows through the sweep.
        weighted_p50_markout_bps = _weighted_avg(
            ((row["p50_markout_bps"], row["markout_count"]) for row in group_rows)
        )

        aggregates.append({
            "execution_model_version": group_rows[0]["execution_model_version"],
            "equal_timestamp_policy": group_rows[0]["equal_timestamp_policy"],
            "snapshot_time_policy": group_rows[0]["snapshot_time_policy"],
            "trade_gap_policy": group_rows[0]["trade_gap_policy"],
            "entry_latency_ms": group_rows[0]["entry_latency_ms"],
            "entry_jitter_ms": group_rows[0]["entry_jitter_ms"],
            "cancel_latency_ms": group_rows[0]["cancel_latency_ms"],
            "cancel_jitter_ms": group_rows[0]["cancel_jitter_ms"],
            "latency_seed": group_rows[0]["latency_seed"],
            "post_only": group_rows[0]["post_only"],
            "strategy": strategy,
            "half_spread": half_spread,
            "requote_interval_ms": requote_interval_ms,
            "queue_cancellation_credit": queue_credit,
            "sessions": len(group_rows),
            "fills": total_fills,
            "maker_fills": maker_fills,
            "maker_pct": (
                Decimal(maker_fills) / Decimal(total_fills) * Decimal("100")
                if total_fills else Decimal("0")
            ),
            "net_pnl": net_pnl,
            "avg_session_net_pnl": net_pnl / Decimal(len(group_rows)),
            "fees": fees,
            "spread_capture": spread_capture,
            "spread_capture_bps": (
                spread_capture / total_notional * Decimal("10000")
                if total_notional else Decimal("0")
            ),
            "inventory_pnl": sum(
                (row["inventory_pnl"] for row in group_rows),
                Decimal("0"),
            ),
            "adverse_selection_horizon": group_rows[0]["adverse_selection_horizon"],
            "adverse_selection_cost": adverse_selection_cost,
            "adverse_selection_bps": (
                adverse_selection_cost / total_notional * Decimal("10000")
                if total_notional else Decimal("0")
            ),
            "avg_markout_bps": avg_markout_bps,
            "weighted_p50_markout_bps": weighted_p50_markout_bps,
            "ending_inventory_abs_avg": (
                sum((abs(row["ending_position"]) for row in group_rows), Decimal("0"))
                / Decimal(len(group_rows))
            ),
            "orders_per_fill": (
                Decimal(sum(row["orders_submitted"] for row in group_rows))
                / Decimal(total_fills)
                if total_fills else Decimal("0")
            ),
            "gaps_detected": sum(row["gaps_detected"] for row in group_rows),
        })

    return sorted(
        aggregates,
        key=lambda row: (
            Decimal(row["half_spread"]),
            int(row["requote_interval_ms"]),
            row["strategy"],
        ),
    )


def _weighted_avg(values_and_weights: Iterable[tuple[Decimal | None, int]]) -> Decimal | None:
    total_weight = 0
    total = Decimal("0")
    for value, weight in values_and_weights:
        if value is None or weight <= 0:
            continue
        total += value * Decimal(weight)
        total_weight += weight
    if total_weight == 0:
        return None
    return total / Decimal(total_weight)


def _aggregate_fill_rate_rows(detail_rows: List[dict]) -> List[dict]:
    """
    Pool OrderContexts by (strategy, half_spread, requote_interval) combo and
    compute fill-rate breakdowns (overall + by distance/age/volatility).
    Returns one flat row per (combo, axis, bin_label).
    """
    grouped: dict[Tuple[str, str, int, str], list] = {}
    provenance_by_key: dict[Tuple[str, str, int, str], dict] = {}
    for row in detail_rows:
        key = _combo_key(row)
        grouped.setdefault(key, []).extend(row.get("_contexts", []))
        provenance = {
            field: row[field]
            for field in (
                "execution_model_version",
                "equal_timestamp_policy",
                "snapshot_time_policy",
                "trade_gap_policy",
                "entry_latency_ms",
                "entry_jitter_ms",
                "cancel_latency_ms",
                "cancel_jitter_ms",
                "latency_seed",
                "post_only",
            )
        }
        previous = provenance_by_key.setdefault(key, provenance)
        if previous != provenance:
            raise ValueError(
                "quote-mechanics rows have incompatible execution provenance"
            )

    out: List[dict] = []
    for key, contexts in grouped.items():
        strategy, half_spread, requote_interval_ms, queue_credit = key
        provenance = provenance_by_key[key]
        summary = summarize_pooled_contexts(contexts)

        def _emit(axis: str, bin_obj: FillRateBin):
            out.append({
                **provenance,
                "strategy": strategy,
                "half_spread": half_spread,
                "requote_interval_ms": requote_interval_ms,
                "queue_cancellation_credit": queue_credit,
                "axis": axis,
                "bin": bin_obj.label,
                "n_orders": bin_obj.n_orders,
                "n_filled": bin_obj.n_filled,
                "fill_rate_pct": Decimal(str(round(bin_obj.fill_rate * 100, 4))),
                "avg_markout_bps_given_filled": bin_obj.avg_markout_bps_given_filled,
                "median_markout_bps_given_filled": bin_obj.median_markout_bps_given_filled,
            })

        _emit("overall", summary["overall"])
        for b in summary["by_distance_bps"]:
            _emit("distance_bps", b)
        for b in summary["by_quote_age_ms"]:
            _emit("quote_age_ms", b)
        for b in summary["by_volatility_bps"]:
            _emit("volatility_bps", b)

    return out


def _format_fill_rate_overview(fill_rate_rows: List[dict]) -> str:
    """One-line-per-combo: overall fill rate + markout-given-fill, sorted."""
    overall = [r for r in fill_rate_rows if r["axis"] == "overall"]
    overall.sort(key=lambda r: (r["strategy"], Decimal(r["half_spread"]),
                                int(r["requote_interval_ms"])))
    if not overall:
        return "No overall fill-rate rows."
    lines = [
        f"  {'strategy':<10} {'spread':>6} {'requote':>8} {'orders':>7} "
        f"{'filled':>6} {'rate%':>7} {'mkout|filled':>14}"
    ]
    for r in overall:
        markout = r["avg_markout_bps_given_filled"]
        markout_str = f"{float(markout):+.2f} bps" if markout is not None else "n/a"
        lines.append(
            f"  {r['strategy']:<10} {r['half_spread']:>6} "
            f"{r['requote_interval_ms']:>8} {r['n_orders']:>7} "
            f"{r['n_filled']:>6} {float(r['fill_rate_pct']):>6.2f}%  "
            f"{markout_str:>14}"
        )
    return "\n".join(lines)


def _format_ranked_table(rows: List[dict]) -> str:
    if not rows:
        return "No aggregate rows."

    ranked = sorted(rows, key=lambda row: row["net_pnl"], reverse=True)
    columns = [
        ("strategy", "Strategy", 10),
        ("half_spread", "HalfSpr", 8),
        ("requote_interval_ms", "Requote", 8),
        ("sessions", "Sess", 5),
        ("fills", "Fills", 8),
        ("maker_pct", "Maker%", 8),
        ("net_pnl", "Net PnL", 14),
        ("fees", "Fees", 12),
        ("avg_markout_bps", "Avg m/o", 10),
        ("weighted_p50_markout_bps", "p50* m/o", 10),
        ("adverse_selection_bps", "AdvSel", 10),
        ("orders_per_fill", "Ord/fill", 9),
        ("gaps_detected", "Gaps", 6),
    ]

    def cell(value, width: int) -> str:
        if isinstance(value, Decimal):
            value = f"{value:.4f}"
        elif value is None:
            value = "n/a"
        return str(value)[:width].rjust(width)

    header = " ".join(label.rjust(width) for _, label, width in columns)
    divider = " ".join("-" * width for _, _, width in columns)
    lines = [header, divider]
    for row in ranked:
        lines.append(" ".join(cell(row.get(key), width) for key, _, width in columns))
    lines.append("* p50 is a weighted average of per-session medians, not a pooled median.")
    return "\n".join(lines)


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Mini-sweep half-spread and requote interval for L2 MM"
    )
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--start", required=True, type=_parse_start,
                        help="First session start, e.g. 2026-04-16T12")
    parser.add_argument("--end", type=_parse_start,
                        help="Exclusive range end, e.g. 2026-04-16T17")
    parser.add_argument("--sessions", type=int,
                        help="Number of contiguous sessions; defaults to 5 without --end")
    parser.add_argument("--session-hours", type=int, default=1)
    parser.add_argument("--strategies", nargs="+",
                        choices=["symmetric", "microprice"],
                        default=["symmetric", "microprice"])
    parser.add_argument("--half-spreads", nargs="+",
                        default=["0.50", "1.00", "2.00", "5.00"])
    parser.add_argument("--requote-intervals-ms", nargs="+", type=int,
                        default=[0, 1000, 5000])
    parser.add_argument("--order-qty", default="0.001")
    parser.add_argument("--max-position", default="0.01")
    parser.add_argument("--tick-size", default="0.01")
    parser.add_argument("--latency-ms", type=int, default=10)
    parser.add_argument("--jitter-ms", type=int, default=0)
    parser.add_argument("--cancel-latency-ms", type=int)
    parser.add_argument("--cancel-jitter-ms", type=int)
    parser.add_argument("--maker-bps", type=int, default=2)
    parser.add_argument("--taker-bps", type=int, default=5)
    parser.add_argument("--queue-cancellation-credit", default="1.0",
                        help="Cancellation-driven queue credit in [0.0, 1.0]")
    parser.add_argument("--queue-cancellation-mode",
                        choices=["proportional", "none"],
                        help=argparse.SUPPRESS)
    parser.add_argument("--adverse-horizon", default="30s")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path(
                            "results/event_driven_v2/compare/quote_mechanics"
                        ))
    parser.add_argument("--skip-missing", action="store_true")
    args = parser.parse_args()
    if args.queue_cancellation_mode is not None:
        args.queue_cancellation_credit = credit_from_legacy_mode(
            args.queue_cancellation_mode
        )
    else:
        args.queue_cancellation_credit = parse_queue_credit(
            args.queue_cancellation_credit
        )
    if args.cancel_latency_ms is None:
        args.cancel_latency_ms = args.latency_ms
    if args.cancel_jitter_ms is None:
        args.cancel_jitter_ms = args.jitter_ms
    return args


def main():
    args = _parse_args()
    guard_event_driven_output_path(args.output_dir)
    if args.end is not None:
        args.sessions = _sessions_from_end(args.start, args.end, args.session_hours)
    elif args.sessions is None:
        args.sessions = 5

    windows = _session_windows(args.start, args.sessions, args.session_hours)
    combos = [
        SweepCombo(half_spread=half_spread, requote_interval_ms=requote_interval_ms)
        for half_spread in args.half_spreads
        for requote_interval_ms in args.requote_intervals_ms
    ]

    detail_rows: List[dict] = []
    total_runs = len(combos) * len(windows) * len(args.strategies)
    run_idx = 0

    for combo in combos:
        run_args = copy(args)
        run_args.half_spread = combo.half_spread
        run_args.requote_interval_ms = combo.requote_interval_ms

        for window in windows:
            for strategy_name in args.strategies:
                run_idx += 1
                print(
                    f"[{run_idx}/{total_runs}] {window.label} "
                    f"{strategy_name} {combo.label}"
                )
                try:
                    detail_rows.append(_run_session(strategy_name, window, run_args))
                except FileNotFoundError as exc:
                    if not args.skip_missing:
                        raise
                    print(f"  Skipping missing session: {exc}")

    aggregate_rows = _aggregate_sweep_rows(detail_rows)
    fill_rate_rows = _aggregate_fill_rate_rows(detail_rows)
    detail_path = args.output_dir / "quote_mechanics_detail.csv"
    aggregate_path = args.output_dir / "quote_mechanics_aggregate.csv"
    fill_rate_path = args.output_dir / "quote_mechanics_fill_rate.csv"
    _write_csv(detail_path, detail_rows)
    _write_csv(aggregate_path, aggregate_rows)
    _write_csv(fill_rate_path, fill_rate_rows)

    print()
    print("Aggregate quote-mechanics ranking")
    print(_format_ranked_table(aggregate_rows))
    print()
    print("Pooled fill-rate overview (per combo)")
    print(_format_fill_rate_overview(fill_rate_rows))
    print(f"\nWrote detail rows to {detail_path}")
    print(f"Wrote aggregate rows to {aggregate_path}")
    print(f"Wrote fill-rate rows to {fill_rate_path}")


if __name__ == "__main__":
    main()
