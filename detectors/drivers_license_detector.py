#!/usr/bin/env python3
# drivers_license_detector.py
"""
Canadian Driver's Licence Detector (standalone)
===============================================
Format + context only for ALL provinces/territories. The ten provincial
grammars come from Microsoft Purview's Canada driver's-licence sensitive
information type. Territories are documented separately beside their patterns.
Canadian DL numbers have no checksum, and several formats are bare digit runs,
so a bare match
collides with SINs, phones, health cards, postal codes and just about every
other number. The keyword/province gate therefore does ALL the work: a candidate
fires only when both a jurisdiction keyword and a generic driver's-licence
keyword occur within the 300-character Purview proximity.

Output shape matches the other detectors so it can plug into the hybrid layer
later:

    { type: [(value, confidence), ...], ... }

Type labels are `drivers_license_<prov>`; the Purview two-keyword contract does
not permit a province-unknown generic licence finding.

Microsoft Purview provincial formats:
    AB  6 digits + hyphen + 3 digits, or 5-9 bare digits
    BC  7 digits
    MB  AA-AA-AA-A999AA (hyphens optional)
    NB  5-7 digits
    NL  1 letter + 9 digits
    NS  AAAAA + optional hyphen + [0-3]9[0-1]999999 shape
    ON  A9999-99999-9[0156]9[0-3]9 (hyphens optional)
    PE  5-6 digits
    QC  1 letter + 12 digits (printed specimen groups those digits with hyphens)
    SK  8 digits

Territory formats (not covered by Microsoft Purview; see source comments):
    NT  10 digits
    NU  A9999 9999-999 (display separators optional)
    YT  6 digits
"""

import re
from typing import Dict, List, Tuple

from detectors.field_label_association import (
    NON_DOB_DATE_LABELS,
    associate_field_label,
    build_field_label_index,
)

# ============================================================
#  CONFIGURATION
# ============================================================

CONFIDENCE = 0.60          # format + context, no checksum — same for every path
CONTEXT_WINDOW = 300       # Purview patternsProximity, measured around the candidate

# Microsoft Purview's Keyword_canada_drivers_license vocabulary.  The published
# list enumerates these generated driver/licence combinations individually;
# retaining the expansion here makes omissions and additions auditable.
_DRIVER_FORMS = ("Driver", "Drivers", "Driver'", "Driver's")
_LICENCE_FORMS = ("Lic", "Lics", "License", "Licenses", "Licence", "Licences")
CANADA_DL_KEYWORDS = frozenset(
    {"DL", "DLS", "CDL", "CDLS", "DL#", "DLS#", "CDL#", "CDLS#"}
    | {
        f"{driver}{separator}{licence}{hash_suffix}"
        for driver in _DRIVER_FORMS
        for separator in ("", " ")
        for licence in _LICENCE_FORMS
        for hash_suffix in ("", "#")
    }
    | {"Permis de Conduire", "Permis de Conduire#"}
    | {
        "id", "ids", "id#", "ids#",
        "idcard", "idcard#",
        "idcard number", "idcard numbers", "idcard #", "idcard #s",
        "idcard card", "idcard cards", "idcard card#", "idcard cards#",
        "identification", "identification#",
        "identification number", "identification numbers",
        "identification #", "identification #s",
        "identification card", "identification cards",
        "identification card#", "identification cards#",
    }
)


