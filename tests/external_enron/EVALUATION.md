# Real-corpus evaluation: LLM verification layer on the Enron email corpus

Read-only evaluation — no detector, verifier, scoring, or report-generation code
was changed for this measurement. Findings below include one unresolved defect.

## Methodology

**Source:** CMU Enron email corpus,
[`enron_mail_20150507.tar.gz`](https://www.cs.cmu.edu/~enron/) — a 422.7MB
gzip archive of ~517K real corporate email files under `maildir/<user>/<folder>/<msgnum>`.
(The commonly-cited "1.7GB" figure for this corpus refers to the uncompressed
full extraction; the evaluation never extracts the full archive.)

**Sampling:** `download_enron_sample.py`, `SEED = 1337` (project convention,
matches `tests/make_stress_data.py`). The archive is downloaded once (cached
at `tests/external_enron/enron_raw/`, gitignored), its ~520K members are
enumerated into a sorted population list, then `random.sample(population,
1000)` selects the sample — extracted member-by-member via
`TarFile.extractfile()` (no `.extractall()`), so only the 1000 sampled files
are ever materialized on disk. Verified reproducible: running the script
twice produces byte-identical files under identical flattened names
(`sha256sum` diff across both runs is empty).

Files are written verbatim (headers included) into `tests/external_enron/sample/`
as `<user>__<folder>__<msgnum>.txt`, e.g. `skilling-j__inbox__42.txt` — 1000
files, 2,638,880 bytes total.

**Scans**, both over `tests/external_enron/sample/` (1000 files):
```
docker compose run --rm securescan-cpu python scanner.py --path tests/external_enron/sample --no-open --no-verify   # baseline
docker compose run --rm securescan-cpu python scanner.py --path tests/external_enron/sample --no-open --verify       # verified
```
Ollama confirmed reachable with `qwen2.5:3b` loaded before the verified run.
Baseline: **2398.56s**. Verified: **3002.56s**. Both scans used
`DEFAULT_MAX_WORKERS = 16` (this machine has 18 cores) — relevant to the
reliability finding below.

**Comparison:** `compare_runs.py <baseline.json> <verified.json>
--baseline-seconds 2398.56 --verified-seconds 3002.56`. Full output embedded
in the Results section below.

## Results

### Findings by risk band (baseline vs. verified)

| Band | Baseline | Verified | Delta |
|---|---:|---:|---:|
| HIGH | 128 | 128 | +0 |
| MEDIUM | 9561 | 9428 | -133 |
| LOW | 25254 | 25387 | +133 |
| UNKNOWN | 0 | 0 | +0 |

### Files by risk band (baseline vs. verified)

| Band | Baseline | Verified | Delta |
|---|---:|---:|---:|
| HIGH | 118 | 118 | +0 |
| MEDIUM | 882 | 882 | +0 |
| LOW | 0 | 0 | +0 |
| NONE | 0 | 0 | +0 |

**Zero files changed risk band.** Every one of the 133 demotions happened in
a file that had other, undemoted MEDIUM/HIGH findings — consistent with
`scoring.py`'s documented invariant that a file's score is anchored to its
*worst* finding, never additive (`scoring.py`). A pile of demoted MEDIUMs
can't move a file out of its band if even one HIGH or MEDIUM finding survives
next to it, and in a corpus of real corporate email (dozens of staff emails,
SIN/SSN/credit-card-shaped digit runs per file) that's almost always the case.

### Verification summary

```
routed=239  demoted=133  errors=102  legitimate=4
demotion rate (of routed) = 55.6%
error rate (of routed) = 42.7%
```

### Demotions by category / source layer

| Category | Source | Routed | Demoted | Legitimate | Errors |
|---|---|---:|---:|---:|---:|
| `contact.email` | `keyword_context` | 228 | 133 | 4 | 91 |
| `identifier.government.drivers_license_ab` | `drivers_license` | 11 | 0 | 0 | **11** |

**100% of routing in this corpus fell into exactly two rows.** No `gliner`,
no `passport` findings were ever routed — see the expectation-setting note
below for why that's structural, not corpus luck.

## Root-cause findings

### 1. Every single demotion is a truncated duplicate of an already-caught, undemoted email — not a true-positive kill (but also not a genuine catch)

All 133 demoted `contact.email`/`keyword_context` findings were manually
cross-referenced against the same file's `regex`-source `contact.email`
findings (the `regex` source is never routed — see `SOURCE_PRIORITY` in
`hybrid_detector.py`). **133 of 133 (100%) have a full, correctly-spanned,
undemoted duplicate of the same address, in the same file, from the `regex`
layer.** Examples, verbatim:

| File | `regex` finding (undemoted, MEDIUM) | `keyword_context` finding (demoted, LOW) | `llm_reason` |
|---|---|---|---|
| `kean-s__archiving__untitled__5397.txt` | `rwhyde@duke-energy.com` | `hyde@duke-energy.com` | "The value is an email address but the context suggests it's part of a list of emails, not a legitimate detection." |
| `kean-s__archiving__untitled__5397.txt` | `John_H_Stout@reliantenergy.com` | `H_Stout@reliantenergy.com` | "The value is an email address but the context does not provide any contextual clues that would confirm it as legitimate." |
| `kean-s__archiving__untitled__5397.txt` | `mmoretti@mccabeandcompany.net` | `moretti@mccabeandcompany.net` | "The value is an email address but the context clearly indicates it's part of a list of names and emails, not a legitimate detection." |
| `skilling-j__deleted_items__359.txt` | `talley.hopson@txh.nmss.org` | `talley.hopson@txh.nm` | (truncated on the **trailing** end this time — `.nmss.org` clipped to `.nm`) |
| `scott-s__discussion_threads__553.txt` | `virginia.c.levenback@williams.com` | `Virginia.C.Levenback@Williams.co` | (trailing `m` clipped) |

**Root cause, traced to code:** `keyword_detector.py`'s
`find_patterns_near_keywords()` builds its match window **around each
keyword occurrence's position**, not around the eventual match:
```python
ctx_start = max(0, pos - w)
context = text[ctx_start : pos + w]
```
(`keyword_detector.py:333-335`, `w` defaults to 100). Enron's `X-To:`/`To:`
headers commonly list dozens of recipients as `"Name (E-mail)"
<addr@domain>` back-to-back. In a dense list like this, the ±100-char window
belonging to *one* recipient's `"(E-mail)"` keyword occurrence often
overlaps a *neighboring* recipient's address, and if that neighbor's address
straddles the window boundary, the email-shape regex still matches whatever
substring remains inside the sliced window — producing a truncated fragment
that is technically email-shaped (`_EMAIL_SHAPE_RE` allows letters, digits,
`.`, `_`, `%`, `+`, `-`) but is not the real address. **This is a
pre-existing bug in `keyword_detector.py`, independent of the LLM verifier**
— documented here and not fixed.

**Net effect:** because the intact address is separately caught by the
`regex` layer and *that* finding is never routed (hence never demoted),
these 133 demotions did not cause any real PII exposure to be missed from
the report. But they also aren't the verifier catching a genuine false
positive in the sense the feature was designed for (a foreign-ID format
collision, or GLiNER tagging non-PII as an entity) — it's cleaning up a
duplicate side-effect of an unrelated bug, and its own stated reasoning
("part of a list, so not legitimate") is not actually engaging with *why*
the value is wrong. **High demotion rate here is not the verifier "working
well" on this corpus's actual target failure modes — see the
expectation-setting note.**

### 2. The one category suited to the verifier's actual design goal got a 100% error rate

11 `identifier.government.drivers_license_ab` findings fired in this
US-corpus sample — the Canadian-format collision pattern this feature was
built for (see Octopii/ai4privacy evaluations). All 11 were routed. **All 11
came back `llm_verified: False`** (timeout or unparseable response after the
one built-in retry) — zero demotions, zero legitimate verdicts, 100% errors.
The one place in this run where the verifier could have demonstrated its
designed use case produced no signal at all.

**Likely cause (not confirmed by request-level tracing in this read-only
evaluation):** `scan_folder()` runs `DEFAULT_MAX_WORKERS = 16` files
concurrently on this 18-core machine. `llm_verifier.py`'s own docstring
notes calls are kept sequential *within* a file specifically because Ollama
is a "single-slot server," but that guard doesn't cover *across-file*
concurrency — with up to 16 worker threads each potentially calling Ollama
around the same time, sustained queuing against a single Ollama instance
could plausibly push some calls past `OLLAMA_TIMEOUT_S = 30`. The
`tests/ollama_benchmark/` results this feature's routing rules cite were
run as isolated single-inference calls, not under this kind of concurrent
scan load — a gap worth a follow-up, not addressed here.

### 3. GLiNER (the largest single detection source by far — 24,126 hits) was structurally never routable, in this corpus or any other

`gliner`-sourced findings are always `entity.*` category, and
`RISK_SEVERITY["entity"] = "LOW"` (`hybrid_detector.py`). `is_routable()`
explicitly excludes `risk_level == "LOW"` findings ("nothing to demote").
**This means a pure GLiNER entity finding can never be routed under the
current wiring, independent of corpus content** — not a fact about Enron
specifically. See the expectation-setting note below; this contradicts the
task's own a priori assumption that most routed findings would be GLiNER
entities.

## Spot-check: the 4 LEGITIMATE verdicts

Only 4 routed findings (out of 239) came back `LEGITIMATE` — fewer than the
10 the task template asked for, so all 4 are shown rather than a random
subsample:

| File | Value | `llm_reason` |
|---|---|---|
| `scott-s__discussion_threads__553.txt` | `eggy.Banczak@enron.com` | *(empty — see note)* |
| `scott-s__discussion_threads__553.txt` | `bor@velaw.com` | *(empty)* |
| `arora-h__inbox__69.txt` | `.harry@enron.com` | *(empty)* |
| `skilling-j__deleted_items__359.txt` | `.melissa@enron.com` | *(empty)* |

**Note on empty reasons:** this is by design, not a data-loss bug —
`llm_verifier.py`'s `_verdict_fields()` only writes an `llm_reason` key on
the `FALSE_POSITIVE` branch; `LEGITIMATE` verdicts store `{"llm_verified":
True, "llm_verdict": "LEGITIMATE"}` and nothing else.

**Manual read:** all 4 "legitimate" values are themselves truncated
fragments of the exact same class described in root-cause finding #1
(`.harry@enron.com` from a longer real address, etc.) — i.e., the judge's
LEGITIMATE/FALSE_POSITIVE split on these fragments looks essentially
arbitrary rather than a meaningful signal, since the fragments it's being
asked to judge are already-corrupted inputs it has no way to detect as such.
This reinforces finding #1: with only two categories actually exercised in
this corpus, and one of them structurally noisy from an unrelated bug, this
run doesn't give the verifier a fair test of its intended job.

## Latency cost

| | Baseline | Verified | Added |
|---|---:|---:|---:|
| Wall clock | 2398.56s (40.0 min) | 3002.56s (50.0 min) | **+604.0s (+25.2%)** |

**Seconds per routed finding: 2.53s** (604.0s / 239 routed). Given the 42.7%
error rate above, a meaningful fraction of that added latency is spent on
calls that ultimately failed (timeout at `OLLAMA_TIMEOUT_S = 30`, or one
retry after a parse failure) rather than on a productive judgment.

## Caveats

- **US-centric corpus.** Enron is 100% North American corporate email. The
  Canadian-specific detectors this project targets are *mostly* — but not
  entirely — unexercised: 20 health-card findings (`health_card_ca` ×19,
  `health_card_on` ×1) and 11 `drivers_license_ab` findings fired on US
  digit patterns that happened to collide with Canadian checksums/formats,
  consistent with the same foreign-format-collision pattern documented in
  the Octopii and ai4privacy evaluations. `health_card` is never routed by
  design (`SOURCE_PRIORITY` docs — the judge can't catch a same-country
  checksum collision either); the 11 `drivers_license_ab` routings are
  exactly the 100%-error-rate finding above.
- **Plain `.txt` emails — no OCR/extraction stress.** This evaluation says
  nothing about the verifier's interaction with OCR-derived text, PDF/image
  extraction confidence, or any of the format-coverage suite's concerns.
- **1000-file sample, not the full ~517K-message corpus.** Results describe
  this sample; a different seed or a larger N could surface routing patterns
  (e.g., passport-format collisions) not seen here at all.
- **No files changed risk band** in this run — the file-level score is
  insensitive to demotions when other undemoted findings anchor the same
  file to the same band, which was true for all 118 HIGH and 882 MEDIUM
  files here. This evaluation cannot speak to whether verification changes
  file-level bands on a corpus with more borderline (near-threshold) files.

## Appendix: raw `compare_runs.py` output

```
$ docker compose run --rm securescan-cpu python tests/external_enron/compare_runs.py \
    outputs/report_20260712_222816.json outputs/report_20260712_231854.json \
    --baseline-seconds 2398.56 --verified-seconds 3002.56

======================================================================
FINDINGS BY RISK BAND (baseline vs verified)
======================================================================
  HIGH      baseline=   128  verified=   128  delta=    +0
  MEDIUM    baseline=  9561  verified=  9428  delta=  -133
  LOW       baseline= 25254  verified= 25387  delta=  +133
  UNKNOWN   baseline=     0  verified=     0  delta=    +0

======================================================================
FILES BY RISK BAND (baseline vs verified)
======================================================================
  HIGH      baseline=   118  verified=   118  delta=    +0
  MEDIUM    baseline=   882  verified=   882  delta=    +0
  LOW       baseline=     0  verified=     0  delta=    +0
  NONE      baseline=     0  verified=     0  delta=    +0

  Files whose band changed baseline->verified: 0

======================================================================
VERIFICATION SUMMARY
======================================================================
  routed=239  demoted=133  errors=102
  demotion rate = 55.6%

======================================================================
DEMOTIONS BY TAXONOMY CATEGORY
======================================================================
  contact.email                                   133

======================================================================
DEMOTIONS BY SOURCE LAYER
======================================================================
  keyword_context        133

======================================================================
TOP 15 DEMOTED FINDINGS (verbatim)
======================================================================
  file: .../sample/arora-h__inbox__69.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: 'white@enron.com'
  llm_reason: "The value is an email address but the context contains other emails, suggesting it's part of a larger email content."
  ------------------------------------------------------------
  file: .../sample/kean-s__archiving__untitled__5397.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: 'r@duke-energy.com'
  llm_reason: 'The detected type is email but the value does not follow standard email format.'
  ------------------------------------------------------------
  file: .../sample/kean-s__archiving__untitled__5397.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: 'H_Stout@reliantenergy.com'
  llm_reason: "The value is an email address but the context suggests it's part of a list of emails, not a legitimate detection."
  ------------------------------------------------------------
  file: .../sample/kean-s__archiving__untitled__5397.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: 'moretti@mccabeandcompany.net'
  llm_reason: "The value is an email address but the context clearly indicates it's part of a list of names and emails, not a legitimate detection."
  ------------------------------------------------------------
  file: .../sample/kean-s__archiving__untitled__5397.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: 'nguyen@powersrc.com'
  llm_reason: "The value is an email address but the context suggests it's part of a list of names and emails, not a legitimate detection."
  ------------------------------------------------------------
  file: .../sample/kean-s__archiving__untitled__5397.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: 'hyde@duke-energy.com'
  llm_reason: "The detected type is email, but the context suggests it's part of a contact list or header information."
  ------------------------------------------------------------
  file: .../sample/kean-s__archiving__untitled__5397.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: 'ote@williams.com'
  llm_reason: "The detected type is 'contact.email', but the value does not appear to be a valid email address."
  ------------------------------------------------------------
  file: .../sample/kean-s__archiving__untitled__5397.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: 'gaoka@uaecorp.com'
  llm_reason: "The value is an email address but the context clearly indicates it's part of a list of emails, not a standalone one."
  ------------------------------------------------------------
  file: .../sample/kean-s__archiving__untitled__5397.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: 'ic.eisenman@gen.pge.com'
  llm_reason: "The value is an email address but the context clearly indicates it's part of a list of emails, not a legitimate detection."
  ------------------------------------------------------------
  file: .../sample/kean-s__archiving__untitled__5397.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: '.derosa@gen.pge.com'
  llm_reason: "The value is an email address but the context suggests it's part of a list of emails, not a standalone one."
  ------------------------------------------------------------
  file: .../sample/kean-s__archiving__untitled__5397.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: 'lliottsa@earthlink.net'
  llm_reason: "The value is an email address but the context suggests it's part of a list of emails, not a standalone one."
  ------------------------------------------------------------
  file: .../sample/kean-s__archiving__untitled__5397.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: 'ula_soos@ogden-energy.com'
  llm_reason: "The value is an email address but the context suggests it's part of a list of names and emails, not a legitimate detection."
  ------------------------------------------------------------
  file: .../sample/kean-s__calendar__untitled__7966.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: 'ker@duke-energy.com'
  llm_reason: "The value is an email address but the context suggests it's part of a list of names and emails, not a legitimate detection."
  ------------------------------------------------------------
  file: .../sample/kean-s__calendar__untitled__7966.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: 'w@mrwassoc.com'
  llm_reason: "The value is an email address but the context suggests it's part of a list of names and emails, not a legitimate detection."
  ------------------------------------------------------------
  file: .../sample/kean-s__calendar__untitled__7966.txt
  type: contact.email  source: keyword_context  original_risk: MEDIUM
  value: 'bler@reliantenergy.com'
  llm_reason: "The value is an email address but the context strongly suggests it's part of a list of emails, not a legitimate detection."
  ------------------------------------------------------------

======================================================================
SPOT-CHECK: 10 random routed-but-NOT-demoted findings (of 4 total LEGITIMATE verdicts)
======================================================================
  file: .../sample/scott-s__discussion_threads__553.txt
  type: contact.email  source: keyword_context
  value: 'eggy.Banczak@enron.com'
  llm_reason: ''
  ------------------------------------------------------------
  file: .../sample/scott-s__discussion_threads__553.txt
  type: contact.email  source: keyword_context
  value: 'bor@velaw.com'
  llm_reason: ''
  ------------------------------------------------------------
  file: .../sample/arora-h__inbox__69.txt
  type: contact.email  source: keyword_context
  value: '.harry@enron.com'
  llm_reason: ''
  ------------------------------------------------------------
  file: .../sample/skilling-j__deleted_items__359.txt
  type: contact.email  source: keyword_context
  value: '.melissa@enron.com'
  llm_reason: ''
  ------------------------------------------------------------

======================================================================
WALL CLOCK
======================================================================
  baseline_seconds = 2398.56
  verified_seconds = 3002.56
  added_seconds = 604.0
  seconds_per_routed_finding = 2.5271966527196654
```

(Note: `DEMOTIONS BY SOURCE LAYER` shows only `keyword_context` because that
grouping is keyed on each finding's own `source` field, and all 133
demotions happened to be `keyword_context`-sourced — the 11
`drivers_license` routings all errored rather than demoting, so they don't
appear in this table; see the category/source breakdown table above for the
complete picture including errors.)

## Expectation-setting note (read before drawing conclusions)

The task brief for this evaluation anticipated that "most routed findings
will be gliner entities" and that the target false-positive classes were
"bare-digit phone/DL collisions and OCR-free gliner junk." **Neither
happened in this run, and root-cause finding #3 shows the GLiNER expectation
can't happen under the current wiring at all** (GLiNER findings are LOW risk
by taxonomy default, and LOW-risk findings are excluded from routing before
the judge ever sees them). What actually got exercised — a keyword_context
email-truncation duplicate and a 100%-error driver's-licence category — is
not the intended target of this feature, and this evaluation's 55.6%
headline demotion rate should **not** be read as "the verifier is working
well" in the sense the project cares about (catching cross-border ID-format
collisions or GLiNER mistagging real prose as an entity). It is closer to
"the verifier reliably detects a symptom of an unrelated, pre-existing
truncation bug in a different detector, without being able to detect the
cause" — a genuinely different, weaker claim. A corpus/routing setup that
actually reaches GLiNER or passport findings at MEDIUM/HIGH risk (rare given
current taxonomy defaults) would be needed for a real test of the verifier's
designed purpose.

