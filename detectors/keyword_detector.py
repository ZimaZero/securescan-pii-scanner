#!/usr/bin/env python3
# keyword_detector.py
"""
Keyword-Based Context Detector (Optimized & Safe)

Purpose:
--------
Detects contextual PII with fuzzy keyword detection and pattern matching.
Prevents false positives through validation + proximity checks.
"""

from datetime import date, datetime
from typing import Dict, List, Tuple, Optional
from difflib import SequenceMatcher
import bisect
import importlib
import re
import threading

from detectors.field_label_association import (
    DOB_FIELD_LABELS,
    NON_DOB_DATE_LABELS,
    associate_field_label,
    build_field_label_index,
)

try:
    from rapidfuzz import fuzz as _rapidfuzz_fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False

# ============================================================
# KEYWORD DEFINITIONS (Canadian + US)
# ============================================================

CONTEXT_KEYWORDS = {
    "sin": {
        "keywords": ["sin", "social insurance", "social insurance number", "s.i.n"],
        "patterns": [
            r"\b\d{3}[-\s]?\d{3}[-\s]?\d{3}\b",  # 123-456-789 OR 123 456 789
        ],
        "confidence": 0.85,
    },
    "ssn": {
        "keywords": ["ssn", "social security", "social security number", "s.s.n"],
        "patterns": [
            r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b",  # 123-45-6789
        ],
        "confidence": 0.85,
    },
    "dob": {
        "keywords": ["dob", "d.o.b", "date of birth", "birth date", "birthday", "born"],
        "patterns": [
            r"\b(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b",  # YYYY-MM-DD
            r"\b\d{1,2}[-/]\d{1,2}[-/](?:19|20)\d{2}\b",  # DD/MM/YYYY
        ],
        "confidence": 0.85,
        "window": 30,
    },
    "email": {
        "keywords": ["email", "e-mail", "emall", "ernail", "e mail"],
        "patterns": [r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"],
        "confidence": 0.90,
    },
    "phone": {
        "keywords": ["phone", "telephone", "tel", "mobile", "cell", "phane", "fone", "call", "fax"],
        # Separators required — same rationale as PHONE_FORMATTED_RE in
        # detectors.py: a fully-optional-separator pattern lets ANY bare
        # 10-digit run within this type's 100-char keyword window match
        # (e.g. an unrelated ID number that happens to share a paragraph
        # with the word "phone"), which is a much looser check than
        # detectors.py's own tight 30-char bare-digit-run gate. Bare
        # digit+keyword phone detection is exclusively detectors.py's job
        # now — this pattern only needs to catch FORMATTED numbers that
        # happen to sit near a keyword.
        "patterns": [
            r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b",
            r"\(\d{3}\)\s?\d{3}[-.\s]?\d{4}",
        ],
        "confidence": 0.85,
    },
    "credit_card": {
        "keywords": ["credit card", "card number", "cc", "visa", "mastercard", "amex"],
        "patterns": [r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"],
        "confidence": 0.80,
    },
    # NOTE: health_card and drivers_license are intentionally NOT handled here.
    # They are owned by the dedicated detectors (health_card_detector.py,
    # drivers_license_detector.py), which the hybrid layer reconciles against the
    # stronger signals. Keeping loose \d{9}/\d{10}/[A-Z]\d{14} patterns here only
    # produced duplicate, lower-quality hits.
    "postal_code_ca": {
        "keywords": ["postal", "postal code", "zip", "postcode"],
        "patterns": [r"\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b"],
        "confidence": 0.80,
    },
}

_ASSOCIATED_DATE_PATTERNS = (
    r"(?<!\d)(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}(?!\d)",
    r"(?<!\d)\d{1,2}[-/]\d{1,2}[-/](?:19|20)\d{2}(?!\d)",
)

DOCUMENT_TYPES = {
    "employment": [
        "employee",
        "employer",
        "payroll",
        "salary",
        "wage",
        "benefits",
        "employment",
        "hr",
        "human resources",
        "personnel",
        "staff",
    ],
    "medical": [
        "patient",
        "doctor",
        "physician",
        "hospital",
        "clinic",
        "diagnosis",
        "prescription",
        "treatment",
        "medical",
        "health",
        "healthcare",
    ],
    "financial": [
        "account",
        "balance",
        "transaction",
        "statement",
        "bank",
        "banking",
        "credit",
        "debit",
        "payment",
        "invoice",
        "receipt",
    ],
    "tax": [
        "tax",
        "irs",
        "cra",
        "t4",
        "w-2",
        "1040",
        "return",
        "income tax",
        "gst",
        "hst",
        "provincial tax",
    ],
    "government": [
        "passport",
        "citizenship",
        "immigration",
        "visa",
        "government",
        "federal",
        "provincial",
        "ministry",
        "department",
    ],
    "educational": [
        "student",
        "university",
        "college",
        "school",
        "grade",
        "transcript",
        "diploma",
        "degree",
        "education",
        "academic",
    ],
}

TEST_INDICATORS = [
    "test",
    "demo",
    "example",
    "sample",
    "fake",
    "dummy",
    "do not use",
    "for testing",
    "test data",
    "placeholder",
]
NEGATIVE_CONTEXT = [
    "example",
    "sample",
    "template",
    "format",
    "specimen",
    "dummy",
    "fake",
    "not real",
    "placeholder",
    "format like",
    "such as",
    "e.g.",
    "guide",
    "demo",
]

# Explicit negation phrases: a value the text itself calls NOT real PII (e.g.
# "Support PIN 078051121 (looks like an SSN, is NOT)"). SHORT and high-precision
# on purpose — only unambiguous negations, no general negation parsing.
NEGATION_PHRASES = [
    "is not", "not a", "not an", "not a real",
    "does not", "do not confuse", "looks like", "resembles",
]

# A negation grammatically attaches to the value it describes, so it is checked
# only in a TIGHT window — otherwise a negation about a *different* item nearby
# (e.g. a "Do NOT confuse these..." header two lines away) would suppress a real
# value. Placeholder terms describe the whole passage, so they stay on the wide
# per-type window `w`.
NEGATION_WINDOW = 40

# Placeholder / example terms. A value sitting near any of these (e.g.
# "SSN 123-45-6789 (example)") is illustrative, not real PII, and is suppressed.
# Matched on word boundaries — (?<![a-z0-9]) / (?![a-z0-9]) — so "format" does
# NOT match inside "information", nor "test" inside "latest".
_PLACEHOLDER_TERMS = sorted(set(NEGATIVE_CONTEXT) | set(TEST_INDICATORS))
_PLACEHOLDER_RE = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(t) for t in _PLACEHOLDER_TERMS) + r")(?![a-z0-9])",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(t) for t in NEGATION_PHRASES) + r")(?![a-z0-9])",
    re.IGNORECASE,
)


