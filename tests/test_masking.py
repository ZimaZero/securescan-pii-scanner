#!/usr/bin/env python3
# tests/test_masking.py
"""
Test suite for the masked-display/export feature:
  - report_generator.mask_value() — type-aware masking rules.
  - report_html.generate_html() — every rendered finding value wrapped in a
    <span class="pii-value" data-masked="..."> with the correct mask.
  - The masked-export transform (replicated here in Python, no browser) never
    leaves a raw fixture value anywhere in the exported string.
  - report_generator.generate_markdown() / report_json.generate_json() stay
    byte-for-byte unaffected (full values, no pii-value/data-masked markup).

Run directly for a pass/fail table + summary:
    docker compose run --rm securescan-cpu python tests/test_masking.py

Also importable / pytest-compatible.
"""

import html as htmllib
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_generator import mask_value, generate_markdown
from report_html import generate_html
from report_json import generate_json

# ============================================================
#  1. mask_value() UNIT CASES
# ============================================================
# (name, value, taxonomy_type, expected)
MASK_CASES = [
    ("SIN identifier", "132-677-360", "identifier.financial.sin", "132-***-**0"),
    ("Credit card (spaced)", "4111 1111 1111 1111",
     "identifier.financial.credit_card", "**** **** **** 1111"),
    ("Credit card (dashed, separator-agnostic)", "4111-1111-1111-1111",
     "identifier.financial.credit_card", "**** **** **** 1111"),
    ("Email", "jane.doe@example.org", "contact.email", "j*******@example.org"),
    ("Email with no '@' falls back to generic", "not-an-email-value",
     "contact.email", "n***e"),
    ("Phone", "(613) 555-0134", "contact.phone", "(***) ***-0134"),
    ("Generic entity", "Toronto", "entity.location", "T***o"),
    ("Generic technical", "https://example.com/path", "technical.url", "h***h"),
    ("Identifier, no separators", "ABCDEFGHIJ",
     "identifier.government.passport_ca", "ABC******J"),
    ("Short value (<4 chars) fully masked", "A12",
     "identifier.financial.sin", "***"),
    ("Empty string", "", "contact.email", "***"),
    ("None value", None, "contact.phone", "***"),
    ("Unicode 4-char value, generic rule", "日本太郎", "entity.person", "日***郎"),
    ("Unknown/missing taxonomy type", "somevalue123", None, "s***3"),
    ("Unknown/missing taxonomy type (empty string)", "somevalue123", "", "s***3"),
]

# ============================================================
#  2/3. SYNTHETIC HTML-RENDER + EXPORT-SIMULATION FIXTURE
# ============================================================
FIXTURE_SIN = "132-677-360"
FIXTURE_CC = "4111 1111 1111 1111"
FIXTURE_EMAIL = "jane.doe@example.org"
FIXTURE_PHONE = "(613) 555-0134"
FIXTURE_FILENAME = "Taylor_Example_identity_photo.jpg"
FIXTURE_FAILED_FILENAME = "Sample_Driving_Licence.jpg"
# Real-corpus-shaped name: spaces, an ampersand, and a comma — exactly the
# characters an HTML id/href fragment can't contain unsanitized.
FIXTURE_TRICKY_FILENAME = "specimen_licence_03.jpg"
# Same basename, two different folders — id derivation must use the full
# path, not just the basename, or these two would collide.
FIXTURE_DUP_BASENAME = "duplicate_scan.jpg"

FIXTURE_FINDINGS = {
    "identifier.financial.sin": FIXTURE_SIN,
    "identifier.financial.credit_card": FIXTURE_CC,
    "contact.email": FIXTURE_EMAIL,
    "contact.phone": FIXTURE_PHONE,
}
FIXTURE_METADATA = {
    "author": "Mara Audit",
    "subject": "Benefits claim for Mara Audit",
    "comments": f"Internal note: SIN {FIXTURE_SIN}",
}


