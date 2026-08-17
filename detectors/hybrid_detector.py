#!/usr/bin/env python3
# hybrid_detector.py
"""
Hybrid PII Detector - Multi-Layer Fusion Engine
================================================
Coordinates eleven detection layers and reconciles their normalized findings
through an explicit source-trust hierarchy.

Layers:
    1) regex and checksum validation
    2) keyword/context detection
    3) GLiNER semantic NER
    4) secrets and credentials
    5) Canadian health cards
    6) passports
    7) IRCC UCI
    8) status-card registration numbers
    9) deterministic OCR recovery
   10) Canadian driver's licences
   11) ICAO 9303 MRZ parsing

Why hybrid?
-----------
No single technique is reliable across all documents. Regex loses distorted
OCR text, keyword rules depend on local context, and semantic NER can classify
non-PII spans as entities. Layer fusion improves precision, recall, confidence,
and provenance while preserving each detector's validation signal.

Standardization:
----------------
Each detector uses different labels. This module maps them into a unified taxonomy:
    identifier.financial.sin
    contact.email
    entity.person
    etc.

Downstream scoring and reporting consume this shared taxonomy.
"""

from typing import Dict, FrozenSet, List, Any, Optional
import sys
import os
import re

# ---------------------------------------------------------------------------
# IMPORT DETECTORS (supports both running inside `/detectors/` and project root)
# ---------------------------------------------------------------------------
try:
    # Typical import when executed inside the detectors folder
    from detectors import detect_pii as detect_regex
    from keyword_detector import detect_pii_keywords
    from detectors.gliner_detector import detect_entities_gliner
    from secrets_detector import detect_secrets
    from health_card_detector import detect_health_cards
    from passport_detector import detect_passports
    from uci_detector import detect_uci
    from status_card_detector import detect_status_card
    from ocr_recovery_detector import detect_ocr_recovery
    from drivers_license_detector import detect_drivers_licenses
    from mrz_detector import detect_mrz
    from detectors.llm_verifier import verify_findings
except ImportError:
    # Fallback when executed from root package or CLI
    parent_dir = os.path.dirname(os.path.dirname(__file__))
    sys.path.insert(0, parent_dir)
    from detectors.detectors import detect_pii as detect_regex
    from detectors.keyword_detector import detect_pii_keywords
    from detectors.gliner_detector import detect_entities_gliner
    from detectors.secrets_detector import detect_secrets
    from detectors.health_card_detector import detect_health_cards
    from detectors.passport_detector import detect_passports
    from detectors.uci_detector import detect_uci
    from detectors.status_card_detector import detect_status_card
    from detectors.ocr_recovery_detector import detect_ocr_recovery
    from detectors.drivers_license_detector import detect_drivers_licenses
    from detectors.mrz_detector import detect_mrz
    from detectors.llm_verifier import verify_findings

try:
    from config import NER_MAX_CHARS
except ImportError:
    parent_dir = os.path.dirname(os.path.dirname(__file__))
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    from config import NER_MAX_CHARS


# ---------------------------------------------------------------------------
# CONFIGURATION CONSTANTS
# ---------------------------------------------------------------------------

MIN_CONFIDENCE = 0.30  # Below this value, results are ignored
HIGH_CONFIDENCE = 0.85  # Used for reporting & statistical metadata

# Priority when duplicate PII is found by multiple layers
# Higher number = more trusted
SOURCE_PRIORITY = {
    "secrets": 4,  # Signature/structure-based credential detection (most trusted)
    "regex": 3,  # Most trusted (pattern validated)
    "health_card": 3,  # Checksum/format-validated health cards (same trust as regex)
    "mrz": 3,  # ICAO 9303 checksum-validated (document number/DOB/expiry) — same trust as regex
    "passport": 2,  # Format + context only (keyword-gated, no checksum)
    "uci": 2,  # IRCC format + context only (keyword-gated, no checksum)
    "status_card": 2,  # Format + context only (keyword-gated, no checksum)
    "ocr_recovery": 2,  # Checksum-valid but reconstructed; always MEDIUM
    "keyword_context": 2,  # Context & fuzzy match
    "gliner": 1,  # GLiNER semantic NER (person/org/location/date) — lowest, loses collisions
    "drivers_license": 1,  # Format + context, no checksum, loosest formats (lowest trust)
}

