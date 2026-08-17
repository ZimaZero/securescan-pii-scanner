#!/usr/bin/env python3
# detectors/mrz_detector.py
"""
Machine Readable Zone (MRZ) Detector — ICAO 9303 checksum-validated (standalone)
==================================================================================
Recognizes the machine-readable zone printed on passports (TD3, 2 lines x 44
chars), national ID / PR cards (TD1, 3 lines x 30 chars), and, cheaply, visa/
TD2-style documents (2 lines x 36 chars). MRZ is a highest-trust identifier
class: every date/number field carries its own ICAO 9303 check digit, so a
match is checksum-validated rather than only format-shaped.

Output shape matches the other detectors, extended with a metadata dict per
finding (issuing state / document type / MRZ format):

    { type: [(value, confidence, metadata), ...], ... }

Pipeline
--------
1. Candidate lines: uppercase, strip spaces, normalize odd/full-width unicode
   to ASCII (NFKC), then require the line be composed only of [A-Z0-9<] and
   land within +/-2 chars of a known MRZ line length (30 / 36 / 44). A line
   that's too short is retried joined with the next physical line, in case
   OCR line-wrapped a single MRZ line into two.
2. A block requires >=2 consecutive candidate lines for TD2/TD3 (both lines
   mandatory — every emitted field for those formats lives in line 2 anyway,
   but a lone 36/44-char line proves nothing on its own) or >=2 of 3 for TD1
   (document/date lines carry all checksummed fields; the name line does not
   feed any checksum, so it's corroborating evidence, not a requirement).
3. Per-field OCR-noise normalization BEFORE parsing, position-aware:
   - Strictly numeric ICAO fields (DOB, expiry, every single check-digit
     position) get an unconditional letter->digit fix (O/Q->0, I/L->1, Z->2,
     S->5, B->8, G->6) — those letters can never legitimately appear there.
   - Strictly alpha fields (issuing state, nationality, doc type, names) get
     the inverse digit->letter fix (0->O, 1->I, 5->S, 8->B).
   - Alphanumeric fields (document number, personal number, optional data)
     are NOT force-rewritten — letters are legitimate content there. Instead,
     validation tries the raw field first and only retries with the numeric
     map as a repair if the raw checksum fails, accepting the repair only if
     it makes the check digit pass. This fixes OCR noise (e.g. an "O" read
     for a "0" in a document number) without mangling genuinely alphanumeric
     document numbers.
   - A lone 'K' adjacent to a real '<' is treated as a misread filler chevron
     (OCR-B's '<' and 'K' are visually close once a run of chevrons gets
     compressed) and normalized to '<' before field slicing.
4. Check digits validated with the standard ICAO 7-3-1 weighted algorithm
   (A=10..Z=35, <=0, weights cycle 7/3/1 over the field's characters).

Emission / trust
-----------------
- document number / DOB / expiry whose own check digit validates -> emitted
  as "mrz_document_number" / "mrz_dob" / "mrz_expiry" at confidence 0.95.
- document number whose check digit does NOT validate (even after repair),
  but which sits in a confirmed MRZ block -> emitted as "mrz_unverified"
  (mirrors health_card_detector's Tier-2 checksum-fails-but-still-signal
  pattern) at confidence 0.55. DOB/expiry get no such fallback: if they don't
  validate, they simply aren't emitted (no unverified date tier).
- A personal-number check-digit position of '<' means "not set" per ICAO
  9303 (personal number is optional) — that's a non-finding, not a failure.

`hybrid_detector.py` invokes this layer and applies source priority, taxonomy,
risk severity, and reconciliation.
"""

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

# ============================================================
#  CONFIGURATION
# ============================================================

VALIDATED_CONFIDENCE = 0.95   # check digit validates (possibly after OCR repair)
UNVERIFIED_CONFIDENCE = 0.55  # MRZ block confirmed, but check digit fails

# Ratified block-admission gates. A+B removed 32/32 measured false positives
# while retaining all 12 true MRZ findings across the anchor, specimen, and
# Enron corpora.
GATE_A_REQUIRE_CHEVRON = True
GATE_B_VALID_ISSUING_STATE = True

