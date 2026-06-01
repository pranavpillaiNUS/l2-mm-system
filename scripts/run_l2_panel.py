"""Resumable runner for the V2 L2 research panel."""

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.execution.queue_credit import parse_queue_credit, queue_credit_suffix


def _load_window_starts(path: Path) -> list[datetime]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [datetime.fromisoformat(row["start"]) for row in rows]


def _status_path(status_dir: Path, step: str) -> Path:
    safe = step.replace(":", "").replace("/", "_").replace(" ", "_")
    return status_dir / f"{safe}.json"


def _command_sha256(command: list[str]) -> str:
    payload = json.dumps(command, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_complete(
    status_dir: Path,
    step: str,
    command: list[str] | None = None,
) -> bool:
    path = _status_path(status_dir, step)
    if not path.exists():
        return False
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if payload.get("status") != "completed":
        return False
    if command is not None and payload.get("command_sha256") != _command_sha256(command):
        return False
    return all(Path(path).exists() for path in payload.get("expected_outputs", []))


def _write_status(
    status_dir: Path,
    step: str,
    status: str,
    command: list[str],
    expected_outputs: list[Path] | None = None,
) -> None:
    status_dir.mkdir(parents=True, exist_ok=True)
    with _status_path(status_dir, step).open("w", encoding="utf-8") as f:
        json.dump({
            "step": step,
            "status": status,
            "command": command,
            "command_sha256": _command_sha256(command),
            "expected_outputs": [
                str(path) for path in (expected_outputs or [])
            ],
        }, f, indent=2)


def _run_step(
    args,
    step: str,
    command: list[str],
    expected_outputs: list[Path] | None = None,
) -> None:
    if _is_complete(args.status_dir, step, command):
        print(f"[skip] {step}")
        return
    print(f"[run] {step}")
    print("  " + " ".join(command))
    if args.dry_run:
        return
    _write_status(args.status_dir, step, "running", command, expected_outputs)
    subprocess.run(command, check=True)
    _write_status(args.status_dir, step, "completed", command, expected_outputs)


def _window_end(start: datetime, hours: int) -> datetime:
    return start + timedelta(hours=hours)


def _start_arg(start: datetime) -> str:
    return start.strftime("%Y-%m-%dT%H")


def _credit_label(credit: str) -> str:
    return f"qc{parse_queue_credit(credit).normalize()}"


def _fill_toxicity_rows_path(args, starts: list[datetime], credit: str) -> Path:
    first = starts[0].strftime("%Y%m%d_%H")
    last = starts[-1].strftime("%Y%m%d_%H")
    run_id = (
        f"{args.symbol}_{args.strategy}_hs{args.half_spread}_"
        f"rq{args.requote_interval_ms}_{first}_to_{last}_"
        f"{len(starts)}blocks"
    )
    run_id = f"{run_id}{queue_credit_suffix(credit)}"
    return args.output_root / "microprice_fill_toxicity" / run_id / "fill_toxicity_rows.csv"


def _baseline_ci_path(args, credit: str) -> Path:
    run_id = (
        f"{args.symbol}_{args.strategy}_hs{args.half_spread}_"
        f"rq{args.requote_interval_ms}_baseline_ci"
    )
    return args.output_root / "baseline_ci" / f"{run_id}{queue_credit_suffix(credit)}.json"


def _reconciliation_summary_path(args, recon_root: Path, start: datetime, credit: str) -> Path:
    run_id = (
        f"{args.symbol}_{args.strategy}_{start.strftime('%Y%m%d_%H')}_"
        f"{args.hours}h_{args.hours // args.session_hours}sessions_"
        f"hs{args.half_spread}_rq{args.requote_interval_ms}"
    )
    return recon_root / f"{run_id}{queue_credit_suffix(credit)}" / "summary.json"


def _phase_a_endpoint(args, starts: list[datetime], credit: str) -> None:
    recon_root = args.output_root / "markout_reconciliation"
    credit_label = _credit_label(credit)
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
            "--queue-cancellation-credit", credit,
            "--output-dir", str(recon_root),
        ]
        _run_step(
            args,
            f"reconcile_{credit_label}_{start.isoformat()}",
            command,
            [_reconciliation_summary_path(args, recon_root, start, credit)],
        )

    start_values = [_start_arg(start) for start in starts]
    baseline_ci = _baseline_ci_path(args, credit)
    _run_step(args, f"bootstrap_ci_{credit_label}", [
        sys.executable, "scripts/bootstrap_baseline_ci.py",
        "--results-root", str(recon_root),
        "--output-dir", str(args.output_root / "baseline_ci"),
        "--half-spread", args.half_spread,
        "--requote-interval-ms", str(args.requote_interval_ms),
        "--latency-ms", str(args.latency_ms),
        "--jitter-ms", str(args.jitter_ms),
        "--maker-bps", str(args.maker_bps),
        "--taker-bps", str(args.taker_bps),
        "--queue-cancellation-credit", credit,
    ], [baseline_ci])
    _run_step(args, f"microprice_fill_toxicity_{credit_label}", [
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
        "--queue-cancellation-credit", credit,
        "--output-dir", str(args.output_root / "microprice_fill_toxicity"),
    ])
    _run_step(args, f"same_ms_audit_{credit_label}", [
        sys.executable, "scripts/audit_same_ms_attribution.py",
        "--starts", *start_values,
        "--hours", str(args.hours),
        "--session-hours", str(args.session_hours),
        "--half-spread", args.half_spread,
        "--requote-interval-ms", str(args.requote_interval_ms),
        "--queue-cancellation-credit", credit,
        "--reconciliation-root", str(recon_root),
        "--output-root", str(args.output_root / "same_ms_audit"),
        "--run-id", f"{args.run_id}{queue_credit_suffix(credit)}",
        "--data-overlap-cache", str(args.output_root / "same_ms_audit" /
                                    "data_overlap_cache.json"),
    ])
    _run_step(args, f"tail_diagnostics_{credit_label}", [
        sys.executable, "scripts/analyze_mm_tail_diagnostics.py",
        "--baseline-ci", str(baseline_ci),
        "--reconciliation-root", str(recon_root),
        "--fill-toxicity-rows", str(_fill_toxicity_rows_path(args, starts, credit)),
        "--output-root", str(args.output_root / "tail_diagnostics"),
        "--run-id", f"{args.run_id}{queue_credit_suffix(credit)}",
    ])


