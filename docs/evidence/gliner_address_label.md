# Adding "address" to production gliner_medium-v2.1's LABELS

Measurement only. `detectors/gliner_detector.py`'s production `LABELS =
["person", "organization", "location", "date"]` was never edited; nothing is
wired into production. This document tests the premise raised against
`docs/evidence/gliner_pii_model_comparison.md`: that model's conclusion was
"adopt `gliner_multi_pii-v1` for its `address` label," but GLiNER is
zero-shot — the production model (`urchade/gliner_medium-v2.1`, already
ONNX-exported and benchmark-anchored) was simply never *asked* for
`address`. This asks it, on the same cached extraction, and measures what
comes back.

**Bottom line: don't adopt it.** Cost is free (-0.18%, noise), but the
5th label is not a clean coverage addition — it's a net *negative* on the
four established anchors (37 findings removed, 11 added) and, at full-corpus
scale, more than half of everything it labels `address` (52.7%) is an email
address or bare domain the label word evidently primed the model to conflate
with "address." Of what's left after removing that confirmed junk class,
manual review puts genuine mailing-address content at roughly 6-7% of all
raw findings. The specialized `gliner_multi_pii-v1` model's own 135 `address`
findings, for comparison, lose only 15.6% to the identical mechanical filter.

## Methodology

- **Harness reuse.** `docs/evidence/gliner_pii_eval_scan.py` gained a third
  mode, `medium5` (`gd.LABELS + ["address"]`, i.e. `person, organization,
  location, date, address`), reusing the exact same chunking
  (`gd._chunks`), threshold (0.5), Torch backend, and 8-thread setting the
  existing `medium`/`pii` modes already used to produce
  `gliner_medium_raw.jsonl` / `gliner_pii_raw.jsonl` — so the new
  `gliner_medium5_raw.jsonl` is directly comparable to both without a second
  variable. Full run: 1381/1381 files, 42,146 raw entities, 3361.5s
  (uncontended; an earlier contended partial run sharing CPU with a second
  GLiNER process is not part of this number). `docs/evidence/gliner_medium5_scan_manifest.jsonl`
  is the per-file manifest.
- **Anchor scan.** New `docs/evidence/gliner_address_anchor_scan.py` mirrors
  `docs/evidence/scan_anchor_findings.py`'s exact methodology (same four
  anchors, same `ThreadPoolExecutor(max_workers=4)`, same schema) through
  the **real** `discovery.scan_file()` production path (ONNX backend,
  `config.GLINER_ONNX_THREADS`). The only change for the `--address` run:
  in-process, `detectors.gliner_detector.LABELS` gains `"address"` and
  `detectors.hybrid_detector.PII_TAXONOMY` gains `"address": "entity.address"`
  (mirroring `person`/`organization`/`location`/`date`'s existing pattern,
  landing the new label at LOW via the existing `entity` parent-category
  default). Without that taxonomy line, an `address` entity falls through to
  `uncategorized.address` at `UNKNOWN` risk, which is not what "adding the
  label" would actually mean if anyone shipped it — so the patch is included
  to measure the real intended behavior, but it lives only in this process's
  imported module objects and touches no file on disk.
- **Cost.** New `docs/evidence/gliner_address_cost.py`: same loaded ONNX
  model object, same `GLINER_ONNX_THREADS`, only the `labels` list passed to
  `model.predict_entities()` differs between arms. Order interleaved
  (4,5,4,5) across 2 repetitions so warmup/thermal drift hits both arms
  symmetrically. Sample: the identical 30-file (28 after empty-text
  filtering), seed-1337, per-corpus-proportional sample
  `docs/evidence/gliner_pii_eval_cost.py` already used for the PII-model cost
  comparison, so the number sits in the same evidentiary frame as
  `gliner_pii_model_comparison.md`'s own cost section.

## 1. Recovery against `gliner_multi_pii-v1`'s 135 `address` findings

| | count |
|---|--:|
| `gliner_multi_pii-v1` `address` findings (reference) | 135 |
| `gliner_medium-v2.1` + `address` raw findings (full corpus) | **921** (6.8x) |
| Loosely recovered (same file, normalized value equal or one contains the other) | 62 / 135 (45.9%) |
| ...of which exact full-value match | 13 / 135 (9.6%) |
| ...of which partial/truncated match | 49 / 135 (36.3%) |
| PII-model-only (not recovered even loosely) | 73 / 135 (54.1%) |
| Production-only (846 of 921 don't match any PII-model finding) | 846 / 921 (91.9%) |

Recovery is real but shallow: when production *does* catch a span the PII
model also caught, it's usually a **shorter fragment**, not the same value —
e.g. PII model's `'1000 Louisiana, Suite 5800, Houston, TX 77002'` (0.87)
vs. production's `'1000 Louisiana'` (0.84); PII model's `'PO Box 4791,
Houston, TX  77210-4791'` vs. production's exact match on the same string in
this one case. 79% of matches are this kind of truncation, not full-address
parity — production tends to stop at the street/suite token rather than
carrying the span through city/state/ZIP.

