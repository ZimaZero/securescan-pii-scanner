# report_generator.py
"""
Markdown report renderer for the PII scanner.

Input shape (unchanged — produced by the scanner, not this module):
    results = [
        {
            "file": "/path/to/file",
            "score": 85,
            "matches": {
                "contact.email": [
                    {"value": "...", "confidence": 0.95, "source": "regex",
                     "risk_level": "MEDIUM"},
                    ...
                ],
                "_metadata": {...},          # skipped when rendering
            },
            "metadata": {...},               # file metadata (PDF producer, etc.)
        },
        ...
    ]

This module only renders — it does not score or detect.
"""

import os
from datetime import datetime

# ------------------------------------------------------------------
# SHARED RENDER HELPERS (data shape only — no scoring logic here)
# ------------------------------------------------------------------

_RISK_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}
_RISK_BADGE = {
    "HIGH": "🔴",
    "MEDIUM": "🟡",
    "LOW": "🟢",
    "NONE": "🔵",
    "UNKNOWN": "⚠️",
}

# Segments that should render upper-cased rather than title-cased.
_ACRONYMS = {
    "ssn", "sin", "dob", "iban", "url", "ip", "aws", "jwt", "api", "ca",
    "on", "bc", "ab", "sk", "mb", "nb", "ns", "pe", "nl", "nu", "yt", "qc", "nt",
}

# Shown as a hover tooltip on the CONFIDENCE column header (report_html.py's
# per-file findings table; the GUI has no CONFIDENCE column to attach this
# to — see gui.py's "Top files" preview table, which is file-level only).
# Wording matches detectors/hybrid_detector.py's own trust model: SOURCE_
# PRIORITY there ranks checksum/format-validated layers (regex, health_card,
# mrz) above format+context-only layers (passport, uci, status_card,
# ocr_recovery, keyword_context, drivers_license, gliner), and REGEX_
# CONFIDENCE/DEFAULT_REGEX_CONFIDENCE assign checksum-backed regex matches
# materially higher confidence (0.95) than unverified/context-only ones
# (0.55-0.60) — confidence is per-detector evidential weight, not a
# probability estimate.
CONFIDENCE_TOOLTIP = (
    "Confidence reflects how strongly the detecting layer's format and "
    "context rules matched — it is not a probability that the value is "
    "real PII. Checksum-validated findings (SIN, health cards, MRZ) carry "
    "higher confidence than format-only or context-only matches (e.g. "
    "driver's licences, semantic NER)."
)


def _safe_score(r):
    if not isinstance(r, dict):
        return 0
    try:
        return int(float(r.get("score", 0)))
    except (TypeError, ValueError):
        return 0


def _scan_status(r):
    status = r.get("scan_status", "scanned") if isinstance(r, dict) else "scanned"
    return status if status in {"scanned", "extraction_failed", "skipped"} else "scanned"


def _failed_files(results):
    return [
        {
            "file": str(r.get("file", "Unknown")),
            "failure_reason": str(r.get("failure_reason") or "Unknown extraction failure"),
        }
        for r in results
        if isinstance(r, dict) and _scan_status(r) == "extraction_failed"
    ]


def _skipped_count(skipped_files):
    """Count ordinary and aggregate skip records without expanding them."""
    total = 0
    for item in skipped_files if isinstance(skipped_files, list) else []:
        if not isinstance(item, dict):
            continue
        count = item.get("count", 1)
        total += count if isinstance(count, int) and count > 0 else 1
    return total


def _risk_of(score, scan_status="scanned"):
    if scan_status == "extraction_failed":
        return "UNKNOWN"
    if score >= 70:
        return "HIGH"
    if score >= 30:
        return "MEDIUM"
    if score > 0:
        return "LOW"
    return "NONE"


def _pretty_type(category):
    """'identifier.financial.ssn' -> 'Identifier / Financial / SSN'."""
    parts = []
    for seg in str(category).split("."):
        words = [
            w.upper() if w.lower() in _ACRONYMS else w.title()
            for w in seg.split("_")
        ]
        parts.append(" ".join(words))
    return " / ".join(parts)


