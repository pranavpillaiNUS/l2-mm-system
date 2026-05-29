"""
Bounded audit for same-millisecond depth/trade attribution risk.

The replay intentionally processes depth before trade on equal timestamps.
This diagnostic quantifies the prevalence of depth/trade ties and how many
persisted strategy fills land on those ties. It is intentionally bounded:
escalation is triggered only by the explicit fill-overlap threshold or by
artifact-evidenced wrong attribution cases.

Example:
    env PYTHONPATH=. python scripts/audit_same_ms_attribution.py
"""
import argparse
import csv
import json
from collections import defaultdict
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Sequence

from scripts.compare_mm import SessionWindow, _parse_start, _select_files
from src.execution.queue_credit import (
    credit_from_legacy_mode,
    parse_queue_credit,
    queue_credit_suffix,
)
from src.replay.depth_parser import DepthParser
from src.replay.trade_parser import TradeParser


DEFAULT_STARTS = [
    "2026-04-13T12",
    "2026-04-14T12",
    "2026-04-15T12",
    "2026-04-16T12",
    "2026-04-16T17",
    "2026-04-17T12",
]
DEFAULT_OUTPUT_ROOT = Path("results/same_ms_audit")
DEFAULT_RUN_ID = "btcusdt_anchor6"


def _window_from_start(start_value: str, hours: int) -> SessionWindow:
    return SessionWindow(start=_parse_start(start_value), hours=hours)


def _run_dir_name(args, start_value: str) -> str:
    start = _parse_start(start_value)
    run_id = (
        f"{args.symbol.lower()}_{args.strategy}_"
        f"{start.strftime('%Y%m%d_%H')}_{args.hours}h_"
        f"{args.hours // args.session_hours}sessions_"
        f"hs{args.half_spread}_rq{args.requote_interval_ms}"
    )
    return f"{run_id}{queue_credit_suffix(args.queue_cancellation_credit)}"


