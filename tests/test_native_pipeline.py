"""Differential integration at the execution and downstream research boundary."""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import scripts.validate_native_pipeline as validation
from src.execution.order import OrderRequest, OrderSide, OrderType
from src.execution.simulator import SimConfig
from src.replay import cpp_orderbook
from src.replay.engine import CancelRequest, ReplayConfig, ReplayEngine


ROOT = Path(__file__).resolve().parents[1]
BASE = datetime(2026, 4, 21, tzinfo=timezone.utc)
BASE_MS = int(BASE.timestamp() * 1000)


@pytest.fixture(scope="module")
def native_available():
    try:
        cpp_orderbook._module_path()
    except ImportError:
        if "L2MM_CPP_MODULE" in os.environ:
            pytest.fail("explicit L2MM_CPP_MODULE must be present")
        pytest.skip("build the optional native extension to run pipeline integration tests")
    return cpp_orderbook.native_build_info()


def timestamp(offset):
    return (BASE + timedelta(milliseconds=offset)).isoformat()


def snapshot(offset, identifier):
    return {"recv_time": timestamp(offset), "type": "snapshot", "data": {
        "lastUpdateId": identifier,
        "bids": [["100.00000000", "0.00200000"], ["99.00000000", "0.00300000"]],
        "asks": [["104.00000000", "0.00200000"], ["105.00000000", "0.00300000"]],
    }}


def diff(offset, identifier, bids=(), asks=()):
    return {"recv_time": timestamp(offset), "data": {
        "E": BASE_MS + offset, "U": identifier, "u": identifier,
        "b": list(bids), "a": list(asks),
    }}


def trade(offset, identifier, quantity, *, price="100.00000000", sell=True):
    return {"recv_time": timestamp(offset), "data": {
        "T": BASE_MS + offset, "a": identifier, "p": price, "q": quantity, "m": sell,
    }}


