#!/usr/bin/env python3
"""Regression tests for scan-boundary audit findings."""

import contextlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import discovery
import report_json


class StubMonitor:
    def __init__(self, interval=2):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def _resolved(path):
    return os.path.realpath(os.path.abspath(path))


def _output_fixture():
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    output_dir = root / "outputs"
    output_dir.mkdir()
    for name in ("report.md", "report.json", "report.html"):
        (output_dir / name).write_text("generated report", encoding="utf-8")
    (root / "outside.md").write_text("ordinary markdown", encoding="utf-8")
    (root / "outside.json").write_text('{"ordinary": true}', encoding="utf-8")
    (root / "outside.html").write_text("<p>ordinary html</p>", encoding="utf-8")
    return temp, root, output_dir


def _fake_scan_file(path, **_kwargs):
    return {
        "file": os.path.abspath(path),
        "scan_status": "scanned",
        "failure_reason": None,
        "matches": {},
        "score": 0,
        "metadata": {},
        "mismatch_alarm": None,
    }


def _extension_filter_cases(root):
    reports = root / "reports"
    for name in ("only.pdf", "notes.txt", "photo.jpg", "slides.pptx", "ignored.xyz"):
        (root / name).write_bytes(b"fixture")

    fixed_timestamp = "2026-07-24_12-00-00"

    def run_scan(pass_extensions, include_kwarg=True):
        output = io.StringIO()
        with (
            patch.object(discovery, "SystemMonitor", StubMonitor),
            patch.object(discovery, "scan_file", side_effect=_fake_scan_file),
            patch.object(
                discovery.llm_verifier,
                "check_availability",
                return_value=(False, "disabled"),
            ),
            patch.object(discovery, "EXCLUDED_REPORT_OUTPUT_DIR", str(reports)),
            patch.object(
                discovery,
                "EXCLUDED_SYSTEM_MONITOR_LOG",
                str(reports / "system_monitor.log"),
            ),
            patch.object(discovery, "_report_timestamp", return_value=fixed_timestamp),
            patch.object(report_json, "datetime") as json_datetime,
            contextlib.redirect_stdout(output),
        ):
            json_datetime.now.return_value = datetime(2026, 7, 24, 12, 0, 0)
            kwargs = {
                "verify": False,
                "run_ner": False,
                "max_workers": 1,
            }
            if include_kwarg:
                kwargs["extensions"] = pass_extensions
            html_path = discovery.scan_folder(str(root), **kwargs)
        json_path = Path(os.path.splitext(str(html_path))[0] + ".json")
        return html_path, json_path.read_bytes(), json.loads(json_path.read_text()), output.getvalue()

    _omitted_path, omitted_bytes, omitted_report, _ = run_scan(None, include_kwarg=False)
    _none_path, none_bytes, none_report, _ = run_scan(None)
    filtered_path, _filtered_bytes, filtered_report, banner = run_scan(
        {".pdf", ".not-supported"}
    )
    md_text = Path(os.path.splitext(str(filtered_path))[0] + ".md").read_text(
        encoding="utf-8"
    )
    html_text = Path(str(filtered_path)).read_text(encoding="utf-8")
    latest_text = (reports / "latest.html").read_text(encoding="utf-8")

    scanned = {
        os.path.basename(item["file"])
        for item in filtered_report["files"]
        if item["scan_status"] == "scanned"
    }
    filtered_details = [
        item
        for item in filtered_report["skipped_file_details"]
        if item.get("reason") == "filtered"
    ]
    return [
        (
            "extension filter scans only selected PDFs",
            scanned == {"only.pdf"},
            f"scanned={sorted(scanned)}",
        ),
        (
            "filtered supported files are counted as skipped",
            filtered_report["summary"]["skipped"] == 3
            and filtered_report["skipped_files"] == 3
            and getattr(filtered_path, "skipped_count", None) == 3
            and filtered_details
            == [{"reason": "filtered", "count": 3, "scan_status": "skipped"}],
            f"summary={filtered_report['summary']} details={filtered_details}",
        ),
        (
            "filtered files are represented once, never listed individually",
            all(
                name not in md_text and name not in html_text
                for name in ("notes.txt", "photo.jpg", "slides.pptx")
            )
            and "3 file(s) — filtered" in md_text
            and "3 file(s) — filtered" in html_text,
            "aggregate-only" if len(filtered_details) == 1 else str(filtered_details),
        ),
        (
            "unsupported extension count is reported in the banner",
            "[i] 1 unsupported extension(s) ignored" in banner,
            banner.strip().replace("\n", " | "),
        ),
        (
            "extensions=None is byte-identical to omitted default",
            omitted_bytes == none_bytes
            and omitted_report["summary"]["scanned"] == 4,
            f"equal={omitted_bytes == none_bytes} scanned={none_report['summary']['scanned']}",
        ),
        (
            "latest.html is published after the unique HTML report",
            latest_text == html_text,
            f"latest_bytes={len(latest_text)} unique_bytes={len(html_text)}",
        ),
        (
            "run_ner=False removes gliner from scan-level report layers",
            "gliner" not in filtered_report["layers"]["enabled"]
            and "gliner" in filtered_report["layers"]["disabled"],
            str(filtered_report["layers"]),
        ),
    ]


