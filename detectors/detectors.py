# detectors.py
"""
Deterministic regex and checksum detectors.

Supported detections:
- Email addresses
- Phone numbers
- Credit card numbers (validated via Luhn algorithm)
- Canadian SIN (9-digit)
- US SSN (3-2-4 format)
- Date-of-birth patterns

Uses strict patterns and validation rules and returns structured findings to
the hybrid detector.
"""

import re

# Reuse SIN validation (Luhn + prefix rule) and the phone keyword list from
# the keyword detector so there is a single source of truth. Dual import
# path: works both when this module is imported from inside detectors/ and
# from the project root.
try:
    from keyword_detector import (
        CONTEXT_KEYWORDS,
        has_context_keyword_near,
        validate_sin,
    )
except ImportError:
    from detectors.keyword_detector import (
        CONTEXT_KEYWORDS,
        has_context_keyword_near,
        validate_sin,
    )

PHONE_KEYWORDS = CONTEXT_KEYWORDS["phone"]["keywords"]

# ============================================================
#  REGEX DEFINITIONS
# ============================================================

EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[A-Za-z]{2,}\b")

# Separators (or literal parens around the area code) are the EVIDENCE that
# a digit run is formatted as a phone number, so they are required here, not
# optional — "403-555-1234" / "(403) 555-1234" / "403.555.1234" /
# "403 555 1234" all match at full confidence.
#
# A bare, separator-free 10-digit run ("4035551234") is NOT matched by this
# pattern on purpose: on its own it's weak evidence, indistinguishable from
# any other 10-digit ID. Two independent external evaluations hit real
# collisions from the old fully-optional-separator version of this regex —
# see tests/external_ai4privacy/EVALUATION.md (563 false positives, almost
# all a foreign 10-digit national-ID field) and
# tests/external_octopii_docs/EVALUATION.md (a Ukrainian passport's
# "Personal No." field misread as a phone number). Bare runs are handled
# separately by PHONE_BARE_RE below, gated on nearby keyword context instead
# of formatting — see detect_pii()'s PHONE section.
PHONE_FORMATTED_RE = re.compile(
    r"(?:\+1[-.\s]?)?"
    r"(?:"
    # (403) 555-1234 / (403)5551234 -- no leading \b: '(' is a non-word char,
    # so a \b almost never sits directly in front of it (both the preceding
    # character and '(' itself are typically non-word) — the literal '('
    # already anchors this branch unambiguously, \b isn't needed.
    r"\(\d{3}\)[-.\s]?\d{3}[-.\s]?\d{4}"
    r"|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}"  # 403-555-1234 / 403.555.1234 / 403 555 1234
    r")\b"
)

# Note: there is no separate bare 7-digit local-number pattern anywhere in
# this module — PHONE_FORMATTED_RE/PHONE_BARE_RE always require the 3-digit
# area code too, so the equivalent 7-digit collision doesn't arise here.
PHONE_BARE_RE = re.compile(r"\b\d{10}\b")
PHONE_BARE_CONTEXT_WINDOW = 30  # chars on each side to scan for a phone keyword

CREDITCARD_RE = re.compile(
    r"\b(?:\d[ -]*?){13,16}\b"  # 13–16 digits with optional spaces/hyphens
)