def write_gzip(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    return path


@pytest.fixture
def captures(tmp_path):
    # Both an ordinary depth gap and snapshot recovery are present. The long
    # tail provides usable 1s/5s/30s markouts and population OFI observations.
    depth = [snapshot(0, 100), diff(1, 101),
             diff(50, 102, bids=[("100.00000000", "0.00100000")]),
             diff(1000, 103, asks=[("104.00000000", "0.00300000")]),
             diff(2000, 104, bids=[("99.00000000", "0.00000000")]),
             diff(5001, 105), diff(6000, 120), snapshot(7000, 200), diff(7001, 201)]
    for index, offset in enumerate(range(8000, 45001, 1000), start=202):
        depth.append(diff(offset, index, bids=[("100.00000000", "0.00200000")],
                          asks=[("104.00000000", "0.00300000" if index % 2 else "0.00200000")]))
    trades = [trade(100, 1, "0.05200000"), trade(120, 2, "0.10000000"),
              trade(150, 3, "0.15000000"),
              trade(1100, 4, "0.00500000", price="104.00000000", sell=False),
              trade(6500, 5, "0.10000000")]
    for index, offset in enumerate(range(8100, 44101, 3000), start=6):
        sell = index % 2 == 0
        trades.append(trade(offset, index, "0.01000000",
                            price="100.00000000" if sell else "104.00000000", sell=sell))
    depth_path = write_gzip(tmp_path / "raw/btcusdt/btcusdt_depth_20260421_0000.jsonl.gz", depth)
    trade_path = write_gzip(tmp_path / "raw/btcusdt_trades/btcusdt_trades_20260421_0000.jsonl.gz", trades)
    return depth_path, trade_path


@pytest.mark.parametrize("credit", ["0", "1"])
@pytest.mark.parametrize("jitter", [0, 3])
def test_full_microprice_research_streams_match_with_gaps_and_jitter(native_available, captures, credit, jitter):
    depth, trades = captures
    reference = validation.run_once([depth], [trades], "python", credit,
                                    jitter=jitter, checkpoint_interval=1)
    native = validation.run_once([depth], [trades], "cpp", credit,
                                 jitter=jitter, checkpoint_interval=1)
    validation.require_parity(reference, native)
    assert reference["stats"]["depth_gaps_detected"] == 1
    assert reference["stats"]["snapshots"] == 2
    assert reference["stats"]["gap_invalidations"] > 0
    assert reference["counts"]["fills"] > 0
    assert reference["counts"]["markouts"] > 0
    assert reference["counts"]["ofi_population_samples"] > 0
    assert reference["counts"]["ofi_conditional_samples"] > 0
    assert reference["counts"]["checkpoints"] == reference["stats"]["total_events"]


class ExerciseExecution:
    """Create simultaneous FIFO orders, both book walks, and a cancel/fill race."""

    def __init__(self):
        self.submitted = False
        self.cancelled = False
        self.orders = []

    def on_book_update(self, book, timestamp_ms):
        if self.submitted:
            return []
        self.submitted = True
        buys = [OrderRequest(OrderSide.BUY, OrderType.LIMIT, Decimal("0.1"), Decimal("100"))
                for _ in range(3)]
        return [*buys,
                OrderRequest(OrderSide.BUY, OrderType.LIMIT, Decimal("0.1"), Decimal("50")),
                OrderRequest(OrderSide.BUY, OrderType.MARKET, Decimal("0.003")),
                OrderRequest(OrderSide.SELL, OrderType.MARKET, Decimal("0.004"))]

    def on_trade(self, trade, book):
        return []

    def on_fill(self, fill):
        if fill.is_maker and not self.cancelled:
            self.cancelled = True
            return [CancelRequest(fill.order_id)]
        return []

    def on_order_placed(self, request, order):
        self.orders.append(order)


def execution_run(captures, backend, credit, jitter, cancel_latency):
    depth, trades = captures
    strategy = ExerciseExecution()
    engine = ReplayEngine(ReplayConfig(
        depth_files=[depth], trade_files=[trades], book_backend=backend,
        sim_config=SimConfig(base_latency_ms=10, jitter_ms=jitter,
                             cancel_latency_ms=cancel_latency, cancel_jitter_ms=jitter,
                             queue_cancellation_credit=credit, seed=42,
                             maker_bps=2, taker_bps=5),
        record_book_samples=True, checkpoint_interval=1,
    ))
    result = engine.run(strategy)
    payload = asdict(result)
    payload["execution_provenance"].pop("orderbook")
    return result, strategy.orders, validation.canonical(payload)


@pytest.mark.parametrize("credit", ["0", "1"])
@pytest.mark.parametrize("jitter,cancel_latency", [(0, 20), (0, 40), (3, 40)])
def test_fifo_partial_fills_taker_walks_and_cancel_races_match(native_available, captures, credit, jitter, cancel_latency):
    reference, orders, reference_bytes = execution_run(captures, "python", credit, jitter, cancel_latency)
    native, native_orders, native_bytes = execution_run(captures, "cpp", credit, jitter, cancel_latency)
    assert reference_bytes == native_bytes
    assert validation.canonical(orders) == validation.canonical(native_orders)
    maker = [fill for fill in reference.fills if fill.is_maker]
    taker = [fill for fill in reference.fills if not fill.is_maker]
    assert sum((fill.quantity for fill in maker), Decimal(0)) == Decimal("0.3")
    assert len({fill.order_id for fill in maker}) == 3
    assert len(maker) > 3  # Multiple partial fills, not merely one fill per order.
    assert {fill.price for fill in taker} == {Decimal(99), Decimal(100), Decimal(104), Decimal(105)}
    assert reference.stats.cancels_too_late == 1
    assert reference.stats.gap_invalidations == 1  # Resting far bid remains until gap.
    fifo = sorted((order for order in orders if order.price == Decimal(100)),
                  key=lambda order: order.queue_sequence)
    first_fills = list(dict.fromkeys(fill.order_id for fill in maker))
    assert first_fills == [order.order_id for order in fifo]


def complete_report():
    return {
        "component_sha256": {"order_events": "a", "fills": "b", "queue_diagnostics": "c"},
        "counts": {"order_events": 4, "fills": 1}, "stats": {"fills": 1},
        "net_pnl": "-1.0", "position": "0.1", "fees": "0.001",
        "execution_provenance": {"policy": "market_before_private", "orderbook": {"backend": "python"}},
    }


@pytest.mark.parametrize("field", ["component_sha256", "counts", "stats", "net_pnl", "position", "fees",
                                   "execution_provenance"])
def test_validation_gate_rejects_changed_execution_or_downstream_result(field):
    reference = complete_report()
    native = deepcopy(reference)
    native["execution_provenance"]["orderbook"]["backend"] = "cpp"
    if field == "component_sha256":
        native[field]["queue_diagnostics"] = "different representation"
    elif field in {"counts", "stats"}:
        native[field]["fills"] += 1
    elif field == "execution_provenance":
        native[field]["policy"] = "changed"
    else:
        native[field] = "changed"
    with pytest.raises(ValueError, match="diverge|differ"):
        validation.require_parity(reference, native)


def test_pipeline_benchmark_refuses_timing_after_failed_parity(monkeypatch):
    calls = []
    def diverge(*args):
        calls.append(args)
        result = complete_report()
        if len(calls) == 2:
            result["component_sha256"]["order_events"] = "corrupted intermediate event"
        return result
    monkeypatch.setattr(validation, "run_once", diverge)
    with pytest.raises(ValueError, match="divergence"):
        validation.benchmark_hour({"depth_path": "unused", "trade_path": "unused"}, "0")
    assert len(calls) == 2


def test_replay_cli_native_backend_writes_separate_results_and_provenance(native_available, captures, tmp_path):
    environment = {**os.environ, "PYTHONPATH": str(ROOT)}
    subprocess.run([
        sys.executable, "scripts/run_replay.py", "--date", "2026-04-21", "--hour", "0",
        "--strategy", "microprice", "--half-spread", "2", "--requote-interval-ms", "5000",
        "--book-backend", "cpp", "--data-root", str(tmp_path), "--write-results",
        "--output-dir", str(tmp_path / "results"),
    ], cwd=ROOT, env=environment, check=True, text=True, capture_output=True)
    summaries = list((tmp_path / "results/cpp").glob("*/summary.json"))
    assert len(summaries) == 1
    result = json.loads(summaries[0].read_text())
    book = result["execution_provenance"]["orderbook"]
    assert book["backend"] == "cpp"
    assert book["derived_arithmetic"] == "python_decimal"
    assert book["native"]["sha256"] == native_available["sha256"]
    assert (summaries[0].parent / "fills.csv").is_file()
    assert (summaries[0].parent / "markouts.csv").is_file()
