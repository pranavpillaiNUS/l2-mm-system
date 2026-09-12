"""Apply the prospective V3 development screen to a fully verified rerun.

The report is deliberately outside the runner's manifest tree. It never runs
candidate strategies, changes the primary horizon, or authorizes a holdout.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from dataclasses import asdict, fields
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from random import Random

from scripts.compare_execution_model_baseline import (
    _artifact_sha256,
    build_report as build_baseline_comparison,
)
from scripts.verify_v3_artifacts import verify
from src.analysis.bootstrap import percentile
from src.analysis.ofi_signal import (
    DEFAULT_ALIGNED_OFI_BUCKET_EDGES,
    DEFAULT_OFI_FILL_HORIZONS_MS,
    DEFAULT_OFI_HORIZONS_MS,
    OFIFillToxicityBucket,
    OFIRegression,
    _bucket_ranges,
    _in_range,
    evaluate_ofi_gates,
)
from src.execution.provenance import (
    guard_event_driven_output_path,
    require_event_driven_provenance,
)
from src.execution.queue_credit import parse_queue_credit


PROTOCOL_ID = "v3_development_protocol_v1"
DEFAULT_ROOT = Path("results/panels/btcusdt_l2_panel_v3_event_driven")
DEFAULT_OUTPUT = Path(
    "results/panels/btcusdt_l2_panel_v3_development_summary/summary.json"
)
DEFAULT_PROTOCOL = Path("notebooks/v3_development_protocol.md")
DEFAULT_LEGACY_ROOT = Path("results/panels/btcusdt_l2_panel_v2")
BOOTSTRAP_ITERATIONS = 10_000
BOOTSTRAP_SEED = 7
REPO_ROOT = Path(__file__).resolve().parents[1]


def protocol_bytes(path: Path, revision: str | None = None) -> bytes:
    """Read the protocol from the same source revision as recorded research."""
    if revision is None:
        return path.read_bytes()
    relative = path.resolve().relative_to(REPO_ROOT)
    return subprocess.check_output(
        ["git", "show", f"{revision}:{relative.as_posix()}"], cwd=REPO_ROOT,
    )


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _number(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"expected finite numeric value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"expected finite numeric value: {value!r}")
    return result


def _optional_number(value) -> Decimal | None:
    return None if value in (None, "") else _number(value)


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _regression(row: dict) -> OFIRegression:
    values = {}
    for field in fields(OFIRegression):
        value = row[field.name]
        if field.name in {"horizon", "signal"}:
            values[field.name] = value
        elif field.name in {"horizon_ms", "n", "hac_lags"}:
            numeric = _number(value)
            if numeric != numeric.to_integral_value() or numeric < 0:
                raise ValueError(f"invalid regression integer: {field.name}")
            values[field.name] = int(numeric)
        else:
            numeric = _optional_number(value)
            values[field.name] = None if numeric is None else float(numeric)
    return OFIRegression(**values)


def _fill_buckets(payload: dict) -> list[OFIFillToxicityBucket]:
    buckets = []
    seen = set()
    for row in payload["fill_buckets"]:
        key = (row["horizon"], row["side"], row["bucket"])
        if key in seen or type(row["n"]) is not int or row["n"] < 0:
            raise ValueError("invalid or duplicate fill bucket")
        seen.add(key)
        buckets.append(OFIFillToxicityBucket(**{
            **row,
            **{name: _optional_number(row[name]) for name in (
                "avg_side_aligned_ofi", "avg_mid_move_bps", "median_mid_move_bps"
            )},
        }))
    return buckets


def _selected_buckets(buckets: list[OFIFillToxicityBucket]):
    eligible = [row for row in buckets if (
        row.horizon == "30s" and row.side == "all"
        and row.avg_mid_move_bps is not None
        and row.avg_side_aligned_ofi is not None
    )]
    adverse = [row for row in eligible if row.avg_side_aligned_ofi < 0]
    favorable = [row for row in eligible if row.avg_side_aligned_ofi >= 0]
    if not adverse or not favorable:
        return None
    return max(adverse, key=lambda row: row.n), max(favorable, key=lambda row: row.n)


def conditional_cluster_bootstrap(
    rows: list[dict],
    buckets: list[OFIFillToxicityBucket],
    starts: list[str],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Resample whole windows for the pooled contrast of fixed bucket labels."""
    if iterations <= 0:
        raise ValueError("bootstrap iterations must be positive")
    labels = [datetime.fromisoformat(start).strftime("%Y-%m-%d %H:00")
              for start in starts]
    if not labels or len(set(labels)) != len(labels):
        raise ValueError("bootstrap requires unique development window starts")
    selected = _selected_buckets(buckets)
    result = {
        "role": "diagnostic_only_does_not_change_gate",
        "unit": "5h_window",
        "windows": len(labels),
        "iterations": iterations,
        "seed": seed,
        "confidence": "0.95",
        "bucket_selection": "fixed_from_original_pooled_sample",
        "selected_buckets": None if selected is None else [r.bucket for r in selected],
        "mean_bps": None,
        "ci_low_bps": None,
        "ci_high_bps": None,
        "undefined_resamples": 0,
        "status": "inconclusive_missing_buckets",
    }
    if selected is None:
        return result
    ranges = {label: (lo, hi) for label, lo, hi in
              _bucket_ranges(DEFAULT_ALIGNED_OFI_BUCKET_EDGES)}
    if any(row.bucket not in ranges for row in selected):
        raise ValueError("conditional bucket labels differ from locked edges")
    # Every frozen window stays in the resampling frame, even with no fills.
    totals = {label: [[Decimal(0), 0], [Decimal(0), 0]] for label in labels}
    for row in rows:
        if row["block_start"] not in totals:
            raise ValueError("conditional fill row is outside the development panel")
        if row["horizon"] != "30s":
            continue
        value = _number(row["side_aligned_ofi"])
        response = _number(row["side_normalized_mid_move_bps"])
        for index, bucket in enumerate(selected):
            if _in_range(float(value), *ranges[bucket.bucket]):
                total = totals[row["block_start"]][index]
                total[0] += response
                total[1] += 1
    clusters = list(totals.values())
    observed = []
    for index, bucket in enumerate(selected):
        count = sum(cluster[index][1] for cluster in clusters)
        total = sum((cluster[index][0] for cluster in clusters), Decimal(0))
        if count != bucket.n or not count:
            raise ValueError("conditional fill counts disagree with summary buckets")
        mean = total / count
        if abs(mean - bucket.avg_mid_move_bps) > Decimal("1e-20"):
            raise ValueError("conditional fill means disagree with summary buckets")
        observed.append(mean)
    result["mean_bps"] = observed[1] - observed[0]
    result["contributing_windows"] = {
        selected[index].bucket: sum(cluster[index][1] > 0 for cluster in clusters)
        for index in (0, 1)
    }
    rng = Random(seed)
    contrasts = []
    for _ in range(iterations):
        drawn = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        counts = [sum(cluster[index][1] for cluster in drawn) for index in (0, 1)]
        if not all(counts):
            result["undefined_resamples"] += 1
            continue
        means = [sum((cluster[index][0] for cluster in drawn), Decimal(0))
                 / counts[index] for index in (0, 1)]
        contrasts.append(means[1] - means[0])
    if result["undefined_resamples"]:
        result["status"] = "inconclusive_undefined_resamples"
    else:
        result.update(
            status="available",
            ci_low_bps=percentile(contrasts, Decimal("0.025")),
            ci_high_bps=percentile(contrasts, Decimal("0.975")),
        )
    return _jsonable(result)


