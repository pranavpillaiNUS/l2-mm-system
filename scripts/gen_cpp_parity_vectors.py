"""
Generate golden parity vectors for the C++ order-book port.

This freezes the EXISTING behavior of `src/replay/orderbook.py` (its
`state_hash` and best-bid/ask) as the canonical reference the C++ port must
reproduce bit-for-bit. It does NOT define a new canonical representation: it
runs the current Python order book on synthetic, self-contained op sequences
and records exactly what that code produces.

The vectors are synthetic (no dependency on the gitignored `data/raw` tree) so
they are committable and deterministic. Op sequences cover operation-level
parity (state after every op, including the fresh empty book) and a rolling
transcript digest that catches divergence even if two books later reconverge.

Run:
    env PYTHONPATH=. python scripts/gen_cpp_parity_vectors.py

Outputs (committed):
    cpp/parity/golden_vectors.json
    cpp/parity/golden_vectors.sha256
"""
import hashlib
import json
from pathlib import Path

from src.replay.orderbook import Orderbook

OUT_DIR = Path("cpp/parity")
VECTORS_PATH = OUT_DIR / "golden_vectors.json"
CHECKSUM_PATH = OUT_DIR / "golden_vectors.sha256"

CONTRACT_VERSION = 1
FROZEN_NOTE = "frozen from src/replay/orderbook.py state_hash on 2026-06-18"

# Scale verified empirically against recorded BTCUSDT depth: every stored level
# is an exact multiple of 1e-8 and renders in plain 8-decimal notation. The only
# values that would render in scientific notation are zero quantities, which the
# order book removes and never serializes. See cpp/parity_contract.md.
SCALE = {
    "price_decimals": 8,
    "qty_decimals": 8,
    "int_scale": 100_000_000,
}

BIG_N = 2**31  # larger than any synthetic book, bid_levels/ask_levels cap at len


def _all_levels(ob):
    """All (price, qty) levels best-first, via the public accessors state_hash uses."""
    bids = ob.bid_levels(BIG_N)
    asks = ob.ask_levels(BIG_N)
    return bids, asks


def capture(ob):
    """Record exactly what state_hash serializes, plus best bid/ask and counts."""
    bids, asks = _all_levels(ob)
    data = {
        "bids": [[str(p), str(q)] for p, q in bids],
        "asks": [[str(p), str(q)] for p, q in asks],
        "last_update_id": ob.last_update_id,
    }
    canonical = json.dumps(data, separators=(",", ":"))
    state_hash = ob.state_hash()
    # Self-check: the canonical bytes we record must be exactly what state_hash hashes.
    assert hashlib.sha256(canonical.encode()).hexdigest() == state_hash, (
        "canonical reconstruction diverged from Orderbook.state_hash"
    )
    return {
        "canonical_bytes": canonical,
        "state_hash": state_hash,
        "best_bid": str(ob.best_bid) if ob.best_bid is not None else None,
        "best_ask": str(ob.best_ask) if ob.best_ask is not None else None,
        "n_bids": len(bids),
        "n_asks": len(asks),
    }


def apply_op(ob, op):
    if op["op"] == "snapshot":
        ob.apply_snapshot(op["bids"], op["asks"], op["last_update_id"])
    elif op["op"] == "diff":
        ob.apply_diff(op["bids"], op["asks"], op["last_update_id"])
    else:
        raise ValueError(f"unknown op {op['op']!r}")


def run_case(case):
    """Fresh book, record state[0]=empty, then state[i+1]=after op i. Plus rolling digest."""
    ob = Orderbook()
    states = [capture(ob)]  # index 0: fresh empty book
    for op in case["ops"]:
        apply_op(ob, op)
        states.append(capture(ob))
    # Rolling transcript digest over every state hash in order (fresh book first).
    # Catches diverge-then-reconverge: any intermediate mismatch changes the digest.
    h = hashlib.sha256()
    for s in states:
        h.update(s["state_hash"].encode())
    return {"name": case["name"], "ops": case["ops"], "states": states,
            "rolling_digest": h.hexdigest()}


