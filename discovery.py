#!/usr/bin/env python3
# discovery.py
"""
==============================================================
PII Scanner – Discovery and Scan Orchestration
==============================================================

Purpose:
--------
Handles directory scanning, file extraction, PII detection,
and coordination of report generation (Markdown + JSON).

Now enhanced with:
  • Hybrid 11-layer detection (regex + keyword + secrets + health card +
    passport + UCI + status card + driver's license + deterministic OCR
    recovery + MRZ + GLiNER semantic NER)
  • Multithreading for concurrent file scanning
  • Thread-safe aggregation of results + skipped files
  • Taxonomy-based risk categorization

==============================================================
"""

import contextlib
import os
import subprocess
import threading
from datetime import datetime
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

# --- SystemMonitor ---
from system_monitor import SystemMonitor

# --- Internal project imports ---
from detectors.hybrid_detector import detect_pii_hybrid, ALL_LAYERS  # ← NEW: Hybrid detection
from detectors import llm_verifier
from detectors.mismatch_alarm import evaluate_mismatch_alarm
from scoring import score_file
from report_generator import (
    generate_markdown, _skipped_count, _all_findings, _risk_of, peak_resource_line,
)
from report_json import generate_json
from report_html import generate_html
from extractors.text_extractor import extract_text, TEXT_EXTENSIONS, looks_like_binary
from extractors.docx_extractor import extract_docx
from extractors.xlsx_extractor import extract_xlsx
from extractors.pdf_extractor import extract_pdf
from extractors.image_extractor import extract_image
from extractors.eml_extractor import extract_eml
from extractors.pptx_extractor import extract_pptx
from extractors.errors import ExtractionError

import time

# Import the central thread setting from config.py
import config
from config import DEFAULT_MAX_WORKERS, DEFAULT_OCR_WORKERS, GLINER_SKIP_EXTENSIONS


# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
MAX_FILE_SIZE_MB = 10  # files over this size are skipped
SUPPORTED_EXTENSIONS = {
    ".txt",
    ".csv",
    ".json",
    ".py",
    ".md",
    ".log",
    ".docx",
    ".xlsx",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tiff",
    ".tif",
    ".bmp",
    ".gif",
    ".webp",
    ".eml",
    ".pptx",
}


class ScanReportPath(str):
    """HTML report path carrying scan summary counts for thin CLI wrappers."""

    def __new__(cls, path, scanned_count=0, failed_count=0, skipped_count=0):
        obj = super().__new__(cls, path)
        obj.scanned_count = scanned_count
        obj.failed_count = failed_count
        obj.skipped_count = skipped_count
        return obj

# Audit finding #5: reports contain plaintext PII and must never become fresh
# scan input. Keep the exclusion and the report writer on this same resolved
# absolute directory so the boundary cannot drift with relative-path handling.
EXCLUDED_REPORT_OUTPUT_DIR = os.path.realpath(os.path.abspath("outputs"))
EXCLUDED_SYSTEM_MONITOR_LOG = os.path.realpath(
    os.path.join(EXCLUDED_REPORT_OUTPUT_DIR, "logs", "system_monitor.log")
)


def latest_report_path() -> str:
    """The most-recent-report pointer path, resolved against the CURRENT
    EXCLUDED_REPORT_OUTPUT_DIR (not frozen at import time — tests patch that
    attribute, and a module-level constant would silently ignore the patch
    and write to the real outputs/ directory instead of the test fixture)."""
    return os.path.join(EXCLUDED_REPORT_OUTPUT_DIR, "latest.html")


def _replace_latest_report(html_document: str) -> None:
    """Atomically publish the already-rendered HTML as ``latest.html``.

    ``generate_html()`` returns only after it has written the uniquely named
    report.  Publishing that returned document avoids trying to copy a path
    whose write has not completed, while ``os.replace`` prevents readers from
    observing a partially written convenience pointer.
    """
    latest_path = latest_report_path()
    temporary_path = f"{latest_path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8") as fh:
            fh.write(html_document)
        os.replace(temporary_path, latest_path)
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


def _report_timestamp() -> str:
    """Return a collision-resistant timestamp for a folder-scan report."""
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _dated_output_dir() -> str:
    """Today's report subdirectory: outputs/YYYY-MM-DD/, keeping outputs/
    from accumulating thousands of flat report_*.{md,json,html} files."""
    return os.path.join(EXCLUDED_REPORT_OUTPUT_DIR, datetime.now().strftime("%Y-%m-%d"))


def _git_short_hash() -> str:
    """Best-effort short commit hash of the running checkout, for report
    provenance (see report_*.py's `provenance` parameter). Never raises —
    a non-git checkout or missing git binary just shows as "unknown".

    "-c safe.directory=*" scopes past git's ownership check for this one
    invocation only (no global/system config touched): the containers run
    as root over a bind mount owned by the host user, which git's dubious-
    ownership guard would otherwise refuse outright.
    """
    try:
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        if result.returncode == 0:
            commit = result.stdout.strip()
            if commit:
                return commit
    except Exception:
        pass
    return "unknown"


# -------------------------------------------------------------------
# STEP 1 – DISCOVER FILES
# -------------------------------------------------------------------
def _count_files(root_path: str) -> int:
    """Count regular directory entries below an excluded tree."""
    count = 0
    for _, _, filenames in os.walk(root_path, followlinks=False):
        count += len(filenames)
    return count