def endpoint_screen(payload: dict, regressions: list[dict], starts: list[str]) -> dict:
    """Recompute the fixed primary gate, upstream all_pass is not advancement."""
    expected_labels = {
        datetime.fromisoformat(start).strftime("%Y-%m-%d %H:00") for start in starts
    }
    primary = [row for row in regressions if (
        row["window"] != "pooled" and row["signal"] == "normalized_ofi"
        and row["horizon"] == "1s"
    )]
    if len(primary) != len(starts) or {row["window"] for row in primary} != expected_labels:
        raise ValueError("OFI regressions do not cover the exact development panel")
    window_regressions = [_regression(row) for row in primary]
    pooled = [_regression(row) for row in payload["pooled_regressions"]]
    primary_pooled = [row for row in pooled if (
        row.signal == "normalized_ofi" and row.horizon == "1s"
    )]
    if len(primary_pooled) != 1:
        raise ValueError("OFI requires exactly one primary pooled regression")
    buckets = _fill_buckets(payload)
    gate = evaluate_ofi_gates(
        window_regressions=window_regressions,
        pooled_regressions=pooled,
        fill_buckets=buckets,
    )
    if _jsonable(asdict(gate)) != payload["gates"]:
        raise ValueError("OFI stored gates disagree with primary regression/bucket evidence")
    if not gate.unconditional_pass or gate.conditional_status == "fail_signal":
        status = "blocked"
    elif gate.conditional_status == "inconclusive_power":
        status = "inconclusive"
    else:
        status = "supported"
    selected = _selected_buckets(buckets)
    return {
        "status": status,
        "primary_interval_ms": 1000,
        "primary_population_horizon": "1s",
        "primary_conditional_horizon": "30s",
        "pooled_positive_beta_hypothesis": (
            None if primary_pooled[0].beta is None else primary_pooled[0].beta > 0
        ),
        "positive_windows": sum(row.beta is not None and row.beta > 0
                                for row in window_regressions),
        "negative_windows": sum(row.beta is not None and row.beta < 0
                                for row in window_regressions),
        "usable_windows": gate.total_windows,
        "conditional_below_1bps_hypothesis": (
            None if gate.conditional_status == "inconclusive_power"
            else gate.conditional_separation_bps < Decimal("1.0")
        ),
        "selected_conditional_buckets": None if selected is None else [
            _jsonable(asdict(row)) for row in selected
        ],
        "primary_gate": _jsonable(asdict(gate)),
        "fallback_5s_role": "exploratory_excluded_from_decision",
    }