# The complete, selectable set of detection-layer names, derived from
# SOURCE_PRIORITY rather than hand-duplicated: every "source" string a
# detector emits must already have a SOURCE_PRIORITY entry (deduplicate()
# below does a bare SOURCE_PRIORITY[d["source"]] lookup, so a layer wired
# into detect_pii_hybrid() without one KeyErrors at merge time on its first
# real finding). ALL_LAYERS is therefore guaranteed to match the layers
# actually callable below, and a newly added detector only needs a
# SOURCE_PRIORITY entry to become selectable here too — nothing in this
# file's layer-selection code needs separate updating.
ALL_LAYERS: FrozenSet[str] = frozenset(SOURCE_PRIORITY.keys())

# Taxonomy map for standardizing PII types
PII_TAXONOMY = {
    # Financial
    "sin": "identifier.financial.sin",
    "sin_context": "identifier.financial.sin",
    "sin_unverified": "identifier.financial_unverified.sin",
    "ssn": "identifier.financial.ssn",
    "ssn_context": "identifier.financial.ssn",
    "credit_card": "identifier.financial.credit_card",
    "credit_card_context": "identifier.financial.credit_card",
    "credit_card_unverified": "identifier.financial_unverified.credit_card",
    # Government — provincial/territorial health cards (all HIGH risk)
    "health_card_on": "identifier.government.health_card_on",
    "health_card_bc": "identifier.government.health_card_bc",
    # Province-specific but unverified (health_card_detector.py Tier 2):
    # either checksum-valid with no corroborating keyword, or explicitly
    # province-labelled with a failed checksum. Both remain visible at MEDIUM.
    "health_card_on_unverified": "identifier.government_unverified.health_card_on",
    "health_card_bc_unverified": "identifier.government_unverified.health_card_bc",
    "health_card_ab": "identifier.government.health_card_ab",
    "health_card_sk": "identifier.government.health_card_sk",
    "health_card_mb": "identifier.government.health_card_mb",
    "health_card_nb": "identifier.government.health_card_nb",
    "health_card_ns": "identifier.government.health_card_ns",
    "health_card_pe": "identifier.government.health_card_pe",
    "health_card_nl": "identifier.government.health_card_nl",
    "health_card_nu": "identifier.government.health_card_nu",
    "health_card_yk": "identifier.government.health_card_yk",
    "health_card_nt": "identifier.government.health_card_nt",
    "health_card_qc": "identifier.government.health_card_qc",
    "health_card_ca": "identifier.government.health_card_ca",
    "drivers_license": "identifier.government.drivers_license",
    "drivers_license_context": "identifier.government.drivers_license",
    "drivers_license_on": "identifier.government.drivers_license_on",
    "drivers_license_qc": "identifier.government.drivers_license_qc",
    "drivers_license_bc": "identifier.government.drivers_license_bc",
    "drivers_license_ab": "identifier.government.drivers_license_ab",
    "drivers_license_sk": "identifier.government.drivers_license_sk",
    "drivers_license_mb": "identifier.government.drivers_license_mb",
    "drivers_license_ns": "identifier.government.drivers_license_ns",
    "drivers_license_nb": "identifier.government.drivers_license_nb",
    "drivers_license_nl": "identifier.government.drivers_license_nl",
    "drivers_license_pe": "identifier.government.drivers_license_pe",
    "drivers_license_nt": "identifier.government.drivers_license_nt",
    "drivers_license_nu": "identifier.government.drivers_license_nu",
    "drivers_license_yt": "identifier.government.drivers_license_yt",
    "drivers_license_ca": "identifier.government.drivers_license_ca",
    "passport": "identifier.government.passport",
    "passport_ca": "identifier.government.passport_ca",
    "passport_generic": "identifier.government.passport_generic",
    "uci": "identifier.government.uci",
    "status_card_registration": "identifier.government.status_card_registration",
    "reconstructed_sin": "identifier.reconstructed.sin",
    "reconstructed_health_card_on": "identifier.reconstructed.health_card_on",
    "reconstructed_health_card_bc": "identifier.reconstructed.health_card_bc",
    # MRZ (Machine Readable Zone) — ICAO 9303 checksum-validated fields
    # parsed out of passport/ID-card MRZ blocks (mrz_detector.py). Document
    # number is a live government identifier (HIGH, same category as
    # passport/health_card/DL). DOB carries the personal-data weight shared
    # with every other identifier.personal.* field (MEDIUM). Expiry alone is
    # far less sensitive than an active identifier or a birthdate, so it
    # gets its own LOW-risk prefix (see RISK_SEVERITY below) rather than
    # inheriting identifier.government's HIGH.
    "mrz_document_number": "identifier.government.mrz_document_number",
    "mrz_dob": "identifier.personal.mrz_dob",
    "mrz_expiry": "identifier.government_low.mrz_expiry",
    # MRZ block confirmed but the document-number check digit does NOT
    # validate (even after OCR-noise repair) — mirrors health_card_detector's
    # Tier-2 checksum-fails-but-still-signal pattern (see
    # identifier.government_unverified.health_card_* above).
    "mrz_unverified": "identifier.government_unverified.mrz",
    # Personal
    "dob": "identifier.personal.dob",
    "dob_context": "identifier.personal.dob",
    # Contact
    "email": "contact.email",
    "email_context": "contact.email",
    "phone": "contact.phone",
    "phone_context": "contact.phone",
    "postal_code_ca": "contact.address.postal_code",
    # Personal entities
    "person": "entity.person",
    "organization": "entity.organization",
    "location": "entity.location",
    "date": "entity.date",
    # Technical
    "ip_address": "technical.ip_address",
    "url": "technical.url",
    # Secrets / credentials (all HIGH risk)
    "aws_access_key": "secret.credential.aws_access_key",
    "aws_secret_key": "secret.credential.aws_secret_key",
    "github_token": "secret.credential.github_token",
    "google_api_key": "secret.credential.google_api_key",
    "stripe_key": "secret.credential.stripe_key",
    "slack_token": "secret.credential.slack_token",
    "private_key": "secret.credential.private_key",
    "jwt": "secret.credential.jwt",
    "api_key": "secret.credential.api_key",
    "access_token": "secret.credential.access_token",
    "secret": "secret.credential.secret",
    "auth_credential": "secret.credential.auth_credential",
    "credential": "secret.credential.credential",
    "password": "secret.credential.password",
    "uri_password": "secret.credential.password",
}

