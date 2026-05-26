"""
Compare anchor-window results under proportional vs no cancellation queue credit.

Example:
    env PYTHONPATH=. python scripts/analyze_queue_sensitivity.py
"""
import argparse
import csv
import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Sequence

from scripts.compare_mm import _parse_start


DEFAULT_STARTS = [
    "2026-04-13T12",
    "2026-04-14T12",
    "2026-04-15T12",
    "2026-04-16T12",
    "2026-04-16T17",
    "2026-04-17T12",
]
DEFAULT_FEE_BREAK_EVEN = Path(
    "results/fee_break_even/btcusdt_microprice_hs2.00_rq5000_anchor6/"
    "fee_break_even.csv"
)
DEFAULT_OUTPUT_ROOT = Path("results/queue_sensitivity")
DEFAULT_RUN_ID = "btcusdt_microprice_hs2.00_rq5000_anchor6"


def _run_dir_name(args, start_value: str, queue_mode: str) -> str:
    start = _parse_start(start_value)
    run_id = (
        f"{args.symbol.lower()}_{args.strategy}_"
        f"{start.strftime('%Y%m%d_%H')}_{args.hours}h_"
        f"{args.hours // args.session_hours}sessions_"
        f"hs{args.half_spread}_rq{args.requote_interval_ms}"
    )
    if queue_mode != "proportional":
        run_id = f"{run_id}_q{queue_mode}"
    return run_id