def _all_findings(r):
    """Flatten a file's matches into detection dicts sorted HIGH-risk first."""
    findings = []
    matches = r.get("matches", {}) if isinstance(r, dict) else {}
    if not isinstance(matches, dict):
        return findings
    for category, detections in matches.items():
        if category == "_metadata" or not isinstance(detections, list):
            continue
        for d in detections:
            if not isinstance(d, dict):
                continue
            findings.append({
                "type": _pretty_type(category),
                "category": str(category),
                "value": str(d.get("value", "")),
                "source": str(d.get("source", "")),
                "confidence": d.get("confidence", 0) or 0,
                "risk_level": str(d.get("risk_level", "UNKNOWN")).upper(),
                "reconstructed": bool(d.get("reconstructed")),
                "original_ocr": str(d.get("original_ocr", "")),
            })
    findings.sort(
        key=lambda f: (_RISK_RANK.get(f["risk_level"], 0), f["confidence"]),
        reverse=True,
    )
    return findings


def _top_finding(findings):
    if not findings:
        return "—"
    f = findings[0]
    val = f["value"]
    if len(val) > 40:
        val = val[:39] + "…"
    return f"{f['type']} — `{val}`"


def _conf(pct):
    try:
        return f"{float(pct):.0%}"
    except (TypeError, ValueError):
        return "—"


def _cell(s):
    """Make a value safe for a Markdown table cell."""
    return str(s).replace("|", r"\|").replace("\n", " ").strip()


def _mask_positions(s, keep):
    """Mask alphanumeric characters in `s` per `keep(i, n)` (i = index within
    the alnum-only stream of length n); non-alnum characters (separators like
    '-', ' ', '(', ')') pass through unchanged. Shared by identifier/phone
    masking, which differ only in which positions they keep.
    """
    alnum_idx = [i for i, ch in enumerate(s) if ch.isalnum()]
    n = len(alnum_idx)
    keep_set = {alnum_idx[i] for i in range(n) if keep(i, n)}
    return "".join(
        ch if (i in keep_set or not ch.isalnum()) else "*"
        for i, ch in enumerate(s)
    )


def _mask_identifier(s):
    """SIN/SSN/DL/health/passport style: keep first 3 + last 1 alnum chars,
    mask the rest, preserve separators. '132-677-360' -> '132-***-**0'."""
    return _mask_positions(s, lambda i, n: i < 3 or i >= n - 1)


def _mask_phone(s):
    """Keep last 4 digits, mask the rest, preserve separators.
    '(613) 555-1234' -> '(***) ***-1234'."""
    return _mask_positions(s, lambda i, n: i >= n - 4)


def _mask_credit_card(s):
    """Mask all but the last 4 digits, grouped in 4s: '**** **** **** 1111'."""
    digits = [ch for ch in s if ch.isdigit()]
    n = len(digits)
    if n == 0:
        return "***"
    keep = min(4, n)
    masked = ["*"] * (n - keep) + digits[n - keep:]
    groups = ["".join(masked[i:i + 4]) for i in range(0, n, 4)]
    return " ".join(groups)


def _mask_email(s):
    """First char of local part + stars until '@', domain kept as-is.
    'john@example.org' -> 'j***@example.org'."""
    at = s.find("@")
    if at <= 0:
        return _mask_generic(s)
    local, domain = s[:at], s[at:]
    masked_local = local[0] + "*" * (len(local) - 1)
    return masked_local + domain


def _mask_generic(s):
    """Fallback for entity.*/network.*/etc.: first char + '***' + last char."""
    if len(s) <= 2:
        return "*" * len(s)
    return s[0] + "***" + s[-1]


def mask_value(value, taxonomy_type):
    """Mask a finding's value for the HTML report's masked-display/export
    feature, dispatching on its dotted taxonomy type string. Pure and
    deterministic — never raises; any unexpected input falls back to a full
    mask ('***').
    """
    try:
        s = "" if value is None else str(value)
        if len(s) < 4:
            return "***"
        t = str(taxonomy_type or "")
        if "credit_card" in t:
            return _mask_credit_card(s)
        if t.startswith("contact.email"):
            return _mask_email(s)
        if t.startswith("contact.phone"):
            return _mask_phone(s)
        if t.startswith("identifier."):
            return _mask_identifier(s)
        return _mask_generic(s)
    except Exception:
        return "***"


