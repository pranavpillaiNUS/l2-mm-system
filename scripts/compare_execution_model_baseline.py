"""Compare an event-driven baseline rerun with the frozen legacy baseline.

This report is deliberately descriptive.  Changing execution semantics changes
the estimand, so this script does not reuse the V2/V1 advancement-verdict
ladder and does not treat differences between two marginal confidence
intervals as a paired confidence interval.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from src.execution.provenance import (
    LEGACY_EXECUTION_MODEL_VERSION,
    artifact_execution_model,
    guard_event_driven_output_path,
    require_event_driven_provenance,
)
from src.execution.queue_credit import (
    credit_from_legacy_mode,
    parse_queue_credit,
)
from src.execution.simulator import EXECUTION_MODEL_VERSION


REPORT_VERSION = "execution_model_baseline_comparison_v1"
_COMPARABLE_PARAM_FIELDS = (
    "symbol",
    "strategy",
    "half_spread",
    "requote_interval_ms",
    "latency_ms",
    "jitter_ms",
    "maker_bps",
    "taker_bps",
    "session_hours",
    "iterations",
    "seed",
)
_CANONICAL_CURRENT_HOURS = 5
_CANONICAL_CURRENT_ORDER_QTY = Decimal("0.001")
_CANONICAL_CURRENT_MAX_POSITION = Decimal("0.01")
FROZEN_DEVELOPMENT_PANEL_SHA256 = (
    "0dd76f47449802ba8a6723192bbc54cc36c009d8970c25f64ae3c8e38b188256"
)
FROZEN_LEGACY_ENDPOINT_SHA256 = {
    "0": "c2962790b8d8fdfe3bea24b61e179e9f2b30f27d366d5bf1c40d8a7a3de169f2",
    "1": "19107d5e4f6384cba5bc249f7b6981ef88392748deff540c11c7cd93a67776fa",
}


def _credit_label(value) -> str:
    return format(parse_queue_credit(value).normalize(), "f")


def _pairs(values: list[str]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not item:
            raise ValueError(f"expected CREDIT=PATH, got {value!r}")
        normalized = _credit_label(key)
        if normalized in out:
            raise ValueError(f"duplicate queue-credit endpoint {normalized}")
        out[normalized] = Path(item)
    return out


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"artifact must contain a JSON object: {path}")
    return payload


def _artifact_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _development_panel(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"development-window panel is empty: {path}")
    if any(row.get("hours") != str(_CANONICAL_CURRENT_HOURS) for row in rows):
        raise ValueError(
            f"development-window panel must use canonical five-hour rows: {path}"
        )
    try:
        starts = [datetime.fromisoformat(row["start"]) for row in rows]
        markers = [
            f"_{start.strftime('%Y%m%d_%H')}_"
            f"{_CANONICAL_CURRENT_HOURS}h_"
            for start, row in zip(starts, rows)
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid development-window panel: {path}") from exc
    if len(markers) != len(set(markers)):
        raise ValueError(f"development-window panel contains duplicates: {path}")
    return {
        "path": str(path),
        "sha256": _artifact_sha256(path),
        "row_count": len(rows),
        "starts": [start.isoformat() for start in starts],
        "run_markers": markers,
    }


def _require_panel_run_set(
    run_dirs: list[str],
    panel: Mapping[str, object],
    *,
    credit: str,
) -> None:
    markers = panel["run_markers"]
    if len(run_dirs) != panel["row_count"]:
        raise ValueError(
            f"current endpoint window count differs from development panel for "
            f"credit {credit}"
        )
    matched: set[str] = set()
    for marker in markers:
        candidates = [run_dir for run_dir in run_dirs if marker in run_dir]
        if len(candidates) != 1:
            raise ValueError(
                f"current endpoint run set does not match development panel for "
                f"credit {credit}"
            )
        matched.add(candidates[0])
    if len(matched) != len(run_dirs):
        raise ValueError(
            f"current endpoint run set does not match development panel for "
            f"credit {credit}"
        )


def _payload_credit(payload: Mapping[str, object]) -> Decimal:
    provenance = payload.get("execution_provenance")
    if isinstance(provenance, Mapping) and "queue_cancellation_credit" in provenance:
        return parse_queue_credit(provenance["queue_cancellation_credit"])
    params = payload.get("params")
    if not isinstance(params, Mapping):
        raise ValueError("baseline artifact is missing params")
    if "queue_cancellation_credit" in params:
        return parse_queue_credit(params["queue_cancellation_credit"])
    return credit_from_legacy_mode(
        str(params.get("queue_cancellation_mode", "proportional"))
    )


def _finite_decimal(value, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return parsed


def _ci_summary(payload: Mapping[str, object], *, path: Path) -> dict[str, object]:
    try:
        ci = payload["ci"]["window"]["net_pnl"]  # type: ignore[index]
        n = ci["n"]
        if type(n) is not int:
            raise ValueError("CI sample size must be an integer")
        mean = _finite_decimal(ci["mean"], field="mean")
        low = _finite_decimal(ci["ci_low"], field="ci_low")
        high = _finite_decimal(ci["ci_high"], field="ci_high")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid window net-PnL CI in {path}") from exc
    if n <= 0 or low > high or not low <= mean <= high:
        raise ValueError(f"invalid window net-PnL CI bounds in {path}")

    counts = payload.get("counts")
    if (
        not isinstance(counts, Mapping)
        or type(counts.get("windows")) is not int
        or counts["windows"] != n
    ):
        raise ValueError(f"window count does not match CI sample size in {path}")
    run_dirs = payload.get("run_dirs")
    if (
        not isinstance(run_dirs, list)
        or any(type(item) is not str for item in run_dirs)
        or len(run_dirs) != n
    ):
        raise ValueError(f"run_dirs does not match CI sample size in {path}")

    if low > 0:
        relation = "entirely_positive"
    elif high < 0:
        relation = "entirely_negative"
    else:
        relation = "crosses_zero"
    return {
        "n": n,
        "mean": mean,
        "ci_low": low,
        "ci_high": high,
        "relation_to_zero": relation,
        "run_dirs": list(run_dirs),
    }


def _params(payload: Mapping[str, object], *, path: Path) -> Mapping[str, object]:
    params = payload.get("params")
    if not isinstance(params, Mapping):
        raise ValueError(f"baseline artifact is missing params: {path}")
    missing = [field for field in _COMPARABLE_PARAM_FIELDS if field not in params]
    if missing:
        raise ValueError(
            f"baseline artifact has incomplete comparable params in {path}: "
            + ", ".join(missing)
        )
    return params


def _require_canonical_current_experiment(
    params: Mapping[str, object],
    *,
    path: Path,
) -> None:
    """Bind the current CI to the one pre-registered development experiment."""
    required = ("hours", "order_qty", "max_position")
    missing = [field for field in required if field not in params]
    if missing:
        raise ValueError(
            f"current CI has incomplete canonical experiment params in {path}: "
            + ", ".join(missing)
        )
    if type(params["hours"]) is not int or params["hours"] != _CANONICAL_CURRENT_HOURS:
        raise ValueError(f"current CI does not use canonical 5-hour windows: {path}")
    order_qty = _finite_decimal(params["order_qty"], field="order_qty")
    max_position = _finite_decimal(params["max_position"], field="max_position")
    if order_qty != _CANONICAL_CURRENT_ORDER_QTY:
        raise ValueError(f"current CI does not use canonical order_qty=0.001: {path}")
    if max_position != _CANONICAL_CURRENT_MAX_POSITION:
        raise ValueError(f"current CI does not use canonical max_position=0.01: {path}")


def _require_safe_run_dir_components(
    values: list[object],
    *,
    field: str,
    path: Path,
) -> None:
    """Reject absolute, nested, or traversal-like allowlist entries."""
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"current CI {field} must contain strings: {path}")
        candidate = Path(value)
        if (
            not value
            or value in {".", ".."}
            or candidate.is_absolute()
            or len(candidate.parts) != 1
            or candidate.name != value
            or "/" in value
            or "\\" in value
        ):
            raise ValueError(
                f"current CI {field} contains an unsafe run-directory "
                f"component in {path}: {value!r}"
            )


def _require_exact_current_inputs(
    payload: Mapping[str, object],
    run_dirs: list[str],
    *,
    path: Path,
    panel: Mapping[str, object],
) -> None:
    selection = payload.get("input_selection")
    if not isinstance(selection, Mapping):
        raise ValueError(f"current CI is missing exact input selection: {path}")
    if selection.get("mode") != "expected_run_allowlist":
        raise ValueError(f"current CI was not built from an exact run allowlist: {path}")
    expected_run_dirs = selection.get("expected_run_dirs")
    expected_starts = selection.get("expected_starts")
    _require_safe_run_dir_components(
        list(run_dirs),
        field="run_dirs",
        path=path,
    )
    if isinstance(expected_run_dirs, list):
        _require_safe_run_dir_components(
            expected_run_dirs,
            field="expected_run_dirs",
            path=path,
        )
    try:
        selected_starts = sorted(
            datetime.fromisoformat(str(item)) for item in expected_starts
        ) if isinstance(expected_starts, list) else []
        panel_starts = sorted(
            datetime.fromisoformat(str(item)) for item in panel["starts"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"current CI has invalid expected starts: {path}") from exc
    if (
        not isinstance(expected_run_dirs, list)
        or sorted(str(item) for item in expected_run_dirs) != sorted(run_dirs)
        or not isinstance(expected_starts, list)
        or len(expected_starts) != len(run_dirs)
        or selected_starts != panel_starts
    ):
        raise ValueError(f"current CI input allowlist does not match run_dirs: {path}")


def _require_current_params_match_provenance(
    params: Mapping[str, object],
    provenance: Mapping[str, object],
    *,
    path: Path,
) -> None:
    pairs = {
        "execution_model_version": "execution_model_version",
        "equal_timestamp_policy": "equal_timestamp_policy",
        "snapshot_time_policy": "snapshot_time_policy",
        "trade_gap_policy": "trade_gap_policy",
        "latency_ms": "entry_latency_ms",
        "jitter_ms": "entry_jitter_ms",
        "cancel_latency_ms": "cancel_latency_ms",
        "cancel_jitter_ms": "cancel_jitter_ms",
        "latency_seed": "latency_seed",
    }
    missing = [field for field in pairs if field not in params]
    if missing:
        raise ValueError(
            f"current CI has incomplete provenance-bearing params in {path}: "
            + ", ".join(missing)
        )
    if any(params[param] != provenance[field] for param, field in pairs.items()):
        raise ValueError(f"current CI params contradict execution provenance: {path}")
    try:
        param_credit = parse_queue_credit(params["queue_cancellation_credit"])
        provenance_credit = parse_queue_credit(
            provenance["queue_cancellation_credit"]
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError(
            f"current CI has invalid queue-credit provenance: {path}"
        ) from exc
    if param_credit != provenance_credit:
        raise ValueError(f"current CI params contradict execution provenance: {path}")


def _json_ci(ci: Mapping[str, object]) -> dict[str, object]:
    return {
        "n": ci["n"],
        "mean": format(ci["mean"], "f"),
        "ci_low": format(ci["ci_low"], "f"),
        "ci_high": format(ci["ci_high"], "f"),
        "relation_to_zero": ci["relation_to_zero"],
    }


def build_report(
    current_paths: Mapping[str, Path],
    legacy_paths: Mapping[str, Path],
    development_windows_path: Path,
    *,
    expected_development_panel_sha256: str = FROZEN_DEVELOPMENT_PANEL_SHA256,
    expected_legacy_endpoint_sha256: Mapping[
        str, str
    ] = FROZEN_LEGACY_ENDPOINT_SHA256,
) -> dict[str, object]:
    """Build a provenance-checked descriptive execution-model comparison."""
    if not current_paths or set(current_paths) != set(legacy_paths):
        raise ValueError("current and legacy endpoint credits must match and be non-empty")
    panel_sha256 = _artifact_sha256(development_windows_path)
    if panel_sha256 != expected_development_panel_sha256:
        raise ValueError("development panel does not match the frozen reference hash")
    if set(legacy_paths) != set(expected_legacy_endpoint_sha256):
        raise ValueError("legacy endpoints do not match the frozen reference set")
    for credit, expected_sha256 in expected_legacy_endpoint_sha256.items():
        if _artifact_sha256(legacy_paths[credit]) != expected_sha256:
            raise ValueError(
                f"legacy endpoint {credit} does not match its frozen reference hash"
            )

    panel = _development_panel(development_windows_path)
    endpoints: dict[str, object] = {}
    common_current_provenance: dict[str, object] | None = None
    for credit in sorted(current_paths, key=Decimal):
        current_path = current_paths[credit]
        legacy_path = legacy_paths[credit]
        current = _load_json(current_path)
        legacy = _load_json(legacy_path)

        provenance = require_event_driven_provenance(current)
        if artifact_execution_model(legacy) != LEGACY_EXECUTION_MODEL_VERSION:
            raise ValueError(
                f"legacy reference must use {LEGACY_EXECUTION_MODEL_VERSION}: "
                f"{legacy_path}"
            )
        expected_credit = parse_queue_credit(credit)
        if _payload_credit(current) != expected_credit:
            raise ValueError(f"current artifact queue credit does not match {credit}")
        if _payload_credit(legacy) != expected_credit:
            raise ValueError(f"legacy artifact queue credit does not match {credit}")

        current_params = _params(current, path=current_path)
        legacy_params = _params(legacy, path=legacy_path)
        _require_canonical_current_experiment(current_params, path=current_path)
        mismatched_params = [
            field for field in _COMPARABLE_PARAM_FIELDS
            if current_params[field] != legacy_params[field]
        ]
        if mismatched_params:
            raise ValueError(
                f"current and legacy experiment params differ for credit {credit}: "
                + ", ".join(mismatched_params)
            )

        current_ci = _ci_summary(current, path=current_path)
        legacy_ci = _ci_summary(legacy, path=legacy_path)
        _require_exact_current_inputs(
            current,
            current_ci["run_dirs"],
            path=current_path,
            panel=panel,
        )
        _require_current_params_match_provenance(
            current_params,
            provenance,
            path=current_path,
        )
        if sorted(current_ci["run_dirs"]) != sorted(legacy_ci["run_dirs"]):
            raise ValueError(
                f"current and legacy development windows differ for credit {credit}"
            )
        _require_panel_run_set(
            current_ci["run_dirs"],
            panel,
            credit=credit,
        )

        shared_provenance = {
            key: value for key, value in provenance.items()
            if key != "queue_cancellation_credit"
        }
        if common_current_provenance is None:
            common_current_provenance = shared_provenance
        elif shared_provenance != common_current_provenance:
            raise ValueError("current endpoints use different execution provenance")

        endpoints[credit] = {
            "current": {
                "path": str(current_path),
                "sha256": _artifact_sha256(current_path),
                "execution_provenance": provenance,
                "window_net_pnl_ci": _json_ci(current_ci),
            },
            "legacy_reference": {
                "path": str(legacy_path),
                "sha256": _artifact_sha256(legacy_path),
                "execution_model_version": LEGACY_EXECUTION_MODEL_VERSION,
                "window_net_pnl_ci": _json_ci(legacy_ci),
            },
            "descriptive_mean_delta_current_minus_legacy": format(
                current_ci["mean"] - legacy_ci["mean"], "f"
            ),
        }

    return {
        "report_version": REPORT_VERSION,
        "headline": (
            "Event-driven development rerun compared with frozen legacy baseline"
        ),
        "comparison_scope": "same frozen development-window set",
        "development_panel": {
            key: value for key, value in panel.items() if key != "run_markers"
        },
        "current_execution_model_version": EXECUTION_MODEL_VERSION,
        "reference_execution_model_version": LEGACY_EXECUTION_MODEL_VERSION,
        "automatic_strategy_verdict": None,
        "interpretation": (
            "Descriptive execution-model sensitivity only. Mean deltas are not "
            "paired confidence intervals and this report does not advance, "
            "reject, strengthen, or overturn a strategy claim."
        ),
        "endpoints": endpoints,
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-endpoint-ci", nargs="+", required=True,
                        help="CREDIT=event-driven baseline_ci.json")
    parser.add_argument("--legacy-endpoint-ci", nargs="+", required=True,
                        help="CREDIT=frozen legacy baseline_ci.json")
    parser.add_argument("--development-windows-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    guard_event_driven_output_path(args.output)
    report = build_report(
        _pairs(args.current_endpoint_ci),
        _pairs(args.legacy_endpoint_ci),
        args.development_windows_csv,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(report["headline"])
    print(report["interpretation"])
    print(f"Wrote execution-model comparison to {args.output}")


if __name__ == "__main__":
    main()
