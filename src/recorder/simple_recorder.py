"""
Minimal L2 recorder for Binance depth stream.

Captures raw WebSocket messages to gzipped JSON files.
Also fetches REST snapshots at connect and every hour for book reconstruction.
Run in background, keep pc on

Usage:
    python -m src.recorder.simple_recorder --symbol btcusdt
"""
import asyncio
import websockets
import json
import gzip
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional
import argparse
import signal


class SimpleRecorder:
    """Records Binance depth stream to gzipped files."""

    # Spot and USD-M perpetual futures endpoints. Spot is the default; perp
    # writes to a separate dataset so the two raw trees can never collide.
    SPOT_WS_URL = "wss://stream.binance.com:9443/ws"
    SPOT_REST_URL = "https://api.binance.com/api/v3/depth"
    PERP_WS_URL = "wss://fstream.binance.com/ws"
    PERP_REST_URL = "https://fapi.binance.com/fapi/v1/depth"

    def __init__(self, symbol: str, output_dir: Path, market: str = "spot"):
        if market not in ("spot", "perp"):
            raise ValueError(f"market must be 'spot' or 'perp', got {market!r}")
        self.symbol = symbol.lower()
        self.market = market
        # Perp gets its own dataset key so depth files land in
        # data/raw/btcusdt_perp/ and never in the spot data/raw/btcusdt/ tree.
        self.dataset = self.symbol if market == "spot" else f"{self.symbol}_perp"
        self._ws_url = self.SPOT_WS_URL if market == "spot" else self.PERP_WS_URL
        self._rest_url = self.SPOT_REST_URL if market == "spot" else self.PERP_REST_URL
        self.output_dir = output_dir / "raw" / self.dataset
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._running = False
        self._current_file: Optional[gzip.GzipFile] = None
        self._current_hour: Optional[int] = None
        self._message_count = 0
        self._total_messages = 0

    def _get_ws_url(self) -> str:
        return f"{self._ws_url}/{self.symbol}@depth@100ms"
    
    def _fetch_snapshot(self) -> Optional[dict]:
        """Fetch full orderbook snapshot from REST API.
        Returns the snapshot dict or None if it fails.
        The snapshot has lastUpdateId, bids, and asks - this is
        the reference point we need to reconstruct the book from diffs."""
        url = f"{self._rest_url}?symbol={self.symbol.upper()}&limit=1000"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                print(f"  Snapshot fetched: lastUpdateId={data['lastUpdateId']}, "
                      f"{len(data['bids'])} bids, {len(data['asks'])} asks")
                return data
        except Exception as e:
            print(f"  Snapshot fetch failed: {e}")
            return None
    
    def _write_snapshot(self, recv_time: datetime) -> None:
        """Fetch and write a snapshot to the current file.
        Tagged with type=snapshot so replay engine can tell it apart from diffs."""
        snapshot = self._fetch_snapshot()
        if snapshot and self._current_file:
            record = {
                "recv_time": recv_time.isoformat(),
                "type": "snapshot",
                "data": snapshot,
            }
            self._current_file.write(json.dumps(record) + "\n")
            self._current_file.flush()
    
    def _rotate_file(self, now: datetime) -> None:
        """Rotate to new file every hour."""
        if self._current_hour != now.hour or self._current_file is None:
            if self._current_file:
                self._current_file.close()
                print(f"Rotated. Messages in last file: {self._message_count}")
            
            filename = f"{self.dataset}_depth_{now.strftime('%Y%m%d_%H')}00.jsonl.gz"
            filepath = self.output_dir / filename
            self._current_file = gzip.open(filepath, 'at', encoding='utf-8')
            self._current_hour = now.hour
            self._message_count = 0
            print(f"Writing to: {filepath.name}")
            
            # snapshot at the start of each file so we have a resync point
            self._write_snapshot(now)
    
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
                    
                    # snapshot right after connecting - this is the anchor
                    # for all diffs that follow until the next snapshot
                    now = datetime.utcnow()
                    self._rotate_file(now)
                    
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
    parser.add_argument("--market", default="spot", choices=["spot", "perp"],
                        help="spot (default) or perp (USD-M futures)")
    args = parser.parse_args()

    recorder = SimpleRecorder(
        symbol=args.symbol,
        output_dir=Path(args.output_dir),
        market=args.market,
    )
    
    # Handle Ctrl+C
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, recorder.stop)
    
    await recorder.run()


if __name__ == "__main__":
    asyncio.run(main())