**Confidence distributions (raw, no post-filtering — matches the "no
filtering beyond the model's own 0.5 minimum" convention of the original
comparison):**

| | n | min | p10 | p50 | p90 | max | mean |
|---|--:|--:|--:|--:|--:|--:|--:|
| `gliner_medium-v2.1` + address | 921 | 0.500 | 0.527 | 0.656 | 0.835 | 0.990 | 0.670 |
| `gliner_multi_pii-v1` | 135 | 0.502 | 0.523 | 0.696 | 0.905 | 0.977 | 0.703 |

Production's confidence distribution is slightly *lower* on average
(0.670 vs. 0.703) and its p90 is meaningfully lower (0.835 vs. 0.905) —
consistent with a model producing more marginal, lower-conviction calls for
a label it wasn't fine-tuned for.

## 2. Cost — the deciding number

| | backend | threads | files | chars | mean wall (2 reps) | chars/s |
|---|---|--:|--:|--:|--:|--:|
| 4 labels (production) | ONNX | 2 (`GLINER_ONNX_THREADS`) | 28 | 3,493,178 | 1282.90s | 2722.9 |
| 5 labels (+ address) | ONNX | 2 | 28 | 3,493,178 | 1280.57s | 2727.8 |

**Percentage increase: -0.18%** (5-label arm was marginally *faster*, well
inside run-to-run noise — rep 0 showed +0.6%, rep 1 showed -1.0%). Both reps
individually confirm the same thing: adding a 5th label to GLiNER's
zero-shot label list has **no measurable wall-clock cost**. This matches the
architecture — the text encoder pass (dominant cost) runs once per chunk
regardless of label count; scoring one more label against the same span
representations is comparatively negligible. Since GLiNER is already 99.7%
of scan wall-time, "the label is free" is the one part of the original
`gliner_multi_pii-v1` proposal that generalizes cleanly to this
zero-shot-relabeling approach — cost was never the reason to reject this,
precision is.

## 3. Precision

**Step 1 — mechanical junk classification, full 921.** GLiNER's `"address"`
label, undifferentiated from `"email address"` in a zero-shot 5-label
prompt, produces a specific, dominant, *reproducible* confusion: email
addresses and bare domains routed to `address` instead of the semantically
closer concept. This is not purely a harness artifact — production's own
existing `gliner_detector._is_structured()` guard (bare-domain / IP /
digit-heavy filter, already shipped) strips 235 raw findings before they'd
ever reach `entity.*` (140 of those are the bare-domain-shaped ones like
`'buy.com'`; the other 95 are digit-heavy phone/IP-shaped strings this same
run also mislabeled `address`, e.g. `'416-555-0199'`, `'128.193.84.130'`).
But the guard **has no email-address check**, so all 325 email-shaped values
would survive into production's merged findings as `entity.address`,
duplicate and confusing against the correct `contact.email` finding the
regex layer already emits for the same string:

