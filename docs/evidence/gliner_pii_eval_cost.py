#!/usr/bin/env python3
"""Controlled, production-thread-matched cost comparison on identical input.

Picks a fixed deterministic sample of files (already extracted) and times:
  - production gliner_medium-v2.1 via its real detect_entities_gliner() path
    (real config.GLINER_BACKEND, i.e. ONNX, GLINER_ONNX_THREADS=2)
  - gliner_multi_pii-v1, Torch backend (no ONNX export attempted), batched
    5-group label pass, torch.set_num_threads(2) to match the production
    ONNX thread cap so the comparison isolates backend/label-set cost
    rather than thread-count cost.

Run as two separate subprocess invocations (mode=medium / mode=pii) so
each gets a clean peak-RSS measurement via `/usr/bin/time -v`.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

EXTRACT_CACHE = Path("/tmp/gliner_pii_eval_extract.jsonl")
SAMPLE_N = 30
SEED = 1337


def pick_sample():
    rows = []
    with EXTRACT_CACHE.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("scan_status") == "scanned" and row.get("text", "").strip():
                rows.append(row)
    rng = random.Random(SEED)
    by_corpus = {}
    for r in rows:
        by_corpus.setdefault(r["corpus"], []).append(r)
    sample = []
    corpora = sorted(by_corpus)
    per = max(1, SAMPLE_N // len(corpora))
    for c in corpora:
        pool = by_corpus[c]
        rng.shuffle(pool)
        sample.extend(pool[:per])
    return sample[:SAMPLE_N]


def main():
    mode = sys.argv[1]
    sample = pick_sample()
    print(f"[*] {len(sample)} files, corpora={sorted(set(r['corpus'] for r in sample))}", flush=True)

    if mode == "medium":
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        import detectors.gliner_detector as gd
        t0 = time.time()
        gd._get_model()  # forces production load (ONNX by config)
        load_time = time.time() - t0
        t0 = time.time()
        total_ents = 0
        for row in sample:
            ents = gd.detect_entities_gliner(row["text"])
            total_ents += sum(len(v) for v in ents.values())
        wall = time.time() - t0
    else:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        import torch
        torch.set_num_threads(2)
        from gliner import GLiNER
        sys.path.insert(0, str(ROOT / "docs/evidence"))
        from gliner_pii_eval_scan import run_pii_batched, PII_MODEL
        t0 = time.time()
        model = GLiNER.from_pretrained(PII_MODEL, local_files_only=True)
        load_time = time.time() - t0
        t0 = time.time()
        total_ents = 0
        for row in sample:
            total_ents += len(run_pii_batched(model, row["text"]))
        wall = time.time() - t0

    total_chars = sum(len(r["text"]) for r in sample)
    print(json.dumps({
        "mode": mode, "files": len(sample), "total_chars": total_chars,
        "load_time_s": round(load_time, 2), "scan_wall_s": round(wall, 2),
        "chars_per_s": round(total_chars / max(wall, 1e-6), 1),
        "total_entities": total_ents,
    }), flush=True)


if __name__ == "__main__":
    main()
