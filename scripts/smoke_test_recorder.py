"""Run a manual network smoke test of the spot depth recorder."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from src.recorder.simple_recorder import SimpleRecorder


async def run_smoke_test(duration_seconds: float, output_dir: Path) -> None:
    recorder = SimpleRecorder(symbol="btcusdt", output_dir=output_dir)
    recorder_task = asyncio.create_task(recorder.run())
    try:
        await asyncio.sleep(duration_seconds)
    finally:
        recorder.stop()
        await recorder_task

    files = list((output_dir / "raw" / "btcusdt").glob("*.jsonl.gz"))
    nonempty = [path for path in files if path.stat().st_size > 0]
    if not nonempty:
        raise RuntimeError("the recorder produced no non-empty depth files")
    latest = max(nonempty, key=lambda path: path.stat().st_mtime)
    print(f"Recorder smoke test passed: {latest}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=float, default=10.0)
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.duration_seconds <= 0:
        raise ValueError("--duration-seconds must be positive")
    asyncio.run(run_smoke_test(args.duration_seconds, args.output_dir))


if __name__ == "__main__":
    main()
