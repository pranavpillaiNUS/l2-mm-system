"""Summarize Phase C queue-credit and latency stress reconciliation runs."""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Mapping

from src.analysis.fee_break_even import load_reconciliation_run
from src.analysis.queue_credit_summary import (
    QueueCreditRun,
    build_queue_credit_summary_rows,
)
from src.execution.provenance import (
    guard_event_driven_output_path,
    require_event_driven_provenance,
)
from src.execution.queue_credit import parse_queue_credit
from src.execution.simulator import EQUAL_TIMESTAMP_POLICY, EXECUTION_MODEL_VERSION
from src.replay.depth_parser import SNAPSHOT_TIME_POLICY


_FIXED_PARAM_FIELDS = (
    "symbol",
    "strategy",
    "hours",
    "sessions",
    "session_hours",
    "order_qty",
    "max_position",
    "half_spread",
    "requote_interval_ms",
    "maker_bps",
    "taker_bps",
    "trade_gap_policy",
)
_VARIED_PARAM_FIELDS = (
    "start",
    "queue_cancellation_credit",
    "latency_ms",
    "cancel_latency_ms",
)
_FIXED_PROVENANCE_FIELDS = (
    "execution_model_version",
    "equal_timestamp_policy",
    "snapshot_time_policy",
    "trade_gap_policy",
    "entry_jitter_ms",
    "cancel_jitter_ms",
    "latency_seed",
    "post_only",
)
_VARIED_PROVENANCE_FIELDS = (
    "entry_latency_ms",
    "cancel_latency_ms",
    "queue_cancellation_credit",
)
_MANIFEST_PROVENANCE_FIELDS = (
    "execution_model_version",
    "equal_timestamp_policy",
    "snapshot_time_policy",
    "trade_gap_policy",
    "queue_cancellation_credit",
    "latency_ms",
    "entry_jitter_ms",
    "cancel_latency_ms",
    "cancel_jitter_ms",
    "latency_seed",
    "post_only",
)
_EXACT_INT_PARAM_FIELDS = (
    "hours",
    "sessions",
    "session_hours",
    "requote_interval_ms",
    "maker_bps",
    "taker_bps",
    "latency_ms",
    "jitter_ms",
    "cancel_latency_ms",
    "cancel_jitter_ms",
)
_DECIMAL_PARAM_FIELDS = (
    "order_qty",
    "max_position",
    "half_spread",
    "queue_cancellation_credit",
)
_NONNEGATIVE_INT = re.compile(r"0|[1-9][0-9]*")


@dataclass(frozen=True)
class SweepInputs:
    runs: list[QueueCreditRun]
    fixed_params: dict[str, object]
    fixed_provenance: dict[str, object]
    varied_params: dict[str, list[object]]
    varied_provenance: dict[str, list[object]]
    cancel_latency_by_endpoint: dict[tuple[Decimal, int], int]


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    return value


def _write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = list(rows[0]) if rows else []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _jsonable(value) for key, value in row.items()})


def _manifest_int(row: Mapping[str, str], field: str) -> int:
    value = row.get(field)
    if value is None or _NONNEGATIVE_INT.fullmatch(value) is None:
        raise ValueError(f"sweep manifest {field} must be a canonical non-negative integer")
    return int(value)


def _manifest_bool(row: Mapping[str, str], field: str) -> bool:
    value = row.get(field)
    if value == "True":
        return True
    if value == "False":
        return False
    raise ValueError(f"sweep manifest {field} must be True or False")


def _parse_start(value: object, *, label: str) -> datetime:
    if type(value) is not str:
        raise ValueError(f"{label} must be an ISO-8601 string")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid ISO-8601 datetime: {value!r}") from exc


