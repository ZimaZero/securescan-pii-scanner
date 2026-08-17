#!/usr/bin/env python3
"""Run the Canadian identifier evaluation corpus against its manifest.

This is a read-only evaluation harness. It scans every corpus file through the
production extraction/detection path, compares only ``identifier.*`` findings
with the public-ground-truth contract in ``canadian_eval_docs/manifest.json``,
and never writes reports or modifies corpus data.

Usage:
    docker compose run --rm securescan-cpu python tests/run_canadian_eval.py

Exit status is non-zero only for an OK-case regression, an unpredicted known-gap
outcome, or an invalid/incomplete corpus-manifest pair. Known gaps behaving as
documented and UNVERIFIED rows do not fail the command.

The corpus intentionally defines no ``_unverified`` tier for UCI or status-card
registration numbers: those types have no public checksum, so missing context
means no finding rather than a demoted finding.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from config import DEFAULT_MAX_WORKERS  # noqa: E402
from discovery import SUPPORTED_EXTENSIONS, scan_file  # noqa: E402
from detectors import llm_verifier  # noqa: E402


CORPUS_DIR = PROJECT_ROOT / "tests" / "canadian_eval_data"
MANIFEST_PATH = PROJECT_ROOT / "tests" / "canadian_eval_docs" / "manifest.json"
VALID_VERDICTS = {"POSITIVE", "NEGATIVE"}
VALID_STATUSES = {
    "OK",
    "GAP-MISS",
    "GAP-TIER",
    "GAP-PARTIAL",
    "GAP-FALSE-POSITIVE",
    "UNVERIFIED",
}


@dataclass(frozen=True)
class Finding:
    category: str
    tier: str
    value: str
    source: str


@dataclass
class Evaluation:
    filename: str
    verdict: str
    status: str
    expected: str
    actual: list[Finding]
    outcome: str
    detail: str
    fatal: bool


def _identifier_findings(result: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    matches = result.get("matches")
    if not isinstance(matches, dict):
        return findings
    for category, items in matches.items():
        if not str(category).startswith("identifier.") or not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            findings.append(
                Finding(
                    category=str(category),
                    tier=str(item.get("risk_level", "UNKNOWN")),
                    value=str(item.get("value", "")),
                    source=str(item.get("source", "unknown")),
                )
            )
    return sorted(
        findings,
        key=lambda item: (item.category, item.tier, item.value, item.source),
    )


def _expected_counter(entry: dict[str, Any]) -> Counter[tuple[str, str]]:
    counter: Counter[tuple[str, str]] = Counter()
    for expected in entry["expected_findings"]:
        counter[
            (
                str(expected["taxonomy_category"]),
                str(expected["trust_tier"]),
            )
        ] += int(expected["count"])
    return counter


def _actual_counter(findings: list[Finding]) -> Counter[tuple[str, str]]:
    return Counter((finding.category, finding.tier) for finding in findings)


def _identifier_kind(category: str) -> str:
    """Collapse trust/province variants to a conceptual identifier type."""
    for marker in (
        "health_card",
        "drivers_license",
        "passport",
        "mrz",
        "status_card_registration",
        "uci",
    ):
        if marker in category:
            return marker
    return category.rsplit(".", 1)[-1]


def _is_documented_partial(
    entry: dict[str, Any],
    findings: list[Finding],
    source_text: str,
) -> bool:
    """Confirm the manifest's one known partial-span contract.

    A GAP-PARTIAL must otherwise have the expected category/tier/count. The
    current manifest describes an omitted one-letter version suffix; require
    the emitted value to occur in source followed by a separated single letter.
    If the detector starts returning the full value, this predicate becomes
    false and the stale gap status is reported as unexpected.
    """
    note = str(entry.get("note", "")).lower()
    if "one-letter version" not in note:
        return False
    for finding in findings:
        if re.search(
            re.escape(finding.value) + r"[ \t]+[A-Za-z]\b",
            source_text,
            re.IGNORECASE,
        ):
            return True
    return False


def _format_expected(entry: dict[str, Any]) -> str:
    specs = []
    for finding in entry["expected_findings"]:
        specs.append(
            f"{finding['count']}x {finding['taxonomy_category']} "
            f"[{finding['trust_tier']}]"
        )
    return "; ".join(specs) if specs else "zero identifier.* findings"


def _format_actual(findings: list[Finding], scan_error: str | None = None) -> str:
    if scan_error:
        return f"SCAN ERROR: {scan_error}"
    if not findings:
        return "zero identifier.* findings"
    return "; ".join(
        f"{finding.category} [{finding.tier}] "
        f"{finding.value!r} ({finding.source})"
        for finding in findings
    )


def _evaluate(
    entry: dict[str, Any],
    result: dict[str, Any],
    source_text: str,
) -> Evaluation:
    filename = str(entry["filename"])
    verdict = str(entry["verdict"])
    status = str(entry["status"])
    expected_text = _format_expected(entry)

    if result.get("scan_status") != "scanned":
        reason = str(result.get("failure_reason") or result.get("scan_status") or "unknown")
        return Evaluation(
            filename,
            verdict,
            status,
            expected_text,
            [],
            "REGRESSION" if status == "OK" else "UNPREDICTED GAP",
            f"file was not scanned: {reason}",
            True,
        )

    actual = _identifier_findings(result)
    expected_counter = _expected_counter(entry)
    actual_counter = _actual_counter(actual)

    if status == "UNVERIFIED":
        return Evaluation(
            filename,
            verdict,
            status,
            expected_text,
            actual,
            "UNVERIFIED",
            "reported only; no public grammar establishes pass/fail",
            False,
        )

    if status == "OK":
        agrees = (
            actual_counter == expected_counter
            if verdict == "POSITIVE"
            else not actual
        )
        return Evaluation(
            filename,
            verdict,
            status,
            expected_text,
            actual,
            "AGREEMENT" if agrees else "REGRESSION",
            (
                "actual identifier set matches ground truth"
                if agrees
                else "OK-case identifier category/tier/count differs from ground truth"
            ),
            not agrees,
        )

    if status == "GAP-MISS":
        if not actual:
            return Evaluation(
                filename,
                verdict,
                status,
                expected_text,
                actual,
                "KNOWN GAP — PREDICTED",
                "expected identifier remains entirely absent",
                False,
            )
        if actual_counter == expected_counter:
            detail = "expected finding now succeeds; manifest gap status is stale"
        else:
            detail = (
                "UNCHARACTERIZED WRONG IDENTIFIER: expected target is still "
                "missing, but a different identifier finding was produced"
            )
        return Evaluation(
            filename,
            verdict,
            status,
            expected_text,
            actual,
            "UNPREDICTED GAP",
            detail,
            True,
        )

    if status == "GAP-TIER":
        if actual_counter == expected_counter:
            return Evaluation(
                filename,
                verdict,
                status,
                expected_text,
                actual,
                "UNPREDICTED GAP",
                "expected category/tier now succeeds; manifest gap status is stale",
                True,
            )
        expected_count = sum(expected_counter.values())
        expected_kinds = {
            _identifier_kind(category) for category, _tier in expected_counter
        }
        actual_kinds = {_identifier_kind(finding.category) for finding in actual}
        predicted = (
            len(actual) == expected_count
            and actual_kinds == expected_kinds
            and actual_counter != expected_counter
        )
        return Evaluation(
            filename,
            verdict,
            status,
            expected_text,
            actual,
            "KNOWN GAP — PREDICTED" if predicted else "UNPREDICTED GAP",
            (
                "identifier type/count exists, but category/trust tier is wrong"
                if predicted
                else "tier gap did not fail in the documented way"
            ),
            not predicted,
        )

    if status == "GAP-PARTIAL":
        shape_matches = actual_counter == expected_counter
        predicted = shape_matches and _is_documented_partial(
            entry, actual, source_text
        )
        return Evaluation(
            filename,
            verdict,
            status,
            expected_text,
            actual,
            "KNOWN GAP — PREDICTED" if predicted else "UNPREDICTED GAP",
            (
                "expected category/tier exists, but emitted span omits the documented suffix"
                if predicted
                else "partial-span gap did not fail in the documented way"
            ),
            not predicted,
        )

    if status == "GAP-FALSE-POSITIVE":
        predicted = bool(actual)
        return Evaluation(
            filename,
            verdict,
            status,
            expected_text,
            actual,
            "KNOWN GAP — PREDICTED" if predicted else "UNPREDICTED GAP",
            (
                "invalid value still produces an identifier finding"
                if predicted
                else "false-positive gap no longer reproduces; manifest status is stale"
            ),
            not predicted,
        )

    raise AssertionError(f"unhandled status {status!r}")


def _load_manifest() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("manifest 'files' must be a list")

    seen: set[str] = set()
    for index, entry in enumerate(files, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"manifest row {index} is not an object")
        filename = str(entry.get("filename", ""))
        if not filename or filename in seen:
            raise ValueError(f"missing/duplicate manifest filename: {filename!r}")
        seen.add(filename)
        verdict = entry.get("verdict")
        status = entry.get("status")
        if verdict not in VALID_VERDICTS:
            raise ValueError(f"{filename}: invalid verdict {verdict!r}")
        if status not in VALID_STATUSES:
            raise ValueError(f"{filename}: invalid status {status!r}")
        expected_findings = entry.get("expected_findings")
        if not isinstance(expected_findings, list):
            raise ValueError(f"{filename}: expected_findings must be a list")
        if verdict == "NEGATIVE" and expected_findings:
            raise ValueError(f"{filename}: NEGATIVE row has expected findings")
        if status == "GAP-FALSE-POSITIVE" and verdict != "NEGATIVE":
            raise ValueError(f"{filename}: GAP-FALSE-POSITIVE must be NEGATIVE")
        if status in {"GAP-MISS", "GAP-TIER", "GAP-PARTIAL"} and verdict != "POSITIVE":
            raise ValueError(f"{filename}: {status} must be POSITIVE")
        if status in {"GAP-TIER", "GAP-PARTIAL"} and not expected_findings:
            raise ValueError(f"{filename}: {status} requires expected findings")
        for finding in expected_findings:
            required = {"taxonomy_category", "trust_tier", "count"}
            if not isinstance(finding, dict) or not required <= finding.keys():
                raise ValueError(f"{filename}: malformed expected finding")
            if not str(finding["taxonomy_category"]).startswith("identifier."):
                raise ValueError(f"{filename}: expected non-identifier taxonomy")
            if finding["trust_tier"] not in {"HIGH", "MEDIUM", "LOW"}:
                raise ValueError(f"{filename}: invalid trust tier")
            if not isinstance(finding["count"], int) or finding["count"] < 1:
                raise ValueError(f"{filename}: invalid finding count")
    return manifest, files


def _validate_corpus(entries: list[dict[str, Any]]) -> None:
    manifest_names = {str(entry["filename"]) for entry in entries}
    corpus_names = {
        path.name
        for path in CORPUS_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    }
    missing = sorted(manifest_names - corpus_names)
    extra = sorted(corpus_names - manifest_names)
    if missing or extra:
        pieces = []
        if missing:
            pieces.append(f"missing corpus files: {', '.join(missing)}")
        if extra:
            pieces.append(f"unmanifested corpus files: {', '.join(extra)}")
        raise ValueError("; ".join(pieces))


def _resolve_verification(force_verify: bool) -> tuple[bool, str]:
    """Resolve whether this run verifies, and produce a status string that
    names both the outcome and why: the config default, an explicit
    --verify override, or an unavailable local Ollama server."""
    llm_verifier.reset_availability_cache()
    force_enabled = True if force_verify else None
    enabled, raw_status = llm_verifier.check_availability(force_enabled=force_enabled)
    source = (
        "--verify"
        if force_verify
        else f"config default (LLM_VERIFICATION_ENABLED={config.LLM_VERIFICATION_ENABLED})"
    )
    if enabled:
        status = f"enabled [{source}]"
    elif raw_status == "disabled":
        status = f"disabled [{source}]"
    else:
        status = f"{raw_status} [{source}, requested]"
    return enabled, status


def _scan_all(
    entries: list[dict[str, Any]],
    *,
    verify_enabled: bool,
) -> tuple[dict[str, dict[str, Any]], float]:
    started = time.perf_counter()
    results: dict[str, dict[str, Any]] = {}

    def scan(entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        filename = str(entry["filename"])
        result = scan_file(
            str(CORPUS_DIR / filename),
            verify=False,
            return_text=verify_enabled,
            run_ner=True,
        )
        return filename, result

    with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
        futures = [executor.submit(scan, entry) for entry in entries]
        for completed, future in enumerate(as_completed(futures), 1):
            filename, result = future.result()
            results[filename] = result
            print(
                f"\rScanning Canadian corpus: {completed}/{len(entries)}",
                end="",
                flush=True,
            )
    print()

    if verify_enabled:
        print("[i] Running sequential LLM verification pass...")
        for entry in entries:
            result = results[str(entry["filename"])]
            source_text = str(result.pop("_text", ""))
            if result.get("scan_status") == "scanned":
                llm_verifier.verify_findings(result.get("matches", {}), source_text)

    elapsed = time.perf_counter() - started
    return results, elapsed


def _print_table(rows: list[Evaluation], heading: str) -> None:
    print(f"\n{heading}")
    print("=" * len(heading))
    print(f"{'FILE':<47}  {'CATEGORY':<23}  EXPECTED -> ACTUAL")
    print("-" * 118)
    for row in rows:
        actual = _format_actual(row.actual)
        print(
            f"{row.filename:<47}  {row.outcome:<23}  "
            f"{row.expected} -> {actual}"
        )
        print(f"{'':<47}  {'':<23}  {row.detail}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Canadian identifier evaluation corpus."
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Force LLM verification on for this run, overriding "
            "config.LLM_VERIFICATION_ENABLED (requires a reachable local "
            "Ollama server). For verifier measurement work; the default "
            "run follows the config default and does not depend on "
            "whether Ollama happens to be running."
        ),
    )
    args = parser.parse_args()

    try:
        _manifest, entries = _load_manifest()
        _validate_corpus(entries)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FATAL] Canadian evaluation configuration error: {exc}")
        return 2

    verify_enabled, verification_status = _resolve_verification(args.verify)

    print(f"[i] Manifest: {MANIFEST_PATH.relative_to(PROJECT_ROOT)}")
    print(f"[i] Corpus: {CORPUS_DIR.relative_to(PROJECT_ROOT)} ({len(entries)} files)")
    print(
        "[i] Detection: all production layers; NER enabled; "
        f"LLM verification: {verification_status}"
    )

    try:
        results, elapsed = _scan_all(entries, verify_enabled=verify_enabled)
    except Exception as exc:
        print(f"\n[FATAL] Corpus scan aborted: {type(exc).__name__}: {exc}")
        return 2

    evaluations = []
    for entry in entries:
        filename = str(entry["filename"])
        source_text = (CORPUS_DIR / filename).read_text(
            encoding="utf-8", errors="replace"
        )
        evaluations.append(_evaluate(entry, results[filename], source_text))

    agreements = sum(row.outcome == "AGREEMENT" for row in evaluations)
    regressions = sum(row.outcome == "REGRESSION" for row in evaluations)
    predicted_gaps = sum(
        row.outcome == "KNOWN GAP — PREDICTED" for row in evaluations
    )
    unexpected_gaps = sum(
        row.outcome == "UNPREDICTED GAP" for row in evaluations
    )
    unverified = sum(row.outcome == "UNVERIFIED" for row in evaluations)
    accounted = (
        agreements
        + regressions
        + predicted_gaps
        + unexpected_gaps
        + unverified
    )
    if accounted != len(evaluations):
        known_outcomes = {
            "AGREEMENT",
            "REGRESSION",
            "KNOWN GAP — PREDICTED",
            "UNPREDICTED GAP",
            "UNVERIFIED",
        }
        unaccounted = Counter(
            row.outcome for row in evaluations if row.outcome not in known_outcomes
        )
        raise AssertionError(
            "evaluation accounting mismatch: "
            f"{accounted} categorized != {len(evaluations)} total; "
            f"unaccounted outcomes={dict(unaccounted)}"
        )
    scorable = len(evaluations) - unverified
    conforming = agreements + predicted_gaps
    score = (100.0 * conforming / scorable) if scorable else 0.0

    print("\nCANADIAN EVALUATION SUMMARY")
    print("=" * 42)
    print(f"Total files                         : {len(evaluations)}")
    print(f"Agreements (OK cases)               : {agreements}")
    print(f"Regressions (broken OK cases)       : {regressions}")
    print(f"Known gaps behaving as predicted    : {predicted_gaps}")
    print(f"Known gaps behaving unexpectedly    : {unexpected_gaps}")
    print(f"Unverified (not scored)             : {unverified}")
    print(f"Expectation-conformance score       : {conforming}/{scorable} ({score:.1f}%)")
    print(f"Runtime                             : {elapsed:.2f}s")
    print(f"LLM verification                    : {verification_status}")

    unexpected = [
        row for row in evaluations if row.outcome == "UNPREDICTED GAP"
    ]
    if unexpected:
        print("\n!!! UNPREDICTED KNOWN-GAP BEHAVIOUR — INVESTIGATE !!!")
        for row in unexpected:
            print(f"- {row.filename}: {row.detail}")
            print(f"  expected: {row.expected}")
            print(f"  actual:   {_format_actual(row.actual)}")

    disagreements = [
        row
        for row in evaluations
        if row.outcome in {
            "REGRESSION",
            "KNOWN GAP — PREDICTED",
            "UNPREDICTED GAP",
        }
    ]
    if disagreements:
        _print_table(disagreements, "DISAGREEMENTS AND DOCUMENTED GAPS")

    unverified_rows = [
        row for row in evaluations if row.outcome == "UNVERIFIED"
    ]
    if unverified_rows:
        _print_table(unverified_rows, "UNVERIFIED — REPORTED, NOT SCORED")

    if regressions or unexpected_gaps:
        print(
            f"\nFAIL: {regressions} regression(s), "
            f"{unexpected_gaps} unpredicted gap outcome(s)."
        )
        return 1
    print("\nPASS: no regressions or unpredicted gap outcomes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