def _load_summary(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _d(value) -> Decimal:
    if value in ("", None):
        return Decimal("0")
    return Decimal(str(value))


def _maybe_d(value):
    if value in ("", None):
        return None
    return Decimal(str(value))


def _load_fee_rows(path: Path) -> dict[tuple[str, str, str], dict]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return {
        (row["queue_cancellation_mode"], row["window"], row["row_type"]): row
        for row in rows
    }


def _mode_window_rows(args, fee_rows: dict[tuple[str, str, str], dict]) -> list[dict]:
    rows = []
    for queue_mode in ("proportional", "none"):
        for start_value in args.starts:
            run_dir = args.reconciliation_root / _run_dir_name(args, start_value, queue_mode)
            if not run_dir.exists():
                raise FileNotFoundError(
                    f"Missing reconciliation artifact: {run_dir}. "
                    "Run scripts/analyze_markout_reconciliation.py for that "
                    "start and queue mode first."
                )
            summary = _load_summary(run_dir / "summary.json")
            params = summary["params"]
            aggregate = summary["aggregate"]
            window = params["start"]
            fee_full = fee_rows[(queue_mode, window, "full_strategy")]
            fee_matched = fee_rows[(queue_mode, window, "full_matched_lots")]
            rows.append({
                "queue_cancellation_mode": queue_mode,
                "window": window,
                "run_dir": run_dir.name,
                "fills": int(aggregate["fills"]),
                "maker_fills": int(aggregate.get("maker_fills", 0)),
                "taker_fills": int(aggregate.get("taker_fills", 0)),
                "orders_submitted": int(aggregate["orders_submitted"]),
                "orders_per_fill": _maybe_d(aggregate.get("orders_per_fill")),
                "maker_pct": _maybe_d(aggregate.get("maker_pct")),
                "postonly_rejects": int(aggregate.get("postonly_rejects", 0)),
                "matched_net_pnl": _d(aggregate["matched_net_pnl"]),
                "residual_inventory_pnl": _maybe_d(aggregate.get("residual_inventory_pnl")),
                "net_pnl": _d(aggregate["net_pnl"]),
                "full_strategy_break_even_maker_fee_bps": _maybe_d(
                    fee_full["break_even_maker_fee_bps"]
                ),
                "matched_break_even_maker_fee_bps": _maybe_d(
                    fee_matched["break_even_maker_fee_bps"]
                ),
                "current_maker_fee_bps": int(params["maker_bps"]),
            })
    return rows


def _pooled_rows(
    mode_rows: Sequence[dict],
    fee_rows: dict[tuple[str, str, str], dict],
) -> list[dict]:
    out = []
    for queue_mode in ("proportional", "none"):
        rows = [row for row in mode_rows if row["queue_cancellation_mode"] == queue_mode]
        fills = sum(int(row["fills"]) for row in rows)
        maker_fills = sum(int(row["maker_fills"]) for row in rows)
        taker_fills = sum(int(row["taker_fills"]) for row in rows)
        orders = sum(int(row["orders_submitted"]) for row in rows)
        full_fee = fee_rows[(queue_mode, "pooled", "full_strategy")]
        matched_fee = fee_rows[(queue_mode, "pooled", "full_matched_lots")]
        out.append({
            "queue_cancellation_mode": queue_mode,
            "window": "pooled",
            "run_dir": "pooled",
            "fills": fills,
            "maker_fills": maker_fills,
            "taker_fills": taker_fills,
            "orders_submitted": orders,
            "orders_per_fill": Decimal(orders) / Decimal(fills) if fills else None,
            "maker_pct": (
                Decimal(maker_fills) / Decimal(fills) * Decimal("100")
                if fills else Decimal("0")
            ),
            "postonly_rejects": sum(int(row["postonly_rejects"]) for row in rows),
            "matched_net_pnl": sum((row["matched_net_pnl"] for row in rows), Decimal("0")),
            "residual_inventory_pnl": sum(
                (row["residual_inventory_pnl"] or Decimal("0") for row in rows),
                Decimal("0"),
            ),
            "net_pnl": sum((row["net_pnl"] for row in rows), Decimal("0")),
            "full_strategy_break_even_maker_fee_bps": _maybe_d(
                full_fee["break_even_maker_fee_bps"]
            ),
            "matched_break_even_maker_fee_bps": _maybe_d(
                matched_fee["break_even_maker_fee_bps"]
            ),
            "current_maker_fee_bps": int(full_fee["current_maker_fee_bps"]),
        })
    return out


def _comparison_rows(mode_rows: Sequence[dict]) -> list[dict]:
    rows = []
    windows = sorted({row["window"] for row in mode_rows})
    for window in windows:
        prop = _find(mode_rows, window, "proportional")
        none = _find(mode_rows, window, "none")
        rows.append({
            "window": window,
            "proportional_fills": prop["fills"],
            "none_fills": none["fills"],
            "fill_ratio_none_over_proportional": _ratio(none["fills"], prop["fills"]),
            "proportional_orders_per_fill": prop["orders_per_fill"],
            "none_orders_per_fill": none["orders_per_fill"],
            "orders_per_fill_ratio_none_over_proportional": _ratio(
                none["orders_per_fill"], prop["orders_per_fill"]
            ),
            "proportional_maker_pct": prop["maker_pct"],
            "none_maker_pct": none["maker_pct"],
            "proportional_postonly_rejects": prop["postonly_rejects"],
            "none_postonly_rejects": none["postonly_rejects"],
            "proportional_matched_net_pnl": prop["matched_net_pnl"],
            "none_matched_net_pnl": none["matched_net_pnl"],
            "matched_net_pnl_delta_none_minus_proportional": (
                none["matched_net_pnl"] - prop["matched_net_pnl"]
            ),
            "proportional_residual_inventory_pnl": prop["residual_inventory_pnl"],
            "none_residual_inventory_pnl": none["residual_inventory_pnl"],
            "proportional_net_pnl": prop["net_pnl"],
            "none_net_pnl": none["net_pnl"],
            "net_pnl_delta_none_minus_proportional": none["net_pnl"] - prop["net_pnl"],
            "proportional_matched_break_even_maker_fee_bps": (
                prop["matched_break_even_maker_fee_bps"]
            ),
            "none_matched_break_even_maker_fee_bps": (
                none["matched_break_even_maker_fee_bps"]
            ),
        })
    return rows


def _find(rows: Sequence[dict], window: str, queue_mode: str) -> dict:
    for row in rows:
        if row["window"] == window and row["queue_cancellation_mode"] == queue_mode:
            return row
    raise KeyError((window, queue_mode))


def _ratio(numerator, denominator):
    if numerator is None or denominator in (None, 0, Decimal("0")):
        return None
    return Decimal(numerator) / Decimal(denominator)


def _flags(args, pooled_comparison: dict) -> dict:
    fill_ratio = pooled_comparison["fill_ratio_none_over_proportional"]
    opf_ratio = pooled_comparison["orders_per_fill_ratio_none_over_proportional"]
    far_fewer_fills = (
        fill_ratio is not None
        and fill_ratio < Decimal("1") - args.material_fill_drop_pct / Decimal("100")
    )
    worse_orders_per_fill = (
        opf_ratio is not None
        and opf_ratio > Decimal("1") + args.material_orders_per_fill_worse_pct / Decimal("100")
    )
    stable_negative = (
        pooled_comparison["proportional_matched_net_pnl"] < Decimal("0")
        and pooled_comparison["none_matched_net_pnl"] < Decimal("0")
        and pooled_comparison["proportional_net_pnl"] < Decimal("0")
        and pooled_comparison["none_net_pnl"] < Decimal("0")
    )
    return {
        "none_far_fewer_fills": far_fewer_fills,
        "none_materially_worse_orders_per_fill": worse_orders_per_fill,
        "negative_conclusion_stable_across_queue_modes": stable_negative,
    }


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
        description="Compare baseline results by queue-cancellation mode"
    )
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--strategy", default="microprice")
    parser.add_argument("--starts", nargs="+", default=DEFAULT_STARTS)
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--session-hours", type=int, default=1)
    parser.add_argument("--half-spread", default="2.00")
    parser.add_argument("--requote-interval-ms", type=int, default=5000)
    parser.add_argument("--material-fill-drop-pct", type=Decimal, default=Decimal("25"))
    parser.add_argument("--material-orders-per-fill-worse-pct",
                        type=Decimal, default=Decimal("25"))
    parser.add_argument("--reconciliation-root", type=Path,
                        default=Path("results/markout_reconciliation"))
    parser.add_argument("--fee-break-even-csv", type=Path,
                        default=DEFAULT_FEE_BREAK_EVEN)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    return parser.parse_args()


