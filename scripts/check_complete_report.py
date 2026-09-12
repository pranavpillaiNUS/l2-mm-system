"""Check the complete report's evidence hashes, LaTeX log, and rendered PDF."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "report/l2_mm_system_complete_report.pdf"
MANIFEST = ROOT / "report/v3/generated/asset_manifest.json"
BUILD_LOG = ROOT / "report/v3/build/main.log"


def command(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True, stderr=subprocess.STDOUT)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_manifest():
    payload = json.loads(MANIFEST.read_text())
    for group in ("inputs", "outputs"):
        if not payload.get(group):
            raise ValueError(f"complete report has no {group} identities")
        for relative, expected in payload[group].items():
            path = ROOT / relative
            if not path.is_file() or sha256(path) != expected:
                raise ValueError(f"complete report {group} hash differs: {relative}")
    generator = payload["generator"]
    if sha256(ROOT / generator["path"]) != generator["sha256"]:
        raise ValueError("complete report generator source differs from its manifest")
    verification = payload["research_verification"]
    if verification["raw_verification"]["mode"] != "inventory_identities_only":
        raise ValueError("report evidence must disclose artifact-only verification")
    if verification["source_verification"]["revision"] != "04df6294872a256374f910d12d5e5e3e08e8757a":
        raise ValueError("report does not identify the frozen research source")


def validate_log():
    log = BUILD_LOG.read_text(errors="replace")
    patterns = (r"Overfull \\hbox", r"Missing character", r"Citation .* undefined",
                r"There were undefined references", r"LaTeX Error")
    failures = [pattern for pattern in patterns if re.search(pattern, log)]
    if failures:
        raise ValueError(f"complete report has unresolved LaTeX diagnostics: {failures}")


def validate_pdf():
    if not REPORT.is_file() or REPORT.stat().st_size < 100_000:
        raise ValueError("complete report PDF is missing or unexpectedly small")
    info = {}
    for line in command("pdfinfo", str(REPORT)).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            info[key.strip()] = value.strip()
    if info.get("Title") != "L2 Market Microstructure System: Complete Project Report":
        raise ValueError("complete report PDF has the wrong title")
    if info.get("Author") != "Pranav Pillai" or info.get("Encrypted") != "no":
        raise ValueError("complete report PDF has invalid author or encryption metadata")
    if int(info.get("Pages", 0)) < 15:
        raise ValueError("complete report PDF has fewer than 15 pages")
    font_rows = command("pdffonts", str(REPORT)).splitlines()[2:]
    if not font_rows:
        raise ValueError("complete report fonts cannot be inspected")
    for row in font_rows:
        columns = row.split()
        if len(columns) < 9 or columns[-5] != "yes" or "Type 3" in row:
            raise ValueError(f"complete report contains unembedded or bitmap font: {row}")
    text = " ".join(command("pdftotext", str(REPORT), "-").split())
    for phrase in ("event_driven_v2", "legacy_book_update_v1", "strategy-sealed",
                   "C++", "Decimal", "120", "240", "0.123285"):
        if phrase not in text:
            raise ValueError(f"complete report is missing required content: {phrase}")
    for phrase in ("Integration measurements are pending", "Integrated native validation is in progress",
                   "TODO", "PLACEHOLDER"):
        if phrase in text:
            raise ValueError(f"complete report contains unfinished content: {phrase}")
    if shutil.which("qpdf"):
        command("qpdf", "--check", str(REPORT))
    print(f"Complete report validated: {info['Pages']} pages; all fonts embedded")


def main():
    validate_manifest()
    validate_log()
    validate_pdf()


if __name__ == "__main__":
    main()
