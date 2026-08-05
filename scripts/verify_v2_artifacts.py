#!/usr/bin/env python3
"""Verify the frozen V2 Phase 2 research artifacts.

This is a lightweight reproducibility guard. It checks selected identities and
file hashes, development verdicts, queue-stress shape, the snapshot-timing
audit, and the strategy-sealed holdout boundary without re-running the
expensive replay panel.
"""

from __future__ import annotations

import json
import hashlib
import csv
import subprocess
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.replay.depth_parser import SNAPSHOT_TIME_POLICY


ROOT = Path(__file__).resolve().parents[1]
PANEL_ROOT = ROOT / "results" / "panels" / "btcusdt_l2_panel_v2"

MANIFEST_SHA = "a3a99b0a616abe3bc39e0863ed047f075db9ed5d8118ced57c16140b499d8a61"
PANEL_SHA = "760c55b7c0929b4a99657f6ca02eb723d930b9f48ebd3786d57bcb1a0f481122"
DEVELOPMENT_CSV_SHA = "c779138fdffb739715c53cfa27c75b3c8ca140bc4f5a6128f60f2251948bd362"
HOLDOUT_CSV_SHA = "be0e92069ee5b6a2939f2148ad10daa18127d841dde063599981333aa8ffe8f7"
SNAPSHOT_AUDIT_SHA = "bd277f939e2e3e1d9b4b4fcc2f3227ea3dd18b8755f43014a9e6d70e9ca2678f"
SNAPSHOT_AUDIT = ROOT / "results/replay_correctness/snapshot_timing_panel24.json"