# Measured but not shipped. C had the weakest separation (23/32 false
# positives removed) because prose can trivially begin with an accepted
# document-type letter. D removed nothing beyond A and retained the Enron
# ALLNYMEXD/10NMEM pair: a random DOB check digit passes roughly one time in
# ten, so accidental date validation is not reliable corroboration.
GATE_C_VALID_DOC_TYPE = False
GATE_D_CORROBORATED_UNVERIFIED = False

TD1_LINE_LEN = 30
TD2_LINE_LEN = 36
TD3_LINE_LEN = 44
LEN_TOLERANCE = 2

_MRZ_CHARS_RE = re.compile(r"^[A-Z0-9<]+$")
_CHECK_WEIGHTS = (7, 3, 1)

# ISO 3166-1 alpha-3 codes, sourced from the ISO Online Browsing Platform
# country-code list (https://www.iso.org/obp/ui/#search/code/), plus the ICAO
# Doc 9303 special issuing-state codes accepted by this gate. `D` is ICAO's
# nationality/issuing-state code for Germany.
VALID_ISSUING_STATES = frozenset({
    'ABW', 'AFG', 'AGO', 'AIA', 'ALA', 'ALB', 'AND', 'ARE', 'ARG', 'ARM', 'ASM', 'ATA', 'ATF', 'ATG',
    'AUS', 'AUT', 'AZE', 'BDI', 'BEL', 'BEN', 'BES', 'BFA', 'BGD', 'BGR', 'BHR', 'BHS', 'BIH', 'BLM',
    'BLR', 'BLZ', 'BMU', 'BOL', 'BRA', 'BRB', 'BRN', 'BTN', 'BVT', 'BWA', 'CAF', 'CAN', 'CCK', 'CHE',
    'CHL', 'CHN', 'CIV', 'CMR', 'COD', 'COG', 'COK', 'COL', 'COM', 'CPV', 'CRI', 'CUB', 'CUW', 'CXR',
    'CYM', 'CYP', 'CZE', 'DEU', 'DJI', 'DMA', 'DNK', 'DOM', 'DZA', 'ECU', 'EGY', 'ERI', 'ESH', 'ESP',
    'EST', 'ETH', 'FIN', 'FJI', 'FLK', 'FRA', 'FRO', 'FSM', 'GAB', 'GBR', 'GEO', 'GGY', 'GHA', 'GIB',
    'GIN', 'GLP', 'GMB', 'GNB', 'GNQ', 'GRC', 'GRD', 'GRL', 'GTM', 'GUF', 'GUM', 'GUY', 'HKG', 'HMD',
    'HND', 'HRV', 'HTI', 'HUN', 'IDN', 'IMN', 'IND', 'IOT', 'IRL', 'IRN', 'IRQ', 'ISL', 'ISR', 'ITA',
    'JAM', 'JEY', 'JOR', 'JPN', 'KAZ', 'KEN', 'KGZ', 'KHM', 'KIR', 'KNA', 'KOR', 'KWT', 'LAO', 'LBN',
    'LBR', 'LBY', 'LCA', 'LIE', 'LKA', 'LSO', 'LTU', 'LUX', 'LVA', 'MAC', 'MAF', 'MAR', 'MCO', 'MDA',
    'MDG', 'MDV', 'MEX', 'MHL', 'MKD', 'MLI', 'MLT', 'MMR', 'MNE', 'MNG', 'MNP', 'MOZ', 'MRT', 'MSR',
    'MTQ', 'MUS', 'MWI', 'MYS', 'MYT', 'NAM', 'NCL', 'NER', 'NFK', 'NGA', 'NIC', 'NIU', 'NLD', 'NOR',
    'NPL', 'NRU', 'NZL', 'OMN', 'PAK', 'PAN', 'PCN', 'PER', 'PHL', 'PLW', 'PNG', 'POL', 'PRI', 'PRK',
    'PRT', 'PRY', 'PSE', 'PYF', 'QAT', 'REU', 'ROU', 'RUS', 'RWA', 'SAU', 'SDN', 'SEN', 'SGP', 'SGS',
    'SHN', 'SJM', 'SLB', 'SLE', 'SLV', 'SMR', 'SOM', 'SPM', 'SRB', 'SSD', 'STP', 'SUR', 'SVK', 'SVN',
    'SWE', 'SWZ', 'SXM', 'SYC', 'SYR', 'TCA', 'TCD', 'TGO', 'THA', 'TJK', 'TKL', 'TKM', 'TLS', 'TON',
    'TTO', 'TUN', 'TUR', 'TUV', 'TWN', 'TZA', 'UGA', 'UKR', 'UMI', 'URY', 'USA', 'UZB', 'VAT', 'VCT',
    'VEN', 'VGB', 'VIR', 'VNM', 'VUT', 'WLF', 'WSM', 'YEM', 'ZAF', 'ZMB', 'ZWE',
    'UNO', 'UNA', 'UNK', 'XOM', 'XCC', 'XXA', 'XXB', 'XXC', 'XXX',
    'GBD', 'GBN', 'GBO', 'GBP', 'GBS', 'RKS', 'D',
})

