# report_html.py
"""
Standalone HTML report renderer for the PII scanner.

Produces a single self-contained .html file (inline CSS, no external deps, one
tiny vanilla-JS sorter) that opens offline in any browser. Same input shape as
report_generator.generate_markdown — this module only renders.
"""

import hashlib
import os
import html
import json
import re
from datetime import datetime

# Reuse the shared, shape-only helpers so HTML and Markdown agree on risk
# bucketing, finding flattening and sort order.
from report_generator import (
    _safe_score, _risk_of, _all_findings, _RISK_BADGE, _verification_summary,
    _mismatch_alarms, mask_value, _scan_status, _failed_files,
    _skipped_count, peak_resource_line, CONFIDENCE_TOOLTIP, detection_layers_line,
)

_RISK_CLASS = {"HIGH": "high", "MEDIUM": "med", "LOW": "low", "NONE": "none",
               "UNKNOWN": "unknown"}

_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1f2430;--muted:#6b7280;--line:#e5e7eb;
--high:#e5484d;--med:#f5a623;--low:#30a46c;--none:#8b8d98;--unknown:#7c3aed;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:1040px;margin:0 auto;padding:32px 20px 64px;}
h1{font-size:26px;margin:0 0 4px;}
h2{font-size:19px;margin:36px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--line);}
.meta{color:var(--muted);margin:0 0 24px;font-size:13px;}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
.cards{display:flex;gap:14px;flex-wrap:wrap;margin:8px 0 8px;}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:16px 20px;min-width:120px;box-shadow:0 1px 2px rgba(0,0,0,.04);
border-left:5px solid var(--none);}
.card.high{border-left-color:var(--high)} .card.med{border-left-color:var(--med)}
.card.low{border-left-color:var(--low)} .card.total{border-left-color:#3b82f6}
.card .num{font-size:30px;font-weight:700;line-height:1;}
.card .lbl{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em;margin-top:6px;}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:14px;}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top;}
th{background:#fafbfc;font-weight:600;font-size:12px;text-transform:uppercase;
letter-spacing:.04em;color:#4b5563;}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:#fafbff}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;}
th[title]{cursor:help;text-decoration:underline dotted;text-decoration-color:var(--muted);}
.val{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
background:#f1f3f5;padding:1px 6px;border-radius:5px;font-size:13px;
word-break:break-all;}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;color:#fff;
font-size:12px;font-weight:600;white-space:nowrap;}
.badge.high{background:var(--high)} .badge.med{background:var(--med)}
.badge.low{background:var(--low)} .badge.none{background:var(--none)}
.badge.unknown{background:var(--unknown)}
.file{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:18px 20px;margin:16px 0;box-shadow:0 1px 2px rgba(0,0,0,.04);}
.file h3{margin:0 0 2px;font-size:17px;display:flex;align-items:center;gap:10px;}
.file .path{color:var(--muted);font-size:12px;margin:0 0 14px;}
.file table{margin-top:4px}
.none-msg{color:var(--muted);font-style:italic;margin:6px 0;}
details{margin-top:14px;border-top:1px dashed var(--line);padding-top:10px;}
summary{cursor:pointer;color:var(--muted);font-size:13px;font-weight:600;}
details table{margin-top:10px}
table.sortable th{cursor:pointer;user-select:none;}
table.sortable th:hover{color:var(--ink)}
td a.file-link{color:inherit;font-weight:inherit;text-decoration:none;
cursor:pointer;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
td a.file-link:hover{text-decoration:underline;}
.file-footer{display:flex;justify-content:flex-end;align-items:flex-start;
gap:12px;margin-top:14px;border-top:1px dashed var(--line);padding-top:10px;}
.file-footer details{flex:1;min-width:0;margin:0;border:0;padding:0;}
.back-to-top{color:var(--muted);font-size:13px;text-decoration:none;
cursor:pointer;white-space:nowrap;flex:0 0 auto;}
.back-to-top:hover{text-decoration:underline;}
.skip{background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:12px 16px;}
#pii-toolbar{position:fixed;top:0;left:0;right:0;z-index:1000;background:var(--ink);
color:#fff;padding:9px 20px;display:flex;align-items:center;gap:14px;
box-shadow:0 1px 4px rgba(0,0,0,.25);font-size:13px;flex-wrap:wrap;}
#pii-toolbar button{font:inherit;font-weight:600;padding:6px 14px;border-radius:7px;
border:1px solid rgba(255,255,255,.35);background:rgba(255,255,255,.08);color:#fff;
cursor:pointer;}
#pii-toolbar button:hover{background:rgba(255,255,255,.2)}
#pii-toolbar .pii-note{color:#c7cad1;font-size:12px;}
body{padding-top:52px;}
"""

_JS = """
document.querySelectorAll('table.sortable').forEach(function(tbl){
  var ths = tbl.tHead.rows[0].cells;
  for (var i=0;i<ths.length;i++){ (function(idx,th){
    th.addEventListener('click', function(){
      var body = tbl.tBodies[0], rows = Array.prototype.slice.call(body.rows);
      var asc = th.getAttribute('data-asc') !== 'true'; th.setAttribute('data-asc', asc);
      rows.sort(function(a,b){
        var av=a.cells[idx].getAttribute('data-sort'); if(av===null) av=a.cells[idx].textContent.trim();
        var bv=b.cells[idx].getAttribute('data-sort'); if(bv===null) bv=b.cells[idx].textContent.trim();
        var an=parseFloat(av), bn=parseFloat(bv), c;
        if(!isNaN(an)&&!isNaN(bn)) c=an-bn; else c=String(av).localeCompare(String(bv));
        return asc?c:-c;
      });
      rows.forEach(function(r){ body.appendChild(r); });
    });
  })(i, ths[i]); }
});
"""

# Placeholder swapped for a json.dumps()-escaped basename in generate_html —
# a plain string token (not str.format/f-string) so the JS's own braces don't
# need doubling.
_TOOLBAR_JS = """
(function(){
  var REPORT_BASENAME = __REPORT_BASENAME_JSON__;
  var maskBtn = document.getElementById('pii-mask-toggle');
  var exportBtn = document.getElementById('pii-export-btn');
  var spans = Array.prototype.slice.call(document.querySelectorAll('.pii-value'));
  // Originals are kept only in this JS variable, never written back to the
  // DOM/data-* attributes, so an export taken while unmasked can't leak them.
  var originals = spans.map(function(el){ return el.textContent; });
  var masked = false;

  maskBtn.addEventListener('click', function(){
    masked = !masked;
    spans.forEach(function(el, i){
      el.textContent = masked ? el.getAttribute('data-masked') : originals[i];
    });
    maskBtn.textContent = masked ? 'Unmask values' : 'Mask values';
  });

  exportBtn.addEventListener('click', function(){
    var clone = document.documentElement.cloneNode(true);
    var cSpans = Array.prototype.slice.call(clone.querySelectorAll('.pii-value'));
    cSpans.forEach(function(el){
      el.textContent = el.getAttribute('data-masked');
      el.removeAttribute('data-masked');
    });
    var cToolbar = clone.querySelector('#pii-toolbar');
    if (cToolbar && cToolbar.parentNode) cToolbar.parentNode.removeChild(cToolbar);
    var htmlOut = '<!DOCTYPE html>\\n' + clone.outerHTML;
    var blob = new Blob([htmlOut], {type: 'text/html'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = REPORT_BASENAME + '_masked.html';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function(){ URL.revokeObjectURL(url); }, 1000);
  });
})();
"""


def _e(s):
    return html.escape(str(s), quote=True)


def _conf(pct):
    try:
        return f"{float(pct):.0%}"
    except (TypeError, ValueError):
        return "—"


def _badge(risk):
    r = str(risk).upper()
    return f'<span class="badge {_RISK_CLASS.get(r, "none")}">{_e(r.title())}</span>'


def _val_span(value, category):
    """A finding value, wrapped so the toolbar's mask toggle/export can find
    and swap it. `category` is the raw dotted taxonomy key (e.g.
    'identifier.financial.sin'), used only to pick mask_value()'s rule."""
    masked = mask_value(value, category)
    return f'<span class="val pii-value" data-masked="{_e(masked)}">{_e(value)}</span>'


# Filenames and paths remain visible by design and are never
# masked, in either the working copy or an exported "masked" copy. A masked
# report exists to tell someone which files need action, and a masked path
# names no file — a file name that itself contains personal information is
# the most urgent file to act on, so it must stay identifiable. Only finding
# VALUES (and separate document metadata like author/subject) go through
# mask_value()/the pii-value toggle. There is no masking function for names
# to call here — this is not conditional, names are always shown plainly.
_ID_UNSAFE_RE = re.compile(r"[^A-Za-z0-9]+")


def _file_anchor_id(path):
    """Stable, unique HTML id for a file's detail-card anchor.

    Derived from the FULL path, not the basename — two files with the same
    basename in different folders (the corpus has several) must not
    collide, so a short hash of the complete path is always appended (and
    alone would already guarantee uniqueness). A sanitized basename prefix
    is added only so ids stay readable when skimming page source. Sanitized
    to [a-z0-9-] only: spaces, slashes, quotes, commas, ampersands, and
    every other punctuation character collapse to a single hyphen.
    """
    path = str(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    safe_stem = _ID_UNSAFE_RE.sub("-", stem).strip("-").lower()
    digest = hashlib.sha1(path.encode("utf-8", errors="surrogateescape")).hexdigest()[:10]
    return f"file-{safe_stem}-{digest}" if safe_stem else f"file-{digest}"


def generate_html(
    results, output_path, skipped_files=None, verification=None,
    system_peaks=None, provenance=None, detection_layers=None,
):
    """Render `results` to a standalone HTML report at `output_path`.

    system_peaks: optional {"peak_rss_mb", "peak_cpu_percent", "workers",
        "ocr_device", "gliner_device"} — see
        report_generator.peak_resource_line() for the shape and the
        rendered format. None (or peak_rss_mb=None) omits the line.
    provenance: optional {"version", "git_commit"} identifying the
        SecureScan build that produced this report, so an old report on
        disk stays identifiable. None omits the provenance line.
    detection_layers: optional {"enabled", "disabled", "total"} — see
        report_generator.detection_layers_line(). None omits the line.
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

    # Anchor ids are computed ONCE per row here (not re-derived separately
    # in the table and in Details) so the All Files table's links and the
    # Details cards' ids can never drift apart. _file_anchor_id() is already
    # effectively unique (it hashes the full path), but a report is exactly
    # the kind of place "effectively" isn't good enough — a plain counter
    # closes the gap for real, so every id is guaranteed to exist once.
    _seen_anchor_ids: dict = {}

    def _unique_anchor_id(path):
        base = _file_anchor_id(path)
        count = _seen_anchor_ids.get(base, 0) + 1
        _seen_anchor_ids[base] = count
        return base if count == 1 else f"{base}-{count}"

    rows = []
    for r in results:
        if not isinstance(r, dict):
            continue
        scan_status = _scan_status(r)
        score = _safe_score(r)
        findings = _all_findings(r)
        file_path = str(r.get("file", "Unknown"))
        rows.append({
            "name": os.path.basename(file_path),
            "path": file_path,
            "anchor_id": _unique_anchor_id(file_path),
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
    generated = datetime.now().isoformat(sep=" ", timespec="seconds")

    H = []
    H.append("<!DOCTYPE html>")
    H.append('<html lang="en"><head><meta charset="utf-8">')
    H.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    H.append("<title>SecureScan Report</title>")
    H.append(f"<style>{_CSS}</style></head><body>")
    H.append(
        "<div id='pii-toolbar'>"
        "<button id='pii-mask-toggle' type='button'>Mask values</button>"
        "<button id='pii-export-btn' type='button'>Export masked copy</button>"
        "<span class='pii-note'>Masked copies redact finding values only — "
        "file names and paths are shown so files can be located and "
        "remediated; review names before sharing. This working copy "
        "contains full values.</span>"
        "</div>"
    )
    H.append("<div class='wrap'>")

    H.append("<h1>🧩 SecureScan Report</h1>")
    H.append(f"<p class='meta'>Generated {_e(generated)}</p>")
    if isinstance(provenance, dict) and provenance:
        H.append(
            "<p class='meta'>SecureScan "
            f"{_e(provenance.get('version', 'unknown'))} · commit "
            f"<span class='mono'>{_e(provenance.get('git_commit', 'unknown'))}</span> "
            "· 11-layer hybrid detection engine</p>"
        )
    peak_line = peak_resource_line(system_peaks)
    if peak_line:
        H.append(f"<p class='meta'>{_e(peak_line)}</p>")
    layers_line = detection_layers_line(detection_layers)
    if layers_line:
        css_class = "meta skip" if (isinstance(detection_layers, dict) and detection_layers.get("disabled")) else "meta"
        H.append(f"<p class='{css_class}'>{_e(layers_line)}</p>")
    H.append(
        f"<p class='meta'>{scanned} scanned, {failed} failed, "
        f"{_skipped_count(skipped_files)} skipped</p>"
    )

    # ---- dashboard cards ----
    H.append("<div class='cards'>")
    H.append(f"<div class='card total'><div class='num'>{scanned}</div><div class='lbl'>Files scanned</div></div>")
    H.append(f"<div class='card high'><div class='num'>{high}</div><div class='lbl'>High risk</div></div>")
    H.append(f"<div class='card med'><div class='num'>{medium}</div><div class='lbl'>Medium risk</div></div>")
    H.append(f"<div class='card low'><div class='num'>{low}</div><div class='lbl'>Low risk</div></div>")
    H.append("</div>")

    # ---- all-files summary table (sortable) ----
    H.append("<h2 id='all-files'>🗂️ All Files</h2>")
    if rows:
        H.append("<table class='sortable'><thead><tr>"
                 "<th>File</th><th>Risk</th><th class='num'>Score</th>"
                 "<th class='num'>Findings</th><th>Top finding</th></tr></thead><tbody>")
        rank = {"HIGH": 4, "MEDIUM": 3, "LOW": 2, "NONE": 1, "UNKNOWN": 0}
        for x in rows:
            top = x["findings"][0] if x["findings"] else None
            top_html = (f"{_e(top['type'])} — {_val_span(top['value'], top['category'])}"
                        if top else "—")
            score_display = "—" if x["score"] is None else x["score"]
            score_sort = -1 if x["score"] is None else x["score"]
            # Plain anchor link, no JS — jumps straight to this file's
            # detail card below. File names/paths are never masked (see the
            # note above _file_anchor_id), so this link and its text both
            # survive the masked export unchanged.
            H.append(
                "<tr>"
                f"<td><a class='file-link' href='#{x['anchor_id']}'>{_e(x['name'])}</a></td>"
                f"<td data-sort='{rank.get(x['risk'],0)}'>{_badge(x['risk'])}</td>"
                f"<td class='num' data-sort='{score_sort}'>{score_display}</td>"
                f"<td class='num' data-sort='{len(x['findings'])}'>{len(x['findings'])}</td>"
                f"<td>{top_html}</td>"
                "</tr>"
            )
        H.append("</tbody></table>")
    else:
        H.append("<p class='none-msg'>No files scanned.</p>")

    # ---- skipped ----
    if skipped_files:
        H.append("<h2>⚠️ Skipped Files</h2><div class='skip'><ul>")
        for s in skipped_files:
            if isinstance(s, dict):
                nm = os.path.basename(str(s.get("file", "unknown")))
                if s.get("reason") == "symlink":
                    H.append(f"<li>{_e(nm)} — symlink</li>")
                elif s.get("reason") == "binary content":
                    H.append(f"<li>{_e(nm)} — binary content</li>")
                elif s.get("reason") == "filtered":
                    H.append(f"<li>{_e(s.get('count', 1))} file(s) — filtered</li>")
                else:
                    H.append(
                        f"<li>{_e(nm)} — "
                        f"{_e(s.get('size_mb', '?'))} MB</li>"
                    )
        H.append("</ul></div>")

    failures = _failed_files([r for r in results if isinstance(r, dict)])
    if failures:
        H.append("<h2>⚠ Could Not Be Scanned</h2>")
        H.append("<table><thead><tr><th>File</th><th>Reason</th></tr></thead><tbody>")
        for failure in failures:
            H.append(
                "<tr>"
                f"<td>{_e(os.path.basename(failure['file']))}</td>"
                f"<td>{_val_span(failure['failure_reason'], '')}</td>"
                "</tr>"
            )
        H.append("</tbody></table>")

    # ---- Possible Silent Misses (mismatch alarm) ----
    alarms = _mismatch_alarms([r for r in results if isinstance(r, dict)])
    if alarms:
        H.append("<h2>⚠ Possible Silent Misses</h2>")
        H.append(
            "<p class='meta'>Files that look like an identity document (by "
            "filename, extracted text, detected portrait, or unreadable OCR) but produced no "
            "identifier finding. The score and risk level above are "
            "unaffected — this is advisory only.</p>"
        )
        H.append("<table class='sortable'><thead><tr>"
                 "<th>File</th><th>Trigger</th><th>Matched keywords</th>"
                 "<th>Recommendation</th></tr></thead><tbody>")
        for a in alarms:
            H.append(
                "<tr>"
                f"<td>{_e(os.path.basename(a['file']))}</td>"
                f"<td>{_e(a['triggered_by'])}</td>"
                f"<td>{_e(', '.join(a['matched_keywords']))}</td>"
                f"<td>{_e(a['reason'])}</td>"
                "</tr>"
            )
        H.append("</tbody></table>")

    # ---- AI Verification Decisions (index of demoted findings) ----
    vsum = _verification_summary(results, verification)
    H.append("<h2>🤖 AI Verification Decisions</h2>")
    if vsum["status"] == "enabled":
        H.append(
            f"<p class='meta'>Model: {_e(vsum['model'])} &nbsp;|&nbsp; "
            f"Routed: {vsum['routed']} &nbsp;|&nbsp; Demoted: {vsum['demoted']} "
            f"&nbsp;|&nbsp; Unverified (errors): {vsum['errors']}</p>"
        )
        if vsum["demoted_findings"]:
            H.append("<table class='sortable'><thead><tr>"
                     "<th>File</th><th>Value</th><th>Type</th>"
                     "<th>Original Risk</th><th>Reason</th><th>Model</th></tr></thead><tbody>")
            for d in vsum["demoted_findings"]:
                H.append(
                    "<tr>"
                    f"<td>{_e(os.path.basename(d['file']))}</td>"
                    f"<td>{_val_span(d['value'], d['category'])}</td>"
                    f"<td>{_e(d['type'])}</td>"
                    f"<td>{_e(d['original_risk_level'].title())} → {_badge('LOW')}</td>"
                    f"<td>{_e(d['llm_reason'])}</td>"
                    f"<td>{_e(d['llm_model'])}</td>"
                    "</tr>"
                )
            H.append("</tbody></table>")
        else:
            H.append("<p class='none-msg'>No findings demoted.</p>")
    else:
        H.append(f"<p class='none-msg'>LLM verification was {_e(vsum['status'])} — no findings were judged.</p>")

    # ---- per-file detail ----
    H.append("<h2>📁 Details</h2>")
    for x in rows:
        H.append(f"<section class='file' id='{x['anchor_id']}'>")
        score_display = "—" if x["score"] is None else x["score"]
        H.append(f"<h3>{_badge(x['risk'])} {_e(x['name'])} "
                 f"<span class='meta'>score {score_display}</span></h3>")
        H.append(f"<p class='path mono'>{_e(x['path'])}</p>")

        if x["scan_status"] == "extraction_failed":
            H.append(
                "<p class='none-msg'>Could not be scanned: "
                f"{_val_span(x['failure_reason'], '')}</p>"
            )
        else:
            if x["detection_degraded"]:
                failed = ", ".join(str(v) for v in x["failed_layers"]) or "unknown"
                H.append(
                    "<p class='skip'>⚠ <strong>Detection degraded:</strong> "
                    f"detector layer(s) failed: {_e(failed)}. This score may "
                    "not reflect the file's complete contents.</p>"
                )
            if x["findings"]:
                H.append(
                    "<table><thead><tr><th>Type</th><th>Value</th><th>Source</th>"
                    f"<th class='num' title='{_e(CONFIDENCE_TOOLTIP)}'>Confidence</th>"
                    "<th>Risk</th></tr></thead><tbody>"
                )
                for f in x["findings"]:
                    value = _val_span(f["value"], f["category"])
                    if f["reconstructed"]:
                        value += (
                            "<br><span class='meta'>Reconstructed from original OCR "
                            f"{_val_span(f['original_ocr'], f['category'])}</span>"
                        )
                    H.append(
                        "<tr>"
                        f"<td>{_e(f['type'])}</td>"
                        f"<td>{value}</td>"
                        f"<td>{_e(f['source'])}</td>"
                        f"<td class='num'>{_conf(f['confidence'])}</td>"
                        f"<td>{_badge(f['risk_level'])}</td>"
                        "</tr>"
                    )
                H.append("</tbody></table>")
            else:
                H.append("<p class='none-msg'>No PII detected.</p>")

        H.append("<div class='file-footer'>")
        if isinstance(x["metadata"], dict) and x["metadata"]:
            H.append("<details><summary>File metadata</summary>"
                     "<table><tbody>")
            for k, v in x["metadata"].items():
                H.append(f"<tr><th>{_e(str(k).replace('_', ' ').title())}</th>"
                         f"<td>{_val_span(v, '')}</td></tr>")
            H.append("</tbody></table></details>")
        H.append("<a class='back-to-top' href='#all-files'>↑ Back to All Files</a>")
        H.append("</div>")
        H.append("</section>")

    footer_provenance = (
        f"SecureScan {_e(provenance.get('version', 'unknown'))} "
        f"(commit {_e(provenance.get('git_commit', 'unknown'))}) — "
        if isinstance(provenance, dict) and provenance else "SecureScan — "
    )
    H.append(
        f"<p class='meta' style='margin-top:32px;border-top:1px solid var(--line);"
        f"padding-top:16px;'>{footer_provenance}11-layer hybrid detection engine "
        "(regex, keyword context, secrets, health cards, passports, UCI, status "
        "card, driver's licences, deterministic OCR recovery, MRZ, GLiNER semantic "
        "NER).</p>"
    )

    basename_no_ext = os.path.splitext(os.path.basename(output_path))[0] or "report"
    toolbar_js = _TOOLBAR_JS.replace(
        "__REPORT_BASENAME_JSON__", json.dumps(basename_no_ext)
    )
    H.append(f"<script>{_JS}</script>")
    H.append(f"<script>{toolbar_js}</script>")
    H.append("</div></body></html>")

    doc = "\n".join(H)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return doc