def _permission_error_case(root):
    reports = root / "reports"
    readable = root / "readable.txt"
    locked = root / "locked.txt"
    readable.write_text("readable", encoding="utf-8")
    locked.write_text("locked", encoding="utf-8")
    real_getsize = os.path.getsize

    def getsize(path):
        if os.path.abspath(path) == os.path.abspath(locked):
            raise PermissionError(13, "Permission denied", str(path))
        return real_getsize(path)

    with (
        patch.object(discovery, "SystemMonitor", StubMonitor),
        patch.object(discovery, "scan_file", side_effect=_fake_scan_file),
        patch.object(discovery.os.path, "getsize", side_effect=getsize),
        patch.object(
            discovery.llm_verifier,
            "check_availability",
            return_value=(False, "disabled"),
        ),
        patch.object(discovery, "EXCLUDED_REPORT_OUTPUT_DIR", str(reports)),
        patch.object(
            discovery,
            "EXCLUDED_SYSTEM_MONITOR_LOG",
            str(reports / "system_monitor.log"),
        ),
    ):
        html_path = discovery.scan_folder(
            str(root), verify=False, run_ner=False, max_workers=1
        )

    report = json.loads(
        Path(os.path.splitext(str(html_path))[0] + ".json").read_text()
    )
    details = report["skipped_file_details"]
    passed = (
        report["summary"]["scanned"] == 1
        and report["summary"]["skipped"] == 1
        and getattr(html_path, "skipped_count", None) == 1
        and details
        == [
            {
                "file": os.path.abspath(locked),
                "reason": "permission denied",
                "scan_status": "skipped",
            }
        ]
    )
    return (
        "getsize PermissionError is recorded as skipped",
        passed,
        f"summary={report['summary']} details={details}",
    )


