#!/usr/bin/env python3
"""Controlled, same-process, same-backend, same-thread-count cost comparison:
production gliner_medium-v2.1 (ONNX, GLINER_ONNX_THREADS) with LABELS=4 vs.
LABELS=5 ("address" appended), over byte-identical cached text.

Isolates exactly one variable (label-list length). Both arms use the same
loaded ONNX model object, the same gd._chunks() chunking, and the same
threshold -- only the `labels` argument passed to model.predict_entities()
differs. Order is interleaved (4,5,4,5) across two repetitions so any
warmup/thermal drift affects both arms symmetrically rather than biasing
whichever arm runs first.

Sample: the identical 30-file, seed-1337, per-corpus-proportional sample
docs/evidence/gliner_pii_eval_cost.py already used for the pii-model cost
comparison (imported directly, not re-implemented), so this number sits in
the same evidentiary frame as the existing cost section of
docs/evidence/gliner_pii_model_comparison.md.

Usage:
    docker compose run --rm securescan-cpu python docs/evidence/gliner_address_cost.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "docs/evidence"))

import detectors.gliner_detector as gd  # noqa: E402
from gliner_pii_eval_cost import pick_sample  # noqa: E402
from gliner_pii_eval_scan import chunks_with_offsets  # noqa: E402

LABELS_4 = list(gd.LABELS)
LABELS_5 = list(gd.LABELS) + ["address"]


def run_pass(model, sample, labels):
    total_ents = 0
    t0 = time.time()
    for row in sample:
        text = row["text"]
        for offset, chunk in chunks_with_offsets(text, model):
            ents = model.predict_entities(chunk, labels, threshold=gd.MIN_CONFIDENCE)
            total_ents += len(ents)
    return time.time() - t0, total_ents


def main():
    sample = pick_sample()
    total_chars = sum(len(r["text"]) for r in sample)
    print(f"[*] {len(sample)} files, {total_chars} chars, "
          f"corpora={sorted(set(r['corpus'] for r in sample))}", flush=True)

    t0 = time.time()
    model = gd._get_model()  # forces production load: ONNX, GLINER_ONNX_THREADS
    load_time = time.time() - t0
    print(f"[*] backend={gd._GLOBAL_BACKEND} loaded in {load_time:.2f}s "
          f"(GLINER_ONNX_THREADS={gd.config.GLINER_ONNX_THREADS})", flush=True)

    # One untimed warmup pass (graph/session warmup, first-call overhead)
    # over a small slice so it doesn't bias either timed arm.
    warmup_sample = sample[:2]
    run_pass(model, warmup_sample, LABELS_4)
    print("[*] warmup complete", flush=True)

    reps = []
    for rep in range(2):
        t4, e4 = run_pass(model, sample, LABELS_4)
        print(f"[rep {rep}] 4-label: {t4:.2f}s, {e4} raw entities", flush=True)
        t5, e5 = run_pass(model, sample, LABELS_5)
        print(f"[rep {rep}] 5-label: {t5:.2f}s, {e5} raw entities", flush=True)
        reps.append({"rep": rep, "t4": t4, "e4": e4, "t5": t5, "e5": e5})

    mean_t4 = sum(r["t4"] for r in reps) / len(reps)
    mean_t5 = sum(r["t5"] for r in reps) / len(reps)
    pct_increase = (mean_t5 - mean_t4) / mean_t4 * 100

    out = {
        "backend": gd._GLOBAL_BACKEND,
        "onnx_threads": gd.config.GLINER_ONNX_THREADS,
        "files": len(sample),
        "total_chars": total_chars,
        "reps": reps,
        "mean_4label_s": round(mean_t4, 2),
        "mean_5label_s": round(mean_t5, 2),
        "pct_increase": round(pct_increase, 2),
        "chars_per_s_4label": round(total_chars / mean_t4, 1),
        "chars_per_s_5label": round(total_chars / mean_t5, 1),
    }
    out_path = ROOT / "docs/evidence/gliner_address_cost.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2), flush=True)


if __name__ == "__main__":
    main()