# Canadian SINs have exactly three valid written layouts: nine compact digits,
# three groups separated by hyphens, or three groups separated by spaces.
# Keep these as explicit alternatives: independently optional separators admit
# invalid groupings such as Alberta licence ``123456-789`` and mixed layouts.
# Dots are also invalid and made internal-IP fragments look SIN-shaped.
SIN_RE = re.compile(r"\b(?:\d{9}|\d{3}-\d{3}-\d{3}|\d{3} \d{3} \d{3})\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

DOB_RE = re.compile(
    r"\b(?:(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}"  # YYYY-MM-DD
    r"|\d{1,2}[-/]\d{1,2}[-/](?:19|20)\d{2})\b"  # DD/MM/YYYY
)

# IPv4 dotted-quad. Octet range (0-255) is validated in code so "999.1.1.1" and
# "256.0.0.1" are rejected. Lookarounds stop it matching inside a longer dotted
# run (e.g. "1.2.3.4.5") or a partial like "192.168".
IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
# IPv6 in the standard 8-group colon-hex form.
IPV6_RE = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b")
# URL: http/https scheme + host + optional path/query. Trailing punctuation is
# stripped after the match.
URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _valid_ipv4(value: str) -> bool:
    """True only for a 4-octet dotted quad with every octet in 0-255."""
    parts = value.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def _has_phone_keyword_nearby(text: str, start: int, end: int, window: int = PHONE_BARE_CONTEXT_WINDOW) -> bool:
    """True if a phone-context keyword sits within `window` chars of the
    match — the substitute evidence a bare 10-digit run needs, since it has
    no separators of its own to lean on."""
    lowered = text[max(0, start - window): end + window].lower()
    return any(kw in lowered for kw in PHONE_KEYWORDS)


def _sin_stands_alone(text: str, start: int, end: int) -> bool:
    """True when a SIN-shaped span is not embedded in a joined identifier.

    ``\b`` is deliberately insufficient here: hyphens and asterisks create
    regex word boundaries inside printed identifiers such as Nova Scotia's
    ``DEMOX--430328096``. A standalone SIN may be surrounded by ordinary
    whitespace/punctuation, but not by an immediately adjacent alphanumeric,
    hyphen, or asterisk on either side.
    """
    forbidden = "-*"
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return not (
        (before and (before.isalnum() or before in forbidden))
        or (after and (after.isalnum() or after in forbidden))
    )


# ============================================================
#  LUHN CHECK FOR CREDIT CARDS
# ============================================================
def luhn_check(number_str: str) -> bool:
    """
    Validate numeric string using the Luhn algorithm.
    Used to confirm if a number is a real credit card.

    HARDENED VERSION - Now handles:
    - None input
    - Non-string types
    - Empty strings
    - Extremely long strings (DoS protection)
    - Non-numeric characters
    """
    # FIX 1: Type validation - Reject None immediately
    if number_str is None:
        return False

    # FIX 2: Convert non-strings to string safely
    if not isinstance(number_str, str):
        try:
            number_str = str(number_str)
        except Exception:
            return False

    # FIX 3: Empty string check
    if not number_str or not number_str.strip():
        return False

    # FIX 4: Length validation (prevent DoS with huge strings)
    MAX_CARD_LENGTH = 19  # Longest real card number
    if len(number_str) > MAX_CARD_LENGTH * 2:  # Allow for spaces/dashes
        return False

    # FIX 5: Extract only digits, handle non-numeric gracefully
    try:
        digits = [int(ch) for ch in number_str if ch.isdigit()]
    except (ValueError, TypeError):
        return False

    # FIX 6: Minimum length check
    if len(digits) < 13:
        return False

    # Original Luhn algorithm
    total = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d

    return total % 10 == 0


# ============================================================
#  MAIN DETECTOR FUNCTION
# ============================================================


def detect_pii(text: str) -> dict:
    """
    Analyze a text string for PII and return dictionary:
      {"email": [...], "phone": [...], "credit_card": [...], ...}

    HARDENED VERSION - Now handles:
    - None input
    - Non-string types (int, float, bool, list, dict, etc.)
    - Empty strings
    - Bytes objects
    - Extremely large strings (DoS protection)
    """
    # FIX 1: Handle None input
    if text is None:
        return {}

    # FIX 2: Convert non-strings to string safely
    if not isinstance(text, str):
        # Special handling for bytes
        if isinstance(text, bytes):
            try:
                text = text.decode("utf-8", errors="ignore")
            except Exception:
                return {}
        else:
            # For other types (int, float, bool, list, dict, etc.)
            try:
                text = str(text)
            except Exception:
                return {}

    # FIX 3: Empty string check
    if not text or not text.strip():
        return {}

    # FIX 4: Length validation (prevent DoS with huge strings)
    MAX_TEXT_LENGTH = 10_000_000  # 10MB limit
    if len(text) > MAX_TEXT_LENGTH:
        # Truncate instead of rejecting entirely
        text = text[:MAX_TEXT_LENGTH]

    results = {
        "email": [],
        "phone": [],
        "credit_card": [],
        "credit_card_unverified": [],
        "sin_9digits": [],
        "sin_unverified": [],
        "ssn": [],
        "date": [],
        "ip_address": [],
        "url": [],
    }

    # FIX 5: Wrap regex operations in try-except for safety
    try:
        # ---------- EMAIL ----------
        results["email"] = [m.group(0) for m in EMAIL_RE.finditer(text)]

        # ---------- PHONE ----------
        # Formatted numbers are always reported (separators are the
        # evidence). Bare 10-digit runs are reported only with a phone
        # keyword nearby (keyword is the substitute evidence) — see
        # PHONE_FORMATTED_RE / PHONE_BARE_RE above for the full rationale.
        # Both land in the same "phone" key the keyword-context layer's own
        # phone_context detections are stripped down to (see
        # normalize_keyword_results in hybrid_detector.py), so the existing
        # per-key dedup in merge_all() reconciles the two layers instead of
        # double-reporting the same value.
        results["phone"] = [m.group(0) for m in PHONE_FORMATTED_RE.finditer(text)]
        for m in PHONE_BARE_RE.finditer(text):
            if _has_phone_keyword_nearby(text, m.start(), m.end()):
                results["phone"].append(m.group(0))

        # ---------- CREDIT CARD ----------
        for m in CREDITCARD_RE.finditer(text):
            candidate = m.group(0)
            digits_only = re.sub(r"\D", "", candidate)
            if luhn_check(digits_only):
                key = (
                    "credit_card"
                    if has_context_keyword_near(text, "credit_card", m.start())
                    else "credit_card_unverified"
                )
                results[key].append(candidate)

        # ---------- SIN (Canadian) ----------
        # Match a 9-digit candidate, validate it (Luhn + prefix rule), then
        # require either explicit SIN context in the keyword layer's normal
        # window or a genuinely standalone source token. This rejects a
        # checksum-coincidental digit suffix embedded in a longer printed ID
        # without losing context-free standalone SINs.
        for m in SIN_RE.finditer(text):
            candidate = m.group(0)
            has_context = has_context_keyword_near(text, "sin", m.start())
            if (
                validate_sin(candidate)
                and (has_context or _sin_stands_alone(text, m.start(), m.end()))
            ):
                key = "sin_9digits" if has_context else "sin_unverified"
                results[key].append(candidate)

        # ---------- DATE (generic) ----------
        # DOB_RE matches any date; whether a date is a *birthdate* is decided by
        # the keyword-context layer, not here. Emit as a generic, low-risk date.
        results["date"] = [m.group(0) for m in DOB_RE.finditer(text)]

        # ---------- IP ADDRESS (IPv4 octet-validated + IPv6) ----------
        for m in IPV4_RE.finditer(text):
            if _valid_ipv4(m.group(0)):
                results["ip_address"].append(m.group(0))
        results["ip_address"] += [m.group(0) for m in IPV6_RE.finditer(text)]

        # ---------- URL ----------
        results["url"] = [
            m.group(0).rstrip(".,;:)]}\"'") for m in URL_RE.finditer(text)
        ]

    except Exception as e:
        # Preserve findings collected before an individual regex failure.
        pass

    # ---------- CLEAN EMPTY KEYS ----------
    return {k: v for k, v in results.items() if v}


# ============================================================
#  SELF-TEST
# ============================================================

if __name__ == "__main__":
    demo_text = """
    Demo: contact alex at alex@example.com or +1 (403) 555-0123.
    Credit card: 4111 1111 1111 1111
    SIN: 123 456 789
    SSN: 123-45-6789
    DOB: 1990-03-15
    """
    print("=== Demo text ===")
    print(demo_text)
    print("\n=== Detections ===")
    detections = detect_pii(demo_text)
    for key, vals in detections.items():
        print(f"{key}:")
        for v in vals:
            print("  -", v)
