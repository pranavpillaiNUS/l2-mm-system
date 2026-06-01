from pathlib import Path

import pytest

from src.backtester.data_loader import DataLoader


def test_load_data(bar_data_dir: Path):
    """Test loading BTCUSDT data."""
    loader = DataLoader(bar_data_dir)

    assert loader.list_available() == ["BTCUSDT_fixture"]

    df = loader.load("BTCUSDT")

    assert len(df) == 120
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.is_monotonic_increasing


def test_date_filter(bar_data_dir: Path):
    """Test date filtering."""
    loader = DataLoader(bar_data_dir)

    full = loader.load("BTCUSDT")
    one_day = loader.load(
        "BTCUSDT",
        start="2024-01-02T00:00:00",
        end="2024-01-02T23:00:00",
    )

    assert len(full) == 120
    assert len(one_day) == 24


def test_bar_iteration(bar_data_dir: Path):
    """Test bar-by-bar iteration."""
    loader = DataLoader(bar_data_dir)

    bars = list(loader.iter_bars("BTCUSDT"))

    assert len(bars) == 120
    assert bars[0].close == 39_940
    assert bars[0].is_bullish
    assert bars[0].bar_range == 220


def test_missing_data_directory_is_rejected(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="Data directory not found"):
        DataLoader(tmp_path / "missing")


if __name__ == "__main__":
    raise SystemExit("Run this file with pytest so its synthetic fixture is available.")
