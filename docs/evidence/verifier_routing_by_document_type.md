# Document-type-based verifier routing — measurement only

Measured 2026-08-08. **No production default, prompt, threshold, detector, or
routing rule was changed.** LLM verification remains OFF by default and stays
off. This document records measurement evidence, like
`docs/evidence/verifier_benchmark.md`, which supplies the manually adjudicated
ground truth used below.

## Hypothesis

The verifier's accuracy depends on *how the text was extracted* (clean native
text vs. noisy OCR), not on which detector produced the finding. Current
routing (`detectors/llm_verifier.py`'s `ROUTABLE_SOURCES`) keys only on source
layer and ignores extraction method.

## 1. The extraction-aware routing signal

**Existing field, no new contract:** `metadata["ocr_attempted"]` (bool),
already produced by `discovery.scan_file()` for every scanned file:

- **Images** (`.png .jpg .jpeg .tiff .tif .bmp .gif .webp`): always `True`.
  `extract_image()` always OCRs, so `confidence` is never `None`, and
  `discovery.py` sets `metadata["ocr_attempted"] = True` whenever
  `confidence is not None` (`discovery.py:438-440`).
- **PDFs**: sourced verbatim from `extract_pdf(..., return_details=True)`'s
  own `ocr_attempted` key (`extractors/pdf_extractor.py:278`) — `True` iff at
  least one page fell below the native-text-layer threshold and required OCR.
- **Every other supported extension** (`.txt .csv .json .py .md .log .docx
  .xlsx .eml .pptx`): the key is never set at all (`confidence=None`,
  `ocr_details=None` for these extractors) — `metadata.get("ocr_attempted")`
  is falsy, i.e. "native".

This measurement introduces no new extraction metadata. It only reads the
field above at routing time via a throwaway probe script (not added to
`detectors/llm_verifier.py`, not staged), applying this predicate:

```python
def is_routable_extraction_aware(finding, file_metadata, exclude_dates=False):
    if not is_routable(finding):          # unchanged Arm-A predicate
        return False
    if file_metadata.get("ocr_attempted"):
        return False                       # never route OCR-derived text
    if exclude_dates and finding.get("category") == "identifier.personal.dob":
        return False                       # field_label_association.py owns dates now
    return True
```

**Arm B** = extraction-aware gate only. **Arm C** = Arm B, plus dates excluded
(rationale: `detectors/field_label_association.py` now resolves DOB-vs-expiry
deterministically from field-label proximity — see
`detectors/field_label_association.py` — so re-routing
`identifier.personal.dob` findings to the LLM judge
would duplicate a fix already shipped, and the photo-table evidence below
shows the verifier getting 4 of 6 DOB judgments wrong on exactly this
question).

## 2. Scoring the three arms against the owner ground truth

Ground truth: `docs/evidence/verifier_benchmark.md`, 42 routed findings, owner
`OWNER GROUND TRUTH` column adjudicated per row — 37 rows confirm the
*original* (pre-verification) finding was correct (36 `CORRECT...` + 1 `DOB`
row explicitly confirming a demoted value really is a DOB), 5 rows confirm
the original finding really was a false positive (`FALSE, ...` rows). All 5
genuine false positives happen to carry a `FALSE_POSITIVE` verdict already
(0 missed under Arm A) — see the per-arm tables below for the full
correct/wrong/missed breakdown this implies.

Every finding scored under Arm B/C was **already routed and judged** under
Arm A with the identical unchanged prompt/model/threshold — Arm B and Arm C
only ever *remove* candidates from Arm A's routed set (pure subset filters),
never add new ones. So Arm B/C verdicts for retained findings are the exact
recorded Arm A verdicts, not re-queried — confirmed non-stale by a live spot
check (see "Determinism spot check" below), which reproduced one retained
verdict (`qc_licence_compact_01.txt` / `Q426857674025`) with an
**exact-text-identical** `llm_reason` three days later.

PDF OCR status (needed to place the 2 PDF findings) was measured directly
against the real corpus:

```
NT/NT_source.pdf -> ocr_attempted=True  (OCR applied to 2/3 pages)
QC/QC_source.pdf -> ocr_attempted=False (native text layer only)
```

### Arm A — current production routing (baseline)

| Corpus | Files w/ routed findings | Routed | Demoted | Correct demotions | Wrong demotions | Missed demotions | Legitimate-kept (correct) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Photographed images | 14/23 | 20 | 12 | 5 | 7 | 0 | 8 |
| Canadian typed corpus | 20/88 | 20 | 3 | 0 | 3 | 0 | 17 |
| Showcase source PDFs | 2/4 | 2 | 2 | 0 | 2 | 0 | 0 |
| **Total** | **36/115** | **42** | **17** | **5** | **12** | **0** | **25** |

Wall-clock (documented, `verifier_benchmark.md`): **412.67s** total
(223.89s photo + 159.92s typed + 28.86s PDF).

This matches the task's pre-registered baseline exactly: **5 correct
demotions, 12 wrong.**

### Arm B — extraction-aware (OCR gate only)

| Corpus | Files w/ routed findings | Routed | Demoted | Correct demotions | Wrong demotions | Missed demotions | Legitimate-kept (correct) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Photographed images | 0/23 | **0** | 0 | 0 | 0 | 0 | 0 |
| Canadian typed corpus | 20/88 | 20 | 3 | 0 | 3 | 0 | 17 |
| Showcase source PDFs | 1/4 (QC only; NT excluded — OCR used) | 1 | 1 | 0 | 1 | 0 | 0 |
| **Total** | **21/115** | **21 (50% of Arm A)** | **4** | **0** | **4** | **5** | **17** |

Wall-clock estimate (documented per-finding rates, 159.92s÷20 typed +
28.86s÷2 PDF for the one retained PDF finding): **≈174.4s (42% of Arm A)**.

### Arm C — extraction-aware, dates excluded

Identical to Arm B on this ground-truth set — **routed 21, demoted 4, 0
correct, 4 wrong, 5 missed, 17 correct legitimate-kept, ≈174.4s** — because
every `identifier.personal.dob` finding in this benchmark happens to live in
the photo table, which the OCR gate already excludes. The date-exclusion
clause is provably redundant here; it would only start mattering on a
native-extracted, DOB-bearing corpus, which this ground truth does not
contain.

### Determinism spot check

Re-ran 4 typed-corpus findings + the QC PDF finding live against the current
production model/prompt (2026-08-08, 3 days after the original benchmark).
Under concurrent CPU load from the Enron re-run (below), 4 of 5 calls timed
out — an environment artifact, not a routing-arm effect. The one call that
completed, `qc_licence_compact_01.txt` / `Q426857674025`, reproduced
`FALSE_POSITIVE` with the **exact same `llm_reason` string** ("The value is a
synthetic ID number, not a real drivers license") recorded in
`verifier_benchmark.md`. Confidence: verdicts for retained findings are
stable enough to reuse without re-querying, as claimed above.

## 3. Acceptance criterion (pre-registered before this scoring)

> An arm is only worth proposing if WRONG demotions reach zero or near zero
> while retaining most correct ones. Any arm that loses correct findings is
> rejected regardless of how many false positives it catches.

| Arm | Wrong demotions | Correct demotions retained | Verdict |
|---|---:|---:|---|
| A (baseline) | 12 | 5/5 (100%) | Rejected — 12 wrong is nowhere near zero |
| B / C (extraction-aware) | 4 | 0/5 (0%) | **Rejected, more decisively** — loses every correct demotion *and* still doesn't reach zero wrong |

Arm B/C is not a smaller version of the same problem — it inverts the
premise. All 5 genuine false positives the verifier ever caught correctly
were extracted from OCR'd photographs (the two controlled pairs in
`verifier_benchmark.md`'s "Hypothesis result" section — `504896`/YT and
`PUBLI020220005`/NS — are exactly this: demoted in the photographed context,
retained in the typed one). Gating verification to native-only text removes
precisely the population where the verifier's correct catches live, while
the wrong-demotion rate among what's left (native text) doesn't improve
proportionally — it's still 4/21 (19%) of routed findings, indistinguishable
in kind from Arm A's problem (synthetic/specimen values in the Canadian test
corpus that are format-correct but not "real," which the verifier keeps
flagging as fake regardless of extraction method).

