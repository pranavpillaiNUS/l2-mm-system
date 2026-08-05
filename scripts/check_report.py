"""Validate the compiled report and its generated evidence assets."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
from pathlib import Path

import matplotlib
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "report/l2_mm_research_report.pdf"
BUILD_LOG = ROOT / "report/build/main.log"
MANIFEST = ROOT / "report/generated/asset_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def command(*args: str) -> str:
    try:
        completed = subprocess.run(
            args,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"required command is not installed: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout).strip()
        raise RuntimeError(f"{' '.join(args)} failed: {detail}") from exc
    return completed.stdout


def validate_manifest() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise RuntimeError("unexpected report asset manifest schema")

    paths = {
        **manifest.get("inputs", {}),
        **manifest.get("outputs", {}),
    }
    if not paths:
        raise RuntimeError("report asset manifest is empty")
    for relative, expected in paths.items():
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"manifest path is missing: {relative}")
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"manifest hash mismatch for {relative}: expected {expected}, got {actual}"
            )

    generator = manifest.get("generator", {})
    generator_path = ROOT / generator.get("path", "")
    if not generator_path.is_file():
        raise RuntimeError("report generator is missing")
    if sha256(generator_path) != generator.get("sha256"):
        raise RuntimeError("report generator hash does not match the asset manifest")
    if generator.get("python_version") != platform.python_version():
        raise RuntimeError("Python version does not match the report asset manifest")
    if generator.get("matplotlib_version") != matplotlib.__version__:
        raise RuntimeError("Matplotlib version does not match the report asset manifest")
    if generator.get("numpy_version") != np.__version__:
        raise RuntimeError("NumPy version does not match the report asset manifest")


def validate_log() -> None:
    if not BUILD_LOG.is_file():
        raise RuntimeError("Tectonic build log is missing")
    log = BUILD_LOG.read_text(encoding="utf-8", errors="replace")
    forbidden = {
        r"Overfull \\hbox": "overfull box",
        r"Missing character": "missing glyph",
        r"Citation .* undefined": "undefined citation",
        r"There were undefined references": "undefined reference",
    }
    failures = [label for pattern, label in forbidden.items() if re.search(pattern, log)]
    if failures:
        raise RuntimeError(f"report log contains: {', '.join(failures)}")


def validate_pdf() -> None:
    if not REPORT.is_file() or REPORT.stat().st_size < 100_000:
        raise RuntimeError("compiled report is missing or unexpectedly small")

    info = {}
    for line in command("pdfinfo", str(REPORT)).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            info[key.strip()] = value.strip()
    if info.get("Title") != "Event-Driven L2 Replay":
        raise RuntimeError(f"unexpected PDF title: {info.get('Title')!r}")
    if info.get("Author") != "Pranav Pillai":
        raise RuntimeError(f"unexpected PDF author: {info.get('Author')!r}")
    if info.get("Encrypted") != "no":
        raise RuntimeError("report PDF must not be encrypted")
    if int(info.get("Pages", "0")) < 15:
        raise RuntimeError("report PDF has fewer than 15 pages")

    font_rows = command("pdffonts", str(REPORT)).splitlines()[2:]
    if not font_rows:
        raise RuntimeError("report PDF contains no inspectable fonts")
    for row in font_rows:
        columns = row.split()
        if len(columns) < 9:
            raise RuntimeError(f"could not parse pdffonts row: {row}")
        font_name = columns[0]
        font_type = " ".join(columns[1:-6])
        embedded = columns[-5]
        if "Type 3" in font_type:
            raise RuntimeError(f"Type 3 font found: {font_name}")
        if embedded.lower() != "yes":
            raise RuntimeError(f"font is not embedded: {font_name}")

    normalized_text = " ".join(command("pdftotext", str(REPORT), "-").split())
    required_phrases = (
        "Event-Driven L2 Replay",
        "legacy_book_update_v1",
        "event_driven_v2",
        "Economic results under the current model remain pending",
        "No candidate strategy was evaluated on the 12-window holdout",
        "not a calibrated observation clock",
        "snapshot_timing_panel24.json",
    )
    missing = [phrase for phrase in required_phrases if phrase not in normalized_text]
    if missing:
        raise RuntimeError(f"report text is missing required phrases: {missing}")

    if shutil.which("qpdf"):
        command("qpdf", "--check", str(REPORT))


def main() -> None:
    validate_manifest()
    validate_log()
    validate_pdf()
    print("Report validation passed")


if __name__ == "__main__":
    main()
