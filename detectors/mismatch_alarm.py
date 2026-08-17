#!/usr/bin/env python3
# detectors/mismatch_alarm.py
"""
Mismatch alarm — silent-miss detection (standalone, pure functions, no I/O)
=============================================================================
Flags a file that LOOKS like a Canadian identity document (by filename or by
its own extracted text) but produced NO identifier-category finding. This is
not a detector in the hybrid pipeline's sense: it never adds/removes/retypes
a finding and never touches score/risk_level. It identifies likely identity
documents that produced no identifier finding.

Four independent triggers, any of which is enough to raise the alarm:
  A) filename  — normalized filename contains a document keyword.
  B) content   — extracted text (uppercased, accent-stripped) contains a
                 document keyword. Only evaluated when extraction returned
                 non-empty text.
  C) face      — an image contains a human portrait.
  D) unreadable — OCR ran on an image but yielded little readable text or
                  low confidence.

The alarm fires if any trigger is present and clearance fails. Clearance is at
least one finding whose taxonomy category starts with "identifier." (any tier,
including identifier.government_unverified.*). entity.*/contact.* findings
do NOT clear it: a passport photo that only yields a name and a date is
exactly the failure this alarm exists to catch.

Output shape (see evaluate_mismatch_alarm): a dict with triggered_by,
matched_keywords, and a fixed reason sentence — or None if the file is not
alarmed. Called once per file from discovery.scan_file() and stored on the
result dict; report_generator.py / report_json.py / report_html.py only
render it.
"""

import os
import re
import unicodedata
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# DOCUMENT KEYWORDS
# ---------------------------------------------------------------------------
# Deliberately narrow: every entry names a SPECIFIC Canadian (or common
# French-Canadian) identity-document type. Bare "INSURANCE", "ID",
# "GOVERNMENT", "CANADA" are excluded on purpose — they're generic enough
# that an EOB, a letterhead, or almost any government-adjacent business
# document would false-fire the alarm on every scan. Matching is always
# word-boundary/phrase (never bare substring), so "compass_report.jpg" does
# not trip "PASSPORT". Accent marks are stripped from both the keyword list
# and the haystack before matching, so French entries need no accents here.
DOCUMENT_KEYWORDS = [
    # --- English ---
    "PASSPORT",
    "DRIVER'S LICENCE",
    "DRIVERS LICENCE",
    "DRIVING LICENCE",
    "DRIVER'S LICENSE",
    "DRIVERS LICENSE",
    "DRIVING LICENSE",
    "SOCIAL INSURANCE",
    "SIN CARD",
    "PERMANENT RESIDENT",
    "PR CARD",
    "STATUS CARD",
    "CERTIFICATE OF INDIAN STATUS",
    "HEALTH CARD",
    "BIRTH CERTIFICATE",
    "CITIZENSHIP CARD",
    # --- French (accent-insensitive; keywords already written unaccented) ---
    "PASSEPORT",
    "PERMIS DE CONDUIRE",
    "ASSURANCE SOCIALE",
    "NAS",
    "RESIDENT PERMANENT",
    "CARTE SANTE",
]

# Trigger A aliases are derived, never hand-maintained: collapsing spaces and
# apostrophes covers camera/phone filenames such as PRCARD and DRIVINGLICENSE
# without broadening the ratified base-keyword vocabulary.
FILENAME_KEYWORD_ALIASES = [
    (re.sub(r"[ '\u2019]", "", keyword), keyword)
    for keyword in DOCUMENT_KEYWORDS
    if " " in keyword or "'" in keyword or "\u2019" in keyword
]

# Fixed advisory sentence, shared verbatim between the module's own output
# and every report renderer's recommendation column/paragraph — one source
# of truth so the two never drift apart.
REASON_TEMPLATE = (
    "Document-type indicators present but no identifier was extracted — "
    "manual review recommended."
)
FACE_REASON_TEMPLATE = (
    "Human portrait detected but no identifier was extracted — possible "
    "unreadable identity document; manual review recommended."
)
UNREADABLE_REASON_TEMPLATE = (
    "Document yielded little or no readable text and no identifiers — possibly "
    "an unreadable document; manual review recommended."
)

