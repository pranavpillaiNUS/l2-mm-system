"""Resumable runner for the event-driven V3 L2 development panel."""

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.execution.provenance import (
    guard_event_driven_output_path,
    require_safe_path_component,
)
from src.execution.queue_credit import parse_queue_credit, queue_credit_suffix
from src.replay.depth_parser import SNAPSHOT_TIME_POLICY
from src.replay.book_backend import add_book_arguments, book_provenance_from_args


DEVELOPMENT_PANEL_SHA256 = (
    "c779138fdffb739715c53cfa27c75b3c8ca140bc4f5a6128f60f2251948bd362"
)
INTEGRITY_MANIFEST_SHA256 = (
    "a3a99b0a616abe3bc39e0863ed047f075db9ed5d8118ced57c16140b499d8a61"
)
INTEGRITY_MANIFEST_FILE_SHA256 = (
    "1e5915973af4721b5ddab33f00c5bc9be395428aed3da6a1a054324794a963ee"
)
ARTIFACT_MANIFEST_VERSION = "event_driven_v3_artifact_manifest_v1"
_SOURCE_FINGERPRINT: str | None = None
_FILE_HASH_CACHE: dict[tuple[str, int, int], str] = {}


def _file_sha256(path: Path) -> str:
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    cached = _FILE_HASH_CACHE.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    result = digest.hexdigest()
    _FILE_HASH_CACHE[key] = result
    return result


def _raw_paths(args, start: datetime) -> tuple[Path, Path]:
    stamp = start.strftime("%Y%m%d_%H")
    return (
        args.data_root / "raw" / args.symbol
        / f"{args.symbol}_depth_{stamp}00.jsonl.gz",
        args.data_root / "raw" / f"{args.symbol}_trades"
        / f"{args.symbol}_trades_{stamp}00.jsonl.gz",
    )


def _verify_raw_inputs(args, starts: list[datetime]) -> None:
    if _file_sha256(args.integrity_manifest) != INTEGRITY_MANIFEST_FILE_SHA256:
        raise ValueError("integrity manifest file hash does not match frozen input")
    with args.integrity_manifest.open("r", encoding="utf-8") as f:
        manifest = json.load(f)
    if manifest.get("manifest_sha256") != INTEGRITY_MANIFEST_SHA256:
        raise ValueError("integrity manifest identity does not match frozen V3 input")
    rows = {
        datetime.fromisoformat(row["start"]): row
        for row in manifest.get("hours", [])
    }
    for block_start in starts:
        for offset in range(args.hours):
            start = block_start + timedelta(hours=offset)
            row = rows.get(start)
            if row is None or not row.get("valid"):
                raise ValueError(f"selected raw hour is absent or invalid: {start}")
            depth_path, trade_path = _raw_paths(args, start)
            if (
                not depth_path.is_file()
                or not trade_path.is_file()
                or _file_sha256(depth_path) != row.get("depth_sha256")
                or _file_sha256(trade_path) != row.get("trade_sha256")
            ):
                raise ValueError(
                    f"raw input no longer matches frozen integrity manifest: {start}"
                )


