#!/usr/bin/env python3
"""Regression coverage for audit findings #1/#6 extraction status."""

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery import scan_file
from report_generator import generate_markdown
from report_html import generate_html
from report_json import generate_json


def _simulate_masked_export(doc):
    out = re.sub(r"<div id='pii-toolbar'>.*?</div>", "", doc, flags=re.DOTALL)
    return re.sub(
        r'<span class="val pii-value" data-masked="([^"]*)">[^<]*</span>',
        lambda match: f'<span class="val pii-value">{match.group(1)}</span>',
        out,
    )


def main():
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fixtures = {
            "broken.docx": b"PK\x03\x04truncated-docx",
            "broken.pdf": b"not a pdf",
            "broken.xlsx": b"not an xlsx zip",
            "broken.jpg": b"not a jpeg",
        }
        for name, payload in fixtures.items():
            (root / name).write_bytes(payload)

        unreadable = root / "unreadable.txt"
        unreadable.write_text("read should fail", encoding="utf-8")
        blank = root / "blank.txt"
        blank.write_text("", encoding="utf-8")

        failed_results = [
            scan_file(str(root / name), verify=False)
            for name in fixtures
        ]
        # chmod(0) is still readable to the root process used by the Docker
        # test runner. Raise the same OS-level failure at the text-extractor
        # boundary so this contract is deterministic for root and non-root.
        with patch(
            "discovery.extract_text",
            side_effect=PermissionError(13, "Permission denied", str(unreadable)),
        ):
            failed_results.append(scan_file(str(unreadable), verify=False))
        blank_result = scan_file(str(blank), verify=False)

        for result in failed_results:
            ok = (
                result.get("scan_status") == "extraction_failed"
                and bool(result.get("failure_reason"))
                and result.get("score") is None
                and result.get("matches") == {}
            )
            rows.append(
                (
                    f"{Path(result['file']).suffix or '.txt'} failure contract",
                    ok,
                    result.get("scan_status"),
                )
            )

        rows.append(
            (
                "blank readable text remains scanned",
                blank_result.get("scan_status") == "scanned"
                and blank_result.get("score") == 0,
                f"status={blank_result.get('scan_status')} score={blank_result.get('score')}",
            )
        )

        results = failed_results + [blank_result]
        skipped = [
            {
                "file": str(root / "linked.txt"),
                "scan_status": "skipped",
                "reason": "symlink",
            }
        ]
        md = generate_markdown(results, str(root / "report.md"), skipped_files=skipped)
        html = generate_html(results, str(root / "report.html"), skipped_files=skipped)
        json_report = generate_json(
            results, str(root / "report.json"), skipped_files=skipped
        )

        failed_names = {Path(result["file"]).name for result in failed_results}
        rows.append(
            (
                "Markdown failed section and summary",
                "## ⚠ Could Not Be Scanned" in md
                and "**Summary:** 1 scanned, 5 failed, 1 skipped" in md
                and all(name in md for name in failed_names),
                "present",
            )
        )
        rows.append(
            (
                "HTML failed section, UNKNOWN risk, and summary",
                "⚠ Could Not Be Scanned" in html
                and "1 scanned, 5 failed, 1 skipped" in html
                and "badge unknown" in html
                and all(name in html for name in failed_names),
                "present",
            )
        )
        rows.append(
            (
                "JSON failed section, statuses, and summary",
                json_report["summary"]["scanned"] == 1
                and json_report["summary"]["failed"] == 5
                and json_report["summary"]["skipped"] == 1
                and len(json_report["failed_files"]) == 5
                and all(
                    "scan_status" in item and "failure_reason" in item
                    for item in json_report["files"]
                ),
                json.dumps(json_report["summary"], sort_keys=True),
            )
        )

        exported = _simulate_masked_export(html)
        rows.append(
            (
                "masked HTML export removes raw failure reasons",
                all(
                    result["failure_reason"] not in exported
                    for result in failed_results
                ),
                "raw reasons absent",
            )
        )

    print(f"{'CASE':<54} {'RESULT':<7} DETAIL")
    print("-" * 90)
    for name, passed, detail in rows:
        print(f"{name:<54} {'PASS' if passed else 'FAIL':<7} {detail}")
    passed_count = sum(passed for _, passed, _ in rows)
    print("-" * 90)
    print(f"SUMMARY: {passed_count}/{len(rows)} passed")
    return 0 if passed_count == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
