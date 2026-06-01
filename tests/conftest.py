"""Shared deterministic test fixtures."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def bar_data_dir(tmp_path: Path) -> Path:
    """Create a small synthetic OHLCV CSV directory for bar-based tests."""
    data_dir = tmp_path / "bars"
    data_dir.mkdir()
    path = data_dir / "BTCUSDT_fixture.csv"
    start = datetime(2024, 1, 1)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["open_time", "open", "high", "low", "close", "volume"],
        )
        writer.writeheader()
        for offset in range(120):
            open_price = 40_000 + offset * 8 + (offset % 12 - 6) * 30
            close_price = open_price + (120 if offset % 8 < 4 else -100)
            writer.writerow(
                {
                    "open_time": (start + timedelta(hours=offset)).isoformat(),
                    "open": open_price,
                    "high": max(open_price, close_price) + 50,
                    "low": min(open_price, close_price) - 50,
                    "close": close_price,
                    "volume": 10 + offset,
                }
            )

    return data_dir
