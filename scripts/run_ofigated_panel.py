"""Run OFIGatedMM only after both queue endpoints pass the OFI support gate."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from scripts.run_l2_panel import (
    DEVELOPMENT_PANEL_SHA256,
    _file_sha256,
    _load_window_starts,
    _reconciliation_output_paths,
    _run_step,
    _verify_raw_inputs,
)
from src.analysis.ofi_signal import (
    DEFAULT_OFI_FILL_HORIZONS_MS,
    DEFAULT_OFI_HORIZONS_MS,
)
from src.execution.provenance import (
    guard_event_driven_output_path,
    require_event_driven_provenance,
)
from src.execution.queue_credit import parse_queue_credit, queue_credit_suffix
from src.execution.simulator import EQUAL_TIMESTAMP_POLICY, EXECUTION_MODEL_VERSION


ALLOWED_OFI_VERDICTS = {
    "supported",
    "supported_with_conditional_power_limit",
}
CANONICAL_QUEUE_CREDITS = {Decimal("0"), Decimal("1")}
CANONICAL_OFI_PROVENANCE = {
    "execution_model_version": EXECUTION_MODEL_VERSION,
    "equal_timestamp_policy": EQUAL_TIMESTAMP_POLICY,
    "trade_gap_policy": "pause_until_snapshot",
    "entry_latency_ms": 10,
    "entry_jitter_ms": 0,
    "cancel_latency_ms": 10,
    "cancel_jitter_ms": 0,
    "latency_seed": 42,
    "post_only": True,
}


def _normalized_starts(values, *, path: Path) -> list[datetime]:
    if not isinstance(values, list) or any(type(value) is not str for value in values):
        raise ValueError(f"OFI summary has invalid development starts: {path}")
    try:
        starts = [datetime.fromisoformat(value) for value in values]
    except ValueError as exc:
        raise ValueError(
            f"OFI summary has invalid development starts: {path}"
        ) from exc
    if len(starts) != len(set(starts)):
        raise ValueError(f"OFI summary has duplicate development starts: {path}")
    return starts


def _require_canonical_ofi_params(
    summary: Mapping[str, object],
    *,
    path: Path,
    expected_starts: list[datetime],
    expected_credit: Decimal,
) -> None:
    params = summary.get("params")
    if not isinstance(params, Mapping):
        raise ValueError(f"OFI summary is missing experiment params: {path}")
    try:
        exact_values = {
            "symbol": "btcusdt",
            "strategy": "microprice",
            "horizons_ms": DEFAULT_OFI_HORIZONS_MS,
            "fill_horizons_ms": DEFAULT_OFI_FILL_HORIZONS_MS,
        }
        if any(params.get(key) != value for key, value in exact_values.items()):
            raise ValueError
        exact_integers = {
            "hours": 5,
            "session_hours": 1,
            "requote_interval_ms": 5000,
            "latency_ms": 10,
            "jitter_ms": 0,
            "maker_bps": 2,
            "taker_bps": 5,
            "sample_interval_ms": 1000,
            "ofi_interval_ms": 1000,
        }
        if any(
            type(params.get(key)) is not int or params[key] != value
            for key, value in exact_integers.items()
        ):
            raise ValueError
        if Decimal(str(params["half_spread"])) != Decimal("2.00"):
            raise ValueError
        if Decimal(str(params["order_qty"])) != Decimal("0.001"):
            raise ValueError
        if Decimal(str(params["max_position"])) != Decimal("0.01"):
            raise ValueError
        if parse_queue_credit(params["queue_cancellation_credit"]) != expected_credit:
            raise ValueError
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError(
            f"OFI summary does not use the canonical development experiment: {path}"
        ) from exc

    if _normalized_starts(params.get("starts"), path=path) != expected_starts:
        raise ValueError(f"OFI summary run set does not match development panel: {path}")

    counts = summary.get("counts")
    if (
        not isinstance(counts, Mapping)
        or type(counts.get("windows")) is not int
        or counts["windows"] != len(expected_starts)
    ):
        raise ValueError(f"OFI summary window count does not match panel: {path}")


def _finite_decimal(value, *, field: str, path: Path) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise ValueError(f"OFI summary has invalid {field}: {path}") from exc
    if not parsed.is_finite():
        raise ValueError(f"OFI summary has invalid {field}: {path}")
    return parsed


def _validated_gate_verdict(
    gates: Mapping[str, object],
    *,
    path: Path,
    expected_windows: int,
) -> str:
    """Recompute the primary gate decision from its persisted components."""
    total = gates.get("total_windows")
    stable = gates.get("stable_sign_windows")
    bucket_count = gates.get("conditional_bucket_count_min")
    if (
        type(total) is not int
        or total != expected_windows
        or type(stable) is not int
        or stable < 0
        or stable > total
        or type(bucket_count) is not int
        or bucket_count < 0
    ):
        raise ValueError(f"OFI summary has invalid gate counts: {path}")

    stable_share = _finite_decimal(
        gates.get("stable_sign_share"),
        field="stable_sign_share",
        path=path,
    )
    expected_share = Decimal(stable) / Decimal(total) if total else Decimal("0")
    if stable_share != expected_share:
        raise ValueError(f"OFI summary has inconsistent stable-sign share: {path}")
    pooled_t = _finite_decimal(
        gates.get("pooled_abs_t_stat"),
        field="pooled_abs_t_stat",
        path=path,
    )
    pooled_effect = _finite_decimal(
        gates.get("pooled_abs_predicted_drift_1std_bps"),
        field="pooled_abs_predicted_drift_1std_bps",
        path=path,
    )
    separation_value = gates.get("conditional_separation_bps")
    separation = (
        None
        if separation_value is None
        else _finite_decimal(
            separation_value,
            field="conditional_separation_bps",
            path=path,
        )
    )

    stable_pass = total > 0 and stable_share >= Decimal("0.75")
    t_pass = pooled_t >= Decimal("2.0")
    effect_pass = pooled_effect >= Decimal("0.05")
    unconditional_pass = stable_pass and t_pass and effect_pass
    if separation is None or bucket_count < 30:
        conditional_status = "inconclusive_power"
    elif separation >= Decimal("1.0"):
        conditional_status = "pass"
    else:
        conditional_status = "fail_signal"
    conditional_pass = conditional_status == "pass"
    if not unconditional_pass or conditional_status == "fail_signal":
        verdict = "blocked"
    elif conditional_pass:
        verdict = "supported"
    else:
        verdict = "supported_with_conditional_power_limit"

    expected_fields = {
        "stable_sign_pass": stable_pass,
        "pooled_t_stat_pass": t_pass,
        "pooled_effect_pass": effect_pass,
        "conditional_status": conditional_status,
        "conditional_pass": conditional_pass,
        "unconditional_pass": unconditional_pass,
        "overall_verdict": verdict,
        "all_pass": verdict != "blocked",
    }
    for field, expected in expected_fields.items():
        actual = gates.get(field)
        if isinstance(expected, bool):
            matches = actual is expected
        else:
            matches = type(actual) is str and actual == expected
        if not matches:
            raise ValueError(f"OFI summary has inconsistent gate result: {path}")
    return verdict


def _require_canonical_candidate_args(args) -> None:
    exact_values = {
        "symbol": "btcusdt",
        "hours": 5,
        "session_hours": 1,
        "requote_interval_ms": 5000,
        "latency_ms": 10,
        "jitter_ms": 0,
        "cancel_latency_ms": 10,
        "cancel_jitter_ms": 0,
        "maker_bps": 2,
        "taker_bps": 5,
        "ofi_interval_ms": 1000,
    }
    if any(getattr(args, key) != value for key, value in exact_values.items()):
        raise ValueError("OFIGatedMM requires the canonical development parameters")
    try:
        decimals_match = (
            Decimal(args.half_spread) == Decimal("2.00")
            and Decimal(args.order_qty) == Decimal("0.001")
            and Decimal(args.max_position) == Decimal("0.01")
            and Decimal(args.ofi_threshold) == Decimal("0.25")
        )
    except ArithmeticError as exc:
        raise ValueError(
            "OFIGatedMM requires the canonical development parameters"
        ) from exc
    if not decimals_match:
        raise ValueError("OFIGatedMM requires the canonical development parameters")


def _validate_ofi_support(
    paths: list[Path],
    expected_credits: list[str],
    expected_starts: list[datetime],
) -> dict[str, str]:
    expected = {parse_queue_credit(value) for value in expected_credits}
    if expected != CANONICAL_QUEUE_CREDITS:
        raise ValueError("OFI support requires exactly queue credits 0 and 1")
    if len(paths) != len(expected):
        raise ValueError("OFI summaries must cover exactly the canonical queue endpoints")

    verdicts: dict[str, str] = {}
    resolved_paths: set[Path] = set()
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"OFI summary must be a direct regular file: {path}")
        resolved = path.resolve()
        if resolved in resolved_paths:
            raise ValueError(f"duplicate OFI summary input: {path}")
        resolved_paths.add(resolved)
        with path.open("r", encoding="utf-8") as f:
            summary = json.load(f)
        provenance = require_event_driven_provenance(summary)
        try:
            params = summary["params"]
            if not isinstance(params, Mapping):
                raise TypeError
            credit_value = parse_queue_credit(params["queue_cancellation_credit"])
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise ValueError(f"OFI summary has invalid queue credit: {path}") from exc
        if credit_value not in expected:
            raise ValueError(f"OFI summary has an unexpected queue credit: {path}")
        if parse_queue_credit(provenance["queue_cancellation_credit"]) != credit_value:
            raise ValueError(f"OFI summary provenance credit mismatch: {path}")
        if any(
            provenance.get(key) != value
            for key, value in CANONICAL_OFI_PROVENANCE.items()
        ):
            raise ValueError(
                f"OFI summary provenance is not the canonical experiment: {path}"
            )
        _require_canonical_ofi_params(
            summary,
            path=path,
            expected_starts=expected_starts,
            expected_credit=credit_value,
        )
        gates = summary.get("gates")
        if not isinstance(gates, Mapping):
            raise ValueError(f"OFI summary has invalid gate metadata: {path}")
        verdict = _validated_gate_verdict(
            gates,
            path=path,
            expected_windows=len(expected_starts),
        )
        credit = format(credit_value.normalize(), "f")
        if credit in verdicts:
            raise ValueError(f"duplicate OFI summary queue endpoint: {credit}")
        verdicts[credit] = verdict
    expected_labels = {format(value.normalize(), "f") for value in expected}
    if verdicts.keys() != expected_labels:
        raise ValueError("OFI summaries must cover exactly the requested queue endpoints")
    blocked = {
        credit: verdict for credit, verdict in verdicts.items()
        if verdict not in ALLOWED_OFI_VERDICTS
    }
    if blocked:
        raise ValueError(f"OFIGatedMM is blocked by OFI verdicts: {blocked}")
    return verdicts


def _summary_path(args, start: datetime, credit: str) -> Path:
    run_id = (
        f"{args.symbol.lower()}_ofi_gated_{start.strftime('%Y%m%d_%H')}_"
        f"{args.hours}h_{args.hours // args.session_hours}sessions_"
        f"hs{args.half_spread}_rq{args.requote_interval_ms}"
    )
    return args.output_root / "markout_reconciliation" / (
        f"{run_id}{queue_credit_suffix(credit)}"
    ) / "summary.json"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ofi-summary", nargs="+", type=Path, required=True)
    parser.add_argument(
        "--windows-csv",
        type=Path,
        default=Path(
            "results/panels/btcusdt_l2_panel_v2/development_windows.csv"
        ),
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--status-dir", type=Path, required=True)
    parser.add_argument("--queue-credits", nargs="+", default=["0.0", "1.0"])
    parser.add_argument("--symbol", choices=["btcusdt"], default="btcusdt")
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--session-hours", type=int, default=1)
    parser.add_argument("--half-spread", default="2.00")
    parser.add_argument("--order-qty", default="0.001")
    parser.add_argument("--max-position", default="0.01")
    parser.add_argument("--requote-interval-ms", type=int, default=5000)
    parser.add_argument("--latency-ms", type=int, default=10)
    parser.add_argument("--jitter-ms", type=int, default=0)
    parser.add_argument("--cancel-latency-ms", type=int)
    parser.add_argument("--cancel-jitter-ms", type=int)
    parser.add_argument("--maker-bps", type=int, default=2)
    parser.add_argument("--taker-bps", type=int, default=5)
    parser.add_argument("--ofi-interval-ms", type=int, default=1000)
    parser.add_argument("--ofi-threshold", default="0.25")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument(
        "--integrity-manifest",
        type=Path,
        default=Path(
            "results/panels/btcusdt_l2_panel_v2/integrity_manifest.json"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.cancel_latency_ms is None:
        args.cancel_latency_ms = args.latency_ms
    if args.cancel_jitter_ms is None:
        args.cancel_jitter_ms = args.jitter_ms
    _require_canonical_candidate_args(args)
    guard_event_driven_output_path(args.output_root)
    guard_event_driven_output_path(args.status_dir)
    panel_sha256 = _file_sha256(args.windows_csv)
    if panel_sha256 != DEVELOPMENT_PANEL_SHA256:
        raise ValueError(
            "OFIGatedMM development runner requires the frozen development "
            "panel; sealed holdout execution needs a separate authorized workflow"
        )
    parsed_credits = []
    for value in args.queue_credits:
        credit = parse_queue_credit(value)
        if credit not in parsed_credits:
            parsed_credits.append(credit)
    args.queue_credits = [
        format(credit.normalize(), "f") for credit in parsed_credits
    ]
    if set(parsed_credits) != CANONICAL_QUEUE_CREDITS:
        raise ValueError("OFIGatedMM requires exactly queue credits 0 and 1")
    starts = _load_window_starts(args.windows_csv, args.hours)
    _verify_raw_inputs(args, starts)
    verdicts = _validate_ofi_support(
        args.ofi_summary,
        args.queue_credits,
        starts,
    )
    print(f"OFI support gate: {verdicts}")
    for credit in args.queue_credits:
        for start in starts:
            end = start + timedelta(hours=args.hours)
            command = [
                sys.executable, "scripts/analyze_markout_reconciliation.py",
                "--symbol", args.symbol,
                "--strategy", "ofi_gated",
                "--start", start.strftime("%Y-%m-%dT%H"),
                "--end", end.strftime("%Y-%m-%dT%H"),
                "--session-hours", str(args.session_hours),
                "--half-spread", args.half_spread,
                "--order-qty", args.order_qty,
                "--max-position", args.max_position,
                "--requote-interval-ms", str(args.requote_interval_ms),
                "--latency-ms", str(args.latency_ms),
                "--jitter-ms", str(args.jitter_ms),
                "--cancel-latency-ms", str(args.cancel_latency_ms),
                "--cancel-jitter-ms", str(args.cancel_jitter_ms),
                "--maker-bps", str(args.maker_bps),
                "--taker-bps", str(args.taker_bps),
                "--queue-cancellation-credit", credit,
                "--trade-gap-policy", "pause_until_snapshot",
                "--ofi-interval-ms", str(args.ofi_interval_ms),
                "--ofi-threshold", args.ofi_threshold,
                "--data-root", str(args.data_root),
                "--output-dir", str(args.output_root / "markout_reconciliation"),
            ]
            summary_path = _summary_path(args, start, credit)
            _run_step(
                args,
                f"ofi_gated_qc{Decimal(credit).normalize()}_{start.isoformat()}",
                command,
                _reconciliation_output_paths(summary_path),
                [args.windows_csv, args.integrity_manifest, *args.ofi_summary],
            )


if __name__ == "__main__":
    main()