def _build_fixture_results():
    matches = {
        category: [{
            "value": value,
            "confidence": 0.95,
            "source": "regex",
            "risk_level": "HIGH",
        }]
        for category, value in FIXTURE_FINDINGS.items()
    }
    return [
        {
            "file": f"/tmp/scans/{FIXTURE_FILENAME}",
            "score": 85,
            "matches": matches,
            "metadata": dict(FIXTURE_METADATA),
            "mismatch_alarm": {
                "triggered_by": "filename",
                "matched_keywords": ["STATUS CARD"],
                "reason": "manual review recommended",
            },
        },
        {
            "file": f"/tmp/scans/{FIXTURE_FAILED_FILENAME}",
            "scan_status": "extraction_failed",
            "failure_reason": f"Could not read {FIXTURE_FAILED_FILENAME}",
            "score": None,
            "matches": {},
            "metadata": {},
        },
        {
            "file": f"/tmp/scans/tricky/{FIXTURE_TRICKY_FILENAME}",
            "score": 40,
            "matches": {},
            "metadata": {},
        },
        {
            "file": f"/tmp/scans/dirA/{FIXTURE_DUP_BASENAME}",
            "score": 20,
            "matches": {},
            "metadata": {},
        },
        {
            "file": f"/tmp/scans/dirB/{FIXTURE_DUP_BASENAME}",
            "score": 10,
            "matches": {},
            "metadata": {},
        },
    ]


def _pii_span_masked_value(doc, raw_value):
    """Find the <span class="val pii-value" data-masked="...">RAW</span>
    for `raw_value` in rendered HTML `doc` and return its data-masked
    attribute text (still HTML-escaped, as written to the file), or None."""
    escaped = re.escape(htmllib.escape(str(raw_value), quote=True))
    pattern = re.compile(
        r'<span class="val pii-value" data-masked="([^"]*)">' + escaped + r"</span>"
    )
    m = pattern.search(doc)
    return m.group(1) if m else None


def _simulate_export(doc):
    """Replicate, in Python, what the toolbar's "Export masked copy" JS does
    to a clone of the document: drop the toolbar, and for every .pii-value
    span replace its text with its data-masked value and drop the attribute.
    """
    out = re.sub(r"<div id='pii-toolbar'>.*?</div>", "", doc, flags=re.DOTALL)
    out = re.sub(
        r'<span class="val pii-value" data-masked="([^"]*)">[^<]*</span>',
        lambda m: f'<span class="val pii-value">{m.group(1)}</span>',
        out,
    )
    return out


# ============================================================
#  EVALUATION
# ============================================================


def _check_mask_cases():
    rows, failures = [], []
    for name, value, ttype, expected in MASK_CASES:
        actual = mask_value(value, ttype)
        ok = actual == expected
        rows.append(("MASK", name, actual, ok,
                      "ok" if ok else f"expected {expected!r}, got {actual!r}"))
        if not ok:
            failures.append((name, actual, expected))
    return rows, failures


