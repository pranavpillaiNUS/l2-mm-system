"""
Bootstrap passive baseline confidence intervals from reconciliation artifacts.

Example:
    env PYTHONPATH=. python scripts/bootstrap_baseline_ci.py
"""
import argparse
import csv
import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from src.analysis.bootstrap import BootstrapResult, bootstrap_mean_ci
from src.execution.provenance import (
    guard_event_driven_output_path,
    require_event_driven_provenance,
)
from src.execution.queue_credit import (
    credit_from_legacy_mode,
    legacy_mode_from_credit,
    parse_queue_credit,
)
from src.execution.simulator import (
    EQUAL_TIMESTAMP_POLICY,
    EXECUTION_MODEL_VERSION,
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


def _expected_provenance(args) -> dict:
    return {
        "execution_model_version": args.execution_model_version,
        "equal_timestamp_policy": EQUAL_TIMESTAMP_POLICY,
        "trade_gap_policy": args.trade_gap_policy,
        "entry_latency_ms": args.latency_ms,
        "entry_jitter_ms": args.jitter_ms,
        "cancel_latency_ms": args.cancel_latency_ms,
        "cancel_jitter_ms": args.cancel_jitter_ms,
        "latency_seed": args.latency_seed,
        "post_only": True,
        "queue_cancellation_credit": format(
            args.queue_cancellation_credit.normalize(), "f"
        ),
    }


def _is_exact_int(value, expected: int) -> bool:
    """Reject JSON booleans, floats, and strings masquerading as integers."""
    return type(value) is int and value == expected


def _parse_start(value, *, label: str) -> datetime:
    if type(value) is not str:
        raise ValueError(f"{label} must be an ISO-8601 string")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid ISO-8601 datetime: {value!r}") from exc


def _required_run_artifact(run_dir: Path, filename: str) -> Path:
    """Return a direct regular-file artifact without following symlinks."""
    path = run_dir / filename
    if path.is_symlink():
        raise ValueError(f"expected reconciliation artifact cannot be a symlink: {path}")
    resolved_run_dir = run_dir.resolve(strict=False)
    resolved_path = path.resolve(strict=False)
    if resolved_path.parent != resolved_run_dir:
        raise ValueError(
            f"expected reconciliation artifact escapes its run directory: {path}"
        )
    if not path.is_file():
        raise ValueError(f"expected reconciliation artifact is missing: {path}")
    return path


def _matches_params(
    summary: dict,
    args,
    *,
    expected_start: datetime | None = None,
) -> bool:
    try:
        params = summary["params"]
        if not isinstance(params, dict):
            return False
        provenance = require_event_driven_provenance(summary)
        run_credit = _summary_queue_credit(params)
        expected_provenance = _expected_provenance(args)
        provenance_credit = parse_queue_credit(
            provenance["queue_cancellation_credit"]
        )
        start_matches = (
            expected_start is None
            or _parse_start(params["start"], label="summary start")
            == expected_start
        )
        return (
            all(
                provenance.get(key) == value
                for key, value in expected_provenance.items()
                if key != "queue_cancellation_credit"
            )
            and provenance_credit == args.queue_cancellation_credit
            and params["symbol"] == args.symbol.lower()
            and params["strategy"] == args.strategy
            and Decimal(str(params["half_spread"])) == Decimal(args.half_spread)
            and Decimal(str(params["order_qty"])) == Decimal(args.order_qty)
            and Decimal(str(params["max_position"])) == Decimal(args.max_position)
            and _is_exact_int(
                params["requote_interval_ms"], args.requote_interval_ms
            )
            and _is_exact_int(params["latency_ms"], args.latency_ms)
            and _is_exact_int(params["jitter_ms"], args.jitter_ms)
            and _is_exact_int(
                params["cancel_latency_ms"], args.cancel_latency_ms
            )
            and _is_exact_int(
                params["cancel_jitter_ms"], args.cancel_jitter_ms
            )
            and _is_exact_int(params["hours"], args.hours)
            and _is_exact_int(
                params["sessions"], args.hours // args.session_hours
            )
            and _is_exact_int(params["session_hours"], args.session_hours)
            and _is_exact_int(params["maker_bps"], args.maker_bps)
            and _is_exact_int(params["taker_bps"], args.taker_bps)
            and params["trade_gap_policy"] == args.trade_gap_policy
            and run_credit == args.queue_cancellation_credit
            and start_matches
        )
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return False


def _load_runs(args) -> list[dict]:
    expected_run_dirs = getattr(args, "expected_run_dirs", None)
    if expected_run_dirs:
        expected_starts = getattr(args, "expected_starts", None)
        if not expected_starts or len(expected_starts) != len(expected_run_dirs):
            raise ValueError(
                "expected run-dir allowlist requires one aligned --expected-starts value"
            )
        if len(expected_run_dirs) != len(set(expected_run_dirs)):
            raise ValueError("expected run-dir allowlist contains duplicates")
        normalized_starts = [
            _parse_start(value, label="expected start")
            for value in expected_starts
        ]
        if len(normalized_starts) != len(set(normalized_starts)):
            raise ValueError("expected start allowlist contains duplicates")
        runs = []
        root = args.results_root.resolve(strict=False)
        for run_dir, expected_start in zip(expected_run_dirs, normalized_starts):
            component = Path(run_dir)
            if (
                not run_dir
                or run_dir in {".", ".."}
                or component.is_absolute()
                or len(component.parts) != 1
            ):
                raise ValueError(
                    f"expected run-dir must be a single directory name: {run_dir!r}"
                )
            unresolved_run_dir = args.results_root / run_dir
            resolved_run_dir = unresolved_run_dir.resolve(strict=False)
            if root not in resolved_run_dir.parents or unresolved_run_dir.is_symlink():
                raise ValueError(
                    f"expected run-dir must remain directly beneath results root: "
                    f"{run_dir!r}"
                )
            path = _required_run_artifact(unresolved_run_dir, "summary.json")
            matched_lots_path = _required_run_artifact(
                unresolved_run_dir, "matched_lots.csv"
            )
            with path.open("r", encoding="utf-8") as f:
                summary = json.load(f)
            if not _matches_params(
                summary,
                args,
                expected_start=expected_start,
            ):
                raise ValueError(
                    f"expected reconciliation summary has incompatible provenance "
                    f"or parameters: {path}"
                )
            runs.append({
                "path": path,
                "matched_lots_path": matched_lots_path,
                "run_dir": path.parent,
                "summary": summary,
            })
        return runs

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
        path = run.get("matched_lots_path", run["run_dir"] / "matched_lots.csv")
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
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--order-qty", default="0.001")
    parser.add_argument("--max-position", default="0.01")
    parser.add_argument("--half-spread", default="2.00")
    parser.add_argument("--requote-interval-ms", type=int, default=5000)
    parser.add_argument("--latency-ms", type=int, default=10)
    parser.add_argument("--jitter-ms", type=int, default=0)
    parser.add_argument("--cancel-latency-ms", type=int)
    parser.add_argument("--cancel-jitter-ms", type=int)
    parser.add_argument("--latency-seed", type=int, default=42)
    parser.add_argument("--execution-model-version", default=EXECUTION_MODEL_VERSION)
    parser.add_argument("--maker-bps", type=int, default=2)
    parser.add_argument("--taker-bps", type=int, default=5)
    parser.add_argument("--queue-cancellation-credit", default="1.0",
                        help="Cancellation-driven queue credit in [0.0, 1.0]")
    parser.add_argument("--queue-cancellation-mode",
                        choices=["proportional", "none"],
                        help=argparse.SUPPRESS)
    parser.add_argument("--session-hours", type=int, default=1)
    parser.add_argument(
        "--trade-gap-policy",
        choices=["ignore", "pause_until_snapshot"],
        default="pause_until_snapshot",
    )
    parser.add_argument("--iterations", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--results-root", type=Path,
                        default=Path(
                            "results/event_driven_v2/markout_reconciliation"
                        ))
    parser.add_argument(
        "--expected-run-dirs",
        nargs="+",
        help=(
            "Exact reconciliation run-directory allowlist. When supplied, "
            "matching stale runs below --results-root are ignored and every "
            "listed run must exist with compatible provenance."
        ),
    )
    parser.add_argument(
        "--expected-starts",
        nargs="+",
        help="Starts aligned one-for-one with --expected-run-dirs",
    )
    parser.add_argument("--output-dir", type=Path,
                        default=Path("results/event_driven_v2/baseline_ci"))
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


def _summary_queue_credit(params: dict) -> Decimal:
    if "queue_cancellation_credit" in params:
        return parse_queue_credit(params["queue_cancellation_credit"])
    return credit_from_legacy_mode(params.get("queue_cancellation_mode", "proportional"))


def main():
    args = parse_args()
    guard_event_driven_output_path(args.output_dir)
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
        "execution_provenance": _expected_provenance(args),
        "params": {
            "symbol": args.symbol.lower(),
            "strategy": args.strategy,
            "hours": args.hours,
            "order_qty": args.order_qty,
            "max_position": args.max_position,
            "half_spread": args.half_spread,
            "requote_interval_ms": args.requote_interval_ms,
            "latency_ms": args.latency_ms,
            "jitter_ms": args.jitter_ms,
            "cancel_latency_ms": args.cancel_latency_ms,
            "cancel_jitter_ms": args.cancel_jitter_ms,
            "latency_seed": args.latency_seed,
            "execution_model_version": args.execution_model_version,
            "equal_timestamp_policy": EQUAL_TIMESTAMP_POLICY,
            "maker_bps": args.maker_bps,
            "taker_bps": args.taker_bps,
            "queue_cancellation_credit": args.queue_cancellation_credit,
            "legacy_queue_cancellation_mode": legacy_mode_from_credit(
                args.queue_cancellation_credit
            ),
            "session_hours": args.session_hours,
            "trade_gap_policy": args.trade_gap_policy,
            "iterations": args.iterations,
            "seed": args.seed,
        },
        "run_dirs": [run["run_dir"].name for run in runs],
        "input_selection": {
            "mode": (
                "expected_run_allowlist"
                if args.expected_run_dirs
                else "compatible_parameter_scan"
            ),
            "expected_run_dirs": args.expected_run_dirs,
            "expected_starts": args.expected_starts,
        },
        "counts": {
            "windows": len(windows),
            "sessions": len(sessions),
            "matched_lots": len(lots),
        },
        "ci": {
            "window": _bootstrap_group(
                windows, f"{args.hours}h_window", window_metrics, args
            ),
            "session": _bootstrap_group(
                sessions, f"{args.session_hours}h_session", session_metrics, args
            ),
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
