#!/usr/bin/env python3
"""Regression tests for the v1.2 binary-content guard.

Covers:
  - extractors.text_extractor.looks_like_binary() as a pure sniff function
    (NUL bytes, normal UTF-8, Latin-1/ANSI, UTF-8 emoji/accents).
  - Wiring into discovery.scan_folder()'s pre-filter loop: a null-heavy
    .txt file is skipped with reason "binary content" via the same
    skipped_files machinery as symlink/too-large, even when
    max_file_size_mb would otherwise let it through.
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import discovery
from extractors.text_extractor import looks_like_binary


class StubMonitor:
    def __init__(self, interval=2):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def _check_sniff_function():
    rows, failures = [], []

    with tempfile.TemporaryDirectory() as tmp:
        cases = [
            ("12MB NUL-filled file", b"\x00" * (1024 * 1024) + b"tail", True),
            ("normal UTF-8 ASCII text", ("Hello, this is a normal report about nothing.\n" * 50).encode("utf-8"), False),
            ("Latin-1/ANSI text with accents", "Café résumé naïve déjà vu, montréal québec\n".encode("latin-1") * 100, False),
            ("UTF-8 text with emoji and accents", ("Great news! 🎉🚀 café — naïve — 日本語も少し。\n" * 50).encode("utf-8"), False),
            ("empty file", b"", False),
        ]
        for label, payload, expected in cases:
            path = os.path.join(tmp, "sample.bin")
            with open(path, "wb") as fh:
                fh.write(payload)
            actual = looks_like_binary(path)
            ok = actual == expected
            rows.append((label, ok, f"got={actual} expected={expected}"))
            if not ok:
                failures.append((label, actual, expected))

        # Unreadable file (nonexistent) -> never raises, treated as "not binary".
        actual = looks_like_binary(os.path.join(tmp, "does_not_exist.txt"))
        ok = actual is False
        rows.append(("nonexistent file never raises, treated as not-binary", ok, f"got={actual}"))
        if not ok:
            failures.append(("nonexistent file", actual, False))

    return rows, failures


def _check_scan_folder_wiring():
    rows, failures = [], []

    with tempfile.TemporaryDirectory() as tmp:
        null_path = Path(tmp) / "large_null.txt"
        with open(null_path, "wb") as fh:
            fh.write(b"\x00" * (1024 * 1024))  # 1MB of NUL

        normal_path = Path(tmp) / "normal.txt"
        normal_path.write_text("This is an ordinary text file with no PII.\n" * 20, encoding="utf-8")

        with (
            patch.object(discovery, "SystemMonitor", StubMonitor),
        ):
            # max_file_size_mb generous enough that size alone would NOT
            # skip the 1MB null file -- proves the binary check is
            # independent of the size threshold.
            html_path = discovery.scan_folder(
                str(tmp), verify=False, run_ner=False, max_file_size_mb=100
            )

        import json
        json_path = os.path.splitext(str(html_path))[0] + ".json"
        with open(json_path, "r", encoding="utf-8") as fh:
            report = json.load(fh)

        skipped_details = report.get("skipped_file_details", [])
        binary_entries = [s for s in skipped_details if s.get("reason") == "binary content"]
        rows.append((
            "null-heavy file skipped with reason 'binary content' despite generous max_file_size_mb",
            len(binary_entries) == 1 and os.path.basename(binary_entries[0]["file"]) == "large_null.txt",
            f"skipped_details={skipped_details}",
        ))

        scanned_names = {os.path.basename(f["file"]) for f in report.get("files", []) if f.get("scan_status") == "scanned"}
        rows.append((
            "normal text file still scanned",
            "normal.txt" in scanned_names,
            f"scanned_names={scanned_names}",
        ))
        rows.append((
            "null-heavy file never appears in scanned files",
            "large_null.txt" not in scanned_names,
            f"scanned_names={scanned_names}",
        ))
        rows.append((
            "JSON summary skipped count includes the binary-content skip",
            report["summary"]["skipped"] == 1,
            f"skipped={report['summary']['skipped']}",
        ))

        # Markdown/HTML rendering surface the reason (same-as-symlink/too-large mechanism).
        from report_generator import generate_markdown
        from report_html import generate_html

        md_path = Path(tmp) / "r.md"
        html_out_path = Path(tmp) / "r.html"
        results = [
            {
                "file": str(normal_path),
                "scan_status": "scanned",
                "failure_reason": None,
                "matches": {},
                "score": 0,
                "metadata": {},
            }
        ]
        skipped = [{"file": str(null_path), "reason": "binary content", "scan_status": "skipped"}]
        md = generate_markdown(results, str(md_path), skipped_files=skipped)
        html = generate_html(results, str(html_out_path), skipped_files=skipped)
        rows.append((
            "Markdown skipped section shows 'binary content' reason",
            "large_null.txt — binary content" in md,
            "present" if "large_null.txt — binary content" in md else "absent",
        ))
        rows.append((
            "HTML skipped section shows 'binary content' reason",
            "binary content" in html and "large_null.txt" in html,
            "present" if "binary content" in html else "absent",
        ))

    for label, ok, detail in rows:
        if not ok:
            failures.append((label, detail, "expected pass"))
    return rows, failures


def main():
    rows, failures = [], []
    for fn in (_check_sniff_function, _check_scan_folder_wiring):
        r, f = fn()
        rows.extend(r)
        failures.extend(f)

    print(f"{'CASE':<70} {'RESULT':<7} DETAIL")
    print("-" * 110)
    for label, ok, detail in rows:
        print(f"{label:<70} {'PASS' if ok else 'FAIL':<7} {detail}")
    passed_count = sum(1 for _, ok, _ in rows if ok)
    print("-" * 110)
    print(f"SUMMARY: {passed_count}/{len(rows)} passed")
    return 0 if passed_count == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