def _finite_decimal(value: object, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return parsed


def _expected_provenance(manifest_row: Mapping[str, str]) -> dict[str, object]:
    missing = [
        field for field in _MANIFEST_PROVENANCE_FIELDS
        if field not in manifest_row or manifest_row[field] == ""
    ]
    if missing:
        raise ValueError(
            "sweep manifest has incomplete execution provenance: "
            + ", ".join(missing)
        )
    expected = {
        "execution_model_version": manifest_row["execution_model_version"],
        "equal_timestamp_policy": manifest_row["equal_timestamp_policy"],
        "snapshot_time_policy": manifest_row["snapshot_time_policy"],
        "trade_gap_policy": manifest_row["trade_gap_policy"],
        "entry_latency_ms": _manifest_int(manifest_row, "latency_ms"),
        "entry_jitter_ms": _manifest_int(manifest_row, "entry_jitter_ms"),
        "cancel_latency_ms": _manifest_int(manifest_row, "cancel_latency_ms"),
        "cancel_jitter_ms": _manifest_int(manifest_row, "cancel_jitter_ms"),
        "latency_seed": _manifest_int(manifest_row, "latency_seed"),
        "post_only": _manifest_bool(manifest_row, "post_only"),
        "queue_cancellation_credit": format(
            parse_queue_credit(manifest_row["queue_cancellation_credit"]).normalize(),
            "f",
        ),
    }
    if expected["execution_model_version"] != EXECUTION_MODEL_VERSION:
        raise ValueError("sweep manifest must select event_driven_v2 artifacts")
    if expected["equal_timestamp_policy"] != EQUAL_TIMESTAMP_POLICY:
        raise ValueError("sweep manifest uses an incompatible equal-timestamp policy")
    if expected["snapshot_time_policy"] != SNAPSHOT_TIME_POLICY:
        raise ValueError("sweep manifest uses an incompatible snapshot-time policy")
    if expected["trade_gap_policy"] not in {"ignore", "pause_until_snapshot"}:
        raise ValueError("sweep manifest uses an invalid trade-gap policy")
    if expected["post_only"] is not True:
        raise ValueError("queue-credit sweep artifacts must be post-only")
    if expected["entry_jitter_ms"] > expected["entry_latency_ms"]:
        raise ValueError("sweep manifest entry jitter exceeds entry latency")
    if expected["cancel_jitter_ms"] > expected["cancel_latency_ms"]:
        raise ValueError("sweep manifest cancel jitter exceeds cancel latency")
    return expected


def _canonical_params(summary: Mapping[str, object]) -> dict[str, object]:
    params = summary.get("params")
    if not isinstance(params, Mapping):
        raise ValueError("reconciliation summary is missing params")
    required = set(_FIXED_PARAM_FIELDS) | set(_VARIED_PARAM_FIELDS) | {
        "jitter_ms",
        "cancel_jitter_ms",
    }
    missing = sorted(field for field in required if field not in params)
    if missing:
        raise ValueError(
            "reconciliation summary has incomplete experiment params: "
            + ", ".join(missing)
        )
    for field in _EXACT_INT_PARAM_FIELDS:
        if type(params[field]) is not int:
            raise ValueError(f"reconciliation param {field} must be an exact integer")
    if params["hours"] <= 0 or params["session_hours"] <= 0:
        raise ValueError("reconciliation hours and session_hours must be positive")
    if params["hours"] % params["session_hours"]:
        raise ValueError("reconciliation hours must be divisible by session_hours")
    if params["sessions"] != params["hours"] // params["session_hours"]:
        raise ValueError("reconciliation session count is inconsistent")
    if params["requote_interval_ms"] < 0:
        raise ValueError("reconciliation requote interval must be non-negative")

    canonical: dict[str, object] = {
        field: params[field]
        for field in _FIXED_PARAM_FIELDS
        if field not in _DECIMAL_PARAM_FIELDS
    }
    canonical.update({
        field: _finite_decimal(params[field], field=field)
        for field in _DECIMAL_PARAM_FIELDS
    })
    if canonical["order_qty"] <= 0 or canonical["max_position"] <= 0:
        raise ValueError("reconciliation quantity and position limit must be positive")
    if canonical["half_spread"] <= 0:
        raise ValueError("reconciliation half spread must be positive")
    if type(canonical["symbol"]) is not str or not canonical["symbol"]:
        raise ValueError("reconciliation symbol must be a non-empty string")
    if type(canonical["strategy"]) is not str or not canonical["strategy"]:
        raise ValueError("reconciliation strategy must be a non-empty string")

    canonical.update({
        "start": _parse_start(params["start"], label="reconciliation start"),
        "latency_ms": params["latency_ms"],
        "jitter_ms": params["jitter_ms"],
        "cancel_latency_ms": params["cancel_latency_ms"],
        "cancel_jitter_ms": params["cancel_jitter_ms"],
    })
    return canonical


def _validate_selected_summary(
    summary: Mapping[str, object],
    manifest_row: Mapping[str, str],
) -> tuple[dict[str, object], dict[str, object]]:
    expected = _expected_provenance(manifest_row)
    provenance = require_event_driven_provenance(summary)
    normalized_provenance = dict(provenance)
    normalized_provenance["queue_cancellation_credit"] = format(
        parse_queue_credit(provenance["queue_cancellation_credit"]).normalize(),
        "f",
    )
    if normalized_provenance != expected:
        raise ValueError(
            "reconciliation execution provenance does not match sweep manifest"
        )

    params = _canonical_params(summary)
    expected_start = _parse_start(manifest_row.get("start"), label="manifest start")
    if params["start"] != expected_start:
        raise ValueError("reconciliation start does not match sweep manifest")
    if params["queue_cancellation_credit"] != parse_queue_credit(
        manifest_row["queue_cancellation_credit"]
    ):
        raise ValueError("reconciliation queue credit does not match sweep manifest")
    provenance_pairs = {
        "latency_ms": "entry_latency_ms",
        "jitter_ms": "entry_jitter_ms",
        "cancel_latency_ms": "cancel_latency_ms",
        "cancel_jitter_ms": "cancel_jitter_ms",
        "trade_gap_policy": "trade_gap_policy",
    }
    if any(
        params[param_field] != expected[provenance_field]
        for param_field, provenance_field in provenance_pairs.items()
    ):
        raise ValueError("reconciliation params contradict execution provenance")
    return params, expected


def _required_run_artifact(run_dir: Path, filename: str) -> Path:
    path = run_dir / filename
    if path.is_symlink():
        raise ValueError(f"sweep reconciliation artifact cannot be a symlink: {path}")
    if path.resolve(strict=False).parent != run_dir.resolve(strict=False):
        raise ValueError(f"sweep reconciliation artifact escapes its run directory: {path}")
    if not path.is_file():
        raise ValueError(f"sweep reconciliation artifact is missing: {path}")
    return path


def _discover_summaries(root: Path) -> list[tuple[Path, dict]]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"reconciliation root must be a non-symlinked directory: {root}")
    resolved_root = root.resolve(strict=True)
    summaries: list[tuple[Path, dict]] = []
    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue
        if run_dir.is_symlink() or run_dir.resolve(strict=True).parent != resolved_root:
            raise ValueError(
                f"reconciliation run must remain directly beneath its root: {run_dir}"
            )
        summary_path = run_dir / "summary.json"
        if not summary_path.exists() and not summary_path.is_symlink():
            continue
        _required_run_artifact(run_dir, "summary.json")
        with summary_path.open("r", encoding="utf-8") as f:
            summary = json.load(f)
        if not isinstance(summary, dict):
            raise ValueError(f"reconciliation summary must contain an object: {summary_path}")
        summaries.append((summary_path, summary))
    return summaries


