#!/usr/bin/env python3
# tests/test_scoring.py
"""
Test suite for scoring.py — banded risk scoring anchored to the worst finding.

The rule under test:
  - any HIGH finding          → score 70–100
  - MEDIUM at worst           → score 30–69 (volume can NEVER push it to 70)
  - only LOW findings         → score 1–29
  - no findings               → 0
Within a band, more findings / higher confidence move the score up, capped
at the band ceiling.

Run directly for a pass/fail table + summary:
    docker compose run --rm securescan-cpu python tests/test_scoring.py

Also importable / pytest-compatible.
"""

import os
import sys

# Allow running as a plain script from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring import score_file

# ============================================================
#  FIXTURE HELPERS — build hybrid-detector-shaped matches
# ============================================================


def det(value, risk, confidence=0.95, category="x"):
    return {
        "value": value,
        "confidence": confidence,
        "risk_level": risk,
        "category": category,
    }


def credit_card(n=1):
    return {
        "identifier.financial.credit_card": [
            det(f"4111 1111 1111 111{i}", "HIGH") for i in range(n)
        ]
    }


def emails(n=1):
    return {"contact.email": [det(f"user{i}@example.com", "MEDIUM") for i in range(n)]}


def phones(n=1):
    return {"contact.phone": [det(f"613-555-01{i:02d}", "MEDIUM") for i in range(n)]}


def dobs(n=1):
    return {
        "identifier.personal.dob": [det(f"1990-01-{i + 1:02d}", "MEDIUM") for i in range(n)]
    }


def persons(n=1, confidence=0.8):
    return {
        "entity.person": [det(f"Person {i}", "LOW", confidence) for i in range(n)]
    }


def low_mixed():
    return {
        "entity.date": [det("March 3rd", "LOW", 0.7)],
        "technical.ip_address": [det("192.168.1.10", "LOW", 0.95)],
        "technical.url": [det("https://example.com", "LOW", 0.95)],
    }


def bare_dates(n=1, confidence=0.95):
    return {
        "entity.date": [
            det(f"2026-07-{(i % 28) + 1:02d}", "LOW", confidence) for i in range(n)
        ]
    }


def merge(*parts):
    out = {}
    for p in parts:
        for k, v in p.items():
            out.setdefault(k, []).extend(v)
    return out


# ============================================================
#  LABELED CASES: (name, matches, check-dict)
#  Check keys: min, max (inclusive score bounds)
# ============================================================

CASES = [
    ("one credit card only → HIGH band (70+)",
     credit_card(1),
     {"min": 70, "max": 100}),

    ("credit card + email + phone → higher in HIGH band",
     merge(credit_card(1), emails(1), phones(1)),
     {"min": 70, "max": 100, "gt_case": credit_card(1)}),

    ("ONLY email + phone → MEDIUM band, < 70",
     merge(emails(1), phones(1)),
     {"min": 30, "max": 69}),

    ("email + phone + DOB + several LOW entities → still < 70 (anti-stacking)",
     merge(emails(1), phones(1), dobs(1), persons(4), low_mixed()),
     {"min": 30, "max": 69}),

    ("massive stack of MEDIUMs alone can never reach 70",
     merge(emails(50), phones(50), dobs(50)),
     {"min": 30, "max": 69}),

    ("only LOW entities → < 30",
     merge(persons(2), low_mixed()),
     {"min": 1, "max": 29}),

    ("a couple of LOW entities → roughly 10–20",
     persons(2),
     {"min": 5, "max": 25}),

    ("empty findings → 0",
     {},
     {"min": 0, "max": 0}),

    ("metadata-only matches → 0",
     {"_metadata": {"layers": ["regex"]}},
     {"min": 0, "max": 0}),

    ("single email alone → ~35–40",
     emails(1),
     {"min": 33, "max": 42}),

    ("email + phone + DOB → ~50–60",
     merge(emails(1), phones(1), dobs(1)),
     {"min": 48, "max": 62}),

    ("single HIGH finding → ~75",
     credit_card(1),
     {"min": 72, "max": 78}),

    ("HIGH + several mediums → 85–95",
     merge(credit_card(1), emails(3), phones(2)),
     {"min": 80, "max": 96}),

    ("multiple HIGHs → up to 100, never above",
     credit_card(10),
     {"min": 95, "max": 100}),

    ("1 bare date, no context → ~0",
     bare_dates(1),
     {"min": 0, "max": 2}),

    ("50 bare dates, no context → still ~0-5, LOW band",
     bare_dates(50),
     {"min": 0, "max": 5}),

    ("DOB via keyword context → unchanged MEDIUM contribution",
     dobs(1),
     {"min": 33, "max": 42}),
]


