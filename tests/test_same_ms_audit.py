"""Tests for split same-ms data overlap computation."""

import gzip
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.audit_same_ms_attribution as same_ms_audit
from scripts.audit_same_ms_attribution import (
    _build_data_overlap,
    _load_or_build_data_cache,
)


def write_gz(path: Path, rows: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_same_ms_data_overlap_is_queue_invariant_raw_computation(tmp_path: Path):
    depth = tmp_path / "depth.jsonl.gz"
    trades = tmp_path / "trades.jsonl.gz"
    write_gz(depth, [{
        "recv_time": "2026-04-21T00:00:00+00:00",
        "type": "snapshot",
        "data": {"lastUpdateId": 1, "bids": [["100", "1"]], "asks": [["101", "1"]]},
    }, {
        "recv_time": "2026-04-21T00:00:01+00:00",
        "data": {"E": 1_000, "U": 2, "u": 2, "b": [["100", "2"]], "a": []},
    }])
    write_gz(trades, [{
        "recv_time": "2026-04-21T00:00:01+00:00",
        "data": {"T": 1_000, "a": 1, "p": "100", "q": "0.5", "m": True},
    }])

    overlap = _build_data_overlap([depth], [trades])

    assert overlap["same_ms_overlap_timestamps"] == 1
    assert overlap["overlap_timestamps"] == [1_000]
    assert overlap["same_side_trade_qty"] == [{
        "timestamp_ms": 1_000,
        "side": "buy",
        "price": Decimal("100"),
        "quantity": Decimal("0.5"),
    }]


def _build_cache_args(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        data_overlap_cache=tmp_path / "same_ms_cache.json",
        data_root=tmp_path / "btc_data",
        symbol="btcusdt",
        starts=["2026-04-21T00"],
        hours=1,
    )


def _write_test_cache(monkeypatch, args: SimpleNamespace) -> None:
    monkeypatch.setattr(
        same_ms_audit,
        "_select_files",
        lambda data_root, symbol, window: ([], []),
    )
    monkeypatch.setattr(
        same_ms_audit,
        "_build_data_overlap",
        lambda depth_files, trade_files: {"source": "generated"},
    )
    _load_or_build_data_cache(args)


def test_same_ms_data_cache_rejects_different_symbol(tmp_path: Path, monkeypatch):
    args = _build_cache_args(tmp_path)
    _write_test_cache(monkeypatch, args)

    args.symbol = "ethusdt"

    with pytest.raises(ValueError, match="does not match requested raw data"):
        _load_or_build_data_cache(args)


def test_same_ms_data_cache_rejects_different_data_root(
    tmp_path: Path,
    monkeypatch,
):
    args = _build_cache_args(tmp_path)
    _write_test_cache(monkeypatch, args)

    args.data_root = tmp_path / "different_btc_data"

    with pytest.raises(ValueError, match="does not match requested raw data"):
        _load_or_build_data_cache(args)