def discover_files(root_path: str, return_details: bool = False):
    """Recursively scan a folder while excluding the active report directory."""
    abs_root = os.path.abspath(root_path)
    if not os.path.exists(abs_root):
        raise FileNotFoundError(f"[!] Path not found: {abs_root}")

    discovered = []
    details = {"excluded_output_files": 0, "skipped_files": []}
    for dirpath, dirnames, filenames in os.walk(abs_root, followlinks=False):
        resolved_dir = os.path.realpath(dirpath)
        if resolved_dir == EXCLUDED_REPORT_OUTPUT_DIR:
            details["excluded_output_files"] += len(filenames)
            details["excluded_output_files"] += sum(
                _count_files(os.path.join(dirpath, dirname)) for dirname in dirnames
            )
            dirnames[:] = []
            continue

        kept_dirs = []
        for dirname in dirnames:
            candidate = os.path.join(dirpath, dirname)
            # Audit finding #3: os.walk(followlinks=False) does not follow a
            # symlinked directory, but it still yields the entry (and always
            # yields symlinked files). Deny all symlinks at the boundary in v1.
            # A future explicit opt-in flag may revisit this policy.
            if os.path.islink(candidate):
                details["skipped_files"].append(
                    {
                        "file": os.path.abspath(candidate),
                        "reason": "symlink",
                        "scan_status": "skipped",
                    }
                )
            elif os.path.realpath(candidate) == EXCLUDED_REPORT_OUTPUT_DIR:
                details["excluded_output_files"] += _count_files(candidate)
            else:
                kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for fname in filenames:
            path = os.path.join(dirpath, fname)
            if os.path.islink(path):
                details["skipped_files"].append(
                    {
                        "file": os.path.abspath(path),
                        "reason": "symlink",
                        "scan_status": "skipped",
                    }
                )
                continue
            if os.path.realpath(path) == EXCLUDED_SYSTEM_MONITOR_LOG:
                details["excluded_output_files"] += 1
                continue
            _, ext = os.path.splitext(fname.lower())
            if ext in SUPPORTED_EXTENSIONS:
                discovered.append(path)

    discovered.sort()
    if return_details:
        return discovered, details
    return discovered


