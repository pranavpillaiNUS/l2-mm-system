"""Tests for queue-invariant unconditional OFI cache reuse."""

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import scripts.analyze_ofi_signal as analyze_ofi


def test_unconditional_ofi_depth_analysis_is_cached_once(tmp_path: Path, monkeypatch):
    args = SimpleNamespace(
        unconditional_cache=tmp_path / "nested" / "ofi_cache.json",
        symbol="btcusdt",
        starts=["2026-04-13T12"],
        hours=5,
        sample_interval_ms=1_000,
        max_staleness_ms=1_000,
        max_future_lag_ms=1_000,
        data_root=tmp_path / "data",
        queue_cancellation_credit=Decimal("0"),
    )
    calls = []

    def scan(args, *, ofi_interval_ms):
        calls.append(ofi_interval_ms)
        return [{"label": "window", "book_samples": 0, "signal_samples": []}]

    monkeypatch.setattr(analyze_ofi, "_scan_signal_windows", scan)

    first = analyze_ofi._load_or_build_signal_windows(args, ofi_interval_ms=1_000)
    args.queue_cancellation_credit = Decimal("1")
    second = analyze_ofi._load_or_build_signal_windows(args, ofi_interval_ms=1_000)

    assert calls == [1_000]
    assert first[0]["label"] == second[0]["label"] == "window"
    cache = json.loads(args.unconditional_cache.read_text(encoding="utf-8"))
    provenance = cache["analyses"]["1000"]["params"]["book_replay_provenance"]
    assert provenance["private_order_scope"] == "none"
    assert "queue_cancellation_credit" not in provenance
