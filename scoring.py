# scoring.py

"""
Risk scoring for hybrid detection findings.
Handles new taxonomy format with risk levels.

Banded scoring anchored to the WORST finding in the file:
  - any HIGH finding present            → 70–100
  - MEDIUM is the worst finding         → 30–69 (can never reach 70)
  - only LOW / UNKNOWN findings         → 1–29
  - no findings                         → 0

Within its band, a file starts at the band floor and each finding pushes
the score up by an increment weighted by that finding's own risk level and
confidence. The score is clamped to the band ceiling, so volume alone can
never promote a file into a higher band — a pile of emails stays MEDIUM.

Bare "entity.date" findings (a timestamp with no person attached — e.g. log
lines) are a special case: a date only means something as PII when it's tied
to a person, and that path already goes through "identifier.personal.dob"
with its own MEDIUM weight, unaffected by this. A machine log full of ISO
timestamps is not a pile of birthdates, so bare dates get a tiny, capped
contribution (DATE_CONTRIBUTION_CAP) instead of the normal per-finding
increment — otherwise a log file with enough timestamps crawls up near the
LOW-band ceiling for zero real PII.
"""

from typing import Dict, Any

# Map risk → numeric level
RISK_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "UNKNOWN": 0}

# Score range (floor, ceiling) per band. The band is chosen by the highest
# risk_level present among the file's findings; UNKNOWN scores in the LOW band.
BAND_RANGES = {
    "HIGH": (70, 100),
    "MEDIUM": (30, 69),
    "LOW": (1, 29),
}

# Per-finding increment above the band floor, keyed by band and then by the
# finding's own risk level. Each increment is scaled by the finding's
# confidence before being added.
BAND_INCREMENTS = {
    "HIGH": {"HIGH": 6.0, "MEDIUM": 2.5, "LOW": 1.0, "UNKNOWN": 0.5},
    "MEDIUM": {"MEDIUM": 8.0, "LOW": 1.5, "UNKNOWN": 0.75},
    "LOW": {"LOW": 6.0, "UNKNOWN": 3.0},
}

# Bare dates (no person context) are near-noise: a tiny per-item nudge that
# quickly saturates, so no volume of timestamps meaningfully moves the score.
DATE_CATEGORY = "entity.date"
DATE_PER_ITEM = 0.3
DATE_CONTRIBUTION_CAP = 4.0

DEFAULT_CONFIDENCE = 0.8


def score_file(matches: Dict[str, Any]) -> int:
    """
    Compute risk score from hybrid detector output.
    """

    if not matches or not isinstance(matches, dict):
        return 0

    # Flatten to (category, risk_level, confidence) per finding
    findings = []
    for category, detections in matches.items():
        # Skip metadata
        if category == "_metadata":
            continue

        if not isinstance(detections, list):
            continue

        for det in detections:
            if isinstance(det, dict):
                risk = det.get("risk_level", "UNKNOWN")
                if risk not in RISK_ORDER:
                    risk = "UNKNOWN"
                confidence = det.get("confidence", DEFAULT_CONFIDENCE)
                try:
                    confidence = float(confidence)
                except (TypeError, ValueError):
                    confidence = DEFAULT_CONFIDENCE
                confidence = min(1.0, max(0.0, confidence))
            else:
                risk, confidence = "UNKNOWN", DEFAULT_CONFIDENCE
            findings.append((category, risk, confidence))

    if not findings:
        return 0

    # Band anchored to the worst finding; UNKNOWN-only files score as LOW
    worst = max((risk for _, risk, _ in findings), key=lambda r: RISK_ORDER[r])
    band = worst if worst in BAND_RANGES else "LOW"
    floor, ceiling = BAND_RANGES[band]
    increments = BAND_INCREMENTS[band]

    raw = float(floor)
    date_contribution = 0.0
    for category, risk, confidence in findings:
        if category == DATE_CATEGORY:
            date_contribution += DATE_PER_ITEM * confidence
        else:
            raw += increments.get(risk, 0.0) * confidence
    raw += min(date_contribution, DATE_CONTRIBUTION_CAP)

    return min(ceiling, max(floor, int(raw)))
