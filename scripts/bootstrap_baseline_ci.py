"""
Bootstrap passive baseline confidence intervals from reconciliation artifacts.

Example:
    env PYTHONPATH=. python scripts/bootstrap_baseline_ci.py
"""
import argparse
import csv
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from src.analysis.bootstrap import BootstrapResult, bootstrap_mean_ci
from src.execution.queue_credit import (
    credit_from_legacy_mode,
    legacy_mode_from_credit,
    parse_queue_credit,
)


def _d(value) -> Decimal:
    if value == "":
        raise ValueError("empty string cannot be converted to Decimal")
    return Decimal(str(value))


def _maybe_d(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return _d(value)


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, BootstrapResult):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _matches_params(summary: dict, args) -> bool:
    params = summary["params"]
    run_credit = _summary_queue_credit(params)
    return (
        params["symbol"] == args.symbol.lower()
        and params["strategy"] == args.strategy
        and params["half_spread"] == args.half_spread
        and int(params["requote_interval_ms"]) == args.requote_interval_ms
        and int(params["latency_ms"]) == args.latency_ms
        and int(params["jitter_ms"]) == args.jitter_ms
        and int(params["session_hours"]) == args.session_hours
        and int(params.get("maker_bps", 2)) == args.maker_bps
        and int(params.get("taker_bps", 5)) == args.taker_bps
        and run_credit == args.queue_cancellation_credit
    )


def _load_runs(args) -> list[dict]:
    runs = []
    for path in sorted(args.results_root.glob("*/summary.json")):
        with path.open("r", encoding="utf-8") as f:
            summary = json.load(f)
        if _matches_params(summary, args):
            runs.append({"path": path, "run_dir": path.parent, "summary": summary})
    return runs


def _session_records(runs: Iterable[dict]) -> list[dict]:
    records = []
    for run in runs:
        for session in run["summary"]["sessions"]:
            pnl = session["pnl"]
            recon = session["reconciliation"]
            hold = session["hold_time"]
            fills = Decimal(session["fills"])
            orders = Decimal(session["orders_submitted"])
            matched_inventory = recon.get("matched_inventory_pnl")
            inventory_pnl = _d(pnl["inventory_pnl"])
            matched_inventory_pnl = _maybe_d(matched_inventory)
            residual_pnl = (
                inventory_pnl - matched_inventory_pnl
                if matched_inventory_pnl is not None else None
            )
            records.append({
                "run": run["run_dir"].name,
                "window": session["window"],
                "net_pnl": _d(pnl["net_pnl"]),
                "matched_net_pnl": _d(hold["matched_net_pnl"]),
                "matched_gross_pnl": _d(hold["matched_gross_pnl"]),
                "matched_fees": _d(hold["matched_fees"]),
                "residual_inventory_pnl": residual_pnl,
                "abs_residual_inventory": abs(_d(hold["residual_inventory"])),
                "orders_per_fill": orders / fills if fills else None,
                "fills": fills,
            })
    return records


def _window_records(runs: Iterable[dict]) -> list[dict]:
    records = []
    for run in runs:
        agg = run["summary"]["aggregate"]
        fills = Decimal(agg["fills"])
        orders = Decimal(agg["orders_submitted"])
        residual_pnl = _maybe_d(agg["residual_inventory_pnl"])
        records.append({
            "run": run["run_dir"].name,
            "net_pnl": _d(agg["net_pnl"]),
            "matched_net_pnl": _d(agg["matched_net_pnl"]),
            "matched_gross_pnl": _d(agg["matched_realized_pnl"]),
            "matched_fees": _d(agg["matched_fees"]),
            "residual_inventory_pnl": residual_pnl,
            "abs_residual_inventory": _d(agg["residual_inventory_abs_avg"]),
            "orders_per_fill": orders / fills if fills else None,
            "fills": fills,
        })
    return records


def _lot_records(runs: Iterable[dict]) -> list[dict]:
    records = []
    for run in runs:
        path = run["run_dir"] / "matched_lots.csv"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                qty = _d(row["quantity"])
                net_pnl = _d(row["net_pnl"])
                records.append({
                    "run": run["run_dir"].name,
                    "window": row["session"],
                    "net_pnl": net_pnl,
                    "gross_pnl": _d(row["realized_pnl"]),
                    "fees": _d(row["total_fees"]),
                    "hold_time_ms": _d(row["hold_time_ms"]),
                    "net_pnl_per_btc": net_pnl / qty if qty else None,
                })
    return records


def _metric_values(records: list[dict], metric: str) -> list[Decimal]:
    return [row[metric] for row in records if row.get(metric) is not None]