def _phase_a(args, starts: list[datetime]) -> None:
    start_values = [_start_arg(start) for start in starts]
    _run_step(args, "microprice_signal", [
        sys.executable, "scripts/analyze_microprice_signal.py",
        "--starts", *start_values,
        "--hours", str(args.hours),
        "--queue-cancellation-credit", "1.0",
        "--output-dir", str(args.output_root / "microprice_signal"),
    ])
    for credit in args.queue_credits:
        _phase_a_endpoint(args, starts, credit)
    credits_by_value = {
        parse_queue_credit(credit): credit for credit in args.queue_credits
    }
    if Decimal("0") in credits_by_value and Decimal("1") in credits_by_value:
        credit_zero = credits_by_value[Decimal("0")]
        credit_one = credits_by_value[Decimal("1")]
        phase_a_verdict = args.output_root / "phase_a_verdict.json"
        _run_step(args, "phase_a_verdict", [
            sys.executable, "scripts/classify_v2_baseline.py",
            "--endpoint-ci",
            f"0.0={_baseline_ci_path(args, credit_zero)}",
            f"1.0={_baseline_ci_path(args, credit_one)}",
            "--frozen-v1-mean",
            f"0.0={args.frozen_v1_mean_credit0}",
            f"1.0={args.frozen_v1_mean_credit1}",
            "--output", str(phase_a_verdict),
        ], [phase_a_verdict])
    _run_step(args, "fee_break_even", [
        sys.executable, "scripts/analyze_fee_break_even.py",
        "--starts", *start_values,
        "--hours", str(args.hours),
        "--session-hours", str(args.session_hours),
        "--half-spread", args.half_spread,
        "--requote-interval-ms", str(args.requote_interval_ms),
        "--maker-bps", str(args.maker_bps),
        "--queue-credits", *args.queue_credits,
        "--reconciliation-root", str(args.output_root / "markout_reconciliation"),
        "--output-root", str(args.output_root / "fee_break_even"),
        "--run-id", args.run_id,
    ])


def _phase_b(args, starts: list[datetime]) -> None:
    for credit in args.queue_credits:
        _run_step(args, f"ofi_signal_{_credit_label(credit)}", [
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
            "--queue-cancellation-credit", credit,
            "--output-dir", str(args.output_root / "ofi_signal"),
            "--unconditional-cache", str(args.output_root / "ofi_signal" /
                                         "unconditional_cache.json"),
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
    parser.add_argument("--queue-credits", nargs="+", default=["0.0", "1.0"])
    parser.add_argument("--frozen-v1-mean-credit0", default="-1.44134121521000000000")
    parser.add_argument("--frozen-v1-mean-credit1",
                        default="-2.438809811930561920142440165")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    args.queue_credits = [
        str(parse_queue_credit(value)) for value in dict.fromkeys(args.queue_credits)
    ]
    starts = _load_window_starts(args.windows_csv)
    if args.phase in ("a", "all"):
        _phase_a(args, starts)
    if args.phase in ("b", "all"):
        _phase_b(args, starts)


if __name__ == "__main__":
    main()