# Category → risk level
RISK_SEVERITY = {
    "identifier.financial": "HIGH",
    # A checksum-valid SIN/card with no corroborating keyword remains useful
    # evidence, but checksum coincidence alone does not justify HIGH.
    "identifier.financial_unverified": "MEDIUM",
    "identifier.government": "HIGH",
    # Checksum-valid health card with no keyword context (health_card_detector.py
    # Tier 2, see PII_TAXONOMY comment above) — real evidence, but not enough on
    # its own to reach HIGH; a single bare checksum hit should score MEDIUM.
    "identifier.government_unverified": "MEDIUM",
    # MRZ expiry date only — see PII_TAXONOMY comment above for why this is
    # split out of identifier.government (HIGH) into its own LOW prefix.
    "identifier.government_low": "LOW",
    "identifier.personal": "MEDIUM",
    "identifier.reconstructed": "MEDIUM",
    "contact": "MEDIUM",
    "entity": "LOW",
    "technical": "LOW",
    "secret.credential": "HIGH",
}


# ---------------------------------------------------------------------------
# NORMALIZATION FUNCTIONS (convert detector output → unified structured format)
# ---------------------------------------------------------------------------


# Per-type confidence for regex-layer hits. Checksum-only SIN/card findings
# mirror the health-card Tier-2 confidence: real evidence, but without enough
# corroboration for the verified HIGH tier.
REGEX_CONFIDENCE = {
    "sin": 0.60,
    "sin_unverified": 0.55,
    "credit_card_unverified": 0.55,
}
DEFAULT_REGEX_CONFIDENCE = 0.95


def normalize_regex_results(results: Dict[str, List[str]]) -> Dict[str, List[Dict]]:
    """Convert regex results into structured dict format for merging."""
    normalized = {}

    if not results:
        return normalized

    for key, values in results.items():
        # Normalize naming from regex detector if necessary
        mapped_key = key.replace("_9digits", "")  # e.g. "sin_9digits" → "sin"

        for value in values:
            normalized.setdefault(mapped_key, []).append(
                {
                    "value": value,
                    "confidence": REGEX_CONFIDENCE.get(
                        mapped_key, DEFAULT_REGEX_CONFIDENCE
                    ),
                    "source": "regex",
                }
            )

    return normalized


