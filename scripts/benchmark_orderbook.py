"""Check every book state for C++ parity before timing identical book updates.

The measurement includes numeric conversion and snapshot/diff application to
preloaded operations. It excludes recording/JSON/protocol parsing, hashing,
execution simulation, and process startup; it is not replay throughput.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import random
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path
from time import perf_counter_ns

from scripts.run_l2_panel import (
    DEVELOPMENT_PANEL_SHA256,
    INTEGRITY_MANIFEST_FILE_SHA256,
)
from src.execution.provenance import guard_event_driven_output_path
from src.replay.depth_parser import DepthParser, SNAPSHOT_TIME_POLICY
from src.replay.event_merger import EventMerger
from src.replay.orderbook import Orderbook


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINARY = ROOT / "cpp/build/l2mm_orderbook"
TICK_SIZE = "0.01000000"
QTY_STEP = "0.00000001"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encode_operations(operations: list[dict]) -> str:
    lines = []
    for operation in operations:
        kind = operation["op"]
        if kind not in {"snapshot", "diff"}:
            raise ValueError(f"unsupported book operation: {kind}")
        bids, asks = operation["bids"], operation["asks"]
        lines.append(f"{kind} {operation['last_update_id']} {len(bids)} {len(asks)}")
        lines.extend(f"{price} {qty}" for price, qty in [*bids, *asks])
    return "\n".join(lines) + ("\n" if lines else "")


def apply_operation(book: Orderbook, operation: dict) -> None:
    if operation["op"] == "snapshot":
        book.apply_snapshot(
            operation["bids"], operation["asks"], operation["last_update_id"]
        )
    elif operation["op"] == "diff":
        book.apply_diff(
            operation["bids"], operation["asks"], operation["last_update_id"]
        )
    else:
        raise ValueError(f"unsupported book operation: {operation['op']}")


def python_transcript(operations: list[dict]) -> dict:
    book = Orderbook()
    digest = hashlib.sha256()
    state_hash = book.state_hash()
    digest.update(state_hash.encode("ascii"))
    for operation in operations:
        apply_operation(book, operation)
        state_hash = book.state_hash()
        digest.update(state_hash.encode("ascii"))
    return {
        "states": len(operations) + 1,
        "rolling_digest": digest.hexdigest(),
        "final_state_hash": state_hash,
    }


def run_cpp(binary: Path, mode: str, protocol: str, *options: str) -> dict:
    result = subprocess.run(
        [str(binary.resolve()), mode, "--tick-size", TICK_SIZE,
         "--qty-step", QTY_STEP, *options],
        input=protocol, text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def _fixed8(value: int) -> str:
    return f"{value // 100_000_000}.{value % 100_000_000:08d}"


def synthetic_operations(count: int, levels: int, seed: int) -> list[dict]:
    if count < 1 or levels < 1:
        raise ValueError("operations and levels must be positive")
    rng = random.Random(seed)
    midpoint = 70_000 * 100_000_000
    tick = 1_000_000

    def level(side: str, index: int, *, delete: bool = False) -> list[str]:
        offset = (index + 1) * tick * (-1 if side == "bids" else 1)
        quantity = 0 if delete else rng.randint(1, 100_000) * 1000
        return [_fixed8(midpoint + offset), _fixed8(quantity)]

    operations = [{
        "op": "snapshot", "last_update_id": 92_000_000_000,
        "bids": [level("bids", index) for index in range(levels)],
        "asks": [level("asks", index) for index in range(levels)],
    }]
    for index in range(1, count):
        operation = {"op": "diff", "last_update_id": 92_000_000_000 + index}
        for side in ("bids", "asks"):
            operation[side] = [
                level(side, rng.randrange(levels + max(1, levels // 4)),
                      delete=rng.random() < 0.2)
                for _ in range(rng.randint(0, 4))
            ]
        operations.append(operation)
    return operations


def _operations_from_events(events) -> tuple[list[dict], dict]:
    """Match the book mutation boundary of ReplayEngine's depth handling."""
    operations = []
    ready = False
    counts = {"resyncs": 0, "gaps": 0, "suppressed_diffs": 0,
              "gap_censored_events": 0}
    for _, group in groupby(events, key=lambda event: event.exchange_time_ms):
        group = list(group)
        if any(event.has_gap for event in group):
            ready = False
            counts["gaps"] += sum(event.has_gap for event in group)
            counts["gap_censored_events"] += len(group)
            continue
        for event in group:
            if event.event_type == "resync":
                ready = False
                counts["resyncs"] += 1
                continue
            if event.event_type == "snapshot":
                ready = True
            elif not ready:
                counts["suppressed_diffs"] += 1
                continue
            operations.append({
                "op": event.event_type,
                "last_update_id": event.last_update_id,
                "bids": event.bids,
                "asks": event.asks,
            })
    return operations, counts


