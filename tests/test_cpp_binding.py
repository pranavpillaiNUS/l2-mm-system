"""Optional in-process native storage: public behavior, exact bytes, and failures."""

from decimal import Decimal, localcontext
import gc
import hashlib
import json
import os
from pathlib import Path
import random
import weakref

import pytest

from src.replay import cpp_orderbook as binding
from src.replay.orderbook import Orderbook


VECTORS = json.loads((Path(__file__).resolve().parents[1]
                      / "cpp/parity/golden_vectors.json").read_text())


@pytest.fixture(scope="module")
def native_available():
    try:
        binding._module_path()
    except ImportError:
        if "L2MM_CPP_MODULE" in os.environ:
            pytest.fail("explicit L2MM_CPP_MODULE must exist and load successfully")
        pytest.skip("build the CPython extension with make cpp-build to enable binding tests")
    # A present but broken build is a test failure, never an optional skip.
    return binding.native_build_info()


def new_native():
    return binding.CppOrderbook(Decimal("0.01"), Decimal("0.00000001"))


def apply(book, operation):
    getattr(book, "apply_" + operation["op"])(
        operation["bids"], operation["asks"], operation["last_update_id"]
    )


def capture(book):
    return {
        "bids": [[str(price), str(qty)] for price, qty in book.bid_levels(2**64)],
        "asks": [[str(price), str(qty)] for price, qty in book.ask_levels(2**64)],
        "last_update_id": book.last_update_id,
    }


def compare(reference, native):
    assert capture(native) == capture(reference)
    assert native.canonical_bytes() == json.dumps(capture(reference), separators=(",", ":")).encode()
    assert native.state_hash() == reference.state_hash()
    for attribute in ("best_bid", "best_ask", "best_bid_qty", "best_ask_qty", "mid",
                      "spread", "microprice", "sequence", "last_update_id",
                      "bid_count", "ask_count"):
        assert getattr(native, attribute) == getattr(reference, attribute), attribute
    assert len(native) == len(reference)
    assert native.is_crossed() == reference.is_crossed()
    for limit in (-1, 0, 1, 3, 10):
        assert native.bid_levels(limit) == reference.bid_levels(limit)
        assert native.ask_levels(limit) == reference.ask_levels(limit)
    for price, qty in reference.bid_levels(2**64):
        assert native.bid_quantity(price) == qty
    for price, qty in reference.ask_levels(2**64):
        assert native.ask_quantity(price) == qty
    for absent in (Decimal(-1), Decimal("0.000000001"), Decimal("999999999999")):
        assert native.bid_quantity(absent) == native.ask_quantity(absent) == Decimal(0)
    # Public queue diagnostics serialize Decimal directly. Equal numeric zeros
    # with different exponents (0 versus 0E-8) are not byte-equivalent streams.
    for absent in (Decimal("0"), Decimal("1.00"), Decimal("999999999999")):
        assert str(native.bid_quantity(absent)) == str(reference.bid_quantity(absent)) == "0"
        assert str(native.ask_quantity(absent)) == str(reference.ask_quantity(absent)) == "0"


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda case: case["name"])
def test_binding_matches_golden_bytes_and_complete_public_book(native_available, case):
    reference, native = Orderbook(), new_native()
    compare(reference, native)
    hashes = hashlib.sha256()
    hashes.update(native.state_hash().encode())
    for operation, expected in zip(case["ops"], case["states"][1:]):
        apply(reference, operation)
        apply(native, operation)
        compare(reference, native)
        assert native.canonical_bytes().decode() == expected["canonical_bytes"]
        hashes.update(native.state_hash().encode())
    assert hashes.hexdigest() == case["rolling_digest"]


@pytest.mark.parametrize("seed", [0, 42, 20260911])
def test_binding_random_states_and_decimal_context(native_available, seed):
    rng = random.Random(seed)
    prices = [f"{Decimal('70000') + Decimal(i) / 100:.8f}" for i in range(-12, 13)]
    quantities = ["0.00000000", "0.00000100", "0.12345678", "1.00000000", "9.10000000"]
    reference, native = Orderbook(), new_native()
    for index in range(240):
        operation = {"op": "snapshot" if index % 19 == 0 else "diff",
                     "last_update_id": 92_000_000_000 + index}
        for side in ("bids", "asks"):
            operation[side] = [[rng.choice(prices), rng.choice(quantities)]
                               for _ in range(rng.randrange(9))]
        if index % 17 == 0:
            operation["bids"] += [[prices[0], "1.00000000"], [prices[0], "0.00000000"]]
        apply(reference, operation)
        apply(native, operation)
        with localcontext() as context:
            context.prec = (7, 28, 50)[index % 3]
            compare(reference, native)