# Compiled keyword locator. The production backend is pyahocorasick (selected
# by the 2026-07-20 three-corpus parity benchmark); the single-regex locator is
# the benchmarked, zero-drift fallback when that optional native import cannot
# be loaded at runtime.
_ALL_KEYWORDS = sorted(
    {kw for cfg in CONTEXT_KEYWORDS.values() for kw in cfg["keywords"]},
    key=lambda value: (-len(value), value),
)
_KEYWORD_TYPES: Dict[str, List[str]] = {}
for _pii_type, _cfg in CONTEXT_KEYWORDS.items():
    for _keyword in _cfg["keywords"]:
        _KEYWORD_TYPES.setdefault(_keyword, []).append(_pii_type)

_KEYWORD_ALTERNATION_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(keyword) for keyword in _ALL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"\b\w+\b")
_AHO_AUTOMATON = None
_AHO_UNAVAILABLE = False
_AHO_WARNING_EMITTED = False
_AHO_LOCK = threading.Lock()


# Email/URL shapes reused here (same patterns as CONTEXT_KEYWORDS["email"] and
# detectors.py's URL_RE) so placeholder-term matching can mask them out of the
# window first: "example" inside "user@example.com" or "format" inside
# "format.test@..." is part of an address/URL, not an illustrative word, and
# must not trigger suppression of a genuinely nearby value (e.g. a postal
# code sitting a few chars after a realistic-looking @example.com address).
_EMAIL_SHAPE_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_URL_SHAPE_RE = re.compile(r"https?://[^\s<>\"']+")


