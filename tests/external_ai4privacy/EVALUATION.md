# Quantitative evaluation: ai4privacy PII-masking dataset

Read-only evaluation only — no detector code was changed for this.

## Methodology

**Dataset:** `ai4privacy/pii-masking-openpii-1.5m` — checked Hugging Face for
the current largest/flagship variant rather than assuming the older
`pii-masking-300k`; the 300k dataset's own model card points to this as its
successor (1.6M samples, 30 languages, 19 PII classes).

**Download:** the full dataset is served as ~460MB parquet shards; the harness
only need a few thousand English rows, `download_ai4privacy_sample.py` pages
through Hugging Face's `datasets-server` `/rows` API instead (100 rows/page,
throttled to avoid its rate limit), keeping only `language == "en"` rows and
drops the unused `mbert_tokens`/`mbert_token_classes` fields.
Paged through 32,800 rows (~15% are English) to collect **4001 English
samples** in ~26 minutes, saved to `ai4privacy_en_sample.json` (gitignored,
not committed — see the commit for why).

**⚠️ All 4001 English samples have `region == "SG"` (Singapore).** This
dataset's language and region axes aren't independent — "English" here means
specifically "Singapore-context English," not a general US/UK/international
mix. This matters a lot for interpreting the results below: Singapore phone
numbers, Singapore addresses, and whatever passport/SSN-equivalent format
convention this dataset's generator uses for its Singapore-English slice are
the measured labels — not NANP phone numbers or
literal US SSNs. See caveats at the end.

**Detection:** `detect_pii_hybrid(text, run_ner=True)` (GLiNER on) over each
sample's `source_text`, unmodified. Processed all 4001 samples in 1555s (389
ms/sample) — GLiNER dominates; these are short texts (median 298 chars) so
no chunking was ever triggered.

**Matching rule:** a labeled span and a SecureScan detection "match" if their
normalized values (lowercased, non-alphanumeric characters stripped) overlap
as a substring in either direction, computed as a per-sample, per-bucket
greedy bipartite match (each detection can satisfy at most one label and
vice versa). This is deliberately lenient — exact string equality would
undercount correct detections on formatting differences alone (`"403-555-
1234"` vs `"4035551234"`).

## Label mapping