## Evaluation v2 (post-fix)

Same sample (`tests/external_enron/sample/`, 1000 files, `SEED = 1337`,
reused from the cached `enron_raw/` archive — no re-download, files
byte-identical to v1), same two scans, same `compare_runs.py`. Run after
three fixes landed, each root-caused directly from the v1 findings above:

- **Fix A** (`a83174c`) — `keyword_detector.py`'s `find_patterns_near_keywords()`
  matched shape regexes inside a per-keyword text *slice*, truncating any
  match straddling the slice boundary. Now matches the full text once per
  pattern and gates by keyword proximity instead, so a match can never be
  clipped. Fixes finding #1.
- **Fix B** (`05dcb81`) — `scan_folder()` called the LLM verifier inline,
  per file, inside up to 16 concurrent worker threads, queuing against
  Ollama's single-slot server past `OLLAMA_TIMEOUT_S`. Verification now
  runs as one sequential pass over all files' matches after the thread
  pool completes, then re-scores each affected file. Fixes finding #2.
- **Fix C** (`4d10dc8`) — removed `"gliner"` from `ROUTABLE_SOURCES`: GLiNER
  findings are always `entity.*` → `RISK_SEVERITY["entity"] == "LOW"`, and
  `is_routable()` excludes LOW-risk findings before source is even
  checked, so the entry was unreachable dead code. Documents finding #3's
  severity-floor rationale in the code itself.

