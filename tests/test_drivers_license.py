#!/usr/bin/env python3
# tests/test_drivers_license.py
"""
Reusable test suite for detectors/drivers_license_detector.py.

Groups:
  - SHOULD_MATCH: input text + expected (type / value).
  - SHOULD_SKIP:  input text that MUST return nothing ({}) — the keyword/province
                  gate is doing all the work, so anything without context leaks.

Run directly for a pass/fail table + summary:
    docker compose run --rm securescan-cpu python tests/test_drivers_license.py

Also pytest-compatible (test_should_match / test_should_skip).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors.drivers_license_detector import detect_drivers_licenses
from detectors.hybrid_detector import detect_pii_hybrid

# Each MATCH case: (name, text, expectation) with optional keys: type, value,
# min_findings, exact_findings, types.
SHOULD_MATCH = [
    # --- Distinctive alphanumeric formats ---
    ("ON (letter + 14, dashed)", "Ontario driver's licence A1234-56789-01231",
     {"type": "drivers_license_on", "value": "A1234-56789-01231"}),
    ("ON (letter + 14, bare + both keywords)", "ON DL A12345678901231 on file",
     {"type": "drivers_license_on", "value": "A12345678901231"}),
    ("ON Purview permits 00 ending", "Ontario driver's licence A1234-56789-01200",
     {"type": "drivers_license_on", "value": "A1234-56789-01200"}),
    ("ON Purview permits 32 ending", "Ontario driver's licence A1234-56789-01232",
     {"type": "drivers_license_on", "value": "A1234-56789-01232"}),
    ("QC compact (letter + 12)", "Quebec driver licence L123456789012",
     {"type": "drivers_license_qc", "value": "L123456789012"}),
    ("QC printed (hyphenated)", "Quebec driver licence L1531-171274-08",
     {"type": "drivers_license_qc", "value": "L1531-171274-08", "exact_findings": 1}),
    ("NS compact (Purview)", "Nova Scotia DL PUBLI020220005",
     {"type": "drivers_license_ns", "value": "PUBLI020220005"}),
    ("NS printed optional hyphen", "Nova Scotia DL PUBLI-020220005",
     {"type": "drivers_license_ns", "value": "PUBLI-020220005", "exact_findings": 1}),
    ("NL (letter + 9)", "Newfoundland driver's licence A123456789",
     {"type": "drivers_license_nl", "value": "A123456789"}),
    ("MB compact (Purview)", "Manitoba driver's licence PUBLIJQ008NH",
     {"type": "drivers_license_mb", "value": "PUBLIJQ008NH"}),
    ("MB printed hyphenated", "Manitoba driver's licence PU-BL-IJ-Q008NH",
     {"type": "drivers_license_mb", "value": "PU-BL-IJ-Q008NH", "exact_findings": 1}),

    # --- Loose numeric: province name + matching length ---
    ("BC legacy (7 digits)", "British Columbia driver licence 1234567",
     {"type": "drivers_license_bc", "value": "1234567"}),
    ("SK (8 digits)", "Saskatchewan driver licence 12345678",
     {"type": "drivers_license_sk", "value": "12345678"}),
    ("PE (5 digits)", "Prince Edward Island driver's licence 12345",
     {"type": "drivers_license_pe", "value": "12345"}),
    ("PE (6 digits)", "Prince Edward Island driver's licence 123456",
     {"type": "drivers_license_pe", "value": "123456"}),
    ("YT (6 digits)", "Yukon driver's licence 654321",
     {"type": "drivers_license_yt", "value": "654321"}),
    ("NT specimen-derived 10 digits", "Northwest Territories driver's licence 1234567890",
     {"type": "drivers_license_nt", "value": "1234567890"}),
    ("NU specimen-derived printed", "Nunavut driver's licence A1234 5678-004",
     {"type": "drivers_license_nu", "value": "A1234 5678-004"}),
    ("NU specimen-derived compact", "Nunavut driver's licence A12345678004",
     {"type": "drivers_license_nu", "value": "A12345678004"}),
    ("AB bare Purview alternative", "Alberta DL 12345",
     {"type": "drivers_license_ab", "value": "12345"}),
    ("AB hyphenated Purview form", "Alberta DL 134711-320",
     {"type": "drivers_license_ab", "value": "134711-320", "exact_findings": 1}),

    # --- Purview keyword classes and 300-character proximity ---
    ("French generic keyword", "QC Permis de Conduire L1531-171274-08",
     {"type": "drivers_license_qc", "value": "L1531-171274-08"}),
    ("compact generic keyword", "MB DriverLic PU-BL-IJ-Q008NH",
     {"type": "drivers_license_mb", "value": "PU-BL-IJ-Q008NH"}),
    ("broad identification keyword", "SK identification card 20000030",
     {"type": "drivers_license_sk", "value": "20000030"}),
    ("territory abbreviation plus ID", "NT Member ID 1234567890",
     {"type": "drivers_license_nt", "value": "1234567890"}),
    (
        "both keywords within 300 characters",
        "Manitoba " + ("x" * 120) + " Driver's Licence " + ("y" * 120)
        + " PU-BL-IJ-Q008NH",
        {"type": "drivers_license_mb", "value": "PU-BL-IJ-Q008NH"},
    ),
]

SHOULD_SKIP = [
    # --- distinctive format, but NO keyword/province ---
    ("Bare ON format, no context", "A1234-56789-01234"),
    ("ON birth-day suffix 99 invalid", "Ontario driver's licence A1234-56789-01299"),
    ("Bare QC printed format, no context", "L1531-171274-08"),
    ("Bare NS printed format, no context", "PUBLI-020220005"),
    ("Bare NL format, no context", "A123456789"),
    ("Bare MB-shaped token, no context", "PUBLIJQ008NH"),
    ("Bare MB asterisk token, no context", "A1234*567890"),
    ("BC superseded 8-digit shape", "British Columbia driver licence 12345678"),
    ("NS superseded double-hyphen shape", "Nova Scotia DL DEMOX--430328096"),
    ("MB superseded asterisk shape", "Manitoba licence number A1234*567890"),
    ("NT superseded 6-digit guess", "Northwest Territories driver's licence 123456"),
    ("NU superseded 6-digit guess", "Nunavut driver's licence 123456"),

    # --- Purview requires BOTH keyword classes ---
    ("generic keyword without province", "Driver's licence A1234-56789-01231"),
    ("province without generic keyword", "Ontario record A1234-56789-01231"),
    (
        "jurisdiction outside 300-character proximity",
        "Manitoba " + ("x" * 301) + " Driver's Licence PU-BL-IJ-Q008NH",
    ),
    (
        "context slice cannot create NT from Amount suffix",
        "Amount" + ("x" * 298) + " Member ID 1234567890",
    ),

    # --- loose numbers with no DL context (collision guard) ---
    ("Bare 7-digit, no context", "Reference 1234567 follows"),
    ("Bare 8-digit, no context", "Reference 12345678 follows"),
    ("Phone number, no DL context", "Call me at 403-555-0123"),
    ("SIN, no DL context", "Employee SIN 193456787 on record"),
    ("Postal code, no DL context", "Mail to K1A 0B1 please"),
    ("Passport, no DL context", "passport number AB 123456"),

    # --- joined-token fragment boundary guard ---
    ("Generic fragments of non-AB hyphenated token", "Driver's licence 123456-7890"),
    ("Generic right fragment of hyphenated token", "Driver's licence 819-123456"),
    ("Numeric fragment beside asterisk", "Alberta DL DEMO*392"),
    ("Distinctive fragment beside alphanumeric", "Ontario DL XA1234-56789-01231"),
    (
        "YT issue date is not a licence",
        "Yukon driver's licence\nIss\n202404/15",
    ),
    (
        "YT OCR-degraded I88 issue label is not a licence",
        "Yukon driver's licence\nI88/DEL\n202404/15",
    ),
]

SIN_GATE_CASES = [
    (
        "standalone context-free SIN survives",
        "Reference number 430328096 is recorded",
        ("identifier.financial_unverified.sin", "MEDIUM"),
    ),
    (
        "hyphen-embedded SIN-shaped suffix is rejected",
        "Nova Scotia licence DEMOX--430328096",
        None,
    ),
    (
        "asterisk-embedded SIN-shaped suffix is rejected",
        "Internal token DEMOX*430328096",
        None,
    ),
    (
        "explicit SIN context permits embedded value",
        "SIN: DEMOX--430328096",
        ("identifier.financial.sin", "HIGH"),
    ),
]

# ============================================================
#  EVALUATION
# ============================================================


def _total(result):
    return sum(len(v) for v in result.values())


def _all_values(result):
    return {v for items in result.values() for v, _ in items}


def evaluate_match(result, exp):
    if not result:
        return False, "no detections (expected a match)"
    if "type" in exp and exp["type"] not in result:
        return False, f"missing type {exp['type']!r} (got {sorted(result)})"
    if "value" in exp and exp["value"] not in _all_values(result):
        return False, f"missing value {exp['value']!r}"
    if "types" in exp:
        missing = [t for t in exp["types"] if t not in result]
        if missing:
            return False, f"missing types {missing}"
    if "min_findings" in exp and _total(result) < exp["min_findings"]:
        return False, f"only {_total(result)} findings (< {exp['min_findings']})"
    if "exact_findings" in exp and _total(result) != exp["exact_findings"]:
        return False, f"{_total(result)} findings (!= {exp['exact_findings']})"
    return True, "ok"


def evaluate_skip(result):
    if result:
        return False, f"leaked: {result}"
    return True, "ok"


def _summarize(result):
    if not result:
        return "{}"
    return ", ".join(f"{t}={len(v)}" for t, v in sorted(result.items()))


def run_suite():
    rows = []
    failures = []

    for name, text, exp in SHOULD_MATCH:
        result = detect_drivers_licenses(text)
        ok, reason = evaluate_match(result, exp)
        rows.append(("MATCH", name, _summarize(result), ok, reason))
        if not ok:
            failures.append((name, reason, result))

    for name, text in SHOULD_SKIP:
        result = detect_drivers_licenses(text)
        ok, reason = evaluate_skip(result)
        rows.append(("SKIP", name, _summarize(result), ok, reason))
        if not ok:
            failures.append((name, reason, result))

    for name, text, expected in SIN_GATE_CASES:
        result = detect_pii_hybrid(text, run_ner=False, verify=False)
        sin_findings = {
            category: items
            for category, items in result.items()
            if category.startswith("identifier.financial") and category.endswith(".sin")
        }
        if expected is None:
            ok = not sin_findings
        else:
            category, risk = expected
            ok = (
                category in sin_findings
                and any(item.get("risk_level") == risk for item in sin_findings[category])
            )
        reason = "ok" if ok else f"SIN findings={sin_findings}, expected={expected}"
        rows.append(("SIN", name, _summarize(result), ok, reason))
        if not ok:
            failures.append((name, reason, result))

    text = "Nova Scotia driver's licence: PUBLI020220005"
    result = detect_pii_hybrid(text, run_ner=False, verify=False)
    has_ns = "identifier.government.drivers_license_ns" in result
    has_sin = "identifier.financial.sin" in result
    ok = has_ns and not has_sin
    reason = "ok" if ok else f"has_ns={has_ns}, has_sin={has_sin}"
    rows.append(("HYBRID", "full NS licence wins; embedded SIN absent", _summarize(result), ok, reason))
    if not ok:
        failures.append(("full NS licence wins", reason, result))

    print(f"{'GRP':5} {'CASE':36} {'RESULT':7} {'ACTUAL':30}")
    print("-" * 96)
    for grp, name, actual, ok, reason in rows:
        status = "PASS" if ok else "FAIL"
        line = f"{grp:5} {name:36} {status:7} {actual:30}"
        if not ok:
            line += f"  <-- {reason}"
        print(line)

    passed = sum(1 for r in rows if r[3])
    failed = len(rows) - passed
    print("-" * 96)
    print(f"SUMMARY: {passed}/{len(rows)} passed, {failed} failed")
    return passed, failed, failures


# ============================================================
#  PYTEST ENTRY POINTS
# ============================================================


def test_should_match():
    for name, text, exp in SHOULD_MATCH:
        ok, reason = evaluate_match(detect_drivers_licenses(text), exp)
        assert ok, f"{name}: {reason}"


def test_should_skip():
    for name, text in SHOULD_SKIP:
        ok, reason = evaluate_skip(detect_drivers_licenses(text))
        assert ok, f"{name}: {reason}"


def test_sin_context_or_standalone_gate():
    for name, text, expected in SIN_GATE_CASES:
        result = detect_pii_hybrid(text, run_ner=False, verify=False)
        sin_findings = {
            category: items
            for category, items in result.items()
            if category.startswith("identifier.financial") and category.endswith(".sin")
        }
        if expected is None:
            assert not sin_findings, f"{name}: leaked SIN findings {sin_findings}"
        else:
            category, risk = expected
            assert category in sin_findings, (
                f"{name}: missing {category}; got {sin_findings}"
            )
            assert any(
                item.get("risk_level") == risk for item in sin_findings[category]
            ), f"{name}: missing {risk} finding; got {sin_findings[category]}"


def test_ns_full_licence_survives_without_embedded_sin():
    result = detect_pii_hybrid(
        "Nova Scotia driver's licence: PUBLI020220005",
        run_ner=False,
        verify=False,
    )
    assert "identifier.government.drivers_license_ns" in result, result
    assert "identifier.financial.sin" not in result, result
    assert "identifier.financial_unverified.sin" not in result, result


if __name__ == "__main__":
    _, failed, _ = run_suite()
    sys.exit(1 if failed else 0)