def _mismatch_alarms(results):
    """Walk `results` for a "mismatch_alarm" dict (written ONLY by
    discovery.scan_file() via detectors/mismatch_alarm.py) and flatten into
    a list of {file, triggered_by, matched_keywords, reason} rows, one per
    alarmed file. Empty list when nothing fired — callers render nothing.
    """
    alarms = []
    for r in results:
        if not isinstance(r, dict):
            continue
        alarm = r.get("mismatch_alarm")
        if not isinstance(alarm, dict):
            continue
        alarms.append({
            "file": str(r.get("file", "Unknown")),
            "triggered_by": str(alarm.get("triggered_by", "")),
            "matched_keywords": [str(k) for k in alarm.get("matched_keywords", []) or []],
            "reason": str(alarm.get("reason", "")),
            "layers_disabled": [str(l) for l in alarm.get("layers_disabled", []) or []],
        })
    return alarms


def _verification_summary(results, verification=None):
    """Walk `results` for llm_verified findings (written ONLY by
    detectors/llm_verifier.py) and build the AI Verification Decisions
    summary + demoted-findings index shared by all three report renderers.

    `verification` is the scan-level {"status": ..., "model": ...} dict from
    discovery.scan_folder() (or main.py's single-file path) — None means the
    caller didn't wire it through, rendered as status "disabled".
    """
    verification = verification if isinstance(verification, dict) else {}
    status = str(verification.get("status") or "disabled")
    model = verification.get("model")

    routed = 0
    demoted = 0
    errors = 0
    demoted_findings = []

    for r in results:
        if not isinstance(r, dict):
            continue
        matches = r.get("matches", {})
        if not isinstance(matches, dict):
            continue
        file_path = str(r.get("file", "Unknown"))
        for category, detections in matches.items():
            if category == "_metadata" or not isinstance(detections, list):
                continue
            for d in detections:
                if not isinstance(d, dict) or "llm_verified" not in d:
                    continue
                routed += 1
                if d.get("llm_verdict") == "FALSE_POSITIVE":
                    demoted += 1
                    demoted_findings.append({
                        "file": file_path,
                        "value": str(d.get("value", "")),
                        "type": _pretty_type(category),
                        "category": str(category),
                        "original_risk_level": str(d.get("original_risk_level", "UNKNOWN")),
                        "llm_reason": str(d.get("llm_reason", "")),
                        "llm_model": str(d.get("llm_model", model or "")),
                    })
                elif d.get("llm_verified") is False:
                    errors += 1

    return {
        "status": status,
        "model": model,
        "routed": routed,
        "demoted": demoted,
        "errors": errors,
        "demoted_findings": demoted_findings,
    }


def detection_layers_line(detection_layers):
    """Format the scan-level "which detection layers ran" line, or None when
    the caller didn't supply the structured info (older callers, tests that
    don't wire it through). Shared by all three renderers so a report can
    never be misread as a full scan when layers were deselected — see
    discovery.py's enabled_layers docstring for the full contract.

    detection_layers: {"enabled": [...], "disabled": [...], "total": int}
    both lists sorted, drawn from detectors.hybrid_detector.ALL_LAYERS.
    """
    if not isinstance(detection_layers, dict):
        return None
    enabled = detection_layers.get("enabled")
    disabled = detection_layers.get("disabled")
    total = detection_layers.get("total")
    if not isinstance(enabled, list) or not isinstance(disabled, list) or not isinstance(total, int):
        return None
    if not disabled:
        return f"Detection layers: all {total} active ({', '.join(enabled)})"
    return (
        f"⚠ PARTIAL SCAN — Detection layers: {len(enabled)}/{total} active "
        f"({', '.join(enabled) or 'none'}); DISABLED for this scan: "
        f"{', '.join(disabled)}. Findings from disabled layers are absent, "
        "and reconciliation between layers only reflects the ones that ran."
    )