```
docker compose run --rm securescan-cpu python -c "import detectors.gliner_detector as gd; print(gd._is_structured('israel.estrada@enron.com'))"
# False -- an email-shaped 'address' finding is NOT filtered by the existing guard
docker compose run --rm securescan-cpu python -c "import detectors.gliner_detector as gd; print(gd._is_structured('buy.com'))"
# True -- a bare domain IS filtered by the existing guard
```

| | `gliner_medium-v2.1` + address (921 raw) | `gliner_multi_pii-v1` (135 raw) |
|---|--:|--:|
| email-shaped (`x@y.z`) | 325 (35.3%) | 0 |
| bare-domain-shaped (`buy.com`) | 157 (17.0%) | 2 |
| URL-shaped | 3 (0.3%) | 0 |
| exact known field-label (`Address`, `Notesaddr`, ...) | 48 (5.2%) | 19 (14.1%) |
| **removed by this mechanical filter** | **533 (57.9%)** | **21 (15.6%)** |
| survives production's *existing* `_is_structured()` gate (email-shaped items pass through it) | 686 (74.5%) | n/a (label not in production `_is_structured` scope today) |

The specialized model's own 135 lose only 15.6% to the same rule, almost all
of it exact field-label repeats (`Notesaddr`-equivalent isn't present there,
but `Address`/`address`/`commercial address`/`contact.address`/`Place of
birth/Lieu de naissance` are, plus 2 bare domains). It essentially never
confuses `address` with `email`/`domain` — because it was trained with those
as *separate, competing* labels in the same forward pass, and correctly
routes each string to its own label. Production, asked for `address` without
the disambiguating sibling labels the PII model was fine-tuned with, has no
such separation.

**Step 2 — manual classification of the non-email/domain/URL remainder
(436 of 921).** Stratified random sample (seed 1337, n=45) drawn from this
436-row pool, before removing the exact-field-label subset, so the field-
label count below cross-checks Step 1's 48:

| category | count (of 45) | share |
|---|--:|--:|
| Real address (street/PO-box fragment or full; includes partials like `"7620  Katy Freeway"`, `"3100 Main Street"`, `"P.O. Box No. 1947"`) | 6 | 13.3% |
| Field label (`Notesaddr` x6, `"address shown on the front of this document"`) | 7 | 15.6% |
| Office/room code (`"RLM 11.170"`, `"Suite 3090"`, `"Suite 5800"`, `"Suite 3AC3120"`) | 4 | 8.9% |
| Other (phone numbers x9, internal Enron usernames/distribution-list IDs x6, organization/building names x5, hostnames/garbage tokens x8) | 28 | 62.2% |

`Notesaddr` (Lotus Notes' own internal field name for a message's routing
address, appearing 6 times in this 45-row sample alone, 46 times across the
full 921) is the single largest field-label repeat offender — a literal
artifact of Enron's email client UI text, not free-text content.

**Combined picture, full 921, exact counts + sample-extrapolated remainder:**

| category | count | share |
|---|--:|--:|
| Email/domain/URL confusion (exact) | 485 | 52.7% |
| Field label (exact) | 48 | 5.2% |
| Genuine address content, extrapolated from the 38 non-field-label samples (real + office/room code, 10/38 = 26.3%) | ~102 | ~11.1% |
| Other noise, extrapolated (28/38 = 73.7%) | ~286 | ~31.1% |

**Genuine mailing-address precision across all raw production+address
findings is roughly 6-11%** depending on how generously office/room-code
fragments are counted as "address-adjacent" — versus a model that was never
asked to separate `address` from `email address`, `phone number`, or
`identity document number` in the first place. This is a materially worse
precision profile than `gliner_multi_pii-v1` produced for the same label on
the same underlying documents (which itself was already characterized as
"noisy at the edges" in the original comparison).

## 4. Anchor impact (`stress`, `format`, `external_octopii`, `test`)

