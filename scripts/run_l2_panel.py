"""Resumable runner for the V2 L2 research panel."""

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from src.execution.queue_credit import queue_credit_suffix


def _load_window_starts(path: Path) -> list[datetime]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [datetime.fromisoformat(row["start"]) for row in rows]


def _status_path(status_dir: Path, step: str) -> Path:
    safe = step.replace(":", "").replace("/", "_").replace(" ", "_")
    return status_dir / f"{safe}.json"


def _is_complete(status_dir: Path, step: str) -> bool:
    path = _status_path(status_dir, step)
    if not path.exists():
        return False
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("status") == "completed"


def _write_status(status_dir: Path, step: str, status: str, command: list[str]) -> None:
    status_dir.mkdir(parents=True, exist_ok=True)
    with _status_path(status_dir, step).open("w", encoding="utf-8") as f:
        json.dump({"step": step, "status": status, "command": command}, f, indent=2)


def _run_step(args, step: str, command: list[str]) -> None:
    if _is_complete(args.status_dir, step):
        print(f"[skip] {step}")
        return
    print(f"[run] {step}")
    print("  " + " ".join(command))
    if not args.dry_run:
        subprocess.run(command, check=True)
    _write_status(args.status_dir, step, "completed", command)


def _window_end(start: datetime, hours: int) -> datetime:
    return start + timedelta(hours=hours)


def _start_arg(start: datetime) -> str:
    return start.strftime("%Y-%m-%dT%H")


def _fill_toxicity_rows_path(args, starts: list[datetime]) -> Path:
    first = starts[0].strftime("%Y%m%d_%H")
    last = starts[-1].strftime("%Y%m%d_%H")
    run_id = (
        f"{args.symbol}_{args.strategy}_hs{args.half_spread}_"
        f"rq{args.requote_interval_ms}_{first}_to_{last}_"
        f"{len(starts)}blocks"
    )
    run_id = f"{run_id}{queue_credit_suffix(args.queue_cancellation_credit)}"
    return args.output_root / "microprice_fill_toxicity" / run_id / "fill_toxicity_rows.csv"


def _phase_a(args, starts: list[datetime]) -> None:
    recon_root = args.output_root / "markout_reconciliation"
    for start in starts:
        end = _window_end(start, args.hours)
        command = [
            sys.executable, "scripts/analyze_markout_reconciliation.py",
            "--start", _start_arg(start),
            "--end", _start_arg(end),
            "--session-hours", str(args.session_hours),
            "--half-spread", args.half_spread,
            "--order-qty", args.order_qty,
            "--max-position", args.max_position,
            "--requote-interval-ms", str(args.requote_interval_ms),
            "--latency-ms", str(args.latency_ms),
            "--jitter-ms", str(args.jitter_ms),
            "--maker-bps", str(args.maker_bps),
            "--taker-bps", str(args.taker_bps),
            "--queue-cancellation-credit", args.queue_cancellation_credit,
            "--output-dir", str(recon_root),
        ]
        _run_step(args, f"reconcile_{start.isoformat()}", command)

    start_values = [_start_arg(start) for start in starts]
    baseline_ci = (
        args.output_root / "baseline_ci" /
        f"{args.symbol}_{args.strategy}_hs{args.half_spread}_"
        f"rq{args.requote_interval_ms}_baseline_ci.json"
    )
    _run_step(args, "bootstrap_ci", [
        sys.executable, "scripts/bootstrap_baseline_ci.py",
        "--results-root", str(recon_root),
        "--output-dir", str(args.output_root / "baseline_ci"),
        "--half-spread", args.half_spread,
        "--requote-interval-ms", str(args.requote_interval_ms),
        "--latency-ms", str(args.latency_ms),
        "--jitter-ms", str(args.jitter_ms),
        "--maker-bps", str(args.maker_bps),
        "--taker-bps", str(args.taker_bps),
        "--queue-cancellation-credit", args.queue_cancellation_credit,
    ])
    _run_step(args, "microprice_signal", [
        sys.executable, "scripts/analyze_microprice_signal.py",
        "--starts", *start_values,
        "--hours", str(args.hours),
        "--queue-cancellation-credit", args.queue_cancellation_credit,
        "--output-dir", str(args.output_root / "microprice_signal"),
    ])
    _run_step(args, "microprice_fill_toxicity", [
        sys.executable, "scripts/analyze_microprice_fill_toxicity.py",
        "--starts", *start_values,
        "--hours", str(args.hours),
        "--session-hours", str(args.session_hours),
        "--half-spread", args.half_spread,
        "--order-qty", args.order_qty,
        "--max-position", args.max_position,
        "--requote-interval-ms", str(args.requote_interval_ms),
        "--latency-ms", str(args.latency_ms),
        "--jitter-ms", str(args.jitter_ms),
        "--maker-bps", str(args.maker_bps),
        "--taker-bps", str(args.taker_bps),
        "--queue-cancellation-credit", args.queue_cancellation_credit,
        "--output-dir", str(args.output_root / "microprice_fill_toxicity"),
    ])
    _run_step(args, "fee_break_even", [
        sys.executable, "scripts/analyze_fee_break_even.py",
        "--starts", *start_values,
        "--hours", str(args.hours),
        "--session-hours", str(args.session_hours),
        "--half-spread", args.half_spread,
        "--requote-interval-ms", str(args.requote_interval_ms),
        "--maker-bps", str(args.maker_bps),
        "--queue-credits", args.queue_cancellation_credit,
        "--reconciliation-root", str(recon_root),
        "--output-root", str(args.output_root / "fee_break_even"),
        "--run-id", args.run_id,
    ])
    _run_step(args, "same_ms_audit", [
        sys.executable, "scripts/audit_same_ms_attribution.py",
        "--starts", *start_values,
        "--hours", str(args.hours),
        "--session-hours", str(args.session_hours),
        "--half-spread", args.half_spread,
        "--requote-interval-ms", str(args.requote_interval_ms),
        "--queue-cancellation-credit", args.queue_cancellation_credit,
        "--reconciliation-root", str(recon_root),
        "--output-root", str(args.output_root / "same_ms_audit"),
        "--run-id", args.run_id,
    ])
    _run_step(args, "tail_diagnostics", [
        sys.executable, "scripts/analyze_mm_tail_diagnostics.py",
        "--baseline-ci", str(baseline_ci),
        "--reconciliation-root", str(recon_root),
        "--fill-toxicity-rows", str(_fill_toxicity_rows_path(args, starts)),
        "--output-root", str(args.output_root / "tail_diagnostics"),
        "--run-id", args.run_id,
    ])


