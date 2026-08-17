#!/usr/bin/env python3
# tests/test_ip_url.py
"""
Test suite for the IPv4 / IPv6 / URL recognizers added to detectors/detectors.py.

Groups:
  - SHOULD_MATCH: text + expected (key: ip_address|url / value).
  - SHOULD_SKIP:  text that MUST NOT produce that key (partial IPs, out-of-range
                  octets, over-long dotted runs).

Run directly for a pass/fail table:
    docker compose run --rm securescan-cpu python tests/test_ip_url.py
Also pytest-compatible (test_should_match / test_should_skip).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors.detectors import detect_pii

# (name, text, {"key": ip_address|url, "value": ...})
SHOULD_MATCH = [
    ("IPv4 private",   "server at 192.168.1.42 today",
     {"key": "ip_address", "value": "192.168.1.42"}),
    ("IPv4 public",    "gateway 203.0.113.75 external",
     {"key": "ip_address", "value": "203.0.113.75"}),
    ("IPv4 max octet", "edge 255.255.255.0 mask",
     {"key": "ip_address", "value": "255.255.255.0"}),
    ("IPv6 full",      "host 2001:0db8:85a3:0000:0000:8a2e:0370:7334 online",
     {"key": "ip_address", "value": "2001:0db8:85a3:0000:0000:8a2e:0370:7334"}),
    ("URL simple",     "portal at https://portal.northwind.com/welcome now",
     {"key": "url", "value": "https://portal.northwind.com/welcome"}),
    ("URL with query", "see https://status.cloudstrike.example/incidents/88421?ref=email&x=1 please",
     {"key": "url", "value": "https://status.cloudstrike.example/incidents/88421?ref=email&x=1"}),
    ("URL trailing dot", "go to http://example.com/page.",
     {"key": "url", "value": "http://example.com/page"}),
]

# (name, text, key-that-must-be-absent-or-not-contain-the-lookalike)
SHOULD_SKIP = [
    ("partial IP (2 octets)",      "Rack unit 192.168 here",              "ip_address"),
    ("octet out of range (999)",   "bogus 999.1.1.1 addr",                "ip_address"),
    ("octet out of range (256)",   "bogus 256.256.256.256 addr",          "ip_address"),
    ("five octets",                "weird 1.2.3.4.5 sequence",            "ip_address"),
    ("version-ish number",         "app version 10.4.2 released",         "ip_address"),
    ("bare domain, no scheme",     "visit example.com for details",       "url"),
]

# ============================================================
#  EVALUATION
# ============================================================


def _values(result, key):
    return {v for v in result.get(key, [])}


def evaluate_match(result, exp):
    key, val = exp["key"], exp["value"]
    if key not in result:
        return False, f"key {key!r} absent (got {sorted(result)})"
    if val not in _values(result, key):
        return False, f"missing {val!r} in {key} (got {sorted(_values(result, key))})"
    return True, "ok"


def evaluate_skip(result, key, text):
    # Fail if any detected value under `key` appears verbatim in the look-alike text.
    leaked = _values(result, key)
    if leaked:
        return False, f"leaked {key}: {sorted(leaked)}"
    return True, "ok"


def run_suite():
    rows, failures = [], []
    for name, text, exp in SHOULD_MATCH:
        ok, reason = evaluate_match(detect_pii(text), exp)
        rows.append(("MATCH", name, ok, reason))
        if not ok:
            failures.append((name, reason))
    for name, text, key in SHOULD_SKIP:
        ok, reason = evaluate_skip(detect_pii(text), key, text)
        rows.append(("SKIP", name, ok, reason))
        if not ok:
            failures.append((name, reason))

    print(f"{'GRP':6} {'CASE':28} {'RESULT':7}")
    print("-" * 70)
    for grp, name, ok, reason in rows:
        line = f"{grp:6} {name:28} {'PASS' if ok else 'FAIL':7}"
        if not ok:
            line += f"  <-- {reason}"
        print(line)
    passed = sum(1 for r in rows if r[2])
    print("-" * 70)
    print(f"SUMMARY: {passed}/{len(rows)} passed, {len(rows) - passed} failed")
    return passed, len(rows) - passed


def test_should_match():
    for name, text, exp in SHOULD_MATCH:
        ok, reason = evaluate_match(detect_pii(text), exp)
        assert ok, f"{name}: {reason}"


def test_should_skip():
    for name, text, key in SHOULD_SKIP:
        ok, reason = evaluate_skip(detect_pii(text), key, text)
        assert ok, f"{name}: {reason}"


if __name__ == "__main__":
    _, failed = run_suite()
    sys.exit(1 if failed else 0)
