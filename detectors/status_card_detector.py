#!/usr/bin/env python3
"""Certificate of Indian Status registration-number detector.

The publicly documented registration number is exactly 10 digits.  No public
checksum exists, so a nearby status-card/Indian-Register keyword is mandatory.
Context-free digits are not findings and there is deliberately no
``_unverified`` tier.
"""

import re
from typing import Dict, List, Tuple


CONFIDENCE = 0.60
CONTEXT_WINDOW = 40

_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"certificate\s+of\s+indian\s+status|"
    r"secure\s+certificate\s+of\s+indian\s+status|"
    r"status\s+card|"
    r"scis|"
    r"registration\s+number|"
    r"registry\s+number|"
    r"indian\s+register"
    r")\b",
    re.IGNORECASE,
)
_REGISTRATION_RE = re.compile(r"(?<!\d)\d{10}(?!\d)")


def detect_status_card(text: str) -> Dict[str, List[Tuple[str, float]]]:
    """Return context-gated status registration numbers at confidence 0.60."""
    if not isinstance(text, str) or not text.strip():
        return {}

    values: List[Tuple[str, float]] = []
    seen = set()
    for match in _REGISTRATION_RE.finditer(text):
        window = text[
            max(0, match.start() - CONTEXT_WINDOW):
            match.end() + CONTEXT_WINDOW
        ]
        value = match.group(0)
        if _CONTEXT_RE.search(window) and value not in seen:
            seen.add(value)
            values.append((value, CONFIDENCE))

    return {"status_card_registration": values} if values else {}