def _keyword_pattern(keywords: frozenset[str]) -> re.Pattern[str]:
    alternatives = []
    for keyword in sorted(keywords, key=len, reverse=True):
        escaped = re.escape(keyword).replace(r"\ ", r"\s+")
        alternatives.append(escaped)
    return re.compile(
        r"(?<![A-Za-z0-9])(?:" + "|".join(alternatives) + r")(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


DL_KEYWORD_RE = _keyword_pattern(CANADA_DL_KEYWORDS)

# Purview's Keyword_[province]_drivers_license_name class is the province's
# full name OR abbreviation. Territories are outside Purview; their analogous
# keywords remain explicitly specimen-derived with their formats.
PROVINCE_KEYWORDS = {
    "on": frozenset({"Ontario", "ON"}),
    "qc": frozenset({"Quebec", "Québec", "QC"}),
    "bc": frozenset({"British Columbia", "BC"}),
    "ab": frozenset({"Alberta", "AB"}),
    "sk": frozenset({"Saskatchewan", "SK"}),
    "mb": frozenset({"Manitoba", "MB"}),
    "ns": frozenset({"Nova Scotia", "NS"}),
    "nb": frozenset({"New Brunswick", "NB"}),
    "nl": frozenset({"Newfoundland and Labrador", "Newfoundland", "Labrador", "NL"}),
    "pe": frozenset({"Prince Edward Island", "PE", "PEI"}),
    "nt": frozenset({"Northwest Territories", "NT", "NWT"}),
    "nu": frozenset({"Nunavut", "NU"}),
    "yt": frozenset({"Yukon", "YT", "YK"}),
}
PROVINCE_KEYWORD_RES = {
    province: _keyword_pattern(keywords)
    for province, keywords in PROVINCE_KEYWORDS.items()
}

# Microsoft Purview definitions for the ten provinces. Distinctive shapes can
# identify their province with either a driver's-licence keyword or the matching
# province name. Formatting separators are consumed inside the full match so the
# standalone-token guard examines only the outside characters.
# https://learn.microsoft.com/en-us/purview/sit-defn-canada-drivers-license-number
DISTINCTIVE_PATTERNS = [
    ("ab", re.compile(r"\d{6}-\d{3}")),
    (
        "mb",
        re.compile(
            r"[A-Za-z]{2}-?[A-Za-z]{2}-?[A-Za-z]{2}-?"
            r"[A-Za-z]\d{3}[A-Za-z]{2}"
        ),
    ),
    ("nl", re.compile(r"[A-Za-z]\d{9}")),
    ("ns", re.compile(r"[A-Za-z]{5}-?[0-3]\d[01]\d{6}")),
    ("on", re.compile(r"[A-Za-z]\d{4}-?\d{5}-?\d[0156]\d[0-3]\d")),
    ("qc", re.compile(r"[A-Za-z]\d{12}")),
    # The photographed Quebec display groups the same 12 digits 4-6-2.
    ("qc", re.compile(r"[A-Za-z]\d{4}-\d{6}-\d{2}")),

    # SPECIMEN-DERIVED (one Nunavut specimen, field 5): one letter plus
    # 4-4-3 digits, printed as A1234 5678-004. Optional display separators
    # tolerate compact OCR, but this is not an established territorial grammar.
    ("nu", re.compile(r"[A-Za-z]\d{4} ?\d{4}-?\d{3}")),
]

# Purview's numeric alternatives require explicit province context because
# their lengths overlap. Alberta's 5-9 digit alternative remains intentionally
# broad and is reported separately from its hyphenated form in evaluations.
NUMERIC_PROVINCE_PATTERNS = {
    "ab": re.compile(r"\d{5,9}"),
    "bc": re.compile(r"\d{7}"),
    "nb": re.compile(r"\d{5,7}"),
    "pe": re.compile(r"\d{5,6}"),
    "sk": re.compile(r"\d{8}"),

    # SPECIMEN-DERIVED (one Northwest Territories specimen, field 4d).
    # A single specimen is weak evidence, not an established grammar.
    "nt": re.compile(r"\d{10}"),
    # SPECIMEN-DERIVED (two independent Yukon specimens: 504896, 129804).
    # This is corroborated specimen evidence, still not an issuing-authority source.
    "yt": re.compile(r"\d{6}"),
}

def _is_standalone_token(text: str, start: int, end: int) -> bool:
    """Reject a licence candidate embedded in a longer joined identifier.

    A regex word boundary is insufficient because hyphens and asterisks create
    boundaries inside printed identifiers.  Provincial patterns may contain
    those separators *inside* their complete match; only the characters
    immediately outside the candidate span are examined here.
    """
    forbidden = "-*"
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return not (
        (before and (before.isalnum() or before in forbidden))
        or (after and (after.isalnum() or after in forbidden))
    )


# ============================================================
#  MAIN DETECTOR
# ============================================================

def detect_drivers_licenses(text: str) -> Dict[str, List[Tuple[str, float]]]:
    """Detect Canadian driver's licences. Returns {type: [(value, conf), ...]}."""
    if not isinstance(text, str) or not text.strip():
        return {}

    findings: Dict[str, List[Tuple[str, float]]] = {}

    def add(stype: str, value: str):
        findings.setdefault(stype, []).append((value, CONFIDENCE))

    lowered = text.lower()
    claimed: List[Tuple[int, int]] = []  # spans consumed by distinctive matches

    def context_bounds(start: int, end: int) -> Tuple[int, int]:
        return max(0, start - CONTEXT_WINDOW), min(
            len(lowered), end + CONTEXT_WINDOW
        )

    def has_keyword(start: int, end: int) -> bool:
        left, right = context_bounds(start, end)
        return DL_KEYWORD_RE.search(lowered, left, right) is not None

    def province_in(start: int, end: int, prov: str) -> bool:
        left, right = context_bounds(start, end)
        return PROVINCE_KEYWORD_RES[prov].search(lowered, left, right) is not None

    def overlaps_claimed(start: int, end: int) -> bool:
        return any(start < ce and cs < end for cs, ce in claimed)

    # ---- Distinctive alphanumeric formats (shape implies province) ----
    for prov, rx in DISTINCTIVE_PATTERNS:
        for m in rx.finditer(text):
            if not _is_standalone_token(text, m.start(), m.end()):
                continue
            if has_keyword(m.start(), m.end()) and province_in(
                m.start(), m.end(), prov
            ):
                add(f"drivers_license_{prov}", m.group(0))
                claimed.append((m.start(), m.end()))

    # ---- Province-specific bare numeric Purview alternatives ----
    for prov, rx in NUMERIC_PROVINCE_PATTERNS.items():
        matches = list(rx.finditer(text))
        field_labels = None
        if prov == "yt":
            field_labels = build_field_label_index(
                text,
                negative_labels=NON_DOB_DATE_LABELS,
                value_spans=(match.span() for match in matches),
            )
        for m in matches:
            if overlaps_claimed(m.start(), m.end()):
                continue
            if not _is_standalone_token(text, m.start(), m.end()):
                continue
            if prov == "yt" and associate_field_label(
                text,
                m.start(),
                m.end(),
                negative_labels=NON_DOB_DATE_LABELS,
                label_index=field_labels,
            ).decision == "negative":
                continue
            if has_keyword(m.start(), m.end()) and province_in(
                m.start(), m.end(), prov
            ):
                add(f"drivers_license_{prov}", m.group(0))
                claimed.append((m.start(), m.end()))

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
    pprint(detect_drivers_licenses(
        "Ontario driver's licence A1234-56789-01234\n"
        "Saskatchewan licence number 12345678\n"
        "driver's licence 1234567 (province unknown)\n"
        "Random bare number 1234567 with no context\n"
        "Call me at 403-555-0123\n"
    ))
