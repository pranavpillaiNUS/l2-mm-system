"""
Run a single L2 replay session and print summary stats.

Example:
    env PYTHONPATH=. python scripts/run_replay.py \
        --strategy symmetric --date 2026-04-16 --hour 12 --hours 1
"""
import argparse
import csv
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import List

from src.analysis.fill_rate import format_fill_rate_summary, summarize_fill_rate
from src.analysis.markout import compute_markouts, summarize_markouts
from src.analysis.pnl import compute_pnl_decomposition, format_pnl_summary
from src.execution.order import Fill
from src.execution.queue_credit import credit_from_legacy_mode, parse_queue_credit
from src.execution.simulator import SimConfig
from src.replay.engine import ReplayConfig, ReplayEngine
from src.strategies.microprice_mm import MicropriceMM
from src.strategies.symmetric_mm import SymmetricMM


def _decimal_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _decimal_json(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _select_files(
    data_root: Path,
    symbol: str,
    date: str,
    hour: int,
    hours: int,
) -> tuple[List[Path], List[Path]]:
    start = datetime.strptime(f"{date} {hour:02d}", "%Y-%m-%d %H")
    symbol = symbol.lower()
    depth_dir = data_root / "raw" / symbol
    trade_dir = data_root / "raw" / f"{symbol}_trades"

    depth_files: List[Path] = []
    trade_files: List[Path] = []
    missing: List[Path] = []

    for offset in range(hours):
        ts = start + timedelta(hours=offset)
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


def _build_strategy(args):
    kwargs = dict(
        half_spread=Decimal(args.half_spread),
        order_qty=Decimal(args.order_qty),
        max_position=Decimal(args.max_position),
        tick_size=Decimal(args.tick_size),
        requote_interval_ms=args.requote_interval_ms,
    )
    if args.strategy == "symmetric":
        return SymmetricMM(**kwargs)
    if args.strategy == "microprice":
        return MicropriceMM(**kwargs)
    raise ValueError(f"Unknown strategy: {args.strategy}")


def _final_mid(engine: ReplayEngine) -> Decimal | None:
    return engine.book.mid


def _gross_pnl(strategy, mark_price: Decimal | None) -> Decimal:
    if mark_price is None:
        return strategy.realized_pnl
    return strategy.realized_pnl + strategy.position * mark_price


def _print_summary(args, result, strategy, markouts, final_mid, decomp=None):
    maker_fills = sum(1 for fill in result.fills if fill.is_maker)
    taker_fills = len(result.fills) - maker_fills
    gross_pnl = _gross_pnl(strategy, final_mid)
    net_pnl = gross_pnl - strategy.total_fees

    print("=" * 72)
    print("L2 REPLAY SUMMARY")
    print("=" * 72)
    print(f"Strategy:         {args.strategy}")
    print(f"Symbol:           {args.symbol.upper()}")
    print(f"Window:           {args.date} {args.hour:02d}:00 for {args.hours} hour(s)")
    print(f"Half spread:      {args.half_spread}")
    print(f"Order qty:        {args.order_qty}")
    print(f"Max position:     {args.max_position}")
    print(f"Requote interval: {args.requote_interval_ms}ms")
    print(f"Latency:          {args.latency_ms}ms +/- {args.jitter_ms}ms")
    print(f"Queue credit:     {args.queue_cancellation_credit}")
    print()

    stats = result.stats
    print("Events")
    print(f"  Total:          {stats.total_events:,}")
    print(f"  Depth diffs:    {stats.depth_diffs:,}")
    print(f"  Snapshots:      {stats.snapshots:,}")
    print(f"  Trades:         {stats.trade_events:,}")
    print(f"  Gaps detected:  {stats.gaps_detected:,}")
    print(f"  Gap events:     {stats.events_during_gap:,}")
    print()

    print("Execution")
    print(f"  Orders:         {stats.orders_submitted:,}")
    print(f"  Cancels:        {stats.orders_cancelled:,}")
    print(f"  Fills:          {len(result.fills):,}")
    print(f"  Maker fills:    {maker_fills:,}")
    print(f"  Taker fills:    {taker_fills:,}")
    print(f"  Fees:           {_decimal_str(strategy.total_fees)}")
    print()

    print("PnL")
    print(f"  Ending position:{_decimal_str(strategy.position):>16}")
    print(f"  Final mid:      {_decimal_str(final_mid):>16}")
    print(f"  Realized PnL:   {_decimal_str(strategy.realized_pnl):>16}")
    print(f"  Gross PnL:      {_decimal_str(gross_pnl):>16}")
    print(f"  Net PnL:        {_decimal_str(net_pnl):>16}")
    print()

    print("Markouts")
    markout_summary = summarize_markouts(markouts)
    if not markout_summary:
        print("  No markouts available.")
    else:
        print(f"  {'Horizon':<8} {'Count':>8} {'Avg bps':>10} {'p25 bps':>10} {'p50 bps':>10} {'p75 bps':>10}")
        for horizon in ["1s", "5s", "30s", "1m", "5m"]:
            row = markout_summary.get(horizon)
            if row is None:
                continue
            p25 = _decimal_str(row["p25_markout_bps"]) if row["p25_markout_bps"] is not None else "n/a"
            p50 = _decimal_str(row["p50_markout_bps"]) if row["p50_markout_bps"] is not None else "n/a"
            p75 = _decimal_str(row["p75_markout_bps"]) if row["p75_markout_bps"] is not None else "n/a"
            print(
                f"  {horizon:<8} {row['count']:>8} "
                f"{_decimal_str(row['avg_markout_bps']):>10} "
                f"{p25:>10} {p50:>10} {p75:>10}"
            )

    if decomp is not None:
        print()
        print(format_pnl_summary(decomp))

    fill_rate_summary = summarize_fill_rate(result, markouts)
    print()
    print(format_fill_rate_summary(fill_rate_summary))


def _write_results(output_dir: Path, args, result, strategy, markouts, final_mid, decomp=None) -> None:
    run_id = f"{args.symbol.lower()}_{args.strategy}_{args.date}_{args.hour:02d}_{args.hours}h"
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    gross_pnl = _gross_pnl(strategy, final_mid)
    summary = {
        "strategy": args.strategy,
        "symbol": args.symbol.lower(),
        "date": args.date,
        "hour": args.hour,
        "hours": args.hours,
        "half_spread": args.half_spread,
        "order_qty": args.order_qty,
        "max_position": args.max_position,
        "requote_interval_ms": args.requote_interval_ms,
        "latency_ms": args.latency_ms,
        "jitter_ms": args.jitter_ms,
        "maker_bps": args.maker_bps,
        "taker_bps": args.taker_bps,
        "queue_cancellation_credit": args.queue_cancellation_credit,
        "events": result.stats.__dict__,
        "fills": len(result.fills),
        "maker_fills": sum(1 for fill in result.fills if fill.is_maker),
        "taker_fills": sum(1 for fill in result.fills if not fill.is_maker),
        "ending_position": strategy.position,
        "final_mid": final_mid,
        "realized_pnl": strategy.realized_pnl,
        "gross_pnl": gross_pnl,
        "fees": strategy.total_fees,
        "net_pnl": gross_pnl - strategy.total_fees,
        "markouts": summarize_markouts(markouts),
        "pnl_decomposition": decomp.__dict__ if decomp is not None else None,
    }

    with (run_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=_decimal_json)

    with (run_dir / "fills.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "fill_id", "order_id", "side", "price", "quantity",
                "notional", "is_maker", "timestamp_ms", "fee",
            ],
        )
        writer.writeheader()
        for fill in result.fills:
            writer.writerow(_fill_row(fill))

    with (run_dir / "markouts.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "fill_id", "order_id", "side", "fill_timestamp_ms",
                "fill_price", "quantity", "horizon", "horizon_ms",
                "sample_timestamp_ms", "future_mid", "markout", "markout_bps",
            ],
        )
        writer.writeheader()
        for row in markouts:
            writer.writerow({
                "fill_id": row.fill_id,
                "order_id": row.order_id,
                "side": row.side.value,
                "fill_timestamp_ms": row.fill_timestamp_ms,
                "fill_price": _decimal_str(row.fill_price),
                "quantity": _decimal_str(row.quantity),
                "horizon": row.horizon,
                "horizon_ms": row.horizon_ms,
                "sample_timestamp_ms": row.sample_timestamp_ms,
                "future_mid": _decimal_str(row.future_mid),
                "markout": _decimal_str(row.markout),
                "markout_bps": _decimal_str(row.markout_bps),
            })

    print(f"\nWrote results to {run_dir}")