def combined_screen(endpoints: dict) -> str:
    if set(endpoints) != {"0", "1"}:
        raise ValueError("both queue-credit endpoints 0 and 1 are required")
    statuses = [row["status"] for row in endpoints.values()]
    if any(status not in {"blocked", "inconclusive", "supported"} for status in statuses):
        raise ValueError("unknown endpoint screen status")
    if "blocked" in statuses:
        return "blocked"
    return "inconclusive" if "inconclusive" in statuses else "supported"


def _verified_file(root: Path, relative: str, manifest: dict) -> Path:
    path = root / relative
    if (
        Path(relative).is_absolute()
        or root.resolve() not in path.resolve().parents
        or path.is_symlink()
        or relative not in manifest["artifacts"]
        or _artifact_sha256(path) != manifest["artifacts"][relative]
    ):
        raise ValueError(f"artifact is not a verified file under the rerun root: {relative}")
    return path


def _endpoint_artifacts(root: Path, directory: str, manifest: dict) -> dict:
    endpoints = {}
    for relative in manifest["artifacts"]:
        parts = Path(relative).parts
        if parts[0] != directory or not relative.endswith(".json"):
            continue
        if directory == "ofi_signal" and parts[-1] != "summary.json":
            continue
        path = _verified_file(root, relative, manifest)
        payload = _read_json(path)
        provenance = require_event_driven_provenance(payload)
        credit = format(parse_queue_credit(provenance["queue_cancellation_credit"]).normalize(), "f")
        if credit in endpoints or credit not in {"0", "1"}:
            raise ValueError(f"duplicate or unexpected {directory} endpoint: {credit}")
        endpoints[credit] = (path, payload)
    if set(endpoints) != {"0", "1"}:
        raise ValueError(f"both verified {directory} endpoints are required")
    return endpoints


def _require_ofi_experiment(payload: dict, ci: dict, starts: list[str]) -> None:
    params = payload["params"]
    for key in ("symbol", "strategy", "hours", "session_hours", "half_spread",
                "order_qty", "max_position", "requote_interval_ms", "latency_ms",
                "jitter_ms", "maker_bps", "taker_bps"):
        if params.get(key) != ci["params"][key]:
            raise ValueError(f"OFI and baseline experiment differ: {key}")
    if require_event_driven_provenance(payload) != require_event_driven_provenance(ci):
        raise ValueError("OFI and baseline execution provenance differ")
    if parse_queue_credit(params["queue_cancellation_credit"]) != parse_queue_credit(
        ci["execution_provenance"]["queue_cancellation_credit"]
    ):
        raise ValueError("OFI queue-credit parameter contradicts provenance")
    if (
        sorted(datetime.fromisoformat(start) for start in params["starts"])
        != sorted(datetime.fromisoformat(start) for start in starts)
        or payload["counts"]["windows"] != len(starts)
        or params.get("sample_interval_ms") != 1000
        or params.get("ofi_interval_ms") != 1000
        or params.get("horizons_ms") != DEFAULT_OFI_HORIZONS_MS
        or params.get("fill_horizons_ms") != DEFAULT_OFI_FILL_HORIZONS_MS
    ):
        raise ValueError("OFI inputs do not match the locked panel and primary horizons")


