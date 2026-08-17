#!/usr/bin/env python3
# health_card_detector.py
"""
Canadian Provincial Health Card Detector (standalone)
=====================================================
Three-tier detection. `hybrid_detector.py` invokes this layer and normalizes
its output:

    { type: [(value, confidence), ...], ... }

TIER 1 — checksum-validated AND a health-card keyword nearby (highest
    confidence, strongest possible evidence):
    - Ontario (health_card_on): 10 digits, EXACT official OHIP checksum (odd
      1-indexed positions doubled & digit-summed, mod-10 check). Optional
      trailing 1- or 2-letter version code.
    - British Columbia (health_card_bc): 10 digits, first digit 9, mod-11
      weighted check (weights 2,4,8,5,10,9,7,3 on digits 2-9).
    - Requires a health keyword within CONTEXT_WINDOW chars (same keyword
      list/window mechanics as Tier 3 below).

TIER 2 — province-specific but unverified (demoted, type suffixed
    "_unverified"): either the checksum passes with no health keyword nearby,
    or the province is explicitly named but its checksum fails.

TIER 3 — format + context, NO checksum (lower confidence, REQUIRES nearby
    keyword):
    - Per-province digit formats. Only fires when a health keyword OR a
      province name is within ~50 chars. A bare number with no health
      context does NOT match (avoids colliding with SIN / other 9-digit
      values).

The hybrid layer assigns checksum-valid results higher source priority than
format-and-context-only results.
"""

import re
from typing import Dict, List, Tuple

# ============================================================
#  CONFIGURATION
# ============================================================

TIER1_CONFIDENCE = 0.95  # checksum + keyword context (strongest evidence)
TIER2_CONFIDENCE = 0.55  # checksum only, no context (demoted — see rationale below)
TIER3_CONFIDENCE = 0.60  # format + keyword, no checksum (unchanged from prior 2-tier scheme)
CONTEXT_WINDOW = 50  # chars on each side of a candidate to scan for context

# Generic health keywords (lowercased). Province names (below) also count.
# "ohip" is Ontario's own name for its health card program — checked here so
# a checksum-valid ON number sitting next to "OHIP" earns Tier 1.
HEALTH_KEYWORDS = [
    "health card", "health number", "phn", "uli",
    "medicare", "care card", "ramq", "msi", "ohip",
]

# Province name aliases (lowercased) per province code.
PROVINCE_NAMES = {
    "on": ["ontario"],
    "bc": ["british columbia"],
    "ab": ["alberta"],
    "sk": ["saskatchewan"],
    "mb": ["manitoba"],
    "nb": ["new brunswick"],
    "nu": ["nunavut"],
    "yk": ["yukon"],
    "ns": ["nova scotia"],
    "pe": ["prince edward island", "pei"],
    "nt": ["northwest territories"],
    "nl": ["newfoundland", "labrador"],
    "qc": ["quebec", "québec"],
}
MAX_PROVINCE_NAME_LENGTH = max(
    len(name) for names in PROVINCE_NAMES.values() for name in names
)

# Tier-2 numeric formats: digit length -> province codes sharing that length.
LEN_PROVINCES = {
    8: ["pe", "nt"],
    9: ["ab", "sk", "mb", "nb", "nu", "yk"],
    10: ["ns"],
    12: ["nl"],
}

# Tier-1 candidate: 10 digits + optional 1- or 2-letter version code.
# The version code must be one or two UPPERCASE letters (e.g. X, YM, XR). A
# lowercase trailing word like "on file" is NOT a version code, so it is not
# captured — a bare "9876543217" or "9876543217 YM" works, but "9876543217 on"
# yields just the 10-digit number. Only horizontal separation is allowed:
# a province label at the start of the next line is not a version code.
_TEN_DIGIT_RE = re.compile(r"\b(\d{10})(?:[- \t]?([A-Z]{1,2}))?\b")
# Alberta displayed form: five digits, hyphen, four digits.
_AB_DISPLAY_RE = re.compile(r"\b\d{5}-\d{4}\b")
# Northwest Territories: one letter followed by seven digits.
_NT_RE = re.compile(r"\b[A-Za-z]\d{7}\b")
# Quebec (RAMQ): 4 letters + 8 digits, compact or printed in 4/4/4 groups.
_QC_RE = re.compile(r"\b[A-Za-z]{4}(?:\d{8}| \d{4} \d{4})\b")