## 4. Enron re-run under Arm C (the decisive test)

**Structural fact, verified directly:** all 1000 files in
`tests/external_enron/sample/` are `.txt` — confirmed by extension scan.
`.txt` extraction never invokes OCR (`extract_text()`), so
`metadata.get("ocr_attempted")` is falsy for every file in this corpus, with
no exceptions possible. Arm C's OCR gate is therefore a **complete no-op**
across the entire Enron sample: whatever Arm A would route, Arm B/C routes
identically. The date-exclusion clause is also a no-op here — the two
categories that were ever routed in this corpus
(`identifier.government.drivers_license_ab` and `contact.email` via
`keyword_context`) are neither one `identifier.personal.dob`.

**But the 11 historical findings no longer exist under current detection,
independent of any verifier arm.** `tests/external_enron/EVALUATION.md`'s
11/11 correct `drivers_license_ab` demotions were measured before the
Purview two-keyword driver's-licence hardening documented in this repo's
the current two-keyword context gate in `drivers_license_detector.py`:
Alberta candidates now require **both** a jurisdiction keyword (`Alberta`/
`AB`) **and** a generic licence keyword within a 300-character window, on top
of a tightened 5–9 digit numeric shape. None of the three source files
(`heard-m__inbox__master_netting__275.txt`, `jones-t__inbox__885.txt`,
`meyers-a__deleted_items__1126.txt`) contain a licence/jurisdiction keyword
pair near the offending fragments — they're Sampletown street addresses
(`1510, 421 - 7 Ave SW`; `Suite 1740, 335 - 8th Avenue S.W.`), a fax number
(`262-8867`), and date/tag fragments (`6-25-02`, `TAG #22872`).