def _mask_email_and_url_spans(text: str) -> str:
    """Blank out email/URL spans (same length, so offsets are unaffected)."""
    text = _EMAIL_SHAPE_RE.sub(lambda m: " " * len(m.group(0)), text)
    text = _URL_SHAPE_RE.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _has_placeholder_context(window: str) -> bool:
    """True if a placeholder/example term sits in the (wide) window around a
    value — ignoring placeholder-shaped substrings that are actually part of
    an email address or URL rather than standalone illustrative prose."""
    return _PLACEHOLDER_RE.search(_mask_email_and_url_spans(window)) is not None


def _has_negation(window: str) -> bool:
    """True if an explicit negation phrase sits in the (tight) window around a value."""
    return _NEGATION_RE.search(window) is not None


# ============================================================
# VALIDATION FUNCTIONS
# ============================================================


def luhn_check(number_str: str) -> bool:
    try:
        digits = [int(ch) for ch in number_str if ch.isdigit()]
    except:
        return False
    if len(digits) < 9:
        return False
    total = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def validate_sin(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return len(digits) == 9 and digits[0] not in ("0", "8") and luhn_check(digits)


def validate_ssn(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 9:
        return False
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    return not (
        area in ("000", "666") or int(area) >= 900 or group == "00" or serial == "0000"
    )


# ============================================================
# FUZZY + CONTEXT MATCHING
# ============================================================


def fuzzy_match(a: str, b: str, t: float = 0.80) -> bool:
    if _HAS_RAPIDFUZZ:
        return _rapidfuzz_fuzz.ratio(a, b) >= t * 100
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= t


def has_context_keyword_near(
    text: str,
    pii_type: str,
    target_start: int,
    window: Optional[int] = None,
) -> bool:
    """Return whether this layer's keyword vocabulary is near a source span.

    This is the lightweight, position-only form of the keyword layer's context
    lookup. It uses the same exact/fuzzy rules and per-type window, but does not
    run the type's value patterns or its placeholder/negation suppression. The
    regex SIN detector uses it as evidence that an otherwise embedded,
    checksum-valid nine-digit run was intentionally labelled as a SIN.
    """
    if not isinstance(text, str) or pii_type not in CONTEXT_KEYWORDS:
        return False
    if not isinstance(target_start, int) or target_start < 0:
        return False

    cfg = CONTEXT_KEYWORDS[pii_type]
    distance = cfg.get("window", 100) if window is None else window
    if not isinstance(distance, int) or distance < 0:
        return False

    keywords = cfg["keywords"]
    longest = max(map(len, keywords), default=0)
    region_start = max(0, target_start - distance)
    region_end = min(len(text), target_start + distance + longest)
    lowered = text[region_start:region_end].lower()

    for keyword in keywords:
        for match in re.finditer(r"\b" + re.escape(keyword) + r"\b", lowered):
            absolute_start = region_start + match.start()
            if abs(absolute_start - target_start) <= distance:
                return True

    # Match the existing fuzzy contract: aliases of three characters or fewer
    # are exact-only because fuzzy short tokens are collision-prone.
    for match in _WORD_RE.finditer(lowered):
        absolute_start = region_start + match.start()
        if abs(absolute_start - target_start) > distance:
            continue
        word = match.group(0)
        for keyword in keywords:
            if len(keyword) <= 3 or abs(len(word) - len(keyword)) > 2:
                continue
            if SequenceMatcher(None, word, keyword).real_quick_ratio() < 0.80:
                continue
            if fuzzy_match(word, keyword):
                return True
    return False


def _is_word_character(char: str) -> bool:
    r"""Match Python regex ``\w`` semantics for Aho boundary checks."""
    return char == "_" or char.isalnum()


def _compiled_regex_positions(lowered: str) -> Dict[str, List[Tuple[int, str]]]:
    """Candidate A: locate exact keyword aliases with one compiled regex."""
    positions: Dict[str, List[Tuple[int, str]]] = {}
    for match in _KEYWORD_ALTERNATION_RE.finditer(lowered):
        keyword = match.group(0).lower()
        for pii_type in _KEYWORD_TYPES[keyword]:
            positions.setdefault(pii_type, []).append((match.start(), keyword))
    return positions


def _aho_automaton():
    """Return the cached automaton, or None after one visible fallback warning."""
    global _AHO_AUTOMATON, _AHO_UNAVAILABLE, _AHO_WARNING_EMITTED
    if _AHO_AUTOMATON is not None:
        return _AHO_AUTOMATON
    if _AHO_UNAVAILABLE:
        return None

    with _AHO_LOCK:
        if _AHO_AUTOMATON is not None:
            return _AHO_AUTOMATON
        if _AHO_UNAVAILABLE:
            return None
        try:
            ahocorasick = importlib.import_module("ahocorasick")
            automaton = ahocorasick.Automaton()
            for keyword in _ALL_KEYWORDS:
                automaton.add_word(keyword, keyword)
            automaton.make_automaton()
            _AHO_AUTOMATON = automaton
        except Exception as exc:
            _AHO_UNAVAILABLE = True
            if not _AHO_WARNING_EMITTED:
                print(
                    "[!] Aho-Corasick keyword matcher unavailable; "
                    f"using compiled-regex fallback ({exc.__class__.__name__}: {exc})"
                )
                _AHO_WARNING_EMITTED = True
        return _AHO_AUTOMATON


def _aho_positions(lowered: str) -> Optional[Dict[str, List[Tuple[int, str]]]]:
    """Candidate B: locate exact aliases using the cached Aho automaton."""
    automaton = _aho_automaton()
    if automaton is None:
        return None

    positions: Dict[str, List[Tuple[int, str]]] = {}
    for end, keyword in automaton.iter(lowered):
        start = end - len(keyword) + 1
        if start and _is_word_character(lowered[start - 1]):
            continue
        if end + 1 < len(lowered) and _is_word_character(lowered[end + 1]):
            continue
        for pii_type in _KEYWORD_TYPES[keyword]:
            positions.setdefault(pii_type, []).append((start, keyword))
    return positions


def _keyword_positions(
    text: str, *, force_backend: Optional[str] = None
) -> Tuple[str, Dict[str, List[Tuple[int, str]]]]:
    """Locate exact and fuzzy keywords after tokenizing the document once."""
    lowered = text.lower()
    if force_backend == "regex":
        positions = _compiled_regex_positions(lowered)
    else:
        positions = _aho_positions(lowered)
        if positions is None:
            positions = _compiled_regex_positions(lowered)

    word_positions: Dict[str, List[int]] = {}
    for match in _WORD_RE.finditer(lowered):
        word_positions.setdefault(match.group(0), []).append(match.start())

    for pii_type, cfg in CONTEXT_KEYWORDS.items():
        type_positions = positions.setdefault(pii_type, [])
        for keyword in cfg["keywords"]:
            if len(keyword) <= 3:
                continue
            for word, starts in word_positions.items():
                # These are safe upper-bound gates: neither can reject a pair
                # capable of reaching the existing 0.80 fuzzy threshold.
                if abs(len(word) - len(keyword)) > 2:
                    continue
                if SequenceMatcher(None, word, keyword).real_quick_ratio() < 0.80:
                    continue
                if fuzzy_match(word, keyword):
                    type_positions.extend((start, keyword) for start in starts)
        type_positions.sort(key=lambda item: item[0])
    return lowered, positions


def _candidate_intervals(
    text: str, positions: List[Tuple[int, str]], window: int
) -> List[Tuple[int, int]]:
    """Build merged verification windows without truncating boundary tokens."""
    intervals: List[Tuple[int, int]] = []
    text_length = len(text)
    for position, _keyword in positions:
        # The fixed padding covers every bounded numeric pattern. Extending
        # through the surrounding token prevents the Enron regression where
        # a window beginning inside an email manufactured a suffix finding.
        start = max(0, position - window - 64)
        while start > 0 and not text[start - 1].isspace():
            start -= 1
        end = min(text_length, position + window + 64)
        while end < text_length and not text[end].isspace():
            end += 1
        if intervals and start <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], end))
        else:
            intervals.append((start, end))
    return intervals