Fresh baseline-vs-address-enabled capture at current HEAD (not the stale
`anchor_current_scan.json`, which predates several since-committed detector
rounds), same 211 files, same production `discovery.scan_file()` path,
`verify=False`, normal NER policy. Wall time: baseline 551.6s, address-on
514.2s (noise-level difference, consistent with §2 — no real per-file cost
change).

| Anchor | Findings before → after | Finding risk before → after |
|---|---|---|
| `tests/stress_data` | 132 → **126** | H16/M7/L109 → H16/M7/L103 |
| `tests/format_data` | 74 → **70** | H22/M39/L13 → H22/M39/L9 |
| `tests/external_octopii` | 51 → **45** | H1/M5/L45 → H1/M5/L39 |
| external photographed-document anchor | 75 → **65** | H4/M6/L65 → H4/M6/L55 |

**Every anchor's total finding count went down, not up.** File-risk bands
(HIGH/MEDIUM/NONE counts) and PII-file counts are unchanged in all four —
only LOW-tier `entity.*` counts moved, so this never touched score or file
risk classification. Net: **11 added, 37 removed** (exact diff below). This
is the direct, small-scale confirmation of `gliner_pii_model_comparison.md`'s
own "Single pass vs. batched groups" methodology note (labels compete for
the same span budget in one forward pass) — even a 4→5 label change measurably
displaces some previously-correct `person`/`organization`/`location`/`date`
classifications, not just adding `address` on top of them.

### Every changed finding

**`external_octopii/dummy-aadhaar.png`** — 4 removed, 2 added:
- REMOVED `entity.location`: `'D.N.Singh Road'`, `'Hathibaug Mazgaon'`, `'Hendre Buldg No.17'`, `'Salarpuria Touchstone'`
- ADDED `entity.address`: `'P.O. Box No. 1947'`, `'Room no.3'` (genuinely new spans, not relabels of the removed ones)

**`external_octopii/dummy-debit-card.jpg`** — 2 removed, 0 added:
- REMOVED `entity.location`: `'5422'`, `'BE'` (vanished outright, no replacement)

**`external_octopii/dummy-drivers-license-maharashtra.jpg`** — 3 removed, 3 added (clean relabel):
- REMOVED `entity.location` / ADDED `entity.address`, same values: `'BABUKHAN'`, `'BAIGANWADI'`, `'GOVANDI'`

**`external_octopii/dummy-hong-kong-resident-id.png`** — 1 removed, 0 added:
- REMOVED `entity.date`: `'26-11-18'`

**`external_octopii/dummy-ssn.jpg`** — 1 removed, 0 added:
- REMOVED `entity.location`: `'USA'`

**`format/deck_summary.pptx`** — 1 removed:
- REMOVED `entity.location`: `'T2X 1V4'`

**`format/pdf_no_pii.pdf`** — 1 removed:
- REMOVED `entity.location`: `'project board'`

**`format/pdf_scanned_2page.pdf`** — 2 removed:
- REMOVED `entity.location`: `'BC'`, `'Ontario'`

**`stress/edge_cases/very_long_single_line.txt`** — 4 removed:
- REMOVED `entity.date`: `'sprint'`; `entity.location`: `'payment region'`, `'revenue region'`; `entity.organization`: `'logistics vendor'`

**`stress/finance/team_delta/project_z/archive/filler_0091.md`** — 2 removed:
- REMOVED `entity.person`: `'vendor'`, `'worker'`

