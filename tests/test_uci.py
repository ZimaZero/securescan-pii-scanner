#!/usr/bin/env python3
"""Standalone and pytest-compatible tests for the IRCC UCI detector."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors.hybrid_detector import detect_pii_hybrid
from detectors.uci_detector import detect_uci


MATCH_CASES = (
    ("compact 8", "UCI: 12345678", "12345678"),
    ("display 4-4", "Client ID: 1234-5678", "1234-5678"),
    (
        "compact 10",
        "Client identification number: 1234567890",
        "1234567890",
    ),
    ("display 2-4-4", "UCI 12-3456-7890", "12-3456-7890"),
)

SKIP_CASES = (
    ("bare compact 8", "Reference 12345678"),
    ("bare display 4-4", "Reference 1234-5678"),
    ("bare compact 10", "Reference 1234567890"),
    ("bare display 2-4-4", "Reference 12-3456-7890"),
    ("too many digits", "UCI: 12345678901"),
    ("too few digits", "UCI: 1234567"),
)


def test_public_formats():
    for name, text, expected in MATCH_CASES:
        result = detect_uci(text)
        assert result == {"uci": [(expected, 0.60)]}, (name, result)


def test_context_absent_and_invalid_lengths():
    for name, text in SKIP_CASES:
        assert detect_uci(text) == {}, (name, detect_uci(text))


def test_hybrid_taxonomy_tier_and_source():
    result = detect_pii_hybrid("IRCC UCI: 12-3456-7890", run_ner=False, verify=False)
    findings = result.get("identifier.government.uci", [])
    assert len(findings) == 1, findings
    finding = findings[0]
    assert finding["value"] == "12-3456-7890", finding
    assert finding["confidence"] == 0.60, finding
    assert finding["risk_level"] == "HIGH", finding
    assert finding["source"] == "uci", finding


def run_suite():
    tests = (
        test_public_formats,
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
