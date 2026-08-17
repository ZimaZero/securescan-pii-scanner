#!/usr/bin/env python3
# tests/test_keyword_negation.py
"""
Test suite for the keyword detector's negation/placeholder suppression
(detectors/keyword_detector.py).

A value the text explicitly calls NOT real PII (via a short, high-precision
negation phrase in a TIGHT window) must be suppressed — but a real value must
still flag, and a negation about a *different* nearby item must NOT suppress it.

Run directly:
    docker compose run --rm securescan-cpu python tests/test_keyword_negation.py
Also pytest-compatible.
"""

import os
import sys
import contextlib
import io
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors import keyword_detector

detect_pii_keywords = keyword_detector.detect_pii_keywords

# Regression fixture for the window-truncation bug (root-caused in
# tests/external_enron/EVALUATION.md finding #1): find_patterns_near_keywords()
# used to slice a +/-100-char window around each "(E-mail)" keyword occurrence
# and match the email-shape regex INSIDE that slice. In a dense Enron-style
# X-To: recipient list, one recipient's window would clip a NEIGHBORING
# recipient's already-correctly-matched address at the window boundary,
# producing a truncated fragment finding (e.g. 'John_H_Stout@reliantenergy.com'
# clipped to 't@reliantenergy.com'). The fix matches the email-shape regex
# over the FULL text once and gates by keyword proximity instead, so a match
# can never be clipped by a window edge.
_DENSE_RECIPIENTS = [
    ("Whyde, Robert D", "rwhyde@duke-energy.com"),
    ("Stout, John Henry", "John_H_Stout@reliantenergy.com"),
    ("Moretti, Michael A", "mmoretti@mccabeandcompany.net"),
    ("Nguyen, Thi Van", "tnguyen@powersrc.com"),
    ("Hyde, Katherine L", "khyde@duke-energy.com"),
    ("Otero, Luis Fernando", "lotero@williams.com"),
    ("Gaoka, Samuel R", "sgaoka@uaecorp.com"),
    ("Eisenman, Richard J", "reisenman@gen.pge.com"),
]
DENSE_XTO_TEXT = "X-To: " + ", ".join(
    f"{name} (E-mail) <{addr}>" for name, addr in _DENSE_RECIPIENTS
)

BACKEND_FIXTURES = [
    "SIN on file: 480 184 514",
    "Employee Emall: analyst@example.org",
    "DOB 1990-02-01; Phone (403) 555-0198",
    "SSN: 123-45-6789 (example)",
    DENSE_XTO_TEXT,
]

# (name, text, pii_type) — the pii_type (e.g. "ssn") must NOT appear as
# "<type>_context" in the result.
SHOULD_SUPPRESS = [
    ("PIN 'looks like an SSN, is NOT'",
     "Support PIN 078051121 (looks like an SSN, is NOT)", "ssn"),
    ("SSN 'is not' real",
     "SSN 123-45-6789 is not a real number", "ssn"),
    ("SSN placeholder '(example)'",
     "SSN: 123-45-6789 (example)", "ssn"),
    ("SIN 'resembles'",
     "This value resembles a SIN 193 456 787 but is fake", "sin"),
    # Regression lock for the email/URL-masking fix: a real standalone
    # "(example)" placeholder word (not inside an email/URL) must still
    # suppress, even though placeholder terms INSIDE an address no longer do.
    ("SSN placeholder '(example)' still suppressed after email/URL masking fix",
     "SSN: 123-45-6789 (example)", "ssn"),
]