def _sorted_unique(values: list[object]) -> list[object]:
    unique: dict[tuple[str, str], object] = {}
    for value in values:
        if isinstance(value, datetime):
            normalized: object = value.isoformat()
        else:
            normalized = value
        unique[(type(normalized).__name__, str(normalized))] = normalized
    return [unique[key] for key in sorted(unique, key=lambda item: item[1])]


def _load_sweep_inputs(path: Path) -> SweepInputs:
    with path.open("r", encoding="utf-8", newline="") as f:
        manifest_rows = list(csv.DictReader(f))
    if not manifest_rows:
        raise ValueError("queue-credit sweep manifest is empty")

    roots_by_row: list[Path] = []
    summaries_by_root: dict[Path, list[tuple[Path, dict]]] = {}
    for row in manifest_rows:
        raw_root = row.get("reconciliation_root")
        if not raw_root:
            raise ValueError("sweep manifest is missing reconciliation_root")
        root = Path(raw_root)
        resolved_root = root.resolve(strict=False)
        roots_by_row.append(resolved_root)
        if resolved_root not in summaries_by_root:
            summaries_by_root[resolved_root] = _discover_summaries(root)

    selected: list[
        tuple[Path, dict, dict[str, object], dict[str, object]]
    ] = []
    seen_expected: set[tuple[Decimal, int, int, datetime]] = set()
    seen_paths: set[Path] = set()
    for manifest_row, root in zip(manifest_rows, roots_by_row):
        expected_provenance = _expected_provenance(manifest_row)
        expected_start = _parse_start(
            manifest_row.get("start"), label="manifest start"
        )
        expected_key = (
            parse_queue_credit(manifest_row["queue_cancellation_credit"]),
            expected_provenance["entry_latency_ms"],
            expected_provenance["cancel_latency_ms"],
            expected_start,
        )
        if expected_key in seen_expected:
            raise ValueError(f"duplicate sweep manifest row: {expected_key}")
        seen_expected.add(expected_key)

        matches = []
        for summary_path, summary in summaries_by_root[root]:
            try:
                params, provenance = _validate_selected_summary(
                    summary, manifest_row
                )
            except (KeyError, TypeError, ValueError, ArithmeticError):
                continue
            matches.append((summary_path, summary, params, provenance))
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one reconciliation for {expected_key}, "
                f"found {len(matches)}"
            )
        selected_path = matches[0][0].resolve(strict=True)
        if selected_path in seen_paths:
            raise ValueError(f"reconciliation summary selected more than once: {selected_path}")
        seen_paths.add(selected_path)
        selected.append(matches[0])

    fixed_params: dict[str, object] | None = None
    fixed_provenance: dict[str, object] | None = None
    varied_param_values = {field: [] for field in _VARIED_PARAM_FIELDS}
    varied_provenance_values = {
        field: [] for field in _VARIED_PROVENANCE_FIELDS
    }
    cancel_by_endpoint: dict[tuple[Decimal, int], int] = {}
    runs: list[QueueCreditRun] = []
    for summary_path, _, params, provenance in selected:
        run_dir = summary_path.parent
        _required_run_artifact(run_dir, "matched_lots.csv")
        _required_run_artifact(run_dir, "open_lots.csv")
        current_fixed_params = {
            field: params[field] for field in _FIXED_PARAM_FIELDS
        }
        current_fixed_provenance = {
            field: provenance[field] for field in _FIXED_PROVENANCE_FIELDS
        }
        if fixed_params is None:
            fixed_params = current_fixed_params
            fixed_provenance = current_fixed_provenance
        elif current_fixed_params != fixed_params:
            differing = [
                field for field in _FIXED_PARAM_FIELDS
                if current_fixed_params[field] != fixed_params[field]
            ]
            raise ValueError(
                "queue-credit sweep mixes fixed experiment params: "
                + ", ".join(differing)
            )
        elif current_fixed_provenance != fixed_provenance:
            differing = [
                field for field in _FIXED_PROVENANCE_FIELDS
                if current_fixed_provenance[field] != fixed_provenance[field]
            ]
            raise ValueError(
                "queue-credit sweep mixes fixed execution provenance: "
                + ", ".join(differing)
            )

        for field in _VARIED_PARAM_FIELDS:
            varied_param_values[field].append(params[field])
        for field in _VARIED_PROVENANCE_FIELDS:
            varied_provenance_values[field].append(provenance[field])
        endpoint = (
            parse_queue_credit(provenance["queue_cancellation_credit"]),
            provenance["entry_latency_ms"],
        )
        prior_cancel_latency = cancel_by_endpoint.setdefault(
            endpoint, provenance["cancel_latency_ms"]
        )
        if prior_cancel_latency != provenance["cancel_latency_ms"]:
            raise ValueError(
                "one queue-credit/entry-latency endpoint mixes cancel latencies"
            )
        runs.append(QueueCreditRun(
            latency_ms=provenance["entry_latency_ms"],
            reconciliation=load_reconciliation_run(run_dir),
        ))

    if fixed_params is None or fixed_provenance is None:
        raise ValueError("queue-credit sweep selected no reconciliation runs")
    return SweepInputs(
        runs=runs,
        fixed_params=fixed_params,
        fixed_provenance=fixed_provenance,
        varied_params={
            field: _sorted_unique(values)
            for field, values in varied_param_values.items()
        },
        varied_provenance={
            field: _sorted_unique(values)
            for field, values in varied_provenance_values.items()
        },
        cancel_latency_by_endpoint=cancel_by_endpoint,
    )


