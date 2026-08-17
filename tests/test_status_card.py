#!/usr/bin/env python3
"""Standalone and pytest-compatible tests for status registration numbers."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors.hybrid_detector import detect_pii_hybrid
from detectors.status_card_detector import detect_status_card


CONTEXT_CASES = (
    "Certificate of Indian Status: 1234567890",
    "Secure Certificate of Indian Status 1234567890",
    "Status card number: 1234567890",
    "SCIS: 1234567890",
    "Registration number: 1234567890",
    "Registry number: 1234567890",
    "Indian Register entry 1234567890",
)

SKIP_CASES = (
    "Reference number: 1234567890",
    "1234567890",
    "Status card: 123456789",
    "Status card: 12345678901",
)


def test_public_format_with_sourced_context():
    for text in CONTEXT_CASES:
        result = detect_status_card(text)
        assert result == {
            "status_card_registration": [("1234567890", 0.60)]
        }, (text, result)


def test_context_absent_and_invalid_lengths():
    for text in SKIP_CASES:
        assert detect_status_card(text) == {}, (text, detect_status_card(text))


def test_hybrid_taxonomy_tier_and_source():
    result = detect_pii_hybrid(
        "Certificate of Indian Status registration number: 1234567890",
        run_ner=False,
        verify=False,
    )
    findings = result.get(
        "identifier.government.status_card_registration", []
    )
    assert len(findings) == 1, findings
    finding = findings[0]
    assert finding["value"] == "1234567890", finding
    assert finding["confidence"] == 0.60, finding
    assert finding["risk_level"] == "HIGH", finding
    assert finding["source"] == "status_card", finding


def run_suite():
    tests = (
        test_public_format_with_sourced_context,
        test_context_absent_and_invalid_lengths,
        test_hybrid_taxonomy_tier_and_source,
    )
    failures = []
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures.append((test.__name__, exc))
            print(f"FAIL {test.__name__}: {exc}")
    print(f"SUMMARY: {len(tests) - len(failures)}/{len(tests)} passed, "
          f"{len(failures)} failed")
    return len(failures)


if __name__ == "__main__":
    sys.exit(1 if run_suite() else 0)
