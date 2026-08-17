#!/usr/bin/env python3
# tests/test_layer_selection.py
"""
Test suite for the detection-layer selection feature (enabled_layers):
  - detectors.hybrid_detector.ALL_LAYERS   — layer registry, derived from
    SOURCE_PRIORITY (not hand-duplicated).
  - detect_pii_hybrid(enabled_layers=...)  — per-layer gating, None ==
    "run everything" byte-identical to every caller before this parameter
    existed, unknown names ignored, GLiNER's dual gate (run_ner AND
    enabled_layers).
  - Reconciliation changes with layer selection — a deselected layer's
    finding can't be reconciled away by another layer that also didn't run;
    demonstrated with a controlled digit collision (mocked detectors, so the
    result doesn't depend on real regex/context acceptance quirks).
  - discovery.scan_file()/scan_folder() thread enabled_layers through to
    detect_pii_hybrid and record the resolved disabled-layer set on the
    result dict (consumed by mismatch_alarm and the report renderers).
  - scanner.py's --layers argument parser.

Run directly for a pass/fail table + summary:
    docker compose run --rm securescan-cpu python tests/test_layer_selection.py

Also importable / pytest-compatible.
"""

import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discovery
from detectors import hybrid_detector
from detectors.hybrid_detector import ALL_LAYERS, SOURCE_PRIORITY, detect_pii_hybrid
from scanner import _parse_layers
import argparse

SAMPLE_TEXT = (
    "Contact Jane Doe at jane@example.com. SIN: 046 454 286. "
    "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n"
    "Ontario Health Card: 1234-567-890 AB\n"
)


def _check_registry():
    rows, failures = [], []

    ok = ALL_LAYERS == frozenset(SOURCE_PRIORITY.keys())
    rows.append(("REGISTRY", "ALL_LAYERS derived from SOURCE_PRIORITY", sorted(ALL_LAYERS), ok))
    if not ok:
        failures.append(("ALL_LAYERS derivation", sorted(ALL_LAYERS), sorted(SOURCE_PRIORITY)))

    expected = {
        "secrets", "regex", "health_card", "mrz", "passport", "uci",
        "status_card", "ocr_recovery", "keyword_context", "gliner",
        "drivers_license",
    }
    ok = ALL_LAYERS == expected
    rows.append(("REGISTRY", "11 known layer names", sorted(ALL_LAYERS), ok))
    if not ok:
        failures.append(("known layer names", sorted(ALL_LAYERS), sorted(expected)))

    return rows, failures


def _check_none_is_byte_identical_to_omitted():
    rows, failures = [], []
    omitted = detect_pii_hybrid(SAMPLE_TEXT, run_ner=False, verify=False)
    explicit_none = detect_pii_hybrid(
        SAMPLE_TEXT, run_ner=False, verify=False, enabled_layers=None
    )
    explicit_all = detect_pii_hybrid(
        SAMPLE_TEXT, run_ner=False, verify=False, enabled_layers=frozenset(ALL_LAYERS)
    )
    ok = omitted == explicit_none
    rows.append(("DEFAULT", "omitted param == enabled_layers=None", ok, ok))
    if not ok:
        failures.append(("omitted vs None", omitted, explicit_none))

    # Explicitly passing every layer name resolves to the SAME merged
    # findings as None (disabled_layers differs only in bookkeeping: None
    # never allocates a "disabled_layers" list at all vs. an explicit full
    # set resolving to an empty one — both are semantically "nothing
    # disabled").
    findings_only = {k: v for k, v in omitted.items() if k != "_metadata"}
    findings_only_all = {k: v for k, v in explicit_all.items() if k != "_metadata"}
    ok = findings_only == findings_only_all
    rows.append(("DEFAULT", "explicit full set == None (findings)", ok, ok))
    if not ok:
        failures.append(("explicit-all vs None findings", findings_only_all, findings_only))

    ok = omitted["_metadata"]["disabled_layers"] == []
    rows.append(("DEFAULT", "no disabled layers by default", omitted["_metadata"]["disabled_layers"], ok))
    if not ok:
        failures.append(("default disabled_layers", omitted["_metadata"]["disabled_layers"], []))

    return rows, failures


