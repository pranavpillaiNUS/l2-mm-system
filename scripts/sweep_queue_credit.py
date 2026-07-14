"""Run Phase C queue-credit and latency stress replays for a panel."""

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from scripts.run_l2_panel import (
    DEVELOPMENT_PANEL_SHA256,
    _file_sha256,
    _load_window_starts,
    _reconciliation_output_paths,
    _run_step,
    _verify_raw_inputs,
)
from src.execution.provenance import (
    guard_event_driven_output_path,
    require_event_driven_provenance,
)
from src.execution.queue_credit import parse_queue_credit, queue_credit_suffix
from src.execution.simulator import EQUAL_TIMESTAMP_POLICY, EXECUTION_MODEL_VERSION


def _load_starts(path: Path) -> list[datetime]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return [datetime.fromisoformat(row["start"]) for row in csv.DictReader(f)]


def _start_arg(start: datetime) -> str:
    return start.strftime("%Y-%m-%dT%H")


def _run_dir_name(args, start: datetime, credit: str) -> str:
    run_id = (
        f"{getattr(args, 'symbol', 'btcusdt')}_microprice_"
        f"{start.strftime('%Y%m%d_%H')}_"
        f"{args.hours}h_{args.hours // args.session_hours}sessions_"
        f"hs{args.half_spread}_rq{args.requote_interval_ms}"
    )
    return f"{run_id}{queue_credit_suffix(credit)}"