def _bootstrap_group(records: list[dict], unit: str, metrics: list[str], args) -> dict:
    return {
        metric: bootstrap_mean_ci(
            _metric_values(records, metric),
            metric=metric,
            unit=unit,
            iterations=args.iterations,
            seed=args.seed,
        )
        for metric in metrics
    }


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, Decimal):
        return f"{value:.6f}"
    return str(value)


def _print_ci_table(title: str, rows: dict[str, BootstrapResult]) -> None:
    print(title)
    print(f"{'metric':<28} {'n':>5} {'mean':>14} {'ci_low':>14} {'ci_high':>14}")
    for metric, result in rows.items():
        print(
            f"{metric:<28} {result.n:>5} "
            f"{_fmt(result.mean):>14} {_fmt(result.ci_low):>14} "
            f"{_fmt(result.ci_high):>14}"
        )
    print()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Bootstrap baseline CIs from markout reconciliation outputs"
    )
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--strategy", default="microprice")
    parser.add_argument("--half-spread", default="2.00")
    parser.add_argument("--requote-interval-ms", type=int, default=5000)
    parser.add_argument("--latency-ms", type=int, default=10)
    parser.add_argument("--jitter-ms", type=int, default=0)
    parser.add_argument("--maker-bps", type=int, default=2)
    parser.add_argument("--taker-bps", type=int, default=5)
    parser.add_argument("--queue-cancellation-credit", default="1.0",
                        help="Cancellation-driven queue credit in [0.0, 1.0]")
    parser.add_argument("--queue-cancellation-mode",
                        choices=["proportional", "none"],
                        help=argparse.SUPPRESS)
    parser.add_argument("--session-hours", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--results-root", type=Path,
                        default=Path("results/markout_reconciliation"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path("results/baseline_ci"))
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


def _summary_queue_credit(params: dict) -> Decimal:
    if "queue_cancellation_credit" in params:
        return parse_queue_credit(params["queue_cancellation_credit"])
    return credit_from_legacy_mode(params.get("queue_cancellation_mode", "proportional"))


def main():
    args = parse_args()
    runs = _load_runs(args)
    if not runs:
        raise SystemExit("No matching reconciliation summaries found.")

    sessions = _session_records(runs)
    windows = _window_records(runs)
    lots = _lot_records(runs)

    session_metrics = [
        "net_pnl",
        "matched_net_pnl",
        "residual_inventory_pnl",
        "abs_residual_inventory",
        "orders_per_fill",
    ]
    window_metrics = session_metrics
    lot_metrics = ["net_pnl", "gross_pnl", "fees", "net_pnl_per_btc", "hold_time_ms"]

    report = {
        "params": {
            "symbol": args.symbol.lower(),
            "strategy": args.strategy,
            "half_spread": args.half_spread,
            "requote_interval_ms": args.requote_interval_ms,
            "latency_ms": args.latency_ms,
            "jitter_ms": args.jitter_ms,
            "maker_bps": args.maker_bps,
            "taker_bps": args.taker_bps,
            "queue_cancellation_credit": args.queue_cancellation_credit,
            "legacy_queue_cancellation_mode": legacy_mode_from_credit(
                args.queue_cancellation_credit
            ),
            "session_hours": args.session_hours,
            "iterations": args.iterations,
            "seed": args.seed,
        },
        "run_dirs": [run["run_dir"].name for run in runs],
        "counts": {
            "windows": len(windows),
            "sessions": len(sessions),
            "matched_lots": len(lots),
        },
        "ci": {
            "window": _bootstrap_group(windows, "5h_window", window_metrics, args),
            "session": _bootstrap_group(sessions, "1h_session", session_metrics, args),
            "matched_lot": _bootstrap_group(lots, "matched_lot", lot_metrics, args),
        },
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    run_id = (
        f"{args.symbol.lower()}_{args.strategy}_hs{args.half_spread}_"
        f"rq{args.requote_interval_ms}_baseline_ci"
    )
    if args.queue_cancellation_credit != Decimal("1"):
        run_id = f"{run_id}_qc{args.queue_cancellation_credit.normalize()}"
    output_path = args.output_dir / f"{run_id}.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=_jsonable)

    print("Baseline bootstrap CI")
    print(f"  Runs:         {len(windows)}")
    print(f"  Sessions:     {len(sessions)}")
    print(f"  Matched lots: {len(lots)}")
    print(f"  Output:       {output_path}")
    print()
    _print_ci_table("Window-level bootstrap", report["ci"]["window"])
    _print_ci_table("Session-level bootstrap", report["ci"]["session"])
    _print_ci_table("Matched-lot bootstrap", report["ci"]["matched_lot"])


if __name__ == "__main__":
    main()
