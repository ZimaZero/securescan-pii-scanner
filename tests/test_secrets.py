#!/usr/bin/env python3
# tests/test_secrets.py
"""
Reusable test suite for detectors/secrets_detector.py.

Two labeled groups:
  - SHOULD_MATCH: input text plus expected detect_secrets() findings.
  - SHOULD_SKIP:  input text that MUST return nothing ({}).

Run directly for a pass/fail table + summary:
    docker compose run --rm securescan-cpu python tests/test_secrets.py

Also importable / pytest-compatible (test_should_match / test_should_skip).
"""

import os
import sys

# Allow running as a plain script from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors.secrets_detector import detect_secrets

# ============================================================
#  TEST FIXTURES
# ============================================================

_PEM = """\
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA1234567890abcdefGHIJKLmnopqrstuvwxyz
-----END RSA PRIVATE KEY-----"""

_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0"
    ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)

_ENV_BLOCK = """\
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
GITHUB_TOKEN=ghp_0123456789abcdef0123456789abcdef0123
STRIPE_KEY=sk_live_0123456789abcdefABCDEFXY
DB_PASSWORD=Xy9Zm2Qw7Lp0Rt5Yb8Nc4Vd6HjF1Kb4A
"""

# A 1x1 PNG as base64 — long and random-ish, but NOT a credential and has no
# nearby keyword/aws/secret, so it must not trip any method.
_B64_IMAGE = (
    "img = iVBORw0KGgoAAAANSUhEUgAAAAUAAAAFCAYAAACNbyblAAAAHElEQVQ"
    "I12P4z9DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg"
)

# Each case: (name, text, expectation-dict)
# Expectation keys (all optional): type, value, min_findings, types
SHOULD_MATCH = [
    ("AWS access key",
     "deploy with AKIAIOSFODNN7EXAMPLE today",
     {"type": "aws_access_key", "value": "AKIAIOSFODNN7EXAMPLE"}),

    ("AWS secret (proximity)",
     "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
     {"type": "aws_secret_key", "value": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"}),

    ("GitHub token",
     "token ghp_0123456789abcdef0123456789abcdef0123",
     {"type": "github_token"}),

    ("Google API key",
     "key=AIzaabcdefghijklmnopqrstuvwxyz012345678",
     {"type": "google_api_key"}),

    ("Stripe live key",
     "charge with sk_live_0123456789abcdefABCDEFXY now",
     {"type": "stripe_key", "value": "sk_live_0123456789abcdefABCDEFXY"}),

    ("Stripe test key",
     "test pk_test_abcdefABCDEF0123456789XYZW",
     {"type": "stripe_key", "value": "pk_test_abcdefABCDEF0123456789XYZW"}),

    ("Slack token",
     "slack xoxb-2401024896156-2402181882626-aBcDeFgHiJkLmNoPqRsTuVwX",
     {"type": "slack_token"}),

    ("PEM private key",
     _PEM,
     {"type": "private_key"}),

    ("JWT",
     "Authorization: Bearer " + _JWT,
     {"type": "jwt"}),

    ("Unknown vendor (SignNow)",
     'SIGNNOW_API_KEY = "a8Ff3K2m9Xz7Qw1Lp0Rt5Yb8Nc4Vd6Hj"',
     {"type": "api_key", "value": "a8Ff3K2m9Xz7Qw1Lp0Rt5Yb8Nc4Vd6Hj"}),

    ("KnowBe4-style token",
     "knowbe4_token: Kb4Xy9Zm2Qw7Lp0Rt5Yb8Nc4Vd6Hj",
     {"type": "access_token", "value": "Kb4Xy9Zm2Qw7Lp0Rt5Yb8Nc4Vd6Hj"}),

    ("Password assignment",
     "login password: S3cret!",
     {"type": "password", "value": "S3cret!"}),

    ("Connection string password",
     "DATABASE_URL = postgres://user:hunter2@host/db",
     {"value": "hunter2"}),

    (".env block (multiple)",
     _ENV_BLOCK,
     {"min_findings": 5,
      "types": ["aws_access_key", "github_token", "stripe_key", "password"]}),
]

SHOULD_SKIP = [
    ("Password policy text", "password requirements: 8 characters minimum"),
    ("UUID", "request id 550e8400-e29b-41d4-a716-446655440000"),
    ("Git commit hash", "git commit 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b"),
    ("English sentence", "The quick brown fox jumps over the lazy dog."),
    ("pass = normal word", "pass = passenger"),
    ("passing_grade var", "passing_grade = 85"),
    ("base64 image fragment", _B64_IMAGE),
]

# ============================================================
#  EVALUATION
# ============================================================


def _total(result):
    return sum(len(v) for v in result.values())


def _all_values(result):
    return {v for items in result.values() for v, _ in items}


def evaluate_match(result, exp):
    """Return (passed, reason) for a SHOULD_MATCH case."""
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
    """Return (passed, reason) for a SHOULD_SKIP case."""
    if result:
        return False, f"leaked: {result}"
    return True, "ok"


def _summarize(result):
    if not result:
        return "{}"
    return ", ".join(f"{t}={len(v)}" for t, v in sorted(result.items()))


def run_suite():
    """Run all cases, print a table, return (passed, failed, failures)."""
    rows = []
    failures = []

    for name, text, exp in SHOULD_MATCH:
        result = detect_secrets(text)
        ok, reason = evaluate_match(result, exp)
        rows.append(("MATCH", name, _summarize(result), ok, reason))
        if not ok:
            failures.append((name, reason, result))

    for name, text in SHOULD_SKIP:
        result = detect_secrets(text)
        ok, reason = evaluate_skip(result)
        rows.append(("SKIP", name, _summarize(result), ok, reason))
        if not ok:
            failures.append((name, reason, result))

    # ---- table ----
    print(f"{'GRP':5} {'CASE':28} {'RESULT':9} {'ACTUAL':40}")
    print("-" * 90)
    for grp, name, actual, ok, reason in rows:
        status = "PASS" if ok else "FAIL"
        line = f"{grp:5} {name:28} {status:9} {actual:40}"
        if not ok:
            line += f"  <-- {reason}"
        print(line)

    passed = sum(1 for r in rows if r[3])
    failed = len(rows) - passed
    print("-" * 90)
    print(f"SUMMARY: {passed}/{len(rows)} passed, {failed} failed")
    return passed, failed, failures


# ============================================================
#  PYTEST ENTRY POINTS
# ============================================================


def test_should_match():
    for name, text, exp in SHOULD_MATCH:
        ok, reason = evaluate_match(detect_secrets(text), exp)
        assert ok, f"{name}: {reason}"


def test_should_skip():
    for name, text in SHOULD_SKIP:
        ok, reason = evaluate_skip(detect_secrets(text))
        assert ok, f"{name}: {reason}"


if __name__ == "__main__":
    _, failed, _ = run_suite()
    sys.exit(1 if failed else 0)