OFI_QC1 = (
    PANEL_ROOT
    / "ofi_signal"
    / "btcusdt_microprice_ofi_20260412_09_to_20260509_13_24blocks_1000ms"
    / "summary.json"
)
OFI_QC0 = (
    PANEL_ROOT
    / "ofi_signal"
    / "btcusdt_microprice_ofi_20260412_09_to_20260509_13_24blocks_1000ms_qc0"
    / "summary.json"
)
SAME_MS_QC1 = (
    PANEL_ROOT
    / "same_ms_audit"
    / "btcusdt_microprice_hs2.00_rq5000_panel24"
    / "summary.json"
)
SAME_MS_QC0 = (
    PANEL_ROOT
    / "same_ms_audit"
    / "btcusdt_microprice_hs2.00_rq5000_panel24_qc0"
    / "summary.json"
)
FEE_BREAK_EVEN = (
    PANEL_ROOT
    / "fee_break_even"
    / "btcusdt_microprice_hs2.00_rq5000_panel24"
    / "summary.json"
)


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise AssertionError(f"missing artifact: {path.relative_to(ROOT)}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _tracked_result_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--", "results"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [
        ROOT / value.decode("utf-8")
        for value in completed.stdout.split(b"\0")
        if value
    ]


def _assert_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def _assert_true(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def verify_panel_selection() -> None:
    summary = _load_json(PANEL_ROOT / "window_selection_summary.json")
    _assert_equal(summary["integrity_manifest_sha256"], MANIFEST_SHA, "manifest hash")
    _assert_equal(summary["panel_sha256"], PANEL_SHA, "panel hash")

    counts = summary["counts"]
    _assert_equal(counts["development_selected"], 24, "development window count")
    _assert_equal(counts["holdout_selected"], 12, "holdout window count")
    _assert_equal(counts["development_nonoverlap_capacity"], 89, "development capacity")
    _assert_equal(counts["holdout_nonoverlap_capacity"], 43, "holdout capacity")
    _assert_equal(summary["regime_comparison"]["label"], "regime-shifted", "holdout regime label")

    manifest = _load_json(PANEL_ROOT / "integrity_manifest.json")
    _assert_equal(manifest["manifest_sha256"], MANIFEST_SHA, "integrity manifest self hash")


def verify_execution_model_boundary() -> None:
    marker = _load_json(PANEL_ROOT / "EXECUTION_MODEL.json")
    _assert_equal(
        marker["execution_model_version"],
        "legacy_book_update_v1",
        "frozen V2 execution model",
    )
    _assert_equal(
        marker["status"],
        "frozen_historical_artifacts",
        "frozen V2 model status",
    )

    for path in PANEL_ROOT.rglob("*.json"):
        if path.name == "EXECUTION_MODEL.json":
            continue
        payload = _load_json(path)
        if not isinstance(payload, dict):
            continue
        provenance = payload.get("execution_provenance", {})
        params = payload.get("params", {})
        if not isinstance(provenance, dict):
            provenance = {}
        if not isinstance(params, dict):
            params = {}
        model = provenance.get(
            "execution_model_version",
            params.get("execution_model_version"),
        )
        _assert_true(
            model != "event_driven_v2",
            f"event-driven artifact found under frozen V2 root: {path}",
        )


def verify_phase_a() -> None:
    verdict = _load_json(PANEL_ROOT / "phase_a_verdict.json")
    _assert_equal(
        verdict["headline"],
        "Conditional V2: queue-model-dependent result",
        "Phase A headline",
    )
    _assert_equal(
        verdict["conservative_passive_edge_verdict"],
        "Strengthens V1",
        "Phase A conservative verdict",
    )
    _assert_equal(verdict["endpoints"]["1.0"]["verdict"], "Strengthens V1", "qc1 verdict")
    _assert_equal(verdict["endpoints"]["0.0"]["verdict"], "Weakens V1", "qc0 verdict")
    _assert_true(
        Decimal(verdict["endpoints"]["1.0"]["ci_high"]) < 0,
        "qc1 CI upper bound should be below zero",
    )


def verify_ofi_summary(path: Path, expected_separation_sign: int) -> None:
    summary = _load_json(path)
    gates = summary["gates"]
    _assert_equal(gates["overall_verdict"], "blocked", f"{path.name} OFI verdict")
    _assert_equal(gates["unconditional_pass"], True, f"{path.name} unconditional pass")
    _assert_equal(gates["conditional_status"], "fail_signal", f"{path.name} conditional status")
    _assert_equal(gates["conditional_pass"], False, f"{path.name} conditional pass")
    _assert_true(
        gates["conditional_bucket_count_min"] >= 30,
        f"{path.name} conditional minimum count",
    )

    separation = Decimal(str(gates["conditional_separation_bps"]))
    if expected_separation_sign > 0:
        _assert_true(separation > 0, f"{path.name} expected positive conditional separation")
    else:
        _assert_true(separation < 0, f"{path.name} expected negative conditional separation")

    normalized_1s = [
        row
        for row in summary["pooled_regressions"]
        if row["horizon"] == "1s" and row["signal"] == "normalized_ofi"
    ]
    _assert_equal(len(normalized_1s), 1, f"{path.name} normalized 1s regression count")
    row = normalized_1s[0]
    _assert_true(row["t_stat"] > 60, f"{path.name} 1s OFI t-stat")
    _assert_true(row["predicted_drift_1std_bps"] > 0.05, f"{path.name} 1s OFI effect size")


def verify_phase_b() -> None:
    verify_ofi_summary(OFI_QC1, expected_separation_sign=1)
    verify_ofi_summary(OFI_QC0, expected_separation_sign=-1)


def verify_phase_c() -> None:
    summary = _load_json(PANEL_ROOT / "queue_credit_sweep" / "summary" / "summary.json")
    pooled = [row for row in summary["rows"] if row["window"] == "pooled"]

    expected_pairs = {
        ("0.0", 0),
        ("0.0", 10),
        ("0.0", 50),
        ("0.25", 10),
        ("0.5", 10),
        ("0.75", 10),
        ("1.0", 0),
        ("1.0", 10),
        ("1.0", 50),
    }
    actual_pairs = {(row["queue_cancellation_credit"], int(row["latency_ms"])) for row in pooled}
    _assert_true(expected_pairs.issubset(actual_pairs), "Phase C pooled credit/latency grid")

    lat10_by_credit = {
        row["queue_cancellation_credit"]: Decimal(str(row["quantity_weighted_matched_net_pnl_per_btc"]))
        for row in pooled
        if int(row["latency_ms"]) == 10
    }
    ordered = [lat10_by_credit[credit] for credit in ["0.0", "0.25", "0.5", "0.75", "1.0"]]
    _assert_true(all(left > right for left, right in zip(ordered, ordered[1:])), "matched net/BTC worsens with queue credit")

    for credit in ["0.0", "1.0"]:
        rows = {
            int(row["latency_ms"]): row
            for row in pooled
            if row["queue_cancellation_credit"] == credit
        }
        base = rows[10]
        for latency in [0, 50]:
            row = rows[latency]
            for key in [
                "fills",
                "matched_net_pnl",
                "quantity_weighted_matched_net_pnl_per_btc",
                "full_strategy_net_pnl",
            ]:
                _assert_equal(row[key], base[key], f"credit {credit} latency-invariant {key}")


def verify_fee_hurdle() -> None:
    summary = _load_json(FEE_BREAK_EVEN)
    _assert_equal(summary["counts"]["runs"], 48, "fee-hurdle run count")
    _assert_equal(summary["counts"]["rows"], 200, "fee-hurdle row count")
    _assert_equal(
        summary["params"]["full_strategy_endpoint_note"],
        "full_strategy rows are endpoint-sensitive because residual inventory "
        "is marked at the window-close mid",
        "preserved fee-summary metadata erratum target",
    )
    rows = {
        (row["queue_cancellation_credit"], row["row_type"]): row
        for row in summary["pooled_rows"]
    }
    _assert_equal(len(rows), 8, "fee-hurdle pooled row count")
    _assert_equal(
        Decimal(rows[("0.0", "full_matched_lots")]["break_even_maker_fee_bps"]),
        Decimal("1.350811289527476826785580593"),
        "no-credit matched break-even maker fee",
    )
    _assert_equal(
        Decimal(rows[("1.0", "full_matched_lots")]["break_even_maker_fee_bps"]),
        Decimal("0.4260803289029836443461217242"),
        "proportional-credit matched break-even maker fee",
    )
    _assert_equal(
        Decimal(rows[("0.0", "full_strategy")]["required_rebate_bps"]),
        Decimal("0.7692962729616433880499865620"),
        "no-credit full-strategy required rebate",
    )
    _assert_equal(
        Decimal(rows[("1.0", "full_strategy")]["required_rebate_bps"]),
        Decimal("1.351459507642685030232297886"),
        "proportional-credit full-strategy required rebate",
    )


def verify_same_ms_audit() -> None:
    expected = {
        SAME_MS_QC1: {
            "total_fills": 2041,
            "same_ms_overlap_fills": 10,
        },
        SAME_MS_QC0: {
            "total_fills": 1595,
            "same_ms_overlap_fills": 7,
        },
    }
    for path, expected_values in expected.items():
        summary = _load_json(path)
        aggregate = summary["aggregate"]
        _assert_equal(
            aggregate["same_ms_overlap_timestamps"],
            16613,
            f"{path.name} same-ms overlap timestamp count",
        )
        _assert_equal(
            aggregate["total_fills"],
            expected_values["total_fills"],
            f"{path.name} same-ms total fills",
        )
        _assert_equal(
            aggregate["same_ms_overlap_fills"],
            expected_values["same_ms_overlap_fills"],
            f"{path.name} same-ms overlap fills",
        )
        _assert_equal(
            aggregate["wrong_attribution_cases"],
            0,
            f"{path.name} same-ms wrong attribution cases",
        )
        _assert_true(
            Decimal(str(aggregate["same_ms_overlap_fill_share_pct"])) < Decimal("1"),
            f"{path.name} same-ms overlap share below 1 pct",
        )


def verify_snapshot_timing_audit() -> None:
    _assert_equal(
        _file_sha256(SNAPSHOT_AUDIT),
        SNAPSHOT_AUDIT_SHA,
        "snapshot timing audit file hash",
    )
    audit = _load_json(SNAPSHOT_AUDIT)
    _assert_equal(audit["schema_version"], 1, "snapshot timing audit schema")
    _assert_equal(
        audit["finding"]["current_snapshot_time_policy"],
        SNAPSHOT_TIME_POLICY,
        "snapshot timing policy",
    )
    _assert_equal(
        audit["counts"],
        {
            "later_receipt_proxies": 120,
            "legacy_pre_fetch_tags": 120,
            "snapshots": 120,
            "unbridged": 0,
            "unreleased_snapshots": 0,
            "valid_bridges": 120,
            "valid_policy_boundaries": 120,
        },
        "snapshot timing coverage",
    )

    development_csv = PANEL_ROOT / "development_windows.csv"
    integrity_path = PANEL_ROOT / "integrity_manifest.json"
    _assert_equal(
        audit["scope"]["windows_csv_sha256"],
        _file_sha256(development_csv),
        "snapshot audit development CSV hash",
    )
    _assert_equal(
        audit["scope"]["integrity_manifest_file_sha256"],
        _file_sha256(integrity_path),
        "snapshot audit integrity file hash",
    )
    integrity = _load_json(integrity_path)
    _assert_equal(
        audit["scope"]["integrity_manifest_identity"],
        integrity["manifest_sha256"],
        "snapshot audit integrity identity",
    )

    selected_hours = []
    with development_csv.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            start = datetime.fromisoformat(row["start"])
            selected_hours.extend(
                start + timedelta(hours=offset)
                for offset in range(int(row["hours"]))
            )
    inventory = {
        datetime.fromisoformat(row["start"]): row
        for row in integrity["hours"]
    }
    expected_depth = {
        (
            hour.isoformat(),
            f"data/raw/btcusdt/{Path(inventory[hour]['depth_path']).name}",
            inventory[hour]["depth_sha256"],
        )
        for hour in selected_hours
    }
    actual_depth = {
        (row["hour"], row["depth_path"], row["depth_sha256"])
        for row in audit["rows"]
    }
    _assert_equal(len(actual_depth), 120, "snapshot audit unique depth rows")
    _assert_equal(actual_depth, expected_depth, "snapshot audit selected depth rows")
    _assert_true(
        all(
            row["bridge_valid"]
            and row["policy_boundary_valid"]
            and row["bridge_event_time_ms"]
            == row["policy_boundary_event_time_ms"]
            for row in audit["rows"]
        ),
        "snapshot audit bridge/policy boundary consistency",
    )

    generator = audit["generator"]
    generator_path = ROOT / generator["path"]
    _assert_equal(
        _file_sha256(generator_path),
        generator["sha256"],
        "snapshot audit generator hash",
    )
    receipt = audit["request_tag_to_first_later_depth_receipt_ms"]
    for key, expected in {
        "n": 120,
        "median": 250.5,
        "p90": 672.9,
        "max": 2335,
    }.items():
        _assert_equal(receipt[key], expected, f"snapshot receipt proxy {key}")

    fill_proximity = audit["frozen_fill_proximity"]
    for credit, fills, pre_bridge in (("0", 1595, 2), ("1", 2041, 4)):
        _assert_equal(
            fill_proximity[credit]["fills"],
            fills,
            f"snapshot audit credit {credit} fill count",
        )
        _assert_equal(
            fill_proximity[credit]["fills_from_orders_placed_before_policy_boundary"],
            pre_bridge,
            f"snapshot audit credit {credit} pre-boundary fills",
        )
        _assert_equal(
            fill_proximity[credit]["fills_within_5s_of_legacy_snapshot_tag"],
            1,
            f"snapshot audit credit {credit} near-tag fills",
        )

    reconciliation_inputs = audit["reconciliation_inputs"]
    _assert_equal(len(reconciliation_inputs), 48, "snapshot reconciliation input count")
    _assert_equal(
        len({row["path"] for row in reconciliation_inputs}),
        48,
        "snapshot reconciliation input paths",
    )
    for credit, expected_rows in (("0", 1595), ("1", 2041)):
        _assert_equal(
            sum(
                row["rows"]
                for row in reconciliation_inputs
                if row["queue_credit"] == credit
            ),
            expected_rows,
            f"snapshot reconciliation credit {credit} rows",
        )
    _assert_true(
        all(
            len(row["sha256"]) == 64
            and set(row["sha256"]) <= set("0123456789abcdef")
            for row in reconciliation_inputs
        ),
        "snapshot reconciliation input hashes",
    )


def verify_holdout_sealed() -> None:
    protocol = ROOT / "notebooks" / "holdout_protocol.md"
    text = protocol.read_text(encoding="utf-8")
    _assert_true("UNLOCKED TEMPLATE" in text, "holdout protocol should remain unlocked")
    _assert_true("Lock timestamp (UTC): `TBD`" in text, "holdout lock timestamp should be TBD")
    _assert_true(
        "no active candidate qualified for holdout" in text,
        "holdout should have no active candidate",
    )
    _assert_true("Strategy: `TBD`" in text, "holdout strategy should remain TBD")

    development_csv = PANEL_ROOT / "development_windows.csv"
    holdout_csv = PANEL_ROOT / "holdout_windows.csv"
    _assert_equal(
        _file_sha256(development_csv),
        DEVELOPMENT_CSV_SHA,
        "development CSV file hash",
    )
    _assert_equal(
        _file_sha256(holdout_csv),
        HOLDOUT_CSV_SHA,
        "holdout CSV file hash",
    )

    allowed = {holdout_csv.resolve()}
    holdout_paths = {
        path.resolve()
        for path in (ROOT / "results").rglob("*holdout*")
        if path.is_file()
    }
    unexpected = sorted(path.relative_to(ROOT) for path in holdout_paths - allowed)
    _assert_equal(unexpected, [], "unexpected holdout result artifacts")

    panel = _load_json(PANEL_ROOT / "window_selection_summary.json")
    holdout_tokens = set()
    for row in panel["holdout_windows"]:
        start = row["start"]
        holdout_tokens.update(
            {
                start,
                start[:13],
                start[:10].replace("-", "") + "_" + start[11:13],
            }
        )

    content_allowlist = {
        (PANEL_ROOT / "integrity_manifest.json").resolve(),
        (PANEL_ROOT / "window_selection_summary.json").resolve(),
        holdout_csv.resolve(),
    }
    contaminated = []
    for path in _tracked_result_files():
        if path.resolve() in content_allowlist:
            continue
        relative = str(path.relative_to(ROOT))
        content = path.read_text(encoding="utf-8", errors="ignore")
        if any(token in relative or token in content for token in holdout_tokens):
            contaminated.append(path.relative_to(ROOT))
    _assert_equal(
        sorted(contaminated),
        [],
        "tracked result artifacts containing holdout window identifiers",
    )


def main() -> None:
    checks = [
        ("legacy execution-model boundary", verify_execution_model_boundary),
        ("panel selection", verify_panel_selection),
        ("Phase A verdict", verify_phase_a),
        ("Phase B OFI gate", verify_phase_b),
        ("Phase C queue stress", verify_phase_c),
        ("fee hurdle", verify_fee_hurdle),
        ("same-ms audit", verify_same_ms_audit),
        ("snapshot timing audit", verify_snapshot_timing_audit),
        ("strategy-sealed holdout", verify_holdout_sealed),
    ]
    for label, check in checks:
        check()
        print(f"OK: {label}")
    print("OK: V2 Phase 2 artifacts verified")


if __name__ == "__main__":
    main()