def _check_html_render():
    rows, failures = [], []
    results = _build_fixture_results()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "report_masking_fixture.html")
        doc = generate_html(
            results,
            out_path,
            skipped_files=[
                {"file": f"/tmp/scans/{FIXTURE_FILENAME}", "reason": "symlink"}
            ],
        )

        for category, value in FIXTURE_FINDINGS.items():
            expected_masked = htmllib.escape(mask_value(value, category), quote=True)
            actual_masked = _pii_span_masked_value(doc, value)
            ok = actual_masked == expected_masked
            name = f"HTML span for {category}"
            rows.append(("RENDER", name, str(actual_masked), ok,
                         "ok" if ok else f"expected data-masked={expected_masked!r}, "
                                         f"got {actual_masked!r}"))
            if not ok:
                failures.append((name, actual_masked, expected_masked))

        for key, value in FIXTURE_METADATA.items():
            expected_masked = htmllib.escape(mask_value(value, ""), quote=True)
            actual_masked = _pii_span_masked_value(doc, value)
            ok = actual_masked == expected_masked
            name = f"HTML generic-mask span for metadata.{key}"
            rows.append(("RENDER", name, str(actual_masked), ok,
                         "ok" if ok else f"expected data-masked={expected_masked!r}, "
                                         f"got {actual_masked!r}"))
            if not ok:
                failures.append((name, actual_masked, expected_masked))

        # Working copy must still show every raw value in plain text.
        for category, value in FIXTURE_FINDINGS.items():
            ok = value in doc
            name = f"Working copy shows raw {category}"
            rows.append(("RENDER", name, "present" if ok else "MISSING", ok,
                         "ok" if ok else "raw value missing from working copy"))
            if not ok:
                failures.append((name, "missing", "present"))

        for key, value in FIXTURE_METADATA.items():
            ok = value in doc
            name = f"Working copy shows raw metadata.{key}"
            rows.append(("RENDER", name, "present" if ok else "MISSING", ok,
                         "ok" if ok else "raw metadata missing from working copy"))
            if not ok:
                failures.append((name, "missing", "present"))

        for filename in (FIXTURE_FILENAME, FIXTURE_FAILED_FILENAME):
            ok = filename in doc
            name = f"Working copy shows full filename {filename}"
            rows.append(
                (
                    "RENDER",
                    name,
                    "present" if ok else "MISSING",
                    ok,
                    "ok" if ok else "full filename missing from working copy",
                )
            )
            if not ok:
                failures.append((name, "missing", "present"))

        exported = _simulate_export(doc)

        # Exported copy must contain NO raw fixture values anywhere.
        for category, value in FIXTURE_FINDINGS.items():
            ok = value not in exported
            name = f"Exported copy has no raw {category}"
            rows.append(("EXPORT", name, "absent" if ok else "LEAKED", ok,
                         "ok" if ok else "raw value leaked into exported copy"))
            if not ok:
                failures.append((name, "leaked", "absent"))

        # Metadata fields can contain names and identifiers. Search the whole
        # masked document, not only the collapsed metadata table.
        for key, value in FIXTURE_METADATA.items():
            ok = value not in exported
            name = f"Exported copy has no raw metadata.{key}"
            rows.append(("EXPORT", name, "absent" if ok else "LEAKED", ok,
                         "ok" if ok else "raw metadata leaked into exported copy"))
            if not ok:
                failures.append((name, "leaked", "absent"))

        # Filenames and paths are never masked in the working
        # copy OR the export — a masked path names no file, and a masked
        # report exists to tell someone which files need action. Search the
        # WHOLE masked export so table, details, alarm, skip, and failure
        # surfaces are all covered.
        for filename in (
            FIXTURE_FILENAME,
            FIXTURE_FAILED_FILENAME,
            FIXTURE_TRICKY_FILENAME,
            FIXTURE_DUP_BASENAME,
        ):
            escaped = htmllib.escape(filename, quote=True)
            ok = escaped in exported
            name = f"Exported copy still shows plaintext filename {filename}"
            rows.append(
                (
                    "EXPORT",
                    name,
                    "present" if ok else "MASKED",
                    ok,
                    "ok" if ok else "filename was masked in exported copy",
                )
            )
            if not ok:
                failures.append((name, "masked", "present"))

        # Full paths (not just basenames) are shown unmasked too — the per-
        # file detail card's path line.
        for full_path in (
            f"/tmp/scans/{FIXTURE_FILENAME}",
            f"/tmp/scans/tricky/{FIXTURE_TRICKY_FILENAME}",
            f"/tmp/scans/dirA/{FIXTURE_DUP_BASENAME}",
            f"/tmp/scans/dirB/{FIXTURE_DUP_BASENAME}",
        ):
            escaped_path = htmllib.escape(full_path, quote=True)
            ok = escaped_path in exported
            name = f"Exported copy still shows full path {full_path}"
            rows.append(
                (
                    "EXPORT",
                    name,
                    "present" if ok else "MASKED",
                    ok,
                    "ok" if ok else "path was masked/missing in exported copy",
                )
            )
            if not ok:
                failures.append((name, "masked", "present"))

        # Finding values must still be masked in the export even though
        # names/paths are not — the two are independent guarantees.
        for category, value in FIXTURE_FINDINGS.items():
            ok = value not in exported
            name = f"Exported copy still masks finding value {category}"
            rows.append(("EXPORT", name, "absent" if ok else "LEAKED", ok,
                         "ok" if ok else "finding value leaked into exported copy"))
            if not ok:
                failures.append((name, "leaked", "absent"))

        ok = "data-masked=" not in exported
        rows.append(("EXPORT", "Exported copy has no data-masked attrs",
                     "absent" if ok else "present", ok,
                     "ok" if ok else "data-masked attribute survived export"))
        if not ok:
            failures.append(("data-masked attrs", "present", "absent"))

        # Check the toolbar *element* is gone (CSS still legitimately
        # mentions the #pii-toolbar selector name, which carries no PII).
        ok = "<div id='pii-toolbar'>" not in exported
        rows.append(("EXPORT", "Exported copy has no toolbar",
                     "absent" if ok else "present", ok,
                     "ok" if ok else "toolbar element survived export"))
        if not ok:
            failures.append(("toolbar", "present", "absent"))

    return rows, failures