def development_depth_operations(path: Path) -> tuple[list[dict], dict]:
    panel = ROOT / "results/panels/btcusdt_l2_panel_v2/development_windows.csv"
    integrity = ROOT / "results/panels/btcusdt_l2_panel_v2/integrity_manifest.json"
    if file_sha256(panel) != DEVELOPMENT_PANEL_SHA256:
        raise ValueError("development panel identity changed")
    if file_sha256(integrity) != INTEGRITY_MANIFEST_FILE_SHA256:
        raise ValueError("raw integrity manifest identity changed")
    with panel.open(newline="", encoding="utf-8") as stream:
        windows = list(csv.DictReader(stream))
    starts = {
        (datetime.fromisoformat(row["start"]) + timedelta(hours=offset)).isoformat()
        for row in windows for offset in range(int(row["hours"]))
    }
    inventory = json.loads(integrity.read_text(encoding="utf-8"))
    allowed = {
        Path(row["depth_path"]).name: row["depth_sha256"]
        for row in inventory["hours"] if row["start"] in starts and row["valid"]
    }
    if path.name not in allowed:
        raise ValueError("only frozen development depth captures may be benchmarked")
    actual_sha256 = file_sha256(path)
    if actual_sha256 != allowed[path.name]:
        raise ValueError("depth capture does not match frozen development integrity")
    events = EventMerger(DepthParser([path]).events(), iter(())).events()
    operations, counts = _operations_from_events(events)
    return operations, {
        "kind": "recorded_development_depth", "path": str(path.resolve()),
        "sha256": actual_sha256, "snapshot_time_policy": SNAPSHOT_TIME_POLICY,
        "development_panel_sha256": DEVELOPMENT_PANEL_SHA256,
        "depth_only": True, "parser_filter_counts": counts,
    }


def time_python(operations: list[dict], repeat: int, warmup: int,
                expected_hash: str) -> list[int]:
    timings = []
    for iteration in range(warmup + repeat):
        start = perf_counter_ns()
        book = Orderbook()
        for operation in operations:
            apply_operation(book, operation)
        elapsed = perf_counter_ns() - start
        if book.state_hash() != expected_hash:
            raise ValueError("Python timed run differs from the parity transcript")
        if iteration >= warmup:
            timings.append(elapsed)
    return timings


def timing_summary(elapsed: list[int], operations: int) -> dict:
    median = statistics.median(elapsed)
    return {
        "elapsed_ns": elapsed, "median_ns": median,
        "min_ns": min(elapsed), "max_ns": max(elapsed),
        "median_operations_per_second": operations * 1_000_000_000 / median,
    }


