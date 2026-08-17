#!/usr/bin/env python3
# tests/test_llm_verifier.py
"""
Reusable test suite for detectors/llm_verifier.py.

No live Ollama server required — all HTTP calls are stubbed by monkeypatching
`llm_verifier.requests.get` / `.post` in place and restoring them afterward.

Run directly for a pass/fail table + summary:
    docker compose run --rm securescan-cpu python tests/test_llm_verifier.py

Also importable / pytest-compatible (test_* functions below).
"""

import os
import sys

# Allow running as a plain script from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from detectors import llm_verifier
from detectors.hybrid_detector import detect_pii_hybrid

# ============================================================
#  STUBBING HELPERS
# ============================================================


class _FakeResponse:
    def __init__(self, json_data, status_ok=True):
        self._json_data = json_data
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise llm_verifier.requests.exceptions.HTTPError("bad status")

    def json(self):
        return self._json_data


class _CallCounter:
    """Wraps a stub function and counts invocations, so tests can assert
    'zero HTTP calls attempted' precisely."""

    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    def __call__(self, *a, **kw):
        self.calls += 1
        return self.fn(*a, **kw)


class _patched:
    """Context manager: temporarily replace an attribute, restore on exit."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.original = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.original)
        llm_verifier.reset_availability_cache()


def _sample_finding(source, category, risk_level, value="ABC123456", confidence=0.7):
    return {
        "value": value,
        "confidence": confidence,
        "source": source,
        "risk_level": risk_level,
        "category": category,
    }


# ============================================================
#  1. ROUTING
# ============================================================


def test_routing_never_routes_checksummed_or_secrets_or_health_card():
    never_route = [
        _sample_finding("secrets", "secret.credential.aws_access_key", "HIGH"),
        _sample_finding("regex", "identifier.financial.sin", "HIGH"),
        _sample_finding("health_card", "identifier.government.health_card_on", "HIGH"),
        _sample_finding("keyword_context", "identifier.financial.sin", "HIGH"),
        _sample_finding("keyword_context", "identifier.financial.ssn", "HIGH"),
        _sample_finding("keyword_context", "identifier.financial.credit_card", "HIGH"),
    ]
    for f in never_route:
        assert not llm_verifier.is_routable(f), f"should NOT route: {f}"


def test_routing_routes_passport_dl_and_noncheck_keyword_context():
    should_route = [
        _sample_finding("passport", "identifier.government.passport_ca", "HIGH"),
        _sample_finding("drivers_license", "identifier.government.drivers_license_ca", "HIGH"),
        _sample_finding("keyword_context", "identifier.personal.dob", "MEDIUM"),
        _sample_finding("keyword_context", "contact.email", "MEDIUM"),
        _sample_finding("keyword_context", "contact.phone", "MEDIUM"),
        _sample_finding("keyword_context", "contact.address.postal_code", "MEDIUM"),
    ]
    for f in should_route:
        assert llm_verifier.is_routable(f), f"should route: {f}"


def test_routing_never_routes_gliner_entity_findings():
    # gliner findings are always entity.* -> RISK_SEVERITY["entity"] == "LOW"
    # in hybrid_detector.py, and is_routable() excludes LOW-risk findings
    # before source is even checked — so a gliner finding can never be
    # routed regardless of the (forced, unrealistic) risk_level here. This
    # locks in the severity-floor rationale documented in ROUTABLE_SOURCES'
    # comment (tests/external_enron/EVALUATION.md finding #3).
    never_route = [
        _sample_finding("gliner", "entity.person", "HIGH"),
        _sample_finding("gliner", "entity.person", "LOW"),
    ]
    for f in never_route:
        assert not llm_verifier.is_routable(f), f"gliner should never route: {f}"


def test_routing_never_routes_low_risk_findings():
    low_risk_but_otherwise_routable = [
        _sample_finding("passport", "identifier.government.passport_ca", "LOW"),
        _sample_finding("drivers_license", "identifier.government.drivers_license_ca", "LOW"),
        _sample_finding("keyword_context", "contact.email", "LOW"),
    ]
    for f in low_risk_but_otherwise_routable:
        assert not llm_verifier.is_routable(f), f"LOW finding should never route: {f}"


# ============================================================
#  2. DEMOTION
# ============================================================


def test_demotion_applies_low_and_audit_fields():
    final = {
        "identifier.government.passport_ca": [
            _sample_finding("passport", "identifier.government.passport_ca", "HIGH",
                             value="EK000001", confidence=0.70)
        ]
    }
    text = "Passport No. EK000001 issued in Ukraine."

    with _patched(llm_verifier, "_judge", lambda *a, **kw: ("FALSE_POSITIVE", "foreign document")):
        llm_verifier.verify_findings(final, text)

    f = final["identifier.government.passport_ca"][0]
    assert f["risk_level"] == "LOW"
    assert f["original_risk_level"] == "HIGH"
    assert f["original_confidence"] == 0.70
    assert f["llm_verified"] is True
    assert f["llm_verdict"] == "FALSE_POSITIVE"
    assert f["llm_reason"] == "foreign document"
    assert f["llm_model"] == config.OLLAMA_MODEL


def test_demotion_dedup_applies_verdict_to_all_copies_and_judges_once():
    final = {
        "identifier.government.drivers_license_ca": [
            _sample_finding("drivers_license", "identifier.government.drivers_license_ca", "HIGH",
                             value="200800000000"),
            _sample_finding("drivers_license", "identifier.government.drivers_license_ca", "HIGH",
                             value="200800000000"),  # duplicate value+category
        ]
    }
    text = "DL No. 200800000000"
    calls = _CallCounter(lambda *a, **kw: ("FALSE_POSITIVE", "dup test"))

    with _patched(llm_verifier, "_judge", calls):
        llm_verifier.verify_findings(final, text)

    assert calls.calls == 1, f"expected exactly 1 judge call for the duplicate pair, got {calls.calls}"
    for f in final["identifier.government.drivers_license_ca"]:
        assert f["risk_level"] == "LOW"
        assert f["llm_verdict"] == "FALSE_POSITIVE"


# ============================================================
#  3. STRUCTURAL GUARANTEE
# ============================================================


def test_legitimate_verdict_never_changes_risk_level():
    original = _sample_finding("passport", "identifier.government.passport_ca", "HIGH", value="EK000001")
    final = {"identifier.government.passport_ca": [dict(original)]}
    text = "Passport No. EK000001 issued in Canada."

    with _patched(llm_verifier, "_judge", lambda *a, **kw: ("LEGITIMATE", "real name in context")):
        llm_verifier.verify_findings(final, text)

    f = final["identifier.government.passport_ca"][0]
    assert f["risk_level"] == original["risk_level"], "LEGITIMATE must not alter risk_level"
    assert f["llm_verified"] is True
    assert f["llm_verdict"] == "LEGITIMATE"
    assert "llm_reason" not in f, "LEGITIMATE verdicts must not store a reason (keep reports lean)"
    assert f["value"] == original["value"]
    assert f["source"] == original["source"]


def test_error_path_never_changes_risk_level():
    original = _sample_finding("passport", "identifier.government.passport_ca", "HIGH")
    final = {"identifier.government.passport_ca": [dict(original)]}
    text = "some text without the value"  # forces context_note="approximate" too

    with _patched(llm_verifier, "_judge", lambda *a, **kw: (None, None)):
        llm_verifier.verify_findings(final, text)

    f = final["identifier.government.passport_ca"][0]
    assert f["risk_level"] == original["risk_level"], "error path must not alter risk_level"
    assert f["llm_verified"] is False
    assert "llm_verdict" not in f
    assert f["value"] == original["value"]


def test_low_risk_findings_are_never_mutated():
    original = _sample_finding("passport", "identifier.government.passport_ca", "LOW")
    final = {"identifier.government.passport_ca": [dict(original)]}

    with _patched(llm_verifier, "_judge", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("_judge must not be called for a LOW-risk finding")
    )):
        llm_verifier.verify_findings(final, "irrelevant text")

    f = final["identifier.government.passport_ca"][0]
    assert f == original, "LOW-risk finding must pass through completely untouched"


def test_verdict_fields_can_never_produce_a_non_low_risk_level():
    for verdict in (None, "LEGITIMATE", "FALSE_POSITIVE", "bogus", "", 123):
        fields = llm_verifier._verdict_fields(verdict, "r", "HIGH", 0.9)
        if "risk_level" in fields:
            assert fields["risk_level"] == "LOW", f"verdict {verdict!r} produced non-LOW risk_level"
        # Never permitted to touch the finding's identity.
        assert "value" not in fields
        assert "source" not in fields
        assert "category" not in fields


def test_verify_findings_never_creates_or_deletes_findings():
    final = {
        "identifier.government.passport_ca": [
            _sample_finding("passport", "identifier.government.passport_ca", "HIGH", value="EK000001")
        ],
        "contact.email": [
            _sample_finding("regex", "contact.email", "MEDIUM", value="a@b.com")
        ],
    }
    text = "EK000001 a@b.com"
    before_counts = {k: len(v) for k, v in final.items()}

    with _patched(llm_verifier, "_judge", lambda *a, **kw: ("FALSE_POSITIVE", "x")):
        llm_verifier.verify_findings(final, text)

    assert set(final.keys()) == set(before_counts.keys()), "categories must not appear/disappear"
    for k, n in before_counts.items():
        assert len(final[k]) == n, f"finding count changed for {k}"


# ============================================================
#  4. DEGRADATION CONTRACT
# ============================================================


def test_unreachable_host_disables_verification_and_records_status():
    def _raise_connection_error(*a, **kw):
        raise llm_verifier.requests.exceptions.ConnectionError("no route to host")

    llm_verifier.reset_availability_cache()
    with _patched(llm_verifier.requests, "get", _raise_connection_error):
        enabled, status = llm_verifier.check_availability(force_enabled=True)

    assert enabled is False
    assert status.startswith("skipped:"), f"expected a 'skipped: <reason>' status, got {status!r}"


def test_model_missing_disables_verification_and_records_status():
    def _fake_get(*a, **kw):
        return _FakeResponse({"models": [{"name": "some-other-model:latest"}]})

    llm_verifier.reset_availability_cache()
    with _patched(llm_verifier.requests, "get", _fake_get):
        enabled, status = llm_verifier.check_availability(force_enabled=True)

    assert enabled is False
    assert status.startswith("skipped:")


def test_findings_pass_through_unverified_when_verify_false():
    # Wires through detect_pii_hybrid with verify=False (what the caller
    # resolves to when check_availability() reports unreachable) — findings
    # must be produced exactly as without verification, no llm_* fields.
    sample = "Passport No. AB123456 issued today."
    result = detect_pii_hybrid(sample, run_ner=False, verify=False)
    for category, detections in result.items():
        if category == "_metadata":
            continue
        for d in detections:
            assert "llm_verified" not in d
            assert "llm_verdict" not in d


# ============================================================
#  5. DISABLED PATH — zero HTTP calls attempted
# ============================================================


def test_disabled_config_makes_zero_http_calls():
    get_counter = _CallCounter(lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("requests.get must not be called when verification is disabled")
    ))
    post_counter = _CallCounter(lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("requests.post must not be called when verification is disabled")
    ))

    llm_verifier.reset_availability_cache()
    with _patched(config, "LLM_VERIFICATION_ENABLED", False):
        with _patched(llm_verifier.requests, "get", get_counter):
            with _patched(llm_verifier.requests, "post", post_counter):
                enabled, status = llm_verifier.check_availability()
                assert enabled is False
                assert status == "disabled"
                assert get_counter.calls == 0
                assert post_counter.calls == 0

                # Even a highly routable finding must produce zero HTTP calls
                # when the resolved `verify` flag is False end-to-end.
                result = detect_pii_hybrid(
                    "Passport No. AB123456 issued today.", run_ner=False, verify=enabled
                )
                assert get_counter.calls == 0
                assert post_counter.calls == 0


# ============================================================
#  TEST REGISTRY / RUNNER
# ============================================================

ALL_TESTS = [
    ("routing", "never routes secrets/regex/health_card/checksummed keyword_context",
     test_routing_never_routes_checksummed_or_secrets_or_health_card),
    ("routing", "routes passport/DL/non-checksum keyword_context",
     test_routing_routes_passport_dl_and_noncheck_keyword_context),
    ("routing", "never routes LOW-risk findings",
     test_routing_never_routes_low_risk_findings),
    ("routing", "never routes gliner entity findings (severity-floor, unreachable dead code otherwise)",
     test_routing_never_routes_gliner_entity_findings),
    ("demotion", "FALSE_POSITIVE -> LOW + audit fields",
     test_demotion_applies_low_and_audit_fields),
    ("demotion", "dedup: judged once, applied to all copies",
     test_demotion_dedup_applies_verdict_to_all_copies_and_judges_once),
    ("structural", "LEGITIMATE never changes risk_level",
     test_legitimate_verdict_never_changes_risk_level),
    ("structural", "error path never changes risk_level",
     test_error_path_never_changes_risk_level),
    ("structural", "LOW-risk findings never mutated (never routed)",
     test_low_risk_findings_are_never_mutated),
    ("structural", "_verdict_fields can never produce non-LOW risk_level",
     test_verdict_fields_can_never_produce_a_non_low_risk_level),
    ("structural", "verify_findings never creates/deletes findings",
     test_verify_findings_never_creates_or_deletes_findings),
    ("degradation", "unreachable host -> disabled + status recorded",
     test_unreachable_host_disables_verification_and_records_status),
    ("degradation", "model missing -> disabled + status recorded",
     test_model_missing_disables_verification_and_records_status),
    ("degradation", "findings pass through unverified when verify=False",
     test_findings_pass_through_unverified_when_verify_false),
    ("disabled", "LLM_VERIFICATION_ENABLED=False -> zero HTTP calls",
     test_disabled_config_makes_zero_http_calls),
]


def run_suite():
    rows = []
    failures = []
    for group, name, fn in ALL_TESTS:
        try:
            fn()
            rows.append((group, name, True, "ok"))
        except AssertionError as e:
            rows.append((group, name, False, str(e)))
            failures.append((name, str(e)))
        finally:
            llm_verifier.reset_availability_cache()

    print(f"{'GRP':12} {'CASE':60} {'RESULT':6}")
    print("-" * 90)
    for group, name, ok, reason in rows:
        status = "PASS" if ok else "FAIL"
        line = f"{group:12} {name:60} {status:6}"
        if not ok:
            line += f"  <-- {reason}"
        print(line)

    passed = sum(1 for r in rows if r[2])
    failed = len(rows) - passed
    print("-" * 90)
    print(f"SUMMARY: {passed}/{len(rows)} passed, {failed} failed")
    return passed, failed, failures


if __name__ == "__main__":
    _, failed, _ = run_suite()
    sys.exit(1 if failed else 0)