# ============================================================
#  CHECKSUMS
# ============================================================

def ohip_valid(value: str) -> bool:
    """Exact official Ontario OHIP checksum (not generic Luhn)."""
    d = re.sub(r"\D", "", value)
    if len(d) != 10 or d[0] == "0":
        return False
    total = 0
    for i in range(9):              # first 9 digits (0-indexed)
        n = int(d[i])
        if i % 2 == 0:             # 1st, 3rd, 5th, ... (odd, 1-indexed) -> double
            n *= 2
            if n > 9:             # digit-sum of a 2-digit double == n - 9
                n -= 9
        total += n
    check = (10 - (total % 10)) % 10  # wrap so total%10==0 -> check 0
    return check == int(d[9])


def bc_phn_valid(value: str) -> bool:
    """British Columbia PHN: 10 digits, first digit 9, mod-11 weighted check."""
    d = re.sub(r"\D", "", value)
    if len(d) != 10 or d[0] != "9":
        return False
    weights = [2, 4, 8, 5, 10, 9, 7, 3]
    total = sum(int(d[i + 1]) * w for i, w in enumerate(weights))  # digits 2-9
    result = 11 - (total % 11)
    return result <= 9 and result == int(d[9])


# ============================================================
#  CONTEXT HELPERS
# ============================================================

def _province_in_window(window: str, provs: List[str]):
    """Return the first province code whose name appears in the window."""
    for prov in provs:
        if any(name in window for name in PROVINCE_NAMES[prov]):
            return prov
    return None


def _has_health_keyword(window: str) -> bool:
    return any(kw in window for kw in HEALTH_KEYWORDS)


# ============================================================
#  MAIN DETECTOR
# ============================================================

