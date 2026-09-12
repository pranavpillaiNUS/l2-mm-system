"""
Analyze whether fixed-horizon markouts reconcile with realized MM PnL.

Default run:
    env PYTHONPATH=. python scripts/analyze_markout_reconciliation.py
"""
import argparse
import csv
import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

from scripts.compare_mm import SessionWindow, _build_strategy, _parse_start, _select_files
from src.analysis.hold_time import (
    BookSpreadBucket,
    MatchedLot,
    OpenInventoryLot,
    PreFillDrift,
    QueueOrderDiagnostic,
    compute_hold_time_summary,
    compute_pre_fill_drifts,
    compute_reconciliation_summary,
    extract_queue_diagnostics,
    summarize_book_spread_by_volatility,
)
from src.analysis.markout import compute_markouts
from src.analysis.pnl import compute_pnl_decomposition
from src.execution.provenance import guard_event_driven_output_path
from src.execution.queue_credit import (
    credit_from_legacy_mode,
    parse_queue_credit,
    queue_credit_suffix,
)
from src.execution.simulator import SimConfig
from src.replay.engine import ReplayConfig, ReplayEngine
from src.replay.book_backend import (
    add_book_arguments, book_kwargs, book_provenance_from_args, separate_backend_output,
)


RECON_HORIZONS_MS = {
    "1s": 1_000,
    "5s": 5_000,
    "10s": 10_000,
    "30s": 30_000,
}


@dataclass
class AnalyzedSession:
    window: SessionWindow
    final_mid: Decimal | None
    strategy_position: Decimal
    fills: int
    maker_fills: int
    taker_fills: int
    postonly_rejects: int
    orders_submitted: int
    replay_stats: object
    decomp: object
    hold: object
    recon: object
    drift_rows: list[PreFillDrift]
    queue_rows: list[QueueOrderDiagnostic]
    spread_buckets: list[BookSpreadBucket]
    drift_summary: dict
    queue_summary: dict
    execution_provenance: dict


def _decimal_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    return str(value)


def _decimal_json(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            field.name: _jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            field.name: _jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    return value


def _write_dataclass_csv(path: Path, rows: Sequence[object], cls) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [field.name for field in fields(cls)]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                name: _decimal_str(getattr(row, name))
                for name in fieldnames
            })


def _write_session_dataclass_csv(path: Path, sessions: Sequence[AnalyzedSession],
                                 attr: str, cls) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields_without_session = [field.name for field in fields(cls)]
    fieldnames = ["session"] + fields_without_session
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for session in sessions:
            for row in _resolve_attr(session, attr):
                out = {"session": session.window.label}
                out.update({
                    name: _decimal_str(getattr(row, name))
                    for name in fields_without_session
                })
                writer.writerow(out)


def _resolve_attr(obj, path: str):
    value = obj
    for part in path.split("."):
        value = getattr(value, part)
    return value


def _mean(values: Iterable[Decimal | None]) -> Decimal | None:
    vals = [value for value in values if value is not None]
    if not vals:
        return None
    return sum(vals, Decimal("0")) / Decimal(len(vals))


