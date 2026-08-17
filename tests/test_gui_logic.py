#!/usr/bin/env python3
# tests/test_gui_logic.py
"""
Test suite for gui.py's pure helpers (SecureScan NiceGUI front end):
  - build_scan_kwargs()       — form-state -> real scan_folder() kwargs.
  - normalize_file_types() / extensions_for_file_types() — extension checkboxes.
  - normalize_layers() / layers_for_scan() / toggle_all_layers() — detection-layer checkboxes.
  - html_path_to_json_path()  — sibling report path derivation.
  - translate_windows_path()  — Explorer drive/UNC path translation.
  - windows_profile_directory() — container-safe Windows Home discovery.
  - load/save/remove_browser_shortcut() — editable shortcut-chip configuration.
  - resolve_windows_profile_folder() — per-folder OneDrive redirection.
  - request_windows_folder_pick() — tokenized Windows-picker bridge round trip.
  - progress_fraction()       — progress-bar value, including the total<=0 edge.
  - infer_phase()             — client-side running-phase inference.
  - summarize_report()        — reads a scan's own JSON report, never recomputes.
  - extract_findings_preview()— top-N-by-score, TYPE only, never a value.
  - load_recent_folders() / add_recent_folder() — .gui_state.json persistence.
  - load/save_gui_settings() — persisted verification/NER/background toggles.
  - in_flight_names() / cancelling_status_line() — pending-cancel feedback.
  - settings_summary()       — one-line next-scan control summary.
  - list_subdirectories()     — server-side folder browser listing, hidden dirs last.
  - try_native_folder_dialog()— zenity/kdialog fallback decision, subprocess mocked.
  - truncate_middle()         — middle-ellipsis filename truncation (v1.2).
  - cancelled_banner_text()   — partial-results banner text (v1.2).
  - current_file_line()       — "Processing: <file>" + large-file hint (v1.2).
  - breadcrumb_segments()     — folder dialog breadcrumb trail (v1.2).
  - initial/running/apply_progress/done/error_scan_state() — module-level
    scan state store transitions, reload-safe (v1.2).
  - client_is_active()       — stale NiceGUI client refresh guard.
  - auto_shutdown_action()  — last-client grace timer state machine.

Plus an import-smoke test: `import gui` must succeed and must NOT start a
server.

Run directly for a pass/fail table + summary:
    docker compose run --rm securescan-cpu python tests/test_gui_logic.py

Also importable / pytest-compatible.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui

# ============================================================
#  1. build_scan_kwargs() CASES
# ============================================================
# (label, verify_on, run_ner_on, max_workers, ocr_workers,
#  max_file_size_mb, ner_max_chars, file_types, layers, expected)
KWARGS_CASES = [
    (
        "all defaults ON",
        True, True, 4, 4, 10, 150_000, list(gui.FILE_TYPE_EXTENSIONS), list(gui.DETECTION_LAYERS),
        {"verify": True, "run_ner": True, "max_workers": 4, "ocr_workers": 4, "max_file_size_mb": 10.0, "ner_max_chars": 150_000, "extensions": None, "enabled_layers": None},
    ),
    (
        "verify + NER both off",
        False, False, 2, 1, 5, 20_000, [".pdf"], ["regex", "secrets"],
        {"verify": False, "run_ner": False, "max_workers": 2, "ocr_workers": 1, "max_file_size_mb": 5.0, "ner_max_chars": 20_000, "extensions": {".pdf"}, "enabled_layers": frozenset({"regex", "secrets"})},
    ),
    (
        "mixed toggles, slider extremes",
        True, False, 16, 8, 999, 1_000_000,
        [".txt", ".md", ".csv", ".json", ".log", ".py", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif", ".webp"],
        list(gui.DETECTION_LAYERS),
        {"verify": True, "run_ner": False, "max_workers": 16, "ocr_workers": 8, "max_file_size_mb": 999.0, "ner_max_chars": 1_000_000, "extensions": {".txt", ".md", ".csv", ".json", ".log", ".py", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif", ".webp"}, "enabled_layers": None},
    ),
]

LAYER_CASES = [
    ("missing selection defaults to all", None, list(gui.DETECTION_LAYERS), None),
    ("single layer", ["regex"], ["regex"], frozenset({"regex"})),
    (
        "selection is restored in canonical (sorted) order",
        ["uci", "regex"],
        ["regex", "uci"],
        frozenset({"regex", "uci"}),
    ),
    ("empty selection runs no layers", [], [], frozenset()),
    (
        "unknown persisted layer fails safe to all",
        ["regex", "bogus"],
        list(gui.DETECTION_LAYERS),
        None,
    ),
]

FILE_TYPE_CASES = [
    ("missing selection defaults to all", None, list(gui.FILE_TYPE_EXTENSIONS), None),
    ("PDF only", [".pdf"], [".pdf"], {".pdf"}),
    (
        "selection is restored in canonical order",
        [".pptx", ".txt"],
        [".txt", ".pptx"],
        {".txt", ".pptx"},
    ),
    ("empty selection scans no types", [], [], set()),
    (
        "unknown persisted extension fails safe to all",
        [".pdf", ".unknown"],
        list(gui.FILE_TYPE_EXTENSIONS),
        None,
    ),
    (
        "v2.4 family labels migrate to extensions",
        ["PDF", "Images"],
        [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif", ".webp"],
        {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif", ".webp"},
    ),
    (
        "v2.4 Word docs label migrates",
        ["Word docs"],
        [".docx"],
        {".docx"},
    ),
]

# ============================================================
#  2. html_path_to_json_path() CASES
# ============================================================
PATH_CASES = [
    ("simple report path", "/outputs/report_20260101_120000_000000.html",
     "/outputs/report_20260101_120000_000000.json"),
    ("no directory component", "report_x.html", "report_x.json"),
    ("path with dots elsewhere", "/a.b/report_x.html", "/a.b/report_x.json"),
]

WINDOWS_PATH_CASES = [
    (
        "backslash drive path",
        r"C:\Users\sampleuser\Desktop",
        None,
        "/mnt/c/Users/sampleuser/Desktop",
    ),
    (
        "forward-slash drive path",
        "D:/Shared/Documents",
        None,
        "/mnt/d/Shared/Documents",
    ),
    (
        "lowercase drive letter is canonicalized",
        r"c:\Users\sampleuser",
        None,
        "/mnt/c/Users/sampleuser",
    ),
    ("drive root trailing backslash", "C:\\", None, "/mnt/c"),
    (
        "folder trailing separators are removed",
        "C:/Users/sampleuser/Desktop///",
        None,
        "/mnt/c/Users/sampleuser/Desktop",
    ),
    (
        "UNC path uses its actual mounted location",
        r"\\server\share\Team\Cases",
        {"//SERVER/SHARE": "/mnt/team_docs"},
        "/mnt/team_docs/Team/Cases",
    ),
    (
        "forward-slash UNC is accepted",
        "//server/share/Team/Cases/",
        {"//server/share": "/mnt/team_docs"},
        "/mnt/team_docs/Team/Cases",
    ),
    (
        "container-native path passes through untouched",
        "/mnt/c/Users/sampleuser/Desktop/",
        None,
        "/mnt/c/Users/sampleuser/Desktop/",
    ),
]

# ============================================================
#  3. progress_fraction() CASES
# ============================================================
FRACTION_CASES = [
    ("not started (0/0)", 0, 0, 0.0),
    ("zero total (no files discovered yet)", 0, 0, 0.0),
    ("halfway", 5, 10, 0.5),
    ("complete", 10, 10, 1.0),
    ("negative total defensive clamp", 3, -1, 0.0),
]

# ============================================================
#  4. infer_phase() CASES
# ============================================================
# (label, done, total, scan_finished, verify_on, expected)
PHASE_CASES = [
    ("before first callback", 0, 0, False, True, "discovery"),
    ("mid-scan", 4, 11, False, True, "scanning"),
    ("all done, verify on -> AI pass still running", 11, 11, False, True, "ai_verification"),
    ("all done, verify off -> just writing reports", 11, 11, False, False, "writing_reports"),
    ("scan_folder() has returned -> done overrides everything", 11, 11, True, True, "done"),
    ("scan_finished true even with total 0 (edge)", 0, 0, True, False, "done"),
]

# ============================================================
#  5. extract_findings_preview() fixtures built inline (see function)
# ============================================================

# ============================================================
#  6. recent-folders persistence CASES built inline (needs tmp files)
# ============================================================

# ============================================================
#  7. list_subdirectories() built inline (needs tmp dir)
# ============================================================

# ============================================================
#  8. try_native_folder_dialog() CASES (subprocess.run mocked)
# ============================================================


def _check_kwargs():
    rows, failures = [], []
    for label, verify_on, run_ner_on, max_workers, ocr_workers, max_file_size_mb, ner_max_chars, file_types, layers, expected in KWARGS_CASES:
        actual = gui.build_scan_kwargs(
            verify_on,
            run_ner_on,
            max_workers,
            ocr_workers,
            max_file_size_mb,
            ner_max_chars,
            file_types,
            layers,
        )
        ok = actual == expected
        rows.append(("KWARGS", label, actual, ok))
        if not ok:
            failures.append((label, actual, expected))
    return rows, failures


def _check_layer_helpers():
    rows, failures = [], []
    for label, value, expected_labels, expected_layers in LAYER_CASES:
        actual_labels = gui.normalize_layers(value)
        actual_layers = gui.layers_for_scan(value)
        actual = (actual_labels, actual_layers)
        expected = (expected_labels, expected_layers)
        ok = actual == expected
        rows.append(("LAYERS", label, actual, ok))
        if not ok:
            failures.append((label, actual, expected))
    # DETECTION_LAYERS must be read from the actual detector registry, not
    # hand-duplicated — a newly added detector (a new SOURCE_PRIORITY entry)
    # should become selectable automatically.
    ok = set(gui.DETECTION_LAYERS) == set(gui.ALL_LAYERS)
    rows.append(("LAYERS", "layer list matches hybrid_detector.ALL_LAYERS", gui.DETECTION_LAYERS, ok))
    if not ok:
        failures.append(("layer registry parity", gui.DETECTION_LAYERS, gui.ALL_LAYERS))
    actual = (
        gui.toggle_all_layers(list(gui.DETECTION_LAYERS)),
        gui.toggle_all_layers([]),
    )
    expected = ([], list(gui.DETECTION_LAYERS))
    ok = actual == expected
    rows.append(("LAYERS", "global toggle switches all and none", actual, ok))
    if not ok:
        failures.append(("global layer toggle", actual, expected))
    return rows, failures


def _check_file_type_helpers():
    rows, failures = [], []
    for label, value, expected_labels, expected_extensions in FILE_TYPE_CASES:
        actual_labels = gui.normalize_file_types(value)
        actual_extensions = gui.extensions_for_file_types(value)
        actual = (actual_labels, actual_extensions)
        expected = (expected_labels, expected_extensions)
        ok = actual == expected
        rows.append(("FILE_TYPES", label, actual, ok))
        if not ok:
            failures.append((label, actual, expected))
    covered = set().union(*gui.FILE_TYPE_GROUPS.values())
    ok = covered == gui.discovery.SUPPORTED_EXTENSIONS
    rows.append(("FILE_TYPES", "groups cover every supported extension", covered, ok))
    if not ok:
        failures.append(("extension coverage", covered, gui.discovery.SUPPORTED_EXTENSIONS))
    toggled = gui.toggle_file_type_family(list(gui.FILE_TYPE_EXTENSIONS), "Images")
    expected = [
        extension
        for extension in gui.FILE_TYPE_EXTENSIONS
        if extension not in gui.FILE_TYPE_GROUPS["Images"]
    ]
    ok = toggled == expected
    rows.append(("FILE_TYPES", "family toggle clears a checked family", toggled, ok))
    if not ok:
        failures.append(("family toggle clear", toggled, expected))
    toggled = gui.toggle_file_type_family(toggled, "Images")
    ok = toggled == list(gui.FILE_TYPE_EXTENSIONS)
    rows.append(("FILE_TYPES", "family toggle selects an incomplete family", toggled, ok))
    if not ok:
        failures.append(("family toggle select", toggled, list(gui.FILE_TYPE_EXTENSIONS)))
    actual = (
        gui.toggle_all_file_types(list(gui.FILE_TYPE_EXTENSIONS)),
        gui.toggle_all_file_types([]),
    )
    expected = ([], list(gui.FILE_TYPE_EXTENSIONS))
    ok = actual == expected
    rows.append(("FILE_TYPES", "global toggle switches all and none", actual, ok))
    if not ok:
        failures.append(("global toggle", actual, expected))
    return rows, failures


def _check_paths():
    rows, failures = [], []
    for label, html_path, expected in PATH_CASES:
        actual = gui.html_path_to_json_path(html_path)
        ok = actual == expected
        rows.append(("PATH", label, actual, ok))
        if not ok:
            failures.append((label, actual, expected))
    return rows, failures


def _check_windows_path_translation():
    rows, failures = [], []
    for label, path, unc_mounts, expected in WINDOWS_PATH_CASES:
        actual = gui.translate_windows_path(path, unc_mounts=unc_mounts)
        ok = actual == expected
        rows.append(("WIN_PATH", label, actual, ok))
        if not ok:
            failures.append((label, actual, expected))
    return rows, failures


def _check_browser_home_and_shortcuts():
    rows, failures = [], []
    with tempfile.TemporaryDirectory() as tmp:
        users_root = os.path.join(tmp, "Users")
        os.makedirs(os.path.join(users_root, "Default"))
        os.makedirs(os.path.join(users_root, "Public"))
        profile = os.path.join(users_root, "CaseWorker")
        os.makedirs(os.path.join(profile, "OneDrive", "Desktop"))
        os.makedirs(os.path.join(profile, "OneDrive - Contoso", "Desktop"))
        os.makedirs(os.path.join(profile, "OneDrive - Contoso", "Documents"))
        os.makedirs(os.path.join(profile, "Desktop"))
        os.makedirs(os.path.join(profile, "Downloads"))
        os.makedirs(os.path.join(profile, "Documents"))

        actual = gui.windows_profile_directory(users_root, environ={})
        ok = actual == profile
        rows.append(("HOME", "unique real Windows profile is derived", actual, ok))
        if not ok:
            failures.append(("Windows profile", actual, profile))

        folder_cases = [
            (
                "personal OneDrive wins over business and plain",
                "Desktop",
                os.path.join(profile, "OneDrive", "Desktop"),
            ),
            (
                "business OneDrive wins over plain",
                "Documents",
                os.path.join(profile, "OneDrive - Contoso", "Documents"),
            ),
            (
                "plain profile folder is the final fallback",
                "Downloads",
                os.path.join(profile, "Downloads"),
            ),
            ("missing profile folder remains unresolved", "Pictures", None),
        ]
        for label, folder, expected in folder_cases:
            actual = gui.resolve_windows_profile_folder(profile, folder)
            ok = actual == expected
            rows.append(("PROFILE_DIR", label, actual, ok))
            if not ok:
                failures.append((label, actual, expected))

        config_path = os.path.join(tmp, "browser_shortcuts.json")
        cases_path = os.path.join(tmp, "Cases")
        os.mkdir(cases_path)
        configured = [
            {"label": "Desktop", "path": "{windows_profile}/Desktop"},
            {"label": "Cases", "path": cases_path},
        ]
        saved = gui.save_browser_shortcuts(configured, config_path)
        actual = gui.load_browser_shortcuts(
            config_path, windows_profile=profile
        )
        expected = [
            {
                "label": "Desktop",
                "path": os.path.join(profile, "OneDrive", "Desktop"),
                "group": "folders",
                "available": True,
            },
            {
                "label": "Cases",
                "path": cases_path,
                "group": "custom",
                "available": True,
            },
        ]
        ok = saved and actual == expected
        rows.append(("SHORTCUT", "config saves and resolves profile/Windows paths", actual, ok))
        if not ok:
            failures.append(("shortcut config", actual, expected))

        raw = gui.load_browser_shortcuts(config_path, resolve=False)
        expected_raw = [
            {**configured[0], "group": "folders"},
            {**configured[1], "group": "custom"},
        ]
        ok = raw == expected_raw
        rows.append(("SHORTCUT", "raw reload preserves placeholder and groups", raw, ok))
        if not ok:
            failures.append(("shortcut placeholder", raw, expected_raw))

        empty_profile = os.path.join(tmp, "EmptyProfile")
        os.mkdir(empty_profile)
        missing_config = os.path.join(tmp, "missing_shortcuts.json")
        gui.save_browser_shortcuts(
            [{"label": "Downloads", "path": "{windows_profile}/Downloads"}],
            missing_config,
        )
        actual = gui.load_browser_shortcuts(
            missing_config, windows_profile=empty_profile
        )
        expected = [
            {
                "label": "Downloads",
                "path": "",
                "group": "folders",
                "available": False,
            }
        ]
        ok = actual == expected
        rows.append(("SHORTCUT", "unresolved profile folder disables its chip", actual, ok))
        if not ok:
            failures.append(("disabled shortcut", actual, expected))

        removable = [
            {"label": "Desktop", "path": "{windows_profile}/Desktop"},
            {"label": "Custom", "path": cases_path},
        ]
        gui.save_browser_shortcuts(removable, config_path)
        removed_default = gui.remove_browser_shortcut(0, config_path)
        removed_custom = gui.remove_browser_shortcut(0, config_path)
        remaining = gui.load_browser_shortcuts(config_path, resolve=False)
        ok = (
            removed_default is not None
            and removed_default["label"] == "Desktop"
            and removed_custom is not None
            and removed_custom["label"] == "Custom"
            and remaining == []
        )
        actual = (
            removed_default and removed_default["label"],
            removed_custom and removed_custom["label"],
            remaining,
        )
        rows.append(("SHORTCUT", "defaults and custom rows use identical removal", actual, ok))
        if not ok:
            failures.append(("uniform removal", actual, ("Desktop", "Custom", [])))

    return rows, failures


def _check_fractions():
    rows, failures = [], []
    for label, done, total, expected in FRACTION_CASES:
        actual = gui.progress_fraction(done, total)
        ok = actual == expected
        rows.append(("FRACTION", label, actual, ok))
        if not ok:
            failures.append((label, actual, expected))
    return rows, failures


def _check_phases():
    rows, failures = [], []
    for label, done, total, scan_finished, verify_on, expected in PHASE_CASES:
        actual = gui.infer_phase(done, total, scan_finished, verify_on)
        ok = actual == expected
        rows.append(("PHASE", label, actual, ok))
        if not ok:
            failures.append((label, actual, expected))
    return rows, failures


def _check_summarize_report():
    rows, failures = [], []

    with tempfile.TemporaryDirectory() as tmp:
        good_path = os.path.join(tmp, "report_good.json")
        with open(good_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "summary": {
                        "scanned": 11,
                        "failed": 0,
                        "skipped": 0,
                        "high_risk": 4,
                        "medium_risk": 3,
                        "low_risk": 3,
                        "no_pii": 1,
                    },
                    "mismatch_alarms": [{"file": "a"}, {"file": "b"}],
                },
                fh,
            )
        actual = gui.summarize_report(good_path)
        expected = {
            "scanned": 11,
            "failed": 0,
            "skipped": 0,
            "high": 4,
            "medium": 3,
            "low": 3,
            "alarms": 2,
            "cancelled": False,
        }
        ok = actual == expected
        rows.append(("SUMMARY", "well-formed report JSON", actual, ok))
        if not ok:
            failures.append(("well-formed report JSON", actual, expected))

        cancelled_path = os.path.join(tmp, "report_cancelled.json")
        with open(cancelled_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "summary": {
                        "scan_cancelled": True,
                        "scanned": 3,
                        "failed": 0,
                        "skipped": 0,
                        "high_risk": 1,
                        "medium_risk": 0,
                        "low_risk": 2,
                        "no_pii": 0,
                    },
                    "mismatch_alarms": [],
                },
                fh,
            )
        actual = gui.summarize_report(cancelled_path)
        ok = actual.get("cancelled") is True and actual.get("scanned") == 3
        rows.append(("SUMMARY", "scan_cancelled flag flows through as 'cancelled'", actual, ok))
        if not ok:
            failures.append(("cancelled report JSON", actual, "cancelled=True, scanned=3"))

        sparse_path = os.path.join(tmp, "report_sparse.json")
        with open(sparse_path, "w", encoding="utf-8") as fh:
            json.dump({}, fh)
        actual = gui.summarize_report(sparse_path)
        ok = (
            actual.get("scanned") == 0
            and actual.get("alarms") == 0
            and actual.get("cancelled") is False
            and "error" not in actual
        )
        rows.append(("SUMMARY", "sparse/empty report JSON defaults to zeros", actual, ok))
        if not ok:
            failures.append(("sparse report JSON", actual, "zeros, no error"))

        actual = gui.summarize_report(os.path.join(tmp, "does_not_exist.json"))
        ok = isinstance(actual, dict) and "error" in actual
        rows.append(("SUMMARY", "missing report file yields error dict", actual, ok))
        if not ok:
            failures.append(("missing report file", actual, "{'error': ...}"))

        bad_path = os.path.join(tmp, "report_bad.json")
        with open(bad_path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        actual = gui.summarize_report(bad_path)
        ok = isinstance(actual, dict) and "error" in actual
        rows.append(("SUMMARY", "malformed report JSON yields error dict", actual, ok))
        if not ok:
            failures.append(("malformed report JSON", actual, "{'error': ...}"))

    return rows, failures


def _fixture_file_entry(name, score, scan_status="scanned", category=None, risk_level="HIGH"):
    matches = {}
    if category is not None:
        matches[category] = [
            {"value": "should-never-appear-in-preview", "confidence": 0.9, "source": "regex", "risk_level": risk_level}
        ]
    return {
        "file": f"/some/path/{name}",
        "scan_status": scan_status,
        "score": score,
        "matches": matches,
    }


def _check_findings_preview():
    rows, failures = [], []

    with tempfile.TemporaryDirectory() as tmp:
        report_path = os.path.join(tmp, "report.json")
        files = [
            _fixture_file_entry("high1.jpg", 90, category="identifier.financial.sin", risk_level="HIGH"),
            _fixture_file_entry("high2.jpg", 85, category="identifier.government.passport_ca", risk_level="HIGH"),
            _fixture_file_entry("medium1.txt", 45, category="contact.phone", risk_level="MEDIUM"),
            _fixture_file_entry("low1.txt", 10, category="contact.email", risk_level="LOW"),
            _fixture_file_entry("no_findings.txt", 0),
            _fixture_file_entry("failed.jpg", None, scan_status="extraction_failed"),
        ]
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump({"files": files}, fh)

        preview = gui.extract_findings_preview(report_path, top_n=10)

        ok = len(preview) == 5  # extraction_failed entry excluded
        rows.append(("PREVIEW", "excludes non-scanned (extraction_failed) files", len(preview), ok))
        if not ok:
            failures.append(("excludes extraction_failed", len(preview), 5))

        ok = [r["file"] for r in preview] == ["high1.jpg", "high2.jpg", "medium1.txt", "low1.txt", "no_findings.txt"]
        rows.append(("PREVIEW", "sorted by score descending", [r["file"] for r in preview], ok))
        if not ok:
            failures.append(("sort order", [r["file"] for r in preview], "score desc"))

        top = preview[0]
        ok = (
            "should-never-appear-in-preview" not in json.dumps(top)
            and top["risk"] == "HIGH"
            and top["top_type"] != "—"
        )
        rows.append(("PREVIEW", "top row has risk + type, never the raw value", top, ok))
        if not ok:
            failures.append(("value leakage / risk / type", top, "no raw value, risk=HIGH"))

        no_finding_row = preview[-1]
        ok = no_finding_row["top_type"] == "—" and no_finding_row["risk"] == "NONE"
        rows.append(("PREVIEW", "file with zero findings shows placeholder type", no_finding_row, ok))
        if not ok:
            failures.append(("no-findings placeholder", no_finding_row, "top_type='—', risk=NONE"))

        top_n1 = gui.extract_findings_preview(report_path, top_n=1)
        ok = len(top_n1) == 1 and top_n1[0]["file"] == "high1.jpg"
        rows.append(("PREVIEW", "top_n respected", [r["file"] for r in top_n1], ok))
        if not ok:
            failures.append(("top_n", top_n1, "1 row, high1.jpg"))

        # Missing/malformed report -> [], never an exception.
        actual = gui.extract_findings_preview(os.path.join(tmp, "missing.json"))
        ok = actual == []
        rows.append(("PREVIEW", "missing report file yields empty list", actual, ok))
        if not ok:
            failures.append(("missing report", actual, []))

    return rows, failures


def _check_recent_folders():
    rows, failures = [], []

    with tempfile.TemporaryDirectory() as tmp:
        state_path = os.path.join(tmp, "nested", ".gui_state.json")

        actual = gui.load_recent_folders(state_path)
        ok = actual == []
        rows.append(("RECENT", "load with no state file yet returns []", actual, ok))
        if not ok:
            failures.append(("load missing", actual, []))

        result = gui.add_recent_folder("/a", state_path=state_path, max_recent=5)
        ok = result == ["/a"]
        rows.append(("RECENT", "add first folder", result, ok))
        if not ok:
            failures.append(("add first", result, ["/a"]))

        result = gui.add_recent_folder("/b", state_path=state_path, max_recent=5)
        result = gui.add_recent_folder("/c", state_path=state_path, max_recent=5)
        ok = result == ["/c", "/b", "/a"]
        rows.append(("RECENT", "most recent first", result, ok))
        if not ok:
            failures.append(("order", result, ["/c", "/b", "/a"]))

        result = gui.add_recent_folder("/a", state_path=state_path, max_recent=5)
        ok = result == ["/a", "/c", "/b"]
        rows.append(("RECENT", "re-adding moves to front, no duplicate", result, ok))
        if not ok:
            failures.append(("dedupe+reorder", result, ["/a", "/c", "/b"]))

        for extra in ("/d", "/e", "/f"):
            result = gui.add_recent_folder(extra, state_path=state_path, max_recent=5)
        ok = len(result) == 5 and result[0] == "/f"
        rows.append(("RECENT", "list capped at max_recent", result, ok))
        if not ok:
            failures.append(("cap", result, "len 5, newest first"))

        reloaded = gui.load_recent_folders(state_path)
        ok = reloaded == result
        rows.append(("RECENT", "persisted list survives reload", reloaded, ok))
        if not ok:
            failures.append(("persistence", reloaded, result))

        # Unwritable path -> best-effort, never raises, still returns the list.
        bad_state_path = "/proc/nonexistent_dir_xyz/.gui_state.json"
        try:
            result = gui.add_recent_folder("/z", state_path=bad_state_path, max_recent=5)
            ok = result and result[0] == "/z"
        except Exception:
            ok = False
            result = "raised"
        rows.append(("RECENT", "write failure is swallowed, list still returned", result, ok))
        if not ok:
            failures.append(("write failure", result, "['/z', ...], no exception"))

    return rows, failures


def _check_gui_settings_persistence():
    rows, failures = [], []
    with tempfile.TemporaryDirectory() as tmp:
        state_path = os.path.join(tmp, "nested", ".gui_state.json")

        actual = gui.load_gui_settings(state_path)
        expected_defaults = {
            "verify_on": False,
            "run_ner_on": True,
            "max_workers": 4,
            "ocr_workers": 4,
            "ner_max_chars": 150_000,
            "file_types": list(gui.FILE_TYPE_EXTENSIONS),
            "layers": list(gui.DETECTION_LAYERS),
        }
        ok = actual == expected_defaults
        rows.append(("SETTINGS", "missing state uses visible defaults", actual, ok))
        if not ok:
            failures.append(("settings defaults", actual, expected_defaults))

        base_settings = {
            "verify_on": False,
            "run_ner_on": True,
            "max_workers": 7,
            "ocr_workers": 6,
            "ner_max_chars": 20_000,
            "layers": list(gui.DETECTION_LAYERS),
        }
        persistence_cases = [
            ("all file types survive reload", list(gui.FILE_TYPE_EXTENSIONS)),
            ("subset of file types survives reload", [".pdf", ".png", ".jpg"]),
            ("empty file-type selection survives reload", []),
        ]
        for label, file_types in persistence_cases:
            expected = {**base_settings, "file_types": file_types}
            gui.save_gui_settings(expected, state_path)
            actual = gui.load_gui_settings(state_path)
            ok = actual == expected
            rows.append(("SETTINGS", label, actual, ok))
            if not ok:
                failures.append((label, actual, expected))

        layer_persistence_cases = [
            ("all layers survive reload", list(gui.DETECTION_LAYERS)),
            ("subset of layers survives reload", ["regex", "secrets"]),
            ("empty layer selection survives reload", []),
        ]
        for label, layers in layer_persistence_cases:
            expected = {
                **base_settings,
                "file_types": list(gui.FILE_TYPE_EXTENSIONS),
                "layers": layers,
            }
            gui.save_gui_settings(expected, state_path)
            actual = gui.load_gui_settings(state_path)
            ok = actual == expected
            rows.append(("SETTINGS", label, actual, ok))
            if not ok:
                failures.append((label, actual, expected))

        expected = {**base_settings, "file_types": [".pdf", ".png", ".jpg"]}
        gui.save_gui_settings(expected, state_path)

        gui.add_recent_folder("/scan/a", state_path=state_path)
        actual_settings = gui.load_gui_settings(state_path)
        actual_recent = gui.load_recent_folders(state_path)
        ok = actual_settings == expected and actual_recent == ["/scan/a"]
        rows.append(
            (
                "SETTINGS",
                "recent-folder write preserves toggle settings",
                (actual_settings, actual_recent),
                ok,
            )
        )
        if not ok:
            failures.append(
                ("settings preserved by recent write", actual_settings, expected)
            )

        gui.save_gui_settings(
            {**expected, "verify_on": True},
            state_path,
        )
        actual_recent = gui.load_recent_folders(state_path)
        ok = actual_recent == ["/scan/a"]
        rows.append(
            ("SETTINGS", "settings write preserves recent folders", actual_recent, ok)
        )
        if not ok:
            failures.append(("recent preserved by settings write", actual_recent, ["/scan/a"]))

        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "settings": {
                        "max_workers": 99,
                        "ocr_workers": 99,
                        "ner_max_chars": 9_999,
                        "file_types": [".pdf", ".unknown"],
                        "layers": ["regex", "bogus"],
                    }
                },
                fh,
            )
        actual = gui.load_gui_settings(state_path)
        ok = (
            actual["max_workers"] == 4
            and actual["ocr_workers"] == 4
            and actual["ner_max_chars"] == 150_000
            and actual["file_types"] == list(gui.FILE_TYPE_EXTENSIONS)
            and actual["layers"] == list(gui.DETECTION_LAYERS)
        )
        rows.append(
            ("SETTINGS", "invalid saved worker/layer values fall back to defaults", actual, ok)
        )
        if not ok:
            failures.append(("worker/layer defaults", actual, expected_defaults))

    return rows, failures


def _check_alarm_details():
    rows, failures = [], []
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "report.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "mismatch_alarms": [
                        {
                            "file": "/private/Taylor.jpg",
                            "triggered_by": "filename+content+face",
                            "reason": "Manual review\n recommended.",
                        },
                        {
                            "file": "/private/scan.jpg",
                            "triggered_by": "both",
                            "reason": "Check.",
                        },
                    ]
                },
                fh,
            )
        actual = gui.extract_alarm_details(path)
        expected = [
            {
                "file": "Taylor.jpg",
                "triggers": ["filename", "content", "face"],
                "reason": "Manual review recommended.",
            },
            {
                "file": "scan.jpg",
                "triggers": ["filename", "content"],
                "reason": "Check.",
            },
        ]
        ok = actual == expected
        rows.append(("ALARMS", "JSON rows normalize trigger badges", actual, ok))
        if not ok:
            failures.append(("alarm details", actual, expected))

        actual = gui.extract_alarm_details(os.path.join(tmp, "missing.json"))
        ok = actual == []
        rows.append(("ALARMS", "missing report returns no rows", actual, ok))
        if not ok:
            failures.append(("missing alarm report", actual, []))
    return rows, failures


def _check_recent_reports():
    rows, failures = [], []
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for index, name in enumerate(
            (
                "report_20260101_000000_000001",
                "report_20260101_000000_000002",
                "report_20260101_000000_000003",
                "report_20260101_000000_000004",
                "report_20260101_000000_000005",
                "report_20260101_000000_000006",
            )
        ):
            html = os.path.join(tmp, name + ".html")
            with open(html, "w", encoding="utf-8") as fh:
                fh.write("report")
            for extension in (".json", ".md"):
                with open(os.path.join(tmp, name + extension), "w", encoding="utf-8") as fh:
                    fh.write("report")
            timestamp = 1000 + index
            os.utime(html, (timestamp, timestamp))
            paths.append(html)
        with open(os.path.join(tmp, "report_ignored.json"), "w") as fh:
            fh.write("{}")

        actual = gui.list_recent_reports(tmp)
        ok = (
            len(actual) == 5
            and actual[0]["name"] == "20260101_000000_000006"
            and actual[-1]["name"] == "20260101_000000_000002"
            and all(row["html_path"].endswith(".html") for row in actual)
        )
        rows.append(("REPORTS", "newest five HTML report sets", actual, ok))
        if not ok:
            failures.append(("recent reports", actual, "newest five"))

        actual = gui.has_latest_report(tmp)
        ok = actual is True
        rows.append(("REPORTS", "complete report set enables latest action", actual, ok))
        if not ok:
            failures.append(("latest availability", actual, True))

        actual = gui.list_recent_reports(os.path.join(tmp, "missing"))
        ok = actual == []
        rows.append(("REPORTS", "missing output directory is empty", actual, ok))
        if not ok:
            failures.append(("missing report dir", actual, []))

        actual = gui.gui_bind_label("0.0.0.0")
        expected = "network bind · 0.0.0.0 · 11-layer engine"
        ok = actual == expected
        rows.append(("REPORTS", "header reflects container network bind", actual, ok))
        if not ok:
            failures.append(("bind label", actual, expected))
    return rows, failures


def _check_output_folder_bridge():
    rows, failures = [], []
    with tempfile.TemporaryDirectory() as tmp:
        actual = gui.OUTPUT_FOLDER_HELPER_TIMEOUT_S
        ok = actual >= 5.0
        rows.append(("EXPLORER", "default helper wait allows WSL startup lag", actual, ok))
        if not ok:
            failures.append(("Explorer helper timeout", actual, ">= 5 seconds"))

        opened, message = gui.request_output_folder_open(
            tmp, timeout=0.02, poll_interval=0.002
        )
        request_path = os.path.join(tmp, gui.OUTPUT_FOLDER_REQUEST_FILENAME)
        token = open(request_path, encoding="ascii").read().strip()
        ok = (
            opened is False
            and "helper is not running" in message
            and len(token) == 32
            and all(char in "0123456789abcdef" for char in token)
        )
        rows.append(("EXPLORER", "missing helper fails visibly", message, ok))
        if not ok:
            failures.append(("missing Explorer helper", (opened, message, token), None))

        os.unlink(request_path)

        def acknowledge_request():
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                try:
                    request_token = open(request_path, encoding="ascii").read().strip()
                except OSError:
                    time.sleep(0.002)
                    continue
                # Exercise a delayed asynchronous acknowledgement.
                time.sleep(0.05)
                ack_path = os.path.join(tmp, gui.OUTPUT_FOLDER_ACK_FILENAME)
                with open(ack_path, "w", encoding="ascii") as fh:
                    fh.write(f"{request_token}\n")
                return

        helper = threading.Thread(target=acknowledge_request, daemon=True)
        helper.start()
        opened, message = gui.request_output_folder_open(
            tmp, timeout=1.0, poll_interval=0.002
        )
        helper.join(timeout=1.0)
        ok = opened is True and message == "Opened outputs in Windows Explorer."
        rows.append(("EXPLORER", "matching helper acknowledgement succeeds", message, ok))
        if not ok:
            failures.append(("Explorer helper acknowledgement", (opened, message), None))
    return rows, failures


def _check_windows_folder_picker_bridge():
    rows, failures = [], []

    actual = gui.WINDOWS_FOLDER_PICKER_TIMEOUT_S
    ok = actual >= 300.0
    rows.append(("WIN_PICK", "dialog timeout allows long navigation", actual, ok))
    if not ok:
        failures.append(("picker timeout", actual, ">= 300 seconds"))

    def start_response(output_dir, status, payload="", opened=True):
        request_path = os.path.join(
            output_dir, gui.WINDOWS_FOLDER_PICKER_REQUEST_FILENAME
        )
        response_path = os.path.join(
            output_dir, gui.WINDOWS_FOLDER_PICKER_RESPONSE_FILENAME
        )

        def respond():
            deadline = time.monotonic() + 1.0
            token = ""
            while time.monotonic() < deadline:
                try:
                    token = open(request_path, encoding="ascii").read().strip()
                except OSError:
                    time.sleep(0.002)
                    continue
                break
            if not token:
                return
            if opened:
                with open(response_path, "w", encoding="ascii") as fh:
                    fh.write(f"{token}\topened\n")
                time.sleep(0.01)
            encoded = (
                base64.b64encode(payload.encode("utf-8")).decode("ascii")
                if payload
                else ""
            )
            suffix = f"\t{encoded}" if encoded else ""
            with open(response_path, "w", encoding="ascii") as fh:
                fh.write(f"{token}\t{status}{suffix}\n")

        helper = threading.Thread(target=respond, daemon=True)
        helper.start()
        return helper

    with tempfile.TemporaryDirectory() as tmp:
        selected_dir = os.path.join(tmp, "selected")
        os.mkdir(selected_dir)
        helper = start_response(tmp, "selected", r"C:\Picked Folder")
        status, selected, message = gui.request_windows_folder_pick(
            tmp,
            timeout=1.0,
            helper_timeout=0.2,
            poll_interval=0.002,
            translate_path=lambda path: (
                selected_dir if path == r"C:\Picked Folder" else path
            ),
        )
        helper.join(timeout=1.0)
        ok = status == "selected" and selected == selected_dir and message == ""
        rows.append(("WIN_PICK", "successful native picker round trip", (status, selected), ok))
        if not ok:
            failures.append(("picker success", (status, selected, message), selected_dir))

    with tempfile.TemporaryDirectory() as tmp:
        helper = start_response(tmp, "cancel")
        status, selected, message = gui.request_windows_folder_pick(
            tmp, timeout=1.0, helper_timeout=0.2, poll_interval=0.002
        )
        helper.join(timeout=1.0)
        ok = status == "cancelled" and selected is None and message == ""
        rows.append(("WIN_PICK", "cancel is silent and selects nothing", status, ok))
        if not ok:
            failures.append(("picker cancel", (status, selected, message), None))

    with tempfile.TemporaryDirectory() as tmp:
        helper = start_response(tmp, "opened", opened=False)
        status, selected, message = gui.request_windows_folder_pick(
            tmp, timeout=0.08, helper_timeout=0.05, poll_interval=0.002
        )
        helper.join(timeout=1.0)
        request_path = os.path.join(
            tmp, gui.WINDOWS_FOLDER_PICKER_REQUEST_FILENAME
        )
        ok = (
            status == "timeout"
            and selected is None
            and "timed out" in message
            and not os.path.exists(request_path)
        )
        rows.append(("WIN_PICK", "opened dialog timeout invalidates request", status, ok))
        if not ok:
            failures.append(("picker timeout", (status, selected, message), "timeout"))

    with tempfile.TemporaryDirectory() as tmp:
        helper = start_response(tmp, "selected", r"Z:\Unavailable")
        status, selected, message = gui.request_windows_folder_pick(
            tmp,
            timeout=1.0,
            helper_timeout=0.2,
            poll_interval=0.002,
            path_is_dir=lambda _path: False,
        )
        helper.join(timeout=1.0)
        ok = (
            status == "unreachable"
            and selected is None
            and "not visible to the scanner" in message
            and "in-app browser" in message
        )
        rows.append(("WIN_PICK", "unmounted Windows path fails visibly", status, ok))
        if not ok:
            failures.append(("picker unreachable", (status, selected, message), None))

    with tempfile.TemporaryDirectory() as tmp:
        response_path = os.path.join(
            tmp, gui.WINDOWS_FOLDER_PICKER_RESPONSE_FILENAME
        )
        stale_payload = base64.b64encode(r"C:\Stale".encode("utf-8")).decode("ascii")
        with open(response_path, "w", encoding="ascii") as fh:
            fh.write(f"{'f' * 32}\tselected\t{stale_payload}\n")
        status, selected, message = gui.request_windows_folder_pick(
            tmp, timeout=0.05, helper_timeout=0.02, poll_interval=0.002
        )
        request_path = os.path.join(
            tmp, gui.WINDOWS_FOLDER_PICKER_REQUEST_FILENAME
        )
        ok = (
            status == "unavailable"
            and selected is None
            and "helper is not running" in message
            and not os.path.exists(request_path)
        )
        rows.append(("WIN_PICK", "stale response token is rejected", status, ok))
        if not ok:
            failures.append(("stale picker token", (status, selected, message), None))

    return rows, failures


def _check_browser_entries():
    rows, failures = [], []
    with tempfile.TemporaryDirectory() as tmp:
        os.mkdir(os.path.join(tmp, "Folder"))
        os.mkdir(os.path.join(tmp, "proc"))
        for name in ("scan.jpg", "notes.txt", "ignored.exe"):
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                fh.write("x")
        actual = gui.list_browser_entries(tmp)
        normalized = [(row["name"], row["is_dir"]) for row in actual]
        expected = [
            ("Folder", True),
            ("proc", True),
            ("notes.txt", False),
            ("scan.jpg", False),
        ]
        ok = normalized == expected
        rows.append(
            (
                "BROWSER",
                "filter is root-only; folders/files elsewhere remain available",
                normalized,
                ok,
            )
        )
        if not ok:
            failures.append(("browser entries", normalized, expected))

    actual = gui.list_browser_entries(os.sep)
    leaked = sorted(
        row["name"]
        for row in actual
        if row["is_dir"] and row["name"] in gui.CONTAINER_INTERNAL_ROOT_DIRS
    )
    ok = leaked == []
    rows.append(("BROWSER", "live root listing hides container internals", leaked, ok))
    if not ok:
        failures.append(("browser root display filter", leaked, []))
    return rows, failures


def _check_cancel_feedback_helpers():
    rows, failures = [], []
    submitted = ["/scan/a.pdf", "b.jpg", "/scan/c.docx"]

    actual = gui.in_flight_names(submitted, 1)
    expected = ["b.jpg", "c.docx"]
    ok = actual == expected
    rows.append(("IN_FLIGHT", "submitted minus done returns tracked basenames", actual, ok))
    if not ok:
        failures.append(("in-flight names", actual, expected))

    actual = gui.in_flight_names(submitted, 99)
    ok = actual == []
    rows.append(("IN_FLIGHT", "done count clamps beyond submissions", actual, ok))
    if not ok:
        failures.append(("in-flight clamp", actual, []))

    actual = gui.cancelling_status_line(submitted, 1)
    expected = "Cancelling — waiting for 2 in-flight file(s): b.jpg, c.docx"
    ok = actual == expected
    rows.append(("IN_FLIGHT", "cancel status includes count and basenames", actual, ok))
    if not ok:
        failures.append(("cancel status", actual, expected))

    return rows, failures


def _check_settings_summary():
    rows, failures = [], []
    cases = [
        (
            "all enabled",
            True,
            True,
            4,
            "Next scan: verification ON · NER ON · 4 workers",
        ),
        (
            "both disabled",
            False,
            False,
            4,
            "Next scan: verification OFF · NER OFF · 4 workers",
        ),
    ]
    for label, verify_on, ner_on, workers, expected in cases:
        actual = gui.settings_summary(verify_on, ner_on, workers)
        ok = actual == expected
        rows.append(("NEXT_SCAN", label, actual, ok))
        if not ok:
            failures.append((label, actual, expected))
    actual = gui.settings_summary(True, True, 4, 20_000)
    expected = "Next scan: verification ON · NER ON · 4 workers · NER cap 20,000"
    ok = actual == expected
    rows.append(("NEXT_SCAN", "custom cap is visible", actual, ok))
    if not ok:
        failures.append(("custom cap", actual, expected))
    file_type_cases = [
        (
            "subset filter is visible",
            [".pdf", ".png", ".jpg"],
            "Next scan: verification ON · NER ON · 4 workers · file types .pdf, .png, .jpg",
        ),
        (
            "long extension list is abbreviated",
            [".txt", ".md", ".csv", ".json", ".log", ".py", ".pdf"],
            "Next scan: verification ON · NER ON · 4 workers · file types .txt, .md, .csv, .json, .log, .py +1 more",
        ),
        (
            "empty filter is visible",
            [],
            "Next scan: verification ON · NER ON · 4 workers · file types none",
        ),
        (
            "all types stay hidden",
            list(gui.FILE_TYPE_EXTENSIONS),
            "Next scan: verification ON · NER ON · 4 workers",
        ),
    ]
    for label, file_types, expected in file_type_cases:
        actual = gui.settings_summary(True, True, 4, file_types=file_types)
        ok = actual == expected
        rows.append(("NEXT_SCAN", label, actual, ok))
        if not ok:
            failures.append((label, actual, expected))
    layer_cases = [
        (
            "subset of layers is visible",
            ["regex", "secrets"],
            "Next scan: verification ON · NER ON · 4 workers · layers regex, secrets",
        ),
        (
            "empty layer selection is visible",
            [],
            "Next scan: verification ON · NER ON · 4 workers · layers none",
        ),
        (
            "all layers stay hidden",
            list(gui.DETECTION_LAYERS),
            "Next scan: verification ON · NER ON · 4 workers",
        ),
    ]
    for label, layers, expected in layer_cases:
        actual = gui.settings_summary(True, True, 4, layers=layers)
        ok = actual == expected
        rows.append(("NEXT_SCAN", label, actual, ok))
        if not ok:
            failures.append((label, actual, expected))
    return rows, failures


def _check_dashboard_helpers():
    rows, failures = [], []
    state_cases = [
        ("idle", False, "idle"),
        ("running", False, "running"),
        ("done", False, "done"),
        ("done", True, "cancelled"),
        ("unexpected", False, "error"),
    ]
    for phase, cancelled, expected in state_cases:
        actual = gui.dashboard_state(phase, cancelled)
        ok = actual == expected
        rows.append(("DASH_STATE", f"{phase}, cancelled={cancelled}", actual, ok))
        if not ok:
            failures.append(("dashboard state", actual, expected))

    summary = {
        "duration": 12.34,
        "scanned": 11,
        "failed": 1,
        "skipped": 2,
        "high": 4,
        "medium": 3,
        "low": 2,
    }
    actual = gui.completion_header(summary, 7, 11, False)
    expected = "Scan complete in 12.3s"
    ok = actual == expected
    rows.append(("DASH_STATE", "normal completion header", actual, ok))
    if not ok:
        failures.append(("normal header", actual, expected))

    actual = gui.completion_header(summary, 7, 11, True)
    expected = "Scan cancelled — partial results (7 of 11 files)"
    ok = actual == expected and "complete" not in actual.lower()
    rows.append(("DASH_STATE", "cancelled header replaces completion", actual, ok))
    if not ok:
        failures.append(("cancelled header", actual, expected))

    actual = gui.stat_tiles(summary)
    expected = [
        ("Scanned", 11, "neutral"),
        ("Failed", 1, "neutral"),
        ("Skipped", 2, "neutral"),
        ("High", 4, "high"),
        ("Medium", 3, "medium"),
        ("Low", 2, "low"),
    ]
    normalized = [(tile["label"], tile["value"], tile["tone"]) for tile in actual]
    ok = normalized == expected
    rows.append(("STAT_TILES", "stable values, order, and tones", normalized, ok))
    if not ok:
        failures.append(("stat tiles", normalized, expected))

    return rows, failures


def _check_auto_shutdown_helpers():
    rows, failures = [], []
    cases = [
        ("client connected, no timer", 1, False, False, "none"),
        ("reconnect cancels timer", 1, False, True, "cancel"),
        ("last client leaves while idle", 0, False, False, "arm"),
        ("idle timer already armed", 0, False, True, "none"),
        ("scan running with no clients", 0, True, False, "defer"),
        ("running scan cancels stale timer", 0, True, True, "cancel"),
    ]
    for label, clients, running, armed, expected in cases:
        actual = gui.auto_shutdown_action(clients, running, armed)
        ok = actual == expected
        rows.append(("AUTO_STOP", label, actual, ok))
        if not ok:
            failures.append((label, actual, expected))
    return rows, failures


def _check_client_guard():
    rows, failures = [], []

    class FakeClient:
        def __init__(self, is_deleted):
            self.is_deleted = is_deleted

    cases = [
        ("live client permits timer-driven refresh", FakeClient(False), True),
        ("deleted client blocks timer-driven refresh", FakeClient(True), False),
        ("missing client blocks timer-driven refresh", None, False),
        ("unknown object fails closed", object(), False),
    ]
    for label, client, expected in cases:
        actual = gui.client_is_active(client)
        ok = actual is expected
        rows.append(("CLIENT", label, actual, ok))
        if not ok:
            failures.append((label, actual, expected))

    return rows, failures


def _check_list_subdirectories():
    rows, failures = [], []

    with tempfile.TemporaryDirectory() as tmp:
        for name in ("zeta", "alpha", "Beta"):
            os.mkdir(os.path.join(tmp, name))
        open(os.path.join(tmp, "not_a_dir.txt"), "w").close()

        actual = gui.list_subdirectories(tmp)
        ok = actual == ["alpha", "Beta", "zeta"]
        rows.append(("SUBDIRS", "only directories, case-insensitive sort, files excluded", actual, ok))
        if not ok:
            failures.append(("listing", actual, ["alpha", "Beta", "zeta"]))

        actual = gui.list_subdirectories(os.path.join(tmp, "does_not_exist"))
        ok = actual == []
        rows.append(("SUBDIRS", "nonexistent path yields empty list, no exception", actual, ok))
        if not ok:
            failures.append(("nonexistent path", actual, []))

    with tempfile.TemporaryDirectory() as tmp:
        for name in ("zeta", "alpha", ".hidden_z", ".hidden_a", "Beta"):
            os.mkdir(os.path.join(tmp, name))

        actual = gui.list_subdirectories(tmp)
        ok = actual == ["alpha", "Beta", "zeta", ".hidden_a", ".hidden_z"]
        rows.append(("SUBDIRS", "dot-prefixed hidden dirs sorted last, still alpha within each group", actual, ok))
        if not ok:
            failures.append(("hidden dirs last", actual, ["alpha", "Beta", "zeta", ".hidden_a", ".hidden_z"]))

    actual = gui.list_subdirectories(os.sep)
    leaked = sorted(set(actual) & gui.CONTAINER_INTERNAL_ROOT_DIRS)
    ok = leaked == []
    rows.append(("SUBDIRS", "container internals hidden from root display", leaked, ok))
    if not ok:
        failures.append(("root display filter", leaked, []))

    return rows, failures


def _check_truncate_middle():
    rows, failures = [], []

    cases = [
        ("short name unchanged", "report.txt", 40, "report.txt"),
        ("exact length boundary unchanged", "a" * 40, 40, "a" * 40),
        ("empty string unchanged", "", 40, ""),
    ]
    for label, name, max_len, expected in cases:
        actual = gui.truncate_middle(name, max_len)
        ok = actual == expected
        rows.append(("TRUNCATE", label, actual, ok))
        if not ok:
            failures.append((label, actual, expected))

    long_name = "a_very_long_filename_that_goes_on_and_on_and_on.pdf"
    actual = gui.truncate_middle(long_name, 20)
    ok = len(actual) == 20 and "…" in actual and actual.startswith("a_very") and actual.endswith(".pdf")
    rows.append(("TRUNCATE", "long name truncated to max_len with middle ellipsis", actual, ok))
    if not ok:
        failures.append(("long name", actual, "len 20, ellipsis in middle"))

    actual = gui.truncate_middle("this_name_is_too_long_for_a_tiny_budget.txt", 1)
    ok = isinstance(actual, str) and len(actual) <= 1
    rows.append(("TRUNCATE", "degenerate max_len=1 never raises", actual, ok))
    if not ok:
        failures.append(("degenerate max_len", actual, "len<=1, no exception"))

    return rows, failures


def _check_cancelled_banner_text():
    rows, failures = [], []

    cases = [
        ("typical partial", 10, 47, "Scan cancelled — partial results (10 of 47 files)"),
        ("cancelled at zero", 0, 5, "Scan cancelled — partial results (0 of 5 files)"),
    ]
    for label, done, total, expected in cases:
        actual = gui.cancelled_banner_text(done, total)
        ok = actual == expected
        rows.append(("CANCEL_BANNER", label, actual, ok))
        if not ok:
            failures.append((label, actual, expected))

    return rows, failures


def _check_current_file_line():
    rows, failures = [], []

    actual = gui.current_file_line(None, None, 1000.0)
    ok = actual is None
    rows.append(("CURRENT_FILE", "no current file -> None", actual, ok))
    if not ok:
        failures.append(("no current file", actual, None))

    actual = gui.current_file_line("report.pdf", 1000.0, 1005.0)
    ok = actual == "Processing: report.pdf"
    rows.append(("CURRENT_FILE", "under threshold -> no large-file hint", actual, ok))
    if not ok:
        failures.append(("under threshold", actual, "Processing: report.pdf"))

    actual = gui.current_file_line("large_test_file.txt", 1000.0, 1020.0)
    ok = actual == "Processing: large_test_file.txt (large file — this can take a while)"
    rows.append(("CURRENT_FILE", "over 15s threshold -> large-file hint appended", actual, ok))
    if not ok:
        failures.append(("over threshold", actual, "large-file hint appended"))

    long_name = "a_very_long_filename_that_goes_on_and_on_and_on_forever.pdf"
    actual = gui.current_file_line(long_name, None, 1000.0, max_len=20)
    ok = actual is not None and "…" in actual and len(actual) < len("Processing: " + long_name)
    rows.append(("CURRENT_FILE", "long filename gets middle-truncated", actual, ok))
    if not ok:
        failures.append(("long filename", actual, "truncated with ellipsis"))

    return rows, failures


def _check_breadcrumb_segments():
    rows, failures = [], []

    actual = gui.breadcrumb_segments("/home/sampleuser/Documents")
    expected = [("/", "/"), ("home", "/home"), ("sampleuser", "/home/sampleuser"), ("Documents", "/home/sampleuser/Documents")]
    ok = actual == expected
    rows.append(("BREADCRUMB", "typical nested path", actual, ok))
    if not ok:
        failures.append(("nested path", actual, expected))

    actual = gui.breadcrumb_segments("/")
    ok = actual == [("/", "/")]
    rows.append(("BREADCRUMB", "root path", actual, ok))
    if not ok:
        failures.append(("root path", actual, [("/", "/")]))

    actual = gui.breadcrumb_segments("/home")
    ok = actual == [("/", "/"), ("home", "/home")]
    rows.append(("BREADCRUMB", "single-level path", actual, ok))
    if not ok:
        failures.append(("single level", actual, [("/", "/"), ("home", "/home")]))

    return rows, failures


def _check_scan_state_transitions():
    rows, failures = [], []

    state = gui.initial_scan_state()
    ok = state["phase"] == "idle" and state["done"] == 0 and state["cancel_event"] is None
    rows.append(("STATE", "initial_scan_state is idle with zeroed progress", state["phase"], ok))
    if not ok:
        failures.append(("initial state", state, "phase=idle"))

    running = gui.running_scan_state(True, now=1000.0)
    ok = (
        running["phase"] == "running"
        and running["verify_on"] is True
        and running["start_time"] == 1000.0
        and running["done"] == 0
    )
    rows.append(("STATE", "running_scan_state resets progress and records start_time", running["phase"], ok))
    if not ok:
        failures.append(("running state", running, "phase=running, start_time=1000.0"))

    progressed = gui.apply_progress(running, 3, 10, current_file="doc.pdf", now=1005.0)
    ok = (
        progressed["done"] == 3
        and progressed["total"] == 10
        and progressed["current_file"] == "doc.pdf"
        and progressed["current_file_started_at"] == 1005.0
        and progressed["submitted_files"] == ["doc.pdf"]
        and running["done"] == 0  # apply_progress must not mutate its input
    )
    rows.append(("STATE", "apply_progress sets current_file, does not mutate input", progressed["done"], ok))
    if not ok:
        failures.append(("apply_progress with current_file", progressed, "done=3, current_file=doc.pdf"))

    progressed_2 = gui.apply_progress(progressed, 4, 10)
    ok = (
        progressed_2["done"] == 4
        and progressed_2["current_file"] == "doc.pdf"  # None means "no new info", keep previous
        and progressed_2["current_file_started_at"] == 1005.0
    )
    rows.append(("STATE", "apply_progress with current_file=None keeps previous file", progressed_2["current_file"], ok))
    if not ok:
        failures.append(("apply_progress without current_file", progressed_2, "current_file unchanged"))

    done = gui.done_scan_state(
        progressed_2,
        cancelled=False,
        summary={"scanned": 4, "duration": 1.0},
        preview=[],
        html_path="/outputs/report.html",
        scan_time="2026-01-01 00:00:00",
    )
    ok = (
        done["phase"] == "done"
        and done["cancelled"] is False
        and done["cancel_event"] is None
        and done["current_file"] is None
        and done["last_html_path"] == "/outputs/report.html"
    )
    rows.append(("STATE", "done_scan_state clears in-flight fields", done["phase"], ok))
    if not ok:
        failures.append(("done state", done, "phase=done, cleared current_file/cancel_event"))

    cancelled_done = gui.done_scan_state(
        progressed_2,
        cancelled=True,
        summary={"scanned": 4, "duration": 1.0},
        preview=[],
        html_path="/outputs/report.html",
        scan_time="2026-01-01 00:00:00",
    )
    ok = cancelled_done["phase"] == "done" and cancelled_done["cancelled"] is True
    rows.append(("STATE", "done_scan_state(cancelled=True) preserves done/total for the banner", cancelled_done["done"], ok and cancelled_done["done"] == 4))
    if not (ok and cancelled_done["done"] == 4):
        failures.append(("cancelled done state", cancelled_done, "cancelled=True, done=4 preserved"))

    errored = gui.error_scan_state(running, "Scan failed: boom")
    ok = errored["phase"] == "error" and errored["error"] == "Scan failed: boom" and errored["cancel_event"] is None
    rows.append(("STATE", "error_scan_state sets phase=error with message", errored["phase"], ok))
    if not ok:
        failures.append(("error state", errored, "phase=error"))

    return rows, failures


def _check_native_folder_dialog():
    rows, failures = [], []

    def _raise_not_found(*a, **k):
        raise FileNotFoundError("no such tool")

    status, path = gui.try_native_folder_dialog(runner=_raise_not_found)
    ok = status == "unavailable" and path is None
    rows.append(("NATIVE", "no zenity/kdialog on system -> unavailable", (status, path), ok))
    if not ok:
        failures.append(("unavailable", (status, path), ("unavailable", None)))

    class _FakeCompleted:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout

    def _fake_selected(cmd, **k):
        return _FakeCompleted(0, "/home/sampleuser/Documents\n")

    status, path = gui.try_native_folder_dialog(runner=_fake_selected)
    ok = status == "selected" and path == "/home/sampleuser/Documents"
    rows.append(("NATIVE", "zenity returns a path -> selected", (status, path), ok))
    if not ok:
        failures.append(("selected", (status, path), ("selected", "/home/sampleuser/Documents")))

    def _fake_cancelled(cmd, **k):
        return _FakeCompleted(1, "")

    status, path = gui.try_native_folder_dialog(runner=_fake_cancelled)
    ok = status == "cancelled" and path is None
    rows.append(("NATIVE", "zenity ran, user cancelled -> cancelled (no fallback)", (status, path), ok))
    if not ok:
        failures.append(("cancelled", (status, path), ("cancelled", None)))

    def _fake_timeout(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 120)

    status, path = gui.try_native_folder_dialog(runner=_fake_timeout)
    ok = status == "unavailable" and path is None
    rows.append(("NATIVE", "dialog tool times out -> treated as unavailable", (status, path), ok))
    if not ok:
        failures.append(("timeout", (status, path), ("unavailable", None)))

    return rows, failures


def _check_windows_bridge_availability():
    rows, failures = [], []
    with tempfile.TemporaryDirectory() as tmp:
        wsl_release = os.path.join(tmp, "wsl-osrelease")
        with open(wsl_release, "w", encoding="utf-8") as fh:
            fh.write("6.6.87.2-microsoft-standard-WSL2\n")
        actual = gui.windows_bridge_available(wsl_release)
        ok = actual is True
        rows.append(("BRIDGE", "WSL kernel release enables Browse (Windows)", actual, ok))
        if not ok:
            failures.append(("wsl osrelease", actual, True))

        plain_release = os.path.join(tmp, "linux-osrelease")
        with open(plain_release, "w", encoding="utf-8") as fh:
            fh.write("6.8.0-generic\n")
        actual = gui.windows_bridge_available(plain_release)
        ok = actual is False
        rows.append(("BRIDGE", "plain Linux kernel release disables Browse (Windows)", actual, ok))
        if not ok:
            failures.append(("plain linux osrelease", actual, False))

        actual = gui.windows_bridge_available(os.path.join(tmp, "missing-osrelease"))
        ok = actual is False
        rows.append(("BRIDGE", "missing osrelease file disables Browse (Windows)", actual, ok))
        if not ok:
            failures.append(("missing osrelease", actual, False))
    return rows, failures


def _check_import_smoke():
    rows, failures = [], []
    ok = hasattr(gui, "index") and callable(gui.index)
    rows.append(("SMOKE", "import gui succeeds, no server started", "ok" if ok else "FAIL", ok))
    if not ok:
        failures.append(("import gui", "index() missing", "index() present"))
    return rows, failures


def run_suite():
    rows, failures = [], []
    for fn in (
        _check_kwargs,
        _check_file_type_helpers,
        _check_layer_helpers,
        _check_paths,
        _check_windows_path_translation,
        _check_browser_home_and_shortcuts,
        _check_fractions,
        _check_phases,
        _check_summarize_report,
        _check_findings_preview,
        _check_recent_folders,
        _check_gui_settings_persistence,
        _check_alarm_details,
        _check_recent_reports,
        _check_output_folder_bridge,
        _check_windows_folder_picker_bridge,
        _check_windows_bridge_availability,
        _check_browser_entries,
        _check_cancel_feedback_helpers,
        _check_settings_summary,
        _check_dashboard_helpers,
        _check_auto_shutdown_helpers,
        _check_client_guard,
        _check_list_subdirectories,
        _check_native_folder_dialog,
        _check_truncate_middle,
        _check_cancelled_banner_text,
        _check_current_file_line,
        _check_breadcrumb_segments,
        _check_scan_state_transitions,
        _check_import_smoke,
    ):
        r, f = fn()
        rows.extend(r)
        failures.extend(f)

    print(f"{'GRP':10} {'CASE':58} {'RESULT':7} {'ACTUAL':30}")
    print("-" * 110)
    for grp, name, actual, ok in rows:
        status = "PASS" if ok else "FAIL"
        print(f"{grp:10} {name:58} {status:7} {str(actual):30}")

    passed = sum(1 for r in rows if r[3])
    failed = len(rows) - passed
    print("-" * 110)
    print(f"SUMMARY: {passed}/{len(rows)} passed, {failed} failed")
    return passed, failed, failures


# ============================================================
#  PYTEST ENTRY POINTS
# ============================================================


def test_build_scan_kwargs():
    _, failures = _check_kwargs()
    assert not failures, failures


def test_file_type_helpers():
    _, failures = _check_file_type_helpers()
    assert not failures, failures


def test_layer_helpers():
    _, failures = _check_layer_helpers()
    assert not failures, failures


def test_html_path_to_json_path():
    _, failures = _check_paths()
    assert not failures, failures


def test_windows_path_translation():
    _, failures = _check_windows_path_translation()
    assert not failures, failures


def test_browser_home_and_shortcuts():
    _, failures = _check_browser_home_and_shortcuts()
    assert not failures, failures


def test_progress_fraction():
    _, failures = _check_fractions()
    assert not failures, failures


def test_infer_phase():
    _, failures = _check_phases()
    assert not failures, failures


def test_summarize_report():
    _, failures = _check_summarize_report()
    assert not failures, failures


def test_extract_findings_preview():
    _, failures = _check_findings_preview()
    assert not failures, failures


def test_recent_folders():
    _, failures = _check_recent_folders()
    assert not failures, failures


def test_gui_settings_persistence():
    _, failures = _check_gui_settings_persistence()
    assert not failures, failures


def test_alarm_details():
    _, failures = _check_alarm_details()
    assert not failures, failures


def test_recent_reports():
    _, failures = _check_recent_reports()
    assert not failures, failures


def test_output_folder_bridge():
    _, failures = _check_output_folder_bridge()
    assert not failures, failures


def test_windows_folder_picker_bridge():
    _, failures = _check_windows_folder_picker_bridge()
    assert not failures, failures


def test_browser_entries():
    _, failures = _check_browser_entries()
    assert not failures, failures


def test_cancel_feedback_helpers():
    _, failures = _check_cancel_feedback_helpers()
    assert not failures, failures


def test_settings_summary():
    _, failures = _check_settings_summary()
    assert not failures, failures


def test_dashboard_helpers():
    _, failures = _check_dashboard_helpers()
    assert not failures, failures


def test_auto_shutdown_helpers():
    _, failures = _check_auto_shutdown_helpers()
    assert not failures, failures


def test_client_guard():
    _, failures = _check_client_guard()
    assert not failures, failures


def test_list_subdirectories():
    _, failures = _check_list_subdirectories()
    assert not failures, failures


def test_native_folder_dialog():
    _, failures = _check_native_folder_dialog()
    assert not failures, failures


def test_truncate_middle():
    _, failures = _check_truncate_middle()
    assert not failures, failures


def test_cancelled_banner_text():
    _, failures = _check_cancelled_banner_text()
    assert not failures, failures


def test_current_file_line():
    _, failures = _check_current_file_line()
    assert not failures, failures


def test_breadcrumb_segments():
    _, failures = _check_breadcrumb_segments()
    assert not failures, failures


def test_scan_state_transitions():
    _, failures = _check_scan_state_transitions()
    assert not failures, failures


def test_import_smoke():
    _, failures = _check_import_smoke()
    assert not failures, failures


if __name__ == "__main__":
    _, failed, _ = run_suite()
    sys.exit(1 if failed else 0)