def normalize_keyword_results(results: Dict[str, Any]) -> Dict[str, List[Dict]]:
    """Convert keyword detector output to unified dict format."""
    normalized = {}

    if not results:
        return normalized

    for key, list_of_matches in results.items():
        if not key.endswith("_context"):
            continue  # Ignore non-PII fields like document classification

        base_key = key.replace("_context", "")
        normalized.setdefault(base_key, [])

        for value, conf in list_of_matches:
            normalized[base_key].append(
                {"value": value, "confidence": conf, "source": "keyword_context"}
            )

    return normalized


def normalize_gliner_results(results: Dict[str, List]) -> Dict[str, List[Dict]]:
    """Convert GLiNER NER entities into unified dict format."""
    normalized = {}

    if not results:
        return normalized

    for key, detections in results.items():
        normalized.setdefault(key, [])
        for value, conf in detections:
            normalized[key].append(
                {"value": value, "confidence": conf, "source": "gliner"}
            )

    return normalized


def normalize_secrets_results(results: Dict[str, List]) -> Dict[str, List[Dict]]:
    """Convert secrets-detector output to the unified finding format."""
    normalized = {}

    if not results:
        return normalized

    for key, detections in results.items():
        normalized.setdefault(key, [])
        for value, conf in detections:
            normalized[key].append(
                {"value": value, "confidence": conf, "source": "secrets"}
            )

    return normalized


def normalize_health_card_results(results: Dict[str, List]) -> Dict[str, List[Dict]]:
    """Convert health card detector output ({type: [(value, conf)]}) to unified dict."""
    normalized = {}

    if not results:
        return normalized

    for key, detections in results.items():
        normalized.setdefault(key, [])
        for value, conf in detections:
            normalized[key].append(
                {"value": value, "confidence": conf, "source": "health_card"}
            )

    return normalized


def normalize_passport_results(results: Dict[str, List]) -> Dict[str, List[Dict]]:
    """Convert passport detector output ({type: [(value, conf)]}) to unified dict."""
    normalized = {}

    if not results:
        return normalized

    for key, detections in results.items():
        normalized.setdefault(key, [])
        for value, conf in detections:
            normalized[key].append(
                {"value": value, "confidence": conf, "source": "passport"}
            )

    return normalized


def normalize_uci_results(results: Dict[str, List]) -> Dict[str, List[Dict]]:
    """Convert UCI detector output ({type: [(value, conf)]}) to unified dict."""
    normalized = {}

    if not results:
        return normalized

    for key, detections in results.items():
        normalized.setdefault(key, [])
        for value, conf in detections:
            normalized[key].append(
                {"value": value, "confidence": conf, "source": "uci"}
            )

    return normalized


def normalize_status_card_results(
    results: Dict[str, List],
) -> Dict[str, List[Dict]]:
    """Convert status-card output ({type: [(value, conf)]}) to unified dict."""
    normalized = {}

    if not results:
        return normalized

    for key, detections in results.items():
        normalized.setdefault(key, [])
        for value, conf in detections:
            normalized[key].append(
                {"value": value, "confidence": conf, "source": "status_card"}
            )

    return normalized


def normalize_ocr_recovery_results(
    results: Dict[str, List],
) -> Dict[str, List[Dict]]:
    """Preserve reconstruction provenance while adding the source label."""
    normalized = {}
    if not results:
        return normalized
    for key, detections in results.items():
        normalized.setdefault(key, [])
        for detection in detections:
            entry = dict(detection)
            entry["source"] = "ocr_recovery"
            normalized[key].append(entry)
    return normalized


def normalize_mrz_results(results: Dict[str, List]) -> Dict[str, List[Dict]]:
    """Convert MRZ detector output ({type: [(value, conf, metadata), ...]})
    to unified dict format. Unlike the other detectors' 2-tuples, MRZ carries
    a per-finding metadata dict (issuing state / doc type / TD format)."""
    normalized = {}

    if not results:
        return normalized

    for key, detections in results.items():
        normalized.setdefault(key, [])
        for value, conf, meta in detections:
            entry = {"value": value, "confidence": conf, "source": "mrz"}
            if meta:
                entry["metadata"] = meta
            normalized[key].append(entry)

    return normalized


def normalize_drivers_license_results(results: Dict[str, List]) -> Dict[str, List[Dict]]:
    """Convert DL detector output ({type: [(value, conf)]}) to unified dict."""
    normalized = {}

    if not results:
        return normalized

    for key, detections in results.items():
        normalized.setdefault(key, [])
        for value, conf in detections:
            normalized[key].append(
                {"value": value, "confidence": conf, "source": "drivers_license"}
            )

    return normalized


