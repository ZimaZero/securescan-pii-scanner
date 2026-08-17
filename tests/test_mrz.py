#!/usr/bin/env python3
# tests/test_mrz.py
"""
Reusable test suite for detectors/mrz_detector.py.

Fixtures are built (never hardcoded) by _icao_check_digit() below — an
independent reimplementation of the ICAO 9303 7-3-1 weighted check-digit
algorithm — so a bug shared between the fixture builder and the detector
itself can't silently make a broken detector look correct. No real personal
MRZ data appears anywhere in this file: all names/numbers are synthetic.

Run directly for a pass/fail table + summary:
    docker compose run --rm securescan-cpu python tests/test_mrz.py

Also pytest-compatible (test_should_match / test_should_skip).
"""

import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors import mrz_detector
from detectors.mrz_detector import detect_mrz

# ============================================================
#  FIXTURE BUILDER (independent checksum reimplementation)
# ============================================================


def _icao_check_digit(s: str) -> str:
    values = {"<": 0}
    for i, c in enumerate("0123456789"):
        values[c] = i
    for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        values[c] = i + 10
    weights = [7, 3, 1]
    total = sum(values[c] * weights[i % 3] for i, c in enumerate(s))
    return str(total % 10)


def _pad(s: str, n: int) -> str:
    return s.ljust(n, "<")[:n]


def build_td3(doc_number, nationality, dob, sex, expiry, surname, given_names,
              issuing_state="CAN", doc_type="P<", personal_number=""):
    doc_number = _pad(doc_number, 9)
    doc_check = _icao_check_digit(doc_number)
    dob_check = _icao_check_digit(dob)
    expiry_check = _icao_check_digit(expiry)
    personal_number = _pad(personal_number, 14)
    personal_check = _icao_check_digit(personal_number) if personal_number.strip("<") else "<"
    line2 = (
        doc_number + doc_check + _pad(nationality, 3) + dob + dob_check + sex
        + expiry + expiry_check + personal_number + personal_check
    )
    composite_input = doc_number + doc_check + dob + dob_check + expiry + expiry_check + personal_number + personal_check
    line2 += _icao_check_digit(composite_input)

    name_field = _pad(f"{surname}<<{given_names}", 39)
    line1 = _pad(doc_type, 2) + _pad(issuing_state, 3) + name_field
    assert len(line1) == 44 and len(line2) == 44
    return line1, line2


def build_td1(doc_number, dob, sex, expiry, nationality, surname, given_names,
              issuing_state="CAN", doc_type="IC", optional1="", optional2=""):
    doc_number = _pad(doc_number, 9)
    doc_check = _icao_check_digit(doc_number)
    optional1 = _pad(optional1, 15)
    line1 = _pad(doc_type, 2) + _pad(issuing_state, 3) + doc_number + doc_check + optional1

    dob_check = _icao_check_digit(dob)
    expiry_check = _icao_check_digit(expiry)
    optional2 = _pad(optional2, 11)
    composite_input = line1[5:30] + dob + dob_check + expiry + expiry_check
    composite_check = _icao_check_digit(composite_input)
    line2 = dob + dob_check + sex + expiry + expiry_check + _pad(nationality, 3) + optional2 + composite_check

    name_field = _pad(f"{surname}<<{given_names}", 30)
    line3 = name_field
    assert len(line1) == 30 and len(line2) == 30 and len(line3) == 30
    return line1, line2, line3


def build_td2(doc_number, nationality, dob, sex, expiry, surname, given_names,
              issuing_state="CAN", doc_type="V<", optional=""):
    """Build a synthetic TD2 block for the gates that also affect TD2."""
    doc_number = _pad(doc_number, 9)
    doc_check = _icao_check_digit(doc_number)
    dob_check = _icao_check_digit(dob)
    expiry_check = _icao_check_digit(expiry)
    line1 = _pad(doc_type, 2) + _pad(issuing_state, 3) + _pad(
        f"{surname}<<{given_names}", 31
    )
    line2 = (
        doc_number + doc_check + _pad(nationality, 3) + dob + dob_check + sex
        + expiry + expiry_check + _pad(optional, 7) + "0"
    )
    assert len(line1) == 36 and len(line2) == 36
    return line1, line2


def _corrupt(s: str, index: int, new_char: str) -> str:
    return s[:index] + new_char + s[index + 1:]


# ============================================================
#  FIXTURES
# ============================================================

