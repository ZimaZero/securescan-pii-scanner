#!/usr/bin/env python3
"""Evaluate the external real-document specimen corpus.

This manual harness keeps extraction failures separate from detector failures:
an expected identifier can only expose a detector defect when the production
extractor first recovered that value.  The corpus is read-only and no normal
scan reports are written.

    Usage:
    docker compose run --rm securescan-cpu python tests/run_specimen_eval.py [corpus_path]

    Set SECURESCAN_SPECIMEN_CORPUS_DIR to use an external corpus by default,
    or pass its root as the optional corpus_path argument. The corpus is a
    read-only collection of Canadian identity-card and health-card specimen
    images; its expected relative paths and identifiers are recorded in
    tests/specimen_eval_docs/GROUND_TRUTH.csv and the images are not shipped
    with this repository.

Exit status is non-zero only for an identifier false positive on a scored
negative row, a regression against ``BASELINE_POSITIVE_PASSES``, or a fatal
configuration/scan error.  Extraction-limited rows do not fail the command.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import DEFAULT_MAX_WORKERS  # noqa: E402
from discovery import SUPPORTED_EXTENSIONS, scan_file  # noqa: E402


# The real specimen corpus is external, read-only test data and is not shipped
# with the repository. Keep the default project-relative so a contributor can
# place a local copy at a predictable path; SECURESCAN_SPECIMEN_CORPUS_DIR or
# the positional corpus_path argument can point to any other corpus root.
DEFAULT_CORPUS_DIR = Path(
    os.environ.get(
        "SECURESCAN_SPECIMEN_CORPUS_DIR",
        str(PROJECT_ROOT / "tests" / "specimen_corpus"),
    )
)
GROUND_TRUTH_PATH = PROJECT_ROOT / "tests" / "specimen_eval_docs" / "GROUND_TRUTH.csv"
EXPECTED_COUNTS = Counter({"POSITIVE": 46, "NEGATIVE": 35})
VALID_VERDICTS = set(EXPECTED_COUNTS)
VALID_READ_CONFIDENCE = {"certain", "probable", "unreadable"}
OUTCOMES = {
    "PASS",
    "DETECTOR-MISS",
    "EXTRACTION-LIMITED",
    "VALUE-MISMATCH",
    "CATEGORY-MISMATCH",
    "FALSE-POSITIVE",
    "UNSCORED",
}

# Ratified after the first real-corpus measurement.  A row in this set moving
# away from PASS is a regression and makes the harness exit non-zero.  Keys use
# the same separator-insensitive value normalization as the evaluator so a
# harmless display-format change in the CSV cannot invalidate the baseline.
BASELINE_POSITIVE_PASSES: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "Canadian_Driver_Licence_Specimens/BC/BC_front_back_specimen.jpg",
            "2222222",
        ),
        (
            "Canadian_Driver_Licence_Specimens/QC/QC_front_sample.jpg",
            "l153117127408",
        ),
        (
            "Canadian_Driver_Licence_Specimens/SK/SK_front_specimens.png",
            "20000030",
        ),
        (
            "Canadian_Health_Card_Specimens_v2/cards/AB/AB_mobile_current.jpg",
            "123450000",
        ),
        (
            "Canadian_Health_Card_Specimens_v2/cards/AB/AB_standalone_current.jpg",
            "123450000",
        ),
        (
            "Canadian_ID_Specimens/Canadian_Passport/passport_new_data_page_mrz_annotated.jpg",
            "p123456aa",
        ),
        (
            "Canadian_ID_Specimens/Canadian_Passport/Canada_passport-data-page-large_2023.jpeg",
            "p123456aa",
        ),
    }
)


@dataclass(frozen=True)
class Finding:
    category: str
    value: str
    source: str


@dataclass
class Evaluation:
    row_number: int
    filename: str
    jurisdiction: str
    doc_type: str
    verdict: str
    expected_type: str
    expected_value: str
    read_confidence: str
    outcome: str
    expected_in_text: bool
    findings: list[Finding]
    detail: str
    regression: bool = False


def _normalize_value(value: str) -> str:
    """Normalize exactly as the ground-truth contract specifies."""
    return re.sub(r"[\s-]+", "", value).casefold()


def _identifier_findings(result: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    matches = result.get("matches")
    if not isinstance(matches, dict):
        return findings
    for category, items in matches.items():
        category = str(category)
        if not category.startswith("identifier.") or not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            findings.append(
                Finding(
                    category=category,
                    value=str(item.get("value", "")),
                    source=str(item.get("source", "unknown")),
                )
            )
    return sorted(findings, key=lambda item: (item.category, item.value, item.source))


def _expected_tokens(expected_type: str) -> set[str]:
    return {token.strip() for token in expected_type.split("+") if token.strip()}


def _category_matches_token(category: str, token: str) -> bool:
    """Map the CSV's detector-type vocabulary onto dotted taxonomy values.

    ``drivers_license_ca`` is the CSV's conceptual Canadian-licence label, so
    province-specific licence taxonomy is a valid refinement.  Passport rows
    deliberately name both accepted routes: direct passport or MRZ.
    Everything else requires the exact verified taxonomy suffix; an
    unverified/reconstructed tier is therefore a CATEGORY-MISMATCH.
    """
    if token == "drivers_license_ca":
        return ".drivers_license" in category
    if token == "mrz":
        return ".mrz" in category
    return category == f"identifier.government.{token}"


def _category_matches_expected(category: str, expected_type: str) -> bool:
    return any(
        _category_matches_token(category, token)
        for token in _expected_tokens(expected_type)
    )


def _same_field_family(category: str, expected_type: str) -> bool:
    tokens = _expected_tokens(expected_type)
    if "drivers_license_ca" in tokens:
        return "drivers_license" in category
    if any(token.startswith("health_card_") for token in tokens):
        return "health_card" in category
    if tokens & {"mrz", "passport_ca"}:
        return ".mrz" in category or "passport" in category
    if "uci" in tokens:
        return category.endswith(".uci")
    if "status_card_registration" in tokens:
        return category.endswith(".status_card_registration")
    return any(token in category for token in tokens)


def _baseline_key(entry: dict[str, str]) -> tuple[str, str]:
    return entry["file"], _normalize_value(entry["expected_value"])


def _load_ground_truth() -> list[dict[str, str]]:
    with GROUND_TRUTH_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    required = {
        "file",
        "jurisdiction",
        "doc_type",
        "expected_identifier_type",
        "expected_value",
        "verdict",
        "read_confidence",
        "notes",
    }
    if not rows:
        raise ValueError("GROUND_TRUTH.csv has no data rows")
    if set(rows[0]) != required:
        raise ValueError(
            "GROUND_TRUTH.csv columns differ from the required schema: "
            f"{sorted(rows[0])}"
        )

    seen: set[str] = set()
    verdicts: Counter[str] = Counter()
    for row_number, entry in enumerate(rows, 1):
        filename = entry["file"]
        if not filename or filename in seen:
            raise ValueError(f"row {row_number}: missing/duplicate file {filename!r}")
        seen.add(filename)
        verdict = entry["verdict"]
        if verdict not in VALID_VERDICTS:
            raise ValueError(f"{filename}: invalid verdict {verdict!r}")
        verdicts[verdict] += 1
        confidence = entry["read_confidence"]
        if confidence not in VALID_READ_CONFIDENCE:
            raise ValueError(f"{filename}: invalid read_confidence {confidence!r}")
        if verdict == "POSITIVE" and confidence != "unreadable":
            if not entry["expected_identifier_type"] or not entry["expected_value"]:
                raise ValueError(f"{filename}: scored POSITIVE row lacks expected truth")
        if verdict == "NEGATIVE" and (
            entry["expected_identifier_type"] or entry["expected_value"]
        ):
            raise ValueError(f"{filename}: NEGATIVE row carries expected truth")
        if entry["notes"].startswith("EXTRACTION-LIMITED") and verdict != "POSITIVE":
            raise ValueError(f"{filename}: known extraction limit must be POSITIVE")

    if verdicts != EXPECTED_COUNTS:
        raise ValueError(
            "GROUND_TRUTH.csv is not the ratified 46/35 version: "
            f"found {dict(verdicts)}"
        )

    current_keys = {
        _baseline_key(entry)
        for entry in rows
        if entry["verdict"] == "POSITIVE" and entry["read_confidence"] != "unreadable"
    }
    stale_baseline = BASELINE_POSITIVE_PASSES - current_keys
    if stale_baseline:
        raise ValueError(
            "baseline references missing/changed ground-truth rows: "
            + ", ".join(sorted(filename for filename, _value in stale_baseline))
        )
    return rows


def _resolve_corpus_file(corpus_dir: Path, filename: str) -> Path:
    candidates = [corpus_dir / filename]
    old_prefix = "Negative_Control_Documents/"
    if filename.startswith(old_prefix):
        candidates.append(
            corpus_dir
            / filename.replace(
                old_prefix, "Negative_Control_Document_Samples/", 1
            )
        )
    root = corpus_dir.resolve()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"ground-truth path escapes corpus root: {filename}")
        if resolved.is_file():
            return resolved
    raise ValueError(f"missing corpus file: {filename}")


def _validate_corpus(
    corpus_dir: Path, entries: list[dict[str, str]]
) -> dict[str, Path]:
    if not corpus_dir.exists():
        raise ValueError(
            f"specimen corpus is absent: {corpus_dir} "
            "(pass its location as the optional corpus_path argument)"
        )
    if not corpus_dir.is_dir():
        raise ValueError(f"specimen corpus path is not a directory: {corpus_dir}")

    paths = {
        entry["file"]: _resolve_corpus_file(corpus_dir, entry["file"])
        for entry in entries
    }
    for entry in entries:
        if entry["notes"].startswith("EXTRACTION-LIMITED"):
            continue
        suffix = paths[entry["file"]].suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"{entry['file']}: unsupported extension {suffix!r} is not "
                "documented as EXTRACTION-LIMITED"
            )
    return paths


def _scan_all(
    entries: list[dict[str, str]], paths: dict[str, Path]
) -> tuple[dict[str, dict[str, Any]], float]:
    scan_entries = [
        entry
        for entry in entries
        if not entry["notes"].startswith("EXTRACTION-LIMITED")
    ]
    results: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()

    def scan(entry: dict[str, str]) -> tuple[str, dict[str, Any]]:
        filename = entry["file"]
        result = scan_file(
            str(paths[filename]),
            verify=False,
            return_text=True,
            run_ner=True,
        )
        return filename, result

    with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
        futures = [executor.submit(scan, entry) for entry in scan_entries]
        for completed, future in enumerate(as_completed(futures), 1):
            filename, result = future.result()
            results[filename] = result
            print(
                f"\rScanning specimen corpus: {completed}/{len(scan_entries)}",
                end="",
                flush=True,
            )
    print()
    return results, time.perf_counter() - started


def _evaluate_positive(
    row_number: int,
    entry: dict[str, str],
    result: dict[str, Any] | None,
) -> Evaluation:
    common = dict(
        row_number=row_number,
        filename=entry["file"],
        jurisdiction=entry["jurisdiction"],
        doc_type=entry["doc_type"],
        verdict=entry["verdict"],
        expected_type=entry["expected_identifier_type"],
        expected_value=entry["expected_value"],
        read_confidence=entry["read_confidence"],
    )
    if entry["notes"].startswith("EXTRACTION-LIMITED"):
        return Evaluation(
            **common,
            outcome="EXTRACTION-LIMITED",
            expected_in_text=False,
            findings=[],
            detail=entry["notes"],
        )
    if entry["read_confidence"] == "unreadable":
        findings = _identifier_findings(result or {})
        return Evaluation(
            **common,
            outcome="UNSCORED",
            expected_in_text=False,
            findings=findings,
            detail="read_confidence=unreadable; reported without scoring",
        )

    result = result or {}
    source_text = str(result.get("_text", ""))
    expected_norm = _normalize_value(entry["expected_value"])
    expected_in_text = bool(expected_norm and expected_norm in _normalize_value(source_text))
    findings = _identifier_findings(result)

    if not expected_in_text:
        outcome = "EXTRACTION-LIMITED"
        detail = "expected value is absent from the raw extracted text"
    else:
        value_matches = [
            finding
            for finding in findings
            if _normalize_value(finding.value) == expected_norm
        ]
        correct = [
            finding
            for finding in value_matches
            if _category_matches_expected(finding.category, entry["expected_identifier_type"])
        ]
        if correct:
            outcome = "PASS"
            detail = "expected value and taxonomy category were found"
        elif value_matches:
            outcome = "CATEGORY-MISMATCH"
            detail = "expected value was found under a different taxonomy category"
        else:
            same_field = [
                finding
                for finding in findings
                if _same_field_family(
                    finding.category, entry["expected_identifier_type"]
                )
            ]
            if same_field:
                outcome = "VALUE-MISMATCH"
                detail = "identifier finding for the expected field has a different value"
            else:
                outcome = "DETECTOR-MISS"
                detail = "expected value is extractable but no matching identifier was produced"

    regression = _baseline_key(entry) in BASELINE_POSITIVE_PASSES and outcome != "PASS"
    return Evaluation(
        **common,
        outcome=outcome,
        expected_in_text=expected_in_text,
        findings=findings,
        detail=detail,
        regression=regression,
    )


def _evaluate_negative(
    row_number: int,
    entry: dict[str, str],
    result: dict[str, Any],
) -> Evaluation:
    findings = _identifier_findings(result)
    if entry["read_confidence"] == "unreadable":
        outcome = "UNSCORED"
        detail = "read_confidence=unreadable; reported without scoring"
    elif result.get("scan_status") != "scanned":
        outcome = "UNSCORED"
        detail = "file was not scanned: " + str(
            result.get("failure_reason") or result.get("scan_status") or "unknown"
        )
    elif findings:
        outcome = "FALSE-POSITIVE"
        detail = "negative control produced identifier.* finding(s)"
    else:
        outcome = "PASS"
        detail = "zero identifier.* findings"
    return Evaluation(
        row_number=row_number,
        filename=entry["file"],
        jurisdiction=entry["jurisdiction"],
        doc_type=entry["doc_type"],
        verdict=entry["verdict"],
        expected_type="",
        expected_value="",
        read_confidence=entry["read_confidence"],
        outcome=outcome,
        expected_in_text=False,
        findings=findings,
        detail=detail,
    )


def _evaluate_all(
    entries: list[dict[str, str]], results: dict[str, dict[str, Any]]
) -> list[Evaluation]:
    evaluations: list[Evaluation] = []
    for row_number, entry in enumerate(entries, 1):
        result = results.get(entry["file"])
        if entry["verdict"] == "POSITIVE":
            evaluations.append(_evaluate_positive(row_number, entry, result))
        else:
            evaluations.append(_evaluate_negative(row_number, entry, result or {}))
    unknown = {row.outcome for row in evaluations} - OUTCOMES
    if unknown:
        raise AssertionError(f"unhandled evaluation outcomes: {sorted(unknown)}")
    return evaluations


def _format_findings(findings: Iterable[Finding]) -> str:
    items = list(findings)
    if not items:
        return "zero identifier.* findings"
    return "; ".join(
        f"{finding.category}={finding.value!r} ({finding.source})"
        for finding in items
    )


def _print_non_passes(evaluations: list[Evaluation]) -> None:
    rows = [row for row in evaluations if row.outcome != "PASS"]
    if not rows:
        return
    print("\nNON-PASS OUTCOMES")
    print("=" * 118)
    print(f"{'ROW':>3}  {'FILE':<52}  {'OUTCOME':<20}  EXPECTED -> ACTUAL")
    print("-" * 118)
    for row in rows:
        expected = (
            f"{row.expected_type}={row.expected_value!r}"
            if row.verdict == "POSITIVE"
            else "zero identifier.* findings"
        )
        marker = " [REGRESSION]" if row.regression else ""
        print(
            f"{row.row_number:>3}  {row.filename:<52}  "
            f"{row.outcome + marker:<20}  {expected} -> "
            f"{_format_findings(row.findings)}"
        )
        print(f"{'':>3}  {'':<52}  {'':<20}  {row.detail}")


def _print_jurisdiction_breakdown(evaluations: list[Evaluation]) -> None:
    rows = [
        row
        for row in evaluations
        if row.verdict == "POSITIVE"
        and row.doc_type in {"drivers_licence", "health_card"}
    ]
    grouped: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in rows:
        grouped[(row.doc_type, row.jurisdiction)][row.outcome] += 1

    print("\nPOSITIVE LICENCE / HEALTH-CARD COVERAGE BY JURISDICTION")
    print("=" * 96)
    print(
        f"{'DOCUMENT':<17} {'JUR':<4} {'TOTAL':>5} {'PASS':>5} {'DET':>5} "
        f"{'EXT':>5} {'VALUE':>7} {'CAT':>5} {'UNSCORED':>8}"
    )
    print("-" * 96)
    for (doc_type, jurisdiction), counts in sorted(grouped.items()):
        total = sum(counts.values())
        print(
            f"{doc_type:<17} {jurisdiction:<4} {total:>5} "
            f"{counts['PASS']:>5} {counts['DETECTOR-MISS']:>5} "
            f"{counts['EXTRACTION-LIMITED']:>5} {counts['VALUE-MISMATCH']:>7} "
            f"{counts['CATEGORY-MISMATCH']:>5} {counts['UNSCORED']:>8}"
        )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate the real Canadian specimen corpus without modifying it."
    )
    parser.add_argument(
        "corpus_path",
        nargs="?",
        type=Path,
        default=DEFAULT_CORPUS_DIR,
        help=f"external corpus root (default: {DEFAULT_CORPUS_DIR})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    corpus_dir = args.corpus_path.expanduser().resolve()
    try:
        entries = _load_ground_truth()
        paths = _validate_corpus(corpus_dir, entries)
    except (OSError, ValueError, csv.Error) as exc:
        print(f"[FATAL] Specimen evaluation configuration error: {exc}")
        return 2

    print(f"[i] Ground truth: {GROUND_TRUTH_PATH.relative_to(PROJECT_ROOT)}")
    print(f"[i] Corpus: {corpus_dir} ({len(entries)} rows)")
    print("[i] Detection: production extraction/hybrid path; NER on; LLM verification off")
    print("[i] Known EXTRACTION-LIMITED rows are reported without detector evaluation")

    try:
        results, elapsed = _scan_all(entries, paths)
    except Exception as exc:
        print(f"\n[FATAL] Specimen scan aborted: {type(exc).__name__}: {exc}")
        return 2

    evaluations = _evaluate_all(entries, results)
    counts = Counter(row.outcome for row in evaluations)
    positives = [row for row in evaluations if row.verdict == "POSITIVE"]
    viable = sum(row.expected_in_text for row in positives)
    viable_pct = 100.0 * viable / len(positives) if positives else 0.0
    regressions = sum(row.regression for row in evaluations)

    print("\nSPECIMEN EVALUATION SUMMARY")
    print("=" * 48)
    print(f"Total rows                         : {len(evaluations)}")
    print(f"Passes                             : {counts['PASS']}")
    print(f"Detector misses                    : {counts['DETECTOR-MISS']}")
    print(f"Extraction-limited                 : {counts['EXTRACTION-LIMITED']}")
    print(f"Value mismatches                   : {counts['VALUE-MISMATCH']}")
    print(f"Category mismatches                : {counts['CATEGORY-MISMATCH']}")
    print(f"False positives on negatives       : {counts['FALSE-POSITIVE']}")
    print(f"Unscored                           : {counts['UNSCORED']}")
    print(f"OCR viability (positive ceiling)   : {viable}/{len(positives)} ({viable_pct:.1f}%)")
    print(f"Previously passing regressions     : {regressions}")
    print(f"Runtime                            : {elapsed:.2f}s")

    _print_non_passes(evaluations)
    _print_jurisdiction_breakdown(evaluations)

    if counts["FALSE-POSITIVE"] or regressions:
        print(
            f"\nFAIL: {counts['FALSE-POSITIVE']} negative false positive(s), "
            f"{regressions} previously passing regression(s)."
        )
        return 1
    print("\nPASS: no negative false positives or previously passing regressions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
