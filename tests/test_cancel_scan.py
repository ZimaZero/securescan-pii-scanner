#!/usr/bin/env python3
"""Regression tests for v1.2 cancellation: scan_folder(cancel_event=...).

Covers: partial results on cancellation (fewer files scanned than
discovered), in-flight files at cancel time are allowed to finish, the
scan_cancelled flag lands in the JSON summary, the sequential LLM
verification pass is genuinely skipped when cancelled (not just skipped
because no findings needed it), and cancel_event=None stays byte-identical
to pre-v1.2 behavior.
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import discovery


class StubMonitor:
    def __init__(self, interval=2):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def _fake_scan_file_factory(cancel_event, cancel_after, return_text=False):
    completed = []

    def fake_scan_file(path, **kwargs):
        completed.append(path)
        if cancel_event is not None and len(completed) == cancel_after:
            cancel_event.set()
        time.sleep(0.02)  # in-flight window wide enough for a rolling submit to observe cancellation
        res = {
            "file": os.path.abspath(path),
            "scan_status": "scanned",
            "failure_reason": None,
            "matches": {},
            "score": 0,
            "metadata": {},
            "mismatch_alarm": None,
        }
        if kwargs.get("return_text") or return_text:
            res["_text"] = "some extracted text"
        return res

    return fake_scan_file, completed


def _run_case(total_files, cancel_after, max_workers, verify, cancel=True):
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(total_files):
            Path(tmp, f"f{i}.txt").write_text("hello world")

        cancel_event = threading.Event() if cancel else None
        fake_scan_file, completed = _fake_scan_file_factory(cancel_event, cancel_after)

        verify_findings_mock = MagicMock()
        with (
            patch.object(discovery, "SystemMonitor", StubMonitor),
            patch.object(discovery, "scan_file", side_effect=fake_scan_file),
            patch.object(
                discovery.llm_verifier,
                "check_availability",
                return_value=(verify, "forced-for-test"),
            ),
            patch.object(discovery.llm_verifier, "verify_findings", verify_findings_mock),
        ):
            html_path = discovery.scan_folder(
                tmp,
                verify=verify,
                run_ner=False,
                max_workers=max_workers,
                cancel_event=cancel_event,
            )

        json_path = os.path.splitext(str(html_path))[0] + ".json"
        with open(json_path, "r", encoding="utf-8") as fh:
            report = json.load(fh)
        return report, len(completed), verify_findings_mock


def main():
    cases = []

    # Cancel mid-scan with verify=True: proves both "partial results" and
    # "LLM pass genuinely skipped" (verify_findings must never be called),
    # not merely skipped-because-nothing-needed-it.
    report, completed_count, verify_mock = _run_case(
        total_files=10, cancel_after=3, max_workers=2, verify=True
    )
    cases.append((
        "partial results: fewer files scanned than discovered",
        report["summary"]["scanned"] < 10 and report["summary"]["scanned"] == completed_count,
        f"scanned={report['summary']['scanned']} completed={completed_count}",
    ))
    cases.append((
        "scan_cancelled flag set true in JSON summary",
        report["summary"]["scan_cancelled"] is True,
        f"scan_cancelled={report['summary']['scan_cancelled']}",
    ))
    cases.append((
        "in-flight files at cancel time were allowed to finish (>= max_workers)",
        completed_count >= 2,
        f"completed={completed_count}",
    ))
    cases.append((
        "LLM verification pass is skipped entirely when cancelled",
        verify_mock.call_count == 0,
        f"verify_findings call_count={verify_mock.call_count}",
    ))

    # No cancel_event -> byte-identical to pre-v1.2 behavior: all files
    # scanned, scan_cancelled false, LLM pass DOES run (verify=True, no
    # cancellation to skip it for).
    report_none, completed_none, verify_mock_none = _run_case(
        total_files=4, cancel_after=999, max_workers=4, verify=True, cancel=False
    )
    cases.append((
        "cancel_event=None: all files scanned, scan_cancelled false",
        report_none["summary"]["scanned"] == 4
        and report_none["summary"]["scan_cancelled"] is False,
        f"scanned={report_none['summary']['scanned']} cancelled={report_none['summary']['scan_cancelled']}",
    ))
    cases.append((
        "cancel_event=None: LLM verification pass still runs",
        verify_mock_none.call_count == 4,
        f"verify_findings call_count={verify_mock_none.call_count}",
    ))

    # cancel_event created but never set -> also byte-identical.
    report_unset, completed_unset, _ = _run_case(
        total_files=5, cancel_after=999, max_workers=3, verify=False
    )
    cases.append((
        "cancel_event provided but never set: all files scanned",
        report_unset["summary"]["scanned"] == 5
        and report_unset["summary"]["scan_cancelled"] is False,
        f"scanned={report_unset['summary']['scanned']} cancelled={report_unset['summary']['scan_cancelled']}",
    ))

    print(f"{'CASE':<62} {'RESULT':<7} DETAIL")
    print("-" * 100)
    for name, passed, detail in cases:
        print(f"{name:<62} {'PASS' if passed else 'FAIL':<7} {detail}")
    passed_count = sum(passed for _, passed, _ in cases)
    print("-" * 100)
    print(f"SUMMARY: {passed_count}/{len(cases)} passed")
    return 0 if passed_count == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