# Doc numbers deliberately avoid letters that are themselves NUMERIC_FIX keys
# (O, Q, I, L, Z, S, B, G), preventing the "bad check digit" fixtures below from
# accidentally "repaired" into passing by mangling a legitimate letter — the
# repair path is exercised on purpose only in the OCR-noise fixtures.
TD3_L1, TD3_L2 = build_td3(
    doc_number="AC0123456", nationality="CAN", dob="900101", sex="F",
    expiry="300101", surname="SAMPLE", given_names="JANE",
)
TD3_TEXT = f"{TD3_L1}\n{TD3_L2}\n"

TD1_L1, TD1_L2, TD1_L3 = build_td1(
    doc_number="AC12345", dob="850615", sex="M", expiry="290615",
    nationality="CAN", surname="TESTUSER", given_names="JOHN",
)
TD1_TEXT = f"{TD1_L1}\n{TD1_L2}\n{TD1_L3}\n"

TD2_L1, TD2_L2 = build_td2(
    doc_number="AC54321", nationality="CAN", dob="910202", sex="F",
    expiry="310202", surname="EXAMPLE", given_names="MARIE",
)
TD2_TEXT = f"{TD2_L1}\n{TD2_L2}\n"

# --- OCR noise: O/0 swap inside the doc-number field (TD3) ---
_noisy_td3_l2 = _corrupt(TD3_L2, 2, "O")  # TD3_L2[2] is the '0' in "AC0123456"
assert TD3_L2[2] == "0" and _noisy_td3_l2[2] == "O"
TD3_OCR_ODIGIT_TEXT = f"{TD3_L1}\n{_noisy_td3_l2}\n"

# --- OCR noise: split line2 mid-way, as if OCR line-wrapped it (TD3) ---
_split_point = 20
TD3_SPLIT_TEXT = f"{TD3_L1}\n{TD3_L2[:_split_point]}\n{TD3_L2[_split_point:]}\n"

# --- OCR noise: O/0 swap inside the doc-number field (TD1) ---
# Separate fixture whose doc number contains a real '0' to swap.
_TD1_ODIGIT_L1, _TD1_ODIGIT_L2, _TD1_ODIGIT_L3 = build_td1(
    doc_number="AC0123456", dob="850615", sex="M", expiry="290615",
    nationality="CAN", surname="TESTUSER", given_names="JOHN",
)
# doc-number field is line1[5:14] = "AC0123456"; its '0' sits at absolute index 7.
assert _TD1_ODIGIT_L1[7] == "0"
_noisy_td1_l1_odigit = _corrupt(_TD1_ODIGIT_L1, 7, "O")
TD1_OCR_ODIGIT_TEXT = f"{_noisy_td1_l1_odigit}\n{_TD1_ODIGIT_L2}\n{_TD1_ODIGIT_L3}\n"

# --- OCR noise: K misread for a filler '<' at the end of the doc-number
# field (TD1). TD1_L1[13] is the second (rightmost) padding '<' in "AC12345<<".
assert TD1_L1[12] == "<" and TD1_L1[13] == "<"
_noisy_td1_l1_kfiller = _corrupt(TD1_L1, 13, "K")
TD1_OCR_KFILLER_TEXT = f"{_noisy_td1_l1_kfiller}\n{TD1_L2}\n{TD1_L3}\n"

# --- Corrupted check digit: doc-number check digit is wrong, dob/expiry are
# untouched -> doc number must land in the unverified tier, dob/expiry still
# validate independently.
_bad_check = str((int(TD3_L2[9]) + 1) % 10)
_corrupted_td3_l2 = _corrupt(TD3_L2, 9, _bad_check)
TD3_BAD_CHECK_TEXT = f"{TD3_L1}\n{_corrupted_td3_l2}\n"

_bad_check_td1 = str((int(TD1_L1[14]) + 1) % 10)
_corrupted_td1_l1 = _corrupt(TD1_L1, 14, _bad_check_td1)
TD1_BAD_CHECK_TEXT = f"{_corrupted_td1_l1}\n{TD1_L2}\n{TD1_L3}\n"

# --- Regression fixtures: block preceded/followed by ordinary text lines.
# Guards the role-anchoring bug where a leading non-candidate line shifted
# a genuine block's line1/line2/line3 into the wrong role slot (line1's
# content parsed as if it were line2, etc.), silently producing zero
# findings. Context lines end in punctuation so they're unambiguously
# rejected by the MRZ charset test, not just by length.
_CONTEXT_BEFORE = [
    "Please review the attached identification documents.",
    "Reference file number is noted below for processing.",
]
_CONTEXT_AFTER = [
    "End of extracted machine-readable zone content.",
    "Please contact the office for any questions.",
]