@pytest.mark.parametrize("method", ["apply_snapshot", "apply_diff"])
@pytest.mark.parametrize("bad_levels,error", [
    ([("100.0", "1.00000000")], ValueError),
    ([("100.00000000", "0.00000001")], ValueError),
    ([("100.00500000", "1.00000000")], ValueError),
    ([("92233720368.54775808", "1.00000000")], OverflowError),
    ([("100.00000000", "-1.00000000")], ValueError),
    ([("100.00000000\0", "1.00000000")], ValueError),
    ([("100.00000000",)], ValueError),
    ([(Decimal(100), "1.00000000")], TypeError),
    ([None], TypeError),
])
def test_invalid_update_on_second_side_preserves_entire_state(native_available, method, bad_levels, error):
    book = new_native()
    book.apply_snapshot([("100.00000000", "1.00000000")],
                        [("101.00000000", "2.00000000")], 10)
    before = (capture(book), book.sequence, book.state_hash(), book.microprice)
    with pytest.raises(error):
        getattr(book, method)([("99.00000000", "3.00000000")], bad_levels, 11)
    assert (capture(book), book.sequence, book.state_hash(), book.microprice) == before


@pytest.mark.parametrize("identifier,error", [(-1, OverflowError), (2**64, OverflowError),
                                             ("1", TypeError), (1.0, TypeError)])
def test_update_id_is_checked_before_mutation(native_available, identifier, error):
    book = new_native()
    book.apply_snapshot([], [], 2**64 - 1)
    before = book.state_hash()
    with pytest.raises(error):
        book.apply_diff([("100.00000000", "1.00000000")], [], identifier)
    assert book.last_update_id == 2**64 - 1
    assert book.sequence == 0
    assert book.state_hash() == before


@pytest.mark.parametrize("value,error", [("0", ValueError), ("-0.01", ValueError),
                                       ("NaN", ValueError), ("Infinity", ValueError),
                                       ("0.000000001", ValueError),
                                       ("1e100", OverflowError), (0.01, TypeError)])
def test_instrument_metadata_never_rounds_or_uses_float(value, error):
    with pytest.raises(error):
        binding.CppOrderbook(value, "0.00000001")
    with pytest.raises(error):
        binding.CppOrderbook("0.01", value)


def test_step_metadata_is_enforced(native_available):
    book = binding.CppOrderbook("0.010000000000", "0.001")
    with pytest.raises(ValueError, match="qty_step"):
        book.apply_snapshot([("100.00000000", "0.00001000")], [], 1)
    assert book.last_update_id is None


def test_native_instances_own_independent_lifetimes_and_read_only_state(native_available):
    references = []
    survivor = new_native()
    for identifier in range(80):
        book = new_native()
        book.apply_snapshot([("100.00000000", "1.00000000")], [], identifier)
        references.append(weakref.ref(book))
        assert book.last_update_id == identifier
    del book
    gc.collect()
    assert all(reference() is None for reference in references)
    assert len(survivor) == 0
    assert survivor.last_update_id is None
    assert "0b/0a" in repr(survivor)
    with pytest.raises(AttributeError):
        survivor.last_update_id = 1
    with pytest.raises(ValueError):
        survivor._native.state(None)


def test_build_provenance_identifies_loaded_extension(native_available):
    info = binding.native_build_info()
    assert info["api_version"] == 1
    assert info["compiler"]
    assert info["build_type"]
    assert info["path"] == info["module_path"]
    assert info["sha256"] == info["module_sha256"]
    assert info["sha256"] == hashlib.sha256(Path(info["path"]).read_bytes()).hexdigest()
    info["api_version"] = -1
    assert binding.native_build_info()["api_version"] == 1


def test_explicit_missing_extension_fails_without_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("L2MM_CPP_MODULE", str(tmp_path / "missing.so"))
    with pytest.raises(ImportError, match="L2MM_CPP_MODULE"):
        new_native()


def test_explicit_python_file_is_rejected_without_execution(monkeypatch, tmp_path):
    module = tmp_path / "_l2mm_book.py"
    module.write_text("raise AssertionError('must never execute')\n")
    monkeypatch.setenv("L2MM_CPP_MODULE", str(module))
    with pytest.raises(ImportError, match="compiled extension"):
        new_native()


def test_default_missing_extension_fails_without_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("L2MM_CPP_MODULE", raising=False)
    monkeypatch.setattr(binding, "_DEFAULT_BUILD", tmp_path)
    with pytest.raises(ImportError, match="make cpp-build"):
        new_native()
