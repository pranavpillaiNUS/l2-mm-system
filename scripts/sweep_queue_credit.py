"""Run Phase C queue-credit and latency stress replays for a panel."""

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from src.execution.queue_credit import parse_queue_credit, queue_credit_suffix


def _load_starts(path: Path) -> list[datetime]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return [datetime.fromisoformat(row["start"]) for row in csv.DictReader(f)]


def _status_path(status_dir: Path, step: str) -> Path:
    safe = step.replace(":", "").replace("/", "_").replace(" ", "_")
    return status_dir / f"{safe}.json"


def _command_sha256(command: list[str]) -> str:
    payload = json.dumps(command, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _complete(status_dir: Path, step: str, command: list[str] | None = None) -> bool:
    path = _status_path(status_dir, step)
    if not path.exists():
        return False
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return (
        payload.get("status") == "completed"
        and (
            command is None
            or payload.get("command_sha256") == _command_sha256(command)
        )
    )


def _run(args, step: str, command: list[str]) -> None:
    if _complete(args.status_dir, step, command):
        print(f"[skip] {step}")
        return
    print(f"[run] {step}")
    print("  " + " ".join(command))
    if args.dry_run:
        return
    subprocess.run(command, check=True)
    args.status_dir.mkdir(parents=True, exist_ok=True)
    with _status_path(args.status_dir, step).open("w", encoding="utf-8") as f:
        json.dump({
            "step": step,
            "status": "completed",
            "command": command,
            "command_sha256": _command_sha256(command),
        }, f, indent=2)


def _start_arg(start: datetime) -> str:
    return start.strftime("%Y-%m-%dT%H")


def _run_dir_name(args, start: datetime, credit: str) -> str:
    run_id = (
        f"btcusdt_microprice_{start.strftime('%Y%m%d_%H')}_"
        f"{args.hours}h_{args.hours // args.session_hours}sessions_"
        f"hs{args.half_spread}_rq{args.requote_interval_ms}"
    )
    return f"{run_id}{queue_credit_suffix(credit)}"


def _can_reuse_phase_a(args, start: datetime, credit: str, latency: int) -> bool:
    return (
        parse_queue_credit(credit) in {parse_queue_credit("0"), parse_queue_credit("1")}
        and latency == args.phase_a_latency_ms
        and (
            args.phase_a_reconciliation_root
            / _run_dir_name(args, start, credit)
            / "summary.json"
        ).exists()
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Sweep queue credit over V2 panel")
    parser.add_argument("--windows-csv", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2/windows.csv"))
    parser.add_argument("--output-root", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2/queue_credit_sweep"))
    parser.add_argument("--status-dir", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2/status_queue_credit"))
    parser.add_argument("--phase-a-reconciliation-root", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2/"
                                     "markout_reconciliation"))
    parser.add_argument("--phase-a-latency-ms", type=int, default=10)
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
    starts = _load_starts(args.windows_csv)
    combos = set()
    for credit in args.queue_credits:
        for latency in args.latencies_ms:
            combos.add((credit, latency))
    for credit in ("0.0", "1.0"):
        for latency in args.endpoint_latencies_ms:
            combos.add((credit, latency))

    rows = []
    for credit, latency in sorted(combos, key=lambda item: (float(item[0]), item[1])):
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
                "--start", _start_arg(start),
                "--end", _start_arg(end),
                "--session-hours", str(args.session_hours),
                "--half-spread", args.half_spread,
                "--order-qty", args.order_qty,
                "--max-position", args.max_position,
                "--requote-interval-ms", str(args.requote_interval_ms),
                "--latency-ms", str(latency),
                "--maker-bps", str(args.maker_bps),
                "--taker-bps", str(args.taker_bps),
                "--queue-cancellation-credit", credit,
                "--output-dir", str(recon_root),
            ]
            rows.append({
                "queue_cancellation_credit": credit,
                "latency_ms": latency,
                "start": start.isoformat(),
                "status_step": step,
                "reconciliation_root": str(recon_root),
                "source": "phase_a_reuse" if reuse_phase_a else "phase_c_replay",
            })
            if reuse_phase_a:
                print(f"[reuse] {step}")
            else:
                _run(args, step, command)

    args.output_root.mkdir(parents=True, exist_ok=True)
    runs_path = args.output_root / "queue_credit_sweep_runs.csv"
    with runs_path.open(
        "w", encoding="utf-8", newline=""
    ) as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote sweep manifest to {runs_path}")
    if not args.dry_run:
        subprocess.run([
            sys.executable,
            "scripts/summarize_queue_credit_sweep.py",
            "--runs-csv", str(runs_path),
            "--output-root", str(args.output_root / "summary"),
        ], check=True)


if __name__ == "__main__":
    main()