def _check_gating():
    rows, failures = [], []

    result = detect_pii_hybrid(
        SAMPLE_TEXT, run_ner=False, verify=False, enabled_layers={"regex"}
    )
    meta = result["_metadata"]
    ok = meta["layers_used"] == ["regex"] and set(meta["disabled_layers"]) == (ALL_LAYERS - {"regex"})
    rows.append(("GATING", "only requested layer executes", meta["layers_used"], ok))
    if not ok:
        failures.append(("single-layer gating", meta, None))

    # No non-regex-sourced finding should survive.
    sources = {d["source"] for k, v in result.items() if k != "_metadata" for d in v}
    ok = sources <= {"regex"}
    rows.append(("GATING", "no findings from disabled layers", sorted(sources), ok))
    if not ok:
        failures.append(("finding sources", sources, {"regex"}))

    # Unknown layer names are ignored (never crash, never widen selection).
    result_unknown = detect_pii_hybrid(
        SAMPLE_TEXT, run_ner=False, verify=False, enabled_layers={"regex", "bogus_layer"}
    )
    ok = result_unknown["_metadata"]["layers_used"] == ["regex"]
    rows.append(("GATING", "unknown layer name ignored, not crashed", result_unknown["_metadata"]["layers_used"], ok))
    if not ok:
        failures.append(("unknown layer name", result_unknown["_metadata"], None))

    # Empty set disables everything.
    result_empty = detect_pii_hybrid(
        SAMPLE_TEXT, run_ner=True, verify=False, enabled_layers=frozenset()
    )
    ok = (
        result_empty["_metadata"]["layers_used"] == []
        and set(result_empty["_metadata"]["disabled_layers"]) == ALL_LAYERS
        and {k for k in result_empty if k != "_metadata"} == set()
    )
    rows.append(("GATING", "empty set disables every layer", result_empty["_metadata"], ok))
    if not ok:
        failures.append(("empty set", result_empty, None))

    return rows, failures


def _check_gliner_dual_gate():
    rows, failures = [], []
    text = "Sarah Johnson works in Toronto."  # entity-only, no other layer trips

    # run_ner=True but gliner not selected -> still no GLiNER output.
    result = detect_pii_hybrid(text, run_ner=True, verify=False, enabled_layers={"regex"})
    ok = "gliner" not in result["_metadata"]["layers_used"]
    rows.append(("GLINER_GATE", "run_ner=True but layer deselected -> skipped", result["_metadata"]["layers_used"], ok))
    if not ok:
        failures.append(("run_ner True, layer off", result["_metadata"], None))

    # gliner selected but run_ner=False (per-file-type gate) -> still no GLiNER output.
    result = detect_pii_hybrid(text, run_ner=False, verify=False, enabled_layers=ALL_LAYERS)
    ok = "gliner" not in result["_metadata"]["layers_used"]
    rows.append(("GLINER_GATE", "layer selected but run_ner=False -> skipped", result["_metadata"]["layers_used"], ok))
    if not ok:
        failures.append(("layer on, run_ner False", result["_metadata"], None))

    return rows, failures


def _check_reconciliation_depends_on_selection():
    """A deselected layer's finding cannot be reconciled away by a layer
    that also never ran — demonstrated with a controlled digit collision
    (mocked detector output, independent of real regex/context acceptance),
    per the task's requirement to measure that a filtered scan is not
    simply 'the full scan minus the filtered layer's own findings'.
    """
    rows, failures = [], []

    def fake_dl(_text):
        return {"drivers_license_ab": [("123456789", 0.70)]}

    def fake_hc(_text):
        return {"health_card_ab": [("123456789", 0.90)]}

    with (
        patch.object(hybrid_detector, "detect_drivers_licenses", side_effect=fake_dl),
        patch.object(hybrid_detector, "detect_health_cards", side_effect=fake_hc),
    ):
        both_on = detect_pii_hybrid(
            "irrelevant text", run_ner=False, verify=False,
            enabled_layers={"drivers_license", "health_card"},
        )
        dl_only = detect_pii_hybrid(
            "irrelevant text", run_ner=False, verify=False,
            enabled_layers={"drivers_license"},
        )

    dl_key = "identifier.government.drivers_license_ab"
    hc_key = "identifier.government.health_card_ab"

    ok = dl_key not in both_on and hc_key in both_on
    rows.append(
        ("RECONCILE", "both layers on: health_card wins the digit collision", sorted(both_on), ok)
    )
    if not ok:
        failures.append(("both-on collision", both_on, None))

    ok = dl_key in dl_only and hc_key not in dl_only
    rows.append(
        ("RECONCILE", "health_card deselected: DL survives (nothing to lose to)", sorted(dl_only), ok)
    )
    if not ok:
        failures.append(("dl-only collision", dl_only, None))

    return rows, failures


def _check_discovery_wiring():
    rows, failures = [], []
    with tempfile.TemporaryDirectory() as tmp:
        fpath = os.path.join(tmp, "note.txt")
        with open(fpath, "w", encoding="utf-8") as fh:
            fh.write(SAMPLE_TEXT)

        result = discovery.scan_file(
            fpath, verify=False, run_ner=False, enabled_layers=frozenset({"regex"})
        )
        ok = result["scan_status"] == "scanned" and result.get("disabled_layers") and set(
            result["disabled_layers"]
        ) == (ALL_LAYERS - {"regex"})
        rows.append(("DISCOVERY", "scan_file() records disabled_layers", result.get("disabled_layers"), ok))
        if not ok:
            failures.append(("scan_file disabled_layers", result, None))

        # SAMPLE_TEXT contains "Ontario Health Card", tripping the content
        # trigger; since only "regex" ran, the identifier-capable layer that
        # would normally clear it (health_card) never ran, and its
        # disablement should be named in the alarm's reason/layers_disabled
        # — proving disabled_layers reached evaluate_mismatch_alarm() intact.
        alarm = result.get("mismatch_alarm")
        ok = (
            isinstance(alarm, dict)
            and "health_card" in alarm.get("layers_disabled", [])
            and "disabled for this scan" in alarm.get("reason", "")
        )
        rows.append(("DISCOVERY", "mismatch_alarm reflects disabled_layers", alarm, ok))
        if not ok:
            failures.append(("mismatch_alarm wiring", alarm, None))

        default_result = discovery.scan_file(fpath, verify=False, run_ner=False)
        ok = default_result.get("disabled_layers") == []
        rows.append(("DISCOVERY", "scan_file() default has no disabled layers", default_result.get("disabled_layers"), ok))
        if not ok:
            failures.append(("scan_file default disabled_layers", default_result, None))

    return rows, failures


