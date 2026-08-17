#!/usr/bin/env python3
"""
Runs detect_pii_hybrid(run_ner=True) over the downloaded ai4privacy English
sample and computes precision/recall/F1 per mapped taxonomy type, plus a
false-positive source-layer breakdown and verbatim FP/FN examples.

Read-only evaluation — does not modify any detector code.

Matching rule: a labeled span and a detection "match" if their normalized
values (lowercased, non-alphanumeric chars stripped) overlap as a substring
in either direction — per-sample, per-bucket greedy bipartite matching (each
detection can satisfy at most one labeled span and vice versa).

Usage:
    docker compose run --rm securescan-cpu python tests/external_ai4privacy/run_evaluation.py
"""

import json
import os
import re
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from detectors.hybrid_detector import detect_pii_hybrid  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_PATH = os.path.join(HERE, "ai4privacy_en_sample.json")
RESULTS_PATH = os.path.join(HERE, "results.json")

# ---------------------------------------------------------------------------
# Label mapping: ai4privacy label -> SecureScan taxonomy bucket. Several labels
# labels can feed the same bucket (GIVENNAME + SURNAME -> entity.person,
# because SecureScan reports a merged full-name span, not separate given/sur
# spans) — recall is measured per ai4privacy label (their granularity),
# precision per bucket (SecureScan granularity), since a detection does not know
# which ai4privacy label it "is".
# ---------------------------------------------------------------------------
LABEL_TO_BUCKET = {
    "GIVENNAME": "entity.person",
    "SURNAME": "entity.person",
    "DATE": "entity.date",
    "EMAIL": "contact.email",
    "TELEPHONENUM": "contact.phone",
    "CREDITCARDNUMBER": "identifier.financial.credit_card",
    "SOCIALNUM": "identifier.financial.ssn",
    "CITY": "entity.location",
    "DRIVERLICENSENUM": "identifier.government.drivers_license",
    "PASSPORTNUM": "identifier.government.passport",
    "ORGANISATION": "entity.organization",
    "URL": "technical.url",
}

# These two buckets match all province/sub-type variants in the taxonomy.
# (e.g. identifier.government.drivers_license_on, ..._bc, ..._ca) via prefix,
# since ai4privacy doesn't distinguish sub-formats either.
PREFIX_BUCKETS = {"identifier.government.drivers_license", "identifier.government.passport"}

BUCKETS = sorted(set(LABEL_TO_BUCKET.values()))

# Labels present in the dataset with no corresponding detector at all —
# listed explicitly in the report as out-of-scope, not silently dropped.
OUT_OF_SCOPE_LABELS = {
    "TITLE", "STREET", "BUILDINGNUM", "AGE", "ZIPCODE", "IDCARDNUM", "TAXNUM",
    "GENDER", "SEX", "TIME", "AMOUNT", "COUNTRY", "CURRENCY", "USERNAME",
    "ACCOUNTNUM", "SALARY",
}


def category_to_bucket(category: str):
    for b in BUCKETS:
        if b in PREFIX_BUCKETS:
            if category.startswith(b):
                return b
        elif category == b:
            return b
    return None


def normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def overlaps(a_norm: str, b_norm: str, min_len: int = 2) -> bool:
    if not a_norm or not b_norm:
        return False
    if len(a_norm) < min_len or len(b_norm) < min_len:
        return a_norm == b_norm
    return a_norm in b_norm or b_norm in a_norm


def match_spans(labeled_values, detected_items):
    """Greedy bipartite match. labeled_values: list[str].
    detected_items: list[dict] (each with 'value' + 'source').
    Returns (tp_count, fn_values, fp_items)."""
    labeled_norm = [(v, normalize(v)) for v in labeled_values]
    detected_norm = [(d, normalize(d["value"])) for d in detected_items]
    matched_detected = set()
    tp = 0
    fn_values = []

    for lv, ln in labeled_norm:
        found = False
        for i, (_d, dn) in enumerate(detected_norm):
            if i in matched_detected:
                continue
            if overlaps(ln, dn):
                matched_detected.add(i)
                found = True
                break
        if found:
            tp += 1
        else:
            fn_values.append(lv)

    fp_items = [detected_norm[i][0] for i in range(len(detected_norm)) if i not in matched_detected]
    return tp, fn_values, fp_items


