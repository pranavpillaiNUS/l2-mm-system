"""
Lock the committed C++ parity golden vectors against the current Python
order book. This runs in the deterministic suite even before any C++ exists,
so a change to src/replay/orderbook.py that would invalidate the frozen
vectors fails loudly here (regenerate via scripts/gen_cpp_parity_vectors.py).

It does not test C++ (that is tests/test_cpp_parity.py in Step 2B). It checks:
  1. the golden_vectors.json file matches its committed sha256,
  2. replaying each case through the current Orderbook reproduces every
     recorded per-op state (canonical bytes, hash, best bid/ask, counts),
  3. the per-case and global rolling digests recompute identically.
"""
import hashlib
import json
from pathlib import Path

import pytest

from src.replay.orderbook import Orderbook

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "cpp" / "parity" / "golden_vectors.json"
CHECKSUM = ROOT / "cpp" / "parity" / "golden_vectors.sha256"
BIG_N = 2**31


def _load():
    return json.loads(VECTORS.read_text())


def _capture(ob):
    bids = ob.bid_levels(BIG_N)
    asks = ob.ask_levels(BIG_N)
    data = {
        "bids": [[str(p), str(q)] for p, q in bids],
        "asks": [[str(p), str(q)] for p, q in asks],
        "last_update_id": ob.last_update_id,
    }
    canonical = json.dumps(data, separators=(",", ":"))
    return {
        "canonical_bytes": canonical,
        "state_hash": ob.state_hash(),
        "best_bid": str(ob.best_bid) if ob.best_bid is not None else None,
        "best_ask": str(ob.best_ask) if ob.best_ask is not None else None,
        "n_bids": len(bids),
        "n_asks": len(asks),
    }


def _apply(ob, op):
    if op["op"] == "snapshot":
        ob.apply_snapshot(op["bids"], op["asks"], op["last_update_id"])
    elif op["op"] == "diff":
        ob.apply_diff(op["bids"], op["asks"], op["last_update_id"])
    else:
        raise AssertionError(f"unknown op {op['op']!r}")


def test_golden_vector_file_matches_checksum():
    recorded = CHECKSUM.read_text().split()[0]
    actual = hashlib.sha256(VECTORS.read_bytes()).hexdigest()
    assert actual == recorded, "golden_vectors.json changed without updating its sha256"


def test_canonical_bytes_hash_to_recorded_state_hash():
    data = _load()
    for case in data["cases"]:
        for i, state in enumerate(case["states"]):
            digest = hashlib.sha256(state["canonical_bytes"].encode("utf-8")).hexdigest()
            assert digest == state["state_hash"], f"{case['name']} state[{i}]"


def test_replay_reproduces_every_recorded_state():
    data = _load()
    for case in data["cases"]:
        ob = Orderbook()
        assert _capture(ob) == case["states"][0], f"{case['name']} fresh book"
        for i, op in enumerate(case["ops"]):
            _apply(ob, op)
            assert _capture(ob) == case["states"][i + 1], f"{case['name']} after op {i}"


def test_rolling_digests_recompute():
    data = _load()
    g = hashlib.sha256()
    for case in data["cases"]:
        h = hashlib.sha256()
        for state in case["states"]:
            h.update(state["state_hash"].encode())
        assert h.hexdigest() == case["rolling_digest"], case["name"]
        g.update(case["rolling_digest"].encode())
    assert g.hexdigest() == data["global_rolling_digest"]


@pytest.mark.parametrize("field", ["price_decimals", "qty_decimals", "int_scale"])
def test_scale_metadata_present(field):
    assert field in _load()["scale"]