def _fill_row(fill: Fill) -> dict:
    return {
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "side": fill.side.value,
        "price": _decimal_str(fill.price),
        "quantity": _decimal_str(fill.quantity),
        "notional": _decimal_str(fill.notional),
        "is_maker": fill.is_maker,
        "timestamp_ms": fill.timestamp_ms,
        "fee": _decimal_str(fill.fee),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Run an L2 replay session")
    parser.add_argument("--strategy", choices=["symmetric", "microprice"],
                        default="symmetric")
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--date", required=True, help="Replay date, YYYY-MM-DD")
    parser.add_argument("--hour", required=True, type=int, help="Start hour, 0-23")
    parser.add_argument("--hours", type=int, default=1)
    parser.add_argument("--half-spread", default="1.00")
    parser.add_argument("--order-qty", default="0.001")
    parser.add_argument("--max-position", default="0.01")
    parser.add_argument("--tick-size", default="0.01")
    parser.add_argument("--requote-interval-ms", type=int, default=0)
    parser.add_argument("--latency-ms", type=int, default=10)
    parser.add_argument("--jitter-ms", type=int, default=0)
    parser.add_argument("--maker-bps", type=int, default=2)
    parser.add_argument("--taker-bps", type=int, default=5)
    parser.add_argument("--queue-cancellation-credit", default="1.0",
                        help="Cancellation-driven queue credit in [0.0, 1.0]")
    parser.add_argument("--queue-cancellation-mode",
                        choices=["proportional", "none"],
                        help=argparse.SUPPRESS)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--write-results", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("results/replay"))
    args = parser.parse_args()
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
    depth_files, trade_files = _select_files(
        args.data_root, args.symbol, args.date, args.hour, args.hours,
    )
    strategy = _build_strategy(args)

    config = ReplayConfig(
        depth_files=depth_files,
        trade_files=trade_files,
        sim_config=SimConfig(
            base_latency_ms=args.latency_ms,
            jitter_ms=args.jitter_ms,
            maker_bps=args.maker_bps,
            taker_bps=args.taker_bps,
            queue_cancellation_credit=args.queue_cancellation_credit,
        ),
        record_book_samples=True,
    )
    engine = ReplayEngine(config)
    result = engine.run(strategy)
    final_mid = _final_mid(engine)
    markouts = compute_markouts(result.fills, result.book_samples)
    decomp = compute_pnl_decomposition(result.fills, result.book_samples, markouts, final_mid)

    _print_summary(args, result, strategy, markouts, final_mid, decomp)
    if args.write_results:
        _write_results(args.output_dir, args, result, strategy, markouts, final_mid, decomp)


if __name__ == "__main__":
    main()
