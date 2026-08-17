#!/usr/bin/env python3
"""Extract text once per file across all evaluation corpora, cached to JSONL.

Reuses discovery.scan_file(..., return_text=True) exactly like
docs/evidence/capture_mrz_corpora.py, so extraction/OCR behavior is
identical to production. This cache is shared by both GLiNER model runs
(production gliner_medium-v2.1 and urchade/gliner_multi_pii-v1) so OCR
only ever runs once per file.
"""
from __future__ import annotations

import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from discovery import SUPPORTED_EXTENSIONS, scan_file
from tests.run_specimen_eval import _resolve_corpus_file

SPECIMEN = Path("/external_corpus/specimen_corpus")
TEST_ANCHOR = Path("/external_corpus/test_anchor")
OUT = Path("/tmp/gliner_pii_eval_extract.jsonl")


def files_under(path: Path):
    if not path.exists():
        return []
    return sorted(
        p for p in path.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def corpus_files():
    corpora = {
        "canadian_eval": files_under(ROOT / "tests/canadian_eval_data"),
        "stress": files_under(ROOT / "tests/stress_data"),
        "format": files_under(ROOT / "tests/format_data"),
        "test_anchor": files_under(TEST_ANCHOR),
        "external_octopii": files_under(ROOT / "tests/external_octopii"),
        "enron": files_under(ROOT / "tests/external_enron/sample"),
    }
    gt_path = ROOT / "tests/specimen_eval_docs/GROUND_TRUTH.csv"
    with gt_path.open(encoding="utf-8-sig", newline="") as handle:
        seen = set()
        specimen_files = []
        for row in csv.DictReader(handle):
            p = _resolve_corpus_file(SPECIMEN, row["file"])
            if p not in seen:
                seen.add(p)
                specimen_files.append(p)
    corpora["specimen"] = specimen_files
    return corpora


def scan_one(corpus, path):
    result = scan_file(str(path), verify=False, run_ner=False, return_text=True)
    return {
        "corpus": corpus,
        "path": str(path),
        "scan_status": result.get("scan_status"),
        "failure_reason": result.get("failure_reason"),
        "text": result.get("_text", ""),
    }


def main():
    corpora = corpus_files()
    counts = {c: len(ps) for c, ps in corpora.items()}
    print(json.dumps({"counts": counts, "total": sum(counts.values())}), flush=True)

    prior = {}
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("scan_status") == "scanned":
                prior[(row["corpus"], row["path"])] = row

    items = [(c, p) for c, ps in corpora.items() for p in ps]
    completed = list(prior.values())
    todo = [(c, p) for c, p in items if (c, str(p)) not in prior]
    print(json.dumps({"reused": len(completed), "to_extract": len(todo)}), flush=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(scan_one, c, p): (c, p) for c, p in todo}
        n = 0
        for fut in as_completed(futures):
            completed.append(fut.result())
            n += 1
            if n % 25 == 0:
                print(f"{n}/{len(todo)}", flush=True)
                completed.sort(key=lambda r: (r["corpus"], r["path"]))
                OUT.write_text(
                    "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in completed),
                    encoding="utf-8",
                )

    completed.sort(key=lambda r: (r["corpus"], r["path"]))
    OUT.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in completed),
        encoding="utf-8",
    )
    chars = sum(len(r["text"]) for r in completed)
    failed = sum(1 for r in completed if r["scan_status"] != "scanned")
    print(json.dumps({
        "files": len(completed), "failed": failed, "total_chars": chars, "out": str(OUT),
    }), flush=True)


if __name__ == "__main__":
    main()
