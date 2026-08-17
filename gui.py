#!/usr/bin/env python3
# gui.py
"""SecureScan — thin local NiceGUI front end over discovery.scan_path()."""

import base64
import glob
import json
import os
import posixpath
import re
import subprocess
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from nicegui import app, run, ui

import config
import discovery
from detectors.hybrid_detector import ALL_LAYERS
from report_generator import _all_findings, _risk_of, _safe_score, _RISK_BADGE

_scan_lock = threading.Lock()

STATE_PATH = os.path.join(discovery.EXCLUDED_REPORT_OUTPUT_DIR, ".gui_state.json")
BROWSER_SHORTCUTS_PATH = os.environ.get(
    "SECURESCAN_BROWSER_SHORTCUTS",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_shortcuts.json"),
)
MAX_RECENT_FOLDERS = 5
WINDOWS_USERS_ROOT = "/mnt/c/Users"
WINDOWS_PROFILE_PLACEHOLDER = "{windows_profile}"
WINDOWS_PROFILE_DIR_NAMES = ("Desktop", "Downloads", "Documents")
NON_USER_WINDOWS_PROFILES = {
    "all users",
    "default",
    "default user",
    "public",
}
CONTAINER_INTERNAL_ROOT_DIRS = {
    "app",
    "bin",
    "boot",
    "dev",
    "etc",
    "home",
    "lib",
    "lib64",
    "media",
    "opt",
    "proc",
    "root",
    "run",
    "sbin",
    "srv",
    "sys",
    "tmp",
    "usr",
    "var",
}
DEFAULT_BROWSER_SHORTCUTS = [
    {
        "label": "Desktop",
        "path": f"{WINDOWS_PROFILE_PLACEHOLDER}/Desktop",
        "group": "folders",
    },
    {
        "label": "Downloads",
        "path": f"{WINDOWS_PROFILE_PLACEHOLDER}/Downloads",
        "group": "folders",
    },
    {
        "label": "Documents",
        "path": f"{WINDOWS_PROFILE_PLACEHOLDER}/Documents",
        "group": "folders",
    },
    {"label": "C:", "path": "/mnt/c", "group": "drives"},
    {"label": "D:", "path": "/mnt/d", "group": "drives"},
    {"label": "Demo", "path": "/mnt/demo", "group": "custom"},
]
BROWSER_SHORTCUT_GROUPS = {
    "folders": {"label": "User folders", "icon": "folder_special"},
    "drives": {"label": "Drives", "icon": "storage"},
    "custom": {"label": "Custom", "icon": "bookmark_outline"},
}
# Reports are served over HTTP (see the app.add_static_files() registration
# below) instead of launched via webbrowser.open()/xdg-open, which only ever
# affected the machine running the server process — a no-op in a container,
# and not even meaningful bare-host once the viewing browser tab isn't
# necessarily on the same machine as the process that opened it.
OUTPUTS_URL_PATH = "/outputs"
GUI_HOST = os.environ.get("SECURESCAN_GUI_HOST", "127.0.0.1")
OUTPUT_FOLDER_REQUEST_FILENAME = ".open-output-folder.request"
OUTPUT_FOLDER_ACK_FILENAME = ".open-output-folder.ack"
OUTPUT_FOLDER_HELPER_TIMEOUT_S = 10.0
WINDOWS_FOLDER_PICKER_REQUEST_FILENAME = ".windows-folder-picker.request"
WINDOWS_FOLDER_PICKER_RESPONSE_FILENAME = ".windows-folder-picker.response"
WINDOWS_FOLDER_PICKER_HELPER_TIMEOUT_S = 10.0
WINDOWS_FOLDER_PICKER_TIMEOUT_S = 600.0
LARGE_FILE_THRESHOLD_S = 15.0
CURRENT_FILE_MAX_LEN = 40
AUTO_SHUTDOWN_GRACE_S = 75.0
FILE_TYPE_GROUPS = {
    "Text & code": (".txt", ".md", ".csv", ".json", ".log", ".py"),
    "Word": (".docx",),
    "Spreadsheets": (".xlsx",),
    "PDF": (".pdf",),
    "Images": (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif", ".webp"),
    "Email": (".eml",),
    "Presentations": (".pptx",),
}
FILE_TYPE_LABELS = tuple(FILE_TYPE_GROUPS)
LEGACY_FILE_TYPE_LABELS = {**FILE_TYPE_GROUPS, "Word docs": FILE_TYPE_GROUPS["Word"]}
FILE_TYPE_EXTENSIONS = tuple(
    extension
    for extensions in FILE_TYPE_GROUPS.values()
    for extension in extensions
)
# Selectable detection layers, read from detectors.hybrid_detector.ALL_LAYERS
# (itself derived from SOURCE_PRIORITY — see that module) rather than
# hand-duplicated here, so a newly added detector becomes selectable in the
# GUI automatically.
DETECTION_LAYERS = tuple(sorted(ALL_LAYERS))
DEFAULT_GUI_SETTINGS = {
    "verify_on": False,
    "run_ner_on": True,
    "max_workers": config.DEFAULT_MAX_WORKERS,
    "ocr_workers": 4,
    "ner_max_chars": config.NER_MAX_CHARS,
    "file_types": list(FILE_TYPE_EXTENSIONS),
    "layers": list(DETECTION_LAYERS),
}
TICKER_MAX_ROWS = 40

_gui_state_file_lock = threading.Lock()
_browser_shortcuts_file_lock = threading.Lock()
_output_folder_request_lock = threading.Lock()
_windows_folder_picker_request_lock = threading.Lock()
_client_lifecycle_lock = threading.RLock()
_connected_client_ids: set[str] = set()
_auto_shutdown_timer: Optional[threading.Timer] = None

RISK_COLOR = {
    "HIGH": "red",
    "MEDIUM": "orange",
    "LOW": "green",
    "NONE": "grey",
    "UNKNOWN": "grey",
}

PHASE_LABELS = {
    "discovery": "Discovering files…",
    "scanning": "Scanning files…",
    "ai_verification": "Running AI verification…",
    "writing_reports": "Writing reports…",
    "done": "Done",
}

THEME_CSS = """
:root {
  --secure-blue: #5898d4;
  --secure-surface: rgba(17, 24, 39, 0.94);
  --secure-line: rgba(148, 163, 184, 0.16);
}
body {
  background: linear-gradient(180deg, #111827 0%, #0b1220 58%, #090e17 100%);
  min-height: 100vh;
}
.nicegui-content {
  position: relative;
  z-index: 1;
  padding: 0 !important;
}
.secure-appbar {
  background: rgba(11, 18, 32, 0.96) !important;
  border-bottom: 1px solid var(--secure-line);
  backdrop-filter: blur(14px);
  padding: 0 !important;
}
.appbar-inner {
  width: 100%;
  max-width: 1200px;
  margin: 0 auto;
  padding: 12px 24px;
}
.dashboard-shell {
  width: calc(100% - 48px);
  max-width: 1200px;
  margin: 24px auto;
  align-items: stretch;
  gap: 16px;
}
.controls-card {
  width: 400px;
  flex: 0 0 400px;
  transition: width 180ms ease, flex-basis 180ms ease;
}
.results-card {
  min-width: 0;
  flex: 1 1 auto;
}
.advanced-layout,
.file-types-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
}
.advanced-col {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.file-type-families {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.file-type-family {
  width: 100%;
  min-width: 0;
  padding: 8px 10px;
  background: var(--secure-line);
  border: 1px solid var(--secure-line);
  border-radius: 8px;
}
.file-type-family-wide {
  grid-column: 1 / -1;
}
/* Detection-layer checkbox grid: dedicated class (not shared with
   .file-type-grid, which is tuned for short ".ext" labels). auto-fit with a
   180px floor comfortably fits the longest current label ("keyword_context",
   15 chars) plus headroom for a longer future one, at any container width —
   this is the fix for labels overlapping when squeezed into a narrow track. */
.layer-grid {
  display: grid !important;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 4px 12px;
  width: 100%;
}
.layer-grid .q-checkbox {
  min-height: 30px;
}
.layer-grid .q-checkbox__inner--truthy {
  color: var(--secure-blue) !important;
}
.layer-grid .q-checkbox__label {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
    "Liberation Mono", "Courier New", monospace;
  font-size: 12px;
  white-space: nowrap;
}
/* Advanced, expanded while idle: the results card is empty at idle (just a
   "Ready to scan" placeholder), so it is hidden and the controls card takes
   the full dashboard width. File types and Detection layers — both "what
   gets scanned" filters — sit side by side as a peer pair in their own row,
   below the worker/limit settings row, so nothing needs vertical scrolling
   to reach the Scan button at 1920x1080/100% zoom. */
.dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) {
  max-width: 1600px;
  margin-top: 4px;
}
.dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) > .controls-card {
  width: 100%;
  flex-basis: 100%;
  padding: 10px !important;
}
.dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) > .results-card {
  display: none;
}
.dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) .advanced-layout {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  grid-template-areas:
    "workers limits"
    "filetypes layers";
  gap: 8px;
  align-items: start;
}
.dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) .advanced-col {
  gap: 4px;
}
.dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) .file-type-family {
  padding: 6px 8px;
}
.dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) .advanced-workers {
  grid-area: workers;
}
.dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) .advanced-limits {
  grid-area: limits;
}
.dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) .panel-filetypes {
  grid-area: filetypes;
}
.dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) .panel-layers {
  grid-area: layers;
}
.dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) .file-type-families {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  align-items: start;
}
.dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) .file-type-grid {
  grid-template-columns: repeat(auto-fit, minmax(62px, 1fr));
}
.dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) .layer-grid {
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
}
.dashboard-card {
  min-height: 650px;
  background: var(--secure-surface) !important;
  border: 1px solid var(--secure-line);
  border-radius: 12px !important;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.18);
  padding: 24px !important;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
.secure-dialog {
  background: #111827 !important;
  border: 1px solid var(--secure-line);
  border-radius: 12px !important;
  padding: 24px !important;
}
.mono-display,
.q-table tbody td:first-child,
.q-table tbody td:nth-child(3) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
    "Liberation Mono", "Courier New", monospace;
}
.secure-header {
  color: #f8fafc;
  letter-spacing: -0.025em;
}
.q-toggle__inner--truthy {
  color: #64748b !important;
}
.q-slider__track-container--h,
.q-slider__thumb {
  color: #64748b !important;
}
.q-expansion-item .q-icon {
  color: #94a3b8;
}
.secure-link {
  color: var(--secure-blue) !important;
}
.next-scan-summary {
  color: #cbd5e1;
  border-left: 2px solid #475569;
  padding-left: 0.65rem;
}
.control-divider {
  background: var(--secure-line) !important;
  margin: 4px 0;
}
.control-helper {
  color: #94a3b8;
  font-size: 12px;
  line-height: 1.25;
  margin: -6px 0 2px 42px;
}
.file-type-header {
  color: #cbd5e1;
  font-size: 12px;
  font-weight: 600;
}
.file-type-grid {
  display: grid !important;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0 8px;
  width: 100%;
}
.file-type-grid .q-checkbox {
  min-height: 30px;
}
.file-type-family .q-checkbox__inner--truthy {
  color: var(--secure-blue) !important;
}
.file-type-grid .q-checkbox__label {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
    "Liberation Mono", "Courier New", monospace;
  font-size: 12px;
}
.q-btn {
  min-height: 40px;
}
.empty-state, .running-state {
  min-height: 590px;
  align-items: center;
  justify-content: center;
  text-align: center;
}
.empty-shield {
  width: 72px;
  height: 72px;
  color: #64748b;
}
.running-progress {
  width: min(560px, 90%);
  height: 12px !important;
  border-radius: 999px;
}
.stat-grid {
  display: grid !important;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  width: 100%;
}
.stat-tile {
  border: 1px solid var(--secure-line);
  border-radius: 10px;
  padding: 12px 10px;
  background: rgba(30, 41, 59, 0.56);
}
.stat-tile.high {background: rgba(244, 67, 54, .10); border: 2px solid #f44336}
.stat-tile.medium {background: rgba(255, 152, 0, .10); border: 2px solid #ff9800}
.stat-tile.low {background: rgba(76, 175, 80, .10); border: 2px solid #4caf50}
.stat-tile.high .stat-number {color: #f44336}
.stat-tile.medium .stat-number {color: #ff9800}
.stat-tile.low .stat-number {color: #4caf50}
.stat-number {font-size: 26px; font-weight: 700; line-height: 1.1}
.stat-label {font-size: 11px; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em}
.alarm-strip {
  width: 100%;
  padding: 10px 14px;
  border-radius: 8px;
  background: rgba(245, 166, 35, .12);
  border: 1px solid rgba(245, 166, 35, .28);
  color: #fbbf24;
}
.alarm-row {
  border-top: 1px solid rgba(245, 166, 35, .20);
  padding: 9px 2px 3px;
}
.alarm-trigger {
  background: rgba(245, 166, 35, .18) !important;
  color: #fbbf24 !important;
  border: 1px solid rgba(245, 166, 35, .36);
}
.recent-report-row {
  border-top: 1px solid var(--secure-line);
  padding: 6px 0;
}
.browser-shortcuts {
  border: 1px solid var(--secure-line);
  border-radius: 8px;
  padding: 8px 10px;
  background: rgba(30, 41, 59, 0.34);
}
.browser-shortcut-group {
  padding-right: 10px;
  border-right: 1px solid var(--secure-line);
}
.browser-shortcut-group:last-child {
  padding-right: 0;
  border-right: 0;
}
.browser-shortcut-main {
  min-width: 112px;
}
.browser-shortcut-remove {
  min-width: 30px !important;
  width: 30px;
  padding: 0 !important;
  color: #94a3b8 !important;
}
.browser-shortcut-action {
  color: #94a3b8 !important;
}
.top-files-table tbody tr:nth-child(even) {background: rgba(148, 163, 184, .04)}
.top-files-table tbody tr:hover {background: rgba(148, 163, 184, .10)}
.top-files-table table {width: 100%; table-layout: fixed}
.top-files-table th:nth-child(1) {width: 46%}
.top-files-table th:nth-child(2) {width: 14%}
.top-files-table th:nth-child(3) {width: 9%}
.top-files-table th:nth-child(4) {width: 31%}
.top-files-table td {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
@media (max-width: 900px) {
  .dashboard-shell {flex-direction: column; width: calc(100% - 32px); margin: 16px auto}
  .dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) {
    max-width: 1200px;
  }
  .controls-card {width: 100%; flex-basis: auto}
  .dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) > .controls-card {
    width: 100%;
    flex-basis: auto;
  }
  .dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) .advanced-layout {
    display: flex;
  }
  .dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) .file-type-families {
    display: flex;
  }
  .dashboard-shell:has(.results-card .idle-state):has(.advanced-controls .q-item[aria-expanded="true"]) .file-type-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .dashboard-card {min-height: auto}
  .stat-grid {grid-template-columns: repeat(3, 1fr)}
}
.ticker-scroll {
  width: 100%;
  max-height: 260px;
  border: 1px solid var(--secure-line);
  border-radius: 8px;
  padding: 2px 10px;
}
.ticker-row {
  padding: 6px 0;
  border-bottom: 1px solid var(--secure-line);
}
.ticker-row:last-child {
  border-bottom: none;
}
.ticker-empty {
  color: #64748b;
  font-size: 13px;
  padding: 8px 2px;
}
"""

# -------------------------------------------------------------------
# Module-level scan state store (owned by the background scan thread).
#
# A page reload or second browser tab creates a separate client connection.
# Keep scan state at module scope so every client renders the active scan
# through its own poll timer instead of displaying an independent idle state.
# _scan_lock (above) remains the single-scan invariant: only the client
# that wins the lock starts the background thread that owns writes to
# _scan_state; every other client (including a reconnecting one) only
# ever reads it.
# -------------------------------------------------------------------
_scan_state_lock = threading.Lock()


def initial_scan_state() -> Dict[str, Any]:
    """Fresh idle state — used at module load and as the base for a new run."""
    return {
        "phase": "idle",  # idle | running | done | error
        "done": 0,
        "total": 0,
        "current_file": None,
        "current_file_started_at": None,
        "submitted_files": [],
        "ticker": [],
        "verify_on": False,
        "start_time": None,
        "summary": None,
        "preview": [],
        "alarms": [],
        "error": None,
        "cancelled": False,
        "cancel_requested": False,
        "last_scan_time": None,
        "last_html_path": None,
        "cancel_event": None,
    }


_scan_state: Dict[str, Any] = initial_scan_state()


# -------------------------------------------------------------------
# Pure helpers (no NiceGUI/UI dependency — table-tested in
# tests/test_gui_logic.py)
# -------------------------------------------------------------------
def normalize_file_types(value: Any) -> List[str]:
    """Return persisted/UI extensions in canonical display order.

    v2.4 saved family labels; expand those values during the v2.4.1 migration.
    Missing or unrecognized state continues to fail safe to every extension.
    """
    if not isinstance(value, (list, tuple, set)):
        return list(FILE_TYPE_EXTENSIONS)
    if all(item in LEGACY_FILE_TYPE_LABELS for item in value):
        selected_extensions = {
            extension
            for family in value
            for extension in LEGACY_FILE_TYPE_LABELS[family]
        }
        return [
            extension
            for extension in FILE_TYPE_EXTENSIONS
            if extension in selected_extensions
        ]
    if any(item not in FILE_TYPE_EXTENSIONS for item in value):
        return list(FILE_TYPE_EXTENSIONS)
    selected = set(value)
    return [extension for extension in FILE_TYPE_EXTENSIONS if extension in selected]


def extensions_for_file_types(file_types: Any) -> set[str] | None:
    """Map checkbox state to discovery extensions; all checked means default."""
    selected = normalize_file_types(file_types)
    if selected == list(FILE_TYPE_EXTENSIONS):
        return None
    return set(selected)


def toggle_file_type_family(file_types: Any, family: str) -> List[str]:
    """Toggle one family's checkbox selection between all and none."""
    selected = set(normalize_file_types(file_types))
    family_extensions = FILE_TYPE_GROUPS.get(family)
    if family_extensions is None:
        return list(FILE_TYPE_EXTENSIONS)
    if all(extension in selected for extension in family_extensions):
        selected.difference_update(family_extensions)
    else:
        selected.update(family_extensions)
    return [
        extension for extension in FILE_TYPE_EXTENSIONS if extension in selected
    ]


def toggle_all_file_types(file_types: Any) -> List[str]:
    """Toggle the global checkbox selection between all and none."""
    selected = normalize_file_types(file_types)
    return [] if selected == list(FILE_TYPE_EXTENSIONS) else list(FILE_TYPE_EXTENSIONS)


def normalize_layers(value: Any) -> List[str]:
    """Return persisted/UI detection layers in canonical (sorted) order.

    Mirrors normalize_file_types(): missing or unrecognized state fails safe
    to every layer, so a bad/old .gui_state.json can never silently narrow a
    scan.
    """
    if not isinstance(value, (list, tuple, set)):
        return list(DETECTION_LAYERS)
    if any(item not in DETECTION_LAYERS for item in value):
        return list(DETECTION_LAYERS)
    selected = set(value)
    return [layer for layer in DETECTION_LAYERS if layer in selected]


def layers_for_scan(layers: Any) -> Optional[frozenset]:
    """Map checkbox state to detect_pii_hybrid's enabled_layers; all checked
    means default (None), mirroring extensions_for_file_types()."""
    selected = normalize_layers(layers)
    if selected == list(DETECTION_LAYERS):
        return None
    return frozenset(selected)


def toggle_all_layers(layers: Any) -> List[str]:
    """Toggle the global layer-checkbox selection between all and none."""
    selected = normalize_layers(layers)
    return [] if selected == list(DETECTION_LAYERS) else list(DETECTION_LAYERS)


def build_scan_kwargs(
    verify_on: bool,
    run_ner_on: bool,
    max_workers: int,
    ocr_workers: int,
    max_file_size_mb: float,
    ner_max_chars: int = config.NER_MAX_CHARS,
    file_types: Any = None,
    layers: Any = None,
) -> Dict[str, Any]:
    """Map every wired control's form state to real scan_folder() kwargs.

    Every value maps to a real scan_path()/scan_folder() parameter; nothing
    here is decorative. Selecting every extension deliberately maps to
    extensions=None (and every layer to enabled_layers=None) so the engine's
    established default path is preserved.
    """
    return {
        "verify": bool(verify_on),
        "run_ner": bool(run_ner_on),
        "max_workers": int(max_workers),
        "ocr_workers": int(ocr_workers),
        "max_file_size_mb": float(max_file_size_mb),
        "ner_max_chars": int(ner_max_chars),
        "extensions": extensions_for_file_types(file_types),
        "enabled_layers": layers_for_scan(layers),
    }


def html_path_to_json_path(html_path: str) -> str:
    """Derive the sibling .json report path discovery.py wrote alongside the HTML one."""
    root, _ext = os.path.splitext(html_path)
    return root + ".json"


def progress_fraction(done: int, total: int) -> float:
    """0..1 fraction for ui.linear_progress; total<=0 is "not started yet"."""
    if not total or total <= 0:
        return 0.0
    return max(0.0, min(1.0, done / total))


def infer_phase(done: int, total: int, scan_finished: bool, verify_on: bool) -> str:
    """Infer the current phase from client-visible scan state.

    scan_folder() calls progress_callback(0, total) immediately after
    discovery + size-filtering, so "discovery" only covers the brief window
    before that first call arrives. Once done==total, the thread-pool pass
    is over but the awaited scan_folder() call hasn't returned yet — the
    only work left is the sequential LLM verification pass (if verify_on)
    or just writing the three report files (if not).
    """
    if scan_finished:
        return "done"
    if total <= 0:
        return "discovery"
    if done < total:
        return "scanning"
    return "ai_verification" if verify_on else "writing_reports"


def truncate_middle(name: str, max_len: int = CURRENT_FILE_MAX_LEN) -> str:
    """Middle-ellipsis truncation for a long filename, e.g. 'a_very_long_fil…es_on.pdf'.

    Names at or under max_len are returned unchanged. Degenerate max_len
    values (<=1) never raise.
    """
    if not name or len(name) <= max_len:
        return name
    if max_len <= 1:
        return name[:max_len]
    keep = max_len - 1  # reserve one character for the ellipsis itself
    head = keep - keep // 2
    tail = keep // 2
    return name[:head] + "…" + (name[-tail:] if tail else "")


def current_file_line(
    current_file: Optional[str],
    started_at: Optional[float],
    now: float,
    max_len: int = CURRENT_FILE_MAX_LEN,
    threshold_s: float = LARGE_FILE_THRESHOLD_S,
) -> Optional[str]:
    """"Processing: <basename>" line text, or None when nothing is in flight.

    Appends "(large file — this can take a while)" once `now - started_at`
    exceeds threshold_s, so a single big file mid-scan doesn't read as a
    frozen UI.
    """
    if not current_file:
        return None
    line = f"Processing: {truncate_middle(current_file, max_len)}"
    if started_at is not None and (now - started_at) > threshold_s:
        line += " (large file — this can take a while)"
    return line


def cancelled_banner_text(done: int, total: int) -> str:
    """Banner text for a cancelled scan's partial-results done screen."""
    return f"Scan cancelled — partial results ({done} of {total} files)"


def in_flight_names(submitted_files: List[str], done: int) -> List[str]:
    """Tracked basenames still in the submitted-minus-done window."""
    names = [os.path.basename(str(name)) for name in submitted_files if name]
    in_flight_count = max(0, len(names) - max(0, int(done)))
    return names[-in_flight_count:] if in_flight_count else []


def cancelling_status_line(submitted_files: List[str], done: int) -> str:
    """Human-readable pending-cancel status from the existing progress state."""
    names = in_flight_names(submitted_files, done)
    displayed = ", ".join(truncate_middle(name, 28) for name in names) or "none"
    return (
        f"Cancelling — waiting for {len(names)} in-flight file(s): {displayed}"
    )


def settings_summary(
    verify_on: bool,
    run_ner_on: bool,
    workers: int,
    ner_max_chars: int = config.NER_MAX_CHARS,
    file_types: Any = None,
    layers: Any = None,
) -> str:
    """One-line preview of the controls that will drive the next scan."""
    summary = (
        f"Next scan: verification {'ON' if verify_on else 'OFF'} · "
        f"NER {'ON' if run_ner_on else 'OFF'} · {int(workers)} workers"
    )
    if int(ner_max_chars) != config.NER_MAX_CHARS:
        summary += f" · NER cap {int(ner_max_chars):,}"
    selected_types = normalize_file_types(file_types)
    if selected_types != list(FILE_TYPE_EXTENSIONS):
        if selected_types:
            visible = selected_types[:6]
            remainder = len(selected_types) - len(visible)
            extension_list = ", ".join(visible)
            if remainder:
                extension_list += f" +{remainder} more"
            summary += f" · file types {extension_list}"
        else:
            summary += " · file types none"
    selected_layers = normalize_layers(layers)
    if selected_layers != list(DETECTION_LAYERS):
        if selected_layers:
            summary += f" · layers {', '.join(selected_layers)}"
        else:
            summary += " · layers none"
    return summary


def dashboard_state(phase: str, cancelled: bool = False) -> str:
    """Select the one results-column composition to render."""
    if phase == "done" and cancelled:
        return "cancelled"
    return phase if phase in {"idle", "running", "done", "error"} else "error"


def client_is_active(client: Any) -> bool:
    """Whether a captured NiceGUI page client still exists.

    A hard reload deletes the old page client after its reconnect timeout.
    Scan-state timers can race that teardown, so every timer-driven render
    checks this helper before touching elements owned by the old client.
    """
    return client is not None and not bool(
        getattr(client, "is_deleted", True)
    )


def auto_shutdown_action(
    connected_clients: int,
    scan_running: bool,
    timer_armed: bool,
) -> str:
    """Choose the auto-shutdown timer transition for the current GUI state."""
    if connected_clients > 0:
        return "cancel" if timer_armed else "none"
    if scan_running:
        return "cancel" if timer_armed else "defer"
    return "none" if timer_armed else "arm"


def completion_header(
    summary: Dict[str, Any], done: int, total: int, cancelled: bool
) -> str:
    """Mutually-exclusive done/cancelled heading (never both)."""
    if cancelled:
        return cancelled_banner_text(done, total)
    return f"Scan complete in {float(summary.get('duration', 0.0)):.1f}s"


def stat_tiles(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Dashboard stat-tile rows in stable display order."""
    return [
        {"label": "Scanned", "value": int(summary.get("scanned", 0)), "tone": "neutral"},
        {"label": "Failed", "value": int(summary.get("failed", 0)), "tone": "neutral"},
        {"label": "Skipped", "value": int(summary.get("skipped", 0)), "tone": "neutral"},
        {"label": "High", "value": int(summary.get("high", 0)), "tone": "high"},
        {"label": "Medium", "value": int(summary.get("medium", 0)), "tone": "medium"},
        {"label": "Low", "value": int(summary.get("low", 0)), "tone": "low"},
    ]


def breadcrumb_segments(path: str) -> List[Tuple[str, str]]:
    """path -> [(label, full_path_up_to_and_including_label), ...] breadcrumb trail.

    '/home/user/Documents' -> [('/', '/'), ('home', '/home'),
    ('user', '/home/user'), ('Documents', '/home/user/Documents')].
    """
    norm = os.path.normpath(path) if path else os.sep
    if norm == os.sep:
        return [(os.sep, os.sep)]
    parts = norm.strip(os.sep).split(os.sep)
    segments = [(os.sep, os.sep)]
    accum = ""
    for part in parts:
        accum = accum + os.sep + part
        segments.append((part, accum))
    return segments


def running_scan_state(verify_on: bool, now: Optional[float] = None) -> Dict[str, Any]:
    """State for a freshly-started scan (starting point for the background thread)."""
    state = initial_scan_state()
    state.update(
        phase="running",
        verify_on=bool(verify_on),
        start_time=now if now is not None else time.time(),
    )
    return state


def apply_progress(
    state: Dict[str, Any],
    done: int,
    total: int,
    current_file: Optional[str] = None,
    now: Optional[float] = None,
    completed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pure progress update: returns a NEW dict, never mutates `state`.

    current_file=None means "no new information" (the once-per-completion
    progress_callback call) — the previous current_file/started_at are kept
    rather than cleared, matching discovery.scan_folder()'s progress_callback
    contract (see its docstring).

    completed: an optional {"file", "score", "risk", "top_type",
    "finding_count"} row for a just-finished file (see discovery.py's
    progress_callback docstring) — prepended to the live findings ticker,
    newest first, capped at TICKER_MAX_ROWS. None (submission calls, and
    completions of files that didn't scan cleanly) leaves the ticker as-is.
    """
    updated = dict(state)
    updated["done"] = done
    updated["total"] = total
    if current_file is not None:
        updated["current_file"] = current_file
        updated["current_file_started_at"] = now if now is not None else time.time()
        updated["submitted_files"] = [
            *state.get("submitted_files", []),
            os.path.basename(current_file),
        ]
    if completed is not None:
        updated["ticker"] = [completed, *state.get("ticker", [])][:TICKER_MAX_ROWS]
    return updated


def done_scan_state(
    state: Dict[str, Any],
    *,
    cancelled: bool,
    summary: Dict[str, Any],
    preview: List[Dict[str, Any]],
    html_path: str,
    scan_time: str,
    alarms: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """State transition for a scan that finished (normally or cancelled)."""
    updated = dict(state)
    updated.update(
        phase="done",
        error=None,
        cancelled=bool(cancelled),
        summary=summary,
        preview=preview,
        alarms=list(alarms or []),
        last_html_path=html_path,
        last_scan_time=scan_time,
        cancel_event=None,
        cancel_requested=False,
        current_file=None,
        current_file_started_at=None,
    )
    return updated


def error_scan_state(state: Dict[str, Any], message: str) -> Dict[str, Any]:
    """State transition for a scan that failed outright."""
    updated = dict(state)
    updated.update(
        phase="error",
        error=message,
        cancel_event=None,
        cancel_requested=False,
        current_file=None,
        current_file_started_at=None,
    )
    return updated


def summarize_report(json_path: str) -> Dict[str, Any]:
    """Read the scan's own JSON report and pull the display summary from it.

    Never recomputes counts — every field here is copied straight out of the
    JSON discovery.scan_folder() already wrote. Returns {"error": ...} on any
    read/parse failure so the GUI can show a message instead of crashing.
    """
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            report = json.load(fh)
    except Exception as e:
        return {"error": f"Could not read report JSON: {e}"}

    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    alarms = report.get("mismatch_alarms", []) if isinstance(report, dict) else []
    return {
        "scanned": summary.get("scanned", 0),
        "failed": summary.get("failed", 0),
        "skipped": summary.get("skipped", 0),
        "high": summary.get("high_risk", 0),
        "medium": summary.get("medium_risk", 0),
        "low": summary.get("low_risk", 0),
        "alarms": len(alarms) if isinstance(alarms, list) else 0,
        "cancelled": bool(summary.get("scan_cancelled", False)),
    }


def extract_findings_preview(json_path: str, top_n: int = 10) -> List[Dict[str, Any]]:
    """Top-N scanned files by score, TYPE only — never a finding value.

    Reuses report_generator's own risk-bucketing/finding-flattening helpers
    (_safe_score/_risk_of/_all_findings) rather than re-deriving score bands
    here, per this repo's "don't re-derive risk bucketing locally" rule.
    """
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            report = json.load(fh)
    except Exception:
        return []

    files = report.get("files", []) if isinstance(report, dict) else []
    rows = []
    for entry in files:
        if not isinstance(entry, dict) or entry.get("scan_status") != "scanned":
            continue
        score = _safe_score(entry)
        risk = _risk_of(score, entry.get("scan_status", "scanned"))
        findings = _all_findings(entry)
        top_type = findings[0]["type"] if findings else "—"
        rows.append(
            {
                "file": os.path.basename(str(entry.get("file", ""))),
                "risk": risk,
                "risk_badge": _RISK_BADGE.get(risk, "⚪"),
                "risk_color": RISK_COLOR.get(risk, "grey"),
                "score": score,
                "top_type": top_type,
            }
        )
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:top_n]


def extract_alarm_details(json_path: str) -> List[Dict[str, Any]]:
    """Read display-safe silent-miss rows from a scan's JSON report."""
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            report = json.load(fh)
    except Exception:
        return []
    raw_alarms = report.get("mismatch_alarms", []) if isinstance(report, dict) else []
    rows = []
    for alarm in raw_alarms if isinstance(raw_alarms, list) else []:
        if not isinstance(alarm, dict):
            continue
        triggered_by = str(alarm.get("triggered_by", ""))
        triggers = (
            ["filename", "content"]
            if triggered_by == "both"
            else [part for part in triggered_by.split("+") if part]
        )
        triggers = [
            trigger
            for trigger in triggers
            if trigger in {"filename", "content", "face", "unreadable"}
        ]
        rows.append(
            {
                "file": os.path.basename(str(alarm.get("file", ""))),
                "triggers": triggers,
                "reason": " ".join(str(alarm.get("reason", "")).split()),
            }
        )
    return rows


def _collect_report_candidates(directory: str) -> List[Tuple[int, Dict[str, str]]]:
    """report_*.html sets directly inside `directory` (non-recursive)."""
    candidates = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    if (
                        entry.is_file()
                        and entry.name.startswith("report_")
                        and entry.name.endswith(".html")
                        and os.path.isfile(entry.path[:-5] + ".json")
                        and os.path.isfile(entry.path[:-5] + ".md")
                    ):
                        stem = entry.name[:-5]
                        candidates.append(
                            (
                                entry.stat().st_mtime_ns,
                                {
                                    "name": stem.removeprefix("report_"),
                                    "html_path": os.path.abspath(entry.path),
                                },
                            )
                        )
                except OSError:
                    continue
    except OSError:
        return []
    return candidates


def list_recent_reports(
    output_dir: str = discovery.EXCLUDED_REPORT_OUTPUT_DIR,
    limit: int = 5,
) -> List[Dict[str, str]]:
    """Newest complete report sets, represented by their HTML entry point.

    Reports live directly under `output_dir` (flat — e.g. a caller-provided
    fixture) OR grouped under its dated subdirectories (outputs/YYYY-MM-DD/,
    see discovery._dated_output_dir()) — both are searched, one level deep
    only, so this never recurses into the unrelated logs/ subdirectory.
    """
    candidates = _collect_report_candidates(output_dir)
    try:
        with os.scandir(output_dir) as entries:
            subdirs = [entry.path for entry in entries if entry.is_dir()]
    except OSError:
        subdirs = []
    for subdir in subdirs:
        candidates.extend(_collect_report_candidates(subdir))
    candidates.sort(key=lambda item: (item[0], item[1]["html_path"]), reverse=True)
    return [row for _mtime, row in candidates[: max(0, int(limit))]]


def has_latest_report(output_dir: str = discovery.EXCLUDED_REPORT_OUTPUT_DIR) -> bool:
    """Whether at least one uniquely named, complete report set exists."""
    return bool(list_recent_reports(output_dir=output_dir, limit=1))


def gui_bind_label(host: str = GUI_HOST) -> str:
    """Human-readable header label derived from the actual NiceGUI bind."""
    scope = "local-only" if host in {"127.0.0.1", "localhost", "::1"} else "network bind"
    return f"{scope} · {host} · 11-layer engine"


def windows_bridge_available(osrelease_path: str = "/proc/sys/kernel/osrelease") -> bool:
    """Whether "Browse (Windows)" can plausibly work at all.

    The native folder picker and Explorer-opening actions both depend on
    scripts/open-output-folder-helper.sh running on a WSL host with
    explorer.exe/powershell.exe reachable (see request_windows_folder_pick()
    and request_output_folder_open()) -- meaningless on plain Linux, where
    that helper is never started (scripts/securescan-gui-common.sh only
    launches it under WSL) and the button would just time out on every
    click. WSL2's kernel release string contains "microsoft"
    (e.g. "6.6.87.2-microsoft-standard-WSL2"); this is the same detection
    scripts/securescan-gui-common.sh uses on the host side.
    """
    try:
        with open(osrelease_path, encoding="utf-8") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def request_output_folder_open(
    output_dir: str = discovery.EXCLUDED_REPORT_OUTPUT_DIR,
    timeout: float = OUTPUT_FOLDER_HELPER_TIMEOUT_S,
    poll_interval: float = 0.02,
) -> Tuple[bool, str]:
    """Ask the fixed-path WSL helper to open ``outputs/`` in Explorer.

    The request carries only an opaque token. The host helper derives the one
    permitted directory from its own repository location, and acknowledges
    that token after dispatching Explorer. A missing helper therefore becomes
    a visible timeout instead of a silent no-op.
    """
    request_path = os.path.join(output_dir, OUTPUT_FOLDER_REQUEST_FILENAME)
    ack_path = os.path.join(output_dir, OUTPUT_FOLDER_ACK_FILENAME)
    token = uuid.uuid4().hex
    temporary_path = f"{request_path}.{os.getpid()}.{threading.get_ident()}.tmp"

    with _output_folder_request_lock:
        try:
            os.makedirs(output_dir, mode=0o700, exist_ok=True)
            with open(temporary_path, "w", encoding="ascii") as fh:
                fh.write(token + "\n")
            os.replace(temporary_path, request_path)
        except OSError as exc:
            return False, f"Could not request Windows Explorer: {exc}"
        finally:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            try:
                acknowledgement = open(ack_path, encoding="ascii").read().strip()
            except (OSError, UnicodeError):
                acknowledgement = ""
            acknowledged_token = acknowledgement.partition("\t")[0]
            if acknowledged_token == token:
                return True, "Opened outputs in Windows Explorer."
            time.sleep(max(0.001, float(poll_interval)))

    return (
        False,
        "Windows Explorer helper is not running. Start SecureScan with "
        "~/securescan-gui.sh.",
    )


def _join_mounted_path(base: str, remainder: str) -> str:
    """Join slash-agnostic Windows path components onto a container path."""
    suffix = remainder.replace("\\", "/").strip("/")
    if not suffix:
        return base.rstrip("/") or os.sep
    return posixpath.normpath(posixpath.join(base, suffix))


def _unescape_mount_field(value: str) -> str:
    """Decode the small octal escape set used by /proc/self/mounts."""
    for escaped, literal in (
        ("\\040", " "),
        ("\\011", "\t"),
        ("\\012", "\n"),
        ("\\134", "\\"),
    ):
        value = value.replace(escaped, literal)
    return value


def discover_unc_mounts(mounts_path: str = "/proc/self/mounts") -> Dict[str, str]:
    """Return case-insensitive ``//server/share`` -> mount-point mappings.

    CIFS/SMB sources are visible in Linux's mount table even when the chosen
    mount point is an unrelated name such as ``/mnt/team_docs``. Reading that
    table lets Explorer UNC paths reach the actual existing mount instead of
    guessing a directory layout under /mnt.
    """
    mappings: Dict[str, str] = {}
    try:
        with open(mounts_path, "r", encoding="utf-8") as fh:
            lines = list(fh)
    except OSError:
        return mappings
    for line in lines:
        fields = line.split()
        if len(fields) < 2:
            continue
        source = _unescape_mount_field(fields[0]).replace("\\", "/")
        if not source.startswith("//"):
            continue
        parts = [part for part in source[2:].split("/") if part]
        if len(parts) < 2:
            continue
        key = f"//{parts[0]}/{parts[1]}".casefold()
        mappings[key] = _unescape_mount_field(fields[1])
    return mappings


def translate_windows_path(
    path: str, unc_mounts: Optional[Dict[str, str]] = None
) -> str:
    """Translate an Explorer path to the equivalent path inside the container.

    Drive paths accept either separator and map to the path-parity mounts under
    ``/mnt``. UNC paths use the live mount table when available, then try the
    conventional ``/mnt/<server>/<share>`` and ``/mnt/<share>`` locations.
    Existing container-native paths and unrelated relative paths pass through
    byte-for-byte unchanged.
    """
    if not path:
        return path

    drive_match = re.fullmatch(r"([A-Za-z]):(?:[\\/](.*))?", path)
    if drive_match:
        base = f"/mnt/{drive_match.group(1).lower()}"
        return _join_mounted_path(base, drive_match.group(2) or "")

    unc_match = re.fullmatch(
        r"[\\/]{2}([^\\/]+)[\\/]([^\\/]+)(?:[\\/](.*))?", path
    )
    if unc_match:
        server, share, remainder = unc_match.groups()
        key = f"//{server}/{share}".casefold()
        mounts = unc_mounts if unc_mounts is not None else discover_unc_mounts()
        normalized_mounts = {
            source.replace("\\", "/").rstrip("/").casefold(): target
            for source, target in mounts.items()
        }
        base = normalized_mounts.get(key)
        if base is None:
            candidates = (f"/mnt/{server}/{share}", f"/mnt/{share}")
            base = next(
                (candidate for candidate in candidates if os.path.isdir(candidate)),
                candidates[0],
            )
        return _join_mounted_path(base, remainder or "")

    return path


def _remove_matching_picker_request(request_path: str, token: str) -> None:
    """Remove only this call's request, never a newer concurrent token."""
    try:
        with open(request_path, "r", encoding="ascii") as fh:
            current_token = fh.read().strip()
    except (OSError, UnicodeError):
        return
    if current_token != token:
        return
    try:
        os.unlink(request_path)
    except OSError:
        pass


def _decode_picker_payload(payload: str) -> Optional[str]:
    try:
        return base64.b64decode(payload, validate=True).decode("utf-8")
    except (ValueError, UnicodeError):
        return None


def request_windows_folder_pick(
    output_dir: str = discovery.EXCLUDED_REPORT_OUTPUT_DIR,
    timeout: float = WINDOWS_FOLDER_PICKER_TIMEOUT_S,
    helper_timeout: float = WINDOWS_FOLDER_PICKER_HELPER_TIMEOUT_S,
    poll_interval: float = 0.05,
    translate_path: Callable[[str], str] = translate_windows_path,
    path_is_dir: Callable[[str], bool] = os.path.isdir,
) -> Tuple[str, Optional[str], str]:
    """Request a native Windows folder pick through the WSL helper bridge.

    Responses are token-matched and have one of four wire statuses: ``opened``
    (the helper is alive and the dialog is now pending), ``selected`` with a
    base64 UTF-8 Windows path, ``cancel``, or ``error`` with a base64 message.
    The short helper deadline keeps a stopped bridge from looking like a user
    who is still navigating the dialog; the latter gets the much longer
    overall timeout.
    """
    request_path = os.path.join(output_dir, WINDOWS_FOLDER_PICKER_REQUEST_FILENAME)
    response_path = os.path.join(output_dir, WINDOWS_FOLDER_PICKER_RESPONSE_FILENAME)
    token = uuid.uuid4().hex
    temporary_path = f"{request_path}.{os.getpid()}.{threading.get_ident()}.tmp"

    with _windows_folder_picker_request_lock:
        try:
            os.makedirs(output_dir, mode=0o700, exist_ok=True)
            with open(temporary_path, "w", encoding="ascii") as fh:
                fh.write(token + "\n")
            os.replace(temporary_path, request_path)
        except OSError as exc:
            return "error", None, f"Could not request the Windows folder picker: {exc}"
        finally:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

        started = time.monotonic()
        deadline = started + max(0.0, float(timeout))
        helper_deadline = started + min(
            max(0.0, float(timeout)), max(0.0, float(helper_timeout))
        )
        helper_seen = False
        try:
            while time.monotonic() < deadline:
                try:
                    response = open(
                        response_path, encoding="ascii"
                    ).read().strip()
                except (OSError, UnicodeError):
                    response = ""
                response_token, separator, remainder = response.partition("\t")
                if separator and response_token == token:
                    status, status_separator, payload = remainder.partition("\t")
                    if status == "opened":
                        helper_seen = True
                    elif status == "cancel":
                        return "cancelled", None, ""
                    elif status == "selected" and status_separator:
                        windows_path = _decode_picker_payload(payload)
                        if windows_path is None:
                            return (
                                "error",
                                None,
                                "The Windows picker returned an invalid path response.",
                            )
                        translated = translate_path(windows_path)
                        if not path_is_dir(translated):
                            return (
                                "unreachable",
                                None,
                                f"{windows_path} is not visible to the scanner. "
                                "Mount that drive or network share under /mnt, or use "
                                "the in-app browser.",
                            )
                        return "selected", translated, ""
                    elif status == "error":
                        decoded = _decode_picker_payload(payload) if status_separator else None
                        return (
                            "error",
                            None,
                            decoded or "The Windows folder picker could not be opened.",
                        )
                if not helper_seen and time.monotonic() >= helper_deadline:
                    return (
                        "unavailable",
                        None,
                        "Windows picker helper is not running. Use the in-app "
                        "browser instead, or start SecureScan with ~/securescan-gui.sh.",
                    )
                time.sleep(max(0.001, float(poll_interval)))
        finally:
            _remove_matching_picker_request(request_path, token)

    return (
        "timeout",
        None,
        "The Windows folder picker timed out. The selection was ignored; use "
        "Browse (in app) and try again.",
    )


def windows_profile_directory(
    users_root: str = WINDOWS_USERS_ROOT,
    environ: Optional[Dict[str, str]] = None,
) -> str:
    """Derive the Windows user's profile directory, with navigable fallbacks."""
    environment = os.environ if environ is None else environ
    for variable in ("SECURESCAN_WINDOWS_PROFILE", "USERPROFILE"):
        hinted = environment.get(variable, "")
        translated = translate_windows_path(hinted) if hinted else ""
        if translated and os.path.isdir(translated):
            return translated.rstrip(os.sep) or os.sep

    name_hints = {
        environment.get(variable, "").casefold()
        for variable in ("SECURESCAN_WINDOWS_USERNAME", "USERNAME", "SUDO_USER", "USER")
        if environment.get(variable, "").casefold() not in {"", "root"}
    }
    candidates: List[str] = []
    try:
        with os.scandir(users_root) as entries:
            for entry in entries:
                try:
                    if (
                        entry.is_dir()
                        and entry.name.casefold() not in NON_USER_WINDOWS_PROFILES
                    ):
                        candidates.append(entry.path)
                except OSError:
                    continue
    except OSError:
        candidates = []

    for candidate in candidates:
        if os.path.basename(candidate).casefold() in name_hints:
            return candidate
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        scored = sorted(
            (
                sum(
                    os.path.isdir(os.path.join(candidate, dirname))
                    for dirname in WINDOWS_PROFILE_DIR_NAMES
                ),
                candidate,
            )
            for candidate in candidates
        )
        if scored[-1][0] > 0 and (
            len(scored) == 1 or scored[-1][0] > scored[-2][0]
        ):
            return scored[-1][1]

    if os.path.isdir(users_root):
        return users_root
    drive_root = os.path.dirname(users_root.rstrip(os.sep))
    if drive_root and os.path.isdir(drive_root):
        return drive_root
    return os.path.expanduser("~")


def resolve_windows_profile_folder(profile: str, folder: str) -> Optional[str]:
    """Resolve one redirected Windows profile folder independently.

    Personal OneDrive wins, followed by alphabetically ordered OneDrive for
    Business directories, then the unredirected profile folder. Returning
    ``None`` is intentional: callers can disable the chip rather than link to
    a plausible-looking path which does not exist.
    """
    candidates = [os.path.join(profile, "OneDrive", folder)]
    candidates.extend(
        sorted(
            glob.glob(os.path.join(profile, "OneDrive - *", folder)),
            key=str.casefold,
        )
    )
    candidates.append(os.path.join(profile, folder))
    return next((candidate for candidate in candidates if os.path.isdir(candidate)), None)


def _browser_shortcut_group(label: str, path: str, group: Any) -> str:
    """Normalize explicit groups and migrate older two-field config rows."""
    if group in BROWSER_SHORTCUT_GROUPS:
        return str(group)
    if label in WINDOWS_PROFILE_DIR_NAMES and path.startswith(
        f"{WINDOWS_PROFILE_PLACEHOLDER}/"
    ):
        return "folders"
    if re.fullmatch(r"[A-Za-z]:", label) or re.fullmatch(r"/mnt/[A-Za-z]", path):
        return "drives"
    return "custom"


def load_browser_shortcuts(
    config_path: Optional[str] = None,
    windows_profile: Optional[str] = None,
    resolve: bool = True,
) -> List[Dict[str, Any]]:
    """Load editable browser shortcuts; malformed/missing config uses defaults."""
    config_path = config_path or BROWSER_SHORTCUTS_PATH
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            configured = json.load(fh)
    except Exception:
        configured = DEFAULT_BROWSER_SHORTCUTS
    if not isinstance(configured, list):
        configured = DEFAULT_BROWSER_SHORTCUTS

    profile = windows_profile or windows_profile_directory()
    shortcuts: List[Dict[str, Any]] = []
    for item in configured:
        if not isinstance(item, dict):
            continue
        label, path = item.get("label"), item.get("path")
        if not isinstance(label, str) or not label.strip():
            continue
        if not isinstance(path, str) or not path.strip():
            continue
        label = label.strip()
        path = path.strip()
        group = _browser_shortcut_group(label, path, item.get("group"))
        available = None
        if resolve:
            profile_folder_match = re.fullmatch(
                rf"{re.escape(WINDOWS_PROFILE_PLACEHOLDER)}/"
                rf"({'|'.join(map(re.escape, WINDOWS_PROFILE_DIR_NAMES))})",
                path,
            )
            if profile_folder_match:
                resolved_path = resolve_windows_profile_folder(
                    profile, profile_folder_match.group(1)
                )
                path = resolved_path or ""
            else:
                path = path.replace(WINDOWS_PROFILE_PLACEHOLDER, profile)
                path = translate_windows_path(path)
            available = bool(path and os.path.isdir(path))
        shortcut = {"label": label, "path": path, "group": group}
        if available is not None:
            shortcut["available"] = available
        shortcuts.append(shortcut)
    return shortcuts


def save_browser_shortcuts(
    shortcuts: List[Dict[str, str]], config_path: Optional[str] = None
) -> bool:
    """Persist shortcut rows. Returns False on a read-only or invalid target."""
    config_path = config_path or BROWSER_SHORTCUTS_PATH
    normalized = [
        {
            "label": item["label"].strip(),
            "path": item["path"].strip(),
            "group": _browser_shortcut_group(
                item["label"].strip(), item["path"].strip(), item.get("group")
            ),
        }
        for item in shortcuts
        if isinstance(item, dict)
        and isinstance(item.get("label"), str)
        and item["label"].strip()
        and isinstance(item.get("path"), str)
        and item["path"].strip()
    ]
    temporary_path = f"{config_path}.tmp"
    try:
        with _browser_shortcuts_file_lock:
            try:
                existing_stat = os.stat(config_path)
            except OSError:
                existing_stat = None
            directory = os.path.dirname(config_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(temporary_path, "w", encoding="utf-8") as fh:
                json.dump(normalized, fh, indent=2)
                fh.write("\n")
            if existing_stat is not None:
                os.chmod(temporary_path, existing_stat.st_mode & 0o777)
                try:
                    os.chown(
                        temporary_path, existing_stat.st_uid, existing_stat.st_gid
                    )
                except (AttributeError, PermissionError):
                    pass
            os.replace(temporary_path, config_path)
    except OSError:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        return False
    return True


def remove_browser_shortcut(
    index: int, config_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Remove any shortcut row by display index, including shipped defaults."""
    configured = load_browser_shortcuts(config_path, resolve=False)
    if not 0 <= index < len(configured):
        return None
    removed = configured.pop(index)
    return removed if save_browser_shortcuts(configured, config_path) else None


def load_gui_state(state_path: Optional[str] = None) -> Dict[str, Any]:
    """Read the small GUI-only state document. Never raises."""
    state_path = state_path or STATE_PATH
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_recent_folders(state_path: Optional[str] = None) -> List[str]:
    """Read the recent-folders list. Paths only, never findings. Never raises."""
    data = load_gui_state(state_path)
    folders = data.get("recent_folders", []) if isinstance(data, dict) else []
    return [f for f in folders if isinstance(f, str)]


def load_gui_settings(state_path: Optional[str] = None) -> Dict[str, Any]:
    """Load persisted controls, applying safe defaults to missing/bad values."""
    data = load_gui_state(state_path)
    settings = data.get("settings", {}) if isinstance(data, dict) else {}
    if not isinstance(settings, dict):
        settings = {}
    loaded = {
        key: value if isinstance(value := settings.get(key), bool) else default
        for key, default in DEFAULT_GUI_SETTINGS.items()
        if isinstance(default, bool)
    }
    for key, default, minimum, maximum in (
        ("max_workers", config.DEFAULT_MAX_WORKERS, 1, 16),
        ("ocr_workers", 4, 1, 8),
        ("ner_max_chars", config.NER_MAX_CHARS, 10_000, 1_000_000),
    ):
        value = settings.get(key)
        loaded[key] = (
            value
            if isinstance(value, int)
            and not isinstance(value, bool)
            and minimum <= value <= maximum
            else default
        )
    loaded["file_types"] = normalize_file_types(settings.get("file_types"))
    loaded["layers"] = normalize_layers(settings.get("layers"))
    return loaded


def save_gui_settings(
    settings: Dict[str, Any], state_path: Optional[str] = None
) -> Dict[str, Any]:
    """Persist recognized controls without disturbing recent paths."""
    state_path = state_path or STATE_PATH
    normalized: Dict[str, Any] = {
        key: bool(settings.get(key, default))
        for key, default in DEFAULT_GUI_SETTINGS.items()
        if isinstance(default, bool)
    }
    for key, default, minimum, maximum in (
        ("max_workers", config.DEFAULT_MAX_WORKERS, 1, 16),
        ("ocr_workers", 4, 1, 8),
        ("ner_max_chars", config.NER_MAX_CHARS, 10_000, 1_000_000),
    ):
        value = settings.get(key, default)
        normalized[key] = (
            int(value)
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and minimum <= int(value) <= maximum
            else default
        )
    normalized["file_types"] = normalize_file_types(settings.get("file_types"))
    normalized["layers"] = normalize_layers(settings.get("layers"))
    try:
        with _gui_state_file_lock:
            data = load_gui_state(state_path)
            data["settings"] = normalized
            directory = os.path.dirname(state_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(state_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
    except Exception:
        pass
    return normalized


def add_recent_folder(
    folder: str,
    state_path: Optional[str] = None,
    max_recent: int = MAX_RECENT_FOLDERS,
) -> List[str]:
    """Move `folder` to the front of the recent list, persist, return the new list.

    Best-effort persistence: a write failure (read-only fs, permissions) is
    swallowed — recent folders are a convenience, not scan-critical state.
    """
    state_path = state_path or STATE_PATH
    folders = [f for f in load_recent_folders(state_path) if f != folder]
    folders.insert(0, folder)
    folders = folders[:max_recent]
    try:
        with _gui_state_file_lock:
            data = load_gui_state(state_path)
            data["recent_folders"] = folders
            directory = os.path.dirname(state_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(state_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
    except Exception:
        pass
    return folders


def list_subdirectories(path: str) -> List[str]:
    """Subdirectory names directly under path: visible (case-insensitive alpha)
    first, then dot-prefixed hidden dirs (case-insensitive alpha) last. Known
    container-only roots are omitted when listing ``/``; this is display-only.
    [] on any access error.
    """
    at_filesystem_root = os.path.abspath(path) == os.sep
    try:
        with os.scandir(path) as entries:
            names = []
            for entry in entries:
                try:
                    if entry.is_dir() and not (
                        at_filesystem_root
                        and entry.name.casefold() in CONTAINER_INTERNAL_ROOT_DIRS
                    ):
                        names.append(entry.name)
                except OSError:
                    continue
    except OSError:
        return []
    visible = sorted((n for n in names if not n.startswith(".")), key=str.lower)
    hidden = sorted((n for n in names if n.startswith(".")), key=str.lower)
    return visible + hidden


def list_browser_entries(path: str) -> List[Dict[str, Any]]:
    """Navigable directories followed by selectable supported files.

    Container implementation directories are hidden only in the root view;
    callers may still navigate to or scan any of them by entering the path.
    """
    at_filesystem_root = os.path.abspath(path) == os.sep
    try:
        with os.scandir(path) as entries:
            rows = []
            for entry in entries:
                try:
                    if entry.is_dir() and not (
                        at_filesystem_root
                        and entry.name.casefold() in CONTAINER_INTERNAL_ROOT_DIRS
                    ):
                        rows.append({"name": entry.name, "is_dir": True})
                    elif (
                        entry.is_file()
                        and os.path.splitext(entry.name.lower())[1]
                        in discovery.SUPPORTED_EXTENSIONS
                    ):
                        rows.append({"name": entry.name, "is_dir": False})
                except OSError:
                    continue
    except OSError:
        return []
    return sorted(
        rows,
        key=lambda row: (
            not row["is_dir"],
            row["name"].startswith("."),
            row["name"].lower(),
        ),
    )


def try_native_folder_dialog(runner=subprocess.run) -> Tuple[str, Optional[str]]:
    """Best-effort native OS folder picker (zenity, then kdialog).

    Returns (status, path): status is "selected" (path set), "cancelled"
    (a dialog ran and the user dismissed it — respect that, don't fall back),
    or "unavailable" (no native dialog tool on this system — caller should
    fall back to the server-side browser dialog).
    """
    for cmd in (
        ["zenity", "--file-selection", "--directory", "--title=Select folder to scan"],
        ["kdialog", "--getexistingdirectory", os.path.expanduser("~")],
    ):
        try:
            proc = runner(cmd, capture_output=True, text=True, timeout=120)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
        if getattr(proc, "returncode", 1) == 0:
            path = (proc.stdout or "").strip()
            return ("selected", path) if path else ("cancelled", None)
        return ("cancelled", None)
    return ("unavailable", None)


def _report_url(path: Optional[str]) -> Optional[str]:
    """Map an absolute report-set path under the served outputs dir to its
    HTTP URL. None if the path is missing, not a file, or outside that tree
    (defensive only — every caller already sources paths from inside it)."""
    if not path or not os.path.isfile(path):
        return None
    rel = os.path.relpath(os.path.abspath(path), discovery.EXCLUDED_REPORT_OUTPUT_DIR)
    if rel.startswith(".."):
        return None
    return f"{OUTPUTS_URL_PATH}/{rel.replace(os.sep, '/')}"


# -------------------------------------------------------------------
# Background scan worker — owns every write to _scan_state. Runs in a
# plain daemon thread, never awaited by any client's request coroutine, so
# a page reload or client disconnect can never orphan it (see the
# _scan_state module comment above). Must never call any ui.* function —
# it does not run on the NiceGUI event loop.
# -------------------------------------------------------------------
def _scan_worker(
    target: str,
    kwargs: Dict[str, Any],
    cancel_event: Optional[threading.Event] = None,
) -> None:
    cancel_event = cancel_event or threading.Event()
    with _scan_state_lock:
        _scan_state["cancel_event"] = cancel_event
        start_time = _scan_state.get("start_time") or time.time()

    def progress_callback(
        done: int,
        total: int,
        current_file: Optional[str] = None,
        completed: Optional[Dict[str, Any]] = None,
    ) -> None:
        with _scan_state_lock:
            _scan_state.update(
                apply_progress(
                    _scan_state, done, total, current_file=current_file, completed=completed
                )
            )

    try:
        try:
            html_path = discovery.scan_path(
                target,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                **kwargs,
            )
        except Exception as e:
            with _scan_state_lock:
                _scan_state.update(error_scan_state(_scan_state, f"Scan failed: {e}"))
            return

        elapsed = time.time() - start_time
        if not html_path:
            with _scan_state_lock:
                _scan_state.update(
                    error_scan_state(_scan_state, "No supported files were found at that path.")
                )
            return

        json_path = html_path_to_json_path(str(html_path))
        summary = summarize_report(json_path)
        if "error" in summary:
            with _scan_state_lock:
                _scan_state.update(error_scan_state(_scan_state, summary["error"]))
            return

        summary["duration"] = elapsed
        preview = extract_findings_preview(json_path)
        alarms = extract_alarm_details(json_path)
        scan_time = time.strftime("%Y-%m-%d %H:%M:%S")
        with _scan_state_lock:
            _scan_state.update(
                done_scan_state(
                    _scan_state,
                    cancelled=summary.get("cancelled", False),
                    summary=summary,
                    preview=preview,
                    html_path=str(html_path),
                    scan_time=scan_time,
                    alarms=alarms,
                )
            )
        add_recent_folder(target)
    finally:
        _scan_lock.release()
        _reconcile_auto_shutdown()


def _cancel_auto_shutdown_locked() -> None:
    global _auto_shutdown_timer
    if _auto_shutdown_timer is not None:
        _auto_shutdown_timer.cancel()
        _auto_shutdown_timer = None


def _auto_shutdown_expired() -> None:
    global _auto_shutdown_timer
    with _client_lifecycle_lock:
        _auto_shutdown_timer = None
        if _connected_client_ids:
            return
        with _scan_state_lock:
            if _scan_state.get("phase") == "running":
                return
    print("No connected clients — SecureScan stopped.", flush=True)
    app.shutdown()


def _reconcile_auto_shutdown() -> None:
    """Arm, defer, or cancel the last-client grace timer."""
    global _auto_shutdown_timer
    with _client_lifecycle_lock:
        with _scan_state_lock:
            scan_running = _scan_state.get("phase") == "running"
        action = auto_shutdown_action(
            len(_connected_client_ids),
            scan_running,
            _auto_shutdown_timer is not None,
        )
        if action == "cancel":
            _cancel_auto_shutdown_locked()
        elif action == "arm":
            _auto_shutdown_timer = threading.Timer(
                AUTO_SHUTDOWN_GRACE_S,
                _auto_shutdown_expired,
            )
            _auto_shutdown_timer.daemon = True
            _auto_shutdown_timer.start()


def _handle_client_connect(client: Any) -> None:
    with _client_lifecycle_lock:
        _connected_client_ids.add(str(client.id))
    _reconcile_auto_shutdown()


def _handle_client_disconnect(client: Any) -> None:
    with _client_lifecycle_lock:
        _connected_client_ids.discard(str(client.id))
    _reconcile_auto_shutdown()


def _handle_shutdown() -> None:
    """Registered once via app.on_shutdown() below (not per-client).

    Sets any running scan's cancel_event so its background thread winds down
    promptly instead of being torn down mid-flight, then exits quietly with
    a single line — no traceback, whether or not a scan was running.
    """
    with _scan_state_lock:
        ev = _scan_state.get("cancel_event")
    if ev is not None:
        ev.set()
    print("SecureScan stopped.")


app.on_shutdown(_handle_shutdown)
app.on_connect(_handle_client_connect)
app.on_disconnect(_handle_client_disconnect)

# -------------------------------------------------------------------
# Serve report files over HTTP so the browser can open uniquely named reports.
# "Open Output Folder" uses the fixed-path WSL helper handshake instead.
# outputs/ is 0o700 (owner-only) on disk specifically because reports carry
# full unmasked PII (see discovery.py) — mirrored here since gui.py can
# start before any scan has created the directory. That filesystem
# protection does NOT extend to this route: anything that can reach this
# port can now read every report ever written, not just the latest one.
# The header reports the configured bind explicitly so the exposure is never
# described as loopback-only when Compose binds the process to 0.0.0.0.
# -------------------------------------------------------------------
os.makedirs(discovery.EXCLUDED_REPORT_OUTPUT_DIR, mode=0o700, exist_ok=True)
os.chmod(discovery.EXCLUDED_REPORT_OUTPUT_DIR, 0o700)
app.add_static_files(OUTPUTS_URL_PATH, discovery.EXCLUDED_REPORT_OUTPUT_DIR)


# -------------------------------------------------------------------
# Page
# -------------------------------------------------------------------
@ui.page("/")
def index():
    page_client = ui.context.client

    ui.dark_mode().enable()
    ui.colors(primary="#5898d4")
    ui.add_css(THEME_CSS)

    browser_state = {"path": windows_profile_directory()}
    persisted_settings = load_gui_settings()

    def open_report_path(path: str) -> None:
        url = _report_url(path)
        if url:
            ui.navigate.to(url, new_tab=True)

    def open_latest_report() -> None:
        reports = list_recent_reports(limit=1)
        if reports:
            open_report_path(reports[0]["html_path"])

    async def open_output_folder() -> None:
        opened, message = await run.io_bound(request_output_folder_open)
        ui.notify(message, type="positive" if opened else "negative")

    def _report_action_buttons() -> None:
        """'Open Latest Report' / 'Open Output Folder' — available before any
        scan has run in this session; disabled with a tooltip only when no
        complete report set exists on disk yet at all. Reports open through
        the browser; the folder action uses the token/ack WSL helper and shows
        a UI notification if that helper never acknowledges the request."""
        with ui.row().classes("gap-2 items-center mt-2"):
            latest_button = ui.button(
                "Open Latest Report", icon="description", on_click=open_latest_report
            ).props("color=primary no-caps")
            ui.button(
                "Open Output Folder",
                icon="folder",
                on_click=open_output_folder,
            ).props("flat no-caps color=blue-grey-4")
            if not has_latest_report():
                latest_button.disable()
                latest_button.tooltip("No reports yet — run a scan first.")

    @ui.refreshable
    def render_status() -> None:
        s = _scan_state
        state = dashboard_state(s["phase"], bool(s.get("cancelled")))

        if state == "idle":
            with ui.column().classes("empty-state idle-state w-full gap-3"):
                ui.html(
                    """
                    <svg class="empty-shield" viewBox="0 0 64 64" fill="none"
                         xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                      <path d="M32 5 53 13v16c0 14-8.8 24.8-21 30C19.8 53.8 11 43 11 29V13L32 5Z"
                            stroke="currentColor" stroke-width="2.5"/>
                      <path d="m23 32 6 6 13-14" stroke="currentColor"
                            stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    """
                )
                ui.label("Ready to scan").classes("text-xl font-semibold")
                ui.label(
                    "Reports are written to outputs/ and open in your browser."
                ).classes("text-sm text-gray-400")
            if s["last_scan_time"]:
                ui.label(f"Last scan: {s['last_scan_time']}").classes(
                    "text-xs text-gray-500 mt-2"
                )
            _report_action_buttons()

        elif state == "running":
            with ui.column().classes("running-state w-full gap-4"):
                if s.get("cancel_requested"):
                    ui.label("CANCELLING…").classes(
                        "text-2xl font-semibold text-amber-400"
                    )
                else:
                    current = infer_phase(
                        s["done"], s["total"], False, s["verify_on"]
                    )
                    ui.label(PHASE_LABELS[current]).classes(
                        "text-2xl font-semibold"
                    )
                ui.linear_progress(
                    value=progress_fraction(s["done"], s["total"]),
                    show_value=False,
                ).props("instant-feedback").classes("running-progress")
                if s["total"]:
                    ui.label(f"{s['done']} / {s['total']} files").classes(
                        "text-xl font-semibold"
                    )
                if s.get("cancel_requested"):
                    ui.label(
                        cancelling_status_line(
                            s.get("submitted_files", []), s["done"]
                        )
                    ).classes("text-base text-amber-300")
                else:
                    line = current_file_line(
                        s["current_file"],
                        s["current_file_started_at"],
                        time.time(),
                    )
                    if line:
                        ui.label(line).classes(
                            "text-base text-gray-300 mono-display"
                        )
                elapsed = time.time() - s["start_time"] if s["start_time"] else 0.0
                ui.label(f"{elapsed:.0f}s elapsed").classes(
                    "text-sm text-gray-400"
                )

                ui.label("Findings landing").classes(
                    "text-sm font-semibold text-gray-300 mt-2 self-start"
                )
                with ui.scroll_area().classes("ticker-scroll"):
                    if s.get("ticker"):
                        for row in s["ticker"]:
                            with ui.row().classes(
                                "ticker-row w-full items-center gap-2 no-wrap"
                            ):
                                ui.badge(str(row.get("risk") or "NONE")).props(
                                    f"color={RISK_COLOR.get(row.get('risk'), 'grey')}"
                                )
                                ui.label(str(row.get("file") or "")).classes(
                                    "text-xs mono-display grow text-left"
                                    " overflow-hidden text-ellipsis whitespace-nowrap"
                                )
                                ui.label(str(row.get("top_type") or "—")).classes(
                                    "text-xs text-gray-400 text-left"
                                )
                    else:
                        ui.label("Waiting for the first file to finish…").classes(
                            "ticker-empty"
                        )

        elif state in {"done", "cancelled"}:
            summary = s["summary"]
            ui.label(
                completion_header(
                    summary, s["done"], s["total"], state == "cancelled"
                )
            ).classes(
                "text-xl font-semibold text-amber-400"
                if state == "cancelled"
                else "text-xl font-semibold"
            )
            with ui.element("div").classes("stat-grid"):
                for tile in stat_tiles(summary):
                    with ui.element("div").classes(
                        f"stat-tile {tile['tone']}"
                    ):
                        ui.label(str(tile["value"])).classes(
                            "stat-number"
                        )
                        ui.label(tile["label"]).classes("stat-label")

            _report_action_buttons()

            recent_reports = list_recent_reports()
            if recent_reports:
                with ui.expansion("Recent reports", icon="history").classes(
                    "w-full text-sm"
                ):
                    for report in recent_reports:
                        with ui.row().classes(
                            "recent-report-row w-full items-center justify-between no-wrap"
                        ):
                            ui.label(report["name"]).classes(
                                "text-xs text-gray-400 mono-display"
                            )
                            ui.button(
                                "Open",
                                on_click=lambda path=report["html_path"]: (
                                    open_report_path(path)
                                ),
                            ).props("flat dense no-caps color=primary")

            if summary["alarms"]:
                count = summary["alarms"]
                with ui.expansion(
                    f"⚠ {count} possible silent miss"
                    f"{'es' if count != 1 else ''}",
                    value=True,
                ).classes("alarm-strip text-sm mt-2"):
                    for alarm in s.get("alarms", []):
                        with ui.column().classes("alarm-row w-full gap-1"):
                            with ui.row().classes(
                                "w-full items-center gap-2 flex-wrap"
                            ):
                                ui.label(alarm["file"]).classes(
                                    "text-sm mono-display grow"
                                )
                                for trigger in alarm["triggers"]:
                                    ui.badge(trigger).classes(
                                        "alarm-trigger text-xs"
                                    )
                            ui.label(alarm["reason"]).classes(
                                "text-xs text-gray-300"
                            )

            if s["preview"]:
                ui.label("Top files").classes(
                    "text-base font-semibold mt-2"
                )
                table = ui.table(
                    columns=[
                        {"name": "file", "label": "File", "field": "file", "align": "left"},
                        {"name": "risk", "label": "Risk", "field": "risk", "align": "center"},
                        {"name": "score", "label": "Score", "field": "score", "align": "right"},
                        {
                            "name": "top_type",
                            "label": "Top Finding Type",
                            "field": "top_type",
                            "align": "left",
                        },
                    ],
                    rows=s["preview"],
                    row_key="file",
                ).props("flat dense").classes("w-full top-files-table")
                table.add_slot(
                    "body-cell-risk",
                    """
                    <q-td :props="props">
                        <q-badge :color="props.row.risk_color">
                            {{ props.row.risk_badge }} {{ props.row.risk }}
                        </q-badge>
                    </q-td>
                    """,
                )

        elif state == "error":
            with ui.column().classes("empty-state w-full gap-3"):
                ui.icon("error_outline", color="red").classes("text-5xl")
                ui.label("Scan could not complete").classes(
                    "text-xl font-semibold"
                )
                ui.label(s["error"]).classes(
                    "text-red-400 whitespace-pre-wrap text-center"
                )

    with ui.header().classes("secure-appbar"):
        with ui.row().classes(
            "appbar-inner items-center justify-between no-wrap"
        ):
            with ui.column().classes("gap-0"):
                with ui.row().classes("items-center gap-2"):
                    ui.html(
                        '<span class="mono-display text-gray-400 text-xl">&gt;_</span>'
                    )
                    ui.label("SecureScan").classes(
                        "text-2xl font-bold secure-header"
                    )
                    ui.badge(
                        f"v{config.SECURESCAN_VERSION}", color="blue-grey-8"
                    ).props("outline")
                ui.label(
                    "Canadian-focused PII scanner — runs entirely on this machine."
                ).classes("text-xs text-gray-500")
            ui.label(gui_bind_label()).classes("text-xs text-gray-400")

    with ui.row().classes("dashboard-shell no-wrap"):
        # ---------------- Controls card (left) ----------------
        with ui.card().classes(
            "controls-card dashboard-card gap-3"
        ):
            ui.label("Scan controls").classes("text-lg font-semibold")

            path_input = ui.input(
                "File or folder to scan",
                placeholder=r"C:\Users\name\Documents or /mnt/c/...",
            ).props("dense").classes("w-full")
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                windows_browse_button = ui.button(
                    "Browse (Windows)", icon="desktop_windows"
                ).props("color=blue-grey-5 outline no-caps").classes("grow")
                if not windows_bridge_available():
                    windows_browse_button.disable()
                    windows_browse_button.tooltip(
                        "Windows folder picker needs WSL — use Browse (in app)."
                    )
                browse_button = ui.button(
                    "Browse (in app)", icon="folder_open"
                ).props("color=blue-grey-5 flat no-caps").classes("grow")

            recent_select = ui.select(
                options=load_recent_folders(), label="Recent paths"
            ).props("dense options-dense").classes("w-full")

            def _on_recent_selected(e) -> None:
                if e.value:
                    path_input.value = e.value

            recent_select.on_value_change(_on_recent_selected)

            folder_browser_dialog = ui.dialog()

            def _jump_to(path: str) -> None:
                browser_state["path"] = path
                render_browser_body.refresh()

            def _go_up() -> None:
                browser_state["path"] = (
                    os.path.dirname(browser_state["path"].rstrip(os.sep)) or os.sep
                )
                render_browser_body.refresh()

            def _go_home() -> None:
                browser_state["path"] = windows_profile_directory()
                render_browser_body.refresh()

            def _descend(name: str) -> None:
                browser_state["path"] = os.path.join(browser_state["path"], name)
                render_browser_body.refresh()

            def _select_file(name: str) -> None:
                path_input.value = os.path.join(browser_state["path"], name)
                folder_browser_dialog.close()

            def _add_current_folder_shortcut() -> None:
                current = os.path.normpath(browser_state["path"])
                resolved = load_browser_shortcuts()
                if any(
                    item["path"] and os.path.normpath(item["path"]) == current
                    for item in resolved
                ):
                    ui.notify("This folder is already a shortcut.", type="info")
                    return
                configured = load_browser_shortcuts(resolve=False)
                label = os.path.basename(current) or "Root"
                configured.append(
                    {"label": label, "path": current, "group": "custom"}
                )
                if save_browser_shortcuts(configured):
                    ui.notify(f"Added shortcut: {label}", type="positive")
                    render_browser_body.refresh()
                else:
                    ui.notify(
                        f"Could not update {BROWSER_SHORTCUTS_PATH}", type="negative"
                    )

            def _remove_shortcut(index: int) -> None:
                removed = remove_browser_shortcut(index)
                if removed is not None:
                    ui.notify(f"Removed shortcut: {removed['label']}", type="positive")
                    render_browser_body.refresh()
                else:
                    ui.notify(
                        f"Could not update {BROWSER_SHORTCUTS_PATH}", type="negative"
                    )

            @ui.refreshable
            def render_browser_body() -> None:
                with ui.row().classes("items-center gap-1 flex-wrap w-full"):
                    for i, (label, full) in enumerate(breadcrumb_segments(browser_state["path"])):
                        if i > 0:
                            ui.label("/").classes("text-gray-500")
                        ui.label(label).classes(
                            "text-sm cursor-pointer hover:underline secure-link"
                        ).on("click", lambda full=full: _jump_to(full))
                with ui.row().classes("items-center gap-2"):
                    parent = os.path.dirname(browser_state["path"].rstrip(os.sep)) or os.sep
                    up_button = ui.button(
                        "Up", icon="arrow_upward", on_click=lambda: _go_up()
                    ).props("no-caps flat")
                    if parent == browser_state["path"]:
                        up_button.disable()
                    ui.button("Home", icon="home", on_click=lambda: _go_home()).props(
                        "no-caps flat"
                    )
                with ui.row().classes("w-full items-center justify-between gap-2"):
                    ui.label("Shortcuts").classes(
                        "text-xs uppercase tracking-wide text-gray-500"
                    )
                    ui.button(
                        "Add current",
                        icon="add",
                        on_click=_add_current_folder_shortcut,
                    ).props("dense flat no-caps").classes(
                        "browser-shortcut-action"
                    ).tooltip("Add the current folder as a custom shortcut")
                shortcuts = load_browser_shortcuts()
                with ui.row().classes(
                    "browser-shortcuts items-start gap-3 flex-wrap w-full"
                ):
                    for group_name, group_display in BROWSER_SHORTCUT_GROUPS.items():
                        grouped = [
                            (index, shortcut)
                            for index, shortcut in enumerate(shortcuts)
                            if shortcut["group"] == group_name
                        ]
                        if not grouped:
                            continue
                        with ui.column().classes(
                            "browser-shortcut-group gap-1 items-start"
                        ):
                            with ui.row().classes("items-center gap-1"):
                                ui.icon(group_display["icon"]).classes(
                                    "text-sm text-gray-500"
                                )
                                ui.label(group_display["label"]).classes(
                                    "text-xs text-gray-500"
                                )
                            with ui.row().classes("items-center gap-1 flex-wrap"):
                                for index, shortcut in grouped:
                                    shortcut_path = shortcut["path"]
                                    with ui.row().classes("items-center gap-0 no-wrap"):
                                        chip = ui.button(
                                            shortcut["label"],
                                            icon=group_display["icon"],
                                            on_click=lambda path=shortcut_path: _jump_to(path),
                                        ).props("dense no-caps outline").classes(
                                            "browser-shortcut-main"
                                        )
                                        if not shortcut["available"]:
                                            chip.disable()
                                            chip.tooltip(
                                                f"Folder is not available: {shortcut['label']}"
                                            )
                                        ui.button(
                                            icon="close",
                                            on_click=lambda index=index: _remove_shortcut(index),
                                        ).props("dense flat").classes(
                                            "browser-shortcut-remove"
                                        ).tooltip(f"Remove {shortcut['label']}")
                with ui.scroll_area().classes("w-full h-72 border rounded"):
                    entries = list_browser_entries(browser_state["path"])
                    if not entries:
                        ui.label("(no supported files or folders)").classes(
                            "text-gray-500 text-sm"
                        )
                    for entry in entries:
                        name = entry["name"]
                        hidden = name.startswith(".")
                        row_classes = (
                            "w-full items-center gap-2 px-2 py-1 cursor-pointer "
                            "hover:bg-white/10 rounded"
                        )
                        if hidden:
                            row_classes += " opacity-50"
                        row = ui.row().classes(row_classes)
                        with row:
                            ui.icon(
                                "folder" if entry["is_dir"] else "description"
                            ).classes("text-base")
                            ui.label(name).classes("text-sm")
                        if entry["is_dir"]:
                            row.on("dblclick", lambda name=name: _descend(name))
                        else:
                            row.on("click", lambda name=name: _select_file(name))

            def _select_current_folder() -> None:
                path_input.value = browser_state["path"]
                folder_browser_dialog.close()

            with folder_browser_dialog, ui.card().classes(
                "w-full max-w-2xl secure-dialog"
            ):
                ui.label("Choose a file or folder").classes(
                    "text-lg font-semibold"
                )
                render_browser_body()
                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=folder_browser_dialog.close).props("flat")
                    ui.button("Select This Folder", on_click=_select_current_folder)

            async def _browse() -> None:
                current = path_input.value.strip() if path_input.value else ""
                translated = translate_windows_path(current)
                if translated != current:
                    path_input.value = translated
                expanded = os.path.expanduser(translated)
                browser_state["path"] = (
                    expanded
                    if expanded and os.path.isdir(expanded)
                    else (
                        os.path.dirname(expanded)
                        if expanded and os.path.isfile(expanded)
                        else windows_profile_directory()
                    )
                )
                render_browser_body.refresh()
                folder_browser_dialog.open()

            async def _browse_windows() -> None:
                windows_browse_button.disable()
                try:
                    status, selected_path, message = await run.io_bound(
                        request_windows_folder_pick
                    )
                    if status == "selected" and selected_path:
                        path_input.value = selected_path
                        ui.notify("Windows folder selected.", type="positive")
                    elif status != "cancelled":
                        ui.notify(
                            message,
                            type="warning" if status == "unreachable" else "negative",
                        )
                finally:
                    windows_browse_button.enable()

            browse_button.on_click(_browse)
            windows_browse_button.on_click(_browse_windows)

            ui.separator().classes("control-divider")

            verify_switch = ui.switch(
                "Experimental AI verification (default OFF)",
                value=persisted_settings["verify_on"],
            ).props("color=blue-grey-5")
            verify_switch.tooltip(
                "Runs a local Ollama qwen2.5:3b model over ambiguous findings "
                "after detection and may demote likely false positives. "
                "This experimental layer is off by default; enabling it can "
                "lower correct findings to LOW. Requires Ollama running "
                "locally and degrades to off if it isn't. Optional second "
                "opinion; may demote correct findings."
            )
            run_ner_switch = ui.switch(
                "Semantic NER (GLiNER)",
                value=persisted_settings["run_ner_on"],
            ).props("color=blue-grey-5")
            run_ner_switch.tooltip(
                "Runs GLiNER to detect person/organization/location/date "
                "entities. Turning this off skips that layer entirely — "
                "faster, but misses entity-only findings (files with no "
                "identifier-type PII may then score lower or find nothing)."
            )

            ui.separator().classes("control-divider")
            with ui.expansion("Advanced", icon="tune").classes(
                "advanced-controls w-full"
            ):
                with ui.element("div").classes("advanced-layout"):
                    with ui.element("div").classes("advanced-col advanced-workers"):
                        saved_max_workers = persisted_settings["max_workers"]
                        workers_label = ui.label(
                            f"Worker threads · {saved_max_workers}"
                        ).classes("text-xs text-gray-400")
                        workers_slider = ui.slider(
                            min=1, max=16, value=saved_max_workers
                        ).props("color=blue-grey-5")
                        workers_slider.on_value_change(
                            lambda e: setattr(
                                workers_label,
                                "text",
                                f"Worker threads · {int(e.value or config.DEFAULT_MAX_WORKERS)}",
                            )
                        )
                        saved_ocr_workers = persisted_settings["ocr_workers"]
                        ocr_workers_label = ui.label(
                            f"OCR workers · {saved_ocr_workers}"
                        ).classes("text-xs text-gray-400 mt-2")
                        ocr_workers_slider = ui.slider(
                            min=1, max=8, value=saved_ocr_workers
                        ).props("color=blue-grey-5")
                        ocr_workers_slider.on_value_change(
                            lambda e: setattr(
                                ocr_workers_label,
                                "text",
                                f"OCR workers · {int(e.value or 4)}",
                            )
                        )

                    with ui.element("div").classes("advanced-col advanced-limits"):
                        max_file_size_input = ui.number(
                            "Max file size (MB)", value=10, min=1, format="%.0f"
                        ).classes("w-full")
                        ner_max_chars_input = ui.number(
                            "NER character limit",
                            value=persisted_settings["ner_max_chars"],
                            min=10_000,
                            max=1_000_000,
                            step=10_000,
                            format="%.0f",
                        ).classes("w-full")
                        ner_max_chars_input.tooltip(
                            "GLiNER analyzes up to this many characters per file; "
                            "larger values cover more of big documents but scan "
                            "slower. Files exceeding the limit are marked 'NER "
                            "truncated' in reports."
                        )

                    with ui.element("div").classes("file-types-panel panel-filetypes"):
                        ui.separator().classes("control-divider")
                        with ui.row().classes("w-full items-center justify-between"):
                            filetypes_header = ui.label("File types").classes(
                                "text-sm text-gray-300"
                            )
                            filetypes_header.tooltip(
                                "Checked extensions are included in the next scan."
                            )
                            all_file_types_button = ui.button("Select none").props(
                                "flat dense no-caps size=sm color=blue-grey-4"
                            )
                        extension_checkboxes: Dict[str, Any] = {}
                        family_toggle_buttons: Dict[str, Any] = {}
                        selected_file_types = set(persisted_settings["file_types"])
                        with ui.element("div").classes("file-type-families"):
                            for family, extensions in FILE_TYPE_GROUPS.items():
                                family_classes = (
                                    "file-type-family file-type-family-wide"
                                    if family == "Text & code"
                                    else "file-type-family"
                                )
                                with ui.element("div").classes(family_classes):
                                    with ui.row().classes(
                                        "w-full items-center justify-between no-wrap"
                                    ):
                                        ui.label(family).classes("file-type-header")
                                        family_toggle_buttons[family] = ui.button(
                                            "None"
                                        ).props(
                                            "flat dense no-caps size=sm color=blue-grey-4"
                                        )
                                    with ui.element("div").classes("file-type-grid"):
                                        for extension in extensions:
                                            extension_checkboxes[extension] = ui.checkbox(
                                                extension,
                                                value=extension in selected_file_types,
                                            ).props("dense color=blue-grey-5")
                    with ui.element("div").classes("file-types-panel panel-layers"):
                        ui.separator().classes("control-divider")
                        with ui.row().classes("w-full items-center justify-between"):
                            layers_header = ui.label("Detection layers").classes(
                                "text-sm text-gray-300"
                            )
                            layers_header.tooltip(
                                "A deselected layer does not run at all — faster, "
                                "but findings and reconciliation between layers "
                                "only reflect the layers that ran (see report "
                                "header)."
                            )
                            all_layers_button = ui.button("Select none").props(
                                "flat dense no-caps size=sm color=blue-grey-4"
                            )
                        layer_checkboxes: Dict[str, Any] = {}
                        selected_layers = set(persisted_settings["layers"])
                        with ui.element("div").classes(
                            "file-type-family file-type-family-wide"
                        ):
                            with ui.element("div").classes("layer-grid"):
                                for layer in DETECTION_LAYERS:
                                    layer_checkboxes[layer] = ui.checkbox(
                                        layer,
                                        value=layer in selected_layers,
                                    ).props("dense color=blue-grey-5")

            next_scan_label = ui.label(
                settings_summary(
                    verify_switch.value,
                    run_ner_switch.value,
                    workers_slider.value or config.DEFAULT_MAX_WORKERS,
                    ner_max_chars_input.value or config.NER_MAX_CHARS,
                    [
                        extension
                        for extension in FILE_TYPE_EXTENSIONS
                        if extension_checkboxes[extension].value
                    ],
                    [
                        layer
                        for layer in DETECTION_LAYERS
                        if layer_checkboxes[layer].value
                    ],
                )
            ).classes("text-sm next-scan-summary mono-display")

            def _selected_file_types() -> List[str]:
                return [
                    extension
                    for extension in FILE_TYPE_EXTENSIONS
                    if extension_checkboxes[extension].value
                ]

            def _set_file_types(file_types: Any) -> None:
                selected = set(normalize_file_types(file_types))
                for extension, checkbox in extension_checkboxes.items():
                    checkbox.value = extension in selected

            def _update_file_type_buttons() -> None:
                selected = set(_selected_file_types())
                for family, extensions in FILE_TYPE_GROUPS.items():
                    family_toggle_buttons[family].text = (
                        "None"
                        if all(extension in selected for extension in extensions)
                        else "All"
                    )
                all_file_types_button.text = (
                    "Select none"
                    if len(selected) == len(FILE_TYPE_EXTENSIONS)
                    else "Select all"
                )

            def _selected_layers() -> List[str]:
                return [
                    layer for layer in DETECTION_LAYERS if layer_checkboxes[layer].value
                ]

            def _set_layers(layers: Any) -> None:
                selected = set(normalize_layers(layers))
                for layer, checkbox in layer_checkboxes.items():
                    checkbox.value = layer in selected

            def _update_layer_buttons() -> None:
                selected = set(_selected_layers())
                all_layers_button.text = (
                    "Select none"
                    if len(selected) == len(DETECTION_LAYERS)
                    else "Select all"
                )

            def _persist_controls() -> None:
                selected_file_types = _selected_file_types()
                selected_layers = _selected_layers()
                save_gui_settings(
                    {
                        "verify_on": bool(verify_switch.value),
                        "run_ner_on": bool(run_ner_switch.value),
                        "max_workers": int(
                            workers_slider.value or config.DEFAULT_MAX_WORKERS
                        ),
                        "ocr_workers": int(ocr_workers_slider.value or 4),
                        "ner_max_chars": int(
                            ner_max_chars_input.value or config.NER_MAX_CHARS
                        ),
                        "file_types": selected_file_types,
                        "layers": selected_layers,
                    }
                )
                next_scan_label.text = settings_summary(
                    verify_switch.value,
                    run_ner_switch.value,
                    workers_slider.value or config.DEFAULT_MAX_WORKERS,
                    ner_max_chars_input.value or config.NER_MAX_CHARS,
                    selected_file_types,
                    selected_layers,
                )
                _update_file_type_buttons()
                _update_layer_buttons()

            def _toggle_file_type_family(family: str) -> None:
                _set_file_types(
                    toggle_file_type_family(_selected_file_types(), family)
                )
                _persist_controls()

            def _toggle_all_file_types() -> None:
                _set_file_types(toggle_all_file_types(_selected_file_types()))
                _persist_controls()

            def _toggle_all_layers() -> None:
                _set_layers(toggle_all_layers(_selected_layers()))
                _persist_controls()

            verify_switch.on_value_change(lambda _e: _persist_controls())
            run_ner_switch.on_value_change(lambda _e: _persist_controls())
            workers_slider.on_value_change(lambda _e: _persist_controls())
            ocr_workers_slider.on_value_change(lambda _e: _persist_controls())
            ner_max_chars_input.on_value_change(lambda _e: _persist_controls())
            for checkbox in extension_checkboxes.values():
                checkbox.on_value_change(lambda _e: _persist_controls())
            for family, button in family_toggle_buttons.items():
                button.on_click(
                    lambda _e, family=family: _toggle_file_type_family(family)
                )
            all_file_types_button.on_click(lambda _e: _toggle_all_file_types())
            for checkbox in layer_checkboxes.values():
                checkbox.on_value_change(lambda _e: _persist_controls())
            all_layers_button.on_click(lambda _e: _toggle_all_layers())
            _update_file_type_buttons()
            _update_layer_buttons()

            scan_button = ui.button("Scan").props(
                "color=primary unelevated no-caps"
            ).classes("w-full")

        # ---------------- Status / results card (right) ----------------
        with ui.card().classes(
            "results-card dashboard-card gap-4"
        ):
            render_status()

    def _request_cancel() -> None:
        with _scan_state_lock:
            ev = _scan_state.get("cancel_event")
        if ev is not None and not ev.is_set():
            ev.set()
            with _scan_state_lock:
                _scan_state["cancel_requested"] = True
            ui.notify("Cancelling — finishing files already in progress…")
            _sync_ui()

    async def _start_scan() -> None:
        entered_target = (path_input.value or "").strip()
        target = translate_windows_path(entered_target)

        if not target:
            ui.notify("Enter a file or folder path to scan.", type="warning")
            return
        if target != entered_target:
            path_input.value = target
        expanded = os.path.expanduser(target)
        if not (os.path.isdir(expanded) or os.path.isfile(expanded)):
            ui.notify(f"Not a valid file or folder: {target}", type="warning")
            return

        if not _scan_lock.acquire(blocking=False):
            ui.notify("A scan is already running — please wait for it to finish.", type="warning")
            return

        kwargs = build_scan_kwargs(
            verify_switch.value,
            run_ner_switch.value,
            workers_slider.value or config.DEFAULT_MAX_WORKERS,
            ocr_workers_slider.value or 4,
            max_file_size_input.value or 10,
            ner_max_chars_input.value or config.NER_MAX_CHARS,
            _selected_file_types(),
            _selected_layers(),
        )
        _persist_controls()
        cancel_event = threading.Event()
        with _scan_state_lock:
            _scan_state.clear()
            _scan_state.update(running_scan_state(bool(verify_switch.value)))
            _scan_state["cancel_event"] = cancel_event
        _sync_ui()

        threading.Thread(
            target=_scan_worker,
            args=(expanded, kwargs, cancel_event),
            daemon=True,
        ).start()

    async def _on_scan_button_click() -> None:
        if _scan_state["phase"] == "running":
            _request_cancel()
        else:
            await _start_scan()

    scan_button.on_click(_on_scan_button_click)

    def _sync_button() -> None:
        if not client_is_active(page_client):
            return
        if _scan_state["phase"] == "running":
            cancelling = bool(_scan_state.get("cancel_requested"))
            scan_button.text = "CANCELLING…" if cancelling else "Cancel"
            scan_button.props("color=negative")
            if cancelling:
                scan_button.disable()
            else:
                scan_button.enable()
        else:
            scan_button.text = "Scan"
            scan_button.props("color=primary")
            scan_button.enable()

    def _sync_ui() -> None:
        if not client_is_active(page_client):
            return
        _sync_button()
        render_status.refresh()

    _sync_ui()

    _last_seen_phase = {"value": _scan_state["phase"]}

    def _tick() -> None:
        if not client_is_active(page_client):
            return
        phase = _scan_state["phase"]
        _sync_button()
        if phase == "running" or phase != _last_seen_phase["value"]:
            render_status.refresh()
            recent_select.set_options(load_recent_folders())
        _last_seen_phase["value"] = phase

    ui.timer(1.0, _tick)


if __name__ in {"__main__", "__mp_main__"}:
    try:
        # Default to loopback for direct execution. Containers set
        # SECURESCAN_GUI_HOST=0.0.0.0 so published ports can reach the server.
        ui.run(host=GUI_HOST, port=8080, title="SecureScan", favicon="🛡️", reload=False)
    except KeyboardInterrupt:
        pass