TD1_EMBEDDED_TEXT = "\n".join(_CONTEXT_BEFORE + [TD1_L1, TD1_L2, TD1_L3] + _CONTEXT_AFTER) + "\n"
TD3_EMBEDDED_TEXT = "\n".join(_CONTEXT_BEFORE + [TD3_L1, TD3_L2] + _CONTEXT_AFTER) + "\n"

# --- The killer case: a short (20-29 char after space-strip) plain-text
# line, letters only (so it passes the MRZ charset test on its own, just
# not the length -- unlike the punctuation-terminated context lines
# above), sitting IMMEDIATELY before the block. This is the exact
# real-world trigger: a single failed role0 attempt at this line was
# enough to shift TD1_L1 into the "line2" role slot.
_SHORT_KILLER_LINE = "Scanned document below"
assert 20 <= len(_SHORT_KILLER_LINE.replace(" ", "")) <= 29
TD1_KILLER_TEXT = f"{_SHORT_KILLER_LINE}\n{TD1_L1}\n{TD1_L2}\n{TD1_L3}\n"

# --- 32-char name line (2 trailing extra chars, within the +/-2 length
# tolerance) in a context-surrounded (embedded) TD1 block -- combines the
# length-tolerance path with the role-anchoring fix in one fixture.
_TD1_32_L1, _TD1_32_L2, _TD1_32_L3 = build_td1(
    doc_number="AC98765", dob="770303", sex="F", expiry="311231",
    nationality="CAN", surname="LONGERNAME", given_names="PATRICIA",
)
_TD1_32_L3_LONG = _TD1_32_L3 + "XX"
assert len(_TD1_32_L3_LONG) == 32
TD1_32CHAR_NAME_EMBEDDED_TEXT = "\n".join(
    _CONTEXT_BEFORE + [_TD1_32_L1, _TD1_32_L2, _TD1_32_L3_LONG] + _CONTEXT_AFTER
) + "\n"

# ============================================================
#  MATCH / SKIP CASES
# ============================================================
# Each MATCH case: (name, text, expected_type, value_predicate)

SHOULD_MATCH = [
    ("Valid TD3 doc number", TD3_TEXT, "mrz_document_number", lambda v: v == "AC0123456"),
    ("Valid TD3 dob", TD3_TEXT, "mrz_dob", lambda v: v == "900101"),
    ("Valid TD3 expiry", TD3_TEXT, "mrz_expiry", lambda v: v == "300101"),

    ("Valid TD1 doc number", TD1_TEXT, "mrz_document_number", lambda v: v == "AC12345"),
    ("Valid TD1 dob", TD1_TEXT, "mrz_dob", lambda v: v == "850615"),
    ("Valid TD1 expiry", TD1_TEXT, "mrz_expiry", lambda v: v == "290615"),

    ("TD3 O/0 swap in doc number still validates", TD3_OCR_ODIGIT_TEXT,
     "mrz_document_number", lambda v: v == "AC0123456"),
    ("TD3 split line still validates", TD3_SPLIT_TEXT,
     "mrz_document_number", lambda v: v == "AC0123456"),
    ("TD1 O/0 swap in doc number still validates", TD1_OCR_ODIGIT_TEXT,
     "mrz_document_number", lambda v: v == "AC0123456"),
    ("TD1 K-for-< filler still validates", TD1_OCR_KFILLER_TEXT,
     "mrz_document_number", lambda v: v == "AC12345"),

    ("TD1 embedded mid-document (context before/after)", TD1_EMBEDDED_TEXT,
     "mrz_document_number", lambda v: v == "AC12345"),
    ("TD1 embedded mid-document dob", TD1_EMBEDDED_TEXT,
     "mrz_dob", lambda v: v == "850615"),
    ("TD1 embedded mid-document expiry", TD1_EMBEDDED_TEXT,
     "mrz_expiry", lambda v: v == "290615"),
    ("TD3 embedded mid-document (context before/after)", TD3_EMBEDDED_TEXT,
     "mrz_document_number", lambda v: v == "AC0123456"),

    ("TD1 killer case: short plain-text line immediately before block",
     TD1_KILLER_TEXT, "mrz_document_number", lambda v: v == "AC12345"),

    ("TD1 32-char name line, embedded mid-document",
     TD1_32CHAR_NAME_EMBEDDED_TEXT, "mrz_document_number", lambda v: v == "AC98765"),
    ("TD1 32-char name line, embedded mid-document dob",
     TD1_32CHAR_NAME_EMBEDDED_TEXT, "mrz_dob", lambda v: v == "770303"),
    ("TD1 32-char name line, embedded mid-document expiry",
     TD1_32CHAR_NAME_EMBEDDED_TEXT, "mrz_expiry", lambda v: v == "311231"),

    ("TD3 bad check digit -> unverified tier", TD3_BAD_CHECK_TEXT,
     "mrz_unverified", lambda v: v == "AC0123456"),
    ("TD3 bad check digit still validates dob", TD3_BAD_CHECK_TEXT,
     "mrz_dob", lambda v: v == "900101"),
    ("TD3 bad check digit still validates expiry", TD3_BAD_CHECK_TEXT,
     "mrz_expiry", lambda v: v == "300101"),
    ("TD1 bad check digit -> unverified tier", TD1_BAD_CHECK_TEXT,
     "mrz_unverified", lambda v: v == "AC12345"),
]

