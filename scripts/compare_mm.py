"""
Compare market-making strategies across multiple L2 replay sessions.

Example:
    env PYTHONPATH=. python scripts/compare_mm.py \
        --start 2026-04-16T12 \
        --sessions 10 \
        --half-spread 0.50 \
        --order-qty 0.001 \
        --max-position 0.01
"""
import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable, List

from src.analysis.fill_rate import compute_order_contexts
from src.analysis.markout import compute_markouts, summarize_markouts
from src.analysis.pnl import compute_pnl_decomposition
from src.execution.simulator import SimConfig
from src.replay.engine import ReplayConfig, ReplayEngine
from src.strategies.microprice_mm import MicropriceMM
from src.strategies.symmetric_mm import SymmetricMM


@dataclass(frozen=True)
class SessionWindow:
    start: datetime
    hours: int

    @property
    def date(self) -> str:
        return self.start.strftime("%Y-%m-%d")

    @property
    def hour(self) -> int:
        return self.start.hour

    @property
    def label(self) -> str:
        return self.start.strftime("%Y-%m-%d %H:00")


def _decimal_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _parse_start(value: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H", "%Y-%m-%d %H", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(
        "Expected start format like 2026-04-16T12 or '2026-04-16 12'"
    )


def _session_windows(start: datetime, sessions: int, hours_per_session: int) -> List[SessionWindow]:
    return [
        SessionWindow(start=start + timedelta(hours=i * hours_per_session),
                      hours=hours_per_session)
        for i in range(sessions)
    ]


def _sessions_from_end(start: datetime, end: datetime, hours_per_session: int) -> int:
    if end <= start:
        raise ValueError("--end must be after --start")

    seconds_per_session = hours_per_session * 60 * 60
    total_seconds = int((end - start).total_seconds())
    if total_seconds % seconds_per_session != 0:
        raise ValueError("--end must align exactly with --session-hours")

    return total_seconds // seconds_per_session


def _select_files(
    data_root: Path,
    symbol: str,
    window: SessionWindow,
) -> tuple[List[Path], List[Path]]:
    symbol = symbol.lower()
    depth_dir = data_root / "raw" / symbol
    trade_dir = data_root / "raw" / f"{symbol}_trades"

    depth_files: List[Path] = []
    trade_files: List[Path] = []
    missing: List[Path] = []

    for offset in range(window.hours):
        ts = window.start + timedelta(hours=offset)
        stamp = ts.strftime("%Y%m%d_%H")
        depth_file = depth_dir / f"{symbol}_depth_{stamp}00.jsonl.gz"
        trade_file = trade_dir / f"{symbol}_trades_{stamp}00.jsonl.gz"

        if depth_file.exists():
            depth_files.append(depth_file)
        else:
            missing.append(depth_file)

        if trade_file.exists():
            trade_files.append(trade_file)
        else:
            missing.append(trade_file)

    if missing:
        missing_list = "\n".join(f"  {path}" for path in missing)
        raise FileNotFoundError(f"Missing replay input files:\n{missing_list}")

    return depth_files, trade_files


def _build_strategy(strategy_name: str, args):
    kwargs = dict(
        half_spread=Decimal(args.half_spread),
        order_qty=Decimal(args.order_qty),
        max_position=Decimal(args.max_position),
        tick_size=Decimal(args.tick_size),
        requote_interval_ms=args.requote_interval_ms,
    )
    if strategy_name == "symmetric":
        return SymmetricMM(**kwargs)
    if strategy_name == "microprice":
        return MicropriceMM(**kwargs)
    raise ValueError(f"Unknown strategy: {strategy_name}")


