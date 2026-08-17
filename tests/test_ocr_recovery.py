#!/usr/bin/env python3
"""Tests for bounded, checksum-only deterministic OCR recovery."""

import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors.hybrid_detector import detect_pii_hybrid
from detectors.mrz_detector import compute_check_digit
from detectors import ocr_recovery_detector
from detectors.ocr_recovery_detector import (
    MAX_VARIANTS,
    detect_ocr_recovery,
    recover_unique_variant,
)
from report_generator import generate_markdown
from report_html import generate_html
from report_json import generate_json


def test_successful_recovery_per_checksummed_type():
    cases = (
        (
            "Social Insurance Number: 3185O7522",
            "reconstructed_sin",
            "318507522",
        ),
        (
            "Social Insurance Number: 3I8507522",
            "reconstructed_sin",
            "318507522",
        ),
        (
            "OHIP health card: B327932763",
            "reconstructed_health_card_on",
            "8327932763",
        ),
        (
            "PHN health number: 96831283O1",
            "reconstructed_health_card_bc",
            "9683128301",
        ),
    )
    for text, raw_type, expected in cases:
        result = detect_ocr_recovery(text)
        findings = result.get(raw_type, [])
        assert len(findings) == 1, (text, result)
        finding = findings[0]
        assert finding["value"] == expected, finding
        assert finding["reconstructed"] is True, finding
        assert finding["original_ocr"] in text, finding

    # The same bounded primitive accepts an ICAO numeric field only when its
    # supplied 7-3-1 check digit validates.
    expected_field = "120456"
    check_digit = compute_check_digit(expected_field)
    recovered = recover_unique_variant(
        "12O456",
        lambda value: compute_check_digit(value) == check_digit,
    )
    assert recovered == expected_field

    # The length-changing rn/m confusion is available to checksum callers
    # whose field grammar permits letters (for example an ICAO document field).
    mrz_expected = "ABM12"
    mrz_check = compute_check_digit(mrz_expected)
    assert recover_unique_variant(
        "ABrn12",
        lambda value: compute_check_digit(value.upper()) == mrz_check,
        numeric_only=False,
    ) == "ABm12"


def test_no_valid_variant_returns_nothing():
    assert detect_ocr_recovery(
        "Social Insurance Number: 3185O7523"
    ) == {}
    assert detect_ocr_recovery("OHIP health card: B327932764") == {}
    assert detect_ocr_recovery("PHN health number: 96831283O2") == {}


def test_search_cap_abandons_without_partial_search():
    calls = []

    def validator(value):
        calls.append(value)
        return True

    assert recover_unique_variant(
        "IIII",
        validator,
        max_variants=MAX_VARIANTS,
    ) is None
    assert calls == []


def test_normal_and_unchecksummed_types_never_enter_recovery():
    texts = (
        "Social Insurance Number: 318507522",
        "OHIP health card: 8327932763",
        "PHN health number: 9683128301",
        "Alberta health card: 1234S6789",
        "Ontario driver's licence: A123456I8901231",
        "Canadian passport number: A8123456",
        "Unique Client Identifier (UCI): 12-3456-789O",
        "Status card registration number: 123456789O",
    )
    with patch.object(
        ocr_recovery_detector,
        "recover_unique_variant",
        side_effect=AssertionError("recovery must not be attempted"),
    ):
        for text in texts:
            assert detect_ocr_recovery(text) == {}, text


def test_hybrid_tier_provenance_and_all_report_formats():
    text = "Social Insurance Number: 3185O7522"
    matches = detect_pii_hybrid(text, run_ner=False, verify=False)
    findings = matches.get("identifier.reconstructed.sin", [])
    assert len(findings) == 1, findings
    finding = findings[0]
    assert finding["risk_level"] == "MEDIUM", finding
    assert finding["source"] == "ocr_recovery", finding
    assert finding["reconstructed"] is True, finding
    assert finding["original_ocr"] == "3185O7522", finding

    result = {
        "file": "synthetic.txt",
        "scan_status": "scanned",
        "score": 35,
        "matches": matches,
        "metadata": {},
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        markdown = generate_markdown([result], str(root / "report.md"))
        html = generate_html([result], str(root / "report.html"))
        payload = generate_json([result], str(root / "report.json"))

    for rendered in (markdown, html):
        assert "Reconstructed from original OCR" in rendered, rendered
        assert "3185O7522" in rendered, rendered
    json_finding = payload["files"][0]["matches"][
        "identifier.reconstructed.sin"
    ][0]
    assert json_finding["reconstructed"] is True, json.dumps(json_finding)
    assert json_finding["original_ocr"] == "3185O7522", json_finding


def run_suite():
    tests = (
        test_successful_recovery_per_checksummed_type,
        test_no_valid_variant_returns_nothing,
        test_search_cap_abandons_without_partial_search,
        test_normal_and_unchecksummed_types_never_enter_recovery,
        test_hybrid_tier_provenance_and_all_report_formats,
    )
    failures = []
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures.append((test.__name__, exc))
            print(f"FAIL {test.__name__}: {exc}")
    print(
        f"SUMMARY: {len(tests) - len(failures)}/{len(tests)} passed, "
        f"{len(failures)} failed"
    )
    return len(failures)


if __name__ == "__main__":
    sys.exit(1 if run_suite() else 0)
