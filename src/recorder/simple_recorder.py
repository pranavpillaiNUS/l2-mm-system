"""
Minimal L2 recorder for Binance depth stream.

Captures raw WebSocket messages to gzipped JSON files.
Run in background, keep pc on

Usage:
    python -m src.recorder.simple_recorder --symbol btcusdt
"""
import asyncio
import websockets
import json
import gzip
from datetime import datetime
from pathlib import Path
from typing import Optional
import argparse
import signal


class SimpleRecorder:
    """Records Binance depth stream to gzipped files."""
    
    WS_URL = "wss://stream.binance.com:9443/ws"
    
    def __init__(self, symbol: str, output_dir: Path):
        self.symbol = symbol.lower()
        self.output_dir = output_dir / "raw" / self.symbol
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self._running = False
        self._current_file: Optional[gzip.GzipFile] = None
        self._current_hour: Optional[int] = None
        self._message_count = 0
        self._total_messages = 0
    
    def _get_ws_url(self) -> str:
        return f"{self.WS_URL}/{self.symbol}@depth@100ms"
    
    def _rotate_file(self, now: datetime) -> None:
        """Rotate to new file every hour."""
        if self._current_hour != now.hour or self._current_file is None:
            if self._current_file:
                self._current_file.close()
                print(f"Rotated. Messages in last file: {self._message_count}")
            
            filename = f"{self.symbol}_depth_{now.strftime('%Y%m%d_%H')}00.jsonl.gz"
            filepath = self.output_dir / filename
            self._current_file = gzip.open(filepath, 'at', encoding='utf-8')
            self._current_hour = now.hour
            self._message_count = 0
            print(f"Writing to: {filepath.name}")
    
    def _write_message(self, data: dict, recv_time: datetime) -> None:
        """Write message to file."""
        self._rotate_file(recv_time)
        
        record = {
            "recv_time": recv_time.isoformat(),
            "data": data,
        }
        self._current_file.write(json.dumps(record) + "\n")
        self._message_count += 1
        self._total_messages += 1
        
        # Progress every 1000 messages
        if self._total_messages % 1000 == 0:
            print(f"  [{recv_time.strftime('%H:%M:%S')}] Total: {self._total_messages:,} messages")
    
    async def run(self) -> None:
        """Main loop with reconnection."""
        self._running = True
        reconnect_delay = 1.0
        
        print(f"\nStarting recorder for {self.symbol.upper()}")
        print(f"   Output: {self.output_dir}")
        print(f"   Press Ctrl+C to stop\n")
        
        while self._running:
            try:
                print(f"Connecting to {self.symbol}@depth...")
                async with websockets.connect(
                    self._get_ws_url(),
                    ping_interval=30,
                    ping_timeout=60,
                ) as ws:
                    print("Connected! Recording...\n")
                    reconnect_delay = 1.0
                    
                    async for message in ws:
                        if not self._running:
                            break
                        recv_time = datetime.utcnow()
                        try:
                            data = json.loads(message)
                            self._write_message(data, recv_time)
                        except json.JSONDecodeError as e:
                            print(f"JSON error: {e}")
                            
            except websockets.ConnectionClosed as e:
                print(f"\nDisconnected: {e}. Reconnecting in {reconnect_delay}s...")
            except Exception as e:
                print(f"\nError: {e}. Reconnecting in {reconnect_delay}s...")
            
            if self._running:
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
    
    def stop(self) -> None:
        """Graceful shutdown."""
        print("\n\nStopping recorder...")
        self._running = False
        if self._current_file:
            self._current_file.close()
        print(f"Total messages recorded: {self._total_messages:,}")


async def main():
    parser = argparse.ArgumentParser(description="Binance L2 Recorder")
    parser.add_argument("--symbol", default="btcusdt", help="Trading pair")
    parser.add_argument("--output-dir", default="data", help="Output directory")
    args = parser.parse_args()
    
    recorder = SimpleRecorder(
        symbol=args.symbol,
        output_dir=Path(args.output_dir),
    )
    
    # Handle Ctrl+C
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, recorder.stop)
    
    await recorder.run()


if __name__ == "__main__":
    asyncio.run(main())