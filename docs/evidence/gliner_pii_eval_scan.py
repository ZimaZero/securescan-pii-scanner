#!/usr/bin/env python3
"""Run one GLiNER model over the cached extraction and dump raw entities.

Usage:
    docker compose run --rm securescan-cpu python docs/evidence/gliner_pii_eval_scan.py medium
    docker compose run --rm securescan-cpu python docs/evidence/gliner_pii_eval_scan.py pii

Reads docs/evidence/gliner_pii_eval_extract.py's cache
(/tmp/gliner_pii_eval_extract.jsonl) so OCR/extraction never reruns.
Writes docs/evidence/gliner_{medium,pii}_raw.jsonl, one JSON object per
entity: file, corpus, label, value, char_start, char_end, confidence,
context_before, context_after (100 chars each side). No filtering beyond
the model's own default threshold (0.5) -- no dedup, no post-hoc cutoff.

"pii" mode queries urchade/gliner_multi_pii-v1 with its full published
53-label set, split into 5 themed batches (see LABEL_GROUPS below) because
a single 53-label pass was benchmarked to suppress/compete out entities
that the batched groups recover (see docs/evidence/gliner_pii_model_comparison.md
methodology section) -- batching trades ~3-5x more model calls per chunk
for higher recall. Each entity's batch_group is recorded so single-pass
behavior can be reconstructed/compared later.
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

import torch  # noqa: E402
import detectors.gliner_detector as gd  # noqa: E402  (reuse _chunks/word splitter)
from gliner import GLiNER  # noqa: E402

EXTRACT_CACHE = Path("/tmp/gliner_pii_eval_extract.jsonl")

MEDIUM_MODEL = "urchade/gliner_medium-v2.1"
PII_MODEL = "urchade/gliner_multi_pii-v1"

# "medium5" mode: production gliner_medium-v2.1, LABELS + "address" appended
# (gd.LABELS itself is never modified -- this list is read once at mode
# dispatch time in main(), see below). Measures whether the zero-shot
# production model recovers gliner_multi_pii-v1's "address" coverage when
# simply asked for it, instead of adopting the PII model. See
# docs/evidence/gliner_address_label.md.
MEDIUM5_LABELS_SUFFIX = ["address"]

# Full published label set from the model card (README), kept verbatim
# including the two underscore-variant duplicates (passport_number,
# social_security_number) -- deduplication would be a subsetting decision
# the task explicitly said not to make ("all of them, not a subset").
PII_FULL_LABELS = [
    "person", "organization", "phone number", "address", "passport number", "email",
    "credit card number", "social security number", "health insurance id number",
    "date of birth", "mobile phone number", "bank account number", "medication", "cpf",
    "driver's license number", "tax identification number", "medical condition",
    "identity card number", "national id number", "ip address", "email address", "iban",
    "credit card expiration date", "username", "health insurance number",
    "registration number", "student id number", "insurance number", "flight number",
    "landline phone number", "blood type", "cvv", "reservation number",
    "digital signature", "social media handle", "license plate number", "cnpj",
    "postal code", "passport_number", "serial number", "vehicle registration number",
    "credit card brand", "fax number", "visa number", "insurance company",
    "identity document number", "transaction number", "national health insurance number",
    "cvc", "birth certificate number", "train ticket number", "passport expiration date",
    "social_security_number",
]

LABEL_GROUPS = {
    "identity_gov": [
        "person", "passport number", "passport_number", "national id number",
        "identity card number", "identity document number", "driver's license number",
        "birth certificate number", "tax identification number", "cpf", "cnpj",
        "passport expiration date", "visa number",
    ],
    "financial": [
        "credit card number", "credit card expiration date", "credit card brand",
        "cvv", "cvc", "bank account number", "iban", "transaction number",
        "social security number", "social_security_number",
    ],
    "contact_location": [
        "email", "email address", "phone number", "mobile phone number",
        "landline phone number", "fax number", "address", "postal code", "ip address",
        "organization", "username", "social media handle", "digital signature",
    ],
    "health_medical": [
        "health insurance id number", "health insurance number",
        "national health insurance number", "medical condition", "medication",
        "blood type",
    ],
    "travel_misc": [
        "flight number", "train ticket number", "reservation number",
        "license plate number", "vehicle registration number", "registration number",
        "student id number", "insurance number", "insurance company", "serial number",
        "date of birth",
    ],
}
assert sorted(sum(LABEL_GROUPS.values(), [])) == sorted(PII_FULL_LABELS)

THRESHOLD = 0.5  # GLiNER library default; no additional filtering applied
CONTEXT_PAD = 100
# Excluded after a real run: 'pii' mode ran 647s on a typical capped 150K-char
# stress file, then over 1h42m of continuous 795%-CPU compute with zero
# progress on this ONE file (a synthetic templated log with dense repeated
# capitalized vocabulary -- "Dashboard", "Cache", "INFO", "WARN", "Database"
# etc. recurring every line). GLiNER's span-classification cost scales with
# the number of plausible entity-shaped spans, not raw char count, so
# capitalized-token-dense log content is a pathological input class distinct
# from ordinary prose of the same length. Documented explicitly in the cost
# section of the comparison report rather than silently dropped -- this is
# the one file this evaluation could not complete.
PATHOLOGICAL_SKIP = {
    ("stress", "/workspaces/securescan/tests/stress_data/marketing/team_gamma/project_z/archive/2024/filler_0072.log"),
}
# Same bound production's own detect_pii_hybrid() applies to GLiNER (see
# config.NER_MAX_CHARS / hybrid_detector.py) -- not a new sampling decision,
# reapplying the exact cap production already lives under. The dominant
# stress corpus (174 files, 44M raw chars) drops to ~7.5M capped chars this
# way; every other corpus is materially unaffected (see gliner_pii_eval
# extraction summary in the comparison report methodology section).
NER_MAX_CHARS = 150_000


def load_rows(corpus_filter=None):
    rows = []
    with EXTRACT_CACHE.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("scan_status") != "scanned":
                continue
            if not row.get("text", "").strip():
                continue
            if corpus_filter and row["corpus"] not in corpus_filter:
                continue
            rows.append(row)
    return rows


def chunks_with_offsets(text, model):
    """Wrap gd._chunks() (word/token-bounded) with recovered char offsets.

    _chunks() yields substrings only, not their offsets, since production
    never needed spans (hybrid_detector re-locates values itself). Chunks
    are literal, left-to-right (overlapping) slices of text, so a bounded
    find() anchored near the previous chunk's start reconstructs offsets
    reliably without duplicating the tokenizer-bounded chunking logic.
    """
    anchor = 0
    for chunk in gd._chunks(text, model):
        idx = text.find(chunk, max(0, anchor - 500))
        if idx == -1:
            idx = text.find(chunk)
        if idx == -1:
            idx = anchor
        yield idx, chunk
        anchor = idx


def run_medium(model, text, labels=None):
    out = []
    labels = gd.LABELS if labels is None else labels
    for offset, chunk in chunks_with_offsets(text, model):
        for e in model.predict_entities(chunk, labels, threshold=THRESHOLD):
            out.append((e, offset, None))
    return out


def run_pii_batched(model, text):
    out = []
    for offset, chunk in chunks_with_offsets(text, model):
        for group_name, labels in LABEL_GROUPS.items():
            for e in model.predict_entities(chunk, labels, threshold=THRESHOLD):
                out.append((e, offset, group_name))
    return out


def context_window(text, start, end):
    return text[max(0, start - CONTEXT_PAD):start], text[end:end + CONTEXT_PAD]


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("medium", "medium5", "pii"):
        print("usage: gliner_pii_eval_scan.py {medium|medium5|pii} [max_files]", file=sys.stderr)
        sys.exit(2)
    mode = sys.argv[1]
    max_files = int(sys.argv[2]) if len(sys.argv) > 2 else None
    medium5_labels = gd.LABELS + MEDIUM5_LABELS_SUFFIX

    threads = int(os.environ.get("GLINER_EVAL_THREADS", "8"))
    torch.set_num_threads(threads)

    out_path = ROOT / f"docs/evidence/gliner_{mode}_raw.jsonl"
    model_name = PII_MODEL if mode == "pii" else MEDIUM_MODEL
    print(f"[*] Loading {model_name} (torch, threads={threads})...", flush=True)
    t0 = time.time()
    model = GLiNER.from_pretrained(model_name, local_files_only=True)
    load_time = time.time() - t0
    print(f"[*] loaded in {load_time:.2f}s", flush=True)
    if mode == "medium5":
        print(f"[*] medium5 labels: {medium5_labels}", flush=True)

    rows = load_rows()
    if max_files:
        rows = rows[:max_files]

    manifest_path = ROOT / f"docs/evidence/gliner_{mode}_scan_manifest.jsonl"
    # Source of truth for "already processed": the MANIFEST, which gets one
    # line per file regardless of whether it produced any entities. Using
    # the raw-output file alone (as an earlier version of this script did)
    # meant a file with zero PII-model findings never appeared there, so a
    # restart couldn't tell it apart from an unprocessed file and reran it
    # from scratch every time -- wasted real compute on a corpus (stress)
    # where most filler files legitimately find nothing.
    done_files = set()
    if manifest_path.exists():
        with manifest_path.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                done_files.add((rec["corpus"], rec["file"]))

    total_chars_done = 0
    write_mode = "a" if out_path.exists() else "w"
    out_fh = out_path.open(write_mode, encoding="utf-8")
    manifest_fh = manifest_path.open("a" if manifest_path.exists() else "w", encoding="utf-8")

    n_done = 0
    n_skip = 0
    t_start = time.time()
    for row in rows:
        key = (row["corpus"], row["path"])
        if key in done_files:
            n_skip += 1
            continue
        if key in PATHOLOGICAL_SKIP:
            manifest_fh.write(json.dumps({
                "corpus": row["corpus"], "file": row["path"], "chars": 0,
                "raw_chars": len(row["text"]), "ner_truncated": False,
                "entities": 0, "seconds": 0, "skipped_pathological": True,
            }) + "\n")
            manifest_fh.flush()
            continue
        text = row["text"]
        truncated = len(text) > NER_MAX_CHARS
        if truncated:
            text = text[:NER_MAX_CHARS]
        t_file = time.time()
        try:
            if mode == "medium":
                raw_ents = run_medium(model, text)
            elif mode == "medium5":
                raw_ents = run_medium(model, text, labels=medium5_labels)
            else:
                raw_ents = run_pii_batched(model, text)
        except Exception as exc:
            manifest_fh.write(json.dumps({
                "corpus": row["corpus"], "file": row["path"], "error": str(exc),
            }) + "\n")
            manifest_fh.flush()
            continue
        elapsed = time.time() - t_file

        for e, offset, group in raw_ents:
            start = offset + e["start"]
            end = offset + e["end"]
            before, after = context_window(text, start, end)
            rec = {
                "file": row["path"],
                "corpus": row["corpus"],
                "label": e["label"],
                "value": e["text"],
                "char_start": start,
                "char_end": end,
                "confidence": round(float(e["score"]), 4),
                "context_before": before,
                "context_after": after,
            }
            if group is not None:
                rec["batch_group"] = group
            out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out_fh.flush()

        manifest_fh.write(json.dumps({
            "corpus": row["corpus"], "file": row["path"], "chars": len(text),
            "raw_chars": len(row["text"]), "ner_truncated": truncated,
            "entities": len(raw_ents), "seconds": round(elapsed, 3),
        }) + "\n")
        manifest_fh.flush()

        n_done += 1
        total_chars_done += len(text)
        if n_done % 20 == 0:
            rate = total_chars_done / max(1e-6, time.time() - t_start)
            print(
                f"[{mode}] {n_done}/{len(rows)-n_skip} new files "
                f"({n_skip} resumed), {rate:.0f} chars/s",
                flush=True,
            )

    out_fh.close()
    manifest_fh.close()
    print(json.dumps({
        "mode": mode, "model": model_name, "load_time_s": round(load_time, 2),
        "files_processed_this_run": n_done, "files_resumed": n_skip,
        "total_wall_s": round(time.time() - t_start, 2),
        "out": str(out_path),
    }), flush=True)


if __name__ == "__main__":
    main()