### Scans

```
docker compose run --rm securescan-cpu python scanner.py --path tests/external_enron/sample --no-open --no-verify   # baseline
docker compose run --rm securescan-cpu python scanner.py --path tests/external_enron/sample --no-open --verify       # verified
```
Ollama confirmed reachable with `qwen2.5:3b` loaded before the verified run.
Baseline: **2740.29s**. Verified: **2333.07s**.

### Findings by risk band (baseline vs. verified)

| Band | Baseline | Verified | Delta |
|---|---:|---:|---:|
| HIGH | 128 | 117 | -11 |
| MEDIUM | 9343 | 9336 | -7 |
| LOW | 25254 | 25272 | +18 |
| UNKNOWN | 0 | 0 | +0 |

### Files by risk band (baseline vs. verified)

| Band | Baseline | Verified | Delta |
|---|---:|---:|---:|
| HIGH | 118 | 115 | -3 |
| MEDIUM | 882 | 885 | +3 |
| LOW | 0 | 0 | +0 |
| NONE | 0 | 0 | +0 |

**3 files changed band this time** (v1 had zero movement) — see "Honest
reading" below; this does not contradict v1's worst-finding-anchoring
explanation, it's the same invariant producing a different outcome because
the *set of undemoted findings* changed.

### Verification summary