# (name, text, pii_type) — the pii_type MUST appear as "<type>_context".
SHOULD_FLAG = [
    ("plain real SSN",
     "Employee SSN: 078-05-1120", "ssn"),
    ("plain real SIN",
     "SIN 480 184 514 on file", "sin"),
    ("'notation' must not fire 'not' boundary",
     "Notation SSN: 123-45-6789", "ssn"),
    # Negation is for a DIFFERENT item further away than the tight window: the
    # real SSN two lines above a "Do NOT confuse..." header must still flag.
    ("real SSN above a 'Do NOT confuse' header",
     "SSN 078-05-1120\n- SIN 193 456 787\n- health card 5544 332 211\n\n"
     "Do NOT confuse these with internal identifiers", "ssn"),
    # Regression test for the email/URL-masking fix: "example" and "format"
    # inside "format.test@example.com" are part of the address, not
    # standalone placeholder words, so they must not suppress a genuinely
    # nearby postal code.
    ("postal code detected despite nearby example.com email",
     "Contact format.test@example.com. Postal Code T2X 1V4", "postal_code_ca"),
]

DATE_ASSOCIATION_CASES = [
    (
        "same-line DOB label retains birth date",
        "3. DOB/DDN 1991/01/01",
        "1991/01/01",
        True,
    ),
    (
        "preceding-line expiry label suppresses date despite later DOB label",
        "4b. Exp\n2023/08/30 3. DOB/DDN 1991/01/01",
        "2023/08/30",
        False,
    ),
    (
        "preceding-line issue label suppresses date",
        "4a. Iss/Del\n2019/08/30",
        "2019/08/30",
        False,
    ),
    (
        "same-rank DOB and expiry conflict retains for recall",
        "DOB EXP 1980/02/02",
        "1980/02/02",
        True,
    ),
    (
        "joined DOB1980 form is accepted",
        "DOB1980/02/02",
        "1980/02/02",
        True,
    ),
    (
        "EXP inside EXPERIENCE is not a field label",
        "EXPERIENCE\n2023/08/30",
        "2023/08/30",
        False,
    ),
]


def _has(result, pii_type):
    return f"{pii_type}_context" in result


def evaluate_suppress(text, pii_type):
    r = detect_pii_keywords(text)
    return (not _has(r, pii_type)), r


def evaluate_flag(text, pii_type):
    r = detect_pii_keywords(text)
    return _has(r, pii_type), r


def evaluate_dense_recipient_list():
    r = detect_pii_keywords(DENSE_XTO_TEXT)
    found = {v for v, _ in r.get("email_context", [])}
    real = {addr for _, addr in _DENSE_RECIPIENTS}
    missing = real - found
    truncated = found - real
    return (not missing and not truncated), r, missing, truncated


def evaluate_backend_parity():
    rows = []
    for text in BACKEND_FIXTURES:
        aho = keyword_detector._detect_pii_keywords(text, force_backend="aho")
        regex = keyword_detector._detect_pii_keywords(text, force_backend="regex")
        rows.append((aho == regex, aho, regex))
    return all(ok for ok, _aho, _regex in rows), rows


def evaluate_import_fallback():
    expected = keyword_detector._detect_pii_keywords(
        BACKEND_FIXTURES[1], force_backend="regex"
    )
    saved = (
        keyword_detector._AHO_AUTOMATON,
        keyword_detector._AHO_UNAVAILABLE,
        keyword_detector._AHO_WARNING_EMITTED,
    )
    output = io.StringIO()
    try:
        keyword_detector._AHO_AUTOMATON = None
        keyword_detector._AHO_UNAVAILABLE = False
        keyword_detector._AHO_WARNING_EMITTED = False
        real_import = keyword_detector.importlib.import_module

        def fail_aho(name):
            if name == "ahocorasick":
                raise ImportError("injected missing dependency")
            return real_import(name)

        with mock.patch.object(
            keyword_detector.importlib, "import_module", side_effect=fail_aho
        ), contextlib.redirect_stdout(output):
            first = keyword_detector.detect_pii_keywords(BACKEND_FIXTURES[1])
            second = keyword_detector.detect_pii_keywords(BACKEND_FIXTURES[1])
    finally:
        (
            keyword_detector._AHO_AUTOMATON,
            keyword_detector._AHO_UNAVAILABLE,
            keyword_detector._AHO_WARNING_EMITTED,
        ) = saved

    warnings = [
        line for line in output.getvalue().splitlines()
        if "compiled-regex fallback" in line
    ]
    return first == expected and second == expected and len(warnings) == 1, warnings


