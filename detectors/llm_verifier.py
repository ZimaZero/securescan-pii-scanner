#!/usr/bin/env python3
# detectors/llm_verifier.py
"""
LLM verification layer: qwen2.5:3b judge over selected hybrid-detector findings.

Wired into detectors/hybrid_detector.py's detect_pii_hybrid(), AFTER merge/
reconciliation and taxonomy application, so scoring.py sees post-verification
risk levels.

Judgment-only, demote-only. The verifier may DEMOTE a finding (risk_level ->
LOW) and annotate it; it may never create findings, raise a risk_level,
delete a finding, or modify value/type/source. Enforced structurally:
verify_findings() is the only public mutator, and every verdict it applies
is produced by _verdict_fields() — the ONLY function in this module that
writes the "risk_level" key, and its only write is the literal "LOW". The
llm_* / original_* annotation fields are written ONLY by this module.
"""

import json
import os
import re
import sys
import threading

try:
    import requests
except ImportError:  # pragma: no cover - requests is a hard dependency in practice
    requests = None

try:
    import config
except ImportError:
    _parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _parent_dir not in sys.path:
        sys.path.insert(0, _parent_dir)
    import config


# ---------------------------------------------------------------------------
# ROUTING RULES (locked — see tests/ollama_benchmark/RESULTS.md)
# ---------------------------------------------------------------------------
# - "passport" / "drivers_license" / "uci" / "status_card": these formats
#   have NO public checksum at all (see the module docstrings in
#   passport_detector.py / drivers_license_detector.py / uci_detector.py /
#   status_card_detector.py) — the judge is the only second check available.
#   RESULTS.md case 1 (Ukrainian passport matching the CA shape) and case 3
#   (Indian DL swept into drivers_license_ca) confirm qwen2.5:3b catches
#   these foreign-document collisions. "uci"/"status_card" were added later
#   (docs/evidence/verifier_routing_uci_status_card.md) to close a routing
#   gap: both are equally checksum-less and keyword-gated as
#   passport/drivers_license, but were left out of this set when they
#   shipped, with no reasoning ever recorded here for the omission.
# - "gliner" is intentionally NOT in ROUTABLE_SOURCES below, despite being
#   the format this feature was originally expected to exercise most (OCR
#   misreads tagged as a person, a bare domain tagged as an organization,
#   etc. — see tests/ollama_benchmark/RESULTS.md cases 4, 5, 8). GLiNER
#   findings are always category entity.* -> RISK_SEVERITY["entity"] == "LOW"
#   (hybrid_detector.py), and is_routable() excludes LOW-risk findings
#   ("nothing to demote") before source is even checked. A LOW-risk finding
#   can never carry demotable risk, so routing "gliner" here would be dead
#   code reachable by no real input — confirmed structurally unreachable in
#   tests/external_enron/EVALUATION.md finding #3 (24,126 GLiNER hits in that
#   corpus, zero routed). If GLiNER findings ever need judging, the fix is in
#   the taxonomy severity floor, not here.
# - "keyword_context": routed ONLY for taxonomy categories with NO checksum
#   behind them (dob, email, phone, postal code). sin/ssn/credit_card
#   keyword-context hits ARE checksum-validated (validate_sin/validate_ssn/
#   luhn_check in keyword_detector.py) and must never be routed — RESULTS.md
#   case 9 (a real, checksum-valid SIN with explicit "SIN:" context) showed
#   qwen2.5:3b wrongly demotes a checksum-valid value it second-guesses on
#   its own read of the context — a documented, unresolved model limitation.
#   A prompt fix was attempted and reverted (see the SYSTEM_PROMPT comment
#   below); it did not fix case 9 and regressed two other cases.
# - "secrets" / "regex" / "health_card" / "mrz" are NEVER routed: health
#   cards are checksum-validated and RESULTS.md case 16 shows the judge is
#   redundant on a genuinely valid one, while case 2 (an Aadhaar reference
#   number that happens to satisfy the Ontario OHIP checksum) shows the
#   judge CANNOT catch a same-country checksum collision either — routing
#   health_card buys nothing. This is a documented limitation, not an
#   oversight. "mrz" (mrz_detector.py) joins this exclusion for the same
#   reason: every emitted MRZ field already carries its own ICAO 9303 check
#   digit, so it belongs with the other checksummed sources, not with
#   passport/drivers_license (which have NO checksum at all, see below).
ROUTABLE_SOURCES = {"passport", "drivers_license", "uci", "status_card", "keyword_context"}

KEYWORD_CONTEXT_ROUTABLE_CATEGORIES = {
    "identifier.personal.dob",
    "contact.email",
    "contact.phone",
    "contact.address.postal_code",
}

CONTEXT_WINDOW = 100  # chars on each side of the match -> ~200 total

