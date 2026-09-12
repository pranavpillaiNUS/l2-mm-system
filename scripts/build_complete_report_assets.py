"""Build the full V3 report from verified committed research and native evidence.

No raw captures, native binary, or new strategy run are needed. Research values
are reconstructed from the complete artifact tree and compared with the
published decision summary before rendering. Native figures use the two frozen
book-update measurements and do not describe full replay throughput.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import platform
import statistics
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ft2font
import numpy as np

from scripts.summarize_v3_development import build_summary
from scripts.verify_v3_artifacts import verify


ROOT = Path(__file__).resolve().parents[1]
PANEL = Path("results/panels/btcusdt_l2_panel_v3_event_driven")
SUMMARY = Path("results/panels/btcusdt_l2_panel_v3_development_summary/summary.json")
PROTOCOL = Path("notebooks/v3_development_protocol.md")
SYNTHETIC = Path("results/cpp_orderbook/synthetic_20000.json")
RECORDED = Path("results/cpp_orderbook/development_hour.json")
NATIVE_PARITY = Path("results/native_pipeline/development_parity.json")
NATIVE_PIPELINE_BENCHMARK = Path("results/native_pipeline/development_hour_benchmark.json")
FIGURES = Path("report/v3/figures")
GENERATED = Path("report/v3/generated")
BUILD_DATE = datetime(2026, 9, 12, tzinfo=timezone.utc)
PINNED_INPUTS = {
    PROTOCOL: "e1890d2e4fa715154f28778febe3367d391a8c8be1b44b766b4ccc0decf3e395",
    SYNTHETIC: "37503344c3596b4fc07aeff51829798824e3048b48645a363e0269c5f122ca51",
    RECORDED: "279aa677a0a6e378de7dd0c20e6615bd9ec31d11f2da154ac16d239a48e31ecd",
}
BUCKET_ORDER = (
    "<-1.0", "[-1.0,-0.25)", "[-0.25,0.0)",
    "[0.0,0.25)", "[0.25,1.0)", ">=1.0",
)
BLUE = "#0072B2"
GREEN = "#009E73"
ORANGE = "#D55E00"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_summary() -> tuple[dict, dict]:
    manifest = verify(PANEL, PANEL / "status", source_revision="recorded", artifacts_only=True)
    published = load(SUMMARY)
    recomputed = build_summary(
        PANEL, PANEL / "status", source_revision="recorded", artifacts_only=True,
    )
    # The original publication predates explicit raw-verification metadata.
    # Only this verification annotation may differ; all decision fields,
    # diagnostics, artifact/source identities, and protocol bytes must match.
    comparable = copy.deepcopy(published)
    rebuilt = copy.deepcopy(recomputed)
    for payload in (comparable, rebuilt):
        payload["verified_artifact_manifest"].pop("raw_verification", None)
    if comparable != rebuilt:
        raise ValueError("published V3 summary differs from its verified reconstruction")
    if published["protocol"]["sha256"] != sha256(PROTOCOL):
        raise ValueError("published summary does not identify the locked protocol")
    if set(published["endpoints"]) != {"0", "1"}:
        raise ValueError("V3 figures require both queue endpoints")
    return published, manifest


def validate_benchmark(path: Path, manifest: dict) -> dict:
    payload = load(path)
    if (
        payload.get("schema_version") != "orderbook_benchmark_v1"
        or payload.get("metric") != "preloaded_book_updates_including_numeric_conversion"
        or payload["parity"]["status"] != "passed"
        or payload["parity"]["states"] != payload["operations"] + 1
    ):
        raise ValueError(f"invalid parity-gated book measurement: {path}")
    timing = payload["benchmark"]
    if timing["repeat"] != 5 or timing["warmup"] != 1:
        raise ValueError("native report expects five measured repeats and one warmup")
    for backend in ("python", "cpp"):
        values = timing[backend]["elapsed_ns"]
        if len(values) != timing["repeat"] or any(type(n) is not int or n <= 0 for n in values):
            raise ValueError("invalid measured durations")
        if timing[backend]["median_ns"] != statistics.median(values):
            raise ValueError("stored timing median differs from measured repeats")
    ratio = timing["python"]["median_ns"] / timing["cpp"]["median_ns"]
    if not math.isclose(ratio, timing["median_speedup"], rel_tol=1e-12):
        raise ValueError("stored native speed ratio does not match the measured medians")
    if timing["cpp_metadata"]["final_state_hash"] != payload["parity"]["final_state_hash"]:
        raise ValueError("timed native final state differs from the parity run")
    if path == RECORDED:
        if (
            payload["input"]["kind"] != "recorded_development_depth"
            or payload["input"]["depth_only"] is not True
            or payload["input"]["development_panel_sha256"] != manifest["development_panel"]["sha256"]
            or payload["input"]["sha256"] not in {row["depth_sha256"] for row in manifest["raw_inputs"]}
        ):
            raise ValueError("recorded book measurement is not a frozen development input")
    elif payload["input"]["kind"] != "synthetic" or payload["input"]["seed"] != 42:
        raise ValueError("unexpected synthetic benchmark input")
    return payload


def style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 9,
        "axes.titlesize": 10, "axes.labelsize": 9,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.alpha": 0.2, "grid.linewidth": 0.6,
        "legend.frameon": False, "figure.dpi": 150,
        "savefig.bbox": "tight", "pdf.fonttype": 42, "ps.fonttype": 42,
        "axes.unicode_minus": False,
    })


def save(fig: plt.Figure, name: str) -> list[Path]:
    pdf = FIGURES / f"{name}.pdf"
    png = FIGURES / f"{name}.png"
    fig.savefig(pdf, metadata={
        "Title": name.replace("_", " ").title(), "Author": "Pranav Pillai",
        "Creator": "l2-mm-system complete report asset builder",
        "CreationDate": BUILD_DATE, "ModDate": BUILD_DATE,
    })
    fig.savefig(png, dpi=180, metadata={"Software": "l2-mm-system complete report asset builder"})
    plt.close(fig)
    return [pdf, png]


def baseline_intervals(summary: dict) -> list[Path]:
    fig, ax = plt.subplots(figsize=(7.0, 2.7), layout="constrained")
    for y, credit, color in ((1, "0", BLUE), (0, "1", ORANGE)):
        row = summary["endpoints"][credit]["baseline_diagnostics"]["ci"]["window"]["net_pnl"]
        if row["n"] != 24 or row["unit"] != "5h_window" or row["confidence"] != "0.95":
            raise ValueError("baseline figure requires 24-window 95% intervals")
        mean, low, high = (float(row[key]) for key in ("mean", "ci_low", "ci_high"))
        ax.errorbar(mean, y, xerr=[[mean - low], [high - mean]], fmt="o",
                    color=color, markersize=6, capsize=5, linewidth=2)
        ax.text(mean, y + 0.16, f"{mean:+.3f}  [{low:+.3f}, {high:+.3f}]",
                ha="center", color=color, fontsize=9)
    ax.axvline(0, color="#444444", linestyle="--", linewidth=0.9)
    ax.set_yticks([0, 1], ["Proportional credit (qc=1)", "No credit (qc=0)"])
    ax.set_ylim(-0.45, 1.55)
    ax.set_xlim(-2.4, 0.4)
    ax.set_xlabel("Mean net P&L (USDT per five-hour window)")
    ax.set_title("V3 baseline: means and 95% window-bootstrap intervals")
    ax.grid(axis="y", visible=False)
    return save(fig, "v3_baseline_intervals")


def ofi_buckets(summary: dict) -> list[Path]:
    fig, ax = plt.subplots(figsize=(7.2, 3.8), layout="constrained")
    x = np.arange(len(BUCKET_ORDER))
    counts = []
    for credit, color, marker, label in (
        ("0", BLUE, "o", "No credit (qc=0)"),
        ("1", ORANGE, "s", "Proportional credit (qc=1)"),
    ):
        rows = [row for row in summary["endpoints"][credit]["ofi_diagnostics"]["fill_buckets"]
                if row["horizon"] == "30s" and row["side"] == "all"]
        by_label = {row["bucket"]: row for row in rows}
        if len(rows) != 6 or set(by_label) != set(BUCKET_ORDER):
            raise ValueError("OFI figure requires every pooled-side 30-second bucket")
        values = [float(by_label[label]["avg_mid_move_bps"]) for label in BUCKET_ORDER]
        counts.append([by_label[label]["n"] for label in BUCKET_ORDER])
        ax.plot(x, values, marker=marker, color=color, label=label, linewidth=1.5, markersize=5)
    for selected in (0, 3):
        ax.axvspan(selected - 0.35, selected + 0.35, color="#D9E7F0", alpha=0.6, zorder=0)
    ax.axhline(0, color="#444444", linewidth=0.8, linestyle="--")
    labels = [f"{label}\nn0={counts[0][i]}\nn1={counts[1][i]}"
              for i, label in enumerate(BUCKET_ORDER)]
    ax.set_xticks(x, labels, fontsize=8)
    ax.set_xlim(-0.55, 5.55)
    ax.set_ylim(-2.5, 0.65)
    ax.set_xlabel("Side-aligned OFI bucket; qualifying fill counts at each endpoint")
    ax.set_ylabel("Mean side-normalized\nforward mid move (bps)")
    ax.set_title("30-second passive-fill response across all six OFI buckets")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(axis="x", visible=False)
    return save(fig, "v3_ofi_buckets")


def native_benchmark(synthetic: dict, recorded: dict) -> list[Path]:
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 3.2), layout="constrained")
    for ax, payload, title in zip(axes, (synthetic, recorded), (
        "Synthetic: 20,000 operations", "Recorded hour: 35,999 operations",
    )):
        timing = payload["benchmark"]
        values = [timing[backend]["median_ns"] / 1_000_000 for backend in ("python", "cpp")]
        bars = ax.bar([0, 1], values, width=0.55, color=[BLUE, GREEN], zorder=3)
        ax.bar_label(bars, labels=[f"{value:.3f} ms" for value in values], padding=4, fontsize=9)
        ax.set_xticks([0, 1], ["Python", "C++17"])
        ax.set_ylim(0, max(values) * 1.24)
        ax.set_ylabel("Median measured duration (ms)")
        ax.set_title(f"{title}\nRatio of medians: {timing['median_speedup']:.5f}x")
        ax.grid(axis="x", visible=False)
    return save(fig, "native_book_benchmark")


def write_macros(summary: dict, synthetic: dict, recorded: dict) -> Path:
    endpoints = summary["endpoints"]
    primary = [row for row in endpoints["0"]["ofi_diagnostics"]["pooled_regressions"]
               if row["horizon"] == "1s" and row["signal"] == "normalized_ofi"]
    if len(primary) != 1:
        raise ValueError("expected one primary normalized OFI population row")
    row = primary[0]
    macros = {
        "VThreeOFIEffect": f"{row['predicted_drift_1std_bps']:.6f}",
        "VThreeOFITStat": f"{row['t_stat']:.4f}",
        "VThreeOFIRSquared": f"{row['r2']:.7f}",
        "VThreePopulationSamples": f"{row['n']:,}",
        "NativeRecordedStates": f"{recorded['parity']['states']:,}",
        "NativeRecordedSpeedup": f"{recorded['benchmark']['median_speedup']:.5f}",
        "NativeSyntheticSpeedup": f"{synthetic['benchmark']['median_speedup']:.5f}",
    }
    for credit, suffix in (("0", "Zero"), ("1", "One")):
        ci = endpoints[credit]["baseline_diagnostics"]["ci"]["window"]["net_pnl"]
        macros[f"VThreeMean{suffix}"] = f"{Decimal(ci['mean']):.3f}"
        macros[f"VThreeCILow{suffix}"] = f"{Decimal(ci['ci_low']):+.3f}"
        macros[f"VThreeCIHigh{suffix}"] = f"{Decimal(ci['ci_high']):+.3f}"
        contrast = endpoints[credit]["primary_gate"]["conditional_separation_bps"]
        macros[f"VThreeContrast{suffix}"] = f"{Decimal(contrast):.3f}"
    output = GENERATED / "results.tex"
    lines = ["% Generated by scripts/build_complete_report_assets.py; do not edit."]
    lines.extend(f"\\newcommand{{\\{name}}}{{{value}}}" for name, value in macros.items())
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def validate_native_pipeline(path: Path, research_manifest: dict, *, complete: bool) -> dict:
    payload = load(path)
    frozen_rows = {row["start"]: row for row in research_manifest["raw_inputs"]}
    expected_starts = set(frozen_rows) if complete else {research_manifest["raw_inputs"][0]["start"]}
    if (
        payload.get("schema_version") != "native_pipeline_validation_v1"
        or payload.get("status") != "passed"
        or payload.get("reference_manifest_sha256") != sha256(PANEL / "ARTIFACT_MANIFEST.json")
        or payload.get("holdout_status") != "strategy_sealed"
        or payload.get("input_hours") != len(expected_starts)
        or payload.get("endpoint_runs") != 2 * len(expected_starts)
    ):
        raise ValueError(f"native pipeline artifact has an incomplete or incompatible identity: {path}")
    raw_rows = payload["raw_inputs"]
    if len(raw_rows) != len(expected_starts) or {row["start"] for row in raw_rows} != expected_starts:
        raise ValueError("native pipeline input hours differ from the frozen development set")
    for row in raw_rows:
        if any(row[f"{kind}_sha256"] != frozen_rows[row["start"]][f"{kind}_sha256"]
               for kind in ("depth", "trade")):
            raise ValueError("native pipeline input hashes differ from the frozen experiment")
    expected_runs = {(start, credit) for start in expected_starts for credit in ("0", "1")}
    results = payload["results"]
    if len(results) != len(expected_runs) or {(row["start"], row["queue_credit"]) for row in results} != expected_runs:
        raise ValueError("native pipeline does not cover every selected hour at both endpoints")
    streams = {
        "checkpoints", "order_events", "fills", "book_samples", "markouts",
        "queue_diagnostics", "ofi_population_samples", "ofi_conditional_samples",
        "hold_time", "pnl", "stats", "final_strategy", "final_book",
    }
    for row in results:
        if row["status"] != "passed" or set(row["component_sha256"]) != streams or set(row["counts"]) != streams:
            raise ValueError("native pipeline result omits required complete-stream parity")
        if any(len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value)
               for value in row["component_sha256"].values()):
            raise ValueError("native pipeline contains an invalid component digest")
    by_run = {(row["start"], row["queue_credit"]): row for row in results}
    checked = set()
    for relative in research_manifest["artifacts"]:
        if not relative.startswith("markout_reconciliation/") or not relative.endswith("/summary.json"):
            continue
        reference = load(PANEL / relative)
        credit = reference["execution_provenance"]["queue_cancellation_credit"]
        for session in reference["sessions"]:
            key = (datetime.fromisoformat(session["window"]).isoformat(), credit)
            if key not in by_run:
                continue
            row = by_run[key]
            if (row["stats"] != session["replay_stats"]
                    or Decimal(row["net_pnl"]) != Decimal(session["pnl"]["net_pnl"])
                    or Decimal(row["fees"]) != Decimal(session["pnl"]["fees"])
                    or Decimal(row["position"]) != Decimal(session["strategy_position"])):
                raise ValueError(f"native pipeline outcome differs from frozen V3: {key}")
            checked.add(key)
    if checked != expected_runs:
        raise ValueError("native pipeline outcomes lack frozen V3 comparison coverage")
    return payload


def write_native_pipeline_results(research_manifest: dict) -> tuple[Path, list[Path]]:
    output = GENERATED / "native_pipeline_results.tex"
    if not NATIVE_PARITY.is_file() or not NATIVE_PIPELINE_BENCHMARK.is_file():
        raise ValueError("complete report requires full native parity and integrated measurements")
    parity = validate_native_pipeline(NATIVE_PARITY, research_manifest, complete=True)
    benchmark = validate_native_pipeline(NATIVE_PIPELINE_BENCHMARK, research_manifest, complete=False)
    measurements = benchmark["benchmarks"]
    if len(measurements) != 2 or {row["queue_credit"] for row in measurements} != {"0", "1"}:
        raise ValueError("integrated timing requires both queue endpoints")
    lines = [
        "% Generated from verified native pipeline artifacts; do not edit.",
        f"Native storage matched Python across all {parity['input_hours']} selected "
        f"development hours and {parity['endpoint_runs']} hour/endpoint runs. "
        "The comparison hashes every order event, fill, book sample, markout, "
        "queue diagnostic, OFI population and conditional sample, and final "
        "accounting state. It also checks hold-time and P\\&L summaries, replay "
        "statistics, and book checkpoints every 1,000 market events. "
        "All hourly replay statistics, net P\\&L, fees, and final positions also "
        "match the frozen V3 reconciliation artifacts. No holdout strategy "
        "outcomes were evaluated.",
        "",
        "\\begin{table}[H]",
        "\\centering\\small",
        "\\begin{tabular}{lrrr}",
        "\\toprule",
        "Queue endpoint & Python median (s) & Native median (s) & Ratio \\\\",
        "\\midrule",
    ]
    for row in sorted(measurements, key=lambda item: item["queue_credit"]):
        if row["parity"] != "passed" or row["repeat"] != 3 or row["warmup"] != 1:
            raise ValueError("integrated timing requires parity and three repeats after one warmup")
        medians = {}
        for backend in ("python", "cpp"):
            values = row["elapsed_ns"][backend]
            if len(values) != 3 or any(type(value) is not int or value <= 0 for value in values):
                raise ValueError("invalid integrated measured durations")
            medians[backend] = statistics.median(values)
            if medians[backend] != row["median_ns"][backend]:
                raise ValueError("integrated timing median does not match its repeats")
        ratio = medians["python"] / medians["cpp"]
        if not math.isclose(ratio, row["median_speedup"], rel_tol=1e-12):
            raise ValueError("integrated speed ratio differs from measured medians")
        label = "No credit" if row["queue_credit"] == "0" else "Proportional credit"
        lines.append(f"{label} & {medians['python']/1e9:.3f} & "
                     f"{medians['cpp']/1e9:.3f} & {ratio:.3f}\\,$\\times$ \\\\")
    lines.extend([
        "\\bottomrule", "\\end{tabular}",
        "\\caption{Integrated pipeline measurements for 12 April 2026, 09:00 UTC. "
        "One warmup and three measured repetitions per backend, with alternating "
        "backend order and a warm operating-system page cache. Ratios compare "
        "the measured medians and need not indicate an overall speedup.}",
        "\\label{tab:native-pipeline-timing}", "\\end{table}",
        "",
        "These timings include raw gzip reads and JSON parsing, object construction, "
        "replay, strategy callbacks, execution, checkpoint hashing, book samples, "
        "markouts, P\\&L, hold-time and queue diagnostics, and OFI sample construction. "
        "They exclude imports and initial native discovery, parity serialization, "
        "object destruction, output writing, pooled regressions, bootstrap inference, "
        "and report generation. Every measured repetition is checked against the "
        "Python output streams. This is a separately scoped measurement from the "
        "preloaded book-update benchmark.",
    ])
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output, [NATIVE_PARITY, NATIVE_PIPELINE_BENCHMARK]


def write_manifest(outputs: list[Path], research_manifest: dict, extra_inputs: list[Path]) -> Path:
    inputs = {
        SUMMARY, PROTOCOL, SYNTHETIC, RECORDED, PANEL / "ARTIFACT_MANIFEST.json",
        Path(research_manifest["development_panel"]["path"]),
        Path(research_manifest["raw_integrity_manifest"]["path"]),
        Path("scripts/verify_v3_artifacts.py"),
        Path("scripts/summarize_v3_development.py"),
        Path("scripts/compare_execution_model_baseline.py"),
    }
    inputs.update(PANEL / relative for relative in research_manifest["artifacts"])
    inputs.update(extra_inputs)
    legacy = Path("results/panels/btcusdt_l2_panel_v2")
    inputs.update(legacy / relative for relative in research_manifest["artifacts"]
                  if Path(relative).parts[0] == "baseline_ci")
    generator = Path(__file__).resolve().relative_to(ROOT)
    payload = {
        "schema_version": 1,
        "description": "Verified V3 development, native book, and integrated pipeline report assets",
        "generator": {
            "path": str(generator), "sha256": sha256(generator),
            "python_version": platform.python_version(),
            "matplotlib_version": matplotlib.__version__, "numpy_version": np.__version__,
            "freetype_version": matplotlib.ft2font.__freetype_version__,
            "source_date_epoch": int(BUILD_DATE.timestamp()),
        },
        "research_verification": {
            "source_verification": research_manifest["source_verification"],
            "raw_verification": research_manifest["raw_verification"],
            "decision_summary": "recomputed_and_equal_except_raw_verification_annotation",
        },
        "inputs": {str(path): sha256(path) for path in sorted(inputs)},
        "outputs": {str(path): sha256(path) for path in sorted(outputs)},
    }
    output = GENERATED / "asset_manifest.json"
    output.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return output


def main() -> None:
    os.chdir(ROOT)
    if platform.python_version() != "3.11.14":
        raise RuntimeError("complete report assets require Python 3.11.14")
    if matplotlib.__version__ != "3.10.8" or np.__version__ != "2.4.1":
        raise RuntimeError("complete report assets require Matplotlib 3.10.8 and NumPy 2.4.1")
    if matplotlib.ft2font.__freetype_version__ != "2.6.1":
        raise RuntimeError(
            "complete report PNGs require the pinned PyPI Matplotlib wheel's "
            "FreeType 2.6.1; install requirements.txt in an isolated venv"
        )
    for path, expected in PINNED_INPUTS.items():
        if sha256(path) != expected:
            raise ValueError(f"frozen report input hash differs: {path}")
    summary, manifest = validate_summary()
    synthetic, recorded = (validate_benchmark(path, manifest) for path in (SYNTHETIC, RECORDED))
    FIGURES.mkdir(parents=True, exist_ok=True)
    GENERATED.mkdir(parents=True, exist_ok=True)
    style()
    native_results, native_inputs = write_native_pipeline_results(manifest)
    outputs = [
        *baseline_intervals(summary), *ofi_buckets(summary),
        *native_benchmark(synthetic, recorded), write_macros(summary, synthetic, recorded),
        native_results,
    ]
    asset_manifest = write_manifest(outputs, manifest, native_inputs)
    print("Built three deterministic PDF/PNG figure pairs and V3 LaTeX scalars")
    print("Research evidence and source verified; raw captures were not read")
    print(f"Wrote {asset_manifest}")


if __name__ == "__main__":
    main()
