# Legacy Tesseract validation: Octopii dummy-PII corpus

This document records the retired Tesseract pipeline for historical comparison.
Current OCR uses PaddleOCR; do not infer current OCR behavior from legacy PSM,
preprocessing, confidence, or dual-pass results below.

Read-only evaluation only — no detector code was changed for this. Scanned
with the normal pipeline (`scanner.py --path tests/external_octopii`),
GLiNER on per current `config.py`. Full run: 9 files (8 specimens + this
directory's own `README.md`, excluded below), 6.72s, model load included.

*Note: this file and `README.md` originally lived alongside the 8 image
specimens in `tests/external_octopii/` and were themselves picked up by
scans of that folder (see the post-fix addendum's `EVALUATION.md` row
scoring 100 on a later re-scan, purely from its own PII-shaped example
text). Both docs were subsequently moved to `tests/external_octopii_docs/`
— a sibling directory nothing ever scans — to stop that. The image corpus
itself is unaffected; every result below is unchanged.*

**Scope reminder:** SecureScan targets Canadian + generic US PII (SIN, SSN, Canadian
health cards/driver's licences/passports, credit cards, email/phone/postal
code). Aadhaar, PAN, Hong Kong resident ID, and UK/Ukraine passport *number
formats* are foreign ID types SecureScan does not target — a correct non-detection of
those specific ID numbers is scored `OUT_OF_SCOPE`, not a gap.

## Results table

| File | Score | OCR excerpt (first ~200 chars) | Detected (taxonomy / band / source) | Verdict |
|---|---|---|---|---|
| `dummy-aadhaar.png` | 92 | `AADHAAR\n© sree marr ang,\n© BHAA IT HTT BT.\n© —\nA 1800 160 1947 Was aU, fear\n4. 1947, Se Ts fain\nheip@uidal.gov.in ax Ha ET.\nite gore 15 aa\naaa Set ST.\nINSTRUCTIONS.\nAadhaar is proof of identity, no` | `contact.email` / MEDIUM / regex — plus 3 **false positives**, see below | OUT_OF_SCOPE (Aadhaar number itself) — see ⚠️ false positives |
| `dummy-debit-card.jpg` | 11 (pre-fix) | `wee ts\nState Bank\n10/13\nIJAY BEHERA ©` | none in-scope (LOW GLiNER noise only) | GAP — extraction quality (see below); no complete card number exists in the source to detect either way. **Fixed, see post-fix addendum.** |
| `dummy-drivers-license-maharashtra.jpg` | 79 | `THE UNION OF INDIA\nMAHARASHTRA STATE MOTOR DRIVING LICENCE ia\nDL No .MM03 200800000000 DO! 24-01-2007\nsee 24 Valid Till : 23-01-2027 (NT) 09-03-2011 (TR)\nAED 15-03-2008 RULE 16 (2)\nAUTHORISATION TO D` | `identifier.government.drivers_license_ca` / HIGH / drivers_license — **false positive**, see below | OUT_OF_SCOPE (Indian DL) — see ⚠️ false positives |
| `dummy-hong-kong-resident-id.png` | 38 | `HONG KONG PERMANENT IDENTITY CARD\nsk "2043308\nLOK, Wing Ching\n2867 3057 2532\nELM Date of Birth\n03-06-1985\nBS HI Date ofissue\n(06-96)\n26-11-18 72683365(5)\nAIWWVS` | `identifier.personal.dob` "03-06-1985" / MEDIUM / keyword_context | OUT_OF_SCOPE (HK ID number) + **DETECTED** (DOB, correctly) |
| `dummy-PAN-India.jpg` | 1 | `06/11/1988\nPermanent Account Number\nSignature` (full text — that's all Tesseract read) | `entity.date` only / LOW / regex | OUT_OF_SCOPE (PAN number — also never OCR-legible at any PSM tried, see below) |
| `dummy-passport-britain.jpg` | 9 | `Kingdom of Great Britain and Northern\nPassport Passeport\nPasspurt Na Poseport Nu\n023477812...\nNatioality\n' BRETISH\ni Date Date tte\nJAN/JAN \| 55\n\| Place af de\nM f° ENFIELD,\n\| Date of issuefate de d` | `entity.organization`, `entity.location` / LOW / gliner | OUT_OF_SCOPE (UK passport number) — near-miss detail below |
| `dummy-passport-ukraine.jpg` | 78 | `YKPAIHA @ UKRAINE &\nTun! Type Kon Country code Homep nacnopra/ Passport No.\nEK000001\neo PASSPORT\nGiven Names\nnaevddo\nIBAH/IVAN\npomaaAHcTeo/ Nationality\nYKPATHA/UKRAINE >\nAata Date of birth Homep/ Pe` | `contact.phone` / MEDIUM / regex — **false positive**; `identifier.government.passport_ca` "EK000001" / HIGH / passport — **false positive**, see below | OUT_OF_SCOPE (Ukrainian passport) — see ⚠️ false positives |
| `dummy-ssn.jpg` | 0 (pre-fix) | *(empty — see special attention below)* | none | GAP — extraction failure (see below); also see SSN validity note. **Fixed (text now extracts), see post-fix addendum — SSN still correctly not detected, for an unrelated reason.** |

Of the 8 specimens: **2 are clean correct non-detections** (PAN, UK passport
— genuinely nothing fired on the target ID); **2 have a correct positive
detection of an in-scope field** (HK ID's DOB, Aadhaar's email — note
Aadhaar also has false positives, see below); **3 have a false-positive
government-ID/financial match** on a foreign-format collision (Aadhaar,
Maharashtra DL, Ukraine passport); **2 have a real extraction gap** (debit
card, SSN). These buckets overlap per-file (e.g. Aadhaar is both a correct
detection and a false-positive case) rather than partitioning the 8 files.

## ⚠️ Notable false positives (the important findings)

These are documents scoring HIGH/MEDIUM largely because a *foreign* ID
number's shape coincidentally satisfies a Canadian/US validator — not because
SecureScan detected the actual target ID type.

### 1. Ukrainian passport number ≡ the Canadian passport shape

`passport_detector.py`'s `passport_ca` pattern is 2 letters + 6 digits
(`[A-Z]{2}\s?\d{6}`), keyword-gated. Ukraine's passport number format is
**also** 2 letters + 6 digits. The specimen's number `EK000001`, sitting near
the (correctly OCR'd) word "PASSPORT", fires the detector at HIGH confidence,
labeled `passport_ca` — a confident, wrong-country mislabel. This is a
genuine format collision, not a bug in the regex (Canada's format just isn't
unique), but worth knowing: any 2-letter+6-digit passport number from
**any** country matching this shape will be flagged and labeled Canadian.

Same file: `Homep nacnopra/ Personal No. 25 83 3052125257` (a Ukrainian
"Personal No." field, 10 digits) matches the bare phone-number regex and is
reported as `contact.phone` — another coincidental digit-shape collision,
unrelated to the passport issue.

### 2. Aadhaar reference numbers ≡ OHIP / Luhn checksums

The Aadhaar card prints an "Enrollment No." and a "Ref:" number
(`1145002075`, `2907 2881906701 906544`). Two 10-digit substrings from these
happen to satisfy Ontario's OHIP checksum exactly (`ohip_valid()` — this is
Tier 1, checksum-only, **no keyword required**, which is exactly why it
fired), and a 12-digit substring happens to pass Luhn and is reported as a
credit card. All three are false positives on bureaucratic reference
numbers that have nothing to do with health cards or payment cards — pure
checksum coincidence. Given health-card Tier 1 requires no keyword at all,
this is the easiest of the three collisions to hit by chance on any
sufficiently long foreign ID/reference number.

### 3. Indian driver's licence swept into generic `drivers_license_ca`

`drivers_license_detector.py`'s generic path (`drivers_license_ca`) fires on
**any** keyword-plausible-length digit run when a driver's-licence keyword
is nearby, with no country check — by design, since Canadian DL formats
vary so much per-province that the detector already leans on keywords over
shape. The Maharashtra licence number `200800000000` (12 digits) sits near
"MOTOR DRIVING LICENCE" / "DL No", so it's flagged HIGH as
`drivers_license_ca`. This is the same class of issue as #1: a
country-agnostic keyword-gated detector doesn't know the document is Indian.

Same file: `identifier.personal.dob` grabbed `10-03-2008` (actually the
"TRANS" authorization date) rather than the real
`DOB : 01-12-1887` a few lines up — the keyword-context window picked the
nearer date-shaped string, not the one actually labeled DOB. A real
mis-association, independent of the false-positive issue above.

*Update (post commit `8ceb0c7`, OCR orientation ladder):* this specific
`drivers_license_ca` collision no longer reproduces on this specimen — the
OCR ladder changed the extracted text enough that the "DL No" keyword no
longer lands next to the digit run, so the keyword gate doesn't fire. The
false-positive *class* itself is still real and unfixed by design (see
`tests/external_enron/EVALUATION.md`'s 11 `drivers_license_ab` findings on
the same foreign-ID-collision pattern), and remains the kind of case the
`llm_verifier` verification layer is meant to catch/downgrade.

## GAP diagnosis (extraction vs detection)

### `dummy-debit-card.jpg` — extraction gap, and no complete number exists anyway

Legacy production OCR text: `'wee ts\nState Bank\n10/13\nIJAY BEHERA ©'`.
Raw `pytesseract.image_to_string()` with **no** preprocessing and default
settings recovers more: `'5422 |\n$422\n\n. mse 10/13 var 11/22\\\n\nBIJAY BEHERA~\n\n'`
— it keeps the partial card-number fragment `5422`, the second date `11/22`,
and reads the name correctly as `BIJAY` (the legacy pipeline drops the leading B → `IJAY`).

The cause is legacy **preprocessing**, not the confidence filter.
`_extract_image_text()` (preprocessing, no confidence filtering at all)
already shows the same losses as the full production path. The
grayscale/contrast/median-filter/sharpen/adaptive-threshold pipeline —
tuned mostly against project-generated white-background test
images — measurably hurts extraction on this photographed, reflective,
colored card compared to doing nothing at all.

That said: this is **not** a missed credit-card detection. Looking at the
image directly, the middle of the card number is physically covered by an
opaque redaction bar in the source specimen, with only a second partial
fragment (`5343`) visible under a scratched-off hologram sticker elsewhere.
There is no complete, contiguous card number anywhere in this image for
Luhn to validate one way or the other — the gap here is purely about lost
OCR fidelity on the fragments that *do* exist, not a failure to recognize a
real card number.

### `dummy-ssn.jpg` — extraction failure at the fixed PSM setting

Legacy production OCR: empty string, 0.0 confidence. Raw `pytesseract` with
**no** preprocessing, same default settings, is *also* empty — this isn't
the legacy preprocessing pipeline's fault. Alternate Tesseract page
segmentation modes directly:

- `--psm 3` (legacy production, "fully automatic"): empty
- `--psm 4`: empty
- `--psm 6` ("uniform block of text"): partial — recovers
  `THIS NUMBER HAS BEEN ESTABLISHED FOR` and other fragments
- `--psm 11` / `--psm 12` ("sparse text"): partial — recovers more fragments
  including a `/ 000.` digit fragment, but never a clean, complete
  `000-00-0000`

The card's stylized banner header, decorative pillar graphics, and circular
red stamp apparently confuse Tesseract's automatic layout analysis (PSM 3)
into finding no coherent text block at all. A non-default PSM recovers
*something*, but even then the actual SSN digits never come through
cleanly. This is a genuine, reproducible extraction gap tied to the fixed
`--psm 3` configuration in the retired pipeline.

## Special attention (task item 5)

**`dummy-ssn.jpg`: should this hit?** The printed number is `000-00-0000` —
not a redacted/random dummy value, but the literal universal placeholder
text pre-printed on generic blank/sample SSA cards (the same text appears
on countless "sample SSN card" stock images). SecureScan's `validate_ssn()`
explicitly rejects `area in ("000", "666")`. **Even with perfect OCR, this
specific specimen would correctly NOT validate as a real SSN** — that's the
validator working as designed, not a miss. Separately and independently,
the legacy OCR pipeline also fails to extract any text from this image (see
above) — a real gap, but not the reason this particular value goes
undetected.

**`dummy-debit-card.jpg`: does the number pass Luhn?** There is no complete
number to check — see the GAP diagnosis above. The middle of the printed
card number is physically redacted in the source image; only two
disconnected 4-digit fragments (`5422`, `5343`) are ever visible. Whether a
full number would pass Luhn is unanswerable from this specimen — this is
not a Luhn-precision case, it's a "the PII was never fully in the source
image" case.

## Post-fix addendum

`image_extractor.py` gained two resilience mechanisms directly from the two
gaps above (see the commit "OCR resilience: PSM fallback for
layout-analysis failures, dual-pass for preprocessing-hostile images"):

1. **PSM fallback** — if the primary `--psm 3` pass returns near-empty text
   (<20 non-whitespace chars), retry with `--psm 11` (sparse text, no
   layout assumptions) and keep whichever has more content.
2. **Dual-pass preprocessing** — if the preprocessed-image result still
   looks weak (avg confidence <50 OR <40 non-whitespace chars), also run
   OCR on the raw, unpreprocessed image (with a lenient per-word confidence
   floor, since the whole point is recovering content the strict floor
   would otherwise trade away) and keep the richer result. Gated so a clean
   image — the common case — takes exactly one OCR pass.

Both mechanisms report which strategy won via new `psm_used` /
`dual_pass` / `pass_used` fields in the file's extraction metadata.

### `dummy-ssn.jpg` — before → after

| | Before | After |
|---|---|---|
| Extracted text | `''` (empty) | `'=\nfe\nSEC\nYet\ntae\nWAR\neel\n000-0 000°\nTHIS iden HA\nESTABLIS ED FOR\nJO\nBH: DOE,\n73\nad\nLee\nSIGNATURE\nSS\nhi\nWut\nvel\nWhi\nUi fil\neee\n=—\n='` |
| OCR confidence | 0.0 | 58.4 |
| Metadata | — | `psm_used: 11` (PSM fallback fired) |
| SSN detected? | No (no text at all) | **Still no** — `000-0 000°` doesn't even survive as a clean 9-digit run after OCR noise, and even a clean read of `000-00-0000` would correctly fail `validate_ssn()`'s `area != "000"` rule regardless |

The extraction gap is fixed — text now comes out — and the correct
non-detection is preserved for an independent reason (the validator, not
missing text). Confirmed by re-running the scan: `validate_ssn()` rejecting
the universal SSA placeholder value is precision working exactly as
designed, not a miss to fix.

### `dummy-debit-card.jpg` — before → after

| | Before | After |
|---|---|---|
| Extracted text | `'wee ts\nState Bank\n10/13\nIJAY BEHERA ©'` | `'5422 \|\n$422\n. mse 10/13 var 11/22\\\nBIJAY BEHERA~'` |
| OCR confidence | 79.9 | 43.7 (lower — the raw pass includes more low-confidence noise, an accepted tradeoff for recovering real content) |
| Metadata | — | `psm_used: 3, dual_pass: true, pass_used: "raw"` |
| Detected | `entity.person: IJAY BEHERA` (name truncated) | `entity.person: BIJAY BEHERA` (correct, full name) — plus the `5422` card-number fragment now survives in the text, though it's still not a complete/checkable number (redacted in the source, see above) |

Confidence going *down* while quality goes *up* is expected and is exactly
why the dual-pass gate checks text length as well as confidence (see
`image_extractor.py`'s `WEAK_TEXT_CHARS` comment) — a bare confidence check
would never have caught this case, since the original 79.9% already looked
"fine" despite silently missing words.
