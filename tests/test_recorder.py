"""
Test recorder module (quick connection test)
"""
import asyncio
from pathlib import Path
from src.recorder.simple_recorder import SimpleRecorder


async def test_recorder_connection():
    """Test that recorder can connect and receive messages."""
    print("=" * 60)
    print("TEST: Recorder Connection (10 seconds)")
    print("=" * 60)
    
    output_dir = Path("data")
    recorder = SimpleRecorder(symbol="btcusdt", output_dir=output_dir)
    
    # Run for 10 seconds
    async def stop_after_delay():
        await asyncio.sleep(10)
        recorder.stop()
    
    # Run both concurrently
    await asyncio.gather(
        recorder.run(),
        stop_after_delay(),
    )
    
    # Check output
    files = list((output_dir / "raw" / "btcusdt").glob("*.jsonl.gz"))
    print(f"\n� Files created: {len(files)}")
    
    if files:
        print(f"Recorder test passed!")
    else:
        print(f"No files created - check connection")


if __name__ == "__main__":
    asyncio.run(test_recorder_connection())