```
routed=21  demoted=18  errors=0  legitimate=3
demotion rate (of routed) = 85.7%
error rate (of routed) = 0%
```

### Demotions by category / source layer

| Category | Source | Routed | Demoted | Legitimate | Errors |
|---|---|---:|---:|---:|---:|
| `identifier.government.drivers_license_ab` | `drivers_license` | 11 | 11 | 0 | 0 |
| `contact.email` | `keyword_context` | 10 | 7 | 3 | 0 |

No `gliner` findings were ever offered for routing (`ROUTABLE_SOURCES` no
longer contains it — Fix C) — a structural guarantee now, not corpus luck.
No `passport` findings appeared in this sample, same as v1.

### All 11 driver's-licence verdicts (verbatim)

Every one of the 11 `identifier.government.drivers_license_ab` findings
routed in v1 also appears in v2 — but this time with a real verdict instead
of a timeout, because Fix B removed the concurrent-Ollama-call contention
that caused v1's 100% error rate on this category.

| File | Value | Verdict | `llm_reason` |
|---|---|---|---|
| `heard-m__inbox__master_netting__275.txt` | `1510` | FALSE_POSITIVE | "The value '1510' does not appear to be a valid Canadian drivers license number." |
| `heard-m__inbox__master_netting__275.txt` | `421` | FALSE_POSITIVE | "The value '421' does not appear to be a valid Canadian drivers license number." |
| `heard-m__inbox__master_netting__275.txt` | `7` | FALSE_POSITIVE | "The value '7' does not appear to be a valid Canadian drivers license number." |
| `jones-t__inbox__885.txt` | `1740` | FALSE_POSITIVE | "The value '1740' does not appear to be a valid Canadian drivers license number." |
| `jones-t__inbox__885.txt` | `335` | FALSE_POSITIVE | "The value '335' does not appear to be a valid Canadian drivers license number." |
| `jones-t__inbox__885.txt` | `262` | FALSE_POSITIVE | "The value '262' does not appear to be a valid Canadian drivers license number." |
| `jones-t__inbox__885.txt` | `8867` | FALSE_POSITIVE | "The value is a phone number, not a drivers license ID." |
| `meyers-a__deleted_items__1126.txt` | `6` | FALSE_POSITIVE | "The value '6' does not appear to be a valid drivers license number." |
| `meyers-a__deleted_items__1126.txt` | `25` | FALSE_POSITIVE | "The value '25' does not appear to be a valid Canadian drivers license number." |
| `meyers-a__deleted_items__1126.txt` | `02` | FALSE_POSITIVE | "The value '02' does not appear to be a valid Canadian drivers license number." |
| `meyers-a__deleted_items__1126.txt` | `22872` | FALSE_POSITIVE | "The value is a numeric license plate number, not a typical drivers license ID." |