# ---------------------------------------------------------------------------
# DEDUPLICATION & MERGE
# ---------------------------------------------------------------------------


def deduplicate(values: List[Dict]) -> List[Dict]:
    """Remove duplicate values → keep strongest (confidence + priority)."""
    bucket = {}

    for det in values:
        normalized_val = det["value"].strip().lower()

        # If unseen, store it
        if normalized_val not in bucket:
            bucket[normalized_val] = det
            continue

        # Existing value → decide which entry is stronger
        current = bucket[normalized_val]
        better = max(
            [current, det],
            key=lambda d: (d["confidence"], SOURCE_PRIORITY[d["source"]]),
        )

        bucket[normalized_val] = better

    # Sort best-first
    return sorted(bucket.values(), key=lambda d: d["confidence"], reverse=True)


def merge_all(detectors: List[Dict[str, List[Dict]]]) -> Dict[str, List[Dict]]:
    """Merge all PII dictionaries into a single deduplicated dictionary."""
    merged = {}

    # Combine values of same PII type
    for result in detectors:
        for key, values in result.items():
            merged.setdefault(key, []).extend(values)

    # Deduplicate for every PII type
    for key in merged:
        merged[key] = deduplicate(merged[key])

    return merged


# ---------------------------------------------------------------------------
# TAXONOMY & RISK ASSIGNMENT
# ---------------------------------------------------------------------------