# ---------------------------------------------------------------------------
# PROMPT — byte-identical to the benchmark's SYSTEM_PROMPT (tests/
# ollama_benchmark/benchmark.py), kept in sync there for run-1/run-2
# comparability.
#
# A "prompt v2" fix was attempted here for RESULTS.md's one open issue
# (case 9: qwen2.5:3b wrongly demotes a real, checksum-valid SIN with
# explicit keyword context) by adding an instruction not to dispute
# already-validated values. A qwen2.5:3b-only regression rerun (see
# RESULTS.md's "Prompt v2 attempt" addendum) showed it did NOT fix case 9
# and additionally regressed two previously-correct LEGITIMATE cases (12, 16)
# to FALSE_POSITIVE with confused/self-contradictory reasoning. Reverted —
# case 9 remains a known, documented limitation of this routing (see the
# keyword_context comment below) rather than something this prompt can fix.
# ---------------------------------------------------------------------------
_BASE_SYSTEM_PROMPT = """You are a PII verification judge for an automated PII/sensitive-data scanner.

You will be given a JSON object describing one detection the scanner made:
- "value": the exact string that was flagged
- "detected_type": the taxonomy label the scanner assigned it (e.g. identifier.government.passport_ca, entity.person, contact.phone)
- "context": up to ~200 characters of surrounding text
- "source_layer": which detection layer produced the match (regex, keyword_context, gliner, secrets, health_card, passport, or drivers_license)

Decide whether this is a LEGITIMATE detection (the value really is what it
claims to be, in context) or a FALSE_POSITIVE (the detector matched
something that is not actually that PII type in reality — e.g. a foreign
document's ID number that happens to fit a different country's format, an
OCR misread, a bare digit run swept up by keyword proximity, or a common
English word mistagged as a name).

Base your decision only on the value and context given. Do not invent facts
(e.g. do not claim to have computed a checksum) beyond what is stated.

Respond with STRICT JSON only, no markdown fences, no extra commentary,
in exactly this shape:
{"verdict": "LEGITIMATE", "reason": "<one line>"}
or
{"verdict": "FALSE_POSITIVE", "reason": "<one line>"}
"""

SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# ROUTING
# ---------------------------------------------------------------------------
def is_routable(finding):
    """Whether a single (already taxonomy-applied) finding dict should be
    sent to the LLM judge, per the locked routing rules above."""
    if not isinstance(finding, dict):
        return False
    source = finding.get("source")
    if source not in ROUTABLE_SOURCES:
        return False
    if finding.get("risk_level") == "LOW":
        return False  # nothing to demote
    if source == "keyword_context" and finding.get("category") not in KEYWORD_CONTEXT_ROUTABLE_CATEGORIES:
        return False
    return True


# ---------------------------------------------------------------------------
# CONTEXT EXTRACTION
# ---------------------------------------------------------------------------
def _extract_context(value, text):
    """Returns (context, context_note). context_note is "approximate" when
    `value` isn't found verbatim in `text` (e.g. normalized/reformatted by a
    detector); otherwise return the first 200 characters of text."""
    idx = text.find(value) if value else -1
    if idx == -1:
        return text[:200], "approximate"
    start = max(0, idx - CONTEXT_WINDOW)
    end = min(len(text), idx + len(value) + CONTEXT_WINDOW)
    return text[start:end], None


