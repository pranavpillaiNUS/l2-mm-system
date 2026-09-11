"""Byte, operation, and transcript parity against the optional native book."""

import hashlib
import json
import os
import random
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

import scripts.benchmark_orderbook as driver
from scripts.gen_cpp_parity_vectors import capture
from src.replay.depth_parser import DepthEvent
from src.replay.orderbook import Orderbook


ROOT = Path(__file__).resolve().parents[1]
VECTORS = json.loads((ROOT / "cpp/parity/golden_vectors.json").read_text())


@pytest.fixture(scope="module")
def cpp_binary():
    explicit = os.environ.get("L2MM_CPP_BINARY")
    path = Path(explicit) if explicit else driver.DEFAULT_BINARY
    if not path.is_file() or not os.access(path, os.X_OK):
        if explicit:
            pytest.fail(f"required L2MM_CPP_BINARY is unavailable: {path}")
        pytest.skip("build cpp/build/l2mm_orderbook to enable native parity checks")
    return path


def cpp_trace(binary, operations):
    result = subprocess.run(
        [str(binary.resolve()), "trace", "--tick-size", driver.TICK_SIZE,
         "--qty-step", driver.QTY_STEP],
        input=driver.encode_operations(operations), text=True,
        capture_output=True, check=True,
    )
    return [json.loads(line) for line in result.stdout.splitlines()]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda case: case["name"])
def test_cpp_matches_every_golden_byte_and_operation(cpp_binary, case):
    states = cpp_trace(cpp_binary, case["ops"])
    assert len(states) == len(case["states"])
    book = Orderbook()
    for index, (actual, expected) in enumerate(zip(states, case["states"])):
        if index:
            driver.apply_operation(book, case["ops"][index - 1])
        assert {key: actual[key] for key in expected} == expected
        assert actual["sequence"] == book.sequence
        assert actual["is_crossed"] == book.is_crossed()


def test_cpp_matches_per_case_and_global_golden_transcripts(cpp_binary):
    global_digest = hashlib.sha256()
    for case in VECTORS["cases"]:
        transcript = driver.run_cpp(
            cpp_binary, "transcript", driver.encode_operations(case["ops"])
        )
        assert transcript["states"] == len(case["states"])
        assert transcript["rolling_digest"] == case["rolling_digest"]
        assert transcript["final_state_hash"] == case["states"][-1]["state_hash"]
        global_digest.update(transcript["rolling_digest"].encode("ascii"))
    assert global_digest.hexdigest() == VECTORS["global_rolling_digest"]


def random_operations(seed):
    rng = random.Random(seed)
    prices = [driver._fixed8(70_000 * 100_000_000 + index * 1_000_000)
              for index in range(-12, 13)]
    quantities = ["0.00000000", "0.00000100", "0.00001000", "0.10000000",
                  "1.23456789", "7.00000000"]
    operations = []
    for index in range(240):
        kind = "snapshot" if index % 17 == 0 else "diff"
        operation = {"op": kind, "last_update_id": 92_000_000_000 + index}
        for side in ("bids", "asks"):
            operation[side] = [
                [rng.choice(prices), rng.choice(quantities)]
                for _ in range(rng.randrange(9))
            ]
        if index % 19 == 0:
            # Snapshot zero duplicates leave earlier positives intact; diff
            # zero duplicates remove them. The maps must honor input order.
            operation["bids"] += [[prices[0], "0.10000000"],
                                  [prices[0], "0.00000000"]]
        if index % 31 == 0:
            operation.update(op="snapshot", bids=[], asks=[])
        operations.append(operation)
    return operations


@pytest.mark.parametrize("seed", [0, 42, 20260910])
def test_cpp_matches_seeded_random_states_including_duplicates_and_resets(cpp_binary, seed):
    operations = random_operations(seed)
    states = cpp_trace(cpp_binary, operations)
    assert len(states) == len(operations) + 1
    book = Orderbook()
    for index, actual in enumerate(states):
        if index:
            driver.apply_operation(book, operations[index - 1])
        expected = {**capture(book), "sequence": book.sequence,
                    "is_crossed": book.is_crossed()}
        assert actual == expected, f"seed={seed}, state={index}"
    assert driver.run_cpp(cpp_binary, "transcript", driver.encode_operations(operations)) == (
        driver.python_transcript(operations)
    )