def detect_health_cards(text: str) -> Dict[str, List[Tuple[str, float]]]:
    """Detect Canadian health cards. Returns {type: [(value, confidence), ...]}."""
    if not isinstance(text, str) or not text.strip():
        return {}

    findings: Dict[str, List[Tuple[str, float]]] = {}

    def add(stype: str, value: str, conf: float):
        findings.setdefault(stype, []).append((value, conf))

    lowered = text.lower()
    claimed = set()  # digit-strings already claimed by Tier 1/2 (checksum)

    # ---- Tier 1 / Tier 2: checksum-validated ----
    # A passing checksum is real evidence on its own (Tier 2), but a foreign
    # document's bureaucratic reference number can coincidentally satisfy the
    # OHIP/BC algorithm too — see tests/external_octopii evaluation, benchmark
    # case 2 (an Aadhaar "Ref:" number that passes ohip_valid() outright).
    # Checksum + a health-card keyword nearby (Tier 1) is strictly stronger
    # evidence than checksum alone, since a genuine health card is normally
    # labeled as one. This narrows but does not eliminate the Aadhaar-class
    # collision: a foreign reference number sitting next to unrelated text
    # that happens to also contain a health keyword would still reach Tier 1.
    ten_digit_matches = list(_TEN_DIGIT_RE.finditer(text))
    for match_index, m in enumerate(ten_digit_matches):
        digits = m.group(1)
        window = lowered[max(0, m.start() - CONTEXT_WINDOW): m.end() + CONTEXT_WINDOW]
        # Include complete province names whose nearest edge is within the
        # context window. Without the name-length padding, a name beginning
        # just inside the boundary is truncated and cannot be recognized.
        province_start = max(
            0, m.start() - CONTEXT_WINDOW - MAX_PROVINCE_NAME_LENGTH
        )
        province_end = min(
            len(lowered), m.end() + CONTEXT_WINDOW + MAX_PROVINCE_NAME_LENGTH
        )
        # A province label belonging to an adjacent 10-digit health value must
        # not leak across that intervening candidate.
        if match_index:
            province_start = max(
                province_start, ten_digit_matches[match_index - 1].end()
            )
        if match_index + 1 < len(ten_digit_matches):
            province_end = min(
                province_end, ten_digit_matches[match_index + 1].start()
            )
        province_window = lowered[province_start:province_end]
        has_context = _has_health_keyword(window)
        # Province-labelled values in shapes the province does not issue are
        # rejected outright and claimed so the generic Tier-3 path cannot
        # retype them as a Canadian health number.
        if digits[0] == "0" and _province_in_window(province_window, ["on"]):
            claimed.add(digits)
            continue
        if bc_phn_valid(digits):
            if has_context:
                add("health_card_bc", digits, TIER1_CONFIDENCE)
            else:
                add("health_card_bc_unverified", digits, TIER2_CONFIDENCE)
            claimed.add(digits)
        elif ohip_valid(digits):
            if has_context:
                add("health_card_on", m.group(0), TIER1_CONFIDENCE)
            else:
                add("health_card_on_unverified", m.group(0), TIER2_CONFIDENCE)
            claimed.add(digits)
        elif _province_in_window(province_window, ["bc"]):
            add("health_card_bc_unverified", digits, TIER2_CONFIDENCE)
            claimed.add(digits)
        elif _province_in_window(province_window, ["on"]):
            add("health_card_on_unverified", m.group(0), TIER2_CONFIDENCE)
            claimed.add(digits)

    # Alberta's printed form is normalized before its shape/length check so
    # displayed and compact cards share the same canonical finding value.
    for m in _AB_DISPLAY_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) != 9:
            continue
        window = lowered[max(0, m.start() - CONTEXT_WINDOW): m.end() + CONTEXT_WINDOW]
        if _province_in_window(window, ["ab"]) or _has_health_keyword(window):
            add("health_card_ab", digits, TIER3_CONFIDENCE)

    # Northwest Territories uses an alphanumeric shape rather than the
    # eight-digit numeric shape shared by the old generic length table.
    for m in _NT_RE.finditer(text):
        window = lowered[max(0, m.start() - CONTEXT_WINDOW): m.end() + CONTEXT_WINDOW]
        if _province_in_window(window, ["nt"]) or _has_health_keyword(window):
            add("health_card_nt", m.group(0), TIER3_CONFIDENCE)

    # ---- Tier 3: format + context (keyword/province required), no checksum ----
    for length, provs in LEN_PROVINCES.items():
        for m in re.finditer(r"\b\d{%d}\b" % length, text):
            digits = m.group(0)
            if digits in claimed:
                continue  # already a Tier-1/Tier-2 checksum hit
            window = lowered[max(0, m.start() - CONTEXT_WINDOW): m.end() + CONTEXT_WINDOW]
            prov = _province_in_window(window, provs)
            if prov:
                add(f"health_card_{prov}", digits, TIER3_CONFIDENCE)
            elif _has_health_keyword(window):
                add("health_card_ca", digits, TIER3_CONFIDENCE)

    # Quebec (alphanumeric, no checksum available — always Tier 3)
    for m in _QC_RE.finditer(text):
        value = re.sub(r"\s", "", m.group(0))
        if re.fullmatch(r"[A-Za-z]{4}\d{8}", value) is None:
            continue
        window = lowered[max(0, m.start() - CONTEXT_WINDOW): m.end() + CONTEXT_WINDOW]
        if any(n in window for n in PROVINCE_NAMES["qc"]) or _has_health_keyword(window):
            add("health_card_qc", value, TIER3_CONFIDENCE)

    # ---- Dedup per type by value (keep highest confidence) ----
    result: Dict[str, List[Tuple[str, float]]] = {}
    for stype, items in findings.items():
        best: Dict[str, float] = {}
        for value, conf in items:
            if value not in best or conf > best[value]:
                best[value] = conf
        result[stype] = [(v, c) for v, c in best.items()]
    return result


if __name__ == "__main__":
    from pprint import pprint
    pprint(detect_health_cards(
        "ON: 9876543217\nBC PHN 9698658215\nAlberta health card: 123456789\n"
        "Random ref 123456789 with no context"
    ))