def _check_markdown_json_unchanged():
    rows, failures = [], []
    results = _build_fixture_results()

    md_text = generate_markdown(results, os.path.join(
        tempfile.mkdtemp(), "report_masking_fixture.md"))
    json_report = generate_json(results, os.path.join(
        tempfile.mkdtemp(), "report_masking_fixture.json"))
    json_text = json.dumps(json_report)

    for label, text in (("Markdown", md_text), ("JSON", json_text)):
        ok = "pii-value" not in text and "data-masked" not in text
        rows.append(("UNCHANGED", f"{label} has no pii-value/data-masked markup",
                     "clean" if ok else "TAINTED", ok,
                     "ok" if ok else "masking markup leaked into renderer meant to stay untouched"))
        if not ok:
            failures.append((f"{label} markup", "tainted", "clean"))

        for category, value in FIXTURE_FINDINGS.items():
            ok = value in text
            rows.append(("UNCHANGED", f"{label} still shows full {category}",
                         "present" if ok else "MISSING", ok,
                         "ok" if ok else "full value missing from unchanged renderer"))
            if not ok:
                failures.append((f"{label} {category}", "missing", "present"))

    return rows, failures


def _check_anchor_links():
    """All Files table rows link to a Details card id that exists exactly
    once — including file names with spaces, commas, and ampersands, and
    two files sharing a basename in different folders — and the link
    target survives the masked export unchanged (no JS involved in either)."""
    rows, failures = [], []
    results = _build_fixture_results()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "report_anchor_fixture.html")
        doc = generate_html(results, out_path)

        # Every <a href='#id'>NAME</a> cell in the All Files table.
        table_match = re.search(
            r"<h2 id='all-files'>.*?</table>", doc, flags=re.DOTALL
        )
        ok = table_match is not None
        rows.append(("ANCHOR", "All Files table has id='all-files' anchor",
                     "present" if ok else "MISSING", ok,
                     "ok" if ok else "could not locate the All Files table"))
        if not ok:
            failures.append(("all-files table", "missing", "present"))
            return rows, failures

        links = re.findall(
            r"<td><a class='file-link' href='#([^']+)'>([^<]*)</a></td>", table_match.group(0)
        )
        ok = len(links) == len(results)
        rows.append(("ANCHOR", "one table link per scanned file",
                     str(len(links)), ok,
                     "ok" if ok else f"expected {len(results)} links, found {len(links)}"))
        if not ok:
            failures.append(("link count", len(links), len(results)))

        # Every linked id resolves to exactly one section in the document —
        # not zero (dead link), not two-or-more (id collision).
        for anchor_id, label in links:
            count = len(re.findall(r"id='" + re.escape(anchor_id) + r"'", doc))
            ok = count == 1
            name = f"id '{anchor_id}' (for {label!r}) exists exactly once"
            rows.append(("ANCHOR", name, str(count), ok,
                         "ok" if ok else f"expected exactly 1 occurrence, found {count}"))
            if not ok:
                failures.append((name, count, 1))

        # The two duplicate-basename files (different folders) must resolve
        # to two DIFFERENT ids — id derivation must use the full path.
        dup_ids = {
            anchor_id for anchor_id, label in links
            if label == htmllib.escape(FIXTURE_DUP_BASENAME, quote=True)
        }
        ok = len(dup_ids) == 2
        rows.append(
            (
                "ANCHOR",
                "same-basename files in different folders get different ids",
                str(sorted(dup_ids)),
                ok,
                "ok" if ok else "duplicate basenames collided onto the same anchor id",
            )
        )
        if not ok:
            failures.append(("dup basename ids", dup_ids, "2 distinct ids"))

        # The tricky filename (spaces, &, ,) must have produced a valid,
        # sanitized id — no raw special characters carried through.
        tricky_label = htmllib.escape(FIXTURE_TRICKY_FILENAME, quote=True)
        tricky_ids = [aid for aid, label in links if label == tricky_label]
        ok = len(tricky_ids) == 1 and re.fullmatch(r"[A-Za-z0-9-]+", tricky_ids[0]) is not None
        rows.append(
            (
                "ANCHOR",
                "tricky filename (spaces/&/,) sanitizes to a safe id",
                tricky_ids[0] if tricky_ids else "MISSING",
                ok,
                "ok" if ok else "id missing or contains unsafe characters",
            )
        )
        if not ok:
            failures.append(("tricky id sanitization", tricky_ids, "one safe id"))

        # Each detail card also has a "back to top" link to the table.
        back_links = doc.count("<a class='back-to-top' href='#all-files'>")
        ok = back_links == len(results)
        rows.append(
            (
                "ANCHOR",
                "every detail card has a back-to-top link",
                str(back_links),
                ok,
                "ok" if ok else f"expected {len(results)} back-links, found {back_links}",
            )
        )
        if not ok:
            failures.append(("back links", back_links, len(results)))

        # The link target must survive the masked export unchanged.
        exported = _simulate_export(doc)
        for anchor_id, label in links:
            href_ok = f"href='#{anchor_id}'" in exported
            id_ok = f"id='{anchor_id}'" in exported
            ok = href_ok and id_ok
            name = f"link + target for {label!r} survive masked export"
            rows.append(("ANCHOR", name, "intact" if ok else "BROKEN", ok,
                         "ok" if ok else "href or id attribute lost during export"))
            if not ok:
                failures.append((name, "broken", "intact"))

    return rows, failures