def _device_label(device):
    """Render a raw device string (e.g. "cpu", "gpu:0", "cuda") for the
    report header — just uppercased, no CPU/GPU inference logic here since
    the caller already resolved what actually ran."""
    return device.upper() if isinstance(device, str) and device else "unknown"


def peak_resource_line(system_peaks):
    """Format SecureScan's own peak-resource line for report headers, or
    None when no monitor sample landed. Shared by all three renderers (and
    discovery.py's terminal summary) so they can never disagree.

    Reports SecureScan's own process RSS/CPU (see system_monitor.py), not
    whole-system utilization.
    RSS is a SAMPLED FLOOR, not a guaranteed maximum — see
    system_monitor.SystemMonitor's docstring.

    "ocr_device"/"gliner_device" (both optional keys on system_peaks) name
    the actual device each layer ran on this scan. "no network" is unaffected by
    either container and remains unconditional (see gliner_detector.py's
    HF_HUB_OFFLINE=1 and PaddleOCR's offline model-source checks).
    """
    if not isinstance(system_peaks, dict):
        return None
    rss_mb = system_peaks.get("peak_rss_mb")
    if rss_mb is None:
        return None
    parts = [f"Peak memory: {rss_mb:,.0f} MB"]
    workers = system_peaks.get("workers")
    if workers is not None:
        parts.append(f"{workers} workers")
    ocr_device = system_peaks.get("ocr_device")
    parts.append(f"OCR {_device_label(ocr_device)}")
    gliner_device = system_peaks.get("gliner_device")
    parts.append(f"GLiNER {_device_label(gliner_device) if gliner_device else 'not run'}")
    parts.append("no network")
    line = " · ".join(parts)
    cpu_percent = system_peaks.get("peak_cpu_percent")
    if cpu_percent is not None:
        line += (
            f" (peak process CPU {cpu_percent:.0f}% — 100% is one core; "
            "multi-threaded scans commonly exceed that)"
        )
    return line


# ------------------------------------------------------------------
# MAIN RENDERER
# ------------------------------------------------------------------

