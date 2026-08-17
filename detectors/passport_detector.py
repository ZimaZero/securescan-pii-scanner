#!/usr/bin/env python3
# passport_detector.py
"""
Passport Number Detector (standalone)
=====================================
Format + context only. Passports have NO public checksum, so detection relies on
a recognizable pattern PLUS a nearby passport keyword — without the keyword guard
these patterns collide with the many other 2-letter+digit / 9-digit codes in the
wild (part numbers, SINs, account numbers, ...).

`hybrid_detector.py` invokes this layer and normalizes its output:

    { type: [(value, confidence), ...], ... }

Patterns
--------
passport_ca       : 2 uppercase letters + 6 digits, optional single space, e.g.
                    "AB123456" or "AB 123456" (Canadian style). REQUIRES a
                    passport keyword within ~40 chars. Confidence ~0.70.
passport_generic  : a standalone 9-digit number, for non-Canadian passports.
                    REQUIRES a passport keyword within ~40 chars. Confidence
                    ~0.60. Deliberately conservative — a bare 9-digit run with no
                    keyword is NOT matched (it would collide with SIN/SSN).

The hybrid layer treats these checksum-less findings as format-and-context
evidence.
"""

import re
from typing import Dict, List, Tuple

# ============================================================
#  CONFIGURATION
# ============================================================

CA_CONFIDENCE = 0.70
GENERIC_CONFIDENCE = 0.60
CONTEXT_WINDOW = 40  # chars on each side of a candidate to scan for a keyword

# Passport keywords (lowercased). "passport no" / "passport number" are already
# covered by the "passport" substring; "travel document" is listed explicitly.
PASSPORT_KEYWORDS = ["passport", "travel document"]

# Canadian-style: 2 uppercase letters + 6 digits, with an optional single space.
_CA_RE = re.compile(r"\b([A-Z]{2})\s?(\d{6})\b")
# Generic: a standalone 9-digit run.
_GENERIC_RE = re.compile(r"\b\d{9}\b")


# ============================================================
#  CONTEXT HELPER
# ============================================================

def _has_passport_keyword(window: str) -> bool:
    return any(kw in window for kw in PASSPORT_KEYWORDS)


# ============================================================
#  MAIN DETECTOR
# ============================================================

def detect_passports(text: str) -> Dict[str, List[Tuple[str, float]]]:
    """Detect passport numbers. Returns {type: [(value, confidence), ...]}."""
    if not isinstance(text, str) or not text.strip():
        return {}

    findings: Dict[str, List[Tuple[str, float]]] = {}

    def add(stype: str, value: str, conf: float):
        findings.setdefault(stype, []).append((value, conf))

    lowered = text.lower()

    # ---- Canadian: 2 letters + 6 digits, keyword required ----
    for m in _CA_RE.finditer(text):
        window = lowered[max(0, m.start() - CONTEXT_WINDOW): m.end() + CONTEXT_WINDOW]
        if _has_passport_keyword(window):
            value = f"{m.group(1)}{m.group(2)}"  # normalize: drop optional space
            add("passport_ca", value, CA_CONFIDENCE)

    # ---- Generic: 9 digits, passport keyword required ----
    for m in _GENERIC_RE.finditer(text):
        window = lowered[max(0, m.start() - CONTEXT_WINDOW): m.end() + CONTEXT_WINDOW]
        if _has_passport_keyword(window):
            add("passport_generic", m.group(0), GENERIC_CONFIDENCE)

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
    pprint(detect_passports(
        "Passport: AB123456\n"
        "passport number AB 123456\n"
        "Passport No 123456789 (foreign)\n"
        "Random code AB123456 in a parts list\n"
        "Postal code A1A 1A1\n"
    ))
