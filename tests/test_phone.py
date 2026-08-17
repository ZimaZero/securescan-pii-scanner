#!/usr/bin/env python3
# tests/test_phone.py
"""
Test suite for the phone detector in detectors/detectors.py.

Formatted numbers (separators, or literal parens around the area code) are
always detected — separators are the evidence. A bare, separator-free
10-digit run is weak evidence on its own (it collides with any other
10-digit ID) and is only detected when a phone-context keyword sits nearby.

Groups:
  - MUST_DETECT:     text that MUST produce a "phone" match.
  - MUST_NOT_DETECT: text that MUST NOT produce a "phone" match.

Run directly for a pass/fail table:
    docker compose run --rm securescan-cpu python tests/test_phone.py
Also pytest-compatible (test_must_detect / test_must_not_detect).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors.detectors import detect_pii

MUST_DETECT = [
    ("dashes", "403-555-1234"),
    ("parens + space + dash", "(403) 555-1234"),
    ("dots", "403.555.1234"),
    ("spaces", "403 555 1234"),
    ("bare + 'Phone:' keyword", "Phone: 4035551234"),
    ("bare + 'call' keyword", "call me at 4035551234"),
    ("bare + 'Tel:' keyword", "Tel: 4035551234"),
]

MUST_NOT_DETECT = [
    ("bare, no context", "4035551234"),
    ("bare 10-digit ID, unrelated keyword", "Personal No. 3052125257"),
    ("bare 10-digit ID, generic label", "ID: 0405196153"),
    ("CSV cell, no phone header context",
     "id,name,national_id\n1,Test Record,4035551234\n"),
]


def _phones(result):
    return set(result.get("phone", []))


def run_suite():
    rows, failures = [], []

    for name, text in MUST_DETECT:
        found = _phones(detect_pii(text))
        ok = len(found) > 0
        rows.append(("DETECT", name, ok, f"got {sorted(found)}"))
        if not ok:
            failures.append(name)

    for name, text in MUST_NOT_DETECT:
        found = _phones(detect_pii(text))
        ok = len(found) == 0
        rows.append(("SKIP", name, ok, f"leaked {sorted(found)}" if found else "ok"))
        if not ok:
            failures.append(name)

    print(f"{'GRP':8} {'CASE':40} {'RESULT':7}")
    print("-" * 70)
    for grp, name, ok, reason in rows:
        line = f"{grp:8} {name:40} {'PASS' if ok else 'FAIL':7}"
        if not ok:
            line += f"  <-- {reason}"
        print(line)
    passed = sum(1 for r in rows if r[2])
    print("-" * 70)
    print(f"SUMMARY: {passed}/{len(rows)} passed, {len(rows) - passed} failed")
    return passed, len(rows) - passed


def test_must_detect():
    for name, text in MUST_DETECT:
        found = _phones(detect_pii(text))
        assert found, f"{name}: expected a phone match, got none"


def test_must_not_detect():
    for name, text in MUST_NOT_DETECT:
        found = _phones(detect_pii(text))
        assert not found, f"{name}: expected no phone match, got {sorted(found)}"


if __name__ == "__main__":
    _, failed = run_suite()
    sys.exit(1 if failed else 0)