def generate_markdown(
    results, output_path, skipped_files=None, verification=None,
    system_peaks=None, provenance=None, detection_layers=None,
):
    """Render `results` to a scannable Markdown report at `output_path`.

    system_peaks: optional {"peak_rss_mb", "peak_cpu_percent", "workers",
        "ocr_device", "gliner_device"} — "peak_rss_mb"/"peak_cpu_percent"
        from system_monitor.SystemMonitor.get_peaks() for this scan,
        "workers"/"ocr_device"/"gliner_device" added by the caller
        ("gliner_device" is None when GLiNER didn't run this scan). None
        (or a dict with peak_rss_mb=None) omits the line — see
        peak_resource_line() above.
    provenance: optional {"version", "git_commit"} identifying the
        SecureScan build that produced this report. None omits the line.
    detection_layers: optional {"enabled", "disabled", "total"} — see
        detection_layers_line() above. None omits the line (older callers).
    """
    if not isinstance(results, list):
        results = []
    if not isinstance(output_path, str) or not output_path.strip():
        raise ValueError("output_path must be a non-empty string")
    if not isinstance(skipped_files, list):
        skipped_files = []

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    files = [r for r in results if isinstance(r, dict)]
    # Precompute per-file summary rows, sorted by score (highest first).
    rows = []
    for r in files:
        scan_status = _scan_status(r)
        score = _safe_score(r)
        findings = _all_findings(r)
        rows.append({
            "name": os.path.basename(str(r.get("file", "Unknown"))),
            "path": str(r.get("file", "Unknown")),
            "score": None if scan_status == "extraction_failed" else score,
            "risk": _risk_of(score, scan_status),
            "scan_status": scan_status,
            "failure_reason": r.get("failure_reason"),
            "detection_degraded": bool(r.get("detection_degraded")),
            "failed_layers": r.get("failed_layers", []) or [],
            "findings": findings,
            "metadata": r.get("metadata", {}) or {},
        })
    rows.sort(key=lambda x: x["score"] if x["score"] is not None else -1, reverse=True)

    scanned = sum(1 for x in rows if x["scan_status"] == "scanned")
    failed = sum(1 for x in rows if x["scan_status"] == "extraction_failed")
    high = sum(1 for x in rows if x["risk"] == "HIGH")
    medium = sum(1 for x in rows if x["risk"] == "MEDIUM")
    low = sum(1 for x in rows if x["risk"] == "LOW")
    with_pii = sum(1 for x in rows if x["score"] is not None and x["score"] > 0)

    L = []
    L.append("# 🧩 SecureScan Report")
    L.append(f"**Generated:** {datetime.now().isoformat(sep=' ', timespec='seconds')}")
    if isinstance(provenance, dict) and provenance:
        L.append(
            f"<sub>SecureScan {_cell(provenance.get('version', 'unknown'))} "
            f"· commit `{_cell(provenance.get('git_commit', 'unknown'))}` "
            f"· 11-layer hybrid detection engine</sub>"
        )
    L.append("")
    L.append(
        f"**Summary:** {scanned} scanned, {failed} failed, "
        f"{_skipped_count(skipped_files)} skipped &nbsp;|&nbsp; "
        f"**With PII:** {with_pii}"
    )
    L.append("")
    L.append(f"**Risk:** 🔴 High {high} &nbsp; 🟡 Medium {medium} &nbsp; 🟢 Low {low}")
    peak_line = peak_resource_line(system_peaks)
    if peak_line:
        L.append(f"**{peak_line}**")
    layers_line = detection_layers_line(detection_layers)
    if layers_line:
        L.append(f"**{layers_line}**")
    L.append("")

    # ---- All-files summary table (see everything at a glance) ----
    L.append("## 🗂️ All Files")
    L.append("")
    if rows:
        L.append("| File | Risk | Score | Findings | Top finding |")
        L.append("|------|------|------:|---------:|-------------|")
        for x in rows:
            badge = _RISK_BADGE.get(x["risk"], "🔵")
            score_display = "—" if x["score"] is None else x["score"]
            L.append(
                f"| {_cell(x['name'])} | {badge} {x['risk'].title()} | "
                f"{score_display} | {len(x['findings'])} | "
                f"{_cell(_top_finding(x['findings']))} |"
            )
    else:
        L.append("_No files scanned._")
    L.append("")

    # ---- Skipped files ----
    if skipped_files:
        L.append("## ⚠️ Skipped Files")
        for s in skipped_files:
            if isinstance(s, dict):
                name = os.path.basename(str(s.get("file", "unknown")))
                if s.get("reason") == "symlink":
                    L.append(f"- {name} — symlink")
                elif s.get("reason") == "binary content":
                    L.append(f"- {name} — binary content")
                elif s.get("reason") == "filtered":
                    L.append(f"- {s.get('count', 1)} file(s) — filtered")
                else:
                    L.append(f"- {name} — {s.get('size_mb', '?')} MB")
        L.append("")

    failures = _failed_files(files)
    if failures:
        L.append("## ⚠ Could Not Be Scanned")
        L.append("")
        L.append("| File | Reason |")
        L.append("|------|--------|")
        for failure in failures:
            L.append(
                f"| {_cell(os.path.basename(failure['file']))} | "
                f"{_cell(failure['failure_reason'])} |"
            )
        L.append("")

    # ---- Possible Silent Misses (mismatch alarm) ----
    alarms = _mismatch_alarms(files)
    if alarms:
        L.append("## ⚠ Possible Silent Misses")
        L.append("")
        L.append(
            "_Files that look like an identity document (by filename, "
            "extracted text, detected portrait, or unreadable OCR) but produced no identifier "
            "finding. The score and risk level above are unaffected — this is "
            "advisory only._"
        )
        L.append("")
        L.append("| File | Trigger | Matched keywords | Recommendation |")
        L.append("|------|---------|-------------------|-----------------|")
        for a in alarms:
            L.append(
                f"| {_cell(os.path.basename(a['file']))} | {_cell(a['triggered_by'])} | "
                f"{_cell(', '.join(a['matched_keywords']))} | {_cell(a['reason'])} |"
            )
        L.append("")

    # ---- AI Verification Decisions (index of demoted findings) ----
    vsum = _verification_summary(files, verification)
    L.append("## 🤖 AI Verification Decisions")
    L.append("")
    if vsum["status"] == "enabled":
        L.append(
            f"**Model:** {vsum['model']} &nbsp;|&nbsp; **Routed:** {vsum['routed']} "
            f"&nbsp;|&nbsp; **Demoted:** {vsum['demoted']} &nbsp;|&nbsp; "
            f"**Unverified (errors):** {vsum['errors']}"
        )
        L.append("")
        if vsum["demoted_findings"]:
            L.append("| File | Value | Type | Original Risk | Reason | Model |")
            L.append("|------|-------|------|----------------|--------|-------|")
            for d in vsum["demoted_findings"]:
                L.append(
                    f"| {_cell(os.path.basename(d['file']))} | `{_cell(d['value'])}` | "
                    f"{_cell(d['type'])} | {d['original_risk_level'].title()} → Low | "
                    f"{_cell(d['llm_reason'])} | {_cell(d['llm_model'])} |"
                )
        else:
            L.append("_No findings demoted._")
    else:
        L.append(f"_LLM verification was {vsum['status']} — no findings were judged._")
    L.append("")

    # ---- Per-file detail (findings first, metadata tucked away) ----
    L.append("## 📁 Details")
    L.append("")
    for x in rows:
        badge = _RISK_BADGE.get(x["risk"], "🔵")
        score_display = "—" if x["score"] is None else x["score"]
        L.append(f"### {badge} {x['name']} — score {score_display}")
        L.append(f"<sub>`{x['path']}`</sub>")
        L.append("")

        if x["scan_status"] == "extraction_failed":
            L.append(f"_Could not be scanned: {_cell(x['failure_reason'])}_")
            L.append("")
        else:
            if x["detection_degraded"]:
                failed = ", ".join(str(v) for v in x["failed_layers"]) or "unknown"
                L.append(
                    f"> ⚠ **Detection degraded:** detector layer(s) failed: "
                    f"{_cell(failed)}. This score may not reflect the file's "
                    "complete contents."
                )
                L.append("")
            if x["findings"]:
                L.append("| Type | Value | Source | Confidence | Risk |")
                L.append("|------|-------|--------|-----------:|------|")
                for f in x["findings"]:
                    value = f"`{_cell(f['value'])}`"
                    if f["reconstructed"]:
                        value += (
                            "<br><sub>Reconstructed from original OCR "
                            f"`{_cell(f['original_ocr'])}`</sub>"
                        )
                    L.append(
                        f"| {_cell(f['type'])} | {value} | {_cell(f['source'])} "
                        f"| {_conf(f['confidence'])} | {_RISK_BADGE.get(f['risk_level'], '⚪')} {f['risk_level'].title()} |"
                    )
                L.append("")
            else:
                L.append("_No PII detected._")
                L.append("")

        # Metadata: collapsed, out of the way but available.
        if isinstance(x["metadata"], dict) and x["metadata"]:
            L.append("<details><summary>File metadata</summary>")
            L.append("")
            for k, v in x["metadata"].items():
                L.append(f"- **{str(k).replace('_', ' ').title()}:** {_cell(v)}")
            L.append("")
            L.append("</details>")
            L.append("")

    footer_provenance = (
        f"SecureScan {provenance.get('version', 'unknown')} "
        f"(commit `{provenance.get('git_commit', 'unknown')}`) — "
        if isinstance(provenance, dict) and provenance else "SecureScan — "
    )
    L.append("---")
    L.append(
        f"<sub>{footer_provenance}11-layer hybrid detection engine (regex, "
        "keyword context, secrets, health cards, passports, UCI, status card, "
        "driver's licences, deterministic OCR recovery, MRZ, GLiNER semantic "
        "NER).</sub>"
    )

    text = "\n".join(L)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text