def _load_runs(path: Path) -> list[QueueCreditRun]:
    """Compatibility wrapper used by focused provenance tests."""
    return _load_sweep_inputs(path).runs


def _build_output_payload(inputs: SweepInputs) -> dict[str, object]:
    rows = build_queue_credit_summary_rows(inputs.runs)
    for row in rows:
        endpoint = (
            parse_queue_credit(row["queue_cancellation_credit"]),
            row["latency_ms"],
        )
        row["cancel_latency_ms"] = inputs.cancel_latency_by_endpoint[endpoint]
    return {
        "execution_provenance": {
            **inputs.fixed_provenance,
            "fixed_fields": list(_FIXED_PROVENANCE_FIELDS),
            "varied_fields": list(_VARIED_PROVENANCE_FIELDS),
            "varied_values": inputs.varied_provenance,
        },
        "params": {
            **inputs.fixed_params,
            "fixed_fields": list(_FIXED_PARAM_FIELDS),
            "varied_fields": list(_VARIED_PARAM_FIELDS),
            "varied_values": inputs.varied_params,
        },
        "counts": {
            "reconciliation_runs": len(inputs.runs),
            "summary_rows": len(rows),
        },
        "rows": [
            {
                **inputs.fixed_provenance,
                "entry_latency_ms": row["latency_ms"],
                **row,
            }
            for row in rows
        ],
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-csv", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    guard_event_driven_output_path(args.output_root)
    payload = _build_output_payload(_load_sweep_inputs(args.runs_csv))
    rows = payload["rows"]
    args.output_root.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output_root / "queue_credit_summary.csv", rows)
    with (args.output_root / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=_jsonable)
    print(f"Wrote queue-credit summary to {args.output_root}")


if __name__ == "__main__":
    main()
