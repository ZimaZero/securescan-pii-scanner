#!/usr/bin/env python3
# tests/test_mismatch_alarm.py
"""
Reusable test suite for detectors/mismatch_alarm.py.

All fixtures are synthetic — no real personal data. Each case supplies a
file path, extracted text, and a hybrid_detector-shaped `matches` dict, and
checks evaluate_mismatch_alarm()'s verdict (None = silent, dict = alarmed)
plus, when alarmed, which trigger fired.

Run directly for a pass/fail table + summary:
    docker compose run --rm securescan-cpu python tests/test_mismatch_alarm.py

Also pytest-compatible (test_cases).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors.mismatch_alarm import (
    FACE_REASON_TEMPLATE,
    UNREADABLE_REASON_TEMPLATE,
    evaluate_mismatch_alarm,
)

# ============================================================
#  HELPERS
# ============================================================


def _finding(category, value="x", risk_level="HIGH"):
    return {category: [{"value": value, "confidence": 0.9, "source": "test",
                         "risk_level": risk_level}]}


NO_FINDINGS = {}

# ============================================================
#  CASES: (name, file_path, text, matches, expect_alarm, expected_triggered_by)
# ============================================================
CASES = [
    (
        "1. Filename keyword, no identifier findings -> ALARM (filename)",
        "/scans/john_passport_scan.jpg", "", NO_FINDINGS,
        True, "filename",
    ),
    (
        "2. Same filename, MRZ document-number finding -> silent",
        "/scans/john_passport_scan.jpg", "",
        _finding("identifier.government.mrz_document_number"),
        False, None,
    ),
    (
        "3. Neutral filename, content keyword, no identifiers -> ALARM (content)",
        "/scans/IMG_2041.jpg", "SOCIAL INSURANCE NUMBER: 123 456 789", NO_FINDINGS,
        True, "content",
    ),
    (
        "4. Same, but government_unverified finding -> silent (unverified clears)",
        "/scans/IMG_2041.jpg", "SOCIAL INSURANCE NUMBER: 123 456 789",
        _finding("identifier.government_unverified.mrz"),
        False, None,
    ),
    (
        "5. 'compass_report.txt' -> silent ('pass' substring must not match)",
        "/scans/compass_report.txt", "", NO_FINDINGS,
        False, None,
    ),
    (
        "6. Generic INSURANCE (Sun Life) -> silent (too generic, excluded)",
        "/scans/statement.txt", "Sun Life Insurance statement enclosed", NO_FINDINGS,
        False, None,
    ),
    (
        "7. FR filename 'permis-de-conduire.jpg' -> ALARM (filename)",
        "/scans/permis-de-conduire.jpg", "", NO_FINDINGS,
        True, "filename",
    ),
    (
        "8. Empty extraction, keyword filename -> ALARM via filename only",
        "/scans/passport_photo.jpg", "", NO_FINDINGS,
        True, "filename",
    ),
    (
        "9. entity.person + contact.phone only, keyword filename -> ALARM",
        "/scans/passport_photo.jpg", "",
        {**_finding("entity.person", risk_level="LOW"),
         **_finding("contact.phone", risk_level="MEDIUM")},
        True, "filename",
    ),
    (
        "9b. Letter-digit boundary: passport2.jpg -> ALARM",
        "/scans/passport2.jpg", "", NO_FINDINGS,
        True, "filename",
    ),
    (
        "9c. Letter-digit boundary does not create substring match",
        "/scans/compass2.txt", "", NO_FINDINGS,
        False, None,
    ),
    (
        "9d. Versioned driving-license filename -> ALARM",
        "/scans/driving-license2024_backup.pdf", "", NO_FINDINGS,
        True, "filename",
    ),
    (
        "9e. Compound PR-card alias + digit boundary -> ALARM",
        "/scans/prcard2026.jpg", "", NO_FINDINGS,
        True, "filename",
    ),
    (
        "9f. Compound driving-license alias + digit boundary -> ALARM",
        "/scans/drivinglicense2.jpg", "", NO_FINDINGS,
        True, "filename",
    ),
    (
        "9g. Compound alias does not create compass substring match",
        "/scans/compass.txt", "", NO_FINDINGS,
        False, None,
    ),
    (
        "9h. Generic plural cards filename stays silent",
        "/scans/cards.txt", "", NO_FINDINGS,
        False, None,
    ),
    (
        "9i. CamelCase name boundary exposes compound driving-license alias",
        "/scans/specimen_drivinglicense_01.jpg", "", NO_FINDINGS,
        True, "filename",
    ),
]

# Extra coverage: content trigger must not crash / must not fire on None text.
NONE_TEXT_CASE = (
    "8b. content trigger tolerates None text (no filename keyword) -> silent",
    "/scans/random_file.jpg", None, NO_FINDINGS,
    False, None,
)

FACE_CASES = [
    (
        "10. Image + face + no identifiers -> ALARM (face)",
        "/scans/IMG_3001.jpg", "", NO_FINDINGS, {"faces_detected": 1},
        True, "face",
    ),
    (
        "11. Image + face + MRZ finding -> silent",
        "/scans/IMG_3001.jpg", "",
        _finding("identifier.government.mrz_document_number"),
        {"faces_detected": 1}, False, None,
    ),
    (
        "12. faces_detected absent -> face trigger stays silent",
        "/scans/IMG_3001.jpg", "", NO_FINDINGS, {},
        False, None,
    ),
    (
        "13. faces_detected=-1 leaves filename trigger unaffected",
        "/scans/passport_photo.jpg", "", NO_FINDINGS, {"faces_detected": -1},
        True, "filename",
    ),
    (
        "14. Non-image file never evaluates face trigger",
        "/scans/portrait.txt", "", NO_FINDINGS, {"faces_detected": 1},
        False, None,
    ),
    (
        "15. Filename and face triggers combine",
        "/scans/passport_photo.jpg", "", NO_FINDINGS, {"faces_detected": 1},
        True, "filename+face",
    ),
    (
        "16. Filename, content, and face triggers combine",
        "/scans/passport_photo.jpg", "PASSPORT", NO_FINDINGS,
        {"faces_detected": 1}, True, "filename+content+face",
    ),
    (
        "17. Taylor license2 filename restores all three triggers",
        "/scans/specimen_drivinglicense_03.jpg",
        "CERTIFICATE OF INDIAN STATUS", NO_FINDINGS,
        {"faces_detected": 4}, True, "filename+content+face",
    ),
]

# (name, file_path, text, matches, disabled_layers, expect_alarm,
#  expected_triggered_by, expected_layers_disabled)
LAYER_DISABLED_CASES = [
    (
        "24. Filename trigger + relevant layers disabled -> note in reason",
        "/scans/john_passport_scan.jpg", "", NO_FINDINGS,
        ["regex", "health_card"],
        True, "filename", ["health_card", "regex"],
    ),
    (
        "25. Filename trigger + only gliner/secrets disabled -> no note (neither is identifier-capable)",
        "/scans/john_passport_scan.jpg", "", NO_FINDINGS,
        ["gliner", "secrets"],
        True, "filename", [],
    ),
    (
        "26. Filename trigger + mixed disabled layers -> only identifier-capable ones listed",
        "/scans/john_passport_scan.jpg", "", NO_FINDINGS,
        ["gliner", "regex", "secrets"],
        True, "filename", ["regex"],
    ),
    (
        "27. Identifier clearance still wins even with layers disabled",
        "/scans/john_passport_scan.jpg", "",
        _finding("identifier.government.mrz_document_number"),
        ["regex", "health_card"],
        False, None, None,
    ),
    (
        "28. No trigger at all -> disabled_layers irrelevant, still silent",
        "/scans/random_file.jpg", "", NO_FINDINGS,
        ["regex"],
        False, None, None,
    ),
    (
        "29. disabled_layers=None (default) behaves exactly as before",
        "/scans/john_passport_scan.jpg", "", NO_FINDINGS,
        None,
        True, "filename", [],
    ),
]

UNREADABLE_CASES = [
    (
        "18. Image + OCR + near-empty text -> ALARM (unreadable)",
        "/scans/IMG_4001.jpg", "faint", NO_FINDINGS,
        {"ocr_confidence": 82.0}, True, "unreadable",
    ),
    (
        "19. Image + OCR + rich confident text -> silent",
        "/scans/IMG_4002.jpg", "Readable document text. " * 20, NO_FINDINGS,
        {"ocr_confidence": 82.0}, False, None,
    ),
    (
        "20. Identifier finding clears unreadable trigger",
        "/scans/IMG_4003.jpg", "faint",
        _finding("identifier.financial.sin"),
        {"ocr_confidence": 20.0}, False, None,
    ),
    (
        "21. Explicit OCR-attempt metadata controls unreadable trigger",
        "/scans/scanned.pdf", "faint", NO_FINDINGS,
        {"ocr_attempted": True}, True, "unreadable",
    ),
    (
        "22. Image without OCR metadata never evaluates unreadable trigger",
        "/scans/IMG_4004.jpg", "faint", NO_FINDINGS,
        {}, False, None,
    ),
    (
        "23. Rich text with low OCR confidence -> ALARM (unreadable)",
        "/scans/IMG_4005.jpg", "Readable document text. " * 20, NO_FINDINGS,
        {"ocr_confidence": 39.9}, True, "unreadable",
    ),
]


# ============================================================
#  EVALUATION
# ============================================================


def _run_case(
    name, file_path, text, matches, expect_alarm, expected_triggered_by, metadata=None
):
    alarm = evaluate_mismatch_alarm(file_path, text, matches, metadata=metadata)
    if expect_alarm:
        ok = alarm is not None and alarm.get("triggered_by") == expected_triggered_by
        if expected_triggered_by == "face" and alarm is not None:
            ok = (
                ok
                and alarm.get("matched_keywords") == []
                and alarm.get("reason") == FACE_REASON_TEMPLATE
            )
        if expected_triggered_by == "unreadable" and alarm is not None:
            ok = (
                ok
                and alarm.get("matched_keywords") == []
                and alarm.get("reason") == UNREADABLE_REASON_TEMPLATE
            )
        actual = alarm.get("triggered_by") if alarm else "None"
        reason = "ok" if ok else f"expected triggered_by={expected_triggered_by!r}, got {actual!r}"
    else:
        ok = alarm is None
        actual = "None" if alarm is None else f"ALARM({alarm.get('triggered_by')})"
        reason = "ok" if ok else "expected silent (None), got an alarm"
    return ok, actual, reason


def _run_layer_case(
    name, file_path, text, matches, disabled_layers, expect_alarm,
    expected_triggered_by, expected_layers_disabled,
):
    alarm = evaluate_mismatch_alarm(
        file_path, text, matches, disabled_layers=disabled_layers
    )
    if expect_alarm:
        ok = (
            alarm is not None
            and alarm.get("triggered_by") == expected_triggered_by
            and alarm.get("layers_disabled") == expected_layers_disabled
        )
        actual = (
            (alarm.get("triggered_by"), alarm.get("layers_disabled")) if alarm else "None"
        )
        reason = (
            "ok"
            if ok
            else f"expected (triggered_by={expected_triggered_by!r}, "
                 f"layers_disabled={expected_layers_disabled!r}), got {actual!r}"
        )
    else:
        ok = alarm is None
        actual = "None" if alarm is None else f"ALARM({alarm.get('triggered_by')})"
        reason = "ok" if ok else "expected silent (None), got an alarm"
    return ok, actual, reason


def run_suite():
    rows, failures = [], []
    for name, file_path, text, matches, expect_alarm, expected_triggered_by in CASES + [NONE_TEXT_CASE]:
        ok, actual, reason = _run_case(name, file_path, text, matches, expect_alarm, expected_triggered_by)
        rows.append((name, actual, ok, reason))
        if not ok:
            failures.append((name, actual, reason))
    for name, file_path, text, matches, metadata, expect_alarm, expected_triggered_by in (
        FACE_CASES + UNREADABLE_CASES
    ):
        ok, actual, reason = _run_case(
            name, file_path, text, matches, expect_alarm, expected_triggered_by, metadata
        )
        rows.append((name, actual, ok, reason))
        if not ok:
            failures.append((name, actual, reason))
    for (
        name, file_path, text, matches, disabled_layers, expect_alarm,
        expected_triggered_by, expected_layers_disabled,
    ) in LAYER_DISABLED_CASES:
        ok, actual, reason = _run_layer_case(
            name, file_path, text, matches, disabled_layers, expect_alarm,
            expected_triggered_by, expected_layers_disabled,
        )
        rows.append((name, actual, ok, reason))
        if not ok:
            failures.append((name, actual, reason))

    print(f"{'CASE':70} {'RESULT':7} {'ACTUAL':10}")
    print("-" * 100)
    for name, actual, ok, reason in rows:
        status = "PASS" if ok else "FAIL"
        line = f"{name:70} {status:7} {str(actual):10}"
        if not ok:
            line += f"  <-- {reason}"
        print(line)

    passed = sum(1 for r in rows if r[2])
    failed = len(rows) - passed
    print("-" * 100)
    print(f"SUMMARY: {passed}/{len(rows)} passed, {failed} failed")
    return passed, failed, failures


# ============================================================
#  PYTEST ENTRY POINTS
# ============================================================


def test_cases():
    for name, file_path, text, matches, expect_alarm, expected_triggered_by in CASES + [NONE_TEXT_CASE]:
        ok, actual, reason = _run_case(name, file_path, text, matches, expect_alarm, expected_triggered_by)
        assert ok, f"{name}: {reason}"
    for name, file_path, text, matches, metadata, expect_alarm, expected_triggered_by in (
        FACE_CASES + UNREADABLE_CASES
    ):
        ok, actual, reason = _run_case(
            name, file_path, text, matches, expect_alarm, expected_triggered_by, metadata
        )
        assert ok, f"{name}: {reason}"
    for (
        name, file_path, text, matches, disabled_layers, expect_alarm,
        expected_triggered_by, expected_layers_disabled,
    ) in LAYER_DISABLED_CASES:
        ok, actual, reason = _run_layer_case(
            name, file_path, text, matches, disabled_layers, expect_alarm,
            expected_triggered_by, expected_layers_disabled,
        )
        assert ok, f"{name}: {reason}"


if __name__ == "__main__":
    _, failed, _ = run_suite()
    sys.exit(1 if failed else 0)
