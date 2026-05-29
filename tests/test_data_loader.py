from pathlib import Path
from src.backtester.data_loader import DataLoader


def test_load_data():
    """Test loading BTCUSDT data."""
    print("TEST: Load Data")
    
    loader = DataLoader(Path("data/bars"))
    
    # List available
    print(f"\nAvailable: {loader.list_available()}")
    
    # Load BTCUSDT
    df = loader.load("BTCUSDT")
    print(f"\nLoaded BTCUSDT:")
    print(f"  Bars: {len(df):,}")
    print(f"  Date range: {df.index[0]} to {df.index[-1]}")
    print(f"  Price range: ${df['low'].min():,.0f} - ${df['high'].max():,.0f}")
    
    print("\nTest passed")


def test_date_filter():
    """Test date filtering."""
    print("\nTEST: Date Filtering")
    
    loader = DataLoader(Path("data/bars"))
    
    full = loader.load("BTCUSDT")
    q1 = loader.load("BTCUSDT", start="2024-01-01", end="2024-03-31")
    
    print(f"\nFull dataset: {len(full):,} bars")
    print(f"Q1 only: {len(q1):,} bars")
    
    print("\nTest passed")


def test_bar_iteration():
    """Test bar-by-bar iteration."""
    print("\nTEST: Bar Iteration")
    
    loader = DataLoader(Path("data/bars"))
    
    print("\nFirst 5 bars:")
    for i, bar in enumerate(loader.iter_bars("BTCUSDT")):
        if i >= 5:
            break
        direction = "up" if bar.is_bullish else "down"
        print(f"  {bar.timestamp}: ${bar.close:,.0f} {direction}")
    
    print("\nTest passed")


if __name__ == "__main__":
    test_load_data()
    test_date_filter()
    test_bar_iteration()
    
    print("\nALL TESTS PASSED")