def _phase_b(args, starts: list[datetime]) -> None:
    _run_step(args, "ofi_signal", [
        sys.executable, "scripts/analyze_ofi_signal.py",
        "--starts", *[_start_arg(start) for start in starts],
        "--hours", str(args.hours),
        "--session-hours", str(args.session_hours),
        "--half-spread", args.half_spread,
        "--order-qty", args.order_qty,
        "--max-position", args.max_position,
        "--requote-interval-ms", str(args.requote_interval_ms),
        "--latency-ms", str(args.latency_ms),
        "--jitter-ms", str(args.jitter_ms),
        "--maker-bps", str(args.maker_bps),
        "--taker-bps", str(args.taker_bps),
        "--queue-cancellation-credit", args.queue_cancellation_credit,
        "--output-dir", str(args.output_root / "ofi_signal"),
    ])


def parse_args():
    parser = argparse.ArgumentParser(description="Run the V2 L2 panel")
    parser.add_argument("--windows-csv", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2/windows.csv"))
    parser.add_argument("--output-root", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2"))
    parser.add_argument("--status-dir", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2/status"))
    parser.add_argument("--phase", choices=["a", "b", "all"], default="all")
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--strategy", default="microprice")
    parser.add_argument("--run-id", default="btcusdt_microprice_hs2.00_rq5000_panel24")
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--session-hours", type=int, default=1)
    parser.add_argument("--half-spread", default="2.00")
    parser.add_argument("--order-qty", default="0.001")
    parser.add_argument("--max-position", default="0.01")
    parser.add_argument("--requote-interval-ms", type=int, default=5000)
    parser.add_argument("--latency-ms", type=int, default=10)
    parser.add_argument("--jitter-ms", type=int, default=0)
    parser.add_argument("--maker-bps", type=int, default=2)
    parser.add_argument("--taker-bps", type=int, default=5)
    parser.add_argument("--queue-cancellation-credit", default="1.0")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    starts = _load_window_starts(args.windows_csv)
    if args.phase in ("a", "all"):
        _phase_a(args, starts)
    if args.phase in ("b", "all"):
        _phase_b(args, starts)


if __name__ == "__main__":
    main()