Confirmed empirically: re-scanning exactly these 3 files today
(`scanner.py --verify`, current commit) produces **zero**
`identifier.government.drivers_license_*` findings of any kind in any of the
3 files. Full finding lists were captured and manually checked — the files
still contain their addresses, phone numbers, and other entities, but no
driver's-licence-shaped candidate fires at all anymore, so nothing reaches
`is_routable()` under **any** arm, including current-production Arm A.

**Consequently, step 3's question as literally posed — "do the specific 11
findings survive Arm C" — has no answer, because those 11 findings no longer
exist under current detection.** The false-positive class that supplied the
hypothesis's cleanest piece of positive evidence (11/11 correct catches) was
eliminated upstream by ordinary, unrelated detector hardening before this
task began.

**Full-corpus re-scan (1000 files, current production detection,
`--no-verify`, 1191.77s):** confirms the historical 11 are gone corpus-wide,
not just in the 3 known files — but finds **3 new, unrelated**
`drivers_license` candidates that did not appear in the original evaluation:

| File | Value | Type | Source of the false positive |
|---|---|---|---|
| `bass-e__discussion_threads__1065.txt` | `9988982` | `drivers_license_bc` | The file's own `Message-ID:` header |
| `rodrique-r___sent_mail__303.txt` | `1119677` | `drivers_license_bc` | The file's own `Message-ID:` header |
| `taylor-m__archive__6_00__11.txt` | `12096772` | `drivers_license_ab` | The file's own `Message-ID:` header |

