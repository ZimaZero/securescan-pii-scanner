#!/usr/bin/env python3
# tests/test_passports.py
"""
Reusable test suite for detectors/passport_detector.py.

Groups:
  - SHOULD_MATCH: input text + expected (type / value).
  - SHOULD_SKIP:  input text that MUST return nothing ({}).

Run directly for a pass/fail table + summary:
    docker compose run --rm securescan-cpu python tests/test_passports.py

Also pytest-compatible (test_should_match / test_should_skip).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors.passport_detector import detect_passports

# Each MATCH case: (name, text, expectation) where expectation has optional
# keys: type, value, min_findings, types.
SHOULD_MATCH = [
    # --- Canadian: 2 letters + 6 digits, keyword required ---
    ("CA with 'Passport:' keyword", "Passport: AB123456",
     {"type": "passport_ca", "value": "AB123456"}),
    ("CA 'passport number' + space", "passport number AB 123456",
     {"type": "passport_ca", "value": "AB123456"}),
    ("CA 'travel document' keyword", "Travel document AB123456 on file",
     {"type": "passport_ca", "value": "AB123456"}),

    # --- Generic: 9 digits, passport keyword required ---
    ("Generic 9-digit + keyword", "Passport No 123456789 (foreign)",
     {"type": "passport_generic", "value": "123456789"}),
]

SHOULD_SKIP = [
    # --- Canadian pattern, but NO passport keyword ---
    ("Bare CA, no keyword", "AB123456"),
    ("CA in unrelated text", "The product code AB123456 shipped today"),

    # --- look-alikes / other PII with no passport context ---
    ("Postal-code-like", "Mail it to A1A 1A1 please"),
    ("Email, no passport context", "Contact alex@example.com for details"),
    ("SIN, no passport context", "Employee SIN 193456787 on record"),
    ("Bare 9-digit, no keyword", "Reference number 123456789 follows"),
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
        result = detect_passports(text)
        ok, reason = evaluate_match(result, exp)
        rows.append(("MATCH", name, _summarize(result), ok, reason))
        if not ok:
            failures.append((name, reason, result))

    for name, text in SHOULD_SKIP:
        result = detect_passports(text)
        ok, reason = evaluate_skip(result)
        rows.append(("SKIP", name, _summarize(result), ok, reason))
        if not ok:
            failures.append((name, reason, result))

    print(f"{'GRP':5} {'CASE':32} {'RESULT':7} {'ACTUAL':28}")
    print("-" * 92)
    for grp, name, actual, ok, reason in rows:
        status = "PASS" if ok else "FAIL"
        line = f"{grp:5} {name:32} {status:7} {actual:28}"
        if not ok:
            line += f"  <-- {reason}"
        print(line)

    passed = sum(1 for r in rows if r[3])
    failed = len(rows) - passed
    print("-" * 92)
    print(f"SUMMARY: {passed}/{len(rows)} passed, {failed} failed")
    return passed, failed, failures


# ============================================================
#  PYTEST ENTRY POINTS
# ============================================================


def test_should_match():
    for name, text, exp in SHOULD_MATCH:
        ok, reason = evaluate_match(detect_passports(text), exp)
        assert ok, f"{name}: {reason}"


def test_should_skip():
    for name, text in SHOULD_SKIP:
        ok, reason = evaluate_skip(detect_passports(text))
        assert ok, f"{name}: {reason}"


if __name__ == "__main__":
    _, failed, _ = run_suite()
    sys.exit(1 if failed else 0)