def evaluate_date_association(text, value, expected):
    result = detect_pii_keywords(text, as_of_date=date(2026, 8, 6))
    values = {item[0] for item in result.get("dob_context", [])}
    return (value in values) is expected, result


def evaluate_future_date_signal():
    text = "DOB2030/01/01"
    before = detect_pii_keywords(text, as_of_date=date(2030, 1, 1))
    after = detect_pii_keywords(text, as_of_date=date(2026, 8, 6))
    return (
        "dob_context" in before and "dob_context" not in after,
        {"as_of_2030": before, "as_of_2026": after},
    )


def run_suite():
    rows, failed = [], 0
    for name, text, t in SHOULD_SUPPRESS:
        ok, r = evaluate_suppress(text, t)
        rows.append(("SUPPRESS", name, ok, r)); failed += not ok
    for name, text, t in SHOULD_FLAG:
        ok, r = evaluate_flag(text, t)
        rows.append(("FLAG", name, ok, r)); failed += not ok
    ok, r, missing, truncated = evaluate_dense_recipient_list()
    rows.append(("WINDOW", "dense X-To: recipient list, no truncated fragments", ok, r))
    failed += not ok
    ok, parity_rows = evaluate_backend_parity()
    rows.append(("BACKEND", "Aho and regex paths have exact fixture parity", ok, parity_rows))
    failed += not ok
    ok, warnings = evaluate_import_fallback()
    rows.append(("FALLBACK", "missing Aho warns once and retains findings", ok, warnings))
    failed += not ok
    for name, text, value, expected in DATE_ASSOCIATION_CASES:
        ok, result = evaluate_date_association(text, value, expected)
        rows.append(("DATE", name, ok, result)); failed += not ok
    ok, result = evaluate_future_date_signal()
    rows.append(("DATE", "future-date signal uses injected as-of date", ok, result))
    failed += not ok

    print(f"{'GRP':10} {'CASE':48} {'RESULT':6}")
    print("-" * 78)
    for grp, name, ok, r in rows:
        line = f"{grp:10} {name:48} {'PASS' if ok else 'FAIL':6}"
        if not ok:
            line += f"  <-- got {r}"
        print(line)
    print("-" * 78)
    print(f"SUMMARY: {len(rows) - failed}/{len(rows)} passed, {failed} failed")
    return failed


def test_should_suppress():
    for name, text, t in SHOULD_SUPPRESS:
        ok, r = evaluate_suppress(text, t)
        assert ok, f"{name}: expected {t} suppressed, got {r}"


def test_should_flag():
    for name, text, t in SHOULD_FLAG:
        ok, r = evaluate_flag(text, t)
        assert ok, f"{name}: expected {t} flagged, got {r}"


def test_dense_recipient_list_no_truncation():
    ok, r, missing, truncated = evaluate_dense_recipient_list()
    assert ok, (
        f"expected all {len(_DENSE_RECIPIENTS)} full addresses found with no "
        f"truncated fragments; missing={missing} truncated={truncated} got={r}"
    )


def test_aho_and_regex_backend_parity():
    ok, rows = evaluate_backend_parity()
    assert ok, rows


def test_missing_aho_falls_back_with_one_warning():
    ok, warnings = evaluate_import_fallback()
    assert ok, warnings


def test_ranked_date_field_association():
    for name, text, value, expected in DATE_ASSOCIATION_CASES:
        ok, result = evaluate_date_association(text, value, expected)
        assert ok, f"{name}: expected present={expected}, got {result}"


def test_future_date_signal_uses_injected_date():
    ok, result = evaluate_future_date_signal()
    assert ok, result


if __name__ == "__main__":
    sys.exit(1 if run_suite() else 0)
