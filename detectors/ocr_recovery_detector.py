#!/usr/bin/env python3
"""Deterministic, checksum-gated recovery of OCR-corrupted identifiers.

This layer is intentionally limited to identifier types with an exposed
checksum validator.  It never guesses: exactly one confusion-derived variant
must validate, otherwise nothing is emitted.  Unchecksummed identifiers are
outside this detector's contract.
"""

from __future__ import annotations

from itertools import product
import re
from typing import Callable, Dict, List, Optional

try:
    from keyword_detector import validate_sin
    from health_card_detector import bc_phn_valid, ohip_valid
except ImportError:
    from detectors.keyword_detector import validate_sin
    from detectors.health_card_detector import bc_phn_valid, ohip_valid


RECOVERED_CONFIDENCE = 0.55
MAX_VARIANTS = 64

# Only out-of-grammar OCR glyphs branch. Existing digits are never rewritten:
# a checksum-invalid all-digit value is evidence of invalidity, not evidence
# that OCR confused a valid digit with a letter.
_NUMERIC_CONFUSIONS = {
    "O": ("O", "0"),
    "o": ("o", "0"),
    "I": ("I", "l", "1"),
    "i": ("i", "l", "1"),
    "l": ("l", "I", "1"),
    "S": ("S", "5"),
    "s": ("s", "5"),
    "B": ("B", "8"),
    "b": ("b", "8"),
}
_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9])([0-9OIlSB]{9,10})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_HEALTH_CONTEXT = (
    "health card",
    "health number",
    "phn",
    "uli",
    "medicare",
    "care card",
    "ramq",
    "msi",
    "ohip",
    "alberta",
    "saskatchewan",
    "manitoba",
    "new brunswick",
    "nunavut",
    "yukon",
)
_UNCHECKSUMMED_CONTEXT = (
    "driver's licence",
    "driver licence",
    "driving licence",
    "driver's license",
    "driver license",
    "driving license",
    "passport",
    "travel document",
    "unique client identifier",
    "client identification number",
    "uci",
    "certificate of indian status",
    "status card",
    "indian register",
    "registration number",
)


def recover_unique_variant(
    candidate: str,
    validator: Callable[[str], bool],
    *,
    max_variants: int = MAX_VARIANTS,
    numeric_only: bool = True,
) -> Optional[str]:
    """Return the sole checksum-valid numeric variant, or ``None``.

    The raw Cartesian size is checked before enumeration. If it exceeds the
    cap, the candidate is abandoned whole; no prefix or partial search occurs.
    This primitive is also suitable for a numeric ICAO field when its caller
    supplies a field/check-digit validator.
    """
    if not isinstance(candidate, str) or not candidate or not callable(validator):
        return None
    if not isinstance(max_variants, int) or max_variants < 1:
        return None

    choices = []
    variant_count = 1
    saw_confusion = False
    index = 0
    while index < len(candidate):
        if candidate[index:index + 2].lower() == "rn":
            options = (candidate[index:index + 2], "m")
            saw_confusion = True
            index += 2
        else:
            char = candidate[index]
            if char.lower() == "m":
                options = (char, "rn")
                saw_confusion = True
            elif char.isdigit():
                options = (char,)
            elif char in _NUMERIC_CONFUSIONS:
                options = _NUMERIC_CONFUSIONS[char]
                saw_confusion = True
            elif numeric_only:
                return None
            else:
                options = (char,)
            index += 1
        variant_count *= len(options)
        if variant_count > max_variants:
            return None
        choices.append(options)

    # Fallback only: already-normal numeric candidates stay on their normal
    # detector path, including checksum failures.
    if not saw_confusion:
        return None

    accepted = set()
    for chars in product(*choices):
        value = "".join(chars)
        if (not numeric_only or value.isdigit()) and validator(value):
            accepted.add(value)
            if len(accepted) > 1:
                return None
    return next(iter(accepted)) if len(accepted) == 1 else None


def _substitutions(original: str, recovered: str) -> List[Dict[str, object]]:
    return [
        {"position": index, "original": before, "recovered": after}
        for index, (before, after) in enumerate(zip(original, recovered))
        if before != after
    ]


def detect_ocr_recovery(text: str) -> Dict[str, List[Dict[str, object]]]:
    """Recover checksum-valid SIN and ON/BC health-card candidates.

    Driver's licences, passports, UCI, status registration, and every
    format-only provincial health number are deliberately absent.
    """
    if not isinstance(text, str) or not text.strip():
        return {}

    findings: Dict[str, List[Dict[str, object]]] = {}

    def add(raw_type: str, original: str, recovered: str) -> None:
        findings.setdefault(raw_type, []).append(
            {
                "value": recovered,
                "confidence": RECOVERED_CONFIDENCE,
                "reconstructed": True,
                "original_ocr": original,
                "substitutions": _substitutions(original, recovered),
            }
        )

    for match in _CANDIDATE_RE.finditer(text):
        original = match.group(1)
        if original.isdigit():
            continue
        window = text[
            max(0, match.start() - 50): match.end() + 50
        ].lower()
        if any(keyword in window for keyword in _UNCHECKSUMMED_CONTEXT):
            continue
        if len(original) == 9:
            if any(keyword in window for keyword in _HEALTH_CONTEXT):
                continue
            recovered = recover_unique_variant(original, validate_sin)
            if recovered is not None:
                add("reconstructed_sin", original, recovered)
            continue

        # BC is checked first because its leading-9 rule makes it the narrower
        # 10-digit grammar. A candidate cannot be emitted as both types.
        recovered = recover_unique_variant(original, bc_phn_valid)
        if recovered is not None:
            add("reconstructed_health_card_bc", original, recovered)
            continue
        recovered = recover_unique_variant(original, ohip_valid)
        if recovered is not None:
            add("reconstructed_health_card_on", original, recovered)

    return findings