def main():
    args = parse_args()
    fee_rows = _load_fee_rows(args.fee_break_even_csv)
    mode_rows = _mode_window_rows(args, fee_rows)
    pooled_rows = _pooled_rows(mode_rows, fee_rows)
    all_mode_rows = [*mode_rows, *pooled_rows]
    comparisons = _comparison_rows(all_mode_rows)
    pooled_comparison = [row for row in comparisons if row["window"] == "pooled"][0]
    flags = _flags(args, pooled_comparison)

    run_dir = args.output_root / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(run_dir / "queue_mode_rows.csv", all_mode_rows)
    _write_csv(run_dir / "queue_sensitivity_comparison.csv", comparisons)

    summary = {
        "params": {
            "symbol": args.symbol.lower(),
            "strategy": args.strategy,
            "starts": args.starts,
            "hours": args.hours,
            "session_hours": args.session_hours,
            "half_spread": args.half_spread,
            "requote_interval_ms": args.requote_interval_ms,
            "material_fill_drop_pct": args.material_fill_drop_pct,
            "material_orders_per_fill_worse_pct": (
                args.material_orders_per_fill_worse_pct
            ),
            "fee_break_even_csv": str(args.fee_break_even_csv),
        },
        "flags": flags,
        "pooled_comparison": pooled_comparison,
        "comparison_rows": comparisons,
    }
    with (run_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=_jsonable)

    print("Queue sensitivity")
    print(f"  Fill ratio none/proportional: "
          f"{_fmt(pooled_comparison['fill_ratio_none_over_proportional'])}")
    print(f"  Orders/fill ratio none/proportional: "
          f"{_fmt(pooled_comparison['orders_per_fill_ratio_none_over_proportional'])}")
    print(f"  Negative conclusion stable: "
          f"{flags['negative_conclusion_stable_across_queue_modes']}")
    print(f"  Far fewer fills under none: {flags['none_far_fewer_fills']}")
    print(f"  Worse orders/fill under none: {flags['none_materially_worse_orders_per_fill']}")
    print(f"\nWrote queue sensitivity artifacts to {run_dir}")


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, Decimal):
        return f"{value:.4f}"
    return str(value)


if __name__ == "__main__":
    main()