def test_benchmark_records_parity_before_timings(cpp_binary):
    operations = driver.synthetic_operations(80, 10, 7)
    report = driver.benchmark(cpp_binary, operations, repeat=2, warmup=1)
    assert report["parity"]["status"] == "passed"
    assert report["parity"]["states"] == 81
    assert report["benchmark"]["median_speedup"] > 0
    for implementation in ("python", "cpp"):
        assert len(report["benchmark"][implementation]["elapsed_ns"]) == 2
    assert "end-to-end replay" in report["excluded"]


def test_benchmark_refuses_timing_after_intermediate_divergence(monkeypatch):
    operations = driver.synthetic_operations(3, 2, 7)
    corrupted = {**driver.python_transcript(operations), "rolling_digest": "0" * 64}
    monkeypatch.setattr(driver, "run_cpp", lambda *args: corrupted)

    def forbidden_timing(*args):
        pytest.fail("timing must not run after failed parity")

    monkeypatch.setattr(driver, "time_python", forbidden_timing)
    with pytest.raises(ValueError, match="transcript diverges"):
        driver.benchmark(Path("unused"), operations)


def test_synthetic_benchmark_input_is_reproducible():
    operations = driver.synthetic_operations(10, 5, 42)
    assert operations == driver.synthetic_operations(10, 5, 42)
    assert operations != driver.synthetic_operations(10, 5, 43)
    assert len(operations) == 10
    assert operations[0]["op"] == "snapshot"
    for operation in operations:
        for price, quantity in [*operation["bids"], *operation["asks"]]:
            assert len(price.split(".")[1]) == len(quantity.split(".")[1]) == 8


def test_python_benchmark_destroys_books_outside_timed_intervals(monkeypatch):
    timer = {"inside": False, "ticks": 0}
    destructions = []

    def clock():
        timer["inside"] = not timer["inside"]
        timer["ticks"] += 1
        return timer["ticks"]

    class Book:
        def state_hash(self):
            return "same"

        def __del__(self):
            destructions.append(timer["inside"])

    monkeypatch.setattr(driver, "Orderbook", Book)
    monkeypatch.setattr(driver, "perf_counter_ns", clock)
    monkeypatch.setattr(driver, "apply_operation", lambda *args: None)
    assert driver.time_python([{}], 2, 1, "same") == [1, 1]
    assert destructions == [False, False, False]


def test_depth_operations_follow_resync_and_atomic_gap_suppression():
    def event(kind, index, *, gap=False, timestamp=None):
        return DepthEvent(
            recv_time=datetime(2026, 4, 12),
            exchange_time_ms=index if timestamp is None else timestamp,
            event_type=kind, first_update_id=index, last_update_id=index,
            bids=[["70000.00000000", "1.00000000"]], asks=[], has_gap=gap,
        )

    operations, counts = driver._operations_from_events([
        event("diff", 1), event("snapshot", 2), event("diff", 3),
        event("resync", 4), event("diff", 5), event("snapshot", 6),
        event("diff", 7, timestamp=8), event("diff", 8, gap=True),
        event("diff", 9), event("snapshot", 10), event("diff", 11),
    ])
    assert [operation["last_update_id"] for operation in operations] == [2, 3, 6, 10, 11]
    assert counts == {"resyncs": 1, "gaps": 1, "suppressed_diffs": 3,
                      "gap_censored_events": 2}


def test_benchmark_rejects_non_development_capture_without_reading_it(tmp_path):
    with pytest.raises(ValueError, match="only frozen development"):
        driver.development_depth_operations(tmp_path / "unknown_depth.jsonl.gz")
