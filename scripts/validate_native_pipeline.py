"""Differential validation and separately gated timing of the full replay path.

Only the frozen development inputs are accepted. No candidate search or holdout
evaluation is performed. The result records hashes of complete output streams,
not merely an equal final book or P&L.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import platform
from statistics import median
import subprocess
from time import perf_counter_ns

from src.analysis.hold_time import compute_hold_time_summary, extract_queue_diagnostics
from src.analysis.markout import compute_markouts
from src.analysis.ofi_signal import compute_ofi_fill_toxicity, compute_ofi_signal_samples
from src.analysis.pnl import compute_pnl_decomposition
from src.execution.provenance import guard_event_driven_output_path
from src.execution.simulator import SimConfig
from src.replay.engine import ReplayConfig, ReplayEngine
from src.strategies.microprice_mm import MicropriceMM


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "results/panels/btcusdt_l2_panel_v3_event_driven/ARTIFACT_MANIFEST.json"
MANIFEST_SHA256 = "d70195704c98a14f7ce0daf747b97906754d7d2ffebcc181d308dad8af57d27a"
HORIZONS = {"1s": 1000, "5s": 5000, "30s": 30000}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(type(value).__name__)


def canonical(value) -> bytes:
    return json.dumps(value, default=json_default, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def stream_hash(rows) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical(row))
        digest.update(b"\n")
    return digest.hexdigest()


def run_once(depth_files, trade_files, backend, credit="0", *, jitter=0,
             checkpoint_interval=1000):
    """Time construction, raw I/O, replay, and listed downstream diagnostics.

    Import/build discovery, parity serialization, and destruction are outside
    timing. Inputs are read again on every run, normally from the OS page cache.
    """
    started = perf_counter_ns()
    strategy = MicropriceMM(
        half_spread=Decimal("2.00"), order_qty=Decimal("0.001"),
        max_position=Decimal("0.01"), tick_size=Decimal("0.01"),
        requote_interval_ms=5000,
    )
    engine = ReplayEngine(ReplayConfig(
        depth_files=list(depth_files), trade_files=list(trade_files),
        sim_config=SimConfig(base_latency_ms=10, jitter_ms=jitter,
                             cancel_latency_ms=10, cancel_jitter_ms=jitter,
                             maker_bps=2, taker_bps=5, seed=42,
                             queue_cancellation_credit=credit),
        record_book_samples=True, checkpoint_interval=checkpoint_interval,
        book_backend=backend,
    ))
    result = engine.run(strategy)
    markouts = compute_markouts(result.fills, result.book_samples, HORIZONS)
    pnl = compute_pnl_decomposition(result.fills, result.book_samples, markouts,
                                    engine.book.mid)
    hold = compute_hold_time_summary(result.fills, result.book_samples)
    queue = extract_queue_diagnostics(result.events)
    ofi_population = compute_ofi_signal_samples(
        result.book_samples, sample_interval_ms=1000, ofi_interval_ms=1000,
        horizons_ms=HORIZONS,
    )
    ofi_conditional = compute_ofi_fill_toxicity(
        result.fills, result.book_samples, ofi_interval_ms=1000,
        horizons_ms=HORIZONS,
    )
    elapsed = perf_counter_ns() - started
    streams = {
        "checkpoints": result.checkpoints, "order_events": result.events,
        "fills": result.fills, "book_samples": result.book_samples,
        "markouts": markouts, "queue_diagnostics": queue,
        "ofi_population_samples": ofi_population,
        "ofi_conditional_samples": ofi_conditional,
        "hold_time": [hold], "pnl": [pnl], "stats": [result.stats],
        "final_strategy": [{"position": strategy.position,
                            "cash": strategy.realized_pnl,
                            "fees": strategy.total_fees}],
        "final_book": [engine.book.state_hash()],
    }
    hashes = {name: stream_hash(rows) for name, rows in streams.items()}
    return {
        "elapsed_ns": elapsed,
        "component_sha256": hashes,
        "counts": {name: len(rows) for name, rows in streams.items()},
        "stats": asdict(result.stats),
        "net_pnl": str(pnl.net_pnl),
        "position": str(strategy.position),
        "fees": str(strategy.total_fees),
        "execution_provenance": result.execution_provenance,
    }


def require_parity(python, native):
    for field in ("component_sha256", "counts", "stats", "net_pnl", "position", "fees"):
        if python[field] != native[field]:
            differing = [key for key in python[field]
                         if python[field][key] != native[field].get(key)] \
                if isinstance(python[field], dict) else [field]
            raise ValueError(f"native pipeline divergence in {field}: {differing}")
    left, right = (dict(run["execution_provenance"]) for run in (python, native))
    for provenance in (left, right):
        provenance.pop("orderbook", None)
    if left != right:
        raise ValueError("native pipeline execution semantics differ")


def selected_inputs(starts=None, data_root=ROOT / "data"):
    if sha256(MANIFEST) != MANIFEST_SHA256:
        raise ValueError("frozen development manifest identity differs")
    payload = json.loads(MANIFEST.read_text())
    rows = payload["raw_inputs"]
    allowed = {row["start"]: row for row in rows}
    requested = list(allowed) if starts is None else [
        datetime.fromisoformat(start).isoformat() for start in starts
    ]
    if not requested or len(requested) != len(set(requested)):
        raise ValueError("select nonempty, unique development hours")
    if any(start not in allowed for start in requested):
        raise ValueError("only frozen development hours are allowed")
    selected = []
    for start in requested:
        row = dict(allowed[start])
        for kind in ("depth", "trade"):
            relative = Path(row[f"{kind}_path"]).relative_to("data")
            path = data_root / relative
            if not path.is_file() or sha256(path) != row[f"{kind}_sha256"]:
                raise ValueError(f"development {kind} capture hash differs: {start}")
            row[f"{kind}_path"] = str(path)
        selected.append(row)
    return selected


def validate_hour(task):
    row, credit = task
    arguments = ([Path(row["depth_path"])], [Path(row["trade_path"])])
    python = run_once(*arguments, "python", credit)
    native = run_once(*arguments, "cpp", credit)
    require_parity(python, native)
    return {"start": row["start"], "queue_credit": credit, "status": "passed",
            "component_sha256": python["component_sha256"],
            "counts": python["counts"], "stats": python["stats"],
            "net_pnl": python["net_pnl"], "fees": python["fees"],
            "position": python["position"]}


def benchmark_hour(row, credit, repeat=3, warmup=1):
    arguments = ([Path(row["depth_path"])], [Path(row["trade_path"])])
    # Complete stream comparison is mandatory before emitting timing claims.
    reference = run_once(*arguments, "python", credit)
    native = run_once(*arguments, "cpp", credit)
    require_parity(reference, native)
    timings = {"python": [], "cpp": []}
    order = []
    for iteration in range(warmup + repeat):
        backends = ("python", "cpp") if iteration % 2 == 0 else ("cpp", "python")
        order.append(list(backends))
        for backend in backends:
            result = run_once(*arguments, backend, credit)
            require_parity(reference, result)
            if iteration >= warmup:
                timings[backend].append(result["elapsed_ns"])
            del result
    medians = {backend: median(values) for backend, values in timings.items()}
    return {
        "start": row["start"], "queue_credit": credit,
        "parity": "passed", "repeat": repeat, "warmup": warmup,
        "order": order, "elapsed_ns": timings, "median_ns": medians,
        "median_speedup": medians["python"] / medians["cpp"],
        "scope": "raw gzip I/O and JSON parsing, construction, replay, strategy, "
                 "execution, checkpoints, samples, markouts, P&L, hold-time, queue "
                 "diagnostics, and OFI population/conditional sample construction",
        "excluded": ["imports and initial native module discovery",
                     "parity serialization", "object destruction", "output file writing",
                     "pooled regression, bootstrap, and report generation"],
        "cache_state": "warm OS page cache; raw files reopened each repetition",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--starts", nargs="+", help="Frozen development hours; default: all 120")
    parser.add_argument("--data-root", type=Path, default=ROOT / "data")
    parser.add_argument("--queue-credits", nargs="+", choices=("0", "1"), default=["0", "1"])
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--benchmark-repeats", type=int, default=0,
                        help="After validation, time the first hour serially at each endpoint")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "results/native_pipeline/development_parity.json")
    args = parser.parse_args()
    if args.workers < 1 or args.benchmark_repeats < 0:
        parser.error("workers must be positive and repetitions nonnegative")
    guard_event_driven_output_path(args.output)
    from src.replay.cpp_orderbook import native_build_info
    native = native_build_info()  # fail before loading raw data if build is absent
    rows = selected_inputs(args.starts, args.data_root)
    tasks = [(row, credit) for row in rows for credit in args.queue_credits]
    results = []
    if args.workers == 1:
        iterator = map(validate_hour, tasks)
        for index, result in enumerate(iterator, 1):
            results.append(result)
            print(f"[{index}/{len(tasks)}] {result['start']} qc{result['queue_credit']}: parity passed", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for index, result in enumerate(pool.map(validate_hour, tasks), 1):
                results.append(result)
                print(f"[{index}/{len(tasks)}] {result['start']} qc{result['queue_credit']}: parity passed", flush=True)
    measurements = []
    if args.benchmark_repeats:
        for credit in args.queue_credits:
            print(f"Benchmarking first hour serially at qc{credit}...", flush=True)
            measurements.append(benchmark_hour(rows[0], credit, args.benchmark_repeats))
    paths = sorted([*ROOT.glob("src/**/*.py"), *ROOT.glob("cpp/src/*.cpp"),
                    *ROOT.glob("cpp/include/**/*.hpp"), ROOT / "cpp/CMakeLists.txt",
                    Path(__file__)])
    report = {
        "schema_version": "native_pipeline_validation_v1",
        "status": "passed", "reference_manifest_sha256": MANIFEST_SHA256,
        "input_hours": len(rows), "endpoint_runs": len(results), "raw_inputs": rows,
        "parity_scope": "full output streams and diagnostics, book checkpoints every 1000 events",
        "results": results, "benchmarks": measurements,
        "provenance": {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "git_status": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip(),
            "source_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in paths},
            "native": native, "python": platform.python_version(),
            "platform": platform.platform(), "load_average": os.getloadavg(),
            "workers_for_validation": args.workers,
        },
        "holdout_status": "strategy_sealed",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