# -------------------------------------------------------------------
# STEP 2 – SCAN A SINGLE FILE
# -------------------------------------------------------------------
def scan_file(
    fpath: str,
    verify: Optional[bool] = None,
    return_text: bool = False,
    run_ner: Optional[bool] = None,
    ner_max_chars: Optional[int] = None,
    ocr_semaphore: Optional[threading.Semaphore] = None,
    enabled_layers: Optional[frozenset] = None,
) -> Dict[str, Any]:
    """
    Extracts, analyzes, and scores a single file using hybrid detection.

    Args:
        fpath: Path to the file to scan.
        enabled_layers: Passed through unchanged to
            detectors.hybrid_detector.detect_pii_hybrid()'s parameter of the
            same name. None (default) runs every layer, byte-identical to
            behavior before this parameter existed.
        ocr_semaphore: Optional threading.Semaphore bounding how many files
            may run OCR-capable extraction (image/PDF) concurrently, across
            all scan_file() workers sharing the same semaphore instance.
            None (default — every standalone caller) means no throttling,
            exactly as before this parameter existed; scan_folder() creates
            one semaphore per call, sized by its ocr_workers parameter, and
            passes it to every worker.
        run_ner: Override for the GLiNER semantic-NER gate. None (default,
            used by every caller that doesn't pass it explicitly) preserves
            the original per-file logic: on unless ext is in
            GLINER_SKIP_EXTENSIONS. False forces GLiNER off for this file
            regardless of extension. True forces the same per-file logic as
            None — GLINER_SKIP_EXTENSIONS still applies even when "forcing
            on", since running NER on code/structured files adds no value
            regardless of what the caller wants. Only False actually changes
            behavior relative to the default.
        ner_max_chars: Optional per-file GLiNER character cap. None uses
            config.NER_MAX_CHARS and preserves the existing behavior.
        verify: Whether to run the LLM verification layer INLINE, as part of
            this call's own detect_pii_hybrid() invocation. When None
            (default — used by direct/standalone callers like the TUI's
            single-file scan, where there is no concurrent Ollama contention
            to worry about), it is resolved via
            llm_verifier.check_availability() using config defaults, cached
            per-process.
            scan_folder() always passes verify=False here, even when the
            scan as a whole has verification enabled — running the LLM judge
            inside a per-file worker means up to DEFAULT_MAX_WORKERS threads
            can call the single-slot Ollama server at once, which queues past
            OLLAMA_TIMEOUT_S and produces a high error rate (see
            tests/external_enron/EVALUATION.md finding #2). Instead
            scan_folder() runs one sequential verification pass over all
            files' matches AFTER the thread pool completes (see
            return_text below), and re-scores each affected file itself.
        return_text: When True, include the extracted text in the returned
            dict under the "_text" key. Used only by scan_folder()'s
            sequential post-pass, which needs the original text back to
            build LLM verification context without re-extracting it. Never
            set by standalone callers; the caller must strip "_text" before
            handing results to a report generator.

    HARDENED VERSION - Now handles:
    - None input
    - Non-string types (int, bool, list, dict, etc.)
    - Empty or whitespace-only paths
    - Non-existent files
    - Directories instead of files
    - FILES DELETED DURING SCAN (race condition)
    """

    # FIX 1: Validate input type
    if not isinstance(fpath, str):
        return {
            "file": str(fpath) if fpath is not None else "None",
            "scan_status": "extraction_failed",
            "failure_reason": f"TypeError: invalid input type {type(fpath).__name__}",
            "matches": {},
            "score": None,
            "metadata": {"error": f"Invalid input type: {type(fpath).__name__}"},
        }

    # FIX 2: Validate non-empty string
    if not fpath or not fpath.strip():
        return {
            "file": "",
            "scan_status": "extraction_failed",
            "failure_reason": "ValueError: empty file path",
            "matches": {},
            "score": None,
            "metadata": {"error": "Empty file path"},
        }

    # FIX 3: Wrap entire operation in try-except for race conditions
    try:
        extractor_map = {
            ".txt": extract_text,
            ".csv": extract_text,
            ".json": extract_text,
            ".py": extract_text,
            ".md": extract_text,
            ".log": extract_text,
            ".docx": extract_docx,
            ".xlsx": extract_xlsx,
            ".pdf": extract_pdf,
            ".png": extract_image,
            ".jpg": extract_image,
            ".jpeg": extract_image,
            ".tiff": extract_image,
            ".tif": extract_image,
            ".bmp": extract_image,
            ".gif": extract_image,
            ".webp": extract_image,
            ".eml": extract_eml,
            ".pptx": extract_pptx,
        }

        _, ext = os.path.splitext(fpath.lower())
        extractor = extractor_map.get(ext, extract_text)

        # Images always OCR; PDFs may OCR internally for scanned pages. Both
        # are gated by ocr_semaphore (when the caller provided one) so a scan
        # doesn't run more concurrent PaddleOCR passes than ocr_workers,
        # independent of how many scan threads (max_workers) are active.
        is_ocr_capable = ext in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif", ".webp", ".pdf")
        sem_ctx = (
            ocr_semaphore if (ocr_semaphore is not None and is_ocr_capable) else contextlib.nullcontext()
        )
        try:
            # For images, get Paddle confidence and extraction details along
            # with the raw recognized text.
            with sem_ctx:
                if ext in [".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif", ".webp"]:
                    text, confidence, ocr_details = extractor(
                        fpath, return_confidence=True, return_details=True
                    )
                elif ext == ".pdf":
                    text, ocr_details = extractor(fpath, return_details=True)
                    confidence = None
                elif ext == ".eml":
                    text, ocr_details = extractor(fpath, return_details=True)
                    confidence = None
                elif ext == ".pptx":
                    text, ocr_details = extractor(fpath, return_details=True)
                    confidence = None
                else:
                    text = extractor(fpath)
                    confidence = None
                    ocr_details = None
        except ExtractionError as e:
            print(f"[!] Extraction failed for {fpath}: {e}")
            metadata = {}
            if ext in [".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif", ".webp"]:
                try:
                    meta = extractor(fpath, metadata_only=True)
                    metadata = meta if isinstance(meta, dict) else {}
                except Exception:
                    pass
            return {
                "file": os.path.abspath(fpath),
                "scan_status": "extraction_failed",
                "failure_reason": str(e),
                "matches": {},
                "score": None,
                "metadata": metadata,
                "mismatch_alarm": evaluate_mismatch_alarm(
                    fpath, "", {}, metadata=metadata
                ),
            }

        # ============================================================
        # Run the resolved eleven-layer hybrid detector selection.
        # ============================================================
        # Gate the GLiNER (semantic NER) layer by file type: skip it for code
        # and structured/machine files where it adds little value. All other
        # layers still run on every file. See GLINER_SKIP_EXTENSIONS in config.
        # run_ner (the parameter) is None on every caller that doesn't pass it
        # explicitly, which reduces to the original per-file logic below —
        # only an explicit False changes anything.
        run_ner = False if run_ner is False else (ext not in GLINER_SKIP_EXTENSIONS)

        # Resolve LLM verification once (cached) when not already resolved by
        # the caller (scan_folder resolves this once per scan and passes it
        # in explicitly instead).
        verify_enabled = verify
        if verify_enabled is None:
            verify_enabled, _status = llm_verifier.check_availability()

        matches = detect_pii_hybrid(
            text,
            run_ner=run_ner,
            verify=verify_enabled,
            ner_max_chars=ner_max_chars,
            enabled_layers=enabled_layers,
        )

        # Calculate score from hybrid results
        score = score_file(matches)

        # Extract file metadata
        metadata = {}
        if ext in [
            ".docx",
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
            ".tiff",
            ".tif",
            ".bmp",
            ".gif",
            ".webp",
            ".eml",
            ".pptx",
        ]:
            try:
                meta = extractor(fpath, metadata_only=True)
                metadata = meta if isinstance(meta, dict) else {}
            except Exception:
                pass

        # Add OCR confidence + extraction strategy details to metadata for images
        if confidence is not None:
            metadata["ocr_confidence"] = round(confidence, 1)
            metadata["ocr_attempted"] = True
        if ocr_details:
            metadata.update(ocr_details)
        match_metadata = matches.get("_metadata", {})
        disabled_layers = match_metadata.get("disabled_layers", []) or []
        detection_degraded = bool(match_metadata.get("detection_degraded"))
        if detection_degraded:
            failed_layers = match_metadata.get("failed_layers", [])
            print(
                f"[!] Detection degraded for {fpath}: "
                f"{', '.join(failed_layers) or 'unknown'} layer(s) failed"
            )
        if ner_max_chars is not None and match_metadata.get("ner_truncated"):
            metadata["ner_truncated"] = True
            metadata["ner_analyzed_chars"] = match_metadata.get(
                "ner_analyzed_chars", int(ner_max_chars)
            )

        result = {
            "file": os.path.abspath(fpath),
            "scan_status": "scanned",
            "failure_reason": None,
            "matches": matches,
            "score": score,
            "metadata": metadata,
            "detection_degraded": detection_degraded,
            "failed_layers": match_metadata.get("failed_layers", []),
            "disabled_layers": disabled_layers,
            "mismatch_alarm": evaluate_mismatch_alarm(
                fpath, text, matches, metadata=metadata, disabled_layers=disabled_layers
            ),
        }
        if return_text:
            result["_text"] = text
        return result

    except FileNotFoundError:
        # FIX 4: Handle race condition - file deleted during scan
        return {
            "file": fpath,
            "scan_status": "extraction_failed",
            "failure_reason": "FileNotFoundError: file deleted during scan",
            "matches": {},
            "score": None,
            "metadata": {"error": "File deleted during scan (race condition)"},
        }
    except PermissionError:
        # FIX 5: Handle permission errors gracefully
        return {
            "file": fpath,
            "scan_status": "extraction_failed",
            "failure_reason": "PermissionError: permission denied",
            "matches": {},
            "score": None,
            "metadata": {"error": "Permission denied"},
        }
    except Exception as e:
        # FIX 6: Catch-all for unexpected errors
        print(f"[!] Unexpected error scanning {fpath}: {type(e).__name__}: {e}")
        return {
            "file": fpath,
            "scan_status": "extraction_failed",
            "failure_reason": f"{type(e).__name__}: {str(e)}",
            "matches": {},
            "score": None,
            "metadata": {"error": f"{type(e).__name__}: {str(e)}"},
        }