def _find_patterns_near_positions(
    text: str,
    lowered: str,
    positions: List[Tuple[int, str]],
    patterns: List[str],
    window: int,
):
    if not positions:
        return []

    sorted_positions = [position for position, _keyword in positions]
    results = []
    for pattern in patterns:
        compiled = re.compile(pattern, re.IGNORECASE)
        seen_spans = set()
        for start, end in _candidate_intervals(text, positions, window):
            for match in compiled.finditer(text, start, end):
                if match.span() in seen_spans:
                    continue
                seen_spans.add(match.span())
                value = match.group(0)
                real_pos = match.start()
                index = bisect.bisect_left(sorted_positions, real_pos)
                keyword = None
                for candidate in (index - 1, index):
                    if (
                        0 <= candidate < len(sorted_positions)
                        and abs(sorted_positions[candidate] - real_pos) <= window
                    ):
                        keyword = positions[candidate][1]
                        break
                if keyword is None:
                    continue

                wide = lowered[
                    max(0, real_pos - window) : real_pos + len(value) + window
                ]
                near = lowered[
                    max(0, real_pos - NEGATION_WINDOW) :
                    real_pos + len(value) + NEGATION_WINDOW
                ]
                if _has_placeholder_context(wide) or _has_negation(near):
                    continue
                results.append((value, keyword, 0.75))
    return list(dict.fromkeys(results))