def _run_session(strategy_name: str, window: SessionWindow, args) -> dict:
    depth_files, trade_files = _select_files(args.data_root, args.symbol, window)
    strategy = _build_strategy(strategy_name, args)

    config = ReplayConfig(
        depth_files=depth_files,
        trade_files=trade_files,
        sim_config=SimConfig(
            base_latency_ms=args.latency_ms,
            jitter_ms=args.jitter_ms,
            maker_bps=args.maker_bps,
            taker_bps=args.taker_bps,
            queue_cancellation_mode=args.queue_cancellation_mode,
        ),
        record_book_samples=True,
    )
    engine = ReplayEngine(config)
    result = engine.run(strategy)

    final_mid = engine.book.mid
    markouts = compute_markouts(result.fills, result.book_samples)
    markout_summary = summarize_markouts(markouts)
    decomp = compute_pnl_decomposition(
        result.fills,
        result.book_samples,
        markouts,
        final_mid,
        adverse_selection_horizon=args.adverse_horizon,
    )
    contexts = compute_order_contexts(
        result, markouts,
        markout_horizon=args.adverse_horizon,
        session_id=window.label,
    )

    maker_fills = sum(1 for fill in result.fills if fill.is_maker)
    taker_fills = len(result.fills) - maker_fills
    maker_pct = (
        Decimal(maker_fills) / Decimal(len(result.fills)) * Decimal("100")
        if result.fills else Decimal("0")
    )
    horizon_row = markout_summary.get(args.adverse_horizon, {})

    return {
        "session_start": window.label,
        "session_hours": window.hours,
        "strategy": strategy_name,
        "symbol": args.symbol.lower(),
        "half_spread": args.half_spread,
        "order_qty": args.order_qty,
        "max_position": args.max_position,
        "tick_size": args.tick_size,
        "latency_ms": args.latency_ms,
        "jitter_ms": args.jitter_ms,
        "requote_interval_ms": args.requote_interval_ms,
        "maker_bps": args.maker_bps,
        "taker_bps": args.taker_bps,
        "queue_cancellation_mode": args.queue_cancellation_mode,
        "total_events": result.stats.total_events,
        "depth_diffs": result.stats.depth_diffs,
        "snapshots": result.stats.snapshots,
        "trades": result.stats.trade_events,
        "gaps_detected": result.stats.gaps_detected,
        "events_during_gap": result.stats.events_during_gap,
        "orders_submitted": result.stats.orders_submitted,
        "orders_cancelled": result.stats.orders_cancelled,
        "fills": len(result.fills),
        "maker_fills": maker_fills,
        "taker_fills": taker_fills,
        "maker_pct": maker_pct,
        "total_notional": decomp.total_notional,
        "fees": decomp.total_fees,
        "spread_capture": decomp.spread_capture,
        "spread_capture_bps": decomp.spread_capture_bps,
        "inventory_pnl": decomp.inventory_pnl,
        "adverse_selection_horizon": args.adverse_horizon,
        "adverse_selection_cost": decomp.adverse_selection_cost,
        "adverse_selection_bps": decomp.adverse_selection_bps,
        "markout_count": horizon_row.get("count", 0),
        "avg_markout_bps": horizon_row.get("avg_markout_bps"),
        "p50_markout_bps": horizon_row.get("p50_markout_bps"),
        "ending_position": decomp.final_position,
        "final_mid": final_mid,
        "net_pnl": decomp.net_pnl,
        "_contexts": contexts,   # private; stripped before CSV write
    }


