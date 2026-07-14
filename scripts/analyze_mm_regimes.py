"""Build a descriptive per-window failure-mode table for the V2 panel."""

import argparse
import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.execution.queue_credit import queue_credit_suffix
from src.execution.provenance import guard_frozen_v2_output_path


def _load_windows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _run_dir_name(args, start: datetime) -> str:
    run_id = (
        f"{args.symbol}_{args.strategy}_{start.strftime('%Y%m%d_%H')}_"
        f"{args.hours}h_{args.hours // args.session_hours}sessions_"
        f"hs{args.half_spread}_rq{args.requote_interval_ms}"
    )
    return f"{run_id}{queue_credit_suffix(args.queue_cancellation_credit)}"


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_csv_by(path: Path, key: str) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return {row[key]: row for row in rows if row.get(key)}


def _load_signal_metric(
    path: Path,
    signal: str = "microprice",
    *,
    artifact_suffix: str = "",
) -> dict[str, dict]:
    paths = [path] if path.is_file() else sorted(path.glob("*/regressions.csv"))
    if artifact_suffix:
        paths = [candidate for candidate in paths if candidate.parent.name.endswith(artifact_suffix)]
    elif signal == "ofi":
        paths = [candidate for candidate in paths if "_qc" not in candidate.parent.name]
    if not paths:
        return {}
    out = {}
    for candidate in paths:
        with candidate.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("window") == "pooled":
                    continue
                if row.get("horizon") != "1s":
                    continue
                if signal == "ofi" and row.get("signal") != "normalized_ofi":
                    continue
                out[row["window"]] = row
    return out


def _d(value) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _csv_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _fill_side_counts(run_dir: Path) -> tuple[int, int]:
    buy_ids = set()
    sell_ids = set()
    matched = run_dir / "matched_lots.csv"
    if matched.exists():
        with matched.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                for fill_key, side_key in (
                    ("open_fill_id", "open_side"),
                    ("close_fill_id", "close_side"),
                ):
                    if row[side_key] == "buy":
                        buy_ids.add(row[fill_key])
                    elif row[side_key] == "sell":
                        sell_ids.add(row[fill_key])
    open_lots = run_dir / "open_lots.csv"
    if open_lots.exists():
        with open_lots.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row["side"] == "buy":
                    buy_ids.add(row["fill_id"])
                elif row["side"] == "sell":
                    sell_ids.add(row["fill_id"])
    return len(buy_ids), len(sell_ids)


def _build_rows(args) -> list[dict]:
    windows = _load_windows(args.windows_csv)
    tail_by_window = _load_csv_by(args.tail_window_summary, "window")
    microprice_by_window = _load_signal_metric(args.microprice_regressions)
    ofi_by_window = _load_signal_metric(
        args.ofi_regressions,
        signal="ofi",
        artifact_suffix=queue_credit_suffix(args.queue_cancellation_credit),
    )

    rows = []
    for window in windows:
        start = datetime.fromisoformat(window["start"])
        run_dir = args.reconciliation_root / _run_dir_name(args, start)
        summary = _load_json(run_dir / "summary.json")
        if summary is None:
            raise FileNotFoundError(f"Missing reconciliation summary: {run_dir}")
        aggregate = summary["aggregate"]
        queue = aggregate.get("queue", {})
        buy_fills, sell_fills = _fill_side_counts(run_dir)
        tail = tail_by_window.get(start.strftime("%Y-%m-%d %H:00"), {})
        micro = microprice_by_window.get(start.strftime("%Y-%m-%d %H:00"), {})
        ofi = ofi_by_window.get(start.strftime("%Y-%m-%d %H:00"), {})
        rows.append({
            "window": start.strftime("%Y-%m-%d %H:00"),
            "utc_bucket": window.get("utc_bucket"),
            "vol_tercile": window.get("vol_tercile"),
            "mid_drift_bps": window.get("mid_drift_bps"),
            "realized_vol_bps": window.get("realized_vol_bps"),
            "net_pnl": aggregate.get("net_pnl"),
            "matched_net_pnl": aggregate.get("matched_net_pnl"),
            "residual_inventory_pnl": aggregate.get("residual_inventory_pnl"),
            "fills": aggregate.get("fills"),
            "orders_per_fill": aggregate.get("orders_per_fill"),
            "postonly_rejects": aggregate.get("postonly_rejects"),
            "buy_fills": buy_fills,
            "sell_fills": sell_fills,
            "fill_imbalance_buy_minus_sell": buy_fills - sell_fills,
            "avg_abs_residual_inventory": aggregate.get("residual_inventory_abs_avg"),
            "p50_hold_time_ms": aggregate.get("p50_hold_time_ms"),
            "queue_initial_ahead_filled": queue.get("avg_initial_queue_ahead_filled"),
            "queue_before_fill": queue.get("avg_queue_before_fill_at_first_fill"),
            "queue_trade_drain": queue.get("total_trade_queue_drained"),
            "queue_cancel_drain": queue.get("total_cancel_queue_drained"),
            "tail_loss_source_label": tail.get("loss_source_label"),
            "fill_worst_5_share": tail.get("fill_tail_5pct_share_of_total_metric_sum"),
            "matched_worst_5_share": tail.get("matched_lot_tail_5pct_share_of_total_metric_sum"),
            "microprice_1s_beta": micro.get("beta"),
            "microprice_1s_t_stat": micro.get("t_stat"),
            "microprice_1s_beta_x_std": micro.get("predicted_drift_1std_bps"),
            "ofi_1s_beta": ofi.get("beta"),
            "ofi_1s_t_stat": ofi.get("t_stat"),
            "ofi_1s_beta_x_std": ofi.get("predicted_drift_1std_bps"),
        })
    return rows


def _write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})


def parse_args():
    parser = argparse.ArgumentParser(description="Build descriptive MM regime table")
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--strategy", default="microprice")
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--session-hours", type=int, default=1)
    parser.add_argument("--half-spread", default="2.00")
    parser.add_argument("--requote-interval-ms", type=int, default=5000)
    parser.add_argument("--queue-cancellation-credit", default="1.0")
    parser.add_argument("--windows-csv", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2/windows.csv"))
    parser.add_argument("--reconciliation-root", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2/markout_reconciliation"))
    parser.add_argument("--tail-window-summary", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2/tail_diagnostics/"
                                     "btcusdt_microprice_hs2.00_rq5000_panel24/"
                                     "window_summary.csv"))
    parser.add_argument("--microprice-regressions", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2/microprice_signal"))
    parser.add_argument("--ofi-regressions", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2/ofi_signal"))
    parser.add_argument("--output-root", type=Path,
                        default=Path("results/panels/btcusdt_l2_panel_v2/regime"))
    return parser.parse_args()


def main():
    args = parse_args()
    guard_frozen_v2_output_path(args.output_root)
    rows = _build_rows(args)
    args.output_root.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output_root / "regime_table.csv", rows)
    summary = {
        "counts": {"windows": len(rows)},
        "warning": (
            "The regime table is descriptive and for writeup context only. "
            "Do not select strategy filters from this 24-row table. With many "
            "columns, spurious correlations are expected by chance; any apparent "
            "pattern requires holdout windows."
        ),
    }
    with (args.output_root / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"Wrote regime table to {args.output_root / 'regime_table.csv'}")


if __name__ == "__main__":
    main()
