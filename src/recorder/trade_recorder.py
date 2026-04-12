"""
Trade recorder for Binance aggTrade stream.

Captures aggregated trades to gzipped JSONL files.
Same pattern as simple_recorder.py but for trade data.

Usage:
    python -m src.recorder.trade_recorder --symbol btcusdt
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


class TradeRecorder:
    """Records Binance aggTrade stream to gzipped files."""

    WS_URL = "wss://stream.binance.com:9443/ws"

    def __init__(self, symbol: str, output_dir: Path):
        self.symbol = symbol.lower()
        # trades go in their own subfolder next to depth data
        self.output_dir = output_dir / "raw" / f"{self.symbol}_trades"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._running = False
        self._current_file: Optional[gzip.GzipFile] = None
        self._current_hour: Optional[int] = None
        self._message_count = 0
        self._total_messages = 0

    def _get_ws_url(self) -> str:
        # aggTrade stream - one message per aggregated trade
        return f"{self.WS_URL}/{self.symbol}@aggTrade"

    def _rotate_file(self, now: datetime) -> None:
        """Rotate to new file every hour."""
        if self._current_hour != now.hour or self._current_file is None:
            if self._current_file:
                self._current_file.close()
                print(f"Rotated. Trades in last file: {self._message_count}")

            filename = f"{self.symbol}_trades_{now.strftime('%Y%m%d_%H')}00.jsonl.gz"
            filepath = self.output_dir / filename
            self._current_file = gzip.open(filepath, 'at', encoding='utf-8')
            self._current_hour = now.hour
            self._message_count = 0
            print(f"Writing to: {filepath.name}")

    def _write_message(self, data: dict, recv_time: datetime) -> None:
        """Write trade to file."""
        self._rotate_file(recv_time)

        record = {
            "recv_time": recv_time.isoformat(),
            "data": data,
        }
        self._current_file.write(json.dumps(record) + "\n")
        self._message_count += 1
        self._total_messages += 1

        if self._total_messages % 5000 == 0:
            print(f"  [{recv_time.strftime('%H:%M:%S')}] Total: {self._total_messages:,} trades")

    async def run(self) -> None:
        """Main loop with reconnection."""
        self._running = True
        reconnect_delay = 1.0

        print(f"\nStarting trade recorder for {self.symbol.upper()}")
        print(f"   Output: {self.output_dir}")
        print(f"   Press Ctrl+C to stop\n")

        while self._running:
            try:
                print(f"Connecting to {self.symbol}@aggTrade...")
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
        print("\n\nStopping trade recorder...")
        self._running = False
        if self._current_file:
            self._current_file.close()
        print(f"Total trades recorded: {self._total_messages:,}")


async def main():
    parser = argparse.ArgumentParser(description="Binance Trade Recorder")
    parser.add_argument("--symbol", default="btcusdt", help="Trading pair")
    parser.add_argument("--output-dir", default="data", help="Output directory")
    args = parser.parse_args()

    recorder = TradeRecorder(
        symbol=args.symbol,
        output_dir=Path(args.output_dir),
    )

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, recorder.stop)

    await recorder.run()


if __name__ == "__main__":
    asyncio.run(main())