def _format_table(rows: List[dict]) -> str:
    if not rows:
        return "No successful runs."

    columns = [
        ("session_start", "Session", 16),
        ("strategy", "Strategy", 10),
        ("fills", "Fills", 8),
        ("maker_pct", "Maker%", 8),
        ("net_pnl", "Net PnL", 14),
        ("fees", "Fees", 12),
        ("spread_capture_bps", "Spread bps", 11),
        ("avg_markout_bps", "Avg m/o", 10),
        ("p50_markout_bps", "p50 m/o", 10),
        ("adverse_selection_bps", "AdvSel bps", 11),
        ("ending_position", "End pos", 12),
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
    for row in rows:
        lines.append(" ".join(cell(row.get(key), width) for key, _, width in columns))
    return "\n".join(lines)


def _aggregate_rows(rows: Iterable[dict]) -> List[dict]:
    grouped: dict[str, List[dict]] = {}
    for row in rows:
        grouped.setdefault(row["strategy"], []).append(row)

    aggregates: List[dict] = []
    for strategy_name, strategy_rows in grouped.items():
        total_fills = sum(row["fills"] for row in strategy_rows)
        maker_fills = sum(row["maker_fills"] for row in strategy_rows)
        total_notional = sum(
            (row["total_notional"] for row in strategy_rows),
            Decimal("0"),
        )
        total_markouts = sum(row["markout_count"] for row in strategy_rows)
        avg_markout_bps = (
            sum(
                (
                    row["avg_markout_bps"] * Decimal(row["markout_count"])
                    for row in strategy_rows
                    if row["avg_markout_bps"] is not None
                ),
                Decimal("0"),
            ) / Decimal(total_markouts)
            if total_markouts else None
        )

        spread_capture = sum(
            (row["spread_capture"] for row in strategy_rows),
            Decimal("0"),
        )
        adverse_selection_cost = sum(
            (row["adverse_selection_cost"] for row in strategy_rows),
            Decimal("0"),
        )
        maker_pct = (
            Decimal(maker_fills) / Decimal(total_fills) * Decimal("100")
            if total_fills else Decimal("0")
        )
        spread_capture_bps = (
            spread_capture / total_notional * Decimal("10000")
            if total_notional else Decimal("0")
        )
        adverse_selection_bps = (
            adverse_selection_cost / total_notional * Decimal("10000")
            if total_notional else Decimal("0")
        )

        aggregates.append({
            "strategy": strategy_name,
            "sessions": len(strategy_rows),
            "fills": total_fills,
            "maker_fills": maker_fills,
            "maker_pct": maker_pct,
            "net_pnl": sum((row["net_pnl"] for row in strategy_rows), Decimal("0")),
            "avg_session_net_pnl": (
                sum((row["net_pnl"] for row in strategy_rows), Decimal("0"))
                / Decimal(len(strategy_rows))
            ),
            "fees": sum((row["fees"] for row in strategy_rows), Decimal("0")),
            "spread_capture": spread_capture,
            "spread_capture_bps": spread_capture_bps,
            "inventory_pnl": sum(
                (row["inventory_pnl"] for row in strategy_rows),
                Decimal("0"),
            ),
            "adverse_selection_horizon": strategy_rows[0]["adverse_selection_horizon"],
            "adverse_selection_cost": adverse_selection_cost,
            "adverse_selection_bps": adverse_selection_bps,
            "avg_markout_bps": avg_markout_bps,
            "gaps_detected": sum(row["gaps_detected"] for row in strategy_rows),
            "queue_cancellation_mode": strategy_rows[0]["queue_cancellation_mode"],
        })

    return sorted(aggregates, key=lambda row: row["strategy"])


def _format_aggregate_table(rows: List[dict]) -> str:
    if not rows:
        return "No aggregate rows."

    columns = [
        ("strategy", "Strategy", 10),
        ("sessions", "Sessions", 8),
        ("fills", "Fills", 8),
        ("maker_pct", "Maker%", 8),
        ("net_pnl", "Net PnL", 14),
        ("avg_session_net_pnl", "Avg Net", 12),
        ("fees", "Fees", 12),
        ("spread_capture_bps", "Spread bps", 11),
        ("avg_markout_bps", "Avg m/o", 10),
        ("adverse_selection_bps", "AdvSel bps", 11),
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
    for row in rows:
        lines.append(" ".join(cell(row.get(key), width) for key, _, width in columns))
    return "\n".join(lines)


def _write_csv(path: Path, rows: Iterable[dict]) -> None:
    # Strip private keys (underscore-prefixed) so callers can attach
    # non-serializable payloads (like OrderContext lists) to row dicts.
    rows = [
        {k: v for k, v in row.items() if not k.startswith("_")}
        for row in rows
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: _decimal_str(value)
                for key, value in row.items()
            })


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare L2 market-making strategies across replay sessions"
    )
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--start", required=True, type=_parse_start,
                        help="First session start, e.g. 2026-04-16T12")
    parser.add_argument("--end", type=_parse_start,
                        help="Exclusive range end, e.g. 2026-04-16T22")
    parser.add_argument("--sessions", type=int,
                        help="Number of contiguous sessions to run; defaults to 10 without --end")
    parser.add_argument("--session-hours", type=int, default=1,
                        help="Hours per replay session")
    parser.add_argument("--strategies", nargs="+",
                        choices=["symmetric", "microprice"],
                        default=["symmetric", "microprice"])
    parser.add_argument("--half-spread", default="0.50")
    parser.add_argument("--order-qty", default="0.001")
    parser.add_argument("--max-position", default="0.01")
    parser.add_argument("--tick-size", default="0.01")
    parser.add_argument("--latency-ms", type=int, default=10)
    parser.add_argument("--jitter-ms", type=int, default=0)
    parser.add_argument("--requote-interval-ms", type=int, default=0)
    parser.add_argument("--maker-bps", type=int, default=2)
    parser.add_argument("--taker-bps", type=int, default=5)
    parser.add_argument("--queue-cancellation-mode",
                        choices=["proportional", "none"],
                        default="proportional")
    parser.add_argument("--adverse-horizon", default="30s")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/compare"))
    parser.add_argument("--skip-missing", action="store_true",
                        help="Skip sessions whose depth/trade files are missing")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.end is not None:
        args.sessions = _sessions_from_end(args.start, args.end, args.session_hours)
    elif args.sessions is None:
        args.sessions = 10

    windows = _session_windows(args.start, args.sessions, args.session_hours)

    rows: List[dict] = []
    total_runs = len(windows) * len(args.strategies)
    run_idx = 0
    for window in windows:
        for strategy_name in args.strategies:
            run_idx += 1
            print(f"[{run_idx}/{total_runs}] {window.label} {strategy_name}")
            try:
                rows.append(_run_session(strategy_name, window, args))
            except FileNotFoundError as exc:
                if not args.skip_missing:
                    raise
                print(f"  Skipping missing session: {exc}")

    csv_path = args.output_dir / "comparison.csv"
    aggregate_path = args.output_dir / "aggregate.csv"
    aggregate_rows = _aggregate_rows(rows)
    _write_csv(csv_path, rows)
    _write_csv(aggregate_path, aggregate_rows)

    print()
    print("Per-session comparison")
    print(_format_table(rows))
    print()
    print("Aggregate by strategy")
    print(_format_aggregate_table(aggregate_rows))
    print(f"\nWrote comparison to {csv_path}")
    print(f"Wrote aggregate to {aggregate_path}")


if __name__ == "__main__":
    main()
