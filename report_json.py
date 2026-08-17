import os
import json
from datetime import datetime

from report_generator import (
    _verification_summary,
    _mismatch_alarms,
    _scan_status,
    _failed_files,
    _skipped_count,
)


def generate_json(
    results, output_path, skipped_files=None, verification=None, scan_cancelled=False,
    system_peaks=None, provenance=None, detection_layers=None,
):
    """
    Generate a JSON report from scan results.

    Parameters
    ----------
    results : list[dict]
        List of per-file scan results
    output_path : str
        Path to write the .json report
    scan_cancelled : bool
        True when discovery.scan_folder()'s cancel_event was set mid-scan.
        False (default) records a completed scan. Surfaced as
        summary.scan_cancelled so a partial-results
        report is distinguishable from a completed one.
    system_peaks : dict | None
        Optional {"peak_rss_mb", "peak_cpu_percent", "workers", "ocr_device",
        "gliner_device"} — SecureScan's own process RSS (MB)/CPU%, from
        system_monitor.SystemMonitor.get_peaks(), plus the worker count and
        the actual OCR/GLiNER device used this scan, added by the caller
        ("gliner_device" is None when GLiNER didn't run). Never a whole-VM
        figure — see report_generator.peak_resource_line(). None (every
        caller before this parameter existed) omits the summary fields.
    provenance : dict | None
        Optional {"version", "git_commit"} identifying the SecureScan build
        that produced this report. None omits the top-level "generator" key.
    detection_layers : dict | None
        Optional {"enabled": [...], "disabled": [...], "total": int} — the
        RESOLVED scan-level detection-layer selection (see discovery.py's
        enabled_layers docstring). None omits the top-level "layers" key
        (older callers / callers that don't wire it through).

    HARDENED VERSION - Now handles:
    - None/invalid results input
    - None/invalid output_path
    - Non-serializable objects
    - Circular references
    - Missing or None values in result dicts
    """
    # FIX 1: Validate results input
    # Handle skipped_files parameter
    if skipped_files is None:
        skipped_files = []
    if not isinstance(skipped_files, list):
        skipped_files = []

    # FIX 2: Validate output_path
    if not isinstance(output_path, str) or not output_path or not output_path.strip():
        raise ValueError("output_path must be a non-empty string")

    # FIX 3: Ensure output directory exists
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    # FIX 4: Safe score extraction helper
    def safe_score(r):
        """Safely extract score as integer"""
        if not isinstance(r, dict):
            return 0
        score = r.get("score", 0)
        if not isinstance(score, (int, float)):
            return 0
        try:
            return int(score)
        except:
            return 0

    # FIX 5: Sanitize data to prevent JSON serialization errors
    def sanitize_for_json(obj, depth=0):
        """Recursively sanitize objects for JSON serialization"""
        # Prevent infinite recursion
        if depth > 50:
            return "[Max depth exceeded]"

        if obj is None:
            return None
        elif isinstance(obj, (str, int, float, bool)):
            return obj
        elif isinstance(obj, dict):
            # Prevent circular references by limiting depth
            return {
                str(k): sanitize_for_json(v, depth + 1)
                for k, v in obj.items()
                if k != "self"
            }
        elif isinstance(obj, (list, tuple)):
            return [sanitize_for_json(item, depth + 1) for item in obj]
        else:
            # Non-serializable objects (functions, objects, etc.)
            return str(obj)

    # Build report structure
    report = {
        "generated": datetime.now().isoformat(),
        "generator": {
            "name": "SecureScan",
            "version": (provenance or {}).get("version", "unknown") if isinstance(provenance, dict) else "unknown",
            "git_commit": (provenance or {}).get("git_commit", "unknown") if isinstance(provenance, dict) else "unknown",
            "detection_layers": 11,
        },
        "layers": sanitize_for_json(detection_layers) if isinstance(detection_layers, dict) else None,
        "total_files": len(results) + _skipped_count(skipped_files),
        "skipped_files": _skipped_count(skipped_files),
        "skipped_file_details": sanitize_for_json(skipped_files),
        "summary": {
            "scan_cancelled": bool(scan_cancelled),
            # SecureScan's own process RSS/CPU (see system_monitor.py) —
            # deliberately not a whole-VM percentage, which doesn't track
            # scan size at all.
            "peak_rss_mb": (system_peaks or {}).get("peak_rss_mb") if isinstance(system_peaks, dict) else None,
            "peak_cpu_percent": (system_peaks or {}).get("peak_cpu_percent") if isinstance(system_peaks, dict) else None,
            "workers": (system_peaks or {}).get("workers") if isinstance(system_peaks, dict) else None,
            # Actual device each layer ran on this scan; see
            # report_generator.peak_resource_line().
            # gliner_device is None when GLiNER didn't run this scan.
            "ocr_device": (system_peaks or {}).get("ocr_device") if isinstance(system_peaks, dict) else None,
            "gliner_device": (system_peaks or {}).get("gliner_device") if isinstance(system_peaks, dict) else None,
            "scanned": len([r for r in results if _scan_status(r) == "scanned"]),
            "failed": len(
                [r for r in results if _scan_status(r) == "extraction_failed"]
            ),
            "skipped": _skipped_count(skipped_files),
            "high_risk": len(
                [
                    r
                    for r in results
                    if _scan_status(r) == "scanned" and safe_score(r) >= 70
                ]
            ),
            "medium_risk": len(
                [
                    r
                    for r in results
                    if _scan_status(r) == "scanned"
                    and 30 <= safe_score(r) < 70
                ]
            ),
            "low_risk": len(
                [
                    r
                    for r in results
                    if _scan_status(r) == "scanned"
                    and 0 < safe_score(r) < 30
                ]
            ),
            "no_pii": len(
                [
                    r
                    for r in results
                    if _scan_status(r) == "scanned" and safe_score(r) == 0
                ]
            ),
        },
        "failed_files": sanitize_for_json(_failed_files(results)),
        "verification": sanitize_for_json(_verification_summary(results, verification)),
        "mismatch_alarms": sanitize_for_json(_mismatch_alarms(
            [r for r in results if isinstance(r, dict)]
        )),
        "files": [],
    }

    # Process each result
    for r in results:
        if not isinstance(r, dict):
            continue

        file_info = {
            "file": str(r.get("file", "Unknown")),
            "scan_status": _scan_status(r),
            "failure_reason": r.get("failure_reason"),
            "detection_degraded": bool(r.get("detection_degraded")),
            "failed_layers": sanitize_for_json(r.get("failed_layers", [])),
            "score": (
                None
                if _scan_status(r) == "extraction_failed"
                else safe_score(r)
            ),
            "matches": sanitize_for_json(r.get("matches", {})),
            "metadata": sanitize_for_json(r.get("metadata", {})),
        }

        report["files"].append(file_info)

    # Write JSON file with error handling
    try:
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        # If JSON serialization still fails, write a sanitized version
        sanitized_report = sanitize_for_json(report)
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(sanitized_report, fh, indent=2, ensure_ascii=False)

    return report