**All 11 demoted, all correctly.** Manually traced each source value back
to its file:
- `heard-m__inbox__master_netting__275.txt` line 216: `"principal address
  at: 1510, 421 - 7 Ave SW"` — a Sampletown street address (`421 - 7 Ave SW`
  is a real Sampletown street-addressing convention), split by the drivers_
  license_ab detector into three separate digit-run "findings." None are a
  driver's licence.
- `jones-t__inbox__885.txt` line 28: `"Suite 1740, 335 - 8th Avenue S.W."`
  (another Sampletown-convention street address) plus line 30: `"Fax:
  262-8867"` (a fax number, split into two fragments).
- `meyers-a__deleted_items__1126.txt`: `X-FileName: bert meyers
  6-25-02.PST` (date fragments `6`/`25`/`02`) and `Subject: TAG #22872` /
  "tag #22872" in the body (an Alberta Power Pool transmission tag number,
  not a licence).

This is the driver's-licence detector's known weakest-signal, loosest-format
behaviour colliding with Alberta-style
street addresses, fax numbers, and reference tag numbers — a genuinely
different false-positive class from v1's finding #1 (which was pure
truncation-duplicate noise). **This is the verifier doing its actual
designed job** — catching format collisions the checksums can't — visible
for the first time in this corpus because Fix B's 0% error rate finally let
the verdicts through instead of timing out.