# Each SKIP case: (name, text) that must produce a completely empty result.
SHOULD_SKIP = [
    ("Base64 blob line", "SGVsbG8gV29ybGQhIFRoaXMgaXMgYSB0ZXN0IG9mIHRoZSBzeXN0ZW0=\n"),
    ("Code line of caps+digits (no companion line)",
     "ABCD1234EFGH5678IJKL9012MNOP345\n"),
    ("Long hex string (no companion line)",
     "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4\n"),
    ("Prose paragraph, no MRZ", "The quick brown fox jumps over the lazy dog.\nNothing to see here.\n"),
    ("Single TD3-shaped line with no companion", TD3_L2 + "\n"),
]

# Cases that must NOT reach the validated HIGH tier even though a check
# digit fails -- must not appear as "mrz_document_number" at all.
NO_HIGH_TIER = [
    ("TD3 bad check digit", TD3_BAD_CHECK_TEXT),
    ("TD1 bad check digit", TD1_BAD_CHECK_TEXT),
]

# Synthetic prose with two TD1-length/charset-clean lines. It reproduces the
# false block-admission class without embedding any real identifier.
PROSE_FALSE_BLOCK = (
    "Constrructing a cross commodity swap\n"
    "need example of a crack spread swap\n"
)

TD3_INVALID_STATE_TEXT = f"{TD3_L1[:2]}ZZZ{TD3_L1[5:]}\n{TD3_L2}\n"
TD3_INVALID_TYPE_TEXT = f"X{TD3_L1[1:]}\n{TD3_L2}\n"
TD3_GERMANY_L1, TD3_GERMANY_L2 = build_td3(
    doc_number="AC7654321", nationality="D", dob="900101", sex="F",
    expiry="300101", surname="MUSTER", given_names="ERIKA",
    issuing_state="D",
)
TD3_GERMANY_TEXT = f"{TD3_GERMANY_L1}\n{TD3_GERMANY_L2}\n"


@contextmanager
def _gate_settings(**enabled):
    names = (
        "GATE_A_REQUIRE_CHEVRON",
        "GATE_B_VALID_ISSUING_STATE",
        "GATE_C_VALID_DOC_TYPE",
        "GATE_D_CORROBORATED_UNVERIFIED",
    )
    old = {name: getattr(mrz_detector, name) for name in names}
    try:
        for name in names:
            setattr(mrz_detector, name, bool(enabled.get(name, False)))
        yield
    finally:
        for name, value in old.items():
            setattr(mrz_detector, name, value)


