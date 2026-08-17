#!/usr/bin/env python3
# secrets_detector.py
"""
Secrets / Credentials Detector (standalone)
===========================================
Detects leaked credentials in text using three layered methods:

  1) SIGNATURE REGEX  – known-format credentials (AWS, GitHub, Stripe, ...).
                        High precision, trusted, no extra validation.
  2) KEYWORD + VALUE  – `name = value` / `name: value` assignments where the
                        variable name contains a credential word. Catches
                        unlabeled / unknown-vendor keys (e.g. SIGNNOW_API_KEY).
  3) ENTROPY FILTER   – safety net for method 2: only keep values that look
                        random (high Shannon entropy) or mix character classes
                        like a real password; reject prose / placeholder words.

`hybrid_detector.py` invokes this layer and normalizes its output:

    { secret_type: [(value, confidence), ...], ... }

Secrets receive the highest source priority during hybrid reconciliation.
"""

import re
import math
from collections import Counter
from typing import Dict, List, Tuple

# ============================================================
#  CONFIGURATION
# ============================================================

MIN_CONFIDENCE = 0.5          # drop anything below this
ENTROPY_MIN = 3.0             # bits/char threshold for method-2 values
AWS_SECRET_WINDOW = 40        # chars around a 40-char blob to look for "aws"/"secret"

SIGNATURE_CONFIDENCE = 0.95   # trusted signature matches
AWS_SECRET_CONFIDENCE = 0.85  # proximity-gated, slightly lower
URI_PASSWORD_CONFIDENCE = 0.90  # password embedded in a URI (structural, trusted)
METHOD2_CONFIDENCE = 0.70     # keyword + entropy heuristic

# Credential words that may appear inside a variable name (method 2).
CREDENTIAL_KEYWORDS = [
    "client_secret", "access_token", "api_key", "apikey",
    "password", "passwd", "token", "secret", "auth", "pwd", "pass",
]

# Values that are obviously not real secrets even if they pass the regex.
PLACEHOLDER_VALUES = {
    "password", "secret", "changeme", "change_me", "example", "placeholder",
    "none", "null", "true", "false", "yes", "no", "test", "todo", "xxx",
    "required", "characters", "enabled", "disabled", "your_key", "your_token",
}

# ============================================================
#  METHOD 1 — SIGNATURE REGEX
# ============================================================

SIGNATURE_PATTERNS = {
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token":   re.compile(r"\bgh[posu]_[0-9A-Za-z]{36}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "stripe_key":     re.compile(r"\b(?:sk|pk)_(?:live|test)_[0-9A-Za-z]{24,}\b"),
    "slack_token":    re.compile(r"xox[bpoa]-[0-9A-Za-z-]+"),
    "private_key":    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "jwt":            re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
}

# 40-char base64 blob (AWS secret). Generic, so gated by proximity to aws/secret.
_AWS_SECRET_RE = re.compile(r"(?<![A-Za-z0-9/+])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+])")

# Credential embedded in a URI: scheme://user:PASSWORD@host. The trailing '@' is
# required, which avoids matching host:port (e.g. http://host:8080/path).
_URI_CRED_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:([^\s:/@]+)@")

# ============================================================
#  METHOD 2 — KEYWORD + VALUE
# ============================================================

_KW_ALT = "|".join(
    re.escape(k) for k in sorted(CREDENTIAL_KEYWORDS, key=len, reverse=True)
)
_ASSIGNMENT_RE = re.compile(
    # The credential keyword must be a whole token in the variable name, not a
    # substring of a larger word — the (?<![A-Za-z]) / (?![A-Za-z]) guards stop
    # "pass" from matching inside "Passport", "passenger", etc. Token boundaries
    # are underscores, digits, or the start/end of the name.
    r"(?P<name>[A-Za-z0-9_]*(?<![A-Za-z])(?:" + _KW_ALT + r")(?![A-Za-z])[A-Za-z0-9_]*)"
    r"\s*[:=]\s*"
    r"(?:(?P<q>[\"'])(?P<qval>[^\"'\n]*)(?P=q)|(?P<val>[^\s\"';,]+))",
    re.IGNORECASE,
)


def _classify(name: str) -> str:
    """Map a credential-bearing variable name to a secret type label."""
    n = name.lower()
    if "password" in n or "passwd" in n or "pwd" in n or n.endswith("pass"):
        return "password"
    if "access_token" in n or "token" in n:
        return "access_token"
    if "client_secret" in n or "secret" in n:
        return "secret"
    if "api_key" in n or "apikey" in n:
        return "api_key"
    if "auth" in n:
        return "auth_credential"
    return "credential"


# ============================================================
#  METHOD 3 — ENTROPY / PROSE FILTER
# ============================================================