# OCR-noise correction maps (ICAO 9303 Part 3, Section 4.9 lists these as the
# canonical confusable pairs for OCR-B).
NUMERIC_FIX = {"O": "0", "Q": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6"}
ALPHA_FIX = {"0": "O", "1": "I", "5": "S", "8": "B"}

# Field layouts: (start, end, kind) 0-indexed half-open spans over a
# normalized (space-stripped, uppercased) line. kind is one of:
#   "numeric" - force letter->digit fix (dates, check digits)
#   "alpha"   - force digit->letter fix (state/nationality/type/name)
#   "mixed"   - alphanumeric by spec, no forced rewrite (doc/personal number)
#   "sex"     - left as-is (M / F / <)
TD3_LINE1_FIELDS = [(0, 2, "alpha"), (2, 5, "alpha"), (5, 44, "alpha")]
TD3_LINE2_FIELDS = [
    (0, 9, "mixed"), (9, 10, "numeric"), (10, 13, "alpha"),
    (13, 19, "numeric"), (19, 20, "numeric"), (20, 21, "sex"),
    (21, 27, "numeric"), (27, 28, "numeric"), (28, 42, "mixed"),
    (42, 43, "numeric"), (43, 44, "numeric"),
]

TD2_LINE1_FIELDS = [(0, 2, "alpha"), (2, 5, "alpha"), (5, 36, "alpha")]
TD2_LINE2_FIELDS = [
    (0, 9, "mixed"), (9, 10, "numeric"), (10, 13, "alpha"),
    (13, 19, "numeric"), (19, 20, "numeric"), (20, 21, "sex"),
    (21, 27, "numeric"), (27, 28, "numeric"), (28, 35, "mixed"),
    (35, 36, "numeric"),
]

TD1_LINE1_FIELDS = [(0, 2, "alpha"), (2, 5, "alpha"), (5, 14, "mixed"), (14, 15, "numeric"), (15, 30, "mixed")]
TD1_LINE2_FIELDS = [
    (0, 6, "numeric"), (6, 7, "numeric"), (7, 8, "sex"),
    (8, 14, "numeric"), (14, 15, "numeric"), (15, 18, "alpha"),
    (18, 29, "mixed"), (29, 30, "numeric"),
]


# ============================================================
#  CHECK DIGIT ALGORITHM (ICAO 9303, 7-3-1 weighted)
# ============================================================

def _char_value(c: str) -> int:
    if c == "<":
        return 0
    if c.isdigit():
        return int(c)
    if "A" <= c <= "Z":
        return ord(c) - ord("A") + 10
    return -1


def compute_check_digit(field: str) -> Optional[int]:
    """7-3-1 weighted check digit. None if `field` has a non-MRZ char."""
    total = 0
    for i, c in enumerate(field):
        v = _char_value(c)
        if v < 0:
            return None
        total += v * _CHECK_WEIGHTS[i % 3]
    return total % 10


def _norm_check_char(c: str) -> str:
    if c.isdigit():
        return c
    return NUMERIC_FIX.get(c, c)


def _validate_with_repair(field: str, check_char: str) -> Tuple[Optional[bool], str, Optional[int]]:
    """Validate `field` against its check digit `check_char`.

    Returns (valid, display_value, expected_check_digit):
      - valid=True  -> matches, possibly only after the numeric OCR-repair map
      - valid=False -> does not match even after repair
      - valid=None  -> check digit position is '<' (unset — legitimate for
        the optional personal-number sub-field), not a pass or a fail
    """
    display = field.replace("<", "")
    if check_char == "<":
        return None, display, None

    check_norm = _norm_check_char(check_char)
    if not check_norm.isdigit():
        return False, display, None

    target = int(check_norm)
    expected = compute_check_digit(field)
    if expected is not None and expected == target:
        return True, display, expected

    # Repair one ambiguous character at a time rather than rewriting the
    # whole field at once: a mixed alphanumeric field (document / personal
    # number) can legitimately contain letters that also happen to be OCR-
    # confusable digits (e.g. a real "B" in the number), so blindly mapping
    # every eligible letter in the field risks mangling a genuine character.
    # Accepting a single-position fix that makes the checksum pass is a much
    # narrower, more defensible correction.
    for i, c in enumerate(field):
        if c not in NUMERIC_FIX:
            continue
        repaired = field[:i] + NUMERIC_FIX[c] + field[i + 1:]
        expected_r = compute_check_digit(repaired)
        if expected_r is not None and expected_r == target:
            return True, repaired.replace("<", ""), expected_r

    return False, display, expected


# ============================================================
#  LINE PREP / CANDIDATE DETECTION
# ============================================================

def _prep_line(raw: str) -> str:
    s = unicodedata.normalize("NFKC", raw).upper()
    return re.sub(r"\s+", "", s)


def _prep_lines(text: str) -> List[str]:
    return [_prep_line(line) for line in text.splitlines()]


def _is_candidate(s: str, target_len: int, tolerance: int = LEN_TOLERANCE) -> bool:
    if not s or not (target_len - tolerance <= len(s) <= target_len + tolerance):
        return False
    return bool(_MRZ_CHARS_RE.match(s))


def _candidate_for_line(lines: List[str], idx: int, target_len: int):
    """Return ((start_idx, end_idx), logical_line) or None. Tries the line
    alone first; if it's too short but otherwise MRZ-charset-clean, retries
    joined with the following physical line (OCR line-split repair)."""
    if idx >= len(lines):
        return None
    single = lines[idx]
    if _is_candidate(single, target_len):
        return (idx, idx), single
    if (
        single
        and _MRZ_CHARS_RE.match(single)
        and len(single) < target_len - LEN_TOLERANCE
        and idx + 1 < len(lines)
    ):
        joined = single + lines[idx + 1]
        if _is_candidate(joined, target_len):
            return (idx, idx + 1), joined
    return None


def _find_pair_blocks(lines: List[str], target_len: int):
    """TD2/TD3: both lines mandatory."""
    blocks = []
    i, n = 0, len(lines)
    while i < n:
        first = _candidate_for_line(lines, i, target_len)
        if first:
            span1, s1 = first
            second = _candidate_for_line(lines, span1[1] + 1, target_len)
            if second:
                span2, s2 = second
                blocks.append((s1, s2))
                i = span2[1] + 1
                continue
        i += 1
    return blocks


def _find_triple_blocks(lines: List[str], target_len: int, min_required: int = 2):
    """TD1: 2-of-3 consecutive lines required.

    Role slot 0 ("line1") is anchored on the first line that actually
    matches as a candidate, found by scanning forward and skipping
    non-candidate lines for free — a line that fails the charset/length
    test outright can never be role0, role1, or role2 of a real MRZ line,
    so it must never consume a role slot. Only ONCE role0 is anchored do
    the scanner inspects the next two physical positions for role1/role2,
    tolerating a single missing/unreadable line there (the 2-of-3 rule).

    This matters because a naive "try role0/1/2 at whatever the current
    scan position is" approach conflates two different situations: "line1
    is genuinely absent from this block" vs. "the scan simply started one
    line too early, on unrelated text immediately before the block". Both
    look identical if you only track how many roles matched — but treating
    the second case as the first shifts the block's real line1 into the
    "line2" role slot (and line2 into "line3"), so every field is parsed
    at the wrong byte offsets and nothing validates. Anchoring role0 to an
    actual match — rather than to an arbitrary scan position — keeps role
    slots aligned to true MRZ line numbers regardless of how much
    unrelated text precedes the block.
    """
    blocks = []
    i, n = 0, len(lines)
    while i < n:
        anchor = _candidate_for_line(lines, i, target_len)
        if not anchor:
            i += 1
            continue
        span0, s0 = anchor
        roles = [s0]
        matched = 1
        cursor = span0[1] + 1
        last_end = span0[1]
        for _ in range(2):
            cand = _candidate_for_line(lines, cursor, target_len) if cursor < n else None
            if cand:
                span, s = cand
                roles.append(s)
                matched += 1
                last_end = span[1]
                cursor = span[1] + 1
            else:
                roles.append(None)
                last_end = max(last_end, cursor)
                cursor += 1
        if matched >= min_required:
            blocks.append(tuple(roles))
            i = last_end + 1
        else:
            i += 1
    return blocks


# ============================================================
#  OCR NORMALIZATION
# ============================================================

def _fix_k_filler(line: str) -> str:
    chars = list(line)
    n = len(chars)
    for i, c in enumerate(chars):
        if c != "K":
            continue
        left = chars[i - 1] if i > 0 else ""
        right = chars[i + 1] if i + 1 < n else ""
        if left == "<" or right == "<":
            chars[i] = "<"
    return "".join(chars)


def _apply_field_normalization(line: str, field_spec) -> str:
    chars = list(_fix_k_filler(line))
    for start, end, kind in field_spec:
        for i in range(start, min(end, len(chars))):
            c = chars[i]
            if kind == "numeric" and c in NUMERIC_FIX:
                chars[i] = NUMERIC_FIX[c]
            elif kind == "alpha" and c in ALPHA_FIX:
                chars[i] = ALPHA_FIX[c]
    return "".join(chars)


# ============================================================
#  LAYOUT PARSERS
# ============================================================

def _parse_td3(line1: Optional[str], line2: Optional[str]) -> Dict:
    result = {"format": "TD3"}
    if line1:
        norm1 = _apply_field_normalization(line1, TD3_LINE1_FIELDS)
        result["doc_type"] = norm1[0:2].replace("<", "")
        result["issuing_state"] = norm1[2:5].replace("<", "")
    if line2:
        norm2 = _apply_field_normalization(line2, TD3_LINE2_FIELDS)
        result["nationality"] = norm2[10:13].replace("<", "")
        result["sex"] = norm2[20]
        result["doc_number"] = _field_result(*_validate_with_repair(norm2[0:9], norm2[9]))
        result["dob"] = _field_result(*_validate_with_repair(norm2[13:19], norm2[19]))
        result["expiry"] = _field_result(*_validate_with_repair(norm2[21:27], norm2[27]))
    return result


def _parse_td2(line1: Optional[str], line2: Optional[str]) -> Dict:
    result = {"format": "TD2"}
    if line1:
        norm1 = _apply_field_normalization(line1, TD2_LINE1_FIELDS)
        result["doc_type"] = norm1[0:2].replace("<", "")
        result["issuing_state"] = norm1[2:5].replace("<", "")
    if line2:
        norm2 = _apply_field_normalization(line2, TD2_LINE2_FIELDS)
        result["nationality"] = norm2[10:13].replace("<", "")
        result["sex"] = norm2[20]
        result["doc_number"] = _field_result(*_validate_with_repair(norm2[0:9], norm2[9]))
        result["dob"] = _field_result(*_validate_with_repair(norm2[13:19], norm2[19]))
        result["expiry"] = _field_result(*_validate_with_repair(norm2[21:27], norm2[27]))
    return result


def _parse_td1(line1: Optional[str], line2: Optional[str], line3: Optional[str]) -> Dict:
    result = {"format": "TD1"}
    if line1:
        norm1 = _apply_field_normalization(line1, TD1_LINE1_FIELDS)
        result["doc_type"] = norm1[0:2].replace("<", "")
        result["issuing_state"] = norm1[2:5].replace("<", "")
        result["doc_number"] = _field_result(*_validate_with_repair(norm1[5:14], norm1[14]))
    if line2:
        norm2 = _apply_field_normalization(line2, TD1_LINE2_FIELDS)
        result["sex"] = norm2[7]
        result["nationality"] = norm2[15:18].replace("<", "")
        result["dob"] = _field_result(*_validate_with_repair(norm2[0:6], norm2[6]))
        result["expiry"] = _field_result(*_validate_with_repair(norm2[8:14], norm2[14]))
    # line3 (name) feeds no checksum and is not used for any emitted field.
    return result


def _field_result(valid, value, expected):
    return {"valid": valid, "value": value, "expected_check_digit": expected}


def _block_admitted(parsed: Dict, block_lines: Tuple[Optional[str], ...]) -> bool:
    """Apply independently toggleable block-admission measurement gates."""
    present_lines = tuple(line for line in block_lines if line)
    if GATE_A_REQUIRE_CHEVRON and not any("<" in line for line in present_lines):
        return False
    if (
        GATE_B_VALID_ISSUING_STATE
        and parsed.get("issuing_state") not in VALID_ISSUING_STATES
    ):
        return False
    if GATE_C_VALID_DOC_TYPE:
        doc_type = parsed.get("doc_type") or ""
        first = doc_type[:1]
        if parsed.get("format") == "TD3":
            if first != "P":
                return False
        elif first not in {"I", "A", "C", "V"}:
            return False
    return True


# ============================================================
#  MAIN DETECTOR
# ============================================================

def _emit_parsed(add, parsed: Dict):
    meta = {
        "format": parsed.get("format"),
        "issuing_state": parsed.get("issuing_state"),
        "doc_type": parsed.get("doc_type"),
    }

    doc = parsed.get("doc_number")
    if doc and doc["value"]:
        if doc["valid"] is True:
            add("mrz_document_number", doc["value"], VALIDATED_CONFIDENCE, meta)
        elif doc["valid"] is False and (
            not GATE_D_CORROBORATED_UNVERIFIED
            or (parsed.get("dob") or {}).get("valid") is True
            or (parsed.get("expiry") or {}).get("valid") is True
        ):
            add("mrz_unverified", doc["value"], UNVERIFIED_CONFIDENCE, meta)

    dob = parsed.get("dob")
    if dob and dob["valid"] is True and dob["value"]:
        add("mrz_dob", dob["value"], VALIDATED_CONFIDENCE, meta)

    expiry = parsed.get("expiry")
    if expiry and expiry["valid"] is True and expiry["value"]:
        add("mrz_expiry", expiry["value"], VALIDATED_CONFIDENCE, meta)


def detect_mrz(text: str) -> Dict[str, List[Tuple[str, float, Dict]]]:
    """Detect MRZ blocks. Returns {type: [(value, confidence, metadata), ...]}."""
    if not isinstance(text, str) or not text.strip():
        return {}

    lines = _prep_lines(text)
    findings: Dict[str, List[Tuple[str, float, Dict]]] = {}

    def add(stype, value, conf, meta):
        findings.setdefault(stype, []).append((value, conf, meta))

    for line1, line2 in _find_pair_blocks(lines, TD3_LINE_LEN):
        parsed = _parse_td3(line1, line2)
        if _block_admitted(parsed, (line1, line2)):
            _emit_parsed(add, parsed)

    for line1, line2 in _find_pair_blocks(lines, TD2_LINE_LEN):
        parsed = _parse_td2(line1, line2)
        if _block_admitted(parsed, (line1, line2)):
            _emit_parsed(add, parsed)

    for line1, line2, line3 in _find_triple_blocks(lines, TD1_LINE_LEN, min_required=2):
        parsed = _parse_td1(line1, line2, line3)
        if _block_admitted(parsed, (line1, line2, line3)):
            _emit_parsed(add, parsed)

    # ---- Dedup per type by value (keep highest confidence), same pattern
    # as health_card_detector / passport_detector ----
    result: Dict[str, List[Tuple[str, float, Dict]]] = {}
    for stype, items in findings.items():
        best: Dict[str, Tuple[float, Dict]] = {}
        for value, conf, meta in items:
            if value not in best or conf > best[value][0]:
                best[value] = (conf, meta)
        result[stype] = [(v, c, m) for v, (c, m) in best.items()]
    return result


if __name__ == "__main__":
    from pprint import pprint
    # Synthetic TD3 sample with hand-computed check digits (no real personal
    # data). Doc number "L898902C3" is the canonical ICAO 9303 worked example.
    sample = (
        "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<\n"
        "L898902C36UTO7408122F1204159ZE184226B<<<<<10\n"
    )
    pprint(detect_mrz(sample))