def main():
    with open(SAMPLE_PATH, "r", encoding="utf-8") as f:
        samples = json.load(f)

    print(f"[i] Loaded {len(samples)} samples")

    # Confirm every label in the dataset is accounted for (mapped or
    # explicitly out-of-scope) — fail loudly on anything unexpected rather
    # than silently miscounting.
    seen_labels = set()
    for s in samples:
        for span in s["privacy_mask"]:
            seen_labels.add(span["label"])
    unaccounted = seen_labels - set(LABEL_TO_BUCKET) - OUT_OF_SCOPE_LABELS
    if unaccounted:
        print(f"[!] WARNING: labels present but not classified: {unaccounted}")

    stats = {b: {"tp": 0, "fn": 0, "fp": 0} for b in BUCKETS}
    fp_source_counts = {b: {} for b in BUCKETS}
    fp_source_counts_overall = {}
    fp_examples = []
    fn_examples = []

    t0 = time.time()
    for idx, s in enumerate(samples, start=1):
        text = s["source_text"]
        matches = detect_pii_hybrid(text, run_ner=True)

        # Group labels by bucket for this sample
        labeled_by_bucket = {}
        for span in s["privacy_mask"]:
            bucket = LABEL_TO_BUCKET.get(span["label"])
            if bucket:
                labeled_by_bucket.setdefault(bucket, []).append(span["value"])

        # Group SecureScan detections by bucket for this sample.
        detected_by_bucket = {}
        for category, dets in matches.items():
            if category == "_metadata":
                continue
            bucket = category_to_bucket(category)
            if bucket:
                detected_by_bucket.setdefault(bucket, []).extend(dets)

        for bucket in set(labeled_by_bucket) | set(detected_by_bucket):
            labeled_values = labeled_by_bucket.get(bucket, [])
            detected_items = detected_by_bucket.get(bucket, [])
            tp, fn_values, fp_items = match_spans(labeled_values, detected_items)

            stats[bucket]["tp"] += tp
            stats[bucket]["fn"] += len(fn_values)
            stats[bucket]["fp"] += len(fp_items)

            for item in fp_items:
                src = item.get("source", "unknown")
                fp_source_counts[bucket][src] = fp_source_counts[bucket].get(src, 0) + 1
                fp_source_counts_overall[src] = fp_source_counts_overall.get(src, 0) + 1
                fp_examples.append({
                    "bucket": bucket,
                    "detected_value": item["value"],
                    "source": src,
                    "confidence": item.get("confidence"),
                    "sample_uid": s.get("uid"),
                    "sample_text": text,
                })

            for lv in fn_values:
                fn_examples.append({
                    "bucket": bucket,
                    "labeled_value": lv,
                    "sample_uid": s.get("uid"),
                    "sample_text": text,
                })

        if idx % 200 == 0:
            elapsed = time.time() - t0
            rate = idx / elapsed
            eta = (len(samples) - idx) / rate
            print(f"[i] {idx}/{len(samples)} samples, {elapsed:.0f}s elapsed, ETA {eta / 60:.1f}m")

    elapsed = time.time() - t0
    print(f"[✓] Processed {len(samples)} samples in {elapsed:.0f}s ({elapsed / len(samples) * 1000:.0f} ms/sample)")

    # Per-bucket precision/recall/F1
    per_bucket = {}
    total_tp = total_fp = total_fn = 0
    for b in BUCKETS:
        tp, fp, fn = stats[b]["tp"], stats[b]["fp"], stats[b]["fn"]
        total_tp += tp
        total_fp += fp
        total_fn += fn
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        f1 = (2 * precision * recall / (precision + recall)) if precision and recall and (precision + recall) else None
        per_bucket[b] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall, "f1": f1,
            "fp_sources": fp_source_counts[b],
        }

    overall_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else None
    overall_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else None
    overall_f1 = (
        2 * overall_precision * overall_recall / (overall_precision + overall_recall)
        if overall_precision and overall_recall and (overall_precision + overall_recall) else None
    )

    results = {
        "num_samples": len(samples),
        "elapsed_seconds": elapsed,
        "seen_labels": sorted(seen_labels),
        "unaccounted_labels": sorted(unaccounted),
        "per_bucket": per_bucket,
        "overall": {
            "tp": total_tp, "fp": total_fp, "fn": total_fn,
            "precision": overall_precision, "recall": overall_recall, "f1": overall_f1,
        },
        "fp_source_counts_overall": fp_source_counts_overall,
        "fp_examples": fp_examples,
        "fn_examples": fn_examples,
    }

    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"[✓] Wrote {RESULTS_PATH}")
    print()
    print(f"{'BUCKET':<45} {'TP':>6} {'FP':>6} {'FN':>6} {'PREC':>7} {'REC':>7} {'F1':>7}")
    for b in BUCKETS:
        r = per_bucket[b]
        p = f"{r['precision']:.3f}" if r["precision"] is not None else "  n/a"
        rc = f"{r['recall']:.3f}" if r["recall"] is not None else "  n/a"
        f1v = f"{r['f1']:.3f}" if r["f1"] is not None else "  n/a"
        print(f"{b:<45} {r['tp']:>6} {r['fp']:>6} {r['fn']:>6} {p:>7} {rc:>7} {f1v:>7}")
    print(f"{'OVERALL':<45} {total_tp:>6} {total_fp:>6} {total_fn:>6} "
          f"{overall_precision:.3f} {overall_recall:.3f} {overall_f1:.3f}")


if __name__ == "__main__":
    main()