def _pct(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _format_ms(value) -> str:
    if value is None:
        return "n/a"
    return f"{int(value):,}ms"


def _windows_from_args(args) -> list[SessionWindow]:
    start = _parse_start(args.start)
    if args.end:
        end = _parse_start(args.end)
        if end <= start:
            raise ValueError("--end must be after --start")
        total_seconds = int((end - start).total_seconds())
        seconds_per_session = args.session_hours * 60 * 60
        if total_seconds % seconds_per_session != 0:
            raise ValueError("--end must align to --session-hours")
        return [
            SessionWindow(
                start=start + timedelta(hours=i * args.session_hours),
                hours=args.session_hours,
            )
            for i in range(total_seconds // seconds_per_session)
        ]
    return [SessionWindow(start=start, hours=args.hours)]


def _run_replay(args, window: SessionWindow):
    depth_files, trade_files = _select_files(args.data_root, args.symbol, window)
    strategy = _build_strategy(args.strategy, args)

    config = ReplayConfig(
        **book_kwargs(args),
        depth_files=depth_files,
        trade_files=trade_files,
        sim_config=SimConfig(
            base_latency_ms=args.latency_ms,
            jitter_ms=args.jitter_ms,
            cancel_latency_ms=args.cancel_latency_ms,
            cancel_jitter_ms=args.cancel_jitter_ms,
            maker_bps=args.maker_bps,
            taker_bps=args.taker_bps,
            queue_cancellation_credit=args.queue_cancellation_credit,
        ),
        record_book_samples=True,
        trade_gap_policy=args.trade_gap_policy,
    )
    engine = ReplayEngine(config)
    result = engine.run(strategy)
    return engine, strategy, result


def _pre_fill_summary(rows: Sequence[PreFillDrift]) -> dict:
    return {
        "avg_quoted_distance_bps": _mean(row.quoted_distance_bps for row in rows),
        "avg_fill_edge_bps": _mean(row.fill_edge_bps for row in rows),
        "avg_pre_fill_mid_move_bps": _mean(row.pre_fill_mid_move_bps for row in rows),
        "avg_book_spread_at_fill": _mean(row.book_spread_at_fill for row in rows),
        "avg_book_spread_bps_at_fill": _mean(row.book_spread_bps_at_fill for row in rows),
    }


def _queue_summary(rows: Sequence[QueueOrderDiagnostic]) -> dict:
    filled = [row for row in rows if row.first_fill_time_ms is not None]
    return {
        "orders": len(rows),
        "filled_orders": len(filled),
        "avg_initial_queue_ahead_filled": _mean(row.initial_queue_ahead for row in filled),
        "avg_queue_before_trade_at_first_fill": _mean(
            row.first_queue_ahead_before_trade for row in filled
        ),
        "avg_queue_before_fill_at_first_fill": _mean(
            row.first_queue_ahead_before_fill for row in filled
        ),
        "total_trade_queue_drained": sum(
            (row.total_trade_queue_drained for row in rows),
            Decimal("0"),
        ),
        "total_cancel_queue_drained": sum(
            (row.total_cancel_queue_drained for row in rows),
            Decimal("0"),
        ),
    }


def _print_summary(args, window, result, decomp, hold, recon, drift_summary, queue_summary):
    print("Markout reconciliation")
    print(f"  Window:      {window.label} for {window.hours}h")
    print(f"  Strategy:    {args.strategy}")
    print(f"  Half spread: {args.half_spread}")
    print(f"  Requote:     {args.requote_interval_ms}ms")
    print(f"  Queue credit:{args.queue_cancellation_credit}")
    print(f"  Fills:       {len(result.fills)}")
    print()

    print("Inventory hold time")
    print(f"  Matched qty: {hold.total_matched_qty}")
    print(f"  Residual inv:{hold.residual_inventory}")
    print(
        "  Hold p25/p50/p75/p90/max: "
        f"{_format_ms(hold.p25_hold_time_ms)} / "
        f"{_format_ms(hold.p50_hold_time_ms)} / "
        f"{_format_ms(hold.p75_hold_time_ms)} / "
        f"{_format_ms(hold.p90_hold_time_ms)} / "
        f"{_format_ms(hold.max_hold_time_ms)}"
    )
    print()

    print("PnL reconciliation")
    print(f"  Reported net PnL:      {decomp.net_pnl}")
    print(f"  Component net PnL:     {recon.actual_net_from_components}")
    print(f"  Actual inventory PnL:  {recon.actual_inventory_pnl}")
    print(f"  Matched gross PnL:     {recon.matched_realized_pnl}")
    print(f"  Matched fees:          {recon.matched_fees}")
    print(f"  Matched net PnL:       {recon.matched_net_pnl}")
    print(f"  Closest hold horizon:  {recon.closest_horizon_to_median}")
    print(f"  Best PnL horizon:      {recon.best_reconciling_horizon}")
    print("  Horizon proxy:")
    for row in recon.horizons:
        print(
            f"    {row.horizon:<4} proxy_inv={row.proxy_inventory_pnl:>12} "
            f"proxy_net={row.proxy_net_pnl:>12} "
            f"error={row.net_error:>12} "
            f"adv_cost={row.adverse_selection_cost:>12}"
        )
    print()

    print("Pre-fill drift")
    print(f"  Avg quoted distance: {_pct(drift_summary['avg_quoted_distance_bps'])} bps")
    print(f"  Avg edge at fill:    {_pct(drift_summary['avg_fill_edge_bps'])} bps")
    print(f"  Avg pre-fill move:   {_pct(drift_summary['avg_pre_fill_mid_move_bps'])} bps")
    print()

    print("Queue summary")
    print(f"  Orders:              {queue_summary['orders']}")
    print(f"  Filled orders:       {queue_summary['filled_orders']}")
    print(f"  Avg init queue fill: {queue_summary['avg_initial_queue_ahead_filled']}")
    print(f"  Avg queue before trade at fill: "
          f"{queue_summary['avg_queue_before_trade_at_first_fill']}")
    print(f"  Total trade drain:   {queue_summary['total_trade_queue_drained']}")
    print(f"  Total cancel drain:  {queue_summary['total_cancel_queue_drained']}")


def _weighted_hold_percentile(lots, percentile: Decimal):
    total_qty = sum((lot.quantity for lot in lots), Decimal("0"))
    if total_qty <= Decimal("0"):
        return None
    target = total_qty * percentile
    cumulative = Decimal("0")
    for lot in sorted(lots, key=lambda row: row.hold_time_ms):
        cumulative += lot.quantity
        if cumulative >= target:
            return lot.hold_time_ms
    return None


def _weighted_hold_average(lots):
    total_qty = sum((lot.quantity for lot in lots), Decimal("0"))
    if total_qty <= Decimal("0"):
        return None
    return sum(
        (Decimal(lot.hold_time_ms) * lot.quantity for lot in lots),
        Decimal("0"),
    ) / total_qty


def _aggregate_horizons(sessions: Sequence[AnalyzedSession]) -> list[dict]:
    rows = []
    for horizon, horizon_ms in RECON_HORIZONS_MS.items():
        horizon_rows = [
            row
            for session in sessions
            for row in session.recon.horizons
            if row.horizon == horizon
        ]
        rows.append({
            "horizon": horizon,
            "horizon_ms": horizon_ms,
            "covered_qty": sum((row.covered_qty for row in horizon_rows), Decimal("0")),
            "proxy_inventory_pnl": sum(
                (row.proxy_inventory_pnl for row in horizon_rows), Decimal("0")
            ),
            "proxy_net_pnl": sum((row.proxy_net_pnl for row in horizon_rows), Decimal("0")),
            "net_error": sum((row.net_error for row in horizon_rows), Decimal("0")),
            "adverse_selection_cost": sum(
                (row.adverse_selection_cost for row in horizon_rows), Decimal("0")
            ),
        })
    return rows


def _aggregate_summary(args, sessions: Sequence[AnalyzedSession]) -> dict:
    all_lots = [
        lot for session in sessions for lot in session.hold.matched_lots
    ]
    matched_inventory_values = [
        session.hold.matched_inventory_pnl
        for session in sessions
        if session.hold.matched_inventory_pnl is not None
    ]
    matched_inventory_pnl = (
        sum(matched_inventory_values, Decimal("0"))
        if len(matched_inventory_values) == len(sessions) else None
    )
    matched_fees = sum(
        (session.hold.matched_fees for session in sessions), Decimal("0")
    )
    matched_net_pnl = sum(
        (session.hold.matched_net_pnl for session in sessions), Decimal("0")
    )
    inventory_pnl = sum(
        (session.decomp.inventory_pnl for session in sessions), Decimal("0")
    )
    horizons = _aggregate_horizons(sessions)
    p50 = _weighted_hold_percentile(all_lots, Decimal("0.50"))
    closest = None
    if p50 is not None:
        closest = min(RECON_HORIZONS_MS, key=lambda h: abs(RECON_HORIZONS_MS[h] - p50))

    best = None
    if horizons:
        best = min(horizons, key=lambda row: abs(row["net_error"]))["horizon"]

    total_fills = sum(session.fills for session in sessions)
    total_maker_fills = sum(session.maker_fills for session in sessions)
    total_orders = sum(session.orders_submitted for session in sessions)

    return {
        "sessions": len(sessions),
        "fills": total_fills,
        "maker_fills": total_maker_fills,
        "taker_fills": sum(session.taker_fills for session in sessions),
        "maker_pct": (
            Decimal(total_maker_fills) / Decimal(total_fills) * Decimal("100")
            if total_fills else Decimal("0")
        ),
        "postonly_rejects": sum(session.postonly_rejects for session in sessions),
        "orders_submitted": total_orders,
        "order_arrivals": sum(
            session.replay_stats.order_arrivals for session in sessions
        ),
        "cancel_requests": sum(
            session.replay_stats.cancel_requests for session in sessions
        ),
        "effective_cancels": sum(
            session.replay_stats.orders_cancelled for session in sessions
        ),
        "cancels_too_late": sum(
            session.replay_stats.cancels_too_late for session in sessions
        ),
        "gap_invalidations": sum(
            session.replay_stats.gap_invalidations for session in sessions
        ),
        "replay_end_invalidations": sum(
            session.replay_stats.replay_end_invalidations for session in sessions
        ),
        "pending_actions_at_end": sum(
            session.replay_stats.pending_actions_at_end for session in sessions
        ),
        "pending_cancels_at_end": sum(
            session.replay_stats.pending_cancels_at_end for session in sessions
        ),
        "gaps_detected": sum(session.replay_stats.gaps_detected for session in sessions),
        "depth_gaps_detected": sum(
            session.replay_stats.depth_gaps_detected for session in sessions
        ),
        "trade_gaps_detected": sum(
            session.replay_stats.trade_gaps_detected for session in sessions
        ),
        "events_during_gap": sum(
            session.replay_stats.events_during_gap for session in sessions
        ),
        "orders_per_fill": (
            Decimal(total_orders) / Decimal(total_fills)
            if total_fills else None
        ),
        "spread_capture": sum(
            (session.decomp.spread_capture for session in sessions), Decimal("0")
        ),
        "fees": sum((session.decomp.total_fees for session in sessions), Decimal("0")),
        "inventory_pnl": inventory_pnl,
        "matched_inventory_pnl": matched_inventory_pnl,
        "residual_inventory_pnl": (
            inventory_pnl - matched_inventory_pnl
            if matched_inventory_pnl is not None else None
        ),
        "matched_fees": matched_fees,
        "matched_net_pnl": matched_net_pnl,
        "matched_realized_pnl": sum(
            (session.hold.realized_pnl for session in sessions), Decimal("0")
        ),
        "net_pnl": sum((session.decomp.net_pnl for session in sessions), Decimal("0")),
        "matched_qty": sum(
            (session.hold.total_matched_qty for session in sessions), Decimal("0")
        ),
        "residual_inventory_abs_avg": (
            sum((abs(session.hold.residual_inventory) for session in sessions), Decimal("0"))
            / Decimal(len(sessions))
            if sessions else Decimal("0")
        ),
        "avg_hold_time_ms": _weighted_hold_average(all_lots),
        "p25_hold_time_ms": _weighted_hold_percentile(all_lots, Decimal("0.25")),
        "p50_hold_time_ms": p50,
        "p75_hold_time_ms": _weighted_hold_percentile(all_lots, Decimal("0.75")),
        "p90_hold_time_ms": _weighted_hold_percentile(all_lots, Decimal("0.90")),
        "max_hold_time_ms": max((lot.hold_time_ms for lot in all_lots), default=None),
        "closest_horizon_to_median": closest,
        "best_reconciling_horizon": best,
        "horizons": horizons,
        "pre_fill_drift": _pre_fill_summary(
            [row for session in sessions for row in session.drift_rows]
        ),
        "queue": _queue_summary(
            [row for session in sessions for row in session.queue_rows]
        ),
    }


def _print_aggregate_summary(args, sessions: Sequence[AnalyzedSession], aggregate: dict):
    start = sessions[0].window.start if sessions else _parse_start(args.start)
    print("Markout reconciliation")
    print(f"  Start:       {start.strftime('%Y-%m-%d %H:00')}")
    print(f"  Sessions:    {aggregate['sessions']} x {args.session_hours}h")
    print(f"  Strategy:    {args.strategy}")
    print(f"  Half spread: {args.half_spread}")
    print(f"  Requote:     {args.requote_interval_ms}ms")
    print(f"  Queue credit:{args.queue_cancellation_credit}")
    print(f"  Fills:       {aggregate['fills']}")
    print(f"  Maker %:     {_pct(aggregate['maker_pct'])}%")
    print(f"  Orders/fill: {_pct(aggregate['orders_per_fill'])}")
    print(f"  Post-only rejects: {aggregate['postonly_rejects']}")
    print()

    print("Inventory hold time")
    print(f"  Matched qty: {aggregate['matched_qty']}")
    print(f"  Avg abs residual inv/session: {aggregate['residual_inventory_abs_avg']}")
    print(
        "  Hold p25/p50/p75/p90/max: "
        f"{_format_ms(aggregate['p25_hold_time_ms'])} / "
        f"{_format_ms(aggregate['p50_hold_time_ms'])} / "
        f"{_format_ms(aggregate['p75_hold_time_ms'])} / "
        f"{_format_ms(aggregate['p90_hold_time_ms'])} / "
        f"{_format_ms(aggregate['max_hold_time_ms'])}"
    )
    print()

    print("PnL reconciliation")
    print(f"  Net PnL:              {aggregate['net_pnl']}")
    print(f"  Spread capture:       {aggregate['spread_capture']}")
    print(f"  Fees:                 {aggregate['fees']}")
    print(f"  Actual inventory PnL: {aggregate['inventory_pnl']}")
    print(f"  Matched inventory PnL:{aggregate['matched_inventory_pnl']}")
    print(f"  Residual inv PnL:     {aggregate['residual_inventory_pnl']}")
    print(f"  Matched gross PnL:    {aggregate['matched_realized_pnl']}")
    print(f"  Matched fees:         {aggregate['matched_fees']}")
    print(f"  Matched net PnL:      {aggregate['matched_net_pnl']}")
    print(f"  Closest hold horizon: {aggregate['closest_horizon_to_median']}")
    print(f"  Best PnL horizon:     {aggregate['best_reconciling_horizon']}")
    print("  Horizon proxy:")
    for row in aggregate["horizons"]:
        print(
            f"    {row['horizon']:<4} proxy_inv={row['proxy_inventory_pnl']:>12} "
            f"proxy_net={row['proxy_net_pnl']:>12} "
            f"error={row['net_error']:>12} "
            f"adv_cost={row['adverse_selection_cost']:>12}"
        )
    print()

    drift = aggregate["pre_fill_drift"]
    print("Pre-fill drift")
    print(f"  Avg quoted distance: {_pct(drift['avg_quoted_distance_bps'])} bps")
    print(f"  Avg edge at fill:    {_pct(drift['avg_fill_edge_bps'])} bps")
    print(f"  Avg pre-fill move:   {_pct(drift['avg_pre_fill_mid_move_bps'])} bps")
    print()

    queue = aggregate["queue"]
    print("Queue summary")
    print(f"  Orders:              {queue['orders']}")
    print(f"  Filled orders:       {queue['filled_orders']}")
    print(f"  Avg init queue fill: {queue['avg_initial_queue_ahead_filled']}")
    print(f"  Avg queue before trade at fill: "
          f"{queue['avg_queue_before_trade_at_first_fill']}")
    print(f"  Total trade drain:   {queue['total_trade_queue_drained']}")
    print(f"  Total cancel drain:  {queue['total_cancel_queue_drained']}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyze markout horizons against realized inventory turnover"
    )
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--strategy", choices=["symmetric", "microprice", "ofi_gated"],
                        default="microprice")
    parser.add_argument("--start", default="2026-04-16T12")
    parser.add_argument("--end", default="2026-04-16T17")
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--session-hours", type=int, default=1)
    parser.add_argument("--half-spread", default="2.00")
    parser.add_argument("--order-qty", default="0.001")
    parser.add_argument("--max-position", default="0.01")
    parser.add_argument("--tick-size", default="0.01")
    parser.add_argument("--requote-interval-ms", type=int, default=5000)
    parser.add_argument("--ofi-interval-ms", type=int, default=1000)
    parser.add_argument("--ofi-threshold", default="0.25")
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
    parser.add_argument("--trade-gap-policy",
                        choices=["ignore", "pause_until_snapshot"],
                        default="pause_until_snapshot")
    parser.add_argument("--vol-window-ms", type=int, default=60_000)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path(
                            "results/event_driven_v2/markout_reconciliation"
                        ))
    add_book_arguments(parser)
    args = parser.parse_args()
    separate_backend_output(args)
    if args.queue_cancellation_mode is not None:
        args.queue_cancellation_credit = credit_from_legacy_mode(
            args.queue_cancellation_mode
        )
    else:
        args.queue_cancellation_credit = parse_queue_credit(
            args.queue_cancellation_credit
        )
    return args