def shannon_entropy(s: str) -> float:
    """Shannon entropy (bits per character) of a string."""
    if not s:
        return 0.0
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in Counter(s).values())


def _char_classes(s: str) -> int:
    """Count how many of {lower, upper, digit, symbol} appear in s."""
    return sum([
        any(c.islower() for c in s),
        any(c.isupper() for c in s),
        any(c.isdigit() for c in s),
        any(not c.isalnum() for c in s),
    ])


def _looks_like_prose(value: str) -> bool:
    """True if value reads like prose: multiple tokens incl. an alphabetic word."""
    parts = value.split()
    return len(parts) > 1 and any(p.isalpha() for p in parts)


def is_probably_secret(value: str) -> bool:
    """Decide whether a method-2 value looks like a real secret."""
    v = value.strip()
    if len(v) < 6:
        return False
    if v.lower() in PLACEHOLDER_VALUES:
        return False
    if _looks_like_prose(v):
        return False
    # Random-looking (high entropy) OR password-like (mixed character classes).
    return shannon_entropy(v) >= ENTROPY_MIN or _char_classes(v) >= 3


# ============================================================
#  MAIN DETECTOR
# ============================================================

def detect_secrets(text: str) -> Dict[str, List[Tuple[str, float]]]:
    """Scan text for secrets. Returns {secret_type: [(value, confidence), ...]}."""
    if not isinstance(text, str) or not text.strip():
        return {}

    findings: Dict[str, List[Tuple[str, float]]] = {}

    def add(stype: str, value: str, conf: float):
        findings.setdefault(stype, []).append((value, conf))

    lowered = text.lower()

    # ---- Method 1: signatures ----
    for stype, rx in SIGNATURE_PATTERNS.items():
        for m in rx.finditer(text):
            add(stype, m.group(0).split("\n")[0].strip(), SIGNATURE_CONFIDENCE)

    # ---- Method 1b: AWS secret (proximity-gated) ----
    for m in _AWS_SECRET_RE.finditer(text):
        ctx = lowered[max(0, m.start() - AWS_SECRET_WINDOW): m.end() + AWS_SECRET_WINDOW]
        if "aws" in ctx or "secret" in ctx:
            add("aws_secret_key", m.group(0).split("\n")[0].strip(), AWS_SECRET_CONFIDENCE)

    # ---- Method 1c: credentials embedded in a URI (scheme://user:PASSWORD@host) ----
    # Trusted signature, so it bypasses the entropy net and catches weak passwords;
    # only obvious placeholders are filtered out.
    for m in _URI_CRED_RE.finditer(text):
        password = m.group(1).strip()
        if password and password.lower() not in PLACEHOLDER_VALUES:
            add("uri_password", password, URI_PASSWORD_CONFIDENCE)

    # ---- Method 2 + 3: keyword assignment, filtered by entropy ----
    for m in _ASSIGNMENT_RE.finditer(text):
        raw = m.group("qval") if m.group("qval") is not None else m.group("val")
        if not raw:
            continue
        value = raw.split("\n")[0].strip()
        if is_probably_secret(value):
            add(_classify(m.group("name")), value, METHOD2_CONFIDENCE)

    # ---- Dedup per type (keep highest conf), apply MIN_CONFIDENCE ----
    result: Dict[str, List[Tuple[str, float]]] = {}
    for stype, items in findings.items():
        best: Dict[str, float] = {}
        for value, conf in items:
            if value not in best or conf > best[value]:
                best[value] = conf
        kept = [(v, c) for v, c in best.items() if c >= MIN_CONFIDENCE]
        if kept:
            result[stype] = kept
    return result


# ============================================================
#  SELF-TEST
# ============================================================

if __name__ == "__main__":
    SHOULD_MATCH = {
        "AWS access key":     "deploy with AKIAIOSFODNN7EXAMPLE today",
        "GitHub token":       "token ghp_0123456789abcdef0123456789abcdef0123",
        "Unknown vendor key": 'SIGNNOW_API_KEY = "a8Ff3K2m9Xz7Qw1Lp0Rt5Yb8Nc4Vd6Hj"',
        "Password":           "login password: S3cret!",
    }
    SHOULD_SKIP = {
        "Password policy":    "password requirements: 8 characters minimum",
        "UUID":               "request id 550e8400-e29b-41d4-a716-446655440000",
        "Git commit hash":    "git commit 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b",
        "English sentence":   "The quick brown fox jumps over the lazy dog.",
    }

    print("=== SHOULD MATCH ===")
    for label, text in SHOULD_MATCH.items():
        print(f"  {label:20} -> {detect_secrets(text)}")
    print("\n=== SHOULD SKIP (expect {}) ===")
    for label, text in SHOULD_SKIP.items():
        out = detect_secrets(text)
        flag = "OK" if not out else "!! LEAK"
        print(f"  [{flag}] {label:20} -> {out}")
