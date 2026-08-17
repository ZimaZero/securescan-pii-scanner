#!/usr/bin/env python3
"""Analysis pass over the raw GLiNER-PII / GLiNER-medium / deterministic
dumps. Produces JSON summaries consumed when writing
docs/evidence/gliner_pii_model_comparison.md by hand (the manual-inspection
true/false-positive judgments in section 1 are not automatable and are
written into the report directly after reading the sampled rows this
script prints out).
"""
from __future__ import annotations

import csv
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVID = ROOT / "docs/evidence"

PII_RAW = EVID / "gliner_pii_raw.jsonl"
MEDIUM_RAW = EVID / "gliner_medium_raw.jsonl"
DET_RAW = EVID / "deterministic_raw.jsonl"
PII_MANIFEST = EVID / "gliner_pii_scan_manifest.jsonl"
MEDIUM_MANIFEST = EVID / "gliner_medium_scan_manifest.jsonl"
GROUND_TRUTH = ROOT / "tests/specimen_eval_docs/GROUND_TRUTH.csv"

# Labels the task calls out as already deterministically handled, mapped to
# a regex over SecureScan's taxonomy category strings (see
# detectors/hybrid_detector.py PII_TAXONOMY). "address" has no deterministic
# full-address detector (only postal_code) -- kept separate deliberately,
# see the report's overlap section.
DETERMINISTIC_LABEL_MAP = {
    "driver's license number": r"^identifier\.government\.drivers_license",
    "passport number": r"^identifier\.government\.(passport_ca|passport_generic|mrz_document_number)$",
    "passport_number": r"^identifier\.government\.(passport_ca|passport_generic|mrz_document_number)$",
    "health insurance id number": r"^identifier\.government(_unverified)?\.health_card",
    "health insurance number": r"^identifier\.government(_unverified)?\.health_card",
    "national health insurance number": r"^identifier\.government(_unverified)?\.health_card",
    "social security number": r"^identifier\.financial(_unverified)?\.(sin|ssn)$",
    "social_security_number": r"^identifier\.financial(_unverified)?\.(sin|ssn)$",
    "credit card number": r"^identifier\.financial(_unverified)?\.credit_card$",
    "date of birth": r"^identifier\.personal\.(dob|mrz_dob)$",
    "phone number": r"^contact\.phone$",
    "mobile phone number": r"^contact\.phone$",
    "landline phone number": r"^contact\.phone$",
    "fax number": r"^contact\.phone$",
    "email": r"^contact\.email$",
    "email address": r"^contact\.email$",
    "postal code": r"^contact\.address\.postal_code$",
}
# "address" is explicitly requested by the task but SecureScan has no
# deterministic full-street-address detector -- reported under NEW COVERAGE.
NO_DETERMINISTIC_EQUIVALENT = {"address"}


def load_jsonl(path):
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def norm_digits(v):
    return re.sub(r"\D", "", v or "")


