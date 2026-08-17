# Does the verifier do better on real documents than on specimens? — measurement only

Measured 2026-08-08 alongside `verifier_routing_by_document_type.md`.
**No production default, prompt, threshold, detector, or routing rule was
changed.** LLM verification remains OFF by default and stays off.

## Why this measurement

`verifier_routing_by_document_type.md` showed extraction method (OCR vs.
native) doesn't separate the verifier's correct calls from its wrong ones —
Arm B/C loses all 5 correct demotions from `verifier_benchmark.md` while
still keeping 4 wrong ones. But every corpus scored in that benchmark (the
Canadian typed corpus and the "Final Showcase" photographed corpus) is
**deliberately staged specimen/test data** — filenames like
`AB_front_specimen.jpg`, `foreign_passport_generic_01.txt`,
`passport_display_space_01.txt` are built to be format-correct but not
"real," which is exactly the axis a "LEGITIMATE vs FALSE_POSITIVE" judge is
being asked to evaluate. A specimen corpus may mechanically depress verifier
accuracy on a question ("is this real") that production usage never actually
poses, because production input is real documents, not test fixtures.
Separately, `tests/external_enron/EVALUATION.md` — real corporate email, not
a constructed test corpus — found the verifier **100% correct** (18/18
demotions, zero real PII lost). That gap (100% on real prose vs. 5/17 = 29%
on specimens) motivated checking whether it holds for photographed identity
documents too, not just prose email.

## What was measured

External photographed-document anchor — 11 files, distinct from every specimen
corpus used elsewhere in this repo's evaluations (`Final Showcase`,
`CanPII_test`, `tests/canadian_eval_data/`). Organic filenames
(`specimen_licence_02.jpg`, `specimen_licence_03.jpg`, `specimen_sin_01.jpg`, `specimen_pr_card_02.jpg`) rather than
provincial specimen naming. The mismatch-alarm acceptance corpus records
this as a real-document evaluation set: "four
HIGH-scoring files (two `drivers_license_ca`, two MRZ-confirmed)."

Ran `scanner.py --path <external-test-corpus> --verify` (current
production model/prompt/routing, unchanged). 565.60s total, 59.43s of that
the verification pass.

## Result

```
routed=4  demoted=0  errors=0  legitimate=4
```

| File | Value | Category | Verdict |
|---|---|---|---|
| `specimen_licence_01.jpg` | `123456-789` | `identifier.government.drivers_license_ab` | LEGITIMATE |
| `specimen_licence_03.jpg` | `654321-987` | `identifier.government.drivers_license_ab` | LEGITIMATE |
| `specimen_benefits_01.jpg` | `02/01/1990` | `identifier.personal.dob` | LEGITIMATE |
| `specimen_benefits_03.jpg` | `02/01/1990` | `identifier.personal.dob` | LEGITIMATE |

**4/4 correct, 0 wrong, 0 missed.** All four HIGH-band files in this corpus
(matching the documented baseline exactly) stayed HIGH after
verification — nothing moved band. Two real Alberta driver's licences and
two real dates of birth, all photographed (OCR-extracted, same population
Arm B/C would exclude), all correctly left alone.

## Reading this honestly

**Directionally strong, not statistically decisive.** n=4 routed findings
from 11 files. This does not clear the pre-registered acceptance bar on its
own — it's too small a sample to certify anything — but it's the *third*
independent data point (after Enron's 18/18 and the photo/typed specimen
corpora's 5/17) and all three point the same way: **the verifier's accuracy
tracks whether the document is genuinely real, not whether the text was
OCR'd or which detector fired.** A real photographed driver's licence
(`123456-789`) was judged correctly; specimen photographed driver's licences
with the *same shape* (`504896`/YT, `PUBLI020220005`/NS in
`verifier_benchmark.md`) were wrongly demoted. Extraction method (both are
OCR'd photos) doesn't explain the difference — realness does.

## Why this can't become Arm D today

Unlike `ocr_attempted` (an existing extraction field this repo already
produces), **there is no existing signal anywhere in this codebase for
"is this document a real capture or a synthetic/staged specimen."**
Confirmed by direct search — no metadata field, detector output, or
extractor contract carries anything like it; the only related content is
comment-level provenance notes on detector *formats* (e.g. driver's-licence
patterns marked `SPECIMEN-DERIVED` in code), which describe where a
*regex pattern* came from, not whether a *scanned document* is real. Building
this signal would mean adding new classification logic, which is a real
detector/extraction change requiring its own scoped authorization and
measurement — out of scope for a "measure only" exercise, unlike Arm B/C's
extraction gate which only had to read a field that already existed.

## Recommendation

Still: **leave verification off in production**, unchanged from
`verifier_routing_by_document_type.md`. But this is now the most promising
lead for a future round, and it changes what that round should look like: not
another routing arm over the *existing* specimen-heavy ground truth (which
may be structurally unfavorable to any verifier, by construction), but a
larger real-document validation — more corpora shaped like this 11-file set,
or the owner's own real documents — before concluding the verifier can never
be made safe. The current 5/17 "unsafe" verdict may be measuring the
specimen corpus's adversarial design as much as it's measuring the model.

## Method notes

- No file under `detectors/`, `config.py`, or any report renderer was
  modified.
- `verify` stayed the CLI/GUI default (off); this scan passed an explicit
  `--verify` flag for evaluation purposes only.
- The photographed-document anchor is read-only, external, and excluded from
  the repository, same convention as `CanPII_test/` and `Final Showcase/`.
- Anchors were not re-derived; this is a verifier-only measurement,
  verification is off by default in every anchor already.
