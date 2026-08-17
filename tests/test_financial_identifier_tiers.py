#!/usr/bin/env python3
"""Regression coverage for context-tiered SIN and credit-card detection."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors.detectors import detect_pii
from detectors.hybrid_detector import detect_pii_hybrid
from scoring import score_file


CASES = [
    (
        "SIN checksum + context stays HIGH",
        "Employee SIN: 757 036 793 is on file",
        "identifier.financial.sin",
        "757 036 793",
        "HIGH",
        0.6375,
    ),
    (
        "SIN checksum without context is MEDIUM",
        "Reference 757 036 793 was received",
        "identifier.financial_unverified.sin",
        "757 036 793",
        "MEDIUM",
        0.55,
    ),
    (
        "card checksum + context stays HIGH",
        "Visa 4111 1111 1111 1111 expires next year",
        "identifier.financial.credit_card",
        "4111 1111 1111 1111",
        "HIGH",
        0.95,
    ),
    (
        "card checksum without context is MEDIUM",
        "Reference 4111 1111 1111 1111 was received",
        "identifier.financial_unverified.credit_card",
        "4111 1111 1111 1111",
        "MEDIUM",
        0.55,
    ),
    (
        "Enron labelled Visa remains HIGH",
        "199.99+ $18 shipping = 217.99\n"
        "Visa 4128 0033 2341 1978 exp 8/02\n"
        "shipping and billing address",
        "identifier.financial.credit_card",
        "4128 0033 2341 1978",
        "HIGH",
        0.95,
    ),
    (
        "Enron ticketless card remains visible at MEDIUM",
        "TICKETLESS RESERVATION\n"
        "FARE: 187.50 CHARGED TO: 4128003300796474/0103\n"
        "ALL FARES ARE SUBJECT TO CHANGE",
        "identifier.financial_unverified.credit_card",
        "4128003300796474",
        "MEDIUM",
        0.55,
    ),
]


def _find(matches, category, value):
    return next(
        (item for item in matches.get(category, []) if item.get("value") == value),
        None,
    )


def test_tiered_cases():
    for name, text, category, value, risk, confidence in CASES:
        matches = detect_pii_hybrid(text, run_ner=False)
        finding = _find(matches, category, value)
        assert finding is not None, f"{name}: missing {category} {value}"
        assert finding["risk_level"] == risk, (
            f"{name}: risk {finding['risk_level']} != {risk}"
        )
        assert finding["confidence"] == confidence, (
            f"{name}: confidence {finding['confidence']} != {confidence}"
        )
        score = score_file(matches)
        if risk == "HIGH":
            assert 70 <= score <= 100, f"{name}: score {score} not HIGH"
        else:
            assert 30 <= score <= 69, f"{name}: score {score} not MEDIUM"


def test_dot_separated_sin_does_not_match():
    raw = detect_pii("Internal route fragment 192.168.110 was logged")
    all_sins = raw.get("sin_9digits", []) + raw.get("sin_unverified", [])
    assert "192.168.110" not in all_sins


def test_sin_grouping_is_exact():
    valid = ("132677360", "132-677-360", "132 677 360")
    invalid = (
        "123456-789",       # photographed Alberta licence
        "132677-360",       # Alberta-style 6-3 grouping
        "132-677360",       # 3-6 grouping
        "132-677 360",      # mixed separators
        "132 677-360",      # mixed separators
        "132\t677\t360",    # arbitrary whitespace is not a printed layout
    )

    for value in valid:
        raw = detect_pii(f"Reference {value} was received")
        all_sins = raw.get("sin_9digits", []) + raw.get("sin_unverified", [])
        assert value in all_sins, f"valid SIN layout was rejected: {value}"

    for value in invalid:
        raw = detect_pii(f"SIN: {value}")
        all_sins = raw.get("sin_9digits", []) + raw.get("sin_unverified", [])
        assert value not in all_sins, f"invalid SIN layout was accepted: {value}"


def run_suite():
    checks = []
    for case in CASES:
        name, text, category, value, risk, confidence = case
        try:
            matches = detect_pii_hybrid(text, run_ner=False)
            finding = _find(matches, category, value)
            ok = (
                finding is not None
                and finding["risk_level"] == risk
                and finding["confidence"] == confidence
            )
            checks.append((name, ok))
        except Exception:
            checks.append((name, False))

    raw = detect_pii("Internal route fragment 192.168.110 was logged")
    all_sins = raw.get("sin_9digits", []) + raw.get("sin_unverified", [])
    checks.append(("dot-separated SIN rejected", "192.168.110" not in all_sins))

    valid_layouts = ("132677360", "132-677-360", "132 677 360")
    invalid_layouts = (
        "123456-789",
        "132677-360",
        "132-677360",
        "132-677 360",
        "132 677-360",
        "132\t677\t360",
    )
    valid_ok = all(
        value in (
            detect_pii(f"Reference {value} was received").get("sin_9digits", [])
            + detect_pii(f"Reference {value} was received").get("sin_unverified", [])
        )
        for value in valid_layouts
    )
    invalid_ok = all(
        value not in (
            detect_pii(f"SIN: {value}").get("sin_9digits", [])
            + detect_pii(f"SIN: {value}").get("sin_unverified", [])
        )
        for value in invalid_layouts
    )
    checks.append(("SIN grouping accepts only 3-3-3 or compact", valid_ok and invalid_ok))

    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL':4}  {name}")
    failed = sum(not ok for _, ok in checks)
    print(f"SUMMARY: {len(checks) - failed}/{len(checks)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if run_suite() else 0)
