#!/usr/bin/env python3
"""Verify the frozen V2 Phase 2 research artifacts.

This is a lightweight reproducibility guard. It checks provenance hashes,
development verdicts, queue-stress shape, and the sealed-holdout boundary
without re-running the expensive replay panel.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL_ROOT = ROOT / "results" / "panels" / "btcusdt_l2_panel_v2"

MANIFEST_SHA = "a3a99b0a616abe3bc39e0863ed047f075db9ed5d8118ced57c16140b499d8a61"
PANEL_SHA = "760c55b7c0929b4a99657f6ca02eb723d930b9f48ebd3786d57bcb1a0f481122"

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


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise AssertionError(f"missing artifact: {path.relative_to(ROOT)}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


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
    _assert_true(gates["conditional_bucket_count_min"] >= 30, f"{path.name} conditional power")

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

    allowed = {(PANEL_ROOT / "holdout_windows.csv").resolve()}
    holdout_paths = {
        path.resolve()
        for path in (ROOT / "results").rglob("*holdout*")
        if path.is_file()
    }
    unexpected = sorted(path.relative_to(ROOT) for path in holdout_paths - allowed)
    _assert_equal(unexpected, [], "unexpected holdout result artifacts")


def main() -> None:
    checks = [
        ("legacy execution-model boundary", verify_execution_model_boundary),
        ("panel selection", verify_panel_selection),
        ("Phase A verdict", verify_phase_a),
        ("Phase B OFI gate", verify_phase_b),
        ("Phase C queue stress", verify_phase_c),
        ("same-ms audit", verify_same_ms_audit),
        ("sealed holdout", verify_holdout_sealed),
    ]
    for label, check in checks:
        check()
        print(f"OK: {label}")
    print("OK: V2 Phase 2 artifacts verified")


if __name__ == "__main__":
    main()
