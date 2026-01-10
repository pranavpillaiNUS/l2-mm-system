import argparse
import os
from datetime import datetime
import yaml

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def utc_date() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(args.config, "r") as f:
        cfg = yaml.safe_load(f)

    exchange = cfg.get("exchange", "binance_spot")
    symbol = cfg.get("symbol") or (cfg.get("symbols", ["BTCUSDT"])[0])
    output_root = cfg.get("output_root", "data_raw/recordings")

    base_dir = os.path.join(
        output_root,
        f"exchange={exchange}",
        f"symbol={symbol}",
        f"date={utc_date()}",
    )

    for sd in ["events_raw", "events_norm", "snapshots", "health"]:
        ensure_dir(os.path.join(base_dir, sd))

    print("DRY_RUN_OK")
    print("base_dir:", base_dir)

    if args.dry_run:
        return

    raise SystemExit("Non-dry run not implemented yet.")

if __name__ == "__main__":
    main()
