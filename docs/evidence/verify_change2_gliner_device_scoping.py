#!/usr/bin/env python3
"""Proof for Change 2: active_device() scan scoping.

Reproduces the exact failing case described in the task:
  1. Run a scan where GLiNER is enabled AND actually executes on a file
     (a .txt file with a real name in it) -- this loads the module-level
     GLiNER singleton and sets its device.
  2. Run a SECOND scan, in the same process, where GLiNER is still an
     enabled layer for the scan but every file is excluded from the layer
     by extension (config.GLINER_SKIP_EXTENSIONS), so GLiNER never actually
     executes for any file this scan.

Before the fix: scan 2's report echoed the device measured during scan 1
(a stale value the second scan never measured).
After the fix: scan 2's report shows gliner_device = None, because the
run counter did not advance during that scan.

Writes JSON evidence to docs/evidence/change2_gliner_device_scoping.json.
Run inside the CPU container:
  docker compose run --rm securescan-cpu python docs/evidence/verify_change2_gliner_device_scoping.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import discovery
from detectors import gliner_detector

EVIDENCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "change2_gliner_device_scoping.json")


def _read_gliner_device(html_path):
    json_path = os.path.splitext(html_path)[0] + ".json"
    with open(json_path, encoding="utf-8") as fh:
        report = json.load(fh)
    return report["summary"]["gliner_device"]


def main():
    evidence = {}

    with tempfile.TemporaryDirectory() as outputs_dir, tempfile.TemporaryDirectory() as scan1_dir, tempfile.TemporaryDirectory() as scan2_dir:
        discovery.EXCLUDED_REPORT_OUTPUT_DIR = outputs_dir

        # --- Scan 1: GLiNER enabled AND actually runs (a real .txt file). ---
        with open(os.path.join(scan1_dir, "note.txt"), "w", encoding="utf-8") as fh:
            fh.write("Jonathan Whitfield met with the team in Toronto on March 3, 2024.")

        run_count_before_scan1 = gliner_detector.run_counter()
        html_path_1 = discovery.scan_folder(scan1_dir, verify=False, max_workers=1)
        run_count_after_scan1 = gliner_detector.run_counter()
        device_scan1 = _read_gliner_device(html_path_1)

        evidence["scan1"] = {
            "description": "GLiNER enabled, real .txt file -- GLiNER actually executes",
            "gliner_run_counter_before": run_count_before_scan1,
            "gliner_run_counter_after": run_count_after_scan1,
            "gliner_actually_ran": run_count_after_scan1 != run_count_before_scan1,
            "reported_gliner_device": device_scan1,
        }

        # --- Scan 2: GLiNER still enabled for the scan, but the only file's
        # extension is in GLINER_SKIP_EXTENSIONS, so GLiNER never executes
        # for any file this scan -- while the singleton device from scan 1
        # is still sitting in the module global. ---
        import config
        skip_ext = sorted(config.GLINER_SKIP_EXTENSIONS)[0]
        skipped_file = os.path.join(scan2_dir, f"data{skip_ext}")
        with open(skipped_file, "w", encoding="utf-8") as fh:
            fh.write("Jonathan Whitfield met with the team in Toronto on March 3, 2024.")

        run_count_before_scan2 = gliner_detector.run_counter()
        html_path_2 = discovery.scan_folder(scan2_dir, verify=False, max_workers=1)
        run_count_after_scan2 = gliner_detector.run_counter()
        device_scan2 = _read_gliner_device(html_path_2)

        evidence["scan2"] = {
            "description": (
                f"GLiNER enabled for the scan, only file is '{skip_ext}' "
                "(GLINER_SKIP_EXTENSIONS) -- GLiNER never executes this scan, "
                "run after a prior scan (scan1) that DID use GLiNER"
            ),
            "skip_extension_used": skip_ext,
            "gliner_run_counter_before": run_count_before_scan2,
            "gliner_run_counter_after": run_count_after_scan2,
            "gliner_actually_ran": run_count_after_scan2 != run_count_before_scan2,
            "reported_gliner_device": device_scan2,
            "module_singleton_device_still_set": gliner_detector.active_device(),
        }

        evidence["verdict"] = {
            "scan1_measured_a_real_device": evidence["scan1"]["reported_gliner_device"] is not None,
            "scan2_correctly_reports_none": evidence["scan2"]["reported_gliner_device"] is None,
            "singleton_was_not_reloaded": (
                gliner_detector.active_device() == evidence["scan1"]["reported_gliner_device"]
            ),
        }

    with open(EVIDENCE_PATH, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, indent=2)

    print(json.dumps(evidence, indent=2))
    print()
    print(f"[i] Evidence written to {EVIDENCE_PATH}")

    ok = (
        evidence["verdict"]["scan1_measured_a_real_device"]
        and evidence["verdict"]["scan2_correctly_reports_none"]
        and evidence["verdict"]["singleton_was_not_reloaded"]
    )
    print(f"[{'PASS' if ok else 'FAIL'}] Change 2 scan-scoping proof")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
