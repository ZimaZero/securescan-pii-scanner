#!/usr/bin/env python3
"""
Compares a baseline (verification OFF) and verified (verification ON) JSON
report from the same scanned folder, to measure the LLM verification
layer's before/after impact.

Read-only analysis — does not touch scanner/detector/verifier code, just
reads the two JSON reports produced by `scanner.py --path ... --no-verify`
and `scanner.py --path ... --verify`.

Usage:
    docker compose run --rm securescan-cpu python tests/external_enron/compare_runs.py \\
        <baseline.json> <verified.json> [--baseline-seconds S] [--verified-seconds S]

Prints a report and also returns the underlying dict (importable for
EVALUATION.md generation / ad-hoc inspection).
"""

import argparse
import json
import random
import sys
from collections import Counter, defaultdict

SEED = 1337  # project convention — used only for the spot-check sample


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _risk_band_counts(report):
    """Findings by risk band + files by risk band, from a JSON report."""
    finding_bands = Counter()
    file_bands = Counter()
    for f in report.get("files", []):
        score = f.get("score", 0)
        if score >= 70:
            file_bands["HIGH"] += 1
        elif score >= 30:
            file_bands["MEDIUM"] += 1
        elif score > 0:
            file_bands["LOW"] += 1
        else:
            file_bands["NONE"] += 1
        for category, detections in f.get("matches", {}).items():
            if category == "_metadata" or not isinstance(detections, list):
                continue
            for d in detections:
                risk = str(d.get("risk_level", "UNKNOWN")).upper()
                finding_bands[risk] += 1
    return finding_bands, file_bands


def _file_band(score):
    if score >= 70:
        return "HIGH"
    if score >= 30:
        return "MEDIUM"
    if score > 0:
        return "LOW"
    return "NONE"


def _files_that_changed_band(baseline, verified):
    b_by_file = {f["file"]: f.get("score", 0) for f in baseline.get("files", [])}
    v_by_file = {f["file"]: f.get("score", 0) for f in verified.get("files", [])}
    changed = []
    for path, b_score in b_by_file.items():
        v_score = v_by_file.get(path)
        if v_score is None:
            continue
        b_band = _file_band(b_score)
        v_band = _file_band(v_score)
        if b_band != v_band:
            changed.append({
                "file": path,
                "baseline_score": b_score,
                "baseline_band": b_band,
                "verified_score": v_score,
                "verified_band": v_band,
            })
    return changed


def _demotions_by_category_and_source(verified):
    by_category = Counter()
    by_source = Counter()
    demoted = []
    for f in verified.get("files", []):
        file_path = f.get("file", "Unknown")
        for category, detections in f.get("matches", {}).items():
            if category == "_metadata" or not isinstance(detections, list):
                continue
            for d in detections:
                if d.get("llm_verdict") == "FALSE_POSITIVE":
                    by_category[category] += 1
                    by_source[str(d.get("source", ""))] += 1
                    demoted.append({
                        "file": file_path,
                        "value": str(d.get("value", "")),
                        "type": category,
                        "source": str(d.get("source", "")),
                        "original_risk_level": str(d.get("original_risk_level", "UNKNOWN")),
                        "llm_reason": str(d.get("llm_reason", "")),
                    })
    return by_category, by_source, demoted


def _routed_not_demoted(verified):
    """All findings with llm_verified=True and llm_verdict=LEGITIMATE."""
    out = []
    for f in verified.get("files", []):
        file_path = f.get("file", "Unknown")
        for category, detections in f.get("matches", {}).items():
            if category == "_metadata" or not isinstance(detections, list):
                continue
            for d in detections:
                if d.get("llm_verified") is True and d.get("llm_verdict") == "LEGITIMATE":
                    out.append({
                        "file": file_path,
                        "value": str(d.get("value", "")),
                        "type": category,
                        "source": str(d.get("source", "")),
                        "llm_reason": str(d.get("llm_reason", "")),
                    })
    return out


def compare(baseline_path, verified_path, baseline_seconds=None, verified_seconds=None):
    baseline = _load(baseline_path)
    verified = _load(verified_path)

    b_finding_bands, b_file_bands = _risk_band_counts(baseline)
    v_finding_bands, v_file_bands = _risk_band_counts(verified)

    changed_files = _files_that_changed_band(baseline, verified)

    vsum = verified.get("verification", {})
    routed = vsum.get("routed", 0)
    demoted = vsum.get("demoted", 0)
    errors = vsum.get("errors", 0)

    by_category, by_source, demoted_findings = _demotions_by_category_and_source(verified)

    demoted_findings_sorted = sorted(demoted_findings, key=lambda d: (d["type"], d["file"]))
    top15 = demoted_findings_sorted[:15]

    not_demoted = _routed_not_demoted(verified)
    rng = random.Random(SEED)
    spot_check = rng.sample(not_demoted, min(10, len(not_demoted)))

    result = {
        "finding_bands": {"baseline": dict(b_finding_bands), "verified": dict(v_finding_bands)},
        "file_bands": {"baseline": dict(b_file_bands), "verified": dict(v_file_bands)},
        "files_changed_band": changed_files,
        "verification_summary": {"routed": routed, "demoted": demoted, "errors": errors},
        "demotions_by_category": dict(by_category),
        "demotions_by_source": dict(by_source),
        "top15_demoted": top15,
        "spot_check_legitimate": spot_check,
        "routed_not_demoted_total": len(not_demoted),
        "wall_clock": {
            "baseline_seconds": baseline_seconds,
            "verified_seconds": verified_seconds,
            "added_seconds": (verified_seconds - baseline_seconds)
            if (baseline_seconds is not None and verified_seconds is not None) else None,
            "seconds_per_routed_finding": (
                (verified_seconds - baseline_seconds) / routed
                if (baseline_seconds is not None and verified_seconds is not None and routed)
                else None
            ),
        },
    }
    return result