def main():
    cases = []

    temp, root, output_dir = _output_fixture()
    try:
        with (
            patch.object(discovery, "EXCLUDED_REPORT_OUTPUT_DIR", _resolved(output_dir)),
            patch.object(
                discovery,
                "EXCLUDED_SYSTEM_MONITOR_LOG",
                _resolved(output_dir / "system_monitor.log"),
            ),
        ):
            files, details = discovery.discover_files(str(root), return_details=True)
        relative = {os.path.relpath(path, root) for path in files}
        cases.append(
            (
                "active outputs directory is excluded",
                not any(name.startswith("outputs" + os.sep) for name in relative)
                and details["excluded_output_files"] == 3,
                f"excluded={details['excluded_output_files']}",
            )
        )
        cases.append(
            (
                "same supported extensions outside outputs are scanned",
                {"outside.md", "outside.json"}.issubset(relative),
                f"discovered={sorted(relative)}",
            )
        )
    finally:
        temp.cleanup()

    # This case exercises the REAL (unpatched) EXCLUDED_REPORT_OUTPUT_DIR
    # against the real project root, so it can't patch the directory away
    # like the fixture above does. Instead it builds its own fixture file
    # inside that real directory so the assertion never depends on
    # whatever happens to already be in outputs/ (previously flaky: an
    # empty outputs/ made excluded_output_files == 0 and failed the case
    # even though exclusion itself was working correctly).
    project_root = Path(__file__).resolve().parents[1]
    real_output_dir = Path(discovery.EXCLUDED_REPORT_OUTPUT_DIR)
    real_output_dir.mkdir(parents=True, exist_ok=True)
    fixture_file = real_output_dir / "test_scan_boundaries_fixture.tmp"
    fixture_file.write_text("fixture", encoding="utf-8")
    try:
        files, details = discovery.discover_files(str(project_root), return_details=True)
    finally:
        fixture_file.unlink(missing_ok=True)
    output_prefix = discovery.EXCLUDED_REPORT_OUTPUT_DIR + os.sep
    cases.append(
        (
            "project-root discovery excludes active outputs",
            details["excluded_output_files"] > 0
            and not any(
                _resolved(path) == discovery.EXCLUDED_SYSTEM_MONITOR_LOG
                or _resolved(path).startswith(output_prefix)
                for path in files
            ),
            f"excluded={details['excluded_output_files']}",
        )
    )

    with tempfile.TemporaryDirectory() as temp_name:
        root = Path(temp_name) / "scan"
        outside = Path(temp_name) / "outside"
        root.mkdir()
        outside.mkdir()
        external_file = outside / "external.txt"
        external_file.write_text("same content", encoding="utf-8")
        regular_file = root / "regular.txt"
        regular_file.write_text("same content", encoding="utf-8")
        external_link = root / "external-link.txt"
        external_link.symlink_to(external_file)

        linked_dir_target = outside / "linked-dir-target"
        linked_dir_target.mkdir()
        (linked_dir_target / "hidden.txt").write_text("hidden", encoding="utf-8")
        linked_dir = root / "linked-dir"
        linked_dir.symlink_to(linked_dir_target, target_is_directory=True)

        files, details = discovery.discover_files(str(root), return_details=True)
        resolved_files = {_resolved(path) for path in files}
        skipped = details["skipped_files"]
        skipped_by_name = {
            os.path.basename(item["file"]): item.get("reason") for item in skipped
        }
        cases.append(
            (
                "external file symlink is skipped and recorded",
                _resolved(external_link) not in resolved_files
                and skipped_by_name.get("external-link.txt") == "symlink",
                f"skipped={skipped_by_name}",
            )
        )
        cases.append(
            (
                "regular file with identical content is scanned",
                _resolved(regular_file) in resolved_files,
                f"files={[os.path.basename(path) for path in files]}",
            )
        )
        cases.append(
            (
                "symlinked directory remains untraversed",
                not any(path.endswith("hidden.txt") for path in files)
                and skipped_by_name.get("linked-dir") == "symlink",
                f"skipped={skipped_by_name}",
            )
        )

    with tempfile.TemporaryDirectory() as temp_name:
        cases.extend(_extension_filter_cases(Path(temp_name)))

    with tempfile.TemporaryDirectory() as temp_name:
        cases.append(_permission_error_case(Path(temp_name)))

    print(f"{'CASE':<58} {'RESULT':<7} DETAIL")
    print("-" * 94)
    for name, passed, detail in cases:
        print(f"{name:<58} {'PASS' if passed else 'FAIL':<7} {detail}")
    passed_count = sum(passed for _, passed, _ in cases)
    print("-" * 94)
    print(f"SUMMARY: {passed_count}/{len(cases)} passed")
    return 0 if passed_count == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