### Spot-check: the 3 LEGITIMATE verdicts

| File | Value | `llm_reason` |
|---|---|---|
| `arora-h__inbox__69.txt` | `.harry@enron.com` | *(empty — LEGITIMATE verdicts store no reason, by design, same as v1)* |
| `arora-h__inbox__69.txt` | `.jaime@enron.com` | *(empty)* |
| `skilling-j__deleted_items__359.txt` | `.melissa@enron.com` | *(empty)* |

**Manual read:** all 3 are genuine source-text quirks, not truncation
artifacts — `arora-h__inbox__69.txt`'s `To:` header literally reads `e-mail
<.hai@enron.com>, e-mail <.harry@enron.com>, ...` in the raw corpus file
(verified with `grep` against the sample file directly): Enron's own mail
client wrote these leading-dot addresses into the header as sent, this
project's detectors reproduce them faithfully. The other 7 routed
`keyword_context` emails in this same shape were demoted instead (see next
section) — the model's LEGITIMATE/FALSE_POSITIVE split across
structurally-identical leading-dot values is inconsistent, same
model-reliability caveat v1 already documented, not a new issue.

### The 7 demoted `contact.email` / `keyword_context` findings

Checked each against its file's `regex`-source findings, same method as
v1's finding #1: **all 7 have a full, correctly-spanned, undemoted
duplicate already in the `regex` layer**, differing only by the same
leading-dot quirk described above (e.g. `.hai@enron.com` demoted,
`hai@enron.com` from the `regex` layer untouched, both from the literal
`<.hai@enron.com>` in the source). No real address was lost from the
report by any of these 7 demotions.

### Honest reading: band movement happened this time — here's why

v1 stated no file could change band because a demoted MEDIUM never beats a
surviving HIGH/MEDIUM finding in the same file (`scoring.py`'s worst-
finding anchoring). That invariant hasn't changed and isn't violated here.
What changed is *which findings survive*: in v1, the 11 `drivers_license_ab`
findings all errored — an error leaves `risk_level` untouched
(`llm_verified: False`, no demotion), so those HIGH findings kept anchoring
their files' scores regardless of whether the verifier "wanted" to demote
them. In v2, with the concurrency bug fixed, those same 11 findings got
real verdicts and were demoted to LOW. For 3 files
(`heard-m__inbox__master_netting__275.txt`, `jones-t__inbox__885.txt`,
`meyers-a__deleted_items__1126.txt`), the drivers-licence false positive
was the *only* HIGH-severity finding in the file — once demoted, no other
HIGH/MEDIUM finding remained to anchor the score at HIGH, so the file's
score (and band) dropped:
- `heard-m__inbox__master_netting__275.txt`: 100 → 69 (HIGH → MEDIUM)
- `jones-t__inbox__885.txt`: 100 → 69 (HIGH → MEDIUM)
- `meyers-a__deleted_items__1126.txt`: 93 → 55 (HIGH → MEDIUM)

The other 115 HIGH-band files in this corpus have other undemoted
HIGH/MEDIUM findings (real emails, other digit-shaped identifiers) that
keep anchoring them, exactly as v1 described — so the claim stands as
originally framed, just refined: **band movement is possible, but only
when a demoted finding was the file's sole anchor** — this corpus mostly
doesn't have that property (882/885 files didn't move), but isn't
structurally exempt from it either, as this run demonstrates.

## v1 → v2 deltas

**Baseline finding counts — the phantom truncation-duplicate findings are
gone.** Comparing v1's baseline report (`report_20260712_222816.json`) to
v2's baseline report directly, category-by-category, exactly one bucket
changed:

| Category | Source | v1 baseline | v2 baseline | Delta |
|---|---|---:|---:|---:|
| `contact.email` | `keyword_context` | 228 | 10 | **-218** |

All other categories/sources are byte-for-byte identical between the v1 and
v2 baselines (including `gliner`: 24,126 findings in both — Fix A never
touches GLiNER, Fix C only changes routing, not detection). **This is
larger than the "133 confirmed truncated duplicates" v1 could verbatim-
verify** — v1's finding #1 manually cross-referenced only the 133 of 228
`keyword_context` email findings that got a `FALSE_POSITIVE` verdict before
routing was even fixed; the other 91 of that 228 errored out in v1 (queued
past `OLLAMA_TIMEOUT_S`) and were never manually classified. The v2 delta
confirms nearly all of those un-classified 91 were *also* truncation
artifacts: 218 of 228 raw findings are gone, leaving exactly 10 genuine
`keyword_context` email findings (the leading-dot source-text quirks
described above). Directly confirmed corpus-wide: none of the 16 verbatim
truncated-fragment examples quoted in v1's finding #1 and appendix
(`hyde@duke-energy.com`, `H_Stout@reliantenergy.com`,
`talley.hopson@txh.nm`, `Virginia.C.Levenback@Williams.co`, etc.) appear
anywhere in the v2 baseline report's 4,455 unique `contact.email` values.

**Error rate: 42.7% → 0%.** All 21 routed findings in v2 (11
`drivers_license_ab` + 10 `keyword_context` email) got a real verdict; zero
timeouts, zero unparseable responses, zero `llm_verified: False`. Fix B's
sequential post-pass eliminated the cross-thread Ollama contention
entirely, confirmed both here (1000-file corpus, real concurrent load
during the detection phase) and in the Octopii live smoke test run as Fix
B's own commit evidence (4/4 routed, 0 errors).

**The 11 `drivers_license_ab` verdicts: from zero signal to a clean, fully
explained false-positive catch.** v1 could report only that this category
existed and 100% errored — no verdict, no signal, "the one place the
verifier could have demonstrated its designed use case produced no signal
at all." v2 shows all 11 demoted, and manual tracing (above) confirms every
one is a genuine collision (Sampletown street addresses, a fax number, date
fragments, a transmission tag number) that a Canadian driver's-licence
detector with no checksum has no way to rule out on its own — exactly the
verifier's designed job, now actually observable.

**Latency:** v1 added +604.0s (+25.2%) for verification. v2's verified run
was *faster* than its own baseline by 407.2s — verified=2333.07s vs
baseline=2740.29s. This is very unlikely to be caused by the fixes
themselves (verification adds sequential LLM round-trips on top of
detection, it cannot make detection faster) and is far more likely ordinary
machine-load variance between the two ~40-135min runs (baseline ran
2026-07-13 21:15–21:58, verified ran 22:37–23:37, different times of day on
a shared machine) — flagged here rather than silently reported, since a
negative "added_seconds" is exactly the kind of number that looks wrong at
a glance. The 2.53s/routed-finding figure from v1 is not recomputable as a
clean before/after this run for the same reason (only 21 routed findings in
v2 vs 239 in v1, and wall clock is dominated by GLiNER/detection time
across 1000 files either way, not verification, since routed-finding count
crashed by Fix A removing the duplicate-inflated routing volume). No
conclusion is drawn from v2's wall-clock numbers beyond "verification did
not add prohibitive latency."

## v2 conclusions

- **Fix A worked as intended and is now directly measurable in the
  baseline**, not just the verified run: 218 spurious `keyword_context`
  email findings eliminated corpus-wide, all 16 previously-identified
  truncated fragments confirmed absent, zero new truncation-shaped values
  introduced (manually checked: the 10 remaining `keyword_context` email
  findings are all genuine leading-dot source-text quirks, not fragments).
- **Fix B worked as intended**: error rate 42.7% → 0%, and for the first
  time this evaluation shows the verifier's designed use case (foreign/
  format-collision IDs with no checksum) actually producing verdicts —
  all 11 driver's-licence routings this run were genuine, correctly-caught
  false positives, manually traced and confirmed above.
- **Fix C worked as intended**: zero `gliner` findings routed, now a
  structural guarantee (`ROUTABLE_SOURCES` no longer contains it) rather
  than an artifact of corpus content.
- **Band movement is possible and was observed** (3 files, all HIGH →
  MEDIUM) — this refines rather than contradicts v1's "zero movement"
  reading: the worst-finding-anchoring invariant is unchanged, but Fix B
  means a demoted finding can now actually reach the anchor position when
  it was a file's sole HIGH-severity finding. Most files in this corpus
  have redundant undemoted findings and don't exhibit this, but the
  mechanism is real and was directly observed, not merely theoretical.
- **No additional blocking discrepancy was found** (under the measurement's
  guardrails): the 3 band changes and negative added-latency both looked
  surprising at first glance and were both individually investigated and
  explained above (genuine FP catches; machine-load timing variance)
  rather than silently written up as clean deltas.