# ---------------------------------------------------------------------------
# MODEL CALL
# ---------------------------------------------------------------------------
def _extract_json(text):
    """Same tolerant extraction as tests/ollama_benchmark/benchmark.py:
    try a straight parse, then fall back to the outermost {...} span."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _build_user_content(value, detected_type, context, source_layer, context_note):
    payload = {
        "value": value,
        "detected_type": detected_type,
        "context": context,
        "source_layer": source_layer,
    }
    if context_note:
        payload["context_note"] = context_note
    return json.dumps(payload)


def _call_ollama_once(value, detected_type, context, source_layer, context_note):
    """One HTTP round trip. Returns (verdict_dict_or_None, error_or_None)."""
    if requests is None:
        return None, "requests library not available"

    request_payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_content(value, detected_type, context, source_layer, context_note)},
        ],
        "stream": False,
        "options": {
            "temperature": 0,
            "num_predict": config.OLLAMA_NUM_PREDICT,
            "num_thread": config.OLLAMA_NUM_THREAD,
        },
    }
    try:
        resp = requests.post(
            f"{config.OLLAMA_HOST}/api/chat", json=request_payload, timeout=config.OLLAMA_TIMEOUT_S
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return None, str(e)

    raw_text = resp.json().get("message", {}).get("content", "")
    parsed = _extract_json(raw_text)
    if isinstance(parsed, dict) and parsed.get("verdict") in ("LEGITIMATE", "FALSE_POSITIVE"):
        return parsed, None
    return None, "unparseable response"


def _judge(value, detected_type, context, source_layer, context_note):
    """Up to one retry, whether the first attempt failed at the transport
    level or just produced unparseable JSON. Returns (verdict_or_None, reason)."""
    parsed, _ = _call_ollama_once(value, detected_type, context, source_layer, context_note)
    if parsed is None:
        parsed, _ = _call_ollama_once(value, detected_type, context, source_layer, context_note)
    if parsed is None:
        return None, None
    return parsed["verdict"], parsed.get("reason", "")


# ---------------------------------------------------------------------------
# DEGRADATION CONTRACT — one availability check per scan, not per file
# ---------------------------------------------------------------------------
_availability_lock = threading.Lock()
_availability_cache = None  # None = not yet checked; else (enabled, status)


def reset_availability_cache():
    """Force the next check_availability() call to recheck the server
    instead of reusing a cached result. Call once at the start of each
    discovery.scan_folder() run so a long-lived process (the TUI) picks up
    Ollama coming back up/down between scans."""
    global _availability_cache
    with _availability_lock:
        _availability_cache = None


def check_availability(force_enabled=None):
    """Resolve whether verification should run, once, and cache the result.

    `force_enabled`, when not None, overrides config.LLM_VERIFICATION_ENABLED
    for this check (the --verify/--no-verify CLI flags). Returns
    (enabled: bool, status: str) where status is one of "enabled",
    "disabled", or "skipped: <reason>".
    """
    global _availability_cache
    with _availability_lock:
        if _availability_cache is not None:
            return _availability_cache

        requested = force_enabled if force_enabled is not None else config.LLM_VERIFICATION_ENABLED
        if not requested:
            _availability_cache = (False, "disabled")
            return _availability_cache

        if requests is None:
            reason = "skipped: requests library not available"
            print(f"[!] LLM verification {reason}")
            _availability_cache = (False, reason)
            return _availability_cache

        try:
            resp = requests.get(f"{config.OLLAMA_HOST}/api/tags", timeout=3)
            resp.raise_for_status()
            models = [m.get("name") for m in resp.json().get("models", [])]
        except requests.exceptions.RequestException as e:
            reason = f"skipped: ollama unreachable at {config.OLLAMA_HOST} ({e})"
            print(f"[!] LLM verification {reason}")
            _availability_cache = (False, reason)
            return _availability_cache

        model_ok = any(
            m == config.OLLAMA_MODEL or (m and m.split(":")[0] == config.OLLAMA_MODEL.split(":")[0])
            for m in models
        )
        if not model_ok:
            reason = f"skipped: model {config.OLLAMA_MODEL} not found in ollama tags {models}"
            print(f"[!] LLM verification {reason}")
            _availability_cache = (False, reason)
            return _availability_cache

        _availability_cache = (True, "enabled")
        return _availability_cache


# ---------------------------------------------------------------------------
# VERDICT APPLICATION — the only place risk_level is written by this module
# ---------------------------------------------------------------------------
def _verdict_fields(verdict, reason, original_risk_level, original_confidence):
    """Fields to merge into a finding dict for a given verdict outcome.

    Structural demote-only guarantee: this is the ONLY function in the
    module that produces a "risk_level" key, and the only value it can ever
    produce is the literal "LOW". It never returns a "value", "source", or
    "category" key, so callers cannot use it to alter what was detected —
    only to annotate it and, at most, lower its risk_level.
    """
    if verdict == "FALSE_POSITIVE":
        return {
            "risk_level": "LOW",
            "original_risk_level": original_risk_level,
            "original_confidence": original_confidence,
            "llm_verified": True,
            "llm_verdict": "FALSE_POSITIVE",
            "llm_reason": reason,
            "llm_model": config.OLLAMA_MODEL,
        }
    if verdict == "LEGITIMATE":
        return {"llm_verified": True, "llm_verdict": "LEGITIMATE"}
    # Parse failure / timeout / connection error after the retry.
    return {"llm_verified": False}


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------
def verify_findings(final, text):
    """Judge routable findings in `final` against the LLM and apply
    demote-only verdicts, in place.

    `final` is the taxonomy dict produced by apply_taxonomy() — {category:
    [finding dict, ...]} — WITHOUT the "_metadata" key (strip before
    calling). `text` is the full scanned document text, used to extract
    per-finding context.

    Sequential, one call per unique (value, category) pair per file — no
    parallel Ollama calls (see PERFORMANCE GUARD: single-slot server, and
    parallel calls risk the thread-starvation failure documented in
    RESULTS.md). Caller is responsible for checking check_availability()
    first; this function assumes verification should run.
    """
    if not isinstance(final, dict) or not text:
        return

    groups = {}  # (value_norm, category) -> [finding dict, ...] (shared refs)
    for category, detections in final.items():
        if not isinstance(detections, list):
            continue
        for finding in detections:
            if not is_routable(finding):
                continue
            value = str(finding.get("value", ""))
            key = (value.strip().lower(), category)
            groups.setdefault(key, []).append(finding)

    for (_value_norm, category), findings in groups.items():
        sample = findings[0]
        value = str(sample.get("value", ""))
        source_layer = sample.get("source", "")
        context, context_note = _extract_context(value, text)
        verdict, reason = _judge(value, category, context, source_layer, context_note)
        for finding in findings:
            fields = _verdict_fields(
                verdict,
                reason,
                original_risk_level=finding.get("risk_level"),
                original_confidence=finding.get("confidence"),
            )
            finding.update(fields)
