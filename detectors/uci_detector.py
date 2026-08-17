#!/usr/bin/env python3
"""IRCC Unique Client Identifier (UCI) detector.

IRCC publishes four written forms: 8 or 10 contiguous digits, and the
displayed 4-4 or 2-4-4 hyphenated equivalents.  No public checksum exists, so
every match requires nearby UCI/client-identification context.  A bare shape
is never a finding and there is deliberately no ``_unverified`` tier.
"""

import re
from typing import Dict, List, Tuple


CONFIDENCE = 0.60
CONTEXT_WINDOW = 40

_CONTEXT_RE = re.compile(
    r"\b(?:uci|client\s+id|client\s+identification\s+number)\b",
    re.IGNORECASE,
)

_PATTERNS = (
    re.compile(r"(?<![\d-])\d{2}-\d{4}-\d{4}(?![\d-])"),
    re.compile(r"(?<![\d-])\d{4}-\d{4}(?![\d-])"),
    re.compile(r"(?<!\d)\d{10}(?!\d)"),
    re.compile(r"(?<!\d)\d{8}(?!\d)"),
)


def detect_uci(text: str) -> Dict[str, List[Tuple[str, float]]]:
    """Return context-gated UCI findings as ``{"uci": [(value, 0.60)]}``."""
    if not isinstance(text, str) or not text.strip():
        return {}

    values: List[Tuple[str, float]] = []
    seen = set()
    for pattern in _PATTERNS:
        for match in pattern.finditer(text):
            window = text[
                max(0, match.start() - CONTEXT_WINDOW):
                match.end() + CONTEXT_WINDOW
            ]
            value = match.group(0)
            if _CONTEXT_RE.search(window) and value not in seen:
                seen.add(value)
                values.append((value, CONFIDENCE))

    return {"uci": values} if values else {}