def _gate_checks():
    checks = []

    def record(name, condition, actual):
        checks.append(("GATE", name, _summarize(actual), condition,
                       "ok" if condition else "gate behavior differs"))

    with _gate_settings(GATE_A_REQUIRE_CHEVRON=True):
        prose = detect_mrz(PROSE_FALSE_BLOCK)
        td3 = detect_mrz(TD3_TEXT)
        td2 = detect_mrz(TD2_TEXT)
        record("A rejects chevron-free prose block", not prose, prose)
        record("A retains chevron-bearing TD3", "mrz_document_number" in td3, td3)
        record("A retains chevron-bearing TD2", "mrz_document_number" in td2, td2)

    with _gate_settings(GATE_B_VALID_ISSUING_STATE=True):
        invalid = detect_mrz(TD3_INVALID_STATE_TEXT)
        canada = detect_mrz(TD3_TEXT)
        germany = detect_mrz(TD3_GERMANY_TEXT)
        td2 = detect_mrz(TD2_TEXT)
        record("B rejects non-authority state ZZZ", not invalid, invalid)
        record("B retains ISO state CAN", "mrz_document_number" in canada, canada)
        record("B retains ICAO special state D", "mrz_document_number" in germany, germany)
        record("B retains TD2 ISO state", "mrz_document_number" in td2, td2)

    with _gate_settings(GATE_C_VALID_DOC_TYPE=True):
        invalid = detect_mrz(TD3_INVALID_TYPE_TEXT)
        td3 = detect_mrz(TD3_TEXT)
        td1 = detect_mrz(TD1_TEXT)
        td2 = detect_mrz(TD2_TEXT)
        record("C rejects TD3 non-P type", not invalid, invalid)
        record("C retains TD3 P type", "mrz_document_number" in td3, td3)
        record("C retains TD1 I type", "mrz_document_number" in td1, td1)
        record("C retains TD2 V type", "mrz_document_number" in td2, td2)

    with _gate_settings(GATE_D_CORROBORATED_UNVERIFIED=True):
        prose = detect_mrz(PROSE_FALSE_BLOCK)
        bad_td3 = detect_mrz(TD3_BAD_CHECK_TEXT)
        record("D suppresses uncorroborated prose document", "mrz_unverified" not in prose, prose)
        record("D retains unverified doc with valid dates", "mrz_unverified" in bad_td3, bad_td3)

    return checks

# ============================================================
#  EVALUATION
# ============================================================


def _values_for(result, stype):
    return {v for v, _conf, _meta in result.get(stype, [])}


def evaluate_match(result, stype, predicate):
    if stype not in result:
        return False, f"missing type {stype!r} (got {sorted(result)})"
    values = _values_for(result, stype)
    if not any(predicate(v) for v in values):
        return False, f"no value in {values} satisfies predicate"
    return True, "ok"


def evaluate_skip(result):
    if result:
        return False, f"leaked: { {k: _values_for(result, k) for k in result} }"
    return True, "ok"


def evaluate_no_high_tier(result, name):
    values = _values_for(result, "mrz_document_number")
    if values:
        return False, f"reached validated tier with bad check digit: {values}"
    return True, "ok"


def _summarize(result):
    if not result:
        return "{}"
    return ", ".join(f"{t}={sorted(_values_for(result, t))}" for t in sorted(result))


def run_suite():
    rows = []
    failures = []

    for name, text, stype, predicate in SHOULD_MATCH:
        result = detect_mrz(text)
        ok, reason = evaluate_match(result, stype, predicate)
        rows.append(("MATCH", name, _summarize(result), ok, reason))
        if not ok:
            failures.append((name, reason, result))

    for name, text in SHOULD_SKIP:
        result = detect_mrz(text)
        ok, reason = evaluate_skip(result)
        rows.append(("SKIP", name, _summarize(result), ok, reason))
        if not ok:
            failures.append((name, reason, result))

    for name, text in NO_HIGH_TIER:
        result = detect_mrz(text)
        ok, reason = evaluate_no_high_tier(result, name)
        rows.append(("NOHIGH", name, _summarize(result), ok, reason))
        if not ok:
            failures.append((name, reason, result))

    for row in _gate_checks():
        rows.append(row)
        if not row[3]:
            failures.append((row[1], row[4], row[2]))

    print(f"{'GRP':7} {'CASE':46} {'RESULT':7} {'ACTUAL':40}")
    print("-" * 110)
    for grp, name, actual, ok, reason in rows:
        status = "PASS" if ok else "FAIL"
        line = f"{grp:7} {name:46} {status:7} {actual:40}"
        if not ok:
            line += f"  <-- {reason}"
        print(line)

    passed = sum(1 for r in rows if r[3])
    failed = len(rows) - passed
    print("-" * 110)
    print(f"SUMMARY: {passed}/{len(rows)} passed, {failed} failed")
    return passed, failed, failures


# ============================================================
#  PYTEST ENTRY POINTS
# ============================================================


def test_should_match():
    for name, text, stype, predicate in SHOULD_MATCH:
        ok, reason = evaluate_match(detect_mrz(text), stype, predicate)
        assert ok, f"{name}: {reason}"


def test_should_skip():
    for name, text in SHOULD_SKIP:
        ok, reason = evaluate_skip(detect_mrz(text))
        assert ok, f"{name}: {reason}"


def test_no_high_tier_on_bad_check_digit():
    for name, text in NO_HIGH_TIER:
        ok, reason = evaluate_no_high_tier(detect_mrz(text), name)
        assert ok, f"{name}: {reason}"


if __name__ == "__main__":
    _, failed, _ = run_suite()
    sys.exit(1 if failed else 0)