def benchmark(binary: Path, operations: list[dict], *, repeat: int = 5,
              warmup: int = 1, skip_benchmark: bool = False) -> dict:
    if repeat < 1 or warmup < 0 or not operations:
        raise ValueError("need nonempty operations, repeat >= 1, and warmup >= 0")
    protocol = encode_operations(operations)
    print(f"Checking all {len(operations) + 1} book states for parity...", file=sys.stderr)
    reference = python_transcript(operations)
    current = run_cpp(binary, "transcript", protocol)
    if any(current.get(field) != value for field, value in reference.items()):
        raise ValueError("C++ transcript diverges from Python; timing is prohibited")
    report = {
        "schema_version": "orderbook_benchmark_v1",
        "metric": "preloaded_book_updates_including_numeric_conversion",
        "timed_scope": "book construction, operation dispatch, decimal conversion, and book mutation",
        "excluded": ["file and protocol parsing", "state hashing", "process startup",
                     "strategy and execution simulation", "end-to-end replay"],
        "parity": {"status": "passed", **reference},
        "input_protocol_sha256": hashlib.sha256(protocol.encode("utf-8")).hexdigest(),
        "operations": len(operations), "tick_size": TICK_SIZE, "qty_step": QTY_STEP,
        "benchmark": None,
    }
    if not skip_benchmark:
        print("Parity passed; timing Python then C++ book updates...", file=sys.stderr)
        python_times = time_python(operations, repeat, warmup, reference["final_state_hash"])
        cpp = run_cpp(binary, "benchmark", protocol,
                      "--repeat", str(repeat), "--warmup", str(warmup))
        if (cpp.get("operations_per_repeat") != len(operations)
                or cpp.get("repeat") != repeat or cpp.get("warmup") != warmup
                or cpp.get("final_state_hash") != reference["final_state_hash"]
                or not isinstance(cpp.get("elapsed_ns"), list)
                or len(cpp["elapsed_ns"]) != repeat
                or any(type(value) is not int or value <= 0 for value in cpp["elapsed_ns"])):
            raise ValueError("C++ timed run metadata or final state differs from parity")
        python_stats = timing_summary(python_times, len(operations))
        cpp_stats = timing_summary(cpp["elapsed_ns"], len(operations))
        report["benchmark"] = {
            "repeat": repeat, "warmup": warmup, "order": ["python", "cpp"],
            "python": python_stats, "cpp": cpp_stats,
            "median_speedup": python_stats["median_ns"] / cpp_stats["median_ns"],
            "cpp_metadata": {key: value for key, value in cpp.items()
                             if key != "elapsed_ns"},
        }
    return report


def provenance(binary: Path) -> dict:
    def git(*arguments):
        result = subprocess.run(["git", *arguments], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    sources = [ROOT / "src/replay/orderbook.py", Path(__file__).resolve()]
    sources.extend(sorted((ROOT / "cpp").rglob("*.cpp")))
    sources.extend(sorted((ROOT / "cpp").rglob("*.hpp")))
    sources = [path for path in sources
               if not any(part.startswith("build") for part in path.relative_to(ROOT).parts)]
    cpu_info = Path("/proc/cpuinfo")
    cpu_model = next((line.split(":", 1)[1].strip()
                      for line in cpu_info.read_text().splitlines()
                      if line.startswith("model name")), None) if cpu_info.exists() else None
    flags_path = binary.parent / "CMakeFiles/l2mm_book.dir/flags.make"
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(), "machine": platform.machine(),
        "processor": platform.processor(), "python_version": sys.version,
        "cpu_model": cpu_model,
        "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        "native_compile_flags": flags_path.read_text() if flags_path.is_file() else None,
        "python_implementation": platform.python_implementation(),
        "git_head": git("rev-parse", "HEAD"), "git_status": git("status", "--short"),
        "binary": str(binary.resolve()), "binary_sha256": file_sha256(binary),
        "source_sha256": {str(path.relative_to(ROOT)): file_sha256(path) for path in sources},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path,
                        default=Path(os.environ.get("L2MM_CPP_BINARY", DEFAULT_BINARY)))
    parser.add_argument("--operations", type=int, default=20_000)
    parser.add_argument("--levels", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--depth-file", type=Path)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--skip-benchmark", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output:
        guard_event_driven_output_path(args.output)
    if args.depth_file:
        operations, input_metadata = development_depth_operations(args.depth_file)
    else:
        operations = synthetic_operations(args.operations, args.levels, args.seed)
        input_metadata = {"kind": "synthetic", "seed": args.seed,
                          "initial_levels_per_side": args.levels}
    report = benchmark(args.binary, operations, repeat=args.repeat, warmup=args.warmup,
                       skip_benchmark=args.skip_benchmark)
    report["input"] = input_metadata
    report["provenance"] = provenance(args.binary)
    payload = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