def run_suite():
    rows, failures = [], []
    for fn in (
        _check_mask_cases, _check_html_render, _check_markdown_json_unchanged,
        _check_anchor_links,
    ):
        r, f = fn()
        rows.extend(r)
        failures.extend(f)

    print(f"{'GRP':9} {'CASE':50} {'RESULT':7} {'ACTUAL':22}")
    print("-" * 100)
    for grp, name, actual, ok, reason in rows:
        status = "PASS" if ok else "FAIL"
        line = f"{grp:9} {name:50} {status:7} {str(actual):22}"
        if not ok:
            line += f"  <-- {reason}"
        print(line)

    passed = sum(1 for r in rows if r[3])
    failed = len(rows) - passed
    print("-" * 100)
    print(f"SUMMARY: {passed}/{len(rows)} passed, {failed} failed")
    return passed, failed, failures


# ============================================================
#  PYTEST ENTRY POINTS
# ============================================================


def test_mask_value_cases():
    for name, value, ttype, expected in MASK_CASES:
        actual = mask_value(value, ttype)
        assert actual == expected, f"{name}: expected {expected!r}, got {actual!r}"


def test_html_render_and_export_simulation():
    _, failures = _check_html_render()
    assert not failures, failures


def test_markdown_json_unchanged():
    _, failures = _check_markdown_json_unchanged()
    assert not failures, failures


def test_anchor_links():
    _, failures = _check_anchor_links()
    assert not failures, failures


if __name__ == "__main__":
    _, failed, _ = run_suite()
    sys.exit(1 if failed else 0)