def _pct(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return Decimal(numerator) / Decimal(denominator) * Decimal("100")


def _audit_window(args, start_value: str) -> tuple[dict, list[dict]]:
    window = _window_from_start(start_value, args.hours)
    depth_files, trade_files = _select_files(args.data_root, args.symbol, window)
    run_dir = args.reconciliation_root / _run_dir_name(args, start_value)
    if not run_dir.exists():
        raise FileNotFoundError(f"Missing reconciliation artifact: {run_dir}")

    depth_counts_by_ts: dict[int, int] = defaultdict(int)
    depth_events = 0
    for event in DepthParser(depth_files).events():
        if event.event_type != "diff":
            continue
        depth_events += 1
        depth_counts_by_ts[event.exchange_time_ms] += 1

    trade_counts_by_ts: dict[int, int] = defaultdict(int)
    same_side_trade_qty: dict[tuple[int, str, Decimal], Decimal] = defaultdict(Decimal)
    trade_events = 0
    for trade in TradeParser(trade_files).events():
        trade_events += 1
        trade_counts_by_ts[trade.exchange_time_ms] += 1
        fill_side = "buy" if trade.is_buyer_maker else "sell"
        same_side_trade_qty[
            (trade.exchange_time_ms, fill_side, trade.price)
        ] += trade.quantity

    overlap_timestamps = set(depth_counts_by_ts) & set(trade_counts_by_ts)
    fills = _load_unique_fills(run_dir)
    queue_by_order = _load_queue_by_order(run_dir)

    overlap_fills = []
    risk_candidate_rows = []
    for fill in fills.values():
        if fill["timestamp_ms"] not in overlap_timestamps:
            continue
        overlap_fills.append(fill)
        same_side_qty = same_side_trade_qty.get(
            (fill["timestamp_ms"], fill["side"], fill["price"]),
            Decimal("0"),
        )
        queue = queue_by_order.get(fill["order_id"], {})
        cancel_drain = queue.get("total_cancel_queue_drained", Decimal("0"))
        if same_side_qty > Decimal("0") and cancel_drain > Decimal("0"):
            risk_candidate_rows.append({
                "window": window.label,
                "session": fill["session"],
                "fill_id": fill["fill_id"],
                "order_id": fill["order_id"],
                "timestamp_ms": fill["timestamp_ms"],
                "side": fill["side"],
                "price": fill["price"],
                "quantity": fill["quantity"],
                "same_side_trade_qty_at_price_ms": same_side_qty,
                "order_total_cancel_queue_drained": cancel_drain,
                "note": (
                    "candidate only: queue_diagnostics is order-level and "
                    "does not persist same-ms queue-drain timestamps"
                ),
            })

    total_fills = int(_load_summary(run_dir)["aggregate"]["fills"])
    if len(fills) != total_fills:
        raise ValueError(
            f"{run_dir} reconstructs {len(fills)} unique fills but summary "
            f"reports {total_fills}"
        )

    wrong_attribution_cases = 0
    overlap_fill_share_pct = _pct(len(overlap_fills), total_fills)
    wrong_share_pct = _pct(wrong_attribution_cases, len(overlap_fills))
    escalates = (
        overlap_fill_share_pct > args.overlap_fill_threshold_pct
        or wrong_share_pct > args.wrong_attribution_threshold_pct
    )

    summary = {
        "window": window.label,
        "hours": args.hours,
        "run_dir": run_dir.name,
        "depth_diff_events": depth_events,
        "trade_events": trade_events,
        "same_ms_overlap_timestamps": len(overlap_timestamps),
        "same_ms_depth_events": sum(depth_counts_by_ts[ts] for ts in overlap_timestamps),
        "same_ms_trade_events": sum(trade_counts_by_ts[ts] for ts in overlap_timestamps),
        "total_fills": total_fills,
        "same_ms_overlap_fills": len(overlap_fills),
        "same_ms_overlap_fill_share_pct": overlap_fill_share_pct,
        "same_ms_trade_price_touch_fill_candidates": len(risk_candidate_rows),
        "wrong_attribution_cases": wrong_attribution_cases,
        "wrong_attribution_share_of_overlap_fills_pct": wrong_share_pct,
        "escalates": escalates,
    }
    return summary, risk_candidate_rows


def _load_unique_fills(run_dir: Path) -> dict[str, dict]:
    fills: dict[str, dict] = {}
    with (run_dir / "matched_lots.csv").open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            _add_fill(
                fills,
                session=row["session"],
                fill_id=row["open_fill_id"],
                order_id=row["open_order_id"],
                side=row["open_side"],
                timestamp_ms=int(row["open_time_ms"]),
                price=_d(row["open_price"]),
                quantity=_d(row["quantity"]),
            )
            _add_fill(
                fills,
                session=row["session"],
                fill_id=row["close_fill_id"],
                order_id=row["close_order_id"],
                side=row["close_side"],
                timestamp_ms=int(row["close_time_ms"]),
                price=_d(row["close_price"]),
                quantity=_d(row["quantity"]),
            )

    with (run_dir / "open_lots.csv").open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            _add_fill(
                fills,
                session=row["session"],
                fill_id=row["fill_id"],
                order_id=row["order_id"],
                side=row["side"],
                timestamp_ms=int(row["open_time_ms"]),
                price=_d(row["open_price"]),
                quantity=_d(row["quantity"]),
            )
    return fills


def _add_fill(
    fills: dict[str, dict],
    *,
    session: str,
    fill_id: str,
    order_id: str,
    side: str,
    timestamp_ms: int,
    price: Decimal,
    quantity: Decimal,
) -> None:
    fill_key = f"{session}:{fill_id}"
    order_key = f"{session}:{order_id}"
    existing = fills.get(fill_key)
    if existing is None:
        fills[fill_key] = {
            "session": session,
            "fill_id": fill_id,
            "fill_key": fill_key,
            "order_id": order_key,
            "side": side,
            "timestamp_ms": timestamp_ms,
            "price": price,
            "quantity": quantity,
        }
        return

    for key, value in (
        ("order_id", order_key),
        ("side", side),
        ("timestamp_ms", timestamp_ms),
        ("price", price),
    ):
        if existing[key] != value:
            raise ValueError(f"Conflicting fill reconstruction for {fill_key}: {key}")
    existing["quantity"] += quantity


def _load_queue_by_order(run_dir: Path) -> dict[str, dict]:
    rows = {}
    with (run_dir / "queue_diagnostics.csv").open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows[f"{row['session']}:{row['order_id']}"] = {
                "total_cancel_queue_drained": _d(row["total_cancel_queue_drained"] or "0"),
                "total_trade_queue_drained": _d(row["total_trade_queue_drained"] or "0"),
            }
    return rows


def _load_summary(run_dir: Path) -> dict:
    with (run_dir / "summary.json").open("r", encoding="utf-8") as f:
        return json.load(f)


def _aggregate(window_rows: Sequence[dict]) -> dict:
    total_fills = sum(int(row["total_fills"]) for row in window_rows)
    overlap_fills = sum(int(row["same_ms_overlap_fills"]) for row in window_rows)
    wrong_cases = sum(int(row["wrong_attribution_cases"]) for row in window_rows)
    return {
        "windows": len(window_rows),
        "depth_diff_events": sum(int(row["depth_diff_events"]) for row in window_rows),
        "trade_events": sum(int(row["trade_events"]) for row in window_rows),
        "same_ms_overlap_timestamps": sum(
            int(row["same_ms_overlap_timestamps"]) for row in window_rows
        ),
        "same_ms_depth_events": sum(int(row["same_ms_depth_events"]) for row in window_rows),
        "same_ms_trade_events": sum(int(row["same_ms_trade_events"]) for row in window_rows),
        "total_fills": total_fills,
        "same_ms_overlap_fills": overlap_fills,
        "same_ms_overlap_fill_share_pct": _pct(overlap_fills, total_fills),
        "same_ms_trade_price_touch_fill_candidates": sum(
            int(row["same_ms_trade_price_touch_fill_candidates"])
            for row in window_rows
        ),
        "wrong_attribution_cases": wrong_cases,
        "wrong_attribution_share_of_overlap_fills_pct": _pct(wrong_cases, overlap_fills),
    }


def _d(value) -> Decimal:
    return Decimal(str(value))


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    return value


def _csv_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _write_csv(path: Path, rows: Sequence[dict]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})


