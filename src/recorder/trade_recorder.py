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

    # Spot and USD-M perpetual futures share the aggTrade stream shape but live
    # on different hosts. Spot is the default, perp writes to its own dataset.
    SPOT_WS_URL = "wss://stream.binance.com:9443/ws"
    PERP_WS_URL = "wss://fstream.binance.com/ws"

    def __init__(self, symbol: str, output_dir: Path, market: str = "spot"):
        if market not in ("spot", "perp"):
            raise ValueError(f"market must be 'spot' or 'perp', got {market!r}")
        self.symbol = symbol.lower()
        self.market = market
        # Perp gets its own dataset key so trades land in
        # data/raw/btcusdt_perp_trades/ and never in the spot trades tree.
        self.dataset = self.symbol if market == "spot" else f"{self.symbol}_perp"
        self._ws_url = self.SPOT_WS_URL if market == "spot" else self.PERP_WS_URL
        # trades go in their own subfolder next to depth data
        self.output_dir = output_dir / "raw" / f"{self.dataset}_trades"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._running = False
        self._current_file: Optional[gzip.GzipFile] = None
        self._current_hour: Optional[int] = None
        self._message_count = 0
        self._total_messages = 0

    def _get_ws_url(self) -> str:
        # Spot publishes aggregated trades on @aggTrade (one message per
        # aggregated trade). The USD-M futures feed in this environment does
        # not populate @aggTrade (verified: 0 messages over 25s while @trade,
        # @depth, and @bookTicker all stream normally), so perp captures the
        # raw per-fill @trade stream instead. Raw trades are strictly more
        # granular than aggTrades (one record per fill, no same-price
        # aggregation). The perp trade parser will account for this difference.
        stream = "aggTrade" if self.market == "spot" else "trade"
        return f"{self._ws_url}/{self.symbol}@{stream}"

    def _rotate_file(self, now: datetime) -> None:
        """Rotate to new file every hour."""
        if self._current_hour != now.hour or self._current_file is None:
            if self._current_file:
                self._current_file.close()
                print(f"Rotated. Trades in last file: {self._message_count}")

            filename = f"{self.dataset}_trades_{now.strftime('%Y%m%d_%H')}00.jsonl.gz"
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
                print(f"Connecting to {self._get_ws_url().rsplit('/', 1)[-1]}...")
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
    parser.add_argument("--market", default="spot", choices=["spot", "perp"],
                        help="spot (default) or perp (USD-M futures)")
    args = parser.parse_args()

    recorder = TradeRecorder(
        symbol=args.symbol,
        output_dir=Path(args.output_dir),
        market=args.market,
    )

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, recorder.stop)

    await recorder.run()


if __name__ == "__main__":
    asyncio.run(main())