def _write_artifact_manifest(args, starts: list[datetime]) -> Path:
    """Commit reproducibility identities that ignored resume markers cannot."""
    manifest_path = args.output_root / "ARTIFACT_MANIFEST.json"
    status_root = args.status_dir.resolve(strict=False)
    artifacts: dict[str, str] = {}
    for path in sorted(args.output_root.rglob("*")):
        if (
            not path.is_file()
            or path.is_symlink()
            or path == manifest_path
            or path.name.endswith("_cache.json")
        ):
            continue
        resolved = path.resolve(strict=False)
        if resolved == status_root or status_root in resolved.parents:
            continue
        artifacts[str(path.relative_to(args.output_root))] = _file_sha256(path)

    completed_steps = []
    if args.status_dir.is_dir():
        for path in sorted(args.status_dir.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"invalid status marker in V3 run: {path}")
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            if (
                payload.get("status") == "completed"
                and payload.get("source_fingerprint") == _source_fingerprint()
            ):
                completed_steps.append({
                    "step": payload.get("step"),
                    "command": payload.get("command"),
                    "command_sha256": payload.get("command_sha256"),
                    "input_fingerprints": payload.get("input_fingerprints"),
                    "expected_outputs": payload.get("expected_outputs"),
                    "output_fingerprints": payload.get("output_fingerprints"),
                })

    raw_inputs = []
    for block_start in starts:
        for offset in range(args.hours):
            start = block_start + timedelta(hours=offset)
            depth_path, trade_path = _raw_paths(args, start)
            raw_inputs.append({
                "start": start.isoformat(),
                "depth_path": str(depth_path),
                "depth_sha256": _file_sha256(depth_path),
                "trade_path": str(trade_path),
                "trade_sha256": _file_sha256(trade_path),
            })

    git_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    payload = {
        "manifest_version": ARTIFACT_MANIFEST_VERSION,
        "git_head": git_head,
        "source_fingerprint": _source_fingerprint(),
        "orderbook": book_provenance_from_args(args),
        "execution_model_version": "event_driven_v2",
        "equal_timestamp_policy": "market_data_before_private_actions_v1",
        "snapshot_time_policy": SNAPSHOT_TIME_POLICY,
        "trade_gap_policy": "pause_until_snapshot",
        "development_panel": {
            "path": str(args.windows_csv),
            "sha256": _file_sha256(args.windows_csv),
            "starts": [start.isoformat() for start in starts],
        },
        "raw_integrity_manifest": {
            "path": str(args.integrity_manifest),
            "file_sha256": _file_sha256(args.integrity_manifest),
            "identity_sha256": INTEGRITY_MANIFEST_SHA256,
        },
        "experiment": {
            "symbol": args.symbol,
            "strategy": args.strategy,
            "hours": args.hours,
            "session_hours": args.session_hours,
            "half_spread": args.half_spread,
            "order_qty": args.order_qty,
            "max_position": args.max_position,
            "requote_interval_ms": args.requote_interval_ms,
            "latency_ms": args.latency_ms,
            "jitter_ms": args.jitter_ms,
            "cancel_latency_ms": args.cancel_latency_ms,
            "cancel_jitter_ms": args.cancel_jitter_ms,
            "maker_bps": args.maker_bps,
            "taker_bps": args.taker_bps,
            "queue_credits": args.queue_credits,
        },
        "raw_inputs": raw_inputs,
        "completed_steps": completed_steps,
        "artifacts": artifacts,
    }
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return manifest_path


