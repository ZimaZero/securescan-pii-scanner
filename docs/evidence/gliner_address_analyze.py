#!/usr/bin/env python3
"""Compare production gliner_medium-v2.1 + "address" (docs/evidence/gliner_medium5_raw.jsonl)
against gliner_multi_pii-v1's 135 "address" findings (docs/evidence/gliner_pii_raw.jsonl).

Recovery: same (corpus, file) and a normalized-value match (case/whitespace-
folded exact match, OR one value containing the other -- span boundaries
differ slightly between models/chunking, e.g. one model keeps a trailing
unit number the other drops).

Writes docs/evidence/gliner_address_comparison.json and prints a summary.
"""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MEDIUM5_RAW = ROOT / "docs/evidence/gliner_medium5_raw.jsonl"
PII_RAW = ROOT / "docs/evidence/gliner_pii_raw.jsonl"
OUT = ROOT / "docs/evidence/gliner_address_comparison.json"


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


def norm(v):
    return re.sub(r"\s+", " ", v.strip().lower())


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


def main():
    medium5 = [r for r in load_jsonl(MEDIUM5_RAW) if r["label"] == "address"]
    pii = [r for r in load_jsonl(PII_RAW) if r["label"] == "address"]

    print(f"medium5 address findings: {len(medium5)}")
    print(f"pii-model address findings: {len(pii)}")

    pii_by_file = {}
    for r in pii:
        pii_by_file.setdefault((r["corpus"], r["file"]), []).append(r)
    medium5_by_file = {}
    for r in medium5:
        medium5_by_file.setdefault((r["corpus"], r["file"]), []).append(r)

    def matches(a, b):
        na, nb = norm(a), norm(b)
        return na == nb or na in nb or nb in na

    recovered = []
    pii_only = []
    for key, rows in pii_by_file.items():
        med_rows = medium5_by_file.get(key, [])
        for pr in rows:
            hit = next((mr for mr in med_rows if matches(pr["value"], mr["value"])), None)
            if hit:
                recovered.append({"pii": pr, "medium5": hit})
            else:
                pii_only.append(pr)

    recovered_pii_values = {(r["pii"]["corpus"], r["pii"]["file"], norm(r["pii"]["value"])) for r in recovered}
    medium5_only = []
    for key, rows in medium5_by_file.items():
        pii_rows = pii_by_file.get(key, [])
        for mr in rows:
            hit = any(matches(mr["value"], pr["value"]) for pr in pii_rows)
            if not hit:
                medium5_only.append(mr)

    report = {
        "medium5_total": len(medium5),
        "pii_total": len(pii),
        "recovered_count": len(recovered),
        "recovered_pct_of_pii": round(100 * len(recovered) / max(1, len(pii)), 1),
        "pii_only_count": len(pii_only),
        "medium5_only_count": len(medium5_only),
        "medium5_confidence": conf_stats(medium5),
        "pii_confidence": conf_stats(pii),
        "recovered_examples": [
            {"file": r["pii"]["file"].split("/")[-1], "pii_value": r["pii"]["value"],
             "pii_conf": r["pii"]["confidence"], "medium5_value": r["medium5"]["value"],
             "medium5_conf": r["medium5"]["confidence"]}
            for r in recovered[:40]
        ],
        "pii_only_examples": [
            {"file": r["file"].split("/")[-1], "value": r["value"], "confidence": r["confidence"]}
            for r in pii_only[:40]
        ],
        "medium5_only_examples": [
            {"file": r["file"].split("/")[-1], "value": r["value"], "confidence": r["confidence"]}
            for r in medium5_only[:60]
        ],
    }
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if not k.endswith("examples")}, indent=2))


if __name__ == "__main__":
    main()
