"""
Fee break-even diagnostics for passive market-making runs.

The calculations are deliberately accounting-first:
  - current net PnL is the artifact's reported net after fees
  - gross before fees adds current fee cost back
  - break-even maker fee is the fee rate that would make gross minus fees zero
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from src.execution.queue_credit import credit_from_legacy_mode, parse_queue_credit
from src.analysis.tail_diagnostics import tail_count


TAIL_FRACTION = Decimal("0.05")


@dataclass(frozen=True)
class ReconciliationRun:
    run_dir: Path
    summary: dict
    matched_lots: list[dict]
    open_lots: list[dict]

    @property
    def queue_credit(self) -> Decimal:
        params = self.summary["params"]
        if "queue_cancellation_credit" in params:
            return parse_queue_credit(params["queue_cancellation_credit"])
        return credit_from_legacy_mode(
            params.get("queue_cancellation_mode", "proportional")
        )

    @property
    def window(self) -> str:
        return self.summary["params"]["start"]


def load_reconciliation_run(run_dir: Path) -> ReconciliationRun:
    with (run_dir / "summary.json").open("r", encoding="utf-8") as f:
        summary = json.load(f)

    return ReconciliationRun(
        run_dir=run_dir,
        summary=summary,
        matched_lots=_load_csv(run_dir / "matched_lots.csv"),
        open_lots=_load_csv(run_dir / "open_lots.csv"),
    )


def build_fee_break_even_rows(
    runs: Sequence[ReconciliationRun],
    *,
    expected_maker_bps: int = 2,
) -> list[dict]:
    rows: list[dict] = []
    for run in runs:
        _validate_run(run, expected_maker_bps=expected_maker_bps)
        rows.extend(_rows_for_run(run))

    for queue_credit in sorted({run.queue_credit for run in runs}):
        queue_runs = [run for run in runs if run.queue_credit == queue_credit]
        rows.extend(_rows_for_pooled_runs(queue_credit, queue_runs))

    return rows


def _rows_for_run(run: ReconciliationRun) -> list[dict]:
    aggregate = run.summary["aggregate"]
    maker_bps = int(run.summary["params"]["maker_bps"])
    window = run.window
    queue_credit = run.queue_credit

    rows = [
        _build_row(
            queue_credit=queue_credit,
            window=window,
            run_dir=run.run_dir.name,
            row_type="full_strategy",
            endpoint_sensitive=True,
            maker_bps=maker_bps,
            net_pnl=_d(aggregate["net_pnl"]),
            gross_before_fees=_d(aggregate["net_pnl"]) + _d(aggregate["fees"]),
            current_fees=_d(aggregate["fees"]),
            lot_count=None,
            total_quantity=_full_strategy_quantity(run),
        )
    ]

    rows.extend(_matched_rows(
        queue_credit=queue_credit,
        window=window,
        run_dir=run.run_dir.name,
        maker_bps=maker_bps,
        matched_lots=run.matched_lots,
    ))
    return rows


def _rows_for_pooled_runs(queue_credit: Decimal, runs: Sequence[ReconciliationRun]) -> list[dict]:
    if not runs:
        return []

    maker_bps_values = {int(run.summary["params"]["maker_bps"]) for run in runs}
    if len(maker_bps_values) != 1:
        raise ValueError(f"Mixed maker_bps for pooled credit {queue_credit}: {maker_bps_values}")
    maker_bps = maker_bps_values.pop()

    net_pnl = sum(
        (_d(run.summary["aggregate"]["net_pnl"]) for run in runs),
        Decimal("0"),
    )
    current_fees = sum(
        (_d(run.summary["aggregate"]["fees"]) for run in runs),
        Decimal("0"),
    )

    rows = [
        _build_row(
            queue_credit=queue_credit,
            window="pooled",
            run_dir="pooled",
            row_type="full_strategy",
            endpoint_sensitive=True,
            maker_bps=maker_bps,
            net_pnl=net_pnl,
            gross_before_fees=net_pnl + current_fees,
            current_fees=current_fees,
            lot_count=None,
            total_quantity=sum((_full_strategy_quantity(run) for run in runs), Decimal("0")),
        )
    ]

    matched_lots = [
        lot for run in runs for lot in run.matched_lots
    ]
    rows.extend(_matched_rows(
        queue_credit=queue_credit,
        window="pooled",
        run_dir="pooled",
        maker_bps=maker_bps,
        matched_lots=matched_lots,
    ))
    return rows


def _matched_rows(
    *,
    queue_credit: Decimal,
    window: str,
    run_dir: str,
    maker_bps: int,
    matched_lots: Sequence[dict],
) -> list[dict]:
    by_metric = sorted(
        matched_lots,
        key=lambda row: (
            _net_pnl_per_btc(row),
            row.get("close_time_ms", ""),
            row.get("open_fill_id", ""),
            row.get("close_fill_id", ""),
        ),
    )
    n_tail = tail_count(len(by_metric), TAIL_FRACTION)

    selections = (
        ("full_matched_lots", list(by_metric)),
        ("matched_tail_excluded_worst_5pct", list(by_metric[n_tail:])),
        ("matched_body_only_ex_worst_best_5pct", list(by_metric[n_tail:len(by_metric) - n_tail])),
    )

    return [
        _build_matched_row(
            queue_credit=queue_credit,
            window=window,
            run_dir=run_dir,
            row_type=row_type,
            maker_bps=maker_bps,
            matched_lots=selection,
        )
        for row_type, selection in selections
    ]


def _build_matched_row(
    *,
    queue_credit: Decimal,
    window: str,
    run_dir: str,
    row_type: str,
    maker_bps: int,
    matched_lots: Sequence[dict],
) -> dict:
    net_pnl = sum((_d(row["net_pnl"]) for row in matched_lots), Decimal("0"))
    gross_before_fees = sum(
        (_d(row["realized_pnl"]) for row in matched_lots),
        Decimal("0"),
    )
    current_fees = sum((_d(row["total_fees"]) for row in matched_lots), Decimal("0"))
    total_quantity = sum((_d(row["quantity"]) for row in matched_lots), Decimal("0"))
    return _build_row(
        queue_credit=queue_credit,
        window=window,
        run_dir=run_dir,
        row_type=row_type,
        endpoint_sensitive=False,
        maker_bps=maker_bps,
        net_pnl=net_pnl,
        gross_before_fees=gross_before_fees,
        current_fees=current_fees,
        lot_count=len(matched_lots),
        total_quantity=total_quantity,
    )


def _build_row(
    *,
    queue_credit: Decimal,
    window: str,
    run_dir: str,
    row_type: str,
    endpoint_sensitive: bool,
    maker_bps: int,
    net_pnl: Decimal,
    gross_before_fees: Decimal,
    current_fees: Decimal,
    lot_count: int | None,
    total_quantity: Decimal,
) -> dict:
    fee_notional = _fee_notional(current_fees, maker_bps)
    break_even = (
        gross_before_fees / fee_notional * Decimal("10000")
        if fee_notional and fee_notional > Decimal("0") else None
    )
    required_rebate = (
        max(Decimal("0"), -break_even)
        if break_even is not None else None
    )
    return {
        "queue_cancellation_credit": queue_credit,
        "window": window,
        "run_dir": run_dir,
        "row_type": row_type,
        "endpoint_sensitive": endpoint_sensitive,
        "current_maker_fee_bps": maker_bps,
        "net_pnl": net_pnl,
        "gross_before_fees": gross_before_fees,
        "current_fees": current_fees,
        "fee_notional": fee_notional,
        "break_even_maker_fee_bps": break_even,
        "required_rebate_bps": required_rebate,
        "lot_count": lot_count,
        "total_quantity": total_quantity,
        "quantity_weighted_net_per_btc": (
            net_pnl / total_quantity if total_quantity > Decimal("0") else None
        ),
    }


def _full_strategy_quantity(run: ReconciliationRun) -> Decimal:
    matched_qty = sum((_d(row["quantity"]) for row in run.matched_lots), Decimal("0"))
    open_qty = sum((_d(row["quantity"]) for row in run.open_lots), Decimal("0"))
    return matched_qty * Decimal("2") + open_qty


def _fee_notional(current_fees: Decimal, maker_bps: int) -> Decimal | None:
    if maker_bps == 0:
        return None
    return current_fees / (Decimal(maker_bps) / Decimal("10000"))


def _validate_run(run: ReconciliationRun, *, expected_maker_bps: int) -> None:
    params = run.summary["params"]
    maker_bps = int(params["maker_bps"])
    if maker_bps != expected_maker_bps:
        raise ValueError(
            f"{run.run_dir} has maker_bps={maker_bps}; expected {expected_maker_bps}"
        )
    if "queue_cancellation_credit" not in params and "queue_cancellation_mode" not in params:
        raise ValueError(f"{run.run_dir} is missing queue-cancellation metadata")


def _load_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _d(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _net_pnl_per_btc(row: dict) -> Decimal:
    quantity = _d(row["quantity"])
    if quantity == Decimal("0"):
        return Decimal("Infinity")
    return _d(row["net_pnl"]) / quantity