def _check_scan_folder_unknown_layer_banner():
    import contextlib
    import io

    class StubMonitor:
        def __init__(self, *_a, **_k):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def get_peaks(self):
            return {}

    rows, failures = [], []
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("plain text, no PII")

        output = io.StringIO()
        with (
            patch.object(discovery, "SystemMonitor", StubMonitor),
            patch.object(discovery, "EXCLUDED_REPORT_OUTPUT_DIR", os.path.join(tmp, "outputs")),
            patch.object(
                discovery, "EXCLUDED_SYSTEM_MONITOR_LOG",
                os.path.join(tmp, "outputs", "system_monitor.log"),
            ),
            patch.object(discovery.llm_verifier, "check_availability", return_value=(False, "disabled")),
            contextlib.redirect_stdout(output),
        ):
            discovery.scan_folder(
                tmp, verify=False, run_ner=False, max_workers=1,
                enabled_layers={"regex", "not_a_real_layer"},
            )
        banner = output.getvalue()
        ok = "unrecognized detection layer(s) ignored" in banner
        rows.append(("DISCOVERY", "unknown layer name banner printed", "present" if ok else "absent", ok))
        if not ok:
            failures.append(("unknown layer banner", banner, None))

    return rows, failures


def _check_cli_layer_parser():
    rows, failures = [], []

    parsed = _parse_layers("regex, secrets")
    ok = parsed == frozenset({"regex", "secrets"})
    rows.append(("CLI", "parses comma-separated list, strips whitespace", sorted(parsed), ok))
    if not ok:
        failures.append(("parse valid", parsed, {"regex", "secrets"}))

    raised = False
    try:
        _parse_layers("regex,bogus")
    except argparse.ArgumentTypeError:
        raised = True
    rows.append(("CLI", "unknown layer name raises ArgumentTypeError", raised, raised))
    if not raised:
        failures.append(("unknown layer", "did not raise", None))

    raised_empty = False
    try:
        _parse_layers("   ")
    except argparse.ArgumentTypeError:
        raised_empty = True
    rows.append(("CLI", "empty --layers value raises ArgumentTypeError", raised_empty, raised_empty))
    if not raised_empty:
        failures.append(("empty value", "did not raise", None))

    return rows, failures


def run_suite():
    rows, failures = [], []
    for fn in (
        _check_registry,
        _check_none_is_byte_identical_to_omitted,
        _check_gating,
        _check_gliner_dual_gate,
        _check_reconciliation_depends_on_selection,
        _check_discovery_wiring,
        _check_scan_folder_unknown_layer_banner,
        _check_cli_layer_parser,
    ):
        r, f = fn()
        rows.extend(r)
        failures.extend(f)

    print(f"{'GRP':10} {'CASE':62} {'RESULT':7} {'ACTUAL':30}")
    print("-" * 115)
    for grp, name, actual, ok in rows:
        status = "PASS" if ok else "FAIL"
        print(f"{grp:10} {name:62} {status:7} {str(actual)[:30]:30}")

    passed = sum(1 for r in rows if r[3])
    failed = len(rows) - passed
    print("-" * 115)
    print(f"SUMMARY: {passed}/{len(rows)} passed, {failed} failed")
    return passed, failed, failures


# ============================================================
#  PYTEST ENTRY POINTS
# ============================================================


def test_registry():
    _, failures = _check_registry()
    assert not failures, failures


def test_none_is_byte_identical_to_omitted():
    _, failures = _check_none_is_byte_identical_to_omitted()
    assert not failures, failures


def test_gating():
    _, failures = _check_gating()
    assert not failures, failures


def test_gliner_dual_gate():
    _, failures = _check_gliner_dual_gate()
    assert not failures, failures


def test_reconciliation_depends_on_selection():
    _, failures = _check_reconciliation_depends_on_selection()
    assert not failures, failures


def test_discovery_wiring():
    _, failures = _check_discovery_wiring()
    assert not failures, failures


def test_scan_folder_unknown_layer_banner():
    _, failures = _check_scan_folder_unknown_layer_banner()
    assert not failures, failures


def test_cli_layer_parser():
    _, failures = _check_cli_layer_parser()
    assert not failures, failures


if __name__ == "__main__":
    _, failed, _ = run_suite()
    sys.exit(1 if failed else 0)