# -------------------------------------------------------------------
# STEP 3 – SCAN ENTIRE FOLDER (multithreaded)
# -------------------------------------------------------------------
def scan_folder(
    folder_path: str,
    verify: Optional[bool] = None,
    progress_callback: Optional["callable"] = None,
    run_ner: Optional[bool] = None,
    ner_max_chars: Optional[int] = None,
    max_workers: Optional[int] = None,
    max_file_size_mb: Optional[float] = None,
    ocr_workers: Optional[int] = None,
    cancel_event: Optional[threading.Event] = None,
    extensions: set[str] | None = None,
    enabled_layers: frozenset[str] | set[str] | None = None,
) -> "str | None":
    """Run a folder scan and always release its system monitor."""
    monitor = SystemMonitor()
    try:
        return _scan_folder_impl(
            folder_path,
            verify=verify,
            monitor=monitor,
            progress_callback=progress_callback,
            run_ner=run_ner,
            ner_max_chars=ner_max_chars,
            max_workers=max_workers,
            max_file_size_mb=max_file_size_mb,
            ocr_workers=ocr_workers,
            cancel_event=cancel_event,
            extensions=extensions,
            enabled_layers=enabled_layers,
        )
    finally:
        monitor.stop()


def scan_path(
    target_path: str,
    verify: Optional[bool] = None,
    progress_callback: Optional["callable"] = None,
    run_ner: Optional[bool] = None,
    ner_max_chars: Optional[int] = None,
    max_workers: Optional[int] = None,
    max_file_size_mb: Optional[float] = None,
    ocr_workers: Optional[int] = None,
    cancel_event: Optional[threading.Event] = None,
    extensions: set[str] | None = None,
    enabled_layers: frozenset[str] | set[str] | None = None,
) -> "str | None":
    """Scan one file or a folder through the same full report pipeline.

    Audit #9: the old single-file CLI route wrote only Markdown and bypassed
    folder-scan orchestration. Directory targets remain a direct delegation
    to scan_folder(); file targets provide a one-item discovery result to the
    unchanged scan body so scoring, verification, reports, and summaries are
    identical.
    """
    if not isinstance(target_path, str):
        raise TypeError(
            f"target_path must be a string, got {type(target_path).__name__}"
        )
    if not target_path or not target_path.strip():
        raise ValueError("target_path cannot be empty or whitespace")

    abs_target = os.path.abspath(os.path.expanduser(target_path))
    if os.path.isdir(abs_target):
        return scan_folder(
            abs_target,
            verify=verify,
            progress_callback=progress_callback,
            run_ner=run_ner,
            ner_max_chars=ner_max_chars,
            max_workers=max_workers,
            max_file_size_mb=max_file_size_mb,
            ocr_workers=ocr_workers,
            cancel_event=cancel_event,
            extensions=extensions,
            enabled_layers=enabled_layers,
        )
    if not os.path.isfile(abs_target):
        raise FileNotFoundError(f"[!] Invalid path: {abs_target}")

    monitor = SystemMonitor()
    try:
        return _scan_folder_impl(
            os.path.dirname(abs_target),
            verify=verify,
            monitor=monitor,
            progress_callback=progress_callback,
            run_ner=run_ner,
            ner_max_chars=ner_max_chars,
            max_workers=max_workers,
            max_file_size_mb=max_file_size_mb,
            ocr_workers=ocr_workers,
            cancel_event=cancel_event,
            extensions=extensions,
            enabled_layers=enabled_layers,
            files_override=[abs_target],
            discovery_label=abs_target,
        )
    finally:
        monitor.stop()