# ============================================================
#  PYTEST-COMPATIBLE TESTS
# ============================================================


def test_band_bounds():
    """Every labeled case scores within its declared band bounds."""
    for name, matches, check in CASES:
        score = score_file(matches)
        assert check["min"] <= score <= check["max"], (
            f"{name}: score={score}, expected {check['min']}–{check['max']}"
        )


def test_volume_moves_score_within_band():
    """More findings raise the score, but never past the band ceiling."""
    assert score_file(emails(1)) < score_file(merge(emails(1), phones(1)))
    assert score_file(credit_card(1)) < score_file(
        merge(credit_card(1), emails(2), phones(2))
    )
    assert score_file(persons(1)) < score_file(persons(3))


def test_worst_finding_sets_the_band():
    """Adding one HIGH to a MEDIUM pile jumps into the HIGH band."""
    mediums = merge(emails(3), phones(2))
    assert score_file(mediums) < 70
    assert score_file(merge(mediums, credit_card(1))) >= 70


def test_medium_cap_is_hard():
    """No volume of MEDIUM/LOW findings ever crosses 69."""
    huge = merge(emails(200), phones(200), dobs(200), persons(200))
    assert score_file(huge) == 69


def test_low_cap_is_hard():
    """No volume of LOW findings ever crosses 29."""
    assert score_file(persons(500)) == 29


def test_bare_dates_never_exceed_low():
    """A file whose ONLY findings are bare entity.date, at any volume, stays
    deep in the LOW band — never near the 29 ceiling, let alone into MEDIUM."""
    assert score_file(bare_dates(1)) <= 2
    assert score_file(bare_dates(1000)) <= 5


def test_bare_dates_do_not_inflate_higher_bands():
    """Bare dates piled onto a MEDIUM/HIGH file barely move the score —
    unlike a real MEDIUM/HIGH finding, they must not push toward the ceiling."""
    base = score_file(emails(1))
    with_dates = score_file(merge(emails(1), bare_dates(200)))
    assert with_dates - base <= 5


def test_invalid_inputs_score_zero():
    assert score_file(None) == 0
    assert score_file("not a dict") == 0
    assert score_file({"contact.email": []}) == 0


# ============================================================
#  SCRIPT MODE — pass/fail table
# ============================================================

if __name__ == "__main__":
    failures = 0
    print(f"{'CASE':<62} {'SCORE':>5}  RESULT")
    print("-" * 78)
    for name, matches, check in CASES:
        score = score_file(matches)
        ok = check["min"] <= score <= check["max"]
        if "gt_case" in check:
            ok = ok and score > score_file(check["gt_case"])
        failures += 0 if ok else 1
        print(f"{name:<62} {score:>5}  {'PASS' if ok else 'FAIL'}")
    for fn in (
        test_band_bounds,
        test_volume_moves_score_within_band,
        test_worst_finding_sets_the_band,
        test_medium_cap_is_hard,
        test_low_cap_is_hard,
        test_bare_dates_never_exceed_low,
        test_bare_dates_do_not_inflate_higher_bands,
        test_invalid_inputs_score_zero,
    ):
        try:
            fn()
            print(f"{fn.__name__:<62} {'':>5}  PASS")
        except AssertionError as e:
            failures += 1
            print(f"{fn.__name__:<62} {'':>5}  FAIL  {e}")
    print("-" * 78)
    print("ALL PASS" if failures == 0 else f"{failures} FAILURE(S)")
    sys.exit(1 if failures else 0)