def find_patterns_near_keywords(
    text: str, keywords: List[str], patterns: List[str], w: int = 100
):
    """Compatibility helper retaining the original standalone signature."""
    lowered = text.lower()

    # Tokenize the document once into {unique_word: [positions]}. Fuzzy
    # comparison then runs once per DISTINCT word (bounded by vocabulary
    # size) instead of once per occurrence (bounded by document length) —
    # on a multi-MB file with a small vocabulary that's orders of magnitude
    # fewer SequenceMatcher/rapidfuzz calls.
    word_positions: Dict[str, List[int]] = {}
    for m in re.finditer(r"\b\w+\b", lowered):
        word_positions.setdefault(m.group(0), []).append(m.start())

    # Keyword detection: exact for all keywords; fuzzy only for keywords longer
    # than 3 chars. Short keywords (sin, ssn, dob, cc, tel) are too collision
    # prone to fuzzy-match (e.g. "sins", "tell", any 2-char token).
    positions = []
    for kw in keywords:
        for m in re.finditer(r"\b" + re.escape(kw) + r"\b", lowered):
            positions.append((m.start(), kw))
        if len(kw) > 3:
            kw_len = len(kw)
            for word, starts in word_positions.items():
                # Cheap length prefilter before the fuzzy-match call: a ratio
                # >= 0.80 is impossible once the length delta is too large.
                if abs(len(word) - kw_len) > 2:
                    continue
                if fuzzy_match(word, kw):
                    positions.extend((start, kw) for start in starts)

    positions.sort(key=lambda pk: pk[0])
    return _find_patterns_near_positions(text, lowered, positions, patterns, w)


# ============================================================
# MAIN DETECTOR
# ============================================================


