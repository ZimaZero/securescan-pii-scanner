# NER Benchmark — Presidio (spaCy) vs GLiNER

A **standalone experiment** (NOT wired into `hybrid_detector`) comparing two NER
engines detecting *everything they can* on one mixed-PII document, with full
entity coverage and no suppression.

## Files
- [dataset.py](dataset.py) — the document + ground truth (single source of truth) and the type maps.
- [ANSWER_KEY.md](ANSWER_KEY.md) — human-readable ground truth (21 items, 13 categories) and scoring rules.
- [benchmark.py](benchmark.py) — runs both models, scores them, prints the full report.
- `results.json` — machine-readable dump (written by the run).

## Run
```bash
docker compose run --rm securescan-cpu python tests/ner_benchmark/benchmark.py
```

## What it does
- **Two documents**: `doc1` (clean onboarding memo, 21 items) and `doc2`
  (multi-page adversarial incident report, 25 items — intl phones, IPv6, tagged
  emails, ordinal/ISO dates, a Canadian health card, and decoy look-alikes).
- **Presidio** is built fresh here (not the project's suppressed wrapper): spaCy
  `en_core_web_lg`, all supported entities, `score_threshold=0.0` — nothing
  blocklisted.
- **GLiNER** sweep — `small`, `medium`, `large` (`urchade/gliner_*-v2.1`) at
  threshold 0.3, over the same broad label set.
- Each model runs over both docs; detections are deduped, greedily matched
  1-to-1 to ground truth, then scored type-aware. The report prints raw
  detections, per-item tables, an aggregate headline, per-category F1 for all
  models, a **GLiNER size sweep on structured PII** (the gap-closing question),
  and false-positive counts. Raw dump -> `results.json`.

Limit which GLiNER sizes run with `BENCH_GLINER_SIZES=small,medium`. Thresholds
are printed so the comparison is transparent.