# Detection layers whose taxonomy output can NEVER carry an "identifier."
# prefix, regardless of which layers are selected for a scan — see
# detectors/hybrid_detector.py's PII_TAXONOMY: gliner only ever emits
# entity.* (person/org/location/date) and secrets only ever emits
# secret.credential.*. Their disablement is therefore irrelevant to
# has_identifier_clearance() below and is deliberately excluded from the
# "this may be why nothing was found" note — naming an unrelated layer would
# be misleading, not honest. Every other layer in
# detectors.hybrid_detector.ALL_LAYERS can contribute an identifier.*
# finding, so its disablement IS relevant.
_LAYERS_NEVER_IDENTIFIER = frozenset({"gliner", "secrets"})

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif", ".webp"}

_FILENAME_SEPARATORS_RE = re.compile(r"[-_.,&+]")
_CAMEL_CASE_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_LETTER_DIGIT_BOUNDARY_RE = re.compile(
    r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])"
)
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_accents(s: str) -> str:
    """NFKD-decompose and drop combining marks, e.g. 'RÉSIDENT' -> 'RESIDENT'."""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch)
    )


def normalize_filename(filename: str) -> str:
    """CamelCase, separators, and letter↔digit boundaries -> space; lowercase.

    'john_passport_scan.jpg' -> 'john passport scan jpg'
    'passport2.jpg' -> 'passport 2 jpg'
    'specimen_licence_01.jpg' -> 'sampleuser drivinglicense 1 face jpg'
    """
    if not filename or not isinstance(filename, str):
        return ""
    s = _CAMEL_CASE_BOUNDARY_RE.sub(" ", filename)
    s = s.lower()
    s = _FILENAME_SEPARATORS_RE.sub(" ", s)
    s = _LETTER_DIGIT_BOUNDARY_RE.sub(" ", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s


def _find_keyword_matches(
    haystack: str, aliases: Optional[List[tuple[str, str]]] = None
) -> List[str]:
    """Word-boundary/phrase search for every DOCUMENT_KEYWORDS entry in an
    already uppercased, accent-stripped `haystack`. Returns matched keywords
    in list order (dedupe happens at the caller)."""
    if not haystack:
        return []
    matched = []
    for kw in DOCUMENT_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", haystack):
            matched.append(kw)
    for alias, keyword in aliases or []:
        if re.search(r"\b" + re.escape(alias) + r"\b", haystack):
            matched.append(keyword)
    return matched


def match_keywords_in_filename(filename: str) -> List[str]:
    """Trigger A: keyword matches against the normalized filename."""
    normalized = normalize_filename(filename)
    haystack = _strip_accents(normalized.upper())
    return _find_keyword_matches(haystack, FILENAME_KEYWORD_ALIASES)


def match_keywords_in_content(text: Optional[str]) -> List[str]:
    """Trigger B: keyword matches against extracted text. Only meaningful
    when extraction returned non-empty text — an empty/whitespace-only
    `text` always returns [] rather than raising."""
    if not text or not isinstance(text, str) or not text.strip():
        return []
    haystack = _strip_accents(text.upper())
    return _find_keyword_matches(haystack)


def has_identifier_clearance(matches: Optional[Dict[str, Any]]) -> bool:
    """True if `matches` (a hybrid_detector-shaped, taxonomy-keyed dict)
    carries at least one finding whose category starts with "identifier."
    — any tier, including identifier.government_unverified.*. entity.*/
    contact.* findings never clear the alarm."""
    if not isinstance(matches, dict):
        return False
    for category, detections in matches.items():
        if category == "_metadata":
            continue
        if not isinstance(detections, list) or not detections:
            continue
        if str(category).startswith("identifier."):
            return True
    return False


def evaluate_mismatch_alarm(
    file_path: str,
    text: Optional[str],
    matches: Optional[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    disabled_layers: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Main entry point. Returns None if the file is not alarmed, otherwise:

        {
            "triggered_by": one or more of filename/content/face/unreadable,
            "matched_keywords": [...],   # sorted, deduped
            "reason": REASON_TEMPLATE,
            "layers_disabled": [...],    # identifier-capable layers that were
                                          # deselected for this scan, sorted;
                                          # [] when none / not applicable.
        }

    `disabled_layers` is this file's scan-level detection-layer selection
    (see discovery.py's enabled_layers / detectors/hybrid_detector.py's
    ALL_LAYERS) — layers that did not run at all, by configuration, not by
    failure. When clearance fails (no identifier finding) and one of these
    disabled layers COULD have contributed an identifier finding (i.e. isn't
    gliner/secrets — see _LAYERS_NEVER_IDENTIFIER above), the reason is
    extended to say so explicitly: this alarm must never read as "the
    scanner tried and failed to find an identifier" when the honest
    statement is "this detector was switched off for this scan."

    Pure and deterministic — no I/O, no mutation of `matches`.
    """
    filename = os.path.basename(file_path) if file_path else ""
    filename_matches = match_keywords_in_filename(filename)
    content_matches = match_keywords_in_content(text)
    extension = os.path.splitext(filename.lower())[1]
    face_count = metadata.get("faces_detected", -1) if isinstance(metadata, dict) else -1
    face_trigger = (
        extension in IMAGE_EXTENSIONS
        and isinstance(face_count, int)
        and not isinstance(face_count, bool)
        and face_count >= 1
    )
    ocr_ran = isinstance(metadata, dict) and (
        metadata.get("ocr_attempted") is True or "ocr_confidence" in metadata
    )
    ocr_confidence = metadata.get("ocr_confidence") if ocr_ran else None
    readable_text = text if isinstance(text, str) else ""
    printable_chars = sum(1 for char in readable_text if char.isprintable())
    unreadable_trigger = (
        ocr_ran
        and (
            not isinstance(text, str)
            or not text.strip()
            or printable_chars < 200
            or (
                isinstance(ocr_confidence, (int, float))
                and not isinstance(ocr_confidence, bool)
                and ocr_confidence < 40
            )
        )
    )

    if (
        not filename_matches
        and not content_matches
        and not face_trigger
        and not unreadable_trigger
    ):
        return None

    if has_identifier_clearance(matches):
        return None

    triggers = []
    if filename_matches:
        triggers.append("filename")
    if content_matches:
        triggers.append("content")
    if face_trigger:
        triggers.append("face")
    if unreadable_trigger:
        triggers.append("unreadable")

    if triggers == ["filename", "content"]:
        triggered_by = "both"
    else:
        triggered_by = "+".join(triggers)

    matched_keywords = sorted(set(filename_matches) | set(content_matches))

    if triggers == ["face"]:
        reason = FACE_REASON_TEMPLATE
    elif triggers == ["unreadable"]:
        reason = UNREADABLE_REASON_TEMPLATE
    else:
        reason = REASON_TEMPLATE

    relevant_disabled_layers = sorted(
        {str(layer) for layer in (disabled_layers or [])} - _LAYERS_NEVER_IDENTIFIER
    )
    if relevant_disabled_layers:
        reason += (
            " Detector layer(s) disabled for this scan: "
            f"{', '.join(relevant_disabled_layers)} — this scan was configured "
            "not to look, which may explain the missing identifier finding; "
            "it is not necessarily a detection failure. Re-scan with these "
            "layers enabled to check."
        )

    return {
        "triggered_by": triggered_by,
        "matched_keywords": matched_keywords,
        "reason": reason,
        "layers_disabled": relevant_disabled_layers,
    }
