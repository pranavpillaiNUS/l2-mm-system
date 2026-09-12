"""Explicit book selection shared by replay entry points.

Execution semantics and Decimal accounting are independent of storage. Backend
identity travels with artifacts and caches so implementations cannot be mixed
without an explicit parity comparison.
"""

import hashlib
from decimal import Decimal
from pathlib import Path

from src.replay.orderbook import Orderbook


def create_orderbook(backend: str, tick_size: Decimal, qty_step: Decimal):
    if backend == "python":
        return Orderbook()
    if backend == "cpp":
        from src.replay.cpp_orderbook import CppOrderbook
        return CppOrderbook(tick_size=tick_size, qty_step=qty_step)
    raise ValueError("book_backend must be 'python' or 'cpp'")


def book_provenance(backend="python", tick_size=Decimal("0.01"),
                    qty_step=Decimal("0.00000001")) -> dict:
    if backend not in {"python", "cpp"}:
        raise ValueError("book_backend must be 'python' or 'cpp'")
    result = {
        "backend": backend,
        "tick_size": format(Decimal(tick_size), "f"),
        "qty_step": format(Decimal(qty_step), "f"),
        "derived_arithmetic": "python_decimal",
        "python_reference_sha256": hashlib.sha256(
            Path(__file__).with_name("orderbook.py").read_bytes()
        ).hexdigest(),
    }
    if backend == "cpp":
        from src.replay.cpp_orderbook import native_build_info
        result["native"] = native_build_info()
    return result


def add_book_arguments(parser) -> None:
    parser.add_argument("--book-backend", choices=("python", "cpp"), default="python")
    parser.add_argument("--book-tick-size", default="0.01",
                        help="Native input price grid; independent of quote rounding")
    parser.add_argument("--book-qty-step", default="0.00000001")


def book_kwargs(args) -> dict:
    return {
        "book_backend": getattr(args, "book_backend", "python"),
        "book_tick_size": Decimal(getattr(args, "book_tick_size", "0.01")),
        "book_qty_step": Decimal(getattr(args, "book_qty_step", "0.00000001")),
    }


def book_provenance_from_args(args) -> dict:
    options = book_kwargs(args)
    return book_provenance(options["book_backend"], options["book_tick_size"],
                           options["book_qty_step"])


def separate_backend_output(args) -> None:
    """Native command outputs live below a backend subdirectory by default."""
    if (getattr(args, "book_backend", "python") == "cpp"
            and "cpp" not in args.output_dir.parts):
        args.output_dir = args.output_dir / "cpp"