def main():
    pii = load_jsonl(PII_RAW)
    medium = load_jsonl(MEDIUM_RAW)
    det = load_jsonl(DET_RAW)
    pii_manifest = load_jsonl(PII_MANIFEST)
    medium_manifest = load_jsonl(MEDIUM_MANIFEST)

    report = {}

    # ---- basic volume ----
    report["pii_total_entities"] = len(pii)
    report["medium_total_entities"] = len(medium)
    report["det_total_findings"] = len(det)
    report["pii_files_covered"] = len(set((r["corpus"], r["file"]) for r in pii_manifest))
    report["medium_files_covered"] = len(set((r["corpus"], r["file"]) for r in medium_manifest))

    # ---- per-corpus, per-label counts ----
    by_corpus_label = Counter((r["corpus"], r["label"]) for r in pii)
    report["pii_by_corpus_label"] = {f"{c}::{l}": n for (c, l), n in by_corpus_label.most_common()}

    by_label = Counter(r["label"] for r in pii)
    report["pii_by_label_total"] = dict(by_label.most_common())

    medium_by_corpus_label = Counter((r["corpus"], r["label"]) for r in medium)
    report["medium_by_corpus_label"] = {f"{c}::{l}": n for (c, l), n in medium_by_corpus_label.most_common()}

    # ---- confidence distribution ----
    def conf_stats(rows):
        confs = [r["confidence"] for r in rows]
        if not confs:
            return {}
        confs.sort()
        n = len(confs)
        return {
            "n": n, "min": confs[0], "p10": confs[int(n * .1)],
            "p50": confs[n // 2], "p90": confs[int(n * .9)], "max": confs[-1],
            "mean": round(statistics.mean(confs), 4),
        }

    report["pii_confidence_overall"] = conf_stats(pii)
    report["medium_confidence_overall"] = conf_stats(medium)
    report["pii_confidence_by_label"] = {
        label: conf_stats([r for r in pii if r["label"] == label])
        for label in by_label
    }

    # ---- new coverage: labels canonicalized, minus deterministic-mapped ones ----
    def canonical(label):
        return {"passport_number": "passport number",
                "social_security_number": "social security number"}.get(label, label)

    canon_counts = Counter()
    for r in pii:
        canon_counts[canonical(r["label"])] += 1
    new_coverage_labels = sorted(
        l for l in canon_counts
        if l not in DETERMINISTIC_LABEL_MAP or l in NO_DETERMINISTIC_EQUIVALENT
    )
    # entity labels shared with production gliner_medium (person/organization)
    # are not "no detector at all" -- carve those out separately.
    shared_with_medium = {"person", "organization"}
    report["new_coverage_labels"] = {
        l: canon_counts[l] for l in new_coverage_labels if l not in shared_with_medium
    }
    report["shared_with_production_ner_labels"] = {
        l: canon_counts[l] for l in shared_with_medium if l in canon_counts
    }

    # Stratified sample (up to 15) per new-coverage label for manual review
    by_label_rows = defaultdict(list)
    for r in pii:
        by_label_rows[canonical(r["label"])].append(r)
    samples = {}
    import random
    rng = random.Random(1337)
    for label in report["new_coverage_labels"]:
        rows = by_label_rows[label]
        rng.shuffle(rows)
        samples[label] = rows[:15]
    (EVID / "gliner_pii_new_coverage_samples.json").write_text(
        json.dumps(samples, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    report["new_coverage_sample_file"] = str(EVID / "gliner_pii_new_coverage_samples.json")

    # ---- overlap with deterministic layers ----
    det_by_file = defaultdict(list)
    for d in det:
        det_by_file[(d["corpus"], d["file"])].append(d)

    overlap_summary = {}
    for pii_label, pattern in DETERMINISTIC_LABEL_MAP.items():
        rx = re.compile(pattern)
        rows = [r for r in pii if r["label"] == pii_label]
        agree = disagree_det_only = disagree_pii_only = 0
        pairs_sample = []
        by_file = defaultdict(list)
        for r in rows:
            by_file[(r["corpus"], r["file"])].append(r)
        files_touched = set(by_file) | {
            k for k, vs in det_by_file.items()
            if any(rx.match(v.get("category", "")) for v in vs)
        }
        for key in files_touched:
            pii_vals = {norm_digits(r["value"]) or r["value"].strip().lower() for r in by_file.get(key, [])}
            det_vals = {
                norm_digits(v["value"]) or v["value"].strip().lower()
                for v in det_by_file.get(key, []) if rx.match(v.get("category", ""))
            }
            both = pii_vals & det_vals
            only_pii = pii_vals - det_vals
            only_det = det_vals - pii_vals
            agree += len(both)
            disagree_pii_only += len(only_pii)
            disagree_det_only += len(only_det)
            if (only_pii or only_det) and len(pairs_sample) < 5:
                pairs_sample.append({
                    "file": key[1], "corpus": key[0],
                    "pii_only": sorted(only_pii), "det_only": sorted(only_det),
                })
        overlap_summary[pii_label] = {
            "agree": agree, "pii_only_missed_by_det": disagree_pii_only,
            "det_only_missed_by_pii": disagree_det_only,
            "sample_disagreements": pairs_sample,
        }
    report["overlap_with_deterministic"] = overlap_summary

    # ---- specimen ground-truth adjudication ----
    if GROUND_TRUTH.exists():
        gt_rows = list(csv.DictReader(GROUND_TRUTH.open(encoding="utf-8-sig")))
        specimen_pii = [r for r in pii if r["corpus"] == "specimen"]
        specimen_det = [d for d in det if d["corpus"] == "specimen"]
        det_by_specfile = defaultdict(list)
        for d in specimen_det:
            det_by_specfile[Path(d["file"]).name].append(d)
        pii_by_specfile = defaultdict(list)
        for r in specimen_pii:
            pii_by_specfile[Path(r["file"]).name].append(r)

        adjudication = []
        for row in gt_rows:
            if row["verdict"] != "POSITIVE":
                continue
            fname = Path(row["file"]).name
            expected_norm = norm_digits(row["expected_value"]) or row["expected_value"].strip().lower()
            det_hit = any(
                (norm_digits(d["value"]) or d["value"].strip().lower()) == expected_norm
                for d in det_by_specfile.get(fname, [])
            )
            pii_hit = any(
                (norm_digits(r["value"]) or r["value"].strip().lower()) == expected_norm
                for r in pii_by_specfile.get(fname, [])
            )
            adjudication.append({
                "file": row["file"], "expected_type": row["expected_identifier_type"],
                "expected_value": row["expected_value"],
                "det_hit": det_hit, "pii_hit": pii_hit,
                "read_confidence": row["read_confidence"],
            })
        report["specimen_adjudication"] = adjudication
        report["specimen_adjudication_summary"] = Counter(
            (a["det_hit"], a["pii_hit"]) for a in adjudication
        ).most_common()

    # ---- noise: OCR-degraded text non-entities ----
    # Heuristic flag: value has runs of repeated identical characters (>=4)
    # or is majority non-alphabetic junk -- same shape as the documented
    # 'SEEEEEN' case. Manual review still required; this is a triage filter.
    def looks_like_ocr_junk(v):
        if re.search(r"(.)\1{3,}", v):
            return True
        letters = sum(c.isalpha() for c in v)
        return letters > 0 and letters / len(v) < 0.5 and len(v) > 3

    report["pii_ocr_junk_candidates"] = sum(1 for r in pii if looks_like_ocr_junk(r["value"]))
    report["medium_ocr_junk_candidates"] = sum(1 for r in medium if looks_like_ocr_junk(r["value"]))
    report["pii_ocr_junk_sample"] = [
        r for r in pii if looks_like_ocr_junk(r["value"])
    ][:25]

    # ---- cost ----
    def cost_stats(manifest):
        if not manifest:
            return {}
        # De-dupe by (corpus, file): a file with zero PII-model findings
        # never appears in the raw dump, so the resume-skip check can't
        # recognize it as done and reprocesses it after a restart -- same
        # entities either time (0), but a second manifest line. Keep the
        # last entry per file so cost totals aren't inflated by the retry.
        deduped = {}
        for m in manifest:
            deduped[(m["corpus"], m["file"])] = m
        manifest = list(deduped.values())
        total_s = sum(m["seconds"] for m in manifest)
        total_chars = sum(m["chars"] for m in manifest)
        return {
            "files": len(manifest), "total_wall_s": round(total_s, 1),
            "total_chars": total_chars,
            "chars_per_s": round(total_chars / max(total_s, 1e-6), 1),
        }
    report["pii_manifest_cost"] = cost_stats(pii_manifest)
    report["medium_manifest_cost"] = cost_stats(medium_manifest)

    out_path = EVID / "gliner_pii_analysis_summary.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"wrote {out_path}")
    print(json.dumps({
        k: v for k, v in report.items()
        if k not in ("specimen_adjudication", "pii_ocr_junk_sample", "new_coverage_sample_file")
    }, indent=2, default=str)[:6000])


if __name__ == "__main__":
    main()