# --- Synthetic cases (8-decimal strings, exactly as Binance sends them) ---
CASES = [
    {
        "name": "empty_book",
        "ops": [],
    },
    {
        "name": "snapshot_out_of_order_sort",
        # Deliberately unsorted input, serialization must be bids desc, asks asc.
        "ops": [{
            "op": "snapshot",
            "bids": [["74574.60000000", "0.50000000"],
                     ["74574.79000000", "1.41823000"],
                     ["74574.78000000", "0.00283000"]],
            "asks": [["74575.10000000", "2.00000000"],
                     ["74574.90000000", "0.30000000"],
                     ["74575.00000000", "0.10000000"]],
            "last_update_id": 92090441860,
        }],
    },
    {
        "name": "snapshot_then_update_add_remove",
        "ops": [
            {"op": "snapshot",
             "bids": [["74574.79000000", "1.41823000"], ["74574.78000000", "0.00283000"]],
             "asks": [["74574.90000000", "0.30000000"], ["74575.00000000", "0.10000000"]],
             "last_update_id": 92090441860},
            # update existing bid, add a new lower bid, remove an ask (qty 0),
            # and remove a nonexistent bid (pop default, no error).
            {"op": "diff",
             "bids": [["74574.79000000", "2.00000000"],
                      ["74574.50000000", "0.05000000"],
                      ["74000.00000000", "0.00000000"]],
             "asks": [["74575.00000000", "0.00000000"]],
             "last_update_id": 92090441861},
        ],
    },
    {
        "name": "resnapshot_clears_book",
        "ops": [
            {"op": "snapshot",
             "bids": [["74574.79000000", "1.41823000"]],
             "asks": [["74574.90000000", "0.30000000"]],
             "last_update_id": 92090441860},
            {"op": "diff",
             "bids": [["74574.50000000", "0.05000000"]], "asks": [],
             "last_update_id": 92090441861},
            # full replace with a different book
            {"op": "snapshot",
             "bids": [["74580.00000000", "0.70000000"]],
             "asks": [["74581.00000000", "0.90000000"]],
             "last_update_id": 92090450000},
        ],
    },
    {
        "name": "crossed_book_is_stored_as_is",
        # Python replicates whatever it is given. It does not reject a crossed book.
        "ops": [{
            "op": "snapshot",
            "bids": [["74575.50000000", "1.00000000"]],
            "asks": [["74575.00000000", "1.00000000"]],
            "last_update_id": 92090441900,
        }],
    },
    {
        "name": "min_qty_and_1e6_boundary",
        # Smallest nonzero qty seen in data is 1e-5, 1e-6 is the plain/scientific
        # boundary and still renders plain. Anything < 1e-6 is out of contract.
        "ops": [{
            "op": "snapshot",
            "bids": [["74574.79000000", "0.00001000"]],
            "asks": [["74574.90000000", "0.00000100"]],
            "last_update_id": 92090441860,
        }],
    },
    {
        "name": "remove_all_back_to_empty",
        "ops": [
            {"op": "snapshot",
             "bids": [["74574.79000000", "1.41823000"]],
             "asks": [["74574.90000000", "0.30000000"]],
             "last_update_id": 92090441860},
            {"op": "diff",
             "bids": [["74574.79000000", "0.00000000"]],
             "asks": [["74574.90000000", "0.00000000"]],
             "last_update_id": 92090441861},
        ],
    },
]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = [run_case(c) for c in CASES]

    # Global rolling digest across all case digests, in declared order: one number
    # the C++ suite can compare to prove the whole vector set matches.
    g = hashlib.sha256()
    for c in cases:
        g.update(c["rolling_digest"].encode())
    global_digest = g.hexdigest()

    payload = {
        "contract_version": CONTRACT_VERSION,
        "source": FROZEN_NOTE,
        "scale": SCALE,
        "serialization": {
            "hash_algorithm": "sha256",
            "encoding": "utf-8",
            "json_separators": [",", ":"],
            "key_order": ["bids", "asks", "last_update_id"],
            "bid_order": "descending_price_best_first",
            "ask_order": "ascending_price_best_first",
            "level_form": '["<price>","<qty>"]',
            "last_update_id_form": "bare JSON integer, or null when unset",
        },
        "rolling_digest_definition": (
            "sha256 over the ASCII state_hash hex of every state in order "
            "(fresh empty book first, then after each op); per case, then a "
            "global sha256 over the per-case rolling digests in declared order"
        ),
        "global_rolling_digest": global_digest,
        "cases": cases,
    }

    text = json.dumps(payload, indent=2, sort_keys=False)
    VECTORS_PATH.write_text(text + "\n")

    checksum = hashlib.sha256(VECTORS_PATH.read_bytes()).hexdigest()
    CHECKSUM_PATH.write_text(f"{checksum}  golden_vectors.json\n")

    print(f"wrote {VECTORS_PATH} ({len(cases)} cases)")
    print(f"global_rolling_digest: {global_digest}")
    print(f"file sha256:           {checksum}")


if __name__ == "__main__":
    main()