def _print_report(result):
    print("=" * 70)
    print("FINDINGS BY RISK BAND (baseline vs verified)")
    print("=" * 70)
    bands = ["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
    for band in bands:
        b = result["finding_bands"]["baseline"].get(band, 0)
        v = result["finding_bands"]["verified"].get(band, 0)
        print(f"  {band:8s}  baseline={b:6d}  verified={v:6d}  delta={v - b:+6d}")

    print()
    print("=" * 70)
    print("FILES BY RISK BAND (baseline vs verified)")
    print("=" * 70)
    for band in ["HIGH", "MEDIUM", "LOW", "NONE"]:
        b = result["file_bands"]["baseline"].get(band, 0)
        v = result["file_bands"]["verified"].get(band, 0)
        print(f"  {band:8s}  baseline={b:6d}  verified={v:6d}  delta={v - b:+6d}")

    print()
    print(f"  Files whose band changed baseline->verified: {len(result['files_changed_band'])}")
    for c in result["files_changed_band"][:30]:
        print(f"    {c['baseline_band']:6s} -> {c['verified_band']:6s}  "
              f"({c['baseline_score']} -> {c['verified_score']})  {c['file']}")
    if len(result["files_changed_band"]) > 30:
        print(f"    ... and {len(result['files_changed_band']) - 30} more")

    print()
    print("=" * 70)
    print("VERIFICATION SUMMARY")
    print("=" * 70)
    vs = result["verification_summary"]
    print(f"  routed={vs['routed']}  demoted={vs['demoted']}  errors={vs['errors']}")
    if vs["routed"]:
        print(f"  demotion rate = {vs['demoted'] / vs['routed']:.1%}")

    print()
    print("=" * 70)
    print("DEMOTIONS BY TAXONOMY CATEGORY")
    print("=" * 70)
    for cat, n in sorted(result["demotions_by_category"].items(), key=lambda x: -x[1]):
        print(f"  {cat:45s} {n:5d}")

    print()
    print("=" * 70)
    print("DEMOTIONS BY SOURCE LAYER")
    print("=" * 70)
    for src, n in sorted(result["demotions_by_source"].items(), key=lambda x: -x[1]):
        print(f"  {src:20s} {n:5d}")

    print()
    print("=" * 70)
    print("TOP 15 DEMOTED FINDINGS (verbatim)")
    print("=" * 70)
    for d in result["top15_demoted"]:
        print(f"  file: {d['file']}")
        print(f"  type: {d['type']}  source: {d['source']}  original_risk: {d['original_risk_level']}")
        print(f"  value: {d['value']!r}")
        print(f"  llm_reason: {d['llm_reason']!r}")
        print("  " + "-" * 60)

    print()
    print("=" * 70)
    print(f"SPOT-CHECK: 10 random routed-but-NOT-demoted findings "
          f"(of {result['routed_not_demoted_total']} total LEGITIMATE verdicts)")
    print("=" * 70)
    for d in result["spot_check_legitimate"]:
        print(f"  file: {d['file']}")
        print(f"  type: {d['type']}  source: {d['source']}")
        print(f"  value: {d['value']!r}")
        print(f"  llm_reason: {d['llm_reason']!r}")
        print("  " + "-" * 60)

    print()
    print("=" * 70)
    print("WALL CLOCK")
    print("=" * 70)
    wc = result["wall_clock"]
    print(f"  baseline_seconds = {wc['baseline_seconds']}")
    print(f"  verified_seconds = {wc['verified_seconds']}")
    print(f"  added_seconds = {wc['added_seconds']}")
    print(f"  seconds_per_routed_finding = {wc['seconds_per_routed_finding']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_json")
    parser.add_argument("verified_json")
    parser.add_argument("--baseline-seconds", type=float, default=None)
    parser.add_argument("--verified-seconds", type=float, default=None)
    parser.add_argument("--out", default=None, help="optional path to dump result dict as JSON")
    args = parser.parse_args()

    result = compare(
        args.baseline_json,
        args.verified_json,
        baseline_seconds=args.baseline_seconds,
        verified_seconds=args.verified_seconds,
    )
    _print_report(result)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"\n[✓] Wrote {args.out}")


if __name__ == "__main__":
    main()