def apply_taxonomy(results: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    """Convert detection keys → taxonomy and assign risk levels."""
    converted = {}

    for raw_key, detected_list in results.items():
        taxonomy_key = PII_TAXONOMY.get(raw_key, f"uncategorized.{raw_key}")

        # Get parts of taxonomy
        parts = taxonomy_key.split(".")
        # Start with default UNKNOWN
        risk_level = "UNKNOWN"

        if len(parts) >= 2:
            # 1) Try exact match, e.g. "contact.email"
            exact = f"{parts[0]}.{parts[1]}"
            risk_level = RISK_SEVERITY.get(exact)
            # 2) If failing, try parent category, e.g. "contact"
            if risk_level is None:
                parent = parts[0]
                risk_level = RISK_SEVERITY.get(parent, "UNKNOWN")
        else:
            # Single-part taxonomy like "technical"
            parent = parts[0]
            risk_level = RISK_SEVERITY.get(parent, "UNKNOWN")

        # ← REMOVED THE DUPLICATE LINE HERE

        for det in detected_list:
            det_copy = det.copy()
            det_copy["risk_level"] = risk_level
            det_copy["category"] = taxonomy_key
            converted.setdefault(taxonomy_key, []).append(det_copy)

    return converted


# ---------------------------------------------------------------------------
# MAIN HYBRID FUNCTION
# ---------------------------------------------------------------------------


def detect_pii_hybrid(
    text: str,
    run_ner: bool = True,
    verify: bool = False,
    ner_max_chars: Optional[int] = None,
    enabled_layers: Optional[FrozenSet[str]] = None,
) -> Dict[str, Any]:
    """Run all detectors → normalize → merge → classify → return results.

    Args:
        text: The extracted document text to scan.
        run_ner: When True (default), run the GLiNER semantic NER layer
            (person/org/location/date). Set False to skip GLiNER for file types
            where semantic NER adds little value (code, structured/machine files).
            All other layers always run, so regex/keyword/secrets/health/passport/
            DL detection (emails, IPs, credentials, etc.) is unaffected.
        verify: When True, run the LLM (qwen2.5:3b) verification layer over
            selected findings (see detectors/llm_verifier.py for routing
            rules) AFTER merge/reconciliation/taxonomy, so any demotion is
            reflected in the risk levels this function returns and therefore
            in score_file()'s output. Callers are responsible for resolving
            this from config.LLM_VERIFICATION_ENABLED / CLI flags / a
            preflight availability check (see llm_verifier.check_availability)
            — this function makes no availability check itself.
        ner_max_chars: Maximum text prefix passed to GLiNER. None preserves
            the config.NER_MAX_CHARS default. Every non-GLiNER layer always
            receives the complete text.
        enabled_layers: Optional set of layer names (see ALL_LAYERS) to run
            for this call. None (default — every caller before this
            parameter existed) runs every layer, byte-identical to prior
            behavior. An explicit set is intersected with ALL_LAYERS —
            unrecognized names are silently ignored here (callers that need
            to warn about a typo, e.g. the CLI/GUI, do so before reaching
            this function). A deselected layer does not execute at all: it
            contributes nothing to merge/reconciliation, so disabling a
            layer can change which OTHER layers' findings win a collision
            (see SOURCE_PRIORITY / the reconciliation blocks below) — a
            filtered scan is not simply "the full scan minus that layer's
            own findings." GLiNER is additionally gated by run_ner as
            before; both must allow it for it to run.
    """
    if not text or not isinstance(text, str):
        return {"_metadata": {"error": "Invalid input"}}

    normalized_results = []
    used_layers = []
    failed_layers = []

    if enabled_layers is None:
        active_layers = set(ALL_LAYERS)
        disabled_layers: List[str] = []
    else:
        active_layers = set(enabled_layers) & set(ALL_LAYERS)
        disabled_layers = [layer for layer in ALL_LAYERS if layer not in active_layers]

    # Layer 1: Regex
    if "regex" in active_layers:
        try:
            regex_raw = detect_regex(text)
            normalized_results.append(normalize_regex_results(regex_raw))
            used_layers.append("regex")
        except Exception as e:
            print(f"[regex error] {e}")
            failed_layers.append("regex")

    # Layer 2: Keyword Context
    if "keyword_context" in active_layers:
        try:
            kw_raw = detect_pii_keywords(text)
            normalized_results.append(normalize_keyword_results(kw_raw))
            used_layers.append("keyword_context")
        except Exception as e:
            print(f"[keyword error] {e}")
            failed_layers.append("keyword_context")

    # Layer 3: GLiNER NER (semantic entities: person/org/location/date)
    # Gated by run_ner — skipped for code/structured file types where semantic
    # NER adds little value (see GLINER_SKIP_EXTENSIONS in config.py). Also
    # gated by enabled_layers — both must allow it.
    #
    # Capped at NER_MAX_CHARS regardless of run_ner's file-type gating: names/
    # orgs recur throughout a real document, so scanning the whole thing has
    # rapidly diminishing returns, while a multi-MB file is GLiNER's worst
    # case for runtime. Every OTHER layer still sees the full, uncapped text.
    ner_truncated = False
    ner_analyzed_chars = 0
    if run_ner and "gliner" in active_layers:
        try:
            effective_ner_max_chars = (
                NER_MAX_CHARS if ner_max_chars is None else int(ner_max_chars)
            )
            ner_text = text
            if len(text) > effective_ner_max_chars:
                ner_text = text[:effective_ner_max_chars]
                ner_truncated = True
            ner_analyzed_chars = len(ner_text)
            gliner_raw = detect_entities_gliner(ner_text)
            normalized_results.append(normalize_gliner_results(gliner_raw))
            used_layers.append("gliner")
        except Exception as e:
            print(f"[gliner error] {e}")
            failed_layers.append("gliner")

    # Layer 4: Secrets / credentials
    if "secrets" in active_layers:
        try:
            secrets_raw = detect_secrets(text)
            normalized_results.append(normalize_secrets_results(secrets_raw))
            used_layers.append("secrets")
        except Exception as e:
            print(f"[secrets error] {e}")
            failed_layers.append("secrets")

    # Layer 5: Canadian health cards (checksum/format-validated)
    if "health_card" in active_layers:
        try:
            health_raw = detect_health_cards(text)
            normalized_results.append(normalize_health_card_results(health_raw))
            used_layers.append("health_card")
        except Exception as e:
            print(f"[health_card error] {e}")
            failed_layers.append("health_card")

    # Layer 6: Passport numbers (format + context, keyword-gated)
    if "passport" in active_layers:
        try:
            passport_raw = detect_passports(text)
            normalized_results.append(normalize_passport_results(passport_raw))
            used_layers.append("passport")
        except Exception as e:
            print(f"[passport error] {e}")
            failed_layers.append("passport")

    # Layer 7: IRCC UCI (format + context, keyword-gated, no public checksum)
    if "uci" in active_layers:
        try:
            uci_raw = detect_uci(text)
            normalized_results.append(normalize_uci_results(uci_raw))
            used_layers.append("uci")
        except Exception as e:
            print(f"[uci error] {e}")
            failed_layers.append("uci")

    # Layer 8: Status-card registration number (format + context, no checksum)
    if "status_card" in active_layers:
        try:
            status_raw = detect_status_card(text)
            normalized_results.append(normalize_status_card_results(status_raw))
            used_layers.append("status_card")
        except Exception as e:
            print(f"[status_card error] {e}")
            failed_layers.append("status_card")

    # Layer 9: Deterministic OCR recovery (checksum-gated; MEDIUM maximum)
    if "ocr_recovery" in active_layers:
        try:
            recovery_raw = detect_ocr_recovery(text)
            normalized_results.append(normalize_ocr_recovery_results(recovery_raw))
            used_layers.append("ocr_recovery")
        except Exception as e:
            print(f"[ocr_recovery error] {e}")
            failed_layers.append("ocr_recovery")

    # Layer 10: Driver's licences (format + context, keyword-gated, weakest signal)
    if "drivers_license" in active_layers:
        try:
            dl_raw = detect_drivers_licenses(text)
            normalized_results.append(normalize_drivers_license_results(dl_raw))
            used_layers.append("drivers_license")
        except Exception as e:
            print(f"[drivers_license error] {e}")
            failed_layers.append("drivers_license")

    # Layer 11: MRZ (Machine Readable Zone) — ICAO 9303 checksum-validated
    # document number / DOB / expiry parsed out of passport/ID-card MRZ
    # blocks. Never routed to the LLM judge: "mrz" is not in
    # llm_verifier.ROUTABLE_SOURCES, joining secrets/regex/health_card in
    # that checksummed exclusion (see the ROUTING RULES comment there).
    if "mrz" in active_layers:
        try:
            mrz_raw = detect_mrz(text)
            normalized_results.append(normalize_mrz_results(mrz_raw))
            used_layers.append("mrz")
        except Exception as e:
            print(f"[mrz error] {e}")
            failed_layers.append("mrz")

    # Combine & dedupe
    merged = merge_all(normalized_results)

    # Reconcile: a value identified as a DOB (MEDIUM) must not also be reported
    # as a generic date (LOW). Drop such duplicates from the "date" bucket.
    # NOTE: this is by normalized value, not position — if the same date string
    # appears as both a birthdate and another date, both collapse into DOB.
    if "dob" in merged and "date" in merged:
        dob_values = {d["value"].strip().lower() for d in merged["dob"]}
        merged["date"] = [
            d for d in merged["date"]
            if d["value"].strip().lower() not in dob_values
        ]
        if not merged["date"]:
            del merged["date"]

    # Reconcile: a checksum/format-validated health card is far more specific
    # than a loose phone pattern. If the SAME digit string is flagged as both,
    # the health card wins and the phone match on those exact digits is dropped.
    # Compare on normalized digits, not exact strings (formatting may differ).
    health_keys = [k for k in merged if k.startswith("health_card")]
    if health_keys and "phone" in merged:
        hc_digits = {
            re.sub(r"\D", "", d["value"]) for k in health_keys for d in merged[k]
        }
        merged["phone"] = [
            d for d in merged["phone"]
            if re.sub(r"\D", "", d["value"]) not in hc_digits
        ]
        if not merged["phone"]:
            del merged["phone"]

    # Reconcile: a checksum-validated SIN beats a keyword-gated 9-digit passport
    # guess. If the SAME digit string is flagged as both, the SIN wins and the
    # passport_generic match on those exact digits is dropped.
    if "passport_generic" in merged and "sin" in merged:
        sin_digits = {re.sub(r"\D", "", d["value"]) for d in merged["sin"]}
        merged["passport_generic"] = [
            d for d in merged["passport_generic"]
            if re.sub(r"\D", "", d["value"]) not in sin_digits
        ]
        if not merged["passport_generic"]:
            del merged["passport_generic"]

    # Reconcile: MRZ is checksum-validated (source priority 3, same as regex/
    # health_card) and strictly more trustworthy than the passport detector's
    # format+keyword-only match (source priority 2). If the SAME document
    # number was caught by both (e.g. a passport photo whose printed number
    # also appears in its own MRZ line), the MRZ finding wins and the
    # passport match on those exact characters is dropped. Alphanumeric, so
    # compare on uppercased alnum-only content rather than digits-only.
    if "mrz_document_number" in merged:
        mrz_ids = {
            re.sub(r"[^A-Z0-9]", "", d["value"].upper()) for d in merged["mrz_document_number"]
        }
        for pk in ("passport_ca", "passport_generic"):
            if pk in merged:
                merged[pk] = [
                    d for d in merged[pk]
                    if re.sub(r"[^A-Z0-9]", "", d["value"].upper()) not in mrz_ids
                ]
                if not merged[pk]:
                    del merged[pk]

    # Reconcile: driver's licence is the weakest signal (format + context, no
    # checksum, loosest formats), so it loses every digit collision. If the SAME
    # digit string is claimed by any stronger detector (SIN, credit card, health
    # card, phone, passport), drop the DL match on those digits — DL survives
    # only when its digits are otherwise unclaimed.
    dl_keys = [k for k in merged if k.startswith("drivers_license")]
    if dl_keys:
        stronger = ("sin", "credit_card", "health_card", "phone", "passport")
        claimed_digits = {
            re.sub(r"\D", "", d["value"])
            for k, vals in merged.items() if k.startswith(stronger)
            for d in vals
        }
        claimed_digits.discard("")
        for k in dl_keys:
            merged[k] = [
                d for d in merged[k]
                if re.sub(r"\D", "", d["value"]) not in claimed_digits
            ]
            if not merged[k]:
                del merged[k]

    # Confidence filtering
    filtered = {
        k: [d for d in v if d["confidence"] >= MIN_CONFIDENCE]
        for k, v in merged.items()
    }

    # Standardize taxonomy & assign risk score
    final = apply_taxonomy(filtered)

    # LLM verification layer (qwen2.5:3b judge) — AFTER merge/reconciliation
    # and taxonomy application, BEFORE returning, so scoring sees
    # post-verification risk levels. Judgment-only: may demote a finding's
    # risk_level to LOW and annotate it, never create/delete/raise/retype.
    if verify:
        try:
            verify_findings(final, text)
        except Exception as e:
            print(f"[llm_verifier error] {e}")

    # Attach summary metadata
    final["_metadata"] = {
        "layers_used": used_layers,
        "failed_layers": failed_layers,
        "disabled_layers": disabled_layers,
        "detection_degraded": bool(text.strip()) and bool(failed_layers),
        "total_detections": sum(len(v) for v in filtered.values()),
        "high_confidence": sum(
            1
            for v in filtered.values()
            for d in v
            if d["confidence"] >= HIGH_CONFIDENCE
        ),
        "sources": {
            s: sum(1 for v in filtered.values() for d in v if d["source"] == s)
            for s in SOURCE_PRIORITY
        },
        "ner_truncated": ner_truncated,
        "ner_analyzed_chars": ner_analyzed_chars,
    }

    return final


def print_results_summary(results: Dict[str, Any]):
    """Pretty-print standardized hybrid detection results with risk and source info."""
    metadata = results.get("_metadata", {})

    print("=" * 60)
    print("HYBRID PII DETECTION SUMMARY")
    print("=" * 60)

    # Layer info
    print(f"Layers used: {', '.join(metadata.get('layers_used', []))}")
    print(f"Total detections: {metadata.get('total_detections', 0)}")
    print(
        f"High confidence (≥ {HIGH_CONFIDENCE}): {metadata.get('high_confidence', 0)}"
    )
    print()

    # Risk breakdown
    print("Risk Summary:")
    print(
        f"  🔴 HIGH:    {metadata['sources'].get('regex', 0)} (regex counted separately)"
    )
    print(f"  🟡 MEDIUM:  (see category listing)")
    print(f"  🟢 LOW:     (see category listing)")
    print()

    # Category breakdown
    print("Detected Categories:")
    for category, detections in sorted(results.items()):
        if category == "_metadata":
            continue

        # assign symbol by risk
        risk = detections[0].get("risk_level", "UNKNOWN")
        icon = "🔴" if risk == "HIGH" else "🟡" if risk == "MEDIUM" else "🟢"

        print(f" {icon} {category}: {len(detections)} found")
        for d in detections:
            print(
                f"    • {d['value']}  (conf={d['confidence']:.2f}, src={d['source']})"
            )

    print("=" * 60)


# ---------------------------------------------------------------------------
# SELF-TEST (only runs when calling python hybrid_detector.py)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    sample = """
    Name: Sarah Johnson
    Email: sarah@example.com
    Phone: (403) 555-1245
    SIN: 046-454-286
    Alberta Health Card: 123456789
    """

    from pprint import pprint

    pprint(detect_pii_hybrid(sample))