def _can_reuse_phase_a(args, start: datetime, credit: str, latency: int) -> bool:
    run_dir = (
        args.phase_a_reconciliation_root / _run_dir_name(args, start, credit)
    )
    summary_path = run_dir / "summary.json"
    if not (
        parse_queue_credit(credit) in {parse_queue_credit("0"), parse_queue_credit("1")}
        and latency == args.phase_a_latency_ms
        and run_dir.is_dir()
        and not run_dir.is_symlink()
        and summary_path.is_file()
        and not summary_path.is_symlink()
    ):
        return False
    if run_dir.resolve().parent != args.phase_a_reconciliation_root.resolve():
        return False
    with summary_path.open("r", encoding="utf-8") as f:
        summary = json.load(f)
    try:
        provenance = require_event_driven_provenance(summary)
    except (TypeError, ValueError, ArithmeticError):
        return False
    expected = {
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "equal_timestamp_policy": EQUAL_TIMESTAMP_POLICY,
        "trade_gap_policy": "pause_until_snapshot",
        "entry_latency_ms": args.phase_a_latency_ms,
        "entry_jitter_ms": getattr(args, "phase_a_jitter_ms", 0),
        "cancel_latency_ms": getattr(
            args, "phase_a_cancel_latency_ms", args.phase_a_latency_ms
        ),
        "cancel_jitter_ms": getattr(args, "phase_a_cancel_jitter_ms", 0),
        "latency_seed": getattr(args, "phase_a_latency_seed", 42),
        "post_only": True,
    }
    try:
        params = summary["params"]
        provenance_credit = parse_queue_credit(
            provenance["queue_cancellation_credit"]
        )
        params_match = (
            isinstance(params, dict)
            and params["symbol"] == getattr(args, "symbol", "btcusdt")
            and params["strategy"] == "microprice"
            and datetime.fromisoformat(params["start"]) == start
            and type(params["hours"]) is int
            and params["hours"] == args.hours
            and type(params["sessions"]) is int
            and params["sessions"] == args.hours // args.session_hours
            and type(params["session_hours"]) is int
            and params["session_hours"] == args.session_hours
            and Decimal(str(params["half_spread"])) == Decimal(args.half_spread)
            and Decimal(str(params["order_qty"])) == Decimal(args.order_qty)
            and Decimal(str(params["max_position"])) == Decimal(args.max_position)
            and type(params["requote_interval_ms"]) is int
            and params["requote_interval_ms"] == args.requote_interval_ms
            and type(params["maker_bps"]) is int
            and params["maker_bps"] == args.maker_bps
            and type(params["taker_bps"]) is int
            and params["taker_bps"] == args.taker_bps
            and params["trade_gap_policy"] == "pause_until_snapshot"
        )
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return False
    return (
        all(provenance.get(key) == value for key, value in expected.items())
        and provenance_credit == parse_queue_credit(credit)
        and params_match
        and all(
            path.is_file()
            and not path.is_symlink()
            and path.resolve().parent == run_dir.resolve()
            for path in _reconciliation_output_paths(summary_path)
        )
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Sweep queue credit over the event-driven V3 panel"
    )
    parser.add_argument("--windows-csv", type=Path,
                        default=Path(
                            "results/panels/btcusdt_l2_panel_v2/"
                            "development_windows.csv"
                        ))
    parser.add_argument("--symbol", choices=["btcusdt"], default="btcusdt")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument(
        "--integrity-manifest",
        type=Path,
        default=Path(
            "results/panels/btcusdt_l2_panel_v2/integrity_manifest.json"
        ),
    )
    parser.add_argument("--output-root", type=Path,
                        default=Path(
                            "results/panels/btcusdt_l2_panel_v3_event_driven/"
                            "queue_credit_sweep"
                        ))
    parser.add_argument("--status-dir", type=Path,
                        default=Path(
                            "results/panels/btcusdt_l2_panel_v3_event_driven/"
                            "status_queue_credit"
                        ))
    parser.add_argument("--phase-a-reconciliation-root", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v3_event_driven/"
                                     "markout_reconciliation"))
    parser.add_argument("--phase-a-latency-ms", type=int, default=10)
    parser.add_argument("--phase-a-jitter-ms", type=int, default=0)
    parser.add_argument("--phase-a-cancel-latency-ms", type=int)
    parser.add_argument("--phase-a-cancel-jitter-ms", type=int, default=0)
    parser.add_argument("--phase-a-latency-seed", type=int, default=42)
    parser.add_argument("--queue-credits", nargs="+",
                        default=["0.0", "0.25", "0.5", "0.75", "1.0"])
    parser.add_argument("--latencies-ms", nargs="+", type=int,
                        default=[10])
    parser.add_argument("--endpoint-latencies-ms", nargs="+", type=int,
                        default=[0, 10, 50])
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--session-hours", type=int, default=1)
    parser.add_argument("--half-spread", default="2.00")
    parser.add_argument("--order-qty", default="0.001")
    parser.add_argument("--max-position", default="0.01")
    parser.add_argument("--requote-interval-ms", type=int, default=5000)
    parser.add_argument("--maker-bps", type=int, default=2)
    parser.add_argument("--taker-bps", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if _file_sha256(args.windows_csv) != DEVELOPMENT_PANEL_SHA256:
        raise ValueError(
            "queue-credit development sweep requires the frozen development "
            "panel; sealed holdout execution is not authorized"
        )
    if args.hours <= 0 or args.session_hours <= 0 or args.hours % args.session_hours:
        raise ValueError("hours must be positive and divisible by session-hours")
    if args.phase_a_cancel_latency_ms is None:
        args.phase_a_cancel_latency_ms = args.phase_a_latency_ms
    guard_event_driven_output_path(args.output_root)
    guard_event_driven_output_path(args.status_dir)
    starts = _load_window_starts(args.windows_csv, args.hours)
    _verify_raw_inputs(args, starts)
    parsed_credits = []
    for value in args.queue_credits:
        credit = parse_queue_credit(value)
        if credit not in parsed_credits:
            parsed_credits.append(credit)
    args.queue_credits = [
        format(credit.normalize(), "f") for credit in parsed_credits
    ]
    combos = set()
    for credit in args.queue_credits:
        for latency in args.latencies_ms:
            combos.add((credit, latency))
    for credit in ("0", "1"):
        for latency in args.endpoint_latencies_ms:
            combos.add((credit, latency))

    rows = []
    for credit, latency in sorted(
        combos, key=lambda item: (parse_queue_credit(item[0]), item[1])
    ):
        for start in starts:
            reuse_phase_a = _can_reuse_phase_a(args, start, credit, latency)
            recon_root = (
                args.phase_a_reconciliation_root
                if reuse_phase_a
                else args.output_root / f"qc{credit}_lat{latency}" / "markout_reconciliation"
            )
            end = start + timedelta(hours=args.hours)
            step = f"qc{credit}_lat{latency}_{start.isoformat()}"
            command = [
                sys.executable, "scripts/analyze_markout_reconciliation.py",
                "--symbol", args.symbol,
                "--start", _start_arg(start),
                "--end", _start_arg(end),
                "--session-hours", str(args.session_hours),
                "--half-spread", args.half_spread,
                "--order-qty", args.order_qty,
                "--max-position", args.max_position,
                "--requote-interval-ms", str(args.requote_interval_ms),
                "--latency-ms", str(latency),
                "--jitter-ms", "0",
                "--cancel-latency-ms", str(latency),
                "--cancel-jitter-ms", "0",
                "--maker-bps", str(args.maker_bps),
                "--taker-bps", str(args.taker_bps),
                "--queue-cancellation-credit", credit,
                "--trade-gap-policy", "pause_until_snapshot",
                "--data-root", str(args.data_root),
                "--output-dir", str(recon_root),
            ]
            rows.append({
                "execution_model_version": EXECUTION_MODEL_VERSION,
                "equal_timestamp_policy": EQUAL_TIMESTAMP_POLICY,
                "trade_gap_policy": "pause_until_snapshot",
                "queue_cancellation_credit": credit,
                "latency_ms": latency,
                "entry_jitter_ms": 0,
                "cancel_latency_ms": latency,
                "cancel_jitter_ms": 0,
                "latency_seed": 42,
                "post_only": True,
                "start": start.isoformat(),
                "status_step": step,
                "reconciliation_root": str(recon_root),
                "source": "phase_a_reuse" if reuse_phase_a else "phase_c_replay",
            })
            if reuse_phase_a:
                print(f"[reuse] {step}")
            else:
                summary_path = (
                    recon_root / _run_dir_name(args, start, credit) / "summary.json"
                )
                _run_step(
                    args,
                    step,
                    command,
                    _reconciliation_output_paths(summary_path),
                    [args.windows_csv, args.integrity_manifest],
                )

    if args.dry_run:
        print(f"Dry run: would write sweep manifest beneath {args.output_root}")
        return
    args.output_root.mkdir(parents=True, exist_ok=True)
    runs_path = args.output_root / "queue_credit_sweep_runs.csv"
    with runs_path.open(
        "w", encoding="utf-8", newline=""
    ) as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote sweep manifest to {runs_path}")
    subprocess.run([
        sys.executable,
        "scripts/summarize_queue_credit_sweep.py",
        "--runs-csv", str(runs_path),
        "--output-root", str(args.output_root / "summary"),
    ], check=True)


if __name__ == "__main__":
    main()