def main():
    args = parse_args()
    guard_event_driven_output_path(args.output_dir)
    windows = _windows_from_args(args)

    sessions: list[AnalyzedSession] = []
    for idx, window in enumerate(windows, start=1):
        if len(windows) > 1:
            print(f"[{idx}/{len(windows)}] {window.label}")
        engine, strategy, result = _run_replay(args, window)
        final_mid = engine.book.mid
        markouts = compute_markouts(result.fills, result.book_samples, RECON_HORIZONS_MS)
        decomp = compute_pnl_decomposition(
            result.fills,
            result.book_samples,
            markouts,
            final_mid,
            adverse_selection_horizon="30s",
        )

        hold = compute_hold_time_summary(result.fills, result.book_samples)
        recon = compute_reconciliation_summary(hold, markouts, decomp, RECON_HORIZONS_MS)
        drift_rows = compute_pre_fill_drifts(result, vol_window_ms=args.vol_window_ms)
        queue_rows = extract_queue_diagnostics(result.events)
        spread_buckets = summarize_book_spread_by_volatility(drift_rows)

        sessions.append(AnalyzedSession(
            window=window,
            final_mid=final_mid,
            strategy_position=strategy.position,
            fills=len(result.fills),
            maker_fills=sum(1 for fill in result.fills if fill.is_maker),
            taker_fills=sum(1 for fill in result.fills if not fill.is_maker),
            postonly_rejects=engine.sim.postonly_rejects,
            orders_submitted=result.stats.orders_submitted,
            replay_stats=result.stats,
            decomp=decomp,
            hold=hold,
            recon=recon,
            drift_rows=drift_rows,
            queue_rows=queue_rows,
            spread_buckets=spread_buckets,
            drift_summary=_pre_fill_summary(drift_rows),
            queue_summary=_queue_summary(queue_rows),
            execution_provenance=result.execution_provenance,
        ))

    first_window = windows[0]
    total_hours = sum(window.hours for window in windows)
    run_id = (
        f"{args.symbol.lower()}_{args.strategy}_"
        f"{first_window.start.strftime('%Y%m%d_%H')}_{total_hours}h_"
        f"{len(windows)}sessions_hs{args.half_spread}_rq{args.requote_interval_ms}"
    )
    run_id = f"{run_id}{queue_credit_suffix(args.queue_cancellation_credit)}"
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_session_dataclass_csv(
        run_dir / "matched_lots.csv", sessions, "hold.matched_lots", MatchedLot
    )
    _write_session_dataclass_csv(
        run_dir / "open_lots.csv", sessions, "hold.open_lots", OpenInventoryLot
    )
    _write_session_dataclass_csv(
        run_dir / "pre_fill_drift.csv", sessions, "drift_rows", PreFillDrift
    )
    _write_session_dataclass_csv(
        run_dir / "queue_diagnostics.csv", sessions, "queue_rows", QueueOrderDiagnostic
    )
    _write_session_dataclass_csv(
        run_dir / "book_spread_by_volatility.csv",
        sessions,
        "spread_buckets",
        BookSpreadBucket,
    )

    aggregate = _aggregate_summary(args, sessions)
    session_summaries = []
    for session in sessions:
        session_summaries.append({
            "window": session.window.label,
            "hours": session.window.hours,
            "fills": session.fills,
            "maker_fills": session.maker_fills,
            "taker_fills": session.taker_fills,
            "maker_pct": (
                Decimal(session.maker_fills) / Decimal(session.fills) * Decimal("100")
                if session.fills else Decimal("0")
            ),
            "postonly_rejects": session.postonly_rejects,
            "orders_submitted": session.orders_submitted,
            "replay_stats": session.replay_stats,
            "orders_per_fill": (
                Decimal(session.orders_submitted) / Decimal(session.fills)
                if session.fills else None
            ),
            "final_mid": session.final_mid,
            "strategy_position": session.strategy_position,
            "pnl": {
                "spread_capture": session.decomp.spread_capture,
                "fees": session.decomp.total_fees,
                "inventory_pnl": session.decomp.inventory_pnl,
                "net_pnl": session.decomp.net_pnl,
            },
            "hold_time": {
                "matched_lots": len(session.hold.matched_lots),
                "matched_qty": session.hold.total_matched_qty,
                "matched_gross_pnl": session.hold.realized_pnl,
                "matched_fees": session.hold.matched_fees,
                "matched_net_pnl": session.hold.matched_net_pnl,
                "residual_inventory": session.hold.residual_inventory,
                "avg_hold_time_ms": session.hold.avg_hold_time_ms,
                "p25_hold_time_ms": session.hold.p25_hold_time_ms,
                "p50_hold_time_ms": session.hold.p50_hold_time_ms,
                "p75_hold_time_ms": session.hold.p75_hold_time_ms,
                "p90_hold_time_ms": session.hold.p90_hold_time_ms,
                "max_hold_time_ms": session.hold.max_hold_time_ms,
            },
            "reconciliation": session.recon,
            "pre_fill_drift": session.drift_summary,
            "queue": session.queue_summary,
            "book_spread_by_volatility": session.spread_buckets,
        })

    summary = {
        "execution_provenance": sessions[0].execution_provenance,
        "params": {
            "symbol": args.symbol.lower(),
            "strategy": args.strategy,
            "start": first_window.start.isoformat(),
            "hours": total_hours,
            "sessions": len(windows),
            "session_hours": args.session_hours,
            "half_spread": args.half_spread,
            "order_qty": args.order_qty,
            "max_position": args.max_position,
            "requote_interval_ms": args.requote_interval_ms,
            "ofi_interval_ms": args.ofi_interval_ms,
            "ofi_threshold": args.ofi_threshold,
            "latency_ms": args.latency_ms,
            "jitter_ms": args.jitter_ms,
            "cancel_latency_ms": sessions[0].execution_provenance[
                "cancel_latency_ms"
            ],
            "cancel_jitter_ms": sessions[0].execution_provenance[
                "cancel_jitter_ms"
            ],
            "maker_bps": args.maker_bps,
            "taker_bps": args.taker_bps,
            "queue_cancellation_credit": args.queue_cancellation_credit,
            "trade_gap_policy": args.trade_gap_policy,
        },
        "aggregate": aggregate,
        "sessions": session_summaries,
    }
    with (run_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=_decimal_json)

    if len(sessions) == 1:
        only = sessions[0]
        _print_summary(
            args,
            only.window,
            type("ResultView", (), {"fills": [None] * only.fills})(),
            only.decomp,
            only.hold,
            only.recon,
            only.drift_summary,
            only.queue_summary,
        )
    else:
        _print_aggregate_summary(args, sessions, aggregate)
    print(f"\nWrote reconciliation artifacts to {run_dir}")

if __name__ == "__main__":
    main()
