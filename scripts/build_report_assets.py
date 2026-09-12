"""Build publication figures and LaTeX scalars from frozen tracked artifacts.

The report intentionally reads a small allowlist of canonical frozen artifacts.
Every input shape is checked before a figure is written so presentation code
cannot silently average the separate pooled rows or mix execution models.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

from src.replay.depth_parser import SNAPSHOT_TIME_POLICY


ROOT = Path(__file__).resolve().parents[1]
PANEL_ROOT = ROOT / "results/panels/btcusdt_l2_panel_v2"
FIGURE_ROOT = ROOT / "report/figures"
GENERATED_ROOT = ROOT / "report/generated"

PHASE_A = PANEL_ROOT / "phase_a_verdict.json"
PANEL_SELECTION = PANEL_ROOT / "window_selection_summary.json"
INTEGRITY_MANIFEST = PANEL_ROOT / "integrity_manifest.json"
OFI_QC1 = PANEL_ROOT / (
    "ofi_signal/"
    "btcusdt_microprice_ofi_20260412_09_to_20260509_13_24blocks_1000ms/"
    "summary.json"
)
OFI_QC0 = PANEL_ROOT / (
    "ofi_signal/"
    "btcusdt_microprice_ofi_20260412_09_to_20260509_13_24blocks_1000ms_qc0/"
    "summary.json"
)
QUEUE_STRESS = PANEL_ROOT / "queue_credit_sweep/summary/summary.json"
FEE_BREAK_EVEN = PANEL_ROOT / (
    "fee_break_even/"
    "btcusdt_microprice_hs2.00_rq5000_panel24/summary.json"
)
EXECUTION_MARKER = PANEL_ROOT / "EXECUTION_MODEL.json"
SNAPSHOT_TIMING_AUDIT = ROOT / (
    "results/replay_correctness/snapshot_timing_panel24.json"
)
SNAPSHOT_AUDIT_GENERATOR = ROOT / "scripts/audit_snapshot_timing.py"
SNAPSHOT_POLICY_SOURCE = ROOT / "src/replay/depth_parser.py"
DEVELOPMENT_WINDOWS = PANEL_ROOT / "development_windows.csv"

INPUTS = (
    PHASE_A,
    PANEL_SELECTION,
    INTEGRITY_MANIFEST,
    OFI_QC1,
    OFI_QC0,
    QUEUE_STRESS,
    FEE_BREAK_EVEN,
    EXECUTION_MARKER,
    SNAPSHOT_TIMING_AUDIT,
    SNAPSHOT_AUDIT_GENERATOR,
    SNAPSHOT_POLICY_SOURCE,
    DEVELOPMENT_WINDOWS,
)

EXPECTED_INPUT_SHA256 = {
    PHASE_A: "afa2e387ad95ff2fcdde71d8552ed6a617af2dfdaa6e53b3d750e7c8f154ff71",
    PANEL_SELECTION: "77d7bb8e58033299db2c6f4220bf104e9bf9aa177c5ee56cd3ce514f5bec2539",
    INTEGRITY_MANIFEST: "1e5915973af4721b5ddab33f00c5bc9be395428aed3da6a1a054324794a963ee",
    OFI_QC1: "4e6a18bdd81e77187795ff6d532448b3a3bd239956d0da169f5e774b23a1977d",
    OFI_QC0: "3c84cb2bfa1e31bf8d2b161b1d01834fa31a062bf194d1f79751b9d24fca979d",
    QUEUE_STRESS: "6d1a0dd81a9727e233a185778a33f432f91111effdd9ddf75d3b0a0a4b7a11e8",
    FEE_BREAK_EVEN: "9c9c71bc5182353ebbd010bd3f1f380a5ec2fdf1168384464e85193ab7da879f",
    EXECUTION_MARKER: "3efb46f139bf8140cbd002c72d7d6a61ae329ad28a519feae2f519d9a18b7c1c",
    SNAPSHOT_TIMING_AUDIT: "bd277f939e2e3e1d9b4b4fcc2f3227ea3dd18b8755f43014a9e6d70e9ca2678f",
    SNAPSHOT_AUDIT_GENERATOR: "60e4da20dded9dfd231c3349c15b6bc7fa37f887cf13ebfcb63e38c3cf1103d0",
    SNAPSHOT_POLICY_SOURCE: "cc84364b6ec48d277ce0de83e9e10b296039050e90259a72eb61bd246fd8fdc2",
    DEVELOPMENT_WINDOWS: "c779138fdffb739715c53cfa27c75b3c8ca140bc4f5a6128f60f2251948bd362",
}

BLACK = "#000000"
BLUE = "#0072B2"
ORANGE = "#E69F00"
BLUISH_GREEN = "#009E73"
VERMILLION = "#D55E00"
SLATE = "#5F6B73"

BUCKET_ORDER = (
    "<-1.0",
    "[-1.0,-0.25)",
    "[-0.25,0.0)",
    "[0.0,0.25)",
    "[0.25,1.0)",
    ">=1.0",
)
EXPECTED_QUEUE_COMBOS = {
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
EXPECTED_MATPLOTLIB_VERSION = "3.10.8"
EXPECTED_NUMPY_VERSION = "2.4.1"


def _load(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"missing report input: {path.relative_to(ROOT)}")
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _assert_keys(value: dict, expected: set[str], label: str) -> None:
    missing = expected - set(value)
    if missing:
        raise ValueError(f"{label} is missing keys: {sorted(missing)}")


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _signed(value: Decimal, places: int) -> str:
    return f"{value:+.{places}f}"


def _style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "text.color": BLACK,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "axes.labelcolor": BLACK,
            "axes.titlecolor": BLACK,
            "axes.edgecolor": BLACK,
            "xtick.color": BLACK,
            "ytick.color": BLACK,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": "#C8C8C8",
            "grid.alpha": 0.22,
            "grid.linewidth": 0.6,
            "legend.frameon": False,
            "figure.dpi": 150,
            "savefig.bbox": "tight",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def _save(fig: plt.Figure, name: str) -> tuple[Path, Path]:
    path = FIGURE_ROOT / name
    png_path = path.with_suffix(".png")
    fixed_timestamp = datetime(2026, 8, 4, tzinfo=timezone.utc)
    fig.savefig(
        path,
        format="pdf",
        metadata={
            "Title": name.removesuffix(".pdf").replace("_", " ").title(),
            "Author": "Pranav Pillai",
            "Creator": "l2-mm-system report asset builder",
            "CreationDate": fixed_timestamp,
            "ModDate": fixed_timestamp,
        },
    )
    fig.savefig(
        png_path,
        format="png",
        dpi=180,
        metadata={"Software": "l2-mm-system report asset builder"},
    )
    plt.close(fig)
    return path, png_path


def build_phase_a_figure(phase_a: dict) -> tuple[Path, Path]:
    _assert_keys(phase_a, {"headline", "endpoints"}, "Phase A verdict")
    endpoints = phase_a["endpoints"]
    if set(endpoints) != {"0.0", "1.0"}:
        raise ValueError("Phase A must contain exactly queue credits 0.0 and 1.0")

    labels = ["No credit\n(qc=0)", "Proportional\n(qc=1)"]
    means = [_decimal(endpoints[key]["mean_net_pnl"]) for key in ("0.0", "1.0")]
    lows = [_decimal(endpoints[key]["ci_low"]) for key in ("0.0", "1.0")]
    highs = [_decimal(endpoints[key]["ci_high"]) for key in ("0.0", "1.0")]
    lower = [float(mean - low) for mean, low in zip(means, lows)]
    upper = [float(high - mean) for mean, high in zip(means, highs)]

    fig, ax = plt.subplots(figsize=(5.4, 3.2))
    for index, color, marker in ((0, ORANGE, "o"), (1, BLUE, "s")):
        ax.errorbar(
            [index],
            [float(means[index])],
            yerr=[[lower[index]], [upper[index]]],
            fmt=marker,
            markersize=7,
            capsize=6,
            color=color,
            ecolor=color,
            linewidth=2,
        )
    ax.axhline(0, color=SLATE, linewidth=1)
    ax.set_xticks([0, 1], labels)
    ax.set_ylabel("Mean summed hourly-mark P&L (USDT)")
    ax.set_title("Frozen legacy Phase A: 95% window-bootstrap intervals")
    ax.text(
        0.02,
        0.03,
        "24 development windows (legacy_book_update_v1)",
        transform=ax.transAxes,
        color=BLACK,
        fontsize=8,
    )
    fig.tight_layout()
    return _save(fig, "phase_a_ci.pdf")


def _ofi_regression(summary: dict) -> dict:
    rows = [
        row
        for row in summary["pooled_regressions"]
        if row["horizon"] == "1s" and row["signal"] == "normalized_ofi"
    ]
    if len(rows) != 1:
        raise ValueError("expected one pooled 1s normalized OFI regression")
    return rows[0]


def _ofi_population_buckets(summary: dict) -> list[dict]:
    rows = [
        row
        for row in summary["pooled_buckets"]
        if row["horizon"] == "1s" and row["signal"] == "normalized_ofi"
    ]
    by_label = {row["label"]: row for row in rows}
    if set(by_label) != set(BUCKET_ORDER):
        raise ValueError("unexpected unconditional OFI bucket schema")
    return [by_label[label] for label in BUCKET_ORDER]


def _ofi_fill_buckets(summary: dict) -> list[dict]:
    rows = [
        row
        for row in summary["fill_buckets"]
        if row["horizon"] == "30s" and row["side"] == "all"
    ]
    by_label = {row["bucket"]: row for row in rows}
    if set(by_label) != set(BUCKET_ORDER):
        raise ValueError("unexpected conditional OFI bucket schema")
    return [by_label[label] for label in BUCKET_ORDER]


def build_ofi_figure(ofi_qc0: dict, ofi_qc1: dict) -> tuple[Path, Path]:
    for expected_credit, summary in (("0.0", ofi_qc0), ("1.0", ofi_qc1)):
        _assert_keys(
            summary,
            {"params", "counts", "pooled_regressions", "pooled_buckets", "fill_buckets", "gates"},
            f"OFI qc={expected_credit}",
        )
        if summary["params"]["queue_cancellation_credit"] != expected_credit:
            raise ValueError(f"OFI queue-credit mismatch for qc={expected_credit}")
        if summary["counts"]["windows"] != 24:
            raise ValueError("OFI report inputs must cover 24 windows")
        if summary["gates"]["overall_verdict"] != "blocked":
            raise ValueError("the frozen OFI report input must retain the blocked verdict")

    regression_zero = _ofi_regression(ofi_qc0)
    regression_one = _ofi_regression(ofi_qc1)
    if regression_zero != regression_one:
        raise ValueError("unconditional OFI results must be queue-independent")

    population = _ofi_population_buckets(ofi_qc1)
    conditional_zero = _ofi_fill_buckets(ofi_qc0)
    conditional_one = _ofi_fill_buckets(ofi_qc1)
    x = list(range(len(BUCKET_ORDER)))
    short_labels = ["<-1", "-1:-.25", "-.25:0", "0:.25", ".25:1", ">=1"]

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.6))
    axes[0].bar(
        x,
        [float(row["avg_forward_drift_bps"]) for row in population],
        color=[VERMILLION] * 3 + [BLUE] * 3,
        edgecolor=BLACK,
        linewidth=0.8,
        width=0.72,
    )
    axes[0].axhline(0, color=SLATE, linewidth=0.9)
    axes[0].set_xticks(x, short_labels, rotation=18)
    axes[0].set_ylabel("Mean 1s forward mid drift (bps)")
    axes[0].set_xlabel("Normalized OFI bucket")
    axes[0].set_title("Population association: monotone")

    axes[1].plot(
        x,
        [float(row["avg_mid_move_bps"]) for row in conditional_zero],
        marker="o",
        color=ORANGE,
        linestyle="--",
        label="qc=0",
    )
    axes[1].plot(
        x,
        [float(row["avg_mid_move_bps"]) for row in conditional_one],
        marker="s",
        color=BLUE,
        linestyle="-",
        label="qc=1",
    )
    axes[1].axhline(0, color=SLATE, linewidth=0.9)
    axes[1].set_xticks(x, short_labels, rotation=18)
    axes[1].set_ylabel("Mean 30s side-normalized move (bps)")
    axes[1].set_xlabel("Side-aligned OFI bucket before fill")
    axes[1].set_title("Passive-fill sample: non-monotone")
    axes[1].legend(loc="upper left")

    fig.suptitle(
        "Frozen legacy OFI evidence: population predictability did not clear the fill gate",
        fontsize=11,
        y=1.02,
    )
    fig.tight_layout()
    return _save(fig, "ofi_population_vs_fills.pdf")


def build_queue_figure(queue_summary: dict) -> tuple[Path, Path]:
    rows = queue_summary.get("rows")
    if not isinstance(rows, list) or len(rows) != 225:
        raise ValueError("queue summary must contain 9 x (24 windows + 1 pooled) rows")

    combos = {(row["queue_cancellation_credit"], row["latency_ms"]) for row in rows}
    if combos != EXPECTED_QUEUE_COMBOS:
        raise ValueError(f"unexpected queue/latency grid: {sorted(combos)}")
    for combo in EXPECTED_QUEUE_COMBOS:
        combo_rows = [
            row
            for row in rows
            if (row["queue_cancellation_credit"], row["latency_ms"]) == combo
        ]
        if len(combo_rows) != 25 or sum(row["window"] == "pooled" for row in combo_rows) != 1:
            raise ValueError(f"queue grid {combo} must have 24 windows and one pooled row")

    pooled_ten = {
        row["queue_cancellation_credit"]: row
        for row in rows
        if row["window"] == "pooled" and row["latency_ms"] == 10
    }
    if set(pooled_ten) != {"0.0", "0.25", "0.5", "0.75", "1.0"}:
        raise ValueError("missing pooled 10ms queue-credit rows")

    credits = ["0.0", "0.25", "0.5", "0.75", "1.0"]
    x = [float(value) for value in credits]
    economics = [
        float(pooled_ten[value]["quantity_weighted_matched_net_pnl_per_btc"])
        for value in credits
    ]
    fills = [pooled_ten[value]["fills"] for value in credits]

    fig, axes = plt.subplots(1, 2, figsize=(9.8, 3.4))
    axes[0].plot(x, economics, marker="o", linewidth=2, color=VERMILLION)
    axes[0].axhline(0, color=SLATE, linewidth=0.9)
    axes[0].set_xlabel("Queue-cancellation credit")
    axes[0].set_ylabel("Matched net P&L per BTC (USDT)")
    axes[0].set_title("Fill-sample economics worsened monotonically")

    axes[1].plot(x, fills, marker="s", linewidth=2, color=BLUISH_GREEN)
    axes[1].set_xlabel("Queue-cancellation credit")
    axes[1].set_ylabel("Maker fills across 24 windows")
    axes[1].set_title("More modeled credit increased throughput")

    fig.suptitle("Frozen legacy Phase C at 10 ms entry latency", fontsize=11, y=1.02)
    fig.tight_layout()
    return _save(fig, "queue_credit_stress.pdf")


def build_panel_figure(panel: dict) -> tuple[Path, Path]:
    _assert_keys(
        panel,
        {"counts", "development_windows", "holdout_windows", "regime_comparison", "panel_sha256"},
        "panel selection",
    )
    if panel["counts"]["development_selected"] != 24 or panel["counts"]["holdout_selected"] != 12:
        raise ValueError("report panel must contain 24 development and 12 holdout windows")

    development = [datetime.fromisoformat(row["start"]) for row in panel["development_windows"]]
    holdout = [datetime.fromisoformat(row["start"]) for row in panel["holdout_windows"]]
    fig, ax = plt.subplots(figsize=(10.0, 2.4))
    ax.scatter(
        development,
        [1] * len(development),
        marker="s",
        s=34,
        facecolors=BLUE,
        edgecolors=BLACK,
        label="Development (24)",
    )
    ax.scatter(
        holdout,
        [0] * len(holdout),
        marker="D",
        s=28,
        facecolors=ORANGE,
        edgecolors=BLACK,
        label="Strategy-sealed (12)",
    )
    ax.set_yticks([0, 1], ["Holdout", "Development"])
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.tick_params(axis="x", rotation=30)
    ax.set_ylim(-0.55, 1.55)
    ax.set_title("Selected non-overlapping five-hour windows (2026)")
    ax.legend(loc="upper left", ncol=2)
    fig.tight_layout()
    return _save(fig, "panel_timeline.pdf")


def write_macros(
    phase_a: dict,
    panel: dict,
    integrity: dict,
    ofi_qc0: dict,
    ofi_qc1: dict,
    queue_summary: dict,
    fee_summary: dict,
    snapshot_audit: dict,
) -> Path:
    endpoint_zero = phase_a["endpoints"]["0.0"]
    endpoint_one = phase_a["endpoints"]["1.0"]
    regression = _ofi_regression(ofi_qc1)
    pooled_ten = {
        row["queue_cancellation_credit"]: row
        for row in queue_summary["rows"]
        if row["window"] == "pooled" and row["latency_ms"] == 10
    }
    fee_rows = {
        (row["queue_cancellation_credit"], row["row_type"]): row
        for row in fee_summary["pooled_rows"]
    }
    expected_fee_rows = {
        (credit, row_type)
        for credit in ("0.0", "1.0")
        for row_type in (
            "full_strategy",
            "full_matched_lots",
            "matched_tail_excluded_worst_5pct",
            "matched_body_only_ex_worst_best_5pct",
        )
    }
    if set(fee_rows) != expected_fee_rows:
        raise ValueError("unexpected fee break-even pooled-row schema")
    if (
        snapshot_audit.get("counts", {}).get("snapshots") != 120
        or snapshot_audit.get("counts", {}).get("valid_bridges") != 120
        or snapshot_audit.get("counts", {}).get("unbridged") != 0
        or snapshot_audit.get("counts", {}).get("valid_policy_boundaries") != 120
        or snapshot_audit.get("counts", {}).get("unreleased_snapshots") != 0
    ):
        raise ValueError("snapshot timing audit must cover 120 valid policy boundaries")
    if snapshot_audit.get("schema_version") != 1:
        raise ValueError("unexpected snapshot timing audit schema")
    if (
        snapshot_audit.get("finding", {}).get("current_snapshot_time_policy")
        != SNAPSHOT_TIME_POLICY
    ):
        raise ValueError("snapshot timing audit uses the wrong policy")
    receipt = snapshot_audit["request_tag_to_first_later_depth_receipt_ms"]
    bridge = snapshot_audit["request_tag_to_bridge_event_time_ms"]
    fill_proximity = snapshot_audit["frozen_fill_proximity"]

    macros = {
        "PhaseAQcZeroMean": f"{_decimal(endpoint_zero['mean_net_pnl']):.3f}",
        "PhaseAQcZeroLow": f"{_decimal(endpoint_zero['ci_low']):.3f}",
        "PhaseAQcZeroHigh": _signed(_decimal(endpoint_zero["ci_high"]), 3),
        "PhaseAQcOneMean": f"{_decimal(endpoint_one['mean_net_pnl']):.3f}",
        "PhaseAQcOneLow": f"{_decimal(endpoint_one['ci_low']):.3f}",
        "PhaseAQcOneHigh": f"{_decimal(endpoint_one['ci_high']):.3f}",
        "OFITStat": f"{Decimal(str(regression['t_stat'])):.2f}",
        "OFIRSquared": f"{Decimal(str(regression['r2'])):.4f}",
        "OFIEffect": _signed(Decimal(str(regression["predicted_drift_1std_bps"])), 3),
        "OFIConditionalQcZero": _signed(_decimal(ofi_qc0["gates"]["conditional_separation_bps"]), 3),
        "OFIConditionalQcOne": _signed(_decimal(ofi_qc1["gates"]["conditional_separation_bps"]), 3),
        "OFIMinCountQcZero": str(ofi_qc0["gates"]["conditional_bucket_count_min"]),
        "OFIMinCountQcOne": str(ofi_qc1["gates"]["conditional_bucket_count_min"]),
        "QueueMatchedQcZero": f"{_decimal(pooled_ten['0.0']['quantity_weighted_matched_net_pnl_per_btc']):.2f}",
        "QueueMatchedQcOne": f"{_decimal(pooled_ten['1.0']['quantity_weighted_matched_net_pnl_per_btc']):.2f}",
        "MatchedBreakEvenQcZero": f"{_decimal(fee_rows[('0.0', 'full_matched_lots')]['break_even_maker_fee_bps']):.3f}",
        "MatchedBreakEvenQcOne": f"{_decimal(fee_rows[('1.0', 'full_matched_lots')]['break_even_maker_fee_bps']):.3f}",
        "FullRebateQcZero": f"{_decimal(fee_rows[('0.0', 'full_strategy')]['required_rebate_bps']):.3f}",
        "FullRebateQcOne": f"{_decimal(fee_rows[('1.0', 'full_strategy')]['required_rebate_bps']):.3f}",
        "SnapshotReceiptMedianMs": f"{_decimal(receipt['median']):.1f}",
        "SnapshotReceiptPninetyMs": f"{_decimal(receipt['p90']):.1f}",
        "SnapshotReceiptMaxMs": f"{int(receipt['max']):,}",
        "SnapshotBridgeMedianMs": f"{_decimal(bridge['median']):.1f}",
        "SnapshotPreBridgeFillsQcZero": str(
            fill_proximity["0"]["fills_from_orders_placed_before_policy_boundary"]
        ),
        "SnapshotPreBridgeFillsQcOne": str(
            fill_proximity["1"]["fills_from_orders_placed_before_policy_boundary"]
        ),
        "DevelopmentWindows": str(panel["counts"]["development_selected"]),
        "HoldoutWindows": str(panel["counts"]["holdout_selected"]),
        "ManifestHours": str(integrity["counts"]["hours"]),
        "ManifestValidHours": str(integrity["counts"]["valid_hours"]),
        "ManifestInvalidHours": str(integrity["counts"]["invalid_hours"]),
        "ManifestHashShort": integrity["manifest_sha256"][:12],
        "PanelHashShort": panel["panel_sha256"][:12],
    }
    lines = ["% Generated by scripts/build_report_assets.py. Do not edit by hand."]
    lines.extend(f"\\newcommand{{\\{name}}}{{{value}}}" for name, value in macros.items())
    path = GENERATED_ROOT / "results.tex"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_manifest(outputs: list[Path]) -> Path:
    generator = Path(__file__).resolve()
    manifest = {
        "schema_version": 1,
        "description": "Inputs and outputs for the technical report",
        "generator": {
            "path": str(generator.relative_to(ROOT)),
            "sha256": _sha256(generator),
            "matplotlib_version": matplotlib.__version__,
            "numpy_version": np.__version__,
            "python_version": platform.python_version(),
            "source_date_epoch": 1785801600,
        },
        "inputs": {
            str(path.relative_to(ROOT)): _sha256(path)
            for path in INPUTS
        },
        "outputs": {
            str(path.relative_to(ROOT)): _sha256(path)
            for path in outputs
        },
    }
    path = GENERATED_ROOT / "asset_manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("report assets require Python 3.11")
    if matplotlib.__version__ != EXPECTED_MATPLOTLIB_VERSION:
        raise RuntimeError(
            "report assets require Matplotlib "
            f"{EXPECTED_MATPLOTLIB_VERSION}, found {matplotlib.__version__}"
        )
    if np.__version__ != EXPECTED_NUMPY_VERSION:
        raise RuntimeError(
            "report assets require NumPy "
            f"{EXPECTED_NUMPY_VERSION}, found {np.__version__}"
        )
    FIGURE_ROOT.mkdir(parents=True, exist_ok=True)
    GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
    _style()

    for path, expected in EXPECTED_INPUT_SHA256.items():
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(
                f"report input hash mismatch for {path.relative_to(ROOT)}: "
                f"expected {expected}, got {actual}"
            )

    marker = _load(EXECUTION_MARKER)
    if marker.get("execution_model_version") != "legacy_book_update_v1":
        raise ValueError("report economic inputs must remain frozen legacy artifacts")

    phase_a = _load(PHASE_A)
    panel = _load(PANEL_SELECTION)
    integrity = _load(INTEGRITY_MANIFEST)
    ofi_qc1 = _load(OFI_QC1)
    ofi_qc0 = _load(OFI_QC0)
    queue_summary = _load(QUEUE_STRESS)
    fee_summary = _load(FEE_BREAK_EVEN)
    snapshot_audit = _load(SNAPSHOT_TIMING_AUDIT)

    figure_outputs = [
        *build_phase_a_figure(phase_a),
        *build_ofi_figure(ofi_qc0, ofi_qc1),
        *build_queue_figure(queue_summary),
        *build_panel_figure(panel),
    ]
    outputs = [
        *figure_outputs,
        write_macros(
            phase_a,
            panel,
            integrity,
            ofi_qc0,
            ofi_qc1,
            queue_summary,
            fee_summary,
            snapshot_audit,
        ),
    ]
    manifest = write_manifest(outputs)
    print(
        f"Built {len(figure_outputs) // 2} PDF/PNG figure pairs and "
        f"{outputs[-1].relative_to(ROOT)}"
    )
    print(f"Wrote {manifest.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