def _parse_calendar_date(value: str) -> Optional[date]:
    formats = ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y")
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def is_future_date(value: str, *, as_of_date: Optional[date] = None) -> bool:
    """Return whether a supported calendar date is after an injectable date."""
    reference = date.today() if as_of_date is None else as_of_date
    parsed = _parse_calendar_date(value)
    return parsed is not None and parsed > reference


def _find_dob_matches(text: str, lowered: str, *, as_of_date: Optional[date]):
    results = []
    date_matches = []
    for pattern in _ASSOCIATED_DATE_PATTERNS:
        date_matches.extend(re.finditer(pattern, text, re.IGNORECASE))
    date_matches.sort(key=lambda match: (match.start(), match.end()))
    label_index = build_field_label_index(
        text,
        positive_labels=DOB_FIELD_LABELS,
        negative_labels=NON_DOB_DATE_LABELS,
        value_spans=(match.span() for match in date_matches),
    )
    for match in date_matches:
        value = match.group(0)
        association = associate_field_label(
            text,
            match.start(),
            match.end(),
            positive_labels=DOB_FIELD_LABELS,
            negative_labels=NON_DOB_DATE_LABELS,
            label_index=label_index,
        )
        if is_future_date(value, as_of_date=as_of_date):
            continue
        if association.decision in ("negative", "none"):
            continue
        window = CONTEXT_KEYWORDS["dob"].get("window", 30)
        wide = lowered[
            max(0, match.start() - window) : match.end() + window
        ]
        near = lowered[
            max(0, match.start() - NEGATION_WINDOW) :
            match.end() + NEGATION_WINDOW
        ]
        if _has_placeholder_context(wide) or _has_negation(near):
            continue
        label = association.evidence[0].label if association.evidence else "dob"
        results.append((value, label, 0.75))
    return list(dict.fromkeys(results))


def _detect_pii_keywords(
    text: str,
    *,
    force_backend: Optional[str] = None,
    as_of_date: Optional[date] = None,
) -> Dict[str, any]:
    if not isinstance(text, str) or not text.strip():
        return {}

    results = {}
    lowered, all_positions = _keyword_positions(text, force_backend=force_backend)

    for pii_type, cfg in CONTEXT_KEYWORDS.items():
        if pii_type == "dob":
            matches = _find_dob_matches(text, lowered, as_of_date=as_of_date)
        else:
            matches = _find_patterns_near_positions(
                text,
                lowered,
                all_positions[pii_type],
                cfg["patterns"],
                cfg.get("window", 100),
            )
        # Dedup by normalized value (keep highest confidence) so the same value
        # matched via several keywords — e.g. "Emall" hitting both the "emall"
        # and fuzzy "email" keywords — is reported once.
        best: Dict[str, Tuple[str, float]] = {}

        for value, kw, base in matches:
            if pii_type == "sin" and not validate_sin(value):
                continue
            if pii_type == "ssn" and not validate_ssn(value):
                continue
            conf = base * cfg["confidence"]
            norm = value.strip().lower()
            if norm not in best or conf > best[norm][1]:
                best[norm] = (value, conf)

        if best:
            results[f"{pii_type}_context"] = list(best.values())

    return results


def detect_pii_keywords(
    text: str, *, as_of_date: Optional[date] = None
) -> Dict[str, any]:
    """Detect contextual PII using Aho-Corasick or its regex fallback."""
    return _detect_pii_keywords(text, as_of_date=as_of_date)


# ============================================================
# SELF TEST
# ============================================================

if __name__ == "__main__":
    samples = [
        "SIN: 132-677-360",  # Valid SIN (passes Luhn and the no-leading-0/8 rule)
        "SIN: 123456789",  # Invalid → MUST NOT detect
        "SSN: 123-45-6789",  # Valid
        "SSN: 000-12-3456",  # Invalid → MUST NOT detect
        "Employee data, Emall: john@acme.com",
    ]
    for t in samples:
        print(t, "→", detect_pii_keywords(t))
