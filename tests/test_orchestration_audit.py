#!/usr/bin/env python3
"""Regression tests for orchestration findings from the repository audit."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import discovery
import main as main_module
import scanner as scanner_module


class StubMonitor:
    instances = []

    def __init__(self, interval=2):
        self.interval = interval
        self.started = False
        self.stopped = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def _run_with_stub_monitor(action):
    StubMonitor.instances.clear()
    with patch.object(discovery, "SystemMonitor", StubMonitor):
        outcome = action()
    return outcome, StubMonitor.instances[-1]


def _no_files_case():
    with tempfile.TemporaryDirectory() as folder:
        with patch.object(
            discovery,
            "discover_files",
            return_value=([], {"excluded_output_files": 0, "skipped_files": []}),
        ):
            return discovery.scan_folder(folder, verify=False)


def _exception_case():
    with tempfile.TemporaryDirectory() as folder:
        sample = os.path.join(folder, "sample.txt")
        with (
            patch.object(
                discovery,
                "discover_files",
                return_value=(
                    [sample],
                    {"excluded_output_files": 0, "skipped_files": []},
                ),
            ),
            patch.object(
                discovery.llm_verifier,
                "check_availability",
                side_effect=RuntimeError("synthetic mid-scan failure"),
            ),
        ):
            try:
                discovery.scan_folder(folder, verify=False)
            except RuntimeError:
                return "raised"
    return "did not raise"


def _normal_case():
    with tempfile.TemporaryDirectory() as folder:
        sample = os.path.join(folder, "sample.txt")
        result = {
            "file": sample,
            "matches": {},
            "score": 0,
            "metadata": {},
        }
        with (
            patch.object(
                discovery,
                "discover_files",
                return_value=(
                    [sample],
                    {"excluded_output_files": 0, "skipped_files": []},
                ),
            ),
            patch.object(discovery.os.path, "getsize", return_value=1),
            patch.object(
                discovery.llm_verifier,
                "check_availability",
                return_value=(False, "disabled"),
            ),
            patch.object(discovery, "scan_file", return_value=result),
            patch.object(discovery, "generate_markdown"),
            patch.object(discovery, "generate_json"),
            patch.object(discovery, "generate_html"),
            patch.object(discovery, "_replace_latest_report"),
            patch.object(discovery.os, "makedirs"),
            patch.object(discovery.os, "chmod"),
        ):
            return discovery.scan_folder(folder, verify=False)


def main():
    cases = []

    outcome, monitor = _run_with_stub_monitor(_no_files_case)
    cases.append(
        (
            "no supported files: stop called without start",
            outcome is None and not monitor.started and monitor.stopped,
            f"started={monitor.started} stopped={monitor.stopped}",
        )
    )

    outcome, monitor = _run_with_stub_monitor(_exception_case)
    cases.append(
        (
            "mid-scan exception: stop called",
            outcome == "raised" and monitor.started and monitor.stopped,
            f"started={monitor.started} stopped={monitor.stopped}",
        )
    )

    outcome, monitor = _run_with_stub_monitor(_normal_case)
    cases.append(
        (
            "normal scan: stop called",
            bool(outcome) and monitor.started and monitor.stopped,
            f"started={monitor.started} stopped={monitor.stopped}",
        )
    )

    folder_timestamps = (discovery._report_timestamp(), discovery._report_timestamp())
    cases.append(
        (
            "folder reports: consecutive basenames differ",
            folder_timestamps[0] != folder_timestamps[1],
            "different" if folder_timestamps[0] != folder_timestamps[1] else "collision",
        )
    )

    with tempfile.TemporaryDirectory() as folder:
        with patch.object(discovery, "scan_folder", return_value="folder-report") as scan:
            outcome = discovery.scan_path(
                folder,
                verify=False,
                ocr_workers=3,
                extensions={".pdf"},
            )
        cases.append(
            (
                "scan_path directory delegates unchanged",
                outcome == "folder-report"
                and scan.call_args.kwargs["verify"] is False
                and scan.call_args.kwargs["ocr_workers"] == 3
                and scan.call_args.kwargs["extensions"] == {".pdf"},
                str(scan.call_args),
            )
        )

    with tempfile.TemporaryDirectory() as folder:
        sample = os.path.join(folder, "interactive.txt")
        Path(sample).write_text("sample", encoding="utf-8")
        answers = iter(["2", sample, "4"])
        with (
            patch("builtins.input", side_effect=lambda _prompt="": next(answers)),
            patch.object(
                main_module.discovery, "scan_path", return_value="report.html"
            ) as scan,
            patch.object(main_module, "prompt_open_report") as prompt,
        ):
            try:
                main_module.main()
            except SystemExit as exc:
                exited_cleanly = exc.code == 0
        cases.append(
            (
                "interactive single-file CLI uses full scan_path pipeline",
                exited_cleanly
                and scan.call_args.args == (sample,)
                and prompt.call_args.args == ("report.html",),
                str(scan.call_args),
            )
        )

    with tempfile.TemporaryDirectory() as folder:
        sample = os.path.join(folder, "noninteractive.txt")
        Path(sample).write_text("sample", encoding="utf-8")
        with (
            patch.object(
                scanner_module, "scan_path", return_value="report.html"
            ) as scan,
            patch.object(scanner_module, "prompt_open_report"),
            patch.object(
                sys,
                "argv",
                ["scanner.py", "--path", sample, "--no-open"],
            ),
        ):
            exit_code = scanner_module.main()
        cases.append(
            (
                "scanner --path accepts a file and uses scan_path",
                exit_code == 0
                and scan.call_args.args == (os.path.abspath(sample),)
                and "verify" in scan.call_args.kwargs,
                str(scan.call_args),
            )
        )

    with tempfile.TemporaryDirectory() as folder:
        sample = os.path.join(folder, "one.txt")
        Path(sample).write_text("sample", encoding="utf-8")
        with (
            patch.object(discovery, "SystemMonitor", StubMonitor),
            patch.object(
                discovery,
                "_scan_folder_impl",
                return_value="single-report",
            ) as scan_impl,
        ):
            outcome = discovery.scan_path(
                sample,
                verify=False,
                extensions={".txt"},
            )
        cases.append(
            (
                "scan_path file enters one-item full pipeline",
                outcome == "single-report"
                and scan_impl.call_args.kwargs["files_override"] == [
                    os.path.abspath(sample)
                ]
                and scan_impl.call_args.kwargs["discovery_label"]
                == os.path.abspath(sample)
                and scan_impl.call_args.kwargs["extensions"] == {".txt"},
                str(scan_impl.call_args),
            )
        )

    print(f"{'CASE':<52} {'RESULT':<7} DETAIL")
    print("-" * 86)
    for name, passed, detail in cases:
        print(f"{name:<52} {'PASS' if passed else 'FAIL':<7} {detail}")
    passed_count = sum(passed for _, passed, _ in cases)
    print("-" * 86)
    print(f"SUMMARY: {passed_count}/{len(cases)} passed")
    return 0 if passed_count == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
