# NER Benchmark — Answer Key (Ground Truth)

There are **two documents** in [dataset.py](dataset.py):
- **doc1** (`DOC1`/`GT1`) — the onboarding memo below, 21 items, clean/well-formed.
- **doc2** (`DOC2`/`GT2`) — a 3-page adversarial incident report, 25 items, with
  intl phone formats, IPv6, tagged emails, ordinal/ISO/slash dates, a hyphenated
  name, a Canadian health card, and **decoy look-alikes** (`DOC2_DECOYS`: an
  order number resembling a card, a PIN resembling an SSN, a partial IP) whose
  digits are deliberately DISTINCT from the real values, so any hit on them is a
  genuine false positive. The full doc2 item list is authoritative in
  `dataset.py` (`GT2`).

Below is doc1's ground truth in prose. This is the ground truth against which
Presidio (spaCy) and GLiNER (small/medium/large) are scored.
**doc1: 21 items across 13 categories; doc2: 25 items across 14 categories.**

| # | Value | True category | Notes |
|---|-------|---------------|-------|
| 1 | Sarah Johnson | PERSON | sender; appears twice (signature) |
| 2 | David Chen | PERSON | recipient |
| 3 | Michael Rodriguez | PERSON | the new hire |
| 4 | Northwind Trading Inc. | ORG | employer; appears twice |
| 5 | 1425 Maple Avenue, Springfield, IL 62704 | ADDRESS | full street address |
| 6 | Vancouver | LOCATION | city (relocating from) |
| 7 | Chicago | LOCATION | city (office) |
| 8 | March 14, 2024 | DATE | memo date (long form) |
| 9 | 2024-04-01 | DATE | start date (ISO form) |
| 10 | sarah.johnson@northwind.com | EMAIL | corporate |
| 11 | m.rodriguez88@gmail.com | EMAIL | personal |
| 12 | (415) 555-0182 | PHONE | mobile, US format w/ parens |
| 13 | 403-555-9910 | PHONE | office, dashed |
| 14 | 536-90-4399 | SSN | US Social Security Number |
| 15 | 046 454 286 | SIN | **Canadian** Social Insurance Number |
| 16 | 4539 1488 0343 6467 | CREDIT_CARD | Visa (Luhn-valid) |
| 17 | 5500 0000 0000 0004 | CREDIT_CARD | Mastercard test number |
| 18 | https://portal.northwind.com/welcome | URL | onboarding portal |
| 19 | 192.168.1.42 | IP | internal (RFC1918) |
| 20 | 203.0.113.75 | IP | external (TEST-NET-3) |
| 21 | GB29 NWBK 6016 1331 9268 19 | IBAN | UK IBAN |

## Category counts

| Category | Count |
|----------|-------|
| PERSON | 3 |
| ORG | 1 |
| LOCATION | 2 |
| ADDRESS | 1 |
| DATE | 2 |
| EMAIL | 2 |
| PHONE | 2 |
| SSN | 1 |
| SIN | 1 |
| CREDIT_CARD | 2 |
| URL | 1 |
| IP | 2 |
| IBAN | 1 |
| **Total** | **21** |

## Scoring rules (see [benchmark.py](benchmark.py))

- **Detections are deduped** per model by (canonical type, normalized value), so
  entities appearing multiple times count once.
- Each unique detection is greedily matched 1-to-1 to the ground-truth item it
  overlaps most (numeric items compared on digits, text items on normalized
  substring).
- **TP** = matched detection with the **correct** canonical type.
- **Mistype** = matched detection with the **wrong** type → counts as a false
  negative for the true category *and* a false positive for the detected category.
- **FN** = ground-truth item with no correct-type detection (missed or mistyped).
- **FP** = detection matching no ground-truth item (spurious/hallucination) or a
  mistyped detection.
- Precision = TP / (TP + FP), Recall = TP / (TP + FN), F1 = harmonic mean.

## Known hard cases (by design)

- **SIN (#15)** — neither model has a Canadian SIN type. It can only be "found"
  by mislabeling it (usually as SSN). Pure discriminator.
- **ADDRESS (#5)** — Presidio has no ADDRESS recognizer; at best it catches
  fragments as LOCATION (a mistype). GLiNER is given an explicit `address` label.
- **IP TEST-NET / RFC1918 (#19, #20)** — structured; tests each model's regex vs.
  semantic handling.
- **IBAN (#21)** — Presidio has an IBAN recognizer; GLiNER must do it semantically.
- **Two DATE forms (#8 long, #9 ISO)** — tests date-format robustness.