def _path_fingerprint(path: Path) -> str:
    if not path.exists():
        return "missing"
    if path.is_file():
        return f"file:{_file_sha256(path)}"
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(str(child.relative_to(path)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(_file_sha256(child).encode("ascii"))
        digest.update(b"\0")
    return f"dir:{digest.hexdigest()}"


def _fingerprints(paths: list[Path] | None) -> dict[str, str]:
    return {
        str(path): _path_fingerprint(path)
        for path in (paths or [])
    }


def _source_fingerprint() -> str:
    global _SOURCE_FINGERPRINT
    if _SOURCE_FINGERPRINT is None:
        digest = hashlib.sha256()
        paths = [
            *Path("src").rglob("*.py"),
            *Path("scripts").rglob("*.py"),
        ]
        for path in sorted(paths):
            digest.update(str(path).encode("utf-8"))
            digest.update(b"\0")
            digest.update(_file_sha256(path).encode("ascii"))
            digest.update(b"\0")
        _SOURCE_FINGERPRINT = digest.hexdigest()
    return _SOURCE_FINGERPRINT


def _load_window_starts(path: Path, expected_hours: int) -> list[datetime]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"development-window panel is empty: {path}")
    if any(int(row["hours"]) != expected_hours for row in rows):
        raise ValueError(
            f"development-window duration does not match --hours={expected_hours}"
        )
    starts = [datetime.fromisoformat(row["start"]) for row in rows]
    if len(starts) != len(set(starts)):
        raise ValueError(f"development-window panel contains duplicate starts: {path}")
    return starts


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
    input_paths: list[Path] | None = None,
    expected_outputs: list[Path] | None = None,
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
    if payload.get("source_fingerprint") != _source_fingerprint():
        return False
    if payload.get("input_fingerprints") != _fingerprints(input_paths):
        return False
    current_expected = [str(path) for path in (expected_outputs or [])]
    if payload.get("expected_outputs") != current_expected:
        return False
    expected_outputs = [Path(path) for path in current_expected]
    return (
        all(path.exists() for path in expected_outputs)
        and payload.get("output_fingerprints") == _fingerprints(expected_outputs)
    )


def _write_status(
    status_dir: Path,
    step: str,
    status: str,
    command: list[str],
    expected_outputs: list[Path] | None = None,
    input_paths: list[Path] | None = None,
) -> None:
    status_dir.mkdir(parents=True, exist_ok=True)
    with _status_path(status_dir, step).open("w", encoding="utf-8") as f:
        json.dump({
            "step": step,
            "status": status,
            "command": command,
            "command_sha256": _command_sha256(command),
            "source_fingerprint": _source_fingerprint(),
            "expected_outputs": [
                str(path) for path in (expected_outputs or [])
            ],
            "input_fingerprints": _fingerprints(input_paths),
            "output_fingerprints": (
                _fingerprints(expected_outputs)
                if status == "completed"
                else {}
            ),
        }, f, indent=2)


def _run_step(
    args,
    step: str,
    command: list[str],
    expected_outputs: list[Path] | None = None,
    input_paths: list[Path] | None = None,
) -> None:
    if getattr(args, "book_backend", "python") == "cpp" and command[1] in {
        "scripts/analyze_markout_reconciliation.py",
        "scripts/analyze_microprice_fill_toxicity.py",
        "scripts/analyze_microprice_signal.py",
        "scripts/analyze_ofi_signal.py",
    }:
        command = [*command, "--book-backend", "cpp",
                   "--book-tick-size", args.book_tick_size,
                   "--book-qty-step", args.book_qty_step]
        from src.replay.cpp_orderbook import native_build_info
        input_paths = [*(input_paths or []), Path(native_build_info()["path"])]
    if _is_complete(
        args.status_dir,
        step,
        command,
        input_paths,
        expected_outputs,
    ):
        print(f"[skip] {step}")
        return
    print(f"[run] {step}")
    print("  " + " ".join(command))
    if args.dry_run:
        return
    _write_status(
        args.status_dir,
        step,
        "running",
        command,
        expected_outputs,
        input_paths,
    )
    subprocess.run(command, check=True)
    missing = [path for path in (expected_outputs or []) if not path.exists()]
    if missing:
        raise RuntimeError(
            f"step {step} did not produce expected outputs: "
            + ", ".join(str(path) for path in missing)
        )
    _write_status(
        args.status_dir,
        step,
        "completed",
        command,
        expected_outputs,
        input_paths,
    )


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


def _microprice_signal_summary_path(args, starts: list[datetime]) -> Path:
    first = starts[0].strftime("%Y%m%d_%H")
    last = starts[-1].strftime("%Y%m%d_%H")
    run_id = f"{args.symbol}_{first}_to_{last}_{len(starts)}windows_1000ms"
    return args.output_root / "microprice_signal" / run_id / "summary.json"


def _ofi_summary_path(args, starts: list[datetime], credit: str) -> Path:
    first = starts[0].strftime("%Y%m%d_%H")
    last = starts[-1].strftime("%Y%m%d_%H")
    run_id = (
        f"{args.symbol}_{args.strategy}_ofi_{first}_to_{last}_"
        f"{len(starts)}blocks_1000ms{queue_credit_suffix(credit)}"
    )
    return args.output_root / "ofi_signal" / run_id / "summary.json"


def _baseline_ci_path(args, credit: str, *, root: Path | None = None) -> Path:
    run_id = (
        f"{args.symbol}_{args.strategy}_hs{args.half_spread}_"
        f"rq{args.requote_interval_ms}_baseline_ci"
    )
    output_root = args.output_root if root is None else root
    return output_root / "baseline_ci" / f"{run_id}{queue_credit_suffix(credit)}.json"


def _reconciliation_summary_path(args, recon_root: Path, start: datetime, credit: str) -> Path:
    run_id = (
        f"{args.symbol}_{args.strategy}_{start.strftime('%Y%m%d_%H')}_"
        f"{args.hours}h_{args.hours // args.session_hours}sessions_"
        f"hs{args.half_spread}_rq{args.requote_interval_ms}"
    )
    return recon_root / f"{run_id}{queue_credit_suffix(credit)}" / "summary.json"


def _reconciliation_input_paths(
    args,
    recon_root: Path,
    starts: list[datetime],
    credit: str,
) -> list[Path]:
    paths = []
    for start in starts:
        summary = _reconciliation_summary_path(args, recon_root, start, credit)
        paths.extend(_reconciliation_output_paths(summary))
    return paths


def _reconciliation_output_paths(summary: Path) -> list[Path]:
    run_dir = summary.parent
    return [
        summary,
        run_dir / "matched_lots.csv",
        run_dir / "open_lots.csv",
        run_dir / "pre_fill_drift.csv",
        run_dir / "queue_diagnostics.csv",
        run_dir / "book_spread_by_volatility.csv",
    ]


def _phase_a_endpoint(args, starts: list[datetime], credit: str) -> None:
    recon_root = args.output_root / "markout_reconciliation"
    credit_label = _credit_label(credit)
    expected_run_dirs = [
        _reconciliation_summary_path(args, recon_root, start, credit).parent.name
        for start in starts
    ]
    for start in starts:
        end = _window_end(start, args.hours)
        command = [
            sys.executable, "scripts/analyze_markout_reconciliation.py",
            "--symbol", args.symbol,
            "--strategy", args.strategy,
            "--start", _start_arg(start),
            "--end", _start_arg(end),
            "--session-hours", str(args.session_hours),
            "--half-spread", args.half_spread,
            "--order-qty", args.order_qty,
            "--max-position", args.max_position,
            "--requote-interval-ms", str(args.requote_interval_ms),
            "--latency-ms", str(args.latency_ms),
            "--jitter-ms", str(args.jitter_ms),
            "--cancel-latency-ms", str(args.cancel_latency_ms),
            "--cancel-jitter-ms", str(args.cancel_jitter_ms),
            "--maker-bps", str(args.maker_bps),
            "--taker-bps", str(args.taker_bps),
            "--queue-cancellation-credit", credit,
            "--trade-gap-policy", "pause_until_snapshot",
            "--data-root", str(args.data_root),
            "--output-dir", str(recon_root),
        ]
        summary_path = _reconciliation_summary_path(
            args, recon_root, start, credit
        )
        _run_step(
            args,
            f"reconcile_{credit_label}_{start.isoformat()}",
            command,
            _reconciliation_output_paths(summary_path),
            [args.windows_csv, args.integrity_manifest],
        )

    start_values = [_start_arg(start) for start in starts]
    baseline_ci = _baseline_ci_path(args, credit)
    _run_step(args, f"bootstrap_ci_{credit_label}", [
        sys.executable, "scripts/bootstrap_baseline_ci.py",
        "--symbol", args.symbol,
        "--strategy", args.strategy,
        "--hours", str(args.hours),
        "--order-qty", args.order_qty,
        "--max-position", args.max_position,
        "--results-root", str(recon_root),
        "--output-dir", str(args.output_root / "baseline_ci"),
        "--half-spread", args.half_spread,
        "--requote-interval-ms", str(args.requote_interval_ms),
        "--latency-ms", str(args.latency_ms),
        "--jitter-ms", str(args.jitter_ms),
        "--cancel-latency-ms", str(args.cancel_latency_ms),
        "--cancel-jitter-ms", str(args.cancel_jitter_ms),
        "--maker-bps", str(args.maker_bps),
        "--taker-bps", str(args.taker_bps),
        "--queue-cancellation-credit", credit,
        "--session-hours", str(args.session_hours),
        "--trade-gap-policy", "pause_until_snapshot",
        "--expected-run-dirs", *expected_run_dirs,
        "--expected-starts", *[_start_arg(start) for start in starts],
    ], [baseline_ci], _reconciliation_input_paths(
        args, recon_root, starts, credit
    ))
    toxicity_rows = _fill_toxicity_rows_path(args, starts, credit)
    _run_step(args, f"microprice_fill_toxicity_{credit_label}", [
        sys.executable, "scripts/analyze_microprice_fill_toxicity.py",
        "--symbol", args.symbol,
        "--strategy", args.strategy,
        "--starts", *start_values,
        "--hours", str(args.hours),
        "--session-hours", str(args.session_hours),
        "--half-spread", args.half_spread,
        "--order-qty", args.order_qty,
        "--max-position", args.max_position,
        "--requote-interval-ms", str(args.requote_interval_ms),
        "--latency-ms", str(args.latency_ms),
        "--jitter-ms", str(args.jitter_ms),
        "--cancel-latency-ms", str(args.cancel_latency_ms),
        "--cancel-jitter-ms", str(args.cancel_jitter_ms),
        "--maker-bps", str(args.maker_bps),
        "--taker-bps", str(args.taker_bps),
        "--queue-cancellation-credit", credit,
        "--data-root", str(args.data_root),
        "--output-dir", str(args.output_root / "microprice_fill_toxicity"),
    ], [
        toxicity_rows,
        toxicity_rows.parent / "buckets.csv",
        toxicity_rows.parent / "summary.json",
    ], [args.windows_csv, args.integrity_manifest])
    same_ms_summary = (
        args.output_root / "same_ms_audit"
        / f"{args.run_id}{queue_credit_suffix(credit)}" / "summary.json"
    )
    _run_step(args, f"same_ms_audit_{credit_label}", [
        sys.executable, "scripts/audit_same_ms_attribution.py",
        "--symbol", args.symbol,
        "--strategy", args.strategy,
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
        "--data-root", str(args.data_root),
    ], [
        same_ms_summary,
        same_ms_summary.parent / "window_summary.csv",
        same_ms_summary.parent / "attribution_risk_candidates.csv",
    ], _reconciliation_input_paths(
        args, recon_root, starts, credit
    ))
    tail_summary = (
        args.output_root / "tail_diagnostics"
        / f"{args.run_id}{queue_credit_suffix(credit)}" / "summary.json"
    )
    _run_step(args, f"tail_diagnostics_{credit_label}", [
        sys.executable, "scripts/analyze_mm_tail_diagnostics.py",
        "--baseline-ci", str(baseline_ci),
        "--reconciliation-root", str(recon_root),
        "--fill-toxicity-rows", str(_fill_toxicity_rows_path(args, starts, credit)),
        "--output-root", str(args.output_root / "tail_diagnostics"),
        "--run-id", f"{args.run_id}{queue_credit_suffix(credit)}",
    ], [
        tail_summary,
        tail_summary.parent / "window_summary.csv",
        tail_summary.parent / "fill_tail_rows.csv",
        tail_summary.parent / "matched_lot_tail_rows.csv",
        tail_summary.parent / "cluster_summary.csv",
        tail_summary.parent / "cluster_rows.csv",
    ], [
        baseline_ci,
        toxicity_rows,
        toxicity_rows.parent / "summary.json",
        *_reconciliation_input_paths(args, recon_root, starts, credit),
    ])


def _phase_a(args, starts: list[datetime]) -> None:
    start_values = [_start_arg(start) for start in starts]
    _run_step(args, "microprice_signal", [
        sys.executable, "scripts/analyze_microprice_signal.py",
        "--symbol", args.symbol,
        "--starts", *start_values,
        "--hours", str(args.hours),
        "--queue-cancellation-credit", "1.0",
        "--data-root", str(args.data_root),
        "--output-dir", str(args.output_root / "microprice_signal"),
    ], [
        _microprice_signal_summary_path(args, starts),
        _microprice_signal_summary_path(args, starts).parent / "regressions.csv",
        _microprice_signal_summary_path(args, starts).parent / "signal_buckets.csv",
    ], [args.windows_csv, args.integrity_manifest])
    for credit in args.queue_credits:
        _phase_a_endpoint(args, starts, credit)
    credits_by_value = {
        parse_queue_credit(credit): credit for credit in args.queue_credits
    }
    if Decimal("0") in credits_by_value and Decimal("1") in credits_by_value:
        credit_zero = credits_by_value[Decimal("0")]
        credit_one = credits_by_value[Decimal("1")]
        comparison = args.output_root / "execution_model_comparison.json"
        current_ci_zero = _baseline_ci_path(args, credit_zero)
        current_ci_one = _baseline_ci_path(args, credit_one)
        legacy_ci_zero = _baseline_ci_path(
            args, credit_zero, root=args.legacy_panel_root
        )
        legacy_ci_one = _baseline_ci_path(
            args, credit_one, root=args.legacy_panel_root
        )
        _run_step(args, "execution_model_comparison", [
            sys.executable, "scripts/compare_execution_model_baseline.py",
            "--current-endpoint-ci",
            f"0.0={current_ci_zero}",
            f"1.0={current_ci_one}",
            "--legacy-endpoint-ci",
            f"0.0={legacy_ci_zero}",
            f"1.0={legacy_ci_one}",
            "--development-windows-csv", str(args.windows_csv),
            "--output", str(comparison),
        ], [comparison], [
            current_ci_zero,
            current_ci_one,
            legacy_ci_zero,
            legacy_ci_one,
            args.windows_csv,
        ])
    fee_summary = (
        args.output_root / "fee_break_even" / args.run_id / "summary.json"
    )
    _run_step(args, "fee_break_even", [
        sys.executable, "scripts/analyze_fee_break_even.py",
        "--symbol", args.symbol,
        "--strategy", args.strategy,
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
    ], [
        fee_summary,
        fee_summary.parent / "fee_break_even.csv",
    ], [
        path
        for credit in args.queue_credits
        for path in _reconciliation_input_paths(
            args,
            args.output_root / "markout_reconciliation",
            starts,
            credit,
        )
    ])


def _phase_b(args, starts: list[datetime]) -> None:
    for credit in args.queue_credits:
        _run_step(args, f"ofi_signal_{_credit_label(credit)}", [
            sys.executable, "scripts/analyze_ofi_signal.py",
            "--symbol", args.symbol,
            "--strategy", args.strategy,
            "--starts", *[_start_arg(start) for start in starts],
            "--hours", str(args.hours),
            "--session-hours", str(args.session_hours),
            "--half-spread", args.half_spread,
            "--order-qty", args.order_qty,
            "--max-position", args.max_position,
            "--requote-interval-ms", str(args.requote_interval_ms),
            "--latency-ms", str(args.latency_ms),
            "--jitter-ms", str(args.jitter_ms),
            "--cancel-latency-ms", str(args.cancel_latency_ms),
            "--cancel-jitter-ms", str(args.cancel_jitter_ms),
            "--maker-bps", str(args.maker_bps),
            "--taker-bps", str(args.taker_bps),
            "--queue-cancellation-credit", credit,
            "--data-root", str(args.data_root),
            "--output-dir", str(args.output_root / "ofi_signal"),
            "--unconditional-cache", str(args.output_root / "ofi_signal" /
                                         "unconditional_cache.json"),
        ], [
            _ofi_summary_path(args, starts, credit),
            _ofi_summary_path(args, starts, credit).parent / "regressions.csv",
            _ofi_summary_path(args, starts, credit).parent / "signal_buckets.csv",
            _ofi_summary_path(args, starts, credit).parent / "fill_toxicity_rows.csv",
            _ofi_summary_path(args, starts, credit).parent / "fill_buckets.csv",
        ], [args.windows_csv, args.integrity_manifest])


def parse_args():
    parser = argparse.ArgumentParser(description="Run the event-driven V3 L2 panel")
    parser.add_argument("--windows-csv", type=Path,
                        default=Path(
                            "results/panels/btcusdt_l2_panel_v2/"
                            "development_windows.csv"
                        ))
    parser.add_argument("--output-root", type=Path,
                        default=Path(
                            "results/panels/btcusdt_l2_panel_v3_event_driven"
                        ))
    parser.add_argument("--status-dir", type=Path,
                        default=Path(
                            "results/panels/btcusdt_l2_panel_v3_event_driven/status"
                        ))
    parser.add_argument(
        "--legacy-panel-root",
        type=Path,
        default=Path("results/panels/btcusdt_l2_panel_v2"),
        help="Read-only frozen legacy panel used for descriptive comparison",
    )
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument(
        "--integrity-manifest",
        type=Path,
        default=Path(
            "results/panels/btcusdt_l2_panel_v2/integrity_manifest.json"
        ),
    )
    parser.add_argument("--phase", choices=["a", "b", "all"], default="all")
    parser.add_argument("--symbol", choices=["btcusdt"], default="btcusdt")
    parser.add_argument("--strategy", choices=["microprice"], default="microprice")
    parser.add_argument("--run-id", default="btcusdt_microprice_hs2.00_rq5000_panel24")
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--session-hours", type=int, default=1)
    parser.add_argument("--half-spread", default="2.00")
    parser.add_argument("--order-qty", default="0.001")
    parser.add_argument("--max-position", default="0.01")
    parser.add_argument("--requote-interval-ms", type=int, default=5000)
    parser.add_argument("--latency-ms", type=int, default=10)
    parser.add_argument("--jitter-ms", type=int, default=0)
    parser.add_argument("--cancel-latency-ms", type=int)
    parser.add_argument("--cancel-jitter-ms", type=int)
    parser.add_argument("--maker-bps", type=int, default=2)
    parser.add_argument("--taker-bps", type=int, default=5)
    parser.add_argument("--queue-credits", nargs="+", default=["0.0", "1.0"])
    parser.add_argument("--dry-run", action="store_true")
    add_book_arguments(parser)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.book_backend == "cpp":
        if args.output_root == Path("results/panels/btcusdt_l2_panel_v3_event_driven"):
            args.output_root = Path("results/panels/btcusdt_l2_panel_native/cpp")
        elif "cpp" not in args.output_root.parts:
            args.output_root = args.output_root / "cpp"
        if args.status_dir == Path("results/panels/btcusdt_l2_panel_v3_event_driven/status"):
            args.status_dir = args.output_root / "status"
    if args.hours <= 0 or args.session_hours <= 0:
        raise ValueError("--hours and --session-hours must be positive")
    if args.hours % args.session_hours:
        raise ValueError("--hours must be divisible by --session-hours")
    args.symbol = args.symbol.lower()
    require_safe_path_component(args.run_id, label="--run-id")
    if args.cancel_latency_ms is None:
        args.cancel_latency_ms = args.latency_ms
    if args.cancel_jitter_ms is None:
        args.cancel_jitter_ms = args.jitter_ms
    guard_event_driven_output_path(args.output_root)
    guard_event_driven_output_path(args.status_dir)
    parsed_credits = []
    for value in args.queue_credits:
        credit = parse_queue_credit(value)
        if credit not in parsed_credits:
            parsed_credits.append(credit)
    parsed_credits.sort()
    args.queue_credits = [
        format(credit.normalize(), "f") for credit in parsed_credits
    ]
    if args.phase == "all" and set(parsed_credits) != {
        Decimal("0"), Decimal("1")
    }:
        raise ValueError(
            "a complete V3 run requires exactly queue-credit endpoints 0 and 1"
        )
    panel_sha256 = _file_sha256(args.windows_csv)
    if panel_sha256 != DEVELOPMENT_PANEL_SHA256:
        raise ValueError(
            "V3 development runner requires the frozen development panel "
            f"SHA-256 {DEVELOPMENT_PANEL_SHA256}; found {panel_sha256}. "
            "The strategy-sealed holdout is not authorized in this workflow."
        )
    starts = _load_window_starts(args.windows_csv, args.hours)
    _verify_raw_inputs(args, starts)
    if args.phase in ("a", "all"):
        _phase_a(args, starts)
    if args.phase in ("b", "all"):
        _phase_b(args, starts)
    if args.phase == "all" and not args.dry_run:
        manifest_path = _write_artifact_manifest(args, starts)
        print(f"Wrote V3 artifact manifest to {manifest_path}")


if __name__ == "__main__":
    main()