**`test/specimen_sin_01.jpg`** — 1 removed, 3 added:
- REMOVED `entity.location` / ADDED `entity.address`, same value: `'PO Box 000'` (clean relabel)
- ADDED `entity.organization`: `'El Program'`, `'Service'` (new, unrelated to address — a knock-on effect of the label-set change on the *other* labels' competition, not a relabel)

**`test/specimen_licence_02.jpg`** — 1 removed:
- REMOVED `entity.location`: `'Sampletown AB'`

**`test/specimen_licence_01.jpg`** — 1 removed:
- REMOVED `entity.location`: `'Sampletown AB'`

**`test/specimen_pr_card_01.jpg`** — 4 removed, 2 added:
- REMOVED `entity.location`: `'Place of Landing'`, `'Taile'`; `entity.person`: `'EXAMPLE'`, `'Sample'`
- ADDED `entity.location`: `'BRUN'`, `'JUIN'` (different OCR-noise spans reclassified, not the same text)

**`test/specimen_benefits_02.jpg`** — 1 removed:
- REMOVED `entity.location`: `'16 Samplewood Pk SW'` (this was itself an address-shaped street value — vanished rather than becoming `entity.address`)

**`test/specimen_licence_03.jpg`** — 1 removed, 1 added:
- REMOVED `entity.location`: `'Alberta'`
- ADDED `entity.date`: `'2030'`

**`test/specimen_pr_card_02.jpg`** — 2 removed:
- REMOVED `entity.location`: `'Nam'`; `entity.person`: `'Sample'`

**`test/specimen_passport_01.jpg`** — 3 removed:
- REMOVED `entity.location`: `'PASAS'`, `'Place d bith'`, `'Rodas'`

**`test/specimen_benefits_01.jpg`** — 1 removed:
- REMOVED `entity.location`: `'DENTAL OFFICE'`

**`test/specimen_benefits_03.jpg`** — 1 removed:
- REMOVED `entity.person`: `'Dr. Example'`

Of the 11 added findings, only 6 are clean relabels of a removed finding at
the same value (`BABUKHAN`/`BAIGANWADI`/`GOVANDI`/`PO Box 000` — 4 distinct
values, `PO Box 000` counted once); the other 5 (`P.O. Box No. 1947`,
`Room no.3`, `El Program`, `Service`, `BRUN`, `JUIN`, `2030` — 7 actually, 2
of which are duplicated relabel-adjacent) are net-new spans the 4-label
model never surfaced under *any* label, some genuinely useful
(`P.O. Box No. 1947`), several not (`El Program`, `Service`, `BRUN`, `JUIN`
read as noise). Of the 37 removed, only 4 are accounted for by a same-value
relabel; the remaining 33 are straightforward losses — real signal
(`'16 Samplewood Pk SW'`, an actual street address, disappeared entirely)
and noise (`'sprint'`, `'vendor'`) alike, with no way to tell which class a
given removal will land in ahead of time.

## Recommendation

Do not add `"address"` to production `LABELS`. Cost is a non-issue (§2), but
precision is not defensible: over half of everything the model would call
`address` at full-corpus scale is a confirmed email/domain mislabel that
production's existing structured-value guard does not catch for the email
case, genuine mailing-address recall against the purpose-built
`gliner_multi_pii-v1` model is shallow (mostly truncated fragments, not full
addresses), and the four production anchors net *lose* findings, not gain
them, because the added label measurably displaces some of the
`person`/`organization`/`location`/`date` classifications production already
relies on. None of these three problems is fixable by tuning a downstream
filter alone without first fixing what the model itself was never trained to
separate.

## Files

- `docs/evidence/gliner_pii_eval_scan.py` — `medium5` evaluation mode;
  `medium`/`pii` modes unchanged.
- `docs/evidence/gliner_medium5_raw.jsonl` / `gliner_medium5_scan_manifest.jsonl`
  — full corpus, 1381/1381 files, 42,146 raw entities.
- `docs/evidence/gliner_address_anchor_scan.py` — anchor harness (baseline /
  `--address`).
- `docs/evidence/gliner_address_cost.py` / `gliner_address_cost.json` —
  controlled cost comparison.
- `docs/evidence/gliner_address_analyze.py` / `gliner_address_comparison.json`
  — recovery/precision analysis against `gliner_pii_raw.jsonl`.
- `/tmp/gliner_address_eval/anchor_baseline.json` / `anchor_address.json` —
  raw anchor scan output (not committed; regenerate via
  `gliner_address_anchor_scan.py` if needed, ~9 min each).

Measurement only. No production files (`detectors/gliner_detector.py`,
`detectors/hybrid_detector.py`, `config.py`) were modified. LLM verification
remained disabled throughout the measurement.