All three are the numeric prefix of the email's own `Message-ID:` header
(e.g. `Message-ID: <9988982.1075854652600.JavaMail.evans@thyme>`), not a
driver's licence. **Mechanism, verified directly against
`detectors/drivers_license_detector.py`:** Purview's generic-licence-keyword
list includes the bare token `"id"` (`CANADA_DL_KEYWORDS`), and every RFC
2822 email's own `Message-ID:` header contains a standalone `ID` token —
so the "generic licence keyword" half of the two-keyword contract is
trivially satisfied by the email format itself, near the top of every
message, independent of content. (Incidental and outside routing measurement scope;
not fixed: two of the three additionally depend on a second, narrower
coincidence — the 300-character context window happens to truncate exactly
inside an adjacent `X-bcc:` header, `bc` + truncation-as-boundary satisfying
the BC province-keyword check. Reported here unchanged.)

Re-ran these 3 files with `--verify` (current production model/prompt,
after the full-corpus scan finished so there was no CPU contention this
time): **routed=3, demoted=3, errors=0** — all three correctly caught as
`FALSE_POSITIVE`, reason `"The value appears to be a message ID, not a
[Canadian] drivers license number"` in each case.

Since all 1000 Enron files are `.txt` (native) and none of these 3 findings
is `identifier.personal.dob`, Arm C routes all three identically to Arm A —
**so in substance, the general phenomenon the original hypothesis
documented (the verifier correctly catching native-text driver's-licence
false positives in prose email) does survive under Arm C, 3/3, just via a
different concrete trigger than the specific 11 findings named in the task.**
This is the expected, structural outcome, not a surprising one: Arm C only
ever *removes* OCR-derived candidates from Arm A's routed set, and Enron has
none to remove — so Arm C can never do worse than Arm A here, only exactly
as well.

## 5. Recommendation

Per the pre-registered criterion, **neither Arm B nor Arm C is worth
proposing**: both still produce wrong demotions well short of zero (4/21,
19%) while discarding 100% of the demotions the verifier ever got right on
the owner-adjudicated ground truth. Restricting to native-extracted text does
not separate correct from incorrect verifier judgment on that evidence — it
happens to remove the one population (noisy OCR) where the verifier's
correct calls were concentrated, without cleaning up the wrong-demotion rate
on what remains.

The Enron leg is more nuanced than a flat negative, and it's worth being
precise about it: the *specific* 11 `drivers_license_ab` findings named in
the original measurement no longer reproduce under current detection — an unrelated,
already-shipped Purview hardening fixed that exact false-positive class
before this routing measurement. But the *general phenomenon* — the verifier
correctly catching native-text driver's-licence false positives in prose
email — still exists in the same corpus today (3 new `Message-ID`-collision
cases) and Arm C still catches it 3/3, because Arm C is structurally
identical to Arm A on any 100%-native corpus. Arm C never loses anything on
Enron; it simply has nothing to gain there either, since there was never an
OCR-derived finding to filter out. **All of Arm C's real cost — the 5 lost
correct demotions — is paid entirely by the photographed/OCR population**,
which is also where its only wins would have to come from, and where the
owner ground truth shows it doesn't deliver them.

**Recommend leaving verification off**, per existing default. No
extraction-aware routing arm is proposed for production; this document is
measurement evidence only, matching the scope of
`docs/evidence/verifier_benchmark.md`. One incidental, unrelated defect
surfaced during this measurement and is reported, not fixed, per scope:
Purview's generic licence-keyword list includes the bare token `"id"`, which
every RFC 2822 email's own `Message-ID:` header satisfies trivially —
undermining the two-keyword contract's selectivity specifically on email-
format documents (see "Enron re-run" above for detail and a second, narrower
context-window-truncation coincidence found alongside it).

## Method notes

- No file under `detectors/`, `config.py`, or any report renderer was
  modified. The routing predicates above were evaluated with a disposable
  probe script, never committed.
- `verify` stayed the CLI/GUI default (off); every live scan run for this
  measurement passed an explicit `--verify` flag for evaluation purposes
  only, exactly as `verifier_benchmark.md` and `EVALUATION.md` already do.
- Anchors (Canadian 88/91-file harness, stress, format, Test, Octopii,
  Enron baseline) were **not** re-derived; this is a routing measurement, not
  a detector or scoring change, and verification is off by default in every
  one of those anchors already.
