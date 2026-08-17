#!/usr/bin/env python3
"""Run SecureScan's deterministic (non-NER) detector stack over the cached
extraction text, so the GLiNER-PII comparison has real deterministic-layer
findings to agree/disagree against (task item 2: OVERLAP WITH DETERMINISTIC
LAYERS). Cheap and OCR-free: re-scores already-extracted text directly
through detect_pii_hybrid(run_ner=False) rather than re-running scan_file().
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from detectors.hybrid_detector import detect_pii_hybrid

EXTRACT_CACHE = Path("/tmp/gliner_pii_eval_extract.jsonl")
OUT = ROOT / "docs/evidence/deterministic_raw.jsonl"


def main():
    rows = []
    with EXTRACT_CACHE.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("scan_status") == "scanned" and row.get("text", "").strip():
                rows.append(row)

    out_fh = OUT.open("w", encoding="utf-8")
    n = 0
    for row in rows:
        result = detect_pii_hybrid(row["text"], run_ner=False, verify=False)
        metadata = result.pop("_metadata", {}) if isinstance(result, dict) else {}
        for det_type, findings in result.items():
            if det_type == "_metadata":
                continue
            for f in findings:
                rec = {
                    "corpus": row["corpus"],
                    "file": row["path"],
                    "detector_type": det_type,
                }
                if isinstance(f, dict):
                    rec.update(f)
                else:
                    rec["finding"] = f
                out_fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        n += 1
        if n % 200 == 0:
            print(f"{n}/{len(rows)}", flush=True)
    out_fh.close()
    print(json.dumps({"files": n, "out": str(OUT)}), flush=True)


if __name__ == "__main__":
    main()