def parse_args():
    parser = argparse.ArgumentParser(
        description="Quantify same-millisecond depth/trade attribution risk"
    )
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--strategy", choices=["symmetric", "microprice"],
                        default="microprice")
    parser.add_argument("--starts", nargs="+", default=DEFAULT_STARTS)
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--session-hours", type=int, default=1)
    parser.add_argument("--half-spread", default="2.00")
    parser.add_argument("--requote-interval-ms", type=int, default=5000)
    parser.add_argument("--queue-cancellation-credit", default="1.0",
                        help="Cancellation-driven queue credit in [0.0, 1.0]")
    parser.add_argument("--queue-cancellation-mode",
                        choices=["proportional", "none"],
                        help=argparse.SUPPRESS)
    parser.add_argument("--overlap-fill-threshold-pct",
                        type=Decimal, default=Decimal("5"))
    parser.add_argument("--wrong-attribution-threshold-pct",
                        type=Decimal, default=Decimal("1"))
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--reconciliation-root", type=Path,
                        default=Path("results/markout_reconciliation"))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
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
    window_rows: list[dict] = []
    candidate_rows: list[dict] = []

    for idx, start_value in enumerate(args.starts, start=1):
        print(f"[{idx}/{len(args.starts)}] {start_value}", flush=True)
        window_summary, candidates = _audit_window(args, start_value)
        window_rows.append(window_summary)
        candidate_rows.extend(candidates)

    aggregate = _aggregate(window_rows)
    escalates = (
        aggregate["same_ms_overlap_fill_share_pct"] > args.overlap_fill_threshold_pct
        or aggregate["wrong_attribution_share_of_overlap_fills_pct"]
        > args.wrong_attribution_threshold_pct
    )
    decision = (
        "escalate_grouped_timestamp_handling"
        if escalates else "document_assumption_no_blocker"
    )

    run_dir = args.output_root / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(run_dir / "window_summary.csv", window_rows)
    _write_csv(run_dir / "attribution_risk_candidates.csv", candidate_rows)
    summary = {
        "params": {
            "symbol": args.symbol.lower(),
            "strategy": args.strategy,
            "starts": args.starts,
            "hours": args.hours,
            "half_spread": args.half_spread,
            "requote_interval_ms": args.requote_interval_ms,
            "queue_cancellation_credit": args.queue_cancellation_credit,
            "depth_trade_tie_rule": "depth_before_trade",
            "overlap_fill_threshold_pct": args.overlap_fill_threshold_pct,
            "wrong_attribution_threshold_pct": args.wrong_attribution_threshold_pct,
            "wrong_attribution_definition": (
                "artifact-evidenced case where persisted data contradicts "
                "the depth-before-trade assumption; candidate rows are not "
                "classified wrong because queue-drain timestamps are not "
                "persisted in reconciliation artifacts"
            ),
        },
        "aggregate": aggregate,
        "decision": decision,
        "windows": window_rows,
    }
    with (run_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=_jsonable)

    print("Same-ms attribution audit")
    print(f"  Total fills:        {aggregate['total_fills']}")
    print(f"  Overlap fills:      {aggregate['same_ms_overlap_fills']} "
          f"({aggregate['same_ms_overlap_fill_share_pct']:.2f}%)")
    print(f"  Risk candidates:    "
          f"{aggregate['same_ms_trade_price_touch_fill_candidates']}")
    print(f"  Wrong-attribution cases: {aggregate['wrong_attribution_cases']} "
          f"({aggregate['wrong_attribution_share_of_overlap_fills_pct']:.2f}% "
          "of overlap fills)")
    print(f"  Decision:           {decision}")
    print(f"\nWrote same-ms audit artifacts to {run_dir}")


if __name__ == "__main__":
    main()
