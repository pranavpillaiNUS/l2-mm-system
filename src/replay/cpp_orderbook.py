"""Optional native storage with the reference book's exact Decimal arithmetic.

Build with ``make cpp-build`` for this Python interpreter. Selecting this backend
requires the compiled module; it never silently falls back to Python storage.
The input-level domain remains the frozen, unsigned fixed-eight BTCUSDT domain.
"""

from decimal import Decimal
from functools import lru_cache
import hashlib
import importlib.machinery
import importlib.util
import operator
import os
from pathlib import Path
from types import ModuleType

from src.replay.orderbook import Orderbook


_DEFAULT_BUILD = Path(__file__).resolve().parents[2] / "cpp" / "build"
_ZERO = Decimal(0)
_MAX_SCALED_VALUE = Decimal("92233720368.54775807")


def _module_path() -> Path:
    explicit = os.environ.get("L2MM_CPP_MODULE")
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        if not explicit or not path.is_file():
            raise ImportError(f"required L2MM_CPP_MODULE is unavailable: {explicit!r}")
        return path
    for suffix in importlib.machinery.EXTENSION_SUFFIXES:
        path = _DEFAULT_BUILD / f"_l2mm_book{suffix}"
        if path.is_file():
            return path.resolve()
    raise ImportError(
        "C++ order-book backend is unavailable; run make cpp-build with the "
        "current Python interpreter, or set L2MM_CPP_MODULE to its extension file"
    )


@lru_cache(maxsize=None)
def _load_path(path: Path) -> tuple[ModuleType, dict]:
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        specification = importlib.util.spec_from_file_location("_l2mm_book", path)
        if specification is None or specification.loader is None:
            raise ImportError("path is not a Python extension module")
        if not isinstance(specification.loader, importlib.machinery.ExtensionFileLoader):
            raise ImportError("path must identify a compiled extension module")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        information = module.build_info()
        if information.get("api_version") != 1:
            raise ImportError("unsupported native order-book API version")
    except (ImportError, AttributeError, OSError) as error:
        raise ImportError(f"cannot load C++ order-book module {path}: {error}") from error
    if hashlib.sha256(path.read_bytes()).hexdigest() != fingerprint:
        raise ImportError(f"C++ order-book module changed while loading: {path}")
    return module, {**information, "path": str(path), "sha256": fingerprint,
                    "module_path": str(path), "module_sha256": fingerprint}


def _load_native() -> tuple[ModuleType, dict]:
    return _load_path(_module_path())


def native_build_info() -> dict:
    """Identify the loaded extension, including the bytes hashed at load time."""
    return dict(_load_native()[1])


def _metadata_fixed8(value: Decimal | str, name: str) -> str:
    if not isinstance(value, (Decimal, str)):
        raise TypeError(f"{name} must be a Decimal or decimal string")
    value = Decimal(value)
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    if value > _MAX_SCALED_VALUE:
        raise OverflowError(f"{name} exceeds the scaled int64 range")
    formatted = format(value, ".8f")
    if Decimal(formatted) != value:
        raise ValueError(f"{name} must be an exact multiple of 0.00000001")
    return formatted


def _lookup_price(price: Decimal) -> str | None:
    if not isinstance(price, Decimal):
        raise TypeError("price lookup requires Decimal")
    # Off-grid and out-of-range values cannot exist in native storage. Returning
    # zero for them preserves the reference dictionary's missing-key semantics.
    if not price.is_finite() or price < 0 or price > _MAX_SCALED_VALUE:
        return None
    formatted = format(price, ".8f")
    return formatted if Decimal(formatted) == price else None


class CppOrderbook(Orderbook):
    """Native book storage; inherited mid/spread/microprice stay in Decimal.

    The capsule owns one C++ book and releases it when this adapter is collected.
    No mirror SortedDict is maintained. BBO Decimals and public counters are
    refreshed once per update. All native calls retain the GIL.
    """

    def __init__(self, tick_size: Decimal | str, qty_step: Decimal | str):
        tick = _metadata_fixed8(tick_size, "tick_size")
        step = _metadata_fixed8(qty_step, "qty_step")
        self._native, self._native_info = _load_native()
        self._handle = self._native.create(tick, step)
        self._cached_state = None
        self._refresh(self._native.state(self._handle))

    def _refresh(self, state):
        self._cached_state = tuple(
            Decimal(value) if value is not None else None for value in state[:4]
        ) + state[4:]

    def _public_state(self):
        if self._cached_state is None:
            self._refresh(self._native.state(self._handle))
        return self._cached_state

    def apply_snapshot(self, bids, asks, last_update_id: int) -> None:
        self._cached_state = None
        self._refresh(self._native.apply_snapshot(self._handle, bids, asks, last_update_id))

    def apply_diff(self, bids, asks, last_update_id: int) -> None:
        self._cached_state = None
        self._refresh(self._native.apply_diff(self._handle, bids, asks, last_update_id))

    @property
    def best_bid(self):
        return self._public_state()[0]

    @property
    def best_ask(self):
        return self._public_state()[1]

    @property
    def best_bid_qty(self):
        return self._public_state()[2]

    @property
    def best_ask_qty(self):
        return self._public_state()[3]

    @property
    def bid_count(self) -> int:
        return self._public_state()[4]

    @property
    def ask_count(self) -> int:
        return self._public_state()[5]

    @property
    def last_update_id(self) -> int | None:
        return self._public_state()[6]

    @property
    def sequence(self) -> int:
        return self._public_state()[7]

    def bid_quantity(self, price: Decimal) -> Decimal:
        formatted = _lookup_price(price)
        quantity = _ZERO if formatted is None else Decimal(self._native.bid_quantity(self._handle, formatted))
        return quantity if quantity else _ZERO

    def ask_quantity(self, price: Decimal) -> Decimal:
        formatted = _lookup_price(price)
        quantity = _ZERO if formatted is None else Decimal(self._native.ask_quantity(self._handle, formatted))
        return quantity if quantity else _ZERO

    def bid_levels(self, n: int = 10):
        count = max(0, min(operator.index(n), self.bid_count))
        return [(Decimal(price), Decimal(qty))
                for price, qty in self._native.bid_levels(self._handle, count)]

    def ask_levels(self, n: int = 10):
        count = max(0, min(operator.index(n), self.ask_count))
        return [(Decimal(price), Decimal(qty))
                for price, qty in self._native.ask_levels(self._handle, count)]

    def state_hash(self) -> str:
        return self._native.state_hash(self._handle)

    def canonical_bytes(self) -> bytes:
        return self._native.canonical_bytes(self._handle)

    def __len__(self) -> int:
        return self.bid_count + self.ask_count

    def __repr__(self) -> str:
        return (f"CppOrderbook(bid={self.best_bid}, ask={self.best_ask}, "
                f"spread={self.spread}, levels={self.bid_count}b/{self.ask_count}a)")