def _scan_folder_impl(
    folder_path: str,
    verify: Optional[bool],
    monitor: SystemMonitor,
    progress_callback: Optional["callable"] = None,
    run_ner: Optional[bool] = None,
    ner_max_chars: Optional[int] = None,
    max_workers: Optional[int] = None,
    max_file_size_mb: Optional[float] = None,
    ocr_workers: Optional[int] = None,
    cancel_event: Optional[threading.Event] = None,
    extensions: set[str] | None = None,
    enabled_layers: frozenset[str] | set[str] | None = None,
    files_override: Optional[List[str]] = None,
    discovery_label: Optional[str] = None,
) -> "str | None":
    """
    Full scanning workflow (multithreaded version):
      1. Discover supported files
      2. Skip oversized ones
      3. Use ThreadPoolExecutor to scan files concurrently
      4. Collect results safely and generate reports
      5. Print a clean terminal summary

    Args:
        folder_path: Directory to scan.
        verify: Overrides config.LLM_VERIFICATION_ENABLED for this run
            (the CLI's --verify/--no-verify). None uses the config default.
            Resolved ONCE here (a single Ollama availability check — see the
            "degradation contract" in detectors/llm_verifier.py), then the
            same resolved bool is passed to every scan_file() call so a
            large scan doesn't re-check availability per file.
        progress_callback: Optional callable invoked with (done, total) as
            files complete in the thread-pool pass below. None (default) is
            a no-op and leaves behavior byte-identical to before this
            parameter existed. Used by the GUI to poll scan progress; never
            set by any other caller.
            Extended ADDITIVELY (v1.2) with a third keyword-only argument,
            current_file: Optional[str] = None — the basename of the file a
            worker slot has just started processing. Called once per file at
            submission time (done/total unchanged from the last completion
            count) in addition to the existing once-per-completion call
            (which never passes current_file, i.e. it stays None on that
            call). Chosen over a second callback parameter as the least
            invasive extension: every existing caller's positional
            progress_callback(done, total) call sites are untouched, and a
            caller that only accepts two positional params is unaffected
            since current_file is keyword-only in practice (passed via
            **kwargs-style keyword, never positionally). The GUI treats a
            current_file=None call as "no new information" and keeps
            whatever file name it last saw, rather than clearing it.
            Extended ADDITIVELY again with a fourth keyword-only argument,
            completed: Optional[dict] = None — a live-ticker payload
            ({"file", "score", "risk", "top_type", "finding_count"}) passed
            on the once-per-completion call for a successfully scanned file
            (None on the submission call and on extraction failures/
            exceptions). Built from the same report_generator helpers
            (_all_findings/_risk_of) the reports themselves use, so it can
            never disagree with the written report.
        cancel_event: Optional threading.Event. None (default) is
            byte-identical to behavior before this parameter existed. When
            provided and set (from another thread) while a scan is running,
            the scheduler stops submitting new files to the thread pool as
            soon as the event is observed (checked before every new
            submission — between files, never mid-file), lets every
            already-submitted (in-flight) file finish normally, then skips
            the sequential LLM verification pass entirely (even if verify
            was requested) and proceeds straight to writing reports for
            whatever completed. The returned report's JSON summary gets a
            "scan_cancelled": true flag in that case (omitted/false
            otherwise) so the GUI can render a distinct partial-results
            state.
        run_ner: Passed through unchanged to every scan_file() call — see
            scan_file()'s docstring for the None/True/False semantics. None
            (default) is byte-identical to behavior before this parameter
            existed.
        ner_max_chars: Passed through unchanged to each scan_file() call.
            None uses config.NER_MAX_CHARS and preserves current behavior.
        max_workers: Overrides config.DEFAULT_MAX_WORKERS for this call's
            ThreadPoolExecutor. None (default) uses the config value, exactly
            as before this parameter existed.
        max_file_size_mb: Overrides the module's MAX_FILE_SIZE_MB skip
            threshold for this call. None (default) uses the module
            constant, exactly as before this parameter existed.
        ocr_workers: Overrides config.DEFAULT_OCR_WORKERS — the number of
            files allowed to run OCR-capable extraction (image/PDF)
            concurrently, independent of max_workers. None (default) uses
            the config value. Enforced via a semaphore created once per
            call and shared by every scan_file() worker.
        extensions: Optional set of filename extensions to scan. None
            preserves the existing behavior and scans every extension in
            SUPPORTED_EXTENSIONS. A provided set is intersected with
            SUPPORTED_EXTENSIONS; unknown entries are ignored with one
            count-only banner. Supported files outside the intersection are
            counted as skipped with reason "filtered" using one aggregate
            record, never one record or console line per file.
        enabled_layers: Optional set of detection-layer names (see
            detectors.hybrid_detector.ALL_LAYERS) to run for this scan. None
            (default) runs every layer, exactly as before this parameter
            existed. A provided set is intersected with ALL_LAYERS; unknown
            names are ignored with one count-only banner, mirroring the
            extensions contract above. Passed through unchanged to every
            scan_file() call, so a deselected layer never executes for any
            file in this scan — the resulting report is genuinely faster,
            not just quieter, and its findings can differ from the full scan
            beyond simply lacking that layer's own findings, because
            reconciliation (SOURCE_PRIORITY collisions) only sees the layers
            that actually ran. The resolved enabled/disabled sets are
            recorded on every report (Markdown/JSON/HTML header) so a
            filtered report can never be mistaken for a full scan.

    Returns the path to the generated HTML report (or None if no supported
    files were found) so the interactive layer can offer to open it in a
    browser.

    HARDENED VERSION - Now handles:
    - None input
    - Non-string types (int, bool, list, dict)
    - Empty strings
    - Invalid folder paths
    """
    effective_max_workers = DEFAULT_MAX_WORKERS if max_workers is None else max_workers
    effective_max_file_size_mb = (
        MAX_FILE_SIZE_MB if max_file_size_mb is None else max_file_size_mb
    )
    effective_ocr_workers = DEFAULT_OCR_WORKERS if ocr_workers is None else ocr_workers
    ocr_semaphore = threading.Semaphore(effective_ocr_workers)
    # FIX 1: Validate input type
    if not isinstance(folder_path, str):
        raise TypeError(
            f"folder_path must be a string, got {type(folder_path).__name__}"
        )

    # FIX 2: Validate non-empty string
    if not folder_path or not folder_path.strip():
        raise ValueError("folder_path cannot be empty or whitespace")

    abs_folder = os.path.abspath(os.path.expanduser(folder_path))
    if not os.path.isdir(abs_folder):
        raise NotADirectoryError(f"[!] Invalid folder: {abs_folder}")

    # Scoped to this call so repeated scan_folder() invocations in the same
    # process (e.g. the interactive menu looping over option 1) each get
    # their own timer instead of measuring elapsed time since process start.
    scan_start = time.time()

    print(f"[+] Starting discovery in: {discovery_label or abs_folder}")
    if files_override is None:
        files, discovery_details = discover_files(abs_folder, return_details=True)
    else:
        files = [
            path
            for path in files_override
            if not os.path.islink(path)
            and os.path.splitext(path.lower())[1] in SUPPORTED_EXTENSIONS
        ]
        discovery_details = {"excluded_output_files": 0, "skipped_files": []}
    requested_extensions = (
        SUPPORTED_EXTENSIONS if extensions is None else set(extensions)
    )
    unsupported_extensions = requested_extensions - SUPPORTED_EXTENSIONS
    if unsupported_extensions:
        print(
            f"[i] {len(unsupported_extensions)} unsupported extension(s) ignored"
        )
    selected_extensions = requested_extensions & SUPPORTED_EXTENSIONS
    requested_layers = ALL_LAYERS if enabled_layers is None else set(enabled_layers)
    unknown_layers = requested_layers - ALL_LAYERS
    if unknown_layers:
        print(f"[i] {len(unknown_layers)} unrecognized detection layer(s) ignored")
    active_layers = requested_layers & ALL_LAYERS
    disabled_layers_scan = sorted(ALL_LAYERS - active_layers)
    resolved_enabled_layers = frozenset(active_layers) if enabled_layers is not None else None
    filtered_count = sum(
        os.path.splitext(path.lower())[1] not in selected_extensions for path in files
    )
    if extensions is not None:
        files = [
            path
            for path in files
            if os.path.splitext(path.lower())[1] in selected_extensions
        ]
    excluded_output_files = discovery_details["excluded_output_files"]
    if excluded_output_files > 0:
        print(
            f"[i] {excluded_output_files} file(s) in the report output "
            "directory excluded from scanning"
        )
    discovery_skipped_files = discovery_details["skipped_files"]
    if discovery_skipped_files:
        print(f"[i] {len(discovery_skipped_files)} symlink(s) skipped")
    if not files and filtered_count == 0:
        print("[i] No supported files found.")
        return None

    # Start monitoring only after discovery confirms there is work to do.
    monitor.start()

    print(f"[i] {len(files)} supported files found. Beginning analysis...")
    if filtered_count:
        print(f"[i] {filtered_count} supported file(s) skipped by extension filter")
    print(f"[i] Using {effective_max_workers} worker threads.")
    if disabled_layers_scan:
        print(
            f"[i] Detection: {len(active_layers)}/{len(ALL_LAYERS)} layers active "
            f"({', '.join(sorted(active_layers)) or 'none'}) — disabled: "
            f"{', '.join(disabled_layers_scan)}\n"
        )
    else:
        print(
            "[i] Detection: 11-layer hybrid (regex + keyword + secrets + health card "
            "+ passport + UCI + status card + drivers license + OCR recovery + MRZ "
            "+ GLiNER)\n"
        )

    # --- LLM verification: one availability check per scan, not per file ---
    llm_verifier.reset_availability_cache()
    verify_enabled, verify_status = llm_verifier.check_availability(force_enabled=verify)
    print(f"[i] LLM verification: {verify_status} (model={config.OLLAMA_MODEL})\n")

    results = []
    skipped_files = list(discovery_skipped_files)
    if filtered_count:
        skipped_files.append(
            {
                "reason": "filtered",
                "count": filtered_count,
                "scan_status": "skipped",
            }
        )
    failed_files = []

    # --- Filter oversized / binary-content files before threading ---
    valid_files = []
    for path in files:
        try:
            size_mb = os.path.getsize(path) / (1024 * 1024)
        except FileNotFoundError:
            continue
        except PermissionError as e:
            print(f"[!] Cannot access {path}: {e}")
            skipped_files.append(
                {
                    "file": os.path.abspath(path),
                    "reason": "permission denied",
                    "scan_status": "skipped",
                }
            )
            continue
        except Exception as e:
            print(f"[!] Cannot access {path}: {e}")
            continue

        # Binary-content guard: checked independent of / before the size
        # limit, so it fires even when max_file_size_mb would otherwise let
        # the file through (motivating case: a multi-MB NUL-filled .txt file
        # previously cost ~4 minutes of replacement-char fuzzy-matching).
        # Text-extraction paths only — pdf/docx/xlsx/images have their own
        # extractors and are unaffected.
        _, path_ext = os.path.splitext(path.lower())
        if path_ext in TEXT_EXTENSIONS and looks_like_binary(path):
            print(f"[!] Skipping {os.path.basename(path)} – binary content detected")
            skipped_files.append(
                {
                    "file": os.path.abspath(path),
                    "reason": "binary content",
                    "scan_status": "skipped",
                }
            )
            continue

        if size_mb > effective_max_file_size_mb:
            print(
                f"[!] Skipping {os.path.basename(path)} – too large ({size_mb:.1f} MB)"
            )
            skipped_files.append(
                {
                    "file": os.path.abspath(path),
                    "size_mb": round(size_mb, 2),
                    "reason": "too_large",
                    "scan_status": "skipped",
                }
            )
        else:
            valid_files.append(path)

    # --- Multithreaded scanning ---
    # LLM verification is NEVER run inline here, even when verify_enabled is
    # True: up to effective_max_workers threads calling the single-slot Ollama
    # server concurrently causes queuing past OLLAMA_TIMEOUT_S and a high
    # error rate (tests/external_enron/EVALUATION.md finding #2). Instead,
    # when verify_enabled, each worker returns its extracted text (via
    # return_text) and a separate sequential pass below runs verification
    # after every file's detection has completed.
    #
    # Submission is a rolling window (at most effective_max_workers files
    # in flight at once) rather than submitting every file up front, so
    # cancel_event can be honored BETWEEN files: once set, the loop below
    # simply stops requesting new submissions and drains whatever is already
    # in flight to completion, never interrupting a file mid-extraction.
    total = len(valid_files)
    if progress_callback is not None:
        progress_callback(0, total)

    files_iter = iter(valid_files)
    futures: Dict[Any, str] = {}

    def _submit_next(executor) -> Optional[str]:
        if cancel_event is not None and cancel_event.is_set():
            return None
        try:
            path = next(files_iter)
        except StopIteration:
            return None
        fut = executor.submit(
            scan_file,
            path,
            verify=False,
            return_text=verify_enabled,
            run_ner=run_ner,
            ner_max_chars=ner_max_chars,
            ocr_semaphore=ocr_semaphore,
            enabled_layers=resolved_enabled_layers,
        )
        futures[fut] = path
        return path

    with ThreadPoolExecutor(max_workers=effective_max_workers) as executor:
        for _ in range(effective_max_workers):
            started = _submit_next(executor)
            if started is None:
                break
            if progress_callback is not None:
                progress_callback(0, total, current_file=os.path.basename(started))

        idx = 0
        while futures:
            done_set, _pending = wait(list(futures.keys()), return_when=FIRST_COMPLETED)
            for fut in done_set:
                path = futures.pop(fut)
                idx += 1
                completed_info = None
                try:
                    res = fut.result()
                    results.append(res)
                    if res.get("scan_status") == "extraction_failed":
                        failed_files.append(
                            {
                                "file": res.get("file", os.path.abspath(path)),
                                "error": res.get("failure_reason", "unknown extraction failure"),
                            }
                        )
                    # Live-ticker payload for the GUI: reuses the same
                    # report_generator helpers the reports themselves use for
                    # risk bucketing/finding flattening, so the ticker can
                    # never disagree with the written report.
                    if res.get("scan_status") == "scanned":
                        score = res.get("score")
                        findings = _all_findings(res)
                        completed_info = {
                            "file": os.path.basename(res.get("file", path)),
                            "score": score,
                            "risk": _risk_of(score if score is not None else 0, "scanned"),
                            "top_type": findings[0]["type"] if findings else None,
                            "finding_count": len(findings),
                        }
                except Exception as e:
                    print(f"[!] FAILED: {os.path.basename(path)} — {e}")
                    failed_files.append({"file": os.path.abspath(path), "error": str(e)})

                if progress_callback is not None:
                    progress_callback(idx, total, completed=completed_info)

                # Progress indicator
                elapsed = time.time() - scan_start
                files_per_sec = idx / elapsed if elapsed > 0 else 0
                eta_seconds = (total - idx) / files_per_sec if files_per_sec > 0 else 0
                eta_min = int(eta_seconds / 60)
                print(
                    f"  → {idx}/{total} files ({idx * 100 // total}%) | {files_per_sec:.1f} files/sec | ETA: {eta_min}m",
                    end="\r",
                )  # ← ADD end="\r" AND CLOSING

                started = _submit_next(executor)
                if started is not None and progress_callback is not None:
                    progress_callback(idx, total, current_file=os.path.basename(started))

    print("\n")  # ← Newline after progress completes

    cancelled = cancel_event is not None and cancel_event.is_set()
    if cancelled:
        print(
            f"[i] Cancellation requested — {idx}/{total} files completed, "
            "remaining files not submitted."
        )

    # --- Sequential LLM verification post-pass ---
    # Runs AFTER every worker thread has finished detection, one file at a
    # time — no concurrent Ollama calls, so no cross-thread contention/
    # timeouts. Deterministic order: file path, then finding offset (finding
    # order within verify_findings' own per-file grouping is already
    # deterministic, driven by detection order). Each verified file's score
    # is recomputed from its (possibly demoted) matches via the same
    # score_file() entry point scan_file() itself uses, so scoring always
    # reflects post-verification risk levels.
    # Skipped entirely when cancelled, even if verify_enabled — a cancelled
    # scan should return partial results as fast as possible, not run a
    # further sequential pass over whatever completed.
    run_verify_pass = verify_enabled and not cancelled
    if run_verify_pass:
        print("[i] Running LLM verification pass (sequential)...")
        verify_start = time.time()
    for res in sorted(results, key=lambda r: r.get("file", "")):
        text = res.pop("_text", None)
        if not run_verify_pass or not text:
            continue
        matches = res.get("matches")
        if not isinstance(matches, dict):
            continue
        meta = matches.pop("_metadata", None)
        try:
            llm_verifier.verify_findings(matches, text)
        except Exception as e:
            print(f"[llm_verifier error] {e}")
        if meta is not None:
            matches["_metadata"] = meta
        res["score"] = score_file(matches)
    if run_verify_pass:
        print(f"[i] LLM verification pass completed in {time.time() - verify_start:.2f}s\n")

    # --- Summary counts (handle new taxonomy format) ---
    scanned_files = [r for r in results if r.get("scan_status", "scanned") == "scanned"]
    pii_files = [r for r in scanned_files if r["score"] > 0]
    high = len([r for r in pii_files if r["score"] >= 70])
    medium = len([r for r in pii_files if 30 <= r["score"] < 70])
    low = len([r for r in pii_files if 0 < r["score"] < 30])

    # --- Generate reports ---
    # 0o700: reports contain full unmasked PII, so outputs/ should not be
    # readable by other local users. mode= on makedirs only takes effect when
    # the dir is newly created (umask can still loosen it), so chmod
    # unconditionally to also harden a pre-existing outputs/ dir. Reports are
    # further grouped under a dated subdirectory (outputs/YYYY-MM-DD/) so a
    # long-lived install doesn't accumulate thousands of flat report files.
    os.makedirs(EXCLUDED_REPORT_OUTPUT_DIR, mode=0o700, exist_ok=True)
    os.chmod(EXCLUDED_REPORT_OUTPUT_DIR, 0o700)
    dated_dir = _dated_output_dir()
    os.makedirs(dated_dir, mode=0o700, exist_ok=True)
    os.chmod(dated_dir, 0o700)
    timestamp = _report_timestamp()
    md_path = os.path.join(dated_dir, f"report_{timestamp}.md")
    json_path = os.path.join(dated_dir, f"report_{timestamp}.json")
    html_path = os.path.join(dated_dir, f"report_{timestamp}.html")

    # SecureScan's own peak RSS/CPU (see system_monitor.py) — never the
    # whole-VM figure, which doesn't track scan size at all (a 1-file scan
    # can show a HIGHER whole-VM percentage than a 27-file scan purely
    # because of what else was running on the machine). "workers" is added
    # here, not by SystemMonitor itself, since worker count is a scan-level
    # detail the monitor has no reason to know about.
    try:
        monitor_peaks = monitor.get_peaks()
    except Exception:
        monitor_peaks = {}

    # Scan-level (not per-file) record of which detection layers ran, so a
    # report can never be misread as a full scan when layers were deselected
    # — see the enabled_layers docstring above. Reflects the RESOLVED
    # selection (already intersected with ALL_LAYERS), not the raw caller
    # input.
    report_active_layers = set(active_layers)
    if run_ner is False:
        report_active_layers.discard("gliner")
    detection_layers = {
        "enabled": sorted(report_active_layers),
        "disabled": sorted(ALL_LAYERS - report_active_layers),
        "total": len(ALL_LAYERS),
    }

    # GLiNER's execution provider can only be known once it has actually run
    # (module-level singleton, see gliner_detector.py) — query it only when
    # this scan enabled the layer, since the singleton persists across scans
    # in the GUI and would otherwise echo a stale device from an earlier
    # scan that had GLiNER on. PaddleOCR has no such fallback path: its
    # device is set directly from config on every call, so the configured
    # value is the actual value.
    gliner_device = None
    if "gliner" in report_active_layers:
        from detectors.gliner_detector import active_device as _gliner_active_device
        gliner_device = _gliner_active_device()
    peak_stats = {
        "peak_rss_mb": monitor_peaks.get("peak_rss_mb"),
        "peak_cpu_percent": monitor_peaks.get("peak_cpu_percent"),
        "workers": effective_max_workers,
        "ocr_device": config.PADDLEOCR_DEVICE,
        "gliner_device": gliner_device,
    }
    provenance = {"version": config.SECURESCAN_VERSION, "git_commit": _git_short_hash()}

    verification_meta = {"status": verify_status, "model": config.OLLAMA_MODEL}
    generate_markdown(
        results, md_path, skipped_files=skipped_files, verification=verification_meta,
        system_peaks=peak_stats, provenance=provenance, detection_layers=detection_layers,
    )
    generate_json(
        results,
        json_path,
        skipped_files=skipped_files,
        verification=verification_meta,
        scan_cancelled=cancelled,
        system_peaks=peak_stats,
        provenance=provenance,
        detection_layers=detection_layers,
    )
    html_document = generate_html(
        results, html_path, skipped_files=skipped_files, verification=verification_meta,
        system_peaks=peak_stats, provenance=provenance, detection_layers=detection_layers,
    )

    # Best-effort "most recent report" pointer — publish only after the unique
    # HTML report has been fully rendered. This convenience pointer is not the
    # report of record and survives across GUI restarts.
    try:
        _replace_latest_report(html_document)
    except Exception as e:
        print(f"[!] Could not update {latest_report_path()}: {e}")

    # --- Terminal summary ---
    skipped_count = _skipped_count(skipped_files)
    print(
        f"[i] {len(scanned_files)} files scanned, {len(failed_files)} failed, "
        f"{skipped_count} skipped → {len(pii_files)} contained PII.\n"
    )
    peak_line = peak_resource_line(peak_stats)
    if peak_line:
        print(f"[i] {peak_line}\n")
    for r in pii_files:
        print(f"  → {os.path.basename(r['file'])}  score={r['score']}")
    print(f"[✓] Reports saved: {md_path} / {json_path} / {html_path}")

    print("\n===== SCAN SUMMARY =====")
    print(f"Files scanned       : {len(scanned_files)}")
    print(f"Extraction failures : {len(failed_files)}")
    print(f"Files skipped       : {skipped_count}")
    print(f"🔴 High Risk Files   : {high}")
    print(f"🟡 Medium Risk Files : {medium}")
    print(f"🟢 Low Risk Files    : {low}")
    oversized_files = [f for f in skipped_files if f.get("reason") == "too_large"]
    binary_content_files = [f for f in skipped_files if f.get("reason") == "binary content"]
    if oversized_files:
        print(
            f"⚠️  Skipped {len(oversized_files)} file(s) larger than "
            f"{effective_max_file_size_mb} MB"
        )
    if binary_content_files:
        print(f"⚠️  Skipped {len(binary_content_files)} file(s) with binary content")
    if cancelled:
        print(
            f"⚠️  Scan cancelled — partial results ({len(scanned_files) + len(failed_files)} "
            f"of {total} files)"
        )
    if failed_files:
        print(f"❌ Failed {len(failed_files)} file(s):")
        for f in failed_files:
            print(f"   - {os.path.basename(f['file'])}: {f['error']}")
    print(f"⏱️  Scan completed in {time.time() - scan_start:.2f} seconds")
    print("========================\n")

    return ScanReportPath(
        os.path.abspath(html_path),
        scanned_count=len(scanned_files),
        failed_count=len(failed_files),
        skipped_count=skipped_count,
    )


# -------------------------------------------------------------------
# STANDALONE TEST MODE
# -------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "tests/sample_data"
    scan_folder(target)
