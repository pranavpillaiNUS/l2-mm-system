"""
Deterministic path/URL mapping guards for the recorders (no network).

These protect the one irreversible failure mode in the recorder: perp data must
NEVER be written into the spot raw tree. Spot and perp must use distinct output
directories and dataset prefixes, and each must point at its own venue host.
"""
import io
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.recorder.simple_recorder import SimpleRecorder
from src.recorder.trade_recorder import TradeRecorder


def test_spot_depth_paths_unchanged(tmp_path: Path) -> None:
    rec = SimpleRecorder(symbol="BTCUSDT", output_dir=tmp_path)
    assert rec.market == "spot"
    assert rec.dataset == "btcusdt"
    assert rec.output_dir == tmp_path / "raw" / "btcusdt"
    assert rec._get_ws_url() == "wss://stream.binance.com:9443/ws/btcusdt@depth@100ms"
    assert rec._rest_url == "https://api.binance.com/api/v3/depth"


def test_perp_depth_paths_isolated(tmp_path: Path) -> None:
    rec = SimpleRecorder(symbol="BTCUSDT", output_dir=tmp_path, market="perp")
    assert rec.market == "perp"
    assert rec.dataset == "btcusdt_perp"
    assert rec.output_dir == tmp_path / "raw" / "btcusdt_perp"
    assert rec._get_ws_url() == "wss://fstream.binance.com/ws/btcusdt@depth@100ms"
    assert rec._rest_url == "https://fapi.binance.com/fapi/v1/depth"


def test_spot_and_perp_depth_never_collide(tmp_path: Path) -> None:
    spot = SimpleRecorder(symbol="btcusdt", output_dir=tmp_path, market="spot")
    perp = SimpleRecorder(symbol="btcusdt", output_dir=tmp_path, market="perp")
    assert spot.output_dir != perp.output_dir
    assert spot.dataset != perp.dataset


def test_trade_paths_isolated(tmp_path: Path) -> None:
    spot = TradeRecorder(symbol="btcusdt", output_dir=tmp_path, market="spot")
    perp = TradeRecorder(symbol="btcusdt", output_dir=tmp_path, market="perp")
    assert spot.output_dir == tmp_path / "raw" / "btcusdt_trades"
    assert perp.output_dir == tmp_path / "raw" / "btcusdt_perp_trades"
    assert spot.output_dir != perp.output_dir
    # Spot serves aggregated trades; this environment's futures feed only
    # streams raw per-fill trades, so perp subscribes to @trade not @aggTrade.
    assert spot._get_ws_url() == "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
    assert perp._get_ws_url() == "wss://fstream.binance.com/ws/btcusdt@trade"


def test_invalid_market_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SimpleRecorder(symbol="btcusdt", output_dir=tmp_path, market="options")
    with pytest.raises(ValueError):
        TradeRecorder(symbol="btcusdt", output_dir=tmp_path, market="options")


def test_snapshot_records_request_and_response_times(tmp_path: Path) -> None:
    rec = SimpleRecorder(symbol="btcusdt", output_dir=tmp_path)
    rec._current_file = io.StringIO()
    rec._fetch_snapshot = lambda: {
        "lastUpdateId": 1,
        "bids": [],
        "asks": [],
    }
    request_time = datetime.utcnow() - timedelta(seconds=1)
    before_response = datetime.utcnow()

    rec._write_snapshot(request_time)

    after_response = datetime.utcnow()
    record = json.loads(rec._current_file.getvalue())
    response_time = datetime.fromisoformat(record["recv_time"])
    assert record["request_time"] == request_time.isoformat()
    assert before_response <= response_time <= after_response