Several ai4privacy labels feed the same taxonomy bucket (e.g. `GIVENNAME` +
`SURNAME` → `entity.person`, since SecureScan reports one merged full-name
span, not separate given/surname spans). **Recall is measured per
ai4privacy label** (dataset granularity — whether a specific GIVENNAME span
was found); **precision is measured per bucket** (SecureScan granularity — a
detection doesn't know which ai4privacy label it corresponds to). This is
why the results table below is indexed by bucket, not by raw ai4privacy
label.

| ai4privacy label | → SecureScan bucket | notes |
|---|---|---|
| `GIVENNAME`, `SURNAME` | `entity.person` | GLiNER; merged-name vs split-name granularity mismatch, handled by overlap matching |
| `DATE` | `entity.date` | regex + GLiNER; generic dates only — this label doesn't distinguish DOB from any other date, so it isn't mapped to `identifier.personal.dob` |
| `EMAIL` | `contact.email` | regex |
| `TELEPHONENUM` | `contact.phone` | regex |
| `CREDITCARDNUMBER` | `identifier.financial.credit_card` | regex + Luhn |
| `SOCIALNUM` | `identifier.financial.ssn` | regex + `validate_ssn` |
| `CITY` | `entity.location` | GLiNER |
| `DRIVERLICENSENUM` | `identifier.government.drivers_license*` | prefix match across all province variants; SecureScan is Canada-specific, see findings |
| `PASSPORTNUM` | `identifier.government.passport*` | prefix match; `passport_generic` (bare 9-digit+keyword) is country-agnostic by construction, `passport_ca` requires the CA-specific 2-letter+6-digit shape |
| `ORGANISATION` | `entity.organization` | GLiNER; **only 2 instances in the whole 4001-sample set** — see caveats |
| `URL` | `technical.url` | regex; only 1 instance |

**Out of scope (unmapped — no corresponding detector, listed explicitly
rather than silently dropped):** `TITLE`, `STREET`, `BUILDINGNUM`, `AGE`,
`ZIPCODE`, `IDCARDNUM`, `TAXNUM`, `GENDER`, `SEX`, `TIME`, `AMOUNT`,
`COUNTRY`, `CURRENCY`, `USERNAME`, `ACCOUNTNUM`, `SALARY`. Every label
present in the downloaded sample was accounted for (mapped or listed here) —
the evaluation script asserts this and would warn loudly otherwise.

## Results

| Bucket | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| `contact.email` | 2175 | 16 | 41 | 0.993 | 0.981 | 0.987 |
| `contact.phone` | 475 | 563 | 1155 | 0.458 | 0.291 | 0.356 |
| `entity.date` | 2791 | 112 | 804 | 0.961 | 0.776 | 0.859 |
| `entity.location` | 1903 | 2774 | 158 | 0.407 | 0.923 | 0.565 |
| `entity.organization` | 1 | 1781 | 1 | 0.001 | 0.500 | 0.001 |
| `entity.person` | 3917 | 724 | 3127 | 0.844 | 0.556 | 0.670 |
| `identifier.financial.credit_card` | 216 | 19 | 794 | 0.919 | 0.214 | 0.347 |
| `identifier.financial.ssn` | 0 | 1 | 617 | 0.000 | 0.000 | n/a |
| `identifier.government.drivers_license` | 0 | 20 | 858 | 0.000 | 0.000 | n/a |
| `identifier.government.passport` | 0 | 0 | 665 | n/a | 0.000 | n/a |
| `technical.url` | 1 | 2 | 0 | 0.333 | 1.000 | 0.500 |
| **OVERALL (micro-avg)** | **11479** | **6012** | **8220** | **0.656** | **0.583** | **0.617** |

**False-positive source layers (overall):** `gliner` 5388, `regex` 597,
`drivers_license` 20, `keyword_context` 7. GLiNER accounts for the large
majority of raw FP count — but see the `entity.location`/`entity.organization`
findings below for why that number is misleading on its own.

## Root-cause findings (not guesses — traced to specific values)

**`identifier.financial.ssn`: 100% structural format mismatch, not a logic
bug.** All 617 FN values are exactly **10 digits**
(e.g. `3601676408`, `0405196153`). SecureScan's SSN pattern is strictly the 9-digit
`XXX-XX-XXXX` shape — a 10-digit string cannot match it regardless of
`validate_ssn()`'s area/group/serial rules. `SOCIALNUM` in this dataset
is evidently a generic "national number" field, not the literal 9-digit
US SSN. **This is the single largest, cleanest finding in this evaluation.**

**`identifier.government.passport`: same story.** All 665 FN values follow
the exact shape `AA9999999` (2 letters + 7 digits = 9 characters total) —
e.g. `OI5864767`, `RL5422599`. This is neither the `passport_ca` shape (2
letters + 6 digits) nor `passport_generic` (bare 9 digits, no letters) — it
sits exactly between the two supported shapes. Zero false positives occur:
nothing in this dataset accidentally collides with either
passport patterns.

**`identifier.financial.credit_card`: mostly format mismatch, partly Luhn
correctly rejecting synthetic numbers.** Of 794 FNs, 420 are a digit-length
the regex does not attempt (12, 13, 14, 15, 17, 18, or 19 digits — only
strict 16-digit 4-4-4-4 is supported). Of the 374 that *are* 16 digits, **0
of 374 pass Luhn** — every one is a randomly-generated non-Luhn-valid
synthetic number. Precision stayed high (0.919) because when a real
Luhn-valid card number does appear, SecureScan catches it correctly; recall is low
because most of this dataset's "credit card numbers" wouldn't pass a real
card issuer's checksum either. Same class of finding as the Octopii
evaluation's `dummy-ssn.jpg` (`000-00-0000` correctly rejected) — precision
working as designed, not a miss to fix.

**`contact.phone`: a genuine two-sided weak point, not just a scope
mismatch.** FNs are Singapore/international formats the NANP-shaped regex
never attempts — `'+01 48.956 0458'`, `'(20) 2145.8281'`, `'+65 95
118.2657'`, `'0065.17-791.4884'` — dots and irregular groupings throughout.
FPs (563, mostly `regex`) are almost entirely the same 10-digit `SOCIALNUM`
values from the SSN finding above — the phone regex's optional separators
(`\d{3}[-.\s]?\d{3}[-.\s]?\d{4}`) make it match any 10 bare digits, so it
picks up national-number fields with no phone-like formatting at all. This
is the same digit-shape collision pattern found in the Octopii evaluation
(Ukrainian passport "Personal No." misread as a phone number) — bare,
separator-optional digit-run regexes are the recurring source of these
collisions across both evaluations.

**`entity.location` and `entity.organization`: FP counts are inflated by
scope boundaries and label scarcity, not primarily by bad detections.**
- `entity.location` FPs are overwhelmingly street addresses and country
  names GLiNER correctly recognizes as location-shaped — `'641 Duxton
  Road'`, `'Loyang Rise'`, `'Singapore'` — but which the dataset labels as
  `STREET`/`COUNTRY`, both deliberately unmapped (no
  detector targets them specifically). Recall is high (0.923) precisely
  because GLiNER is doing its job; the 0.407 precision number substantially
  understates real quality.
- `entity.organization` is close to statistically meaningless: the entire
  4001-sample set contains **2** `ORGANISATION` labels. GLiNER correctly
  identifies organization-shaped text throughout these form/letter samples
  (`'Artisan Craftsmanship Workshop'`, `'Funsaga'`) that the dataset simply
  never labeled as such. 1781 "false positives" against a base rate of 2
  true labels is a labeling-density artifact of this dataset slice, not a
  measurement of SecureScan.

**`entity.person`: recall gap traced to unusual synthetic surnames, not a
systematic bug.** Most FNs are single surname fragments GLiNER doesn't
confidently tag as a person on their own — `'Sbigottiti'`, `'Peccatus'`,
`'Guikovaty'`, `'van Düren'`. These read as algorithmically-generated
multicultural names outside GLiNER's likely training distribution, checked
here as isolated surname tokens (since ai4privacy splits given/surname but
SecureScan emits one merged span). A stray `entity.person` FP worth
noting: GLiNER tagged the literal word `'employee'` as a person name in one
sample — a real, if rare, quality miss.

**`identifier.government.drivers_license`: near-total miss, as expected —
SecureScan is explicitly Canada-specific** (province-name or DL-keyword
gated, per-province formats). 858 FNs, 0 TP. The 20 FPs came from SecureScan's
`drivers_license` detector's loosest, keyword-only generic path firing on
unrelated digit runs near incidental "licence"-adjacent words — the same
failure mode flagged in the Octopii evaluation (Indian driver's licence
swept into `drivers_license_ca`), here misfiring even without a real
driver's licence number nearby.

## Top-10 false positives (verbatim, picked for diversity across buckets)

| Bucket | Detected value | Source | Sample excerpt |
|---|---|---|---|
| `entity.location` | `641 Duxton Road` | gliner | "...Title: Master\nDate of Birth: 28/11/1980..." (STREET label, unmapped) |
| `entity.organization` | `Artisan Craftsmanship Workshop` | gliner | same sample — form title GLiNER correctly reads as an org, unlabeled |
| `entity.person` | `employee` | gliner | "Policy Document – Expenditure Rounding Guidelines..." — a real GLiNER miss |
| `contact.phone` | `3601676408` | regex | same sample — the 10-digit `SOCIALNUM` misread as a phone number |
| `entity.date` | `next Friday` | gliner | "Hey, can I take next Friday off..." — relative date, dataset only labels absolute dates |
| `identifier.government.drivers_license` | `247964` | drivers_license | "...Dear Felicia Mariam..." — the generic DL path misfiring on an unrelated number |
| `identifier.financial.credit_card` | `659-785-716-8859` | regex | "...contact the support line at +659-785-716-8859..." — a phone number misread as a card number |
| `contact.email` | `audit@energycorp.com` | regex | correctly-formed email the dataset didn't label (a second recipient address) |
| `technical.url` | `https://example.gov.sg/impact‑fee/13HC7VIDLR` | regex | correctly-detected URL, unlabeled in the dataset |
| `identifier.financial.ssn` | `139774545` | keyword_context | a 9-digit number near financial-planning context, not actually the labeled `SOCIALNUM` |

## Top-10 false negatives (verbatim, picked for diversity across buckets)

| Bucket | Labeled value | Sample excerpt | Why missed |
|---|---|---|---|
| `entity.person` | `Aazou Walusiak` | "Name: Lautrim Aazou Walusiak" | unusual synthetic surname |
| `contact.phone` | `02457 09914 ` | same sample | non-NANP format, no matching separator pattern |
| `identifier.government.drivers_license` | `HGUZUE2OPY` | same sample | 10-char alphanumeric, not a Canadian province format |
| `entity.date` | `28/11/1980` | same sample | (mostly caught elsewhere; occasional miss on DD/MM/YYYY near other numbers) |
| `identifier.financial.credit_card` | `676261260221` | "A modest fee of 676261260221 will be applied..." | 12 digits — wrong length for the 16-digit-only regex |
| `identifier.government.passport` | `OI5864767` | "...thank you for trying our AR car loan demo..." | `AA9999999` shape, between the two supported passport formats |
| `identifier.financial.ssn` | `3601676408` | "Policy Document – Expenditure Rounding Guidelines..." | 10 digits — the SSN regex is strictly 9 |
| `entity.location` | `Singapore Sengkang Community Hospital` | "This Certificate of Completion is awarded to..." | long compound institution/place name, not GLiNER-tagged as one span |
| `contact.email` | `lópedriale@hotmail.com` | "Dear Mstr Nangsel Daloua..." | accented character (ó) in the local-part; regex or OCR-adjacent Unicode edge case |
| `entity.organization` | `Funsaga` | "Invoice #: VNFX0AA2A9..." | single-word fabricated org name, low GLiNER confidence |

## Caveats

- **This dataset's English slice is 100% Singapore-region (`region: "SG"`),
  not a general US/UK/international mix.** Every finding above about phone/
  passport/SSN format mismatches should be read as "doesn't match this
  dataset's Singapore-context conventions," not necessarily "doesn't match
  international conventions in general." A different English-region slice
  (if this dataset had one) could show different numbers entirely.
- **SecureScan's Canadian-specific detectors are essentially unexercised by this
  dataset:** SIN, Canadian health cards, Canadian driver's licence formats,
  and Canadian postal codes have no natural presence in Singapore-context
  synthetic data, so this evaluation says nothing about their quality one
  way or the other.
- **Micro-averaged OVERALL numbers are dominated by high-volume buckets**
  (`entity.person`, `entity.date`, `entity.location` account for the large
  majority of TP+FP+FN) — these results do not mean "SecureScan is 66%
  precise" in any general sense; see the per-bucket table and root-cause
  findings for what's actually driving that number.
- **`entity.organization`'s numbers are not meaningful** given a 2-label
  base rate in 4001 samples — included for completeness/transparency, not
  as a real precision/recall measurement.
- GLiNER (`run_ner=True`) ran on every sample regardless of extension, per
  the task; this is not representative of scanning `.py`/`.json`/etc. files
  where `GLINER_SKIP_EXTENSIONS` normally disables it.

## Post-fix addendum: bare-digit-run phone false positives

Follow-up task, driven directly by this evaluation's original `contact.phone`
row (563 FPs) plus the matching finding in
`tests/external_octopii_docs/EVALUATION.md` (Ukrainian passport "Personal
No." misread as a phone). Root cause: the phone regex's fully-optional
separators (`\d{3}[-.\s]?\d{3}[-.\s]?\d{4}`) let ANY bare 10-digit run match
at full confidence — indistinguishable from any other 10-digit ID. Fixed in
two places that both had this exact pattern:

- `detectors.py`: split into `PHONE_FORMATTED_RE` (separators or literal
  parens required — unconditional) and `PHONE_BARE_RE` (bare 10-digit,
  gated on a phone keyword within 30 chars).
- `keyword_detector.py`'s own phone pattern had the identical
  fully-optional-separator bug, gated only by a much wider 100-char keyword
  window — same fix (separators required), since bare-digit+keyword
  detection is now exclusively `detectors.py`'s job with its tighter check.

**Two-pass re-evaluation** (fixing `detectors.py` alone first, then also
`keyword_detector.py`, to isolate which layer was contributing what):

| | Before | After detectors.py fix | After both fixes |
|---|---:|---:|---:|
| TP | 475 | 451 | 440 |
| FP | 563 | 83 | **7** |
| FN | 1155 | 1179 | 1190 |
| Precision | 0.458 | 0.845 | **0.984** |
| Recall | 0.291 | 0.277 | 0.270 |

FP collapsed from 563 to 7 (98.8% reduction) — the remaining 7 are a mix of
a formatted placeholder-looking number (`123-456-7890`, a real phone shape,
just not the one labeled), formatted numbers near a keyword that don't match
any label, and bare digits that happened to sit within 30 chars of a
genuine phone keyword coincidentally — exactly the small residual risk the
keyword-gate design accepts by construction, not a bug.

**TP dropped 475 → 440 (7.4%) — traced to a specific, defensible cause, not
a general regression.** All 35 newly-missed labels are Singapore-format
numbers like `090.3123477` and `059 2083811` — a 2-group split (3+7 digits,
one separator), not the 3-group NANP split (`\d{3}-\d{3}-\d{4}`) the
formatted pattern requires. Confirmed directly: the *old* buggy regex only
matched these as a side effect of its fully-optional separators absorbing
digits into whichever group had room, regardless of where the real
separator fell — it was never designed to support this format. The stated
scope is Canadian + generic US (NANP) phone numbers; losing an accidental,
unintended catch of a non-NANP format is the correct trade for eliminating
the false-positive collision, not a loss of in-scope recall.

**Found, not caused: `identifier.government.drivers_license` FP rose 20 →
54.** Traced all 34 newly-appeared DL false positives to bare 10-digit
values that were previously misclaimed by the buggy phone regex —
`hybrid_detector.py`'s reconciliation logic drops a DL match whenever the
same digits are claimed by a *stronger* detector, phone included. Those
digits are no longer falsely claimed by phone, so they now surface in the
DL bucket instead of being silently reconciled away. This is a real,
pre-existing weakness in the driver's-licence detector's own bare-digit
matching that the phone bug was incidentally masking — not something this
fix introduced. This remains an unresolved benchmark finding; a follow-up
could apply the same separator-or-keyword-window discipline there.