def build_summary(
    output_root: Path,
    status_dir: Path,
    *,
    legacy_root: Path = DEFAULT_LEGACY_ROOT,
    protocol_path: Path = DEFAULT_PROTOCOL,
    source_revision: str | None = None,
    artifacts_only: bool = False,
) -> dict:
    manifest = verify(
        output_root, status_dir,
        source_revision=source_revision, artifacts_only=artifacts_only,
    )
    starts = manifest["development_panel"]["starts"]
    if len(starts) != 24:
        raise ValueError("V3 decision requires the complete 24-window panel")
    protocol = protocol_bytes(protocol_path, manifest["source_verification"]["revision"])
    if PROTOCOL_ID not in protocol.decode("utf-8"):
        raise ValueError("V3 protocol identity is missing")
    cis = _endpoint_artifacts(output_root, "baseline_ci", manifest)
    ofis = _endpoint_artifacts(output_root, "ofi_signal", manifest)
    comparison = build_baseline_comparison(
        {credit: path for credit, (path, _) in cis.items()},
        {credit: legacy_root / "baseline_ci" / path.name
         for credit, (path, _) in cis.items()},
        Path(manifest["development_panel"]["path"]),
    )
    endpoints = {}
    for credit in ("0", "1"):
        ci_path, ci = cis[credit]
        ofi_path, ofi = ofis[credit]
        _require_ofi_experiment(ofi, ci, starts)
        siblings = {}
        for filename in ("regressions.csv", "fill_toxicity_rows.csv"):
            relative = str((ofi_path.parent / filename).relative_to(output_root))
            siblings[filename] = _read_csv(_verified_file(output_root, relative, manifest))
        fill_rows = siblings["fill_toxicity_rows.csv"]
        if len(fill_rows) != ofi["counts"]["fill_horizon_rows"]:
            raise ValueError("OFI fill row count disagrees with summary")
        screen = endpoint_screen(ofi, siblings["regressions.csv"], starts)
        endpoints[credit] = {
            **screen,
            "conditional_cluster_bootstrap": conditional_cluster_bootstrap(
                fill_rows, _fill_buckets(ofi), starts
            ),
            "baseline_ci_path": str(ci_path),
            "baseline_ci_sha256": _artifact_sha256(ci_path),
            "ofi_path": str(ofi_path),
            "ofi_sha256": _artifact_sha256(ofi_path),
            "baseline_diagnostics": ci,
            "ofi_diagnostics": ofi,
        }
    status = combined_screen(endpoints)
    return {
        "report_version": "v3_development_summary_v1",
        "protocol": {"id": PROTOCOL_ID, "path": str(protocol_path),
                     "sha256": hashlib.sha256(protocol).hexdigest()},
        "verified_artifact_manifest": {
            "path": str(output_root / "ARTIFACT_MANIFEST.json"),
            "sha256": _artifact_sha256(output_root / "ARTIFACT_MANIFEST.json"),
            "source_fingerprint": manifest["source_fingerprint"],
            "source_verification": manifest["source_verification"],
            "raw_verification": manifest["raw_verification"],
        },
        "development_panel": manifest["development_panel"],
        "combined_screen": status,
        "candidate_evaluated": False,
        "automatic_candidate_advance": False,
        "automatic_holdout_advance": False,
        "holdout_status": "strategy_sealed",
        "interpretation": (
            "Fixed primary development screen only. Both endpoints are required; "
            "weak conditional counts are inconclusive. Clustered intervals and "
            "five-second fallback analyses do not change the gate. A supported "
            "screen requires a separately specified development candidate and "
            "does not authorize holdout evaluation."
        ),
        "execution_model_comparison": comparison,
        "endpoints": endpoints,
        "evidence_limits": [
            "Five-hour windows aggregate five independently reset hourly episodes.",
            "Legacy queue/latency grids remain historical; only current endpoint "
            "results at the locked latency are regenerated here.",
            "Conditional bootstrap fixes selected pooled buckets and assumes "
            "window clusters are independent; it is not selection-adjusted.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--status-dir", type=Path)
    parser.add_argument("--legacy-root", type=Path, default=DEFAULT_LEGACY_ROOT)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument(
        "--source-revision", metavar="recorded|COMMIT",
        help="Verify the run's recorded Git source; default checks the current tree",
    )
    parser.add_argument(
        "--artifacts-only", action="store_true",
        help="Recompute from verified artifacts and frozen input identities without reading raw captures",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    guard_event_driven_output_path(args.output)
    if args.output_root.resolve() in args.output.resolve().parents:
        raise ValueError("write the decision summary outside the verified artifact tree")
    report = build_summary(
        args.output_root,
        args.status_dir or args.output_root / "status",
        legacy_root=args.legacy_root,
        protocol_path=args.protocol,
        source_revision=args.source_revision,
        artifacts_only=args.artifacts_only,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"V3 development screen: {report['combined_screen']}; holdout remains strategy-sealed")
    if args.artifacts_only:
        print("Raw captures: NOT READ (--artifacts-only); frozen inventory identities verified")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
