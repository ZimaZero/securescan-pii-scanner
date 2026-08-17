#!/usr/bin/env python3
# tests/test_health_cards.py
"""
Reusable test suite for detectors/health_card_detector.py.

Groups:
  - SHOULD_MATCH: input text + expected (type / value).
  - SHOULD_SKIP:  input text that MUST return nothing ({}).

Run directly for a pass/fail table + summary:
    docker compose run --rm securescan-cpu python tests/test_health_cards.py

Also pytest-compatible (test_should_match / test_should_skip).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors.health_card_detector import (
    detect_health_cards, TIER1_CONFIDENCE, TIER2_CONFIDENCE, TIER3_CONFIDENCE,
)
from detectors.hybrid_detector import normalize_health_card_results, apply_taxonomy
from scoring import score_file

# Verified OHIP numbers.
ON_VALID = ["9876543217", "5322369835", "7089771195",
            "8108876957", "4395667779", "6983806917"]
ON_INVALID = ["2790412845", "5762696912"]

# Each MATCH case: (name, text, expectation) where expectation has optional
# keys: type, value, min_findings, exact_findings, types, confidence (checked against the
# confidence of the matching (type, value) pair).
#
# Health-card tier restructure (checksum+context outranks checksum-only):
# these bare/no-keyword checksum cases used to be Tier 1 ("health_card_on" /
# "health_card_bc" at the old TIER1_CONFIDENCE=0.90). They are now demoted to
# Tier 2 ("*_unverified" type, TIER2_CONFIDENCE=0.55) because a passing OHIP/
# BC checksum alone can't rule out a foreign document's reference number
# coincidentally matching the algorithm (tests/external_octopii evaluation,
# benchmark case 2 — see health_card_detector.py's rationale comment).
SHOULD_MATCH = [
    # --- Tier 2 (demoted): ON checksum, NO keyword (bare numbers) ---
    *[(f"ON valid {n}", n,
       {"type": "health_card_on_unverified", "value": n, "confidence": TIER2_CONFIDENCE})
      for n in ON_VALID],

    # --- Tier 2 (demoted): BC checksum, NO keyword ---
    ("BC valid (bare)", "9698658215",
     {"type": "health_card_bc_unverified", "value": "9698658215", "confidence": TIER2_CONFIDENCE}),

    # --- Tier 1 (new top): BC checksum + keyword ("phn") in context ---
    ("BC valid in context", "Patient PHN 9698658215 on file",
     {"type": "health_card_bc", "value": "9698658215", "confidence": TIER1_CONFIDENCE}),

    # --- Tier 1 (new top): ON checksum + "OHIP" keyword in context ---
    ("ON valid + OHIP nearby", "OHIP number: 9876543217",
     {"type": "health_card_on", "value": "9876543217", "confidence": TIER1_CONFIDENCE}),
    ("ON valid + one-letter version", "OHIP number: 9876543217 X",
     {"type": "health_card_on", "value": "9876543217 X",
      "confidence": TIER1_CONFIDENCE, "exact_findings": 1}),
    # Same number, bare (no keyword) -> Tier 2, not Tier 1.
    ("ON valid, bare (no OHIP)", "9876543217",
     {"type": "health_card_on_unverified", "value": "9876543217", "confidence": TIER2_CONFIDENCE}),

    # --- Tier 3: format + context, no checksum (unchanged) ---
    ("AB compact (province name)", "Alberta health card: 123456789",
     {"type": "health_card_ab", "value": "123456789", "confidence": TIER3_CONFIDENCE}),
    ("AB printed (hyphenated)", "Alberta health card: 12345-6789",
     {"type": "health_card_ab", "value": "123456789",
      "confidence": TIER3_CONFIDENCE, "exact_findings": 1}),
    ("SK (province name)", "Saskatchewan health number 987654321",
     {"type": "health_card_sk", "value": "987654321", "confidence": TIER3_CONFIDENCE}),
    ("PEI (alias)", "PEI health number 12345678",
     {"type": "health_card_pe", "value": "12345678", "confidence": TIER3_CONFIDENCE}),
    ("NWT letter + seven digits", "Northwest Territories health card X1234567",
     {"type": "health_card_nt", "value": "X1234567",
      "confidence": TIER3_CONFIDENCE, "exact_findings": 1}),
    ("NS (10-digit, name)", "Nova Scotia health card 1234567890",
     {"type": "health_card_ns", "value": "1234567890", "confidence": TIER3_CONFIDENCE}),
    ("NL (12-digit, name)", "Newfoundland health number 123456789012",
     {"type": "health_card_nl", "value": "123456789012", "confidence": TIER3_CONFIDENCE}),
    ("QC RAMQ compact", "RAMQ number ABCD12345678",
     {"type": "health_card_qc", "value": "ABCD12345678", "confidence": TIER3_CONFIDENCE}),
    ("QC RAMQ printed", "RAMQ number ABCD 1234 5678",
     {"type": "health_card_qc", "value": "ABCD12345678",
      "confidence": TIER3_CONFIDENCE, "exact_findings": 1}),
    ("Generic keyword only", "health card: 123456789",
     {"type": "health_card_ca", "value": "123456789", "confidence": TIER3_CONFIDENCE}),

    # --- Octopii benchmark case 2: Aadhaar "Ref:" number passes OHIP checksum
    # but has no health keyword nearby -> Tier 2 (demoted), not Tier 1.
    # Real context from tests/external_octopii_docs/EVALUATION.md finding #2 /
    # tests/ollama_benchmark/cases.json id 2 (dummy-aadhaar.png).
    ("Aadhaar Ref: number (OHIP-checksum collision)",
     "AADHAAR\nGovernment of India - Unique Identification Authority\n"
     "Enrollment No.: 1234/56789/01234\nRef: 1145002075\nName: Sree Marrang\n"
     "DOB: 01/01/1990\nAadhaar is proof of identity, not of citizenship.\n"
     "help@uidai.gov.in",
     {"type": "health_card_on_unverified", "value": "1145002075", "confidence": TIER2_CONFIDENCE}),

    # Province-named checksum failures remain visible at Tier 2.
    ("BC MOD-11 result 11 is province-unverified",
     "British Columbia PHN health number: 9000000000",
     {"type": "health_card_bc_unverified", "value": "9000000000",
      "confidence": TIER2_CONFIDENCE}),
    ("BC named checksum mismatch is province-unverified",
     "British Columbia health card number: 9123456781",
     {"type": "health_card_bc_unverified", "value": "9123456781",
      "confidence": TIER2_CONFIDENCE}),
    ("ON named checksum mismatch is province-unverified",
     "Ontario health card number: 1234567890",
     {"type": "health_card_on_unverified", "value": "1234567890",
      "confidence": TIER2_CONFIDENCE}),
    ("Unnamed checksum mismatch stays generic Canadian",
     "Health card number: 9123456781",
     {"type": "health_card_ca", "value": "9123456781",
      "confidence": TIER3_CONFIDENCE}),
    ("Province context does not cross an intervening health value",
     "Ontario Health Card: 9876543217\nBC Health Card (PHN): 9123456780",
     {"type": "health_card_ca", "value": "9123456780",
      "confidence": TIER3_CONFIDENCE,
      "absent_types": ["health_card_on_unverified"]}),
]

SHOULD_SKIP = [
    # --- Tier 1 checksum failures (bare) ---
    *[(f"ON invalid {n}", n) for n in ON_INVALID],
    ("ON zero-leading schema invalid", "Ontario OHIP health card: 0000000000"),
    ("BC invalid (digit changed)", "9698648215"),
    ("BC invalid (first not 9)", "5736504210"),

    # --- Tier 2 context guard (SIN-collision protection) ---
    ("Bare 9-digit, no keyword", "123456789"),
    ("Bare AB printed form, no keyword", "12345-6789"),
    ("9-digit, non-health context", "Employee SIN 193456787 on record"),
    ("Bare 8-digit, no keyword", "12345678"),
    ("Bare NWT format, no keyword", "X1234567"),
    ("Bare QC printed form, no keyword", "ABCD 1234 5678"),
    ("Random 10-digit, no keyword", "1234567890"),
    ("Province format, no keyword", "Reference number 987654321 follows"),
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
    if "absent_types" in exp:
        unexpected = [t for t in exp["absent_types"] if t in result]
        if unexpected:
            return False, f"unexpected types {unexpected}"
    if "min_findings" in exp and _total(result) < exp["min_findings"]:
        return False, f"only {_total(result)} findings (< {exp['min_findings']})"
    if "exact_findings" in exp and _total(result) != exp["exact_findings"]:
        return False, f"{_total(result)} findings (!= {exp['exact_findings']})"
    if "confidence" in exp:
        stype, val = exp.get("type"), exp.get("value")
        if stype is None or val is None:
            return False, "confidence check requires both 'type' and 'value' in expectation"
        found = dict(result.get(stype, []))
        if val not in found:
            return False, f"missing value {val!r} in type {stype!r} for confidence check"
        actual_conf = found[val]
        if actual_conf != exp["confidence"]:
            return False, f"confidence {actual_conf} != expected {exp['confidence']}"
    return True, "ok"


# ============================================================
#  SCORING CHECKS
# ============================================================
# The tier restructure's whole point is that a file whose ONLY finding is a
# bare (no-context) checksum hit must score MEDIUM, not HIGH. Runs the real
# taxonomy/risk pipeline (hybrid_detector.normalize/apply_taxonomy + scoring
# .score_file) over a single detector's output — isolated from the other 6
# detection layers — so this is a check on this detector + taxonomy, not a
# full-pipeline integration test.

SCORING_CHECKS = [
    ("Bare ON checksum-only -> MEDIUM band, not HIGH",
     "9876543217", "MEDIUM"),
    ("ON checksum + OHIP context -> HIGH band",
     "OHIP number: 9876543217", "HIGH"),
    ("Aadhaar Ref: number (OHIP-checksum collision, no health keyword) -> MEDIUM band",
     "AADHAAR\nGovernment of India - Unique Identification Authority\n"
     "Enrollment No.: 1234/56789/01234\nRef: 1145002075\nName: Sree Marrang\n"
     "DOB: 01/01/1990\nAadhaar is proof of identity, not of citizenship.\n"
     "help@uidai.gov.in", "MEDIUM"),
]

BAND_RANGES = {"HIGH": (70, 100), "MEDIUM": (30, 69), "LOW": (1, 29)}


def score_band(text):
    raw = detect_health_cards(text)
    taxonomy = apply_taxonomy(normalize_health_card_results(raw))
    score = score_file(taxonomy)
    for band, (lo, hi) in BAND_RANGES.items():
        if lo <= score <= hi:
            return score, band
    return score, "NONE"


def evaluate_scoring(text, expected_band):
    score, band = score_band(text)
    if band != expected_band:
        return False, f"score={score} band={band} != expected {expected_band}"
    return True, f"score={score} band={band}"


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
        result = detect_health_cards(text)
        ok, reason = evaluate_match(result, exp)
        rows.append(("MATCH", name, _summarize(result), ok, reason))
        if not ok:
            failures.append((name, reason, result))

    for name, text in SHOULD_SKIP:
        result = detect_health_cards(text)
        ok, reason = evaluate_skip(result)
        rows.append(("SKIP", name, _summarize(result), ok, reason))
        if not ok:
            failures.append((name, reason, result))

    for name, text, expected_band in SCORING_CHECKS:
        ok, reason = evaluate_scoring(text, expected_band)
        rows.append(("SCORE", name, reason, ok, reason))
        if not ok:
            failures.append((name, reason, None))

    print(f"{'GRP':5} {'CASE':30} {'RESULT':7} {'ACTUAL':28}")
    print("-" * 90)
    for grp, name, actual, ok, reason in rows:
        status = "PASS" if ok else "FAIL"
        line = f"{grp:5} {name:30} {status:7} {actual:28}"
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
        ok, reason = evaluate_match(detect_health_cards(text), exp)
        assert ok, f"{name}: {reason}"


def test_should_skip():
    for name, text in SHOULD_SKIP:
        ok, reason = evaluate_skip(detect_health_cards(text))
        assert ok, f"{name}: {reason}"


def test_scoring_bands():
    for name, text, expected_band in SCORING_CHECKS:
        ok, reason = evaluate_scoring(text, expected_band)
        assert ok, f"{name}: {reason}"


if __name__ == "__main__":
    _, failed, _ = run_suite()
    sys.exit(1 if failed else 0)
