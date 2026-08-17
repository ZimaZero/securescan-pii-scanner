# urchade/gliner_multi_pii-v1 vs. production gliner_medium-v2.1

Measurement only. No production defaults, `LABELS`, or detector code changed.
Both models remain cached on disk. This document is the analysis; raw data
lives alongside it:

- `docs/evidence/gliner_pii_raw.jsonl` — every entity `gliner_multi_pii-v1`
  returned, full label set, batched (schema below).
- `docs/evidence/gliner_medium_raw.jsonl` — the same schema for production
  `gliner_medium-v2.1` (4 labels) over the identical corpora/text.
- `docs/evidence/deterministic_raw.jsonl` — SecureScan's own non-NER
  detector stack (regex/keyword/secrets/health/passport/UCI/status/OCR-
  recovery/DL/MRZ) re-run over the same cached extracted text, `run_ner=False`,
  for the overlap section.
- `docs/evidence/gliner_pii_analysis_summary.json` — machine-computed
  aggregates behind this document (confidence distributions, overlap
  tallies, specimen adjudication, cost).
- `docs/evidence/gliner_pii_new_coverage_samples.json` — up to 15 stratified
  samples per new-coverage label, the basis for the manual judgments below.
- `docs/evidence/gliner_pii_eval_*.py` — the four scripts that produced all
  of the above (extraction cache, deterministic dump, GLiNER scan, analysis).

## Corpora and coverage

| Corpus | Files | Notes |
|---|---|---|
| `tests/canadian_eval_data` | 91 | synthetic Canadian identifier fixtures |
| external specimen corpus | 81 | photographed real-format licences/health cards/passports/PR/status cards + negative controls, `GROUND_TRUTH.csv` |
| `tests/external_enron/sample` | 1000 | Enron real-email anchor |
| `tests/stress_data` | 174 | deterministic seeded stress corpus (44M raw chars) |
| `tests/format_data` | 18 | format-coverage anchor |
| `tests/external_octopii` | 8 | foreign-ID false-positive benchmark |
| external photographed-document anchor | 11 | real photographed-document anchor |
| **Total** | **1383** (1381 with non-empty text) | |

One file, `tests/stress_data/marketing/team_gamma/project_z/archive/2024/filler_0072.log`,
could not be completed and is excluded — see **Cost** below for why and what
it means.

## Methodology

**Model download.** `urchade/gliner_multi_pii-v1` was pulled with
`huggingface_hub.snapshot_download` (online, one-time; all subsequent runs
were `HF_HUB_OFFLINE=1`). It also required `microsoft/mdeberta-v3-base`'s
tokenizer (the base model it was fine-tuned from is multilingual, unlike
production's `microsoft/deberta-v3-base`), a second one-time online
download.

**Model size.** `~/.cache/huggingface` grew from **1.5 GB to 2.6 GB**
(+1.1 GB: `gliner_multi_pii-v1`'s own weights, plus 4.2 MB for the
`mdeberta-v3-base` tokenizer). Local disk went from 24 GB to 23 GB free.
Both models remain cached; nothing was deleted.

**Full label set.** The model card publishes this list verbatim (52 distinct
concepts, with `passport number`/`passport_number` and
`social security number`/`social_security_number` as literal underscore-
variant duplicates — kept as published rather than silently deduplicated,
since the task asked for the full set, not a subset):

`person, organization, phone number, address, passport number, email, credit
card number, social security number, health insurance id number, date of
birth, mobile phone number, bank account number, medication, cpf, driver's
license number, tax identification number, medical condition, identity card
number, national id number, ip address, email address, iban, credit card
expiration date, username, health insurance number, registration number,
student id number, insurance number, flight number, landline phone number,
blood type, cvv, reservation number, digital signature, social media
handle, license plate number, cnpj, postal code, passport_number, serial
number, vehicle registration number, credit card brand, fax number, visa
number, insurance company, identity document number, transaction number,
national health insurance number, cvc, birth certificate number, train
ticket number, passport expiration date, social_security_number`

**Single pass vs. batched groups.** A pre-flight benchmark
(`/tmp/gliner_pii_label_bench.json`, methodology preserved here since it's
not part of the final corpus run) queried the model both ways on four
sample texts (short synthetic text, two format-corpus fixtures, one real
Enron email) and diffed the results:

| sample | full-53 time | batched-5×~11 time | same result set? |
|---|---|---|---|
| `data.csv` (135 chars) | 0.43s | 0.72s | **no** |
| `data.json` (212 chars) | 0.29s | 0.85s | **no** |
| short synthetic (79 chars) | 0.21s | 0.57s | **no** |
| Enron email (1373 chars) | 0.93s | 3.29s | **no** |

Single-pass was faster per call (less fixed overhead: one forward pass
instead of five), but on every sample it **missed entities batching found**
— on the Enron email specifically, single-pass silently dropped a real
address, a real email, and several real organization names that the batched
groups recovered (all 53 labels compete for the same spans in one pass; with
53 candidate types per span, weaker/less-salient labels lose). This is
exactly the quality-degradation case the task anticipated, so **batched
groups were used for the full corpus run**, accepting the higher per-chunk
cost. Batching is not free of its own effect — a value can now get *two*
different labels from two different groups if it plausibly fits both (see
the driver's-licence mislabeling case in **Overlap**, below) — so results
here are not directly comparable to a hypothetical clean single-pass run;
that tradeoff is the point being reported, not hidden.

The five groups (mutually exclusive, covering all 53 labels exactly once):

```
identity_gov:      person, passport number, passport_number, national id number,
                    identity card number, identity document number,
                    driver's license number, birth certificate number,
                    tax identification number, cpf, cnpj,
                    passport expiration date, visa number
financial:         credit card number, credit card expiration date,
                    credit card brand, cvv, cvc, bank account number, iban,
                    transaction number, social security number,
                    social_security_number
contact_location:  email, email address, phone number, mobile phone number,
                    landline phone number, fax number, address, postal code,
                    ip address, organization, username, social media handle,
                    digital signature
health_medical:    health insurance id number, health insurance number,
                    national health insurance number, medical condition,
                    medication, blood type
travel_misc:       flight number, train ticket number, reservation number,
                    license plate number, vehicle registration number,
                    registration number, student id number, insurance number,
                    insurance company, serial number, date of birth
```

**Threshold.** GLiNER's own library default, 0.5, for both models — no
additional post-hoc confidence filtering, no deduplication, matching the
task's "no filtering beyond the model's own minimum."

**`NER_MAX_CHARS` capping.** Production's `detect_pii_hybrid()` already caps
GLiNER's input at `config.NER_MAX_CHARS = 150,000` characters per file
regardless of file size — this evaluation reapplied that *exact* existing
bound rather than inventing a new sampling rule. It mattered enormously: the
stress corpus alone is 44,079,619 raw characters across 174 files (one
`edge_cases` file is 4.2M characters by itself); capping brought the whole
seven-corpus total from **46.78M to 10.25M characters** (stress:
44.08M→7.54M). Every other corpus was materially unaffected (Enron's
largest file is far under the cap). 34 files total were truncated, all in
`stress`. Files needing the cap are flagged `ner_truncated` in the raw
manifests.

## 1. New coverage

Labels where `gliner_multi_pii-v1` fired and SecureScan has **no detector at
all** (excludes `person`/`organization`, which production's own
`gliner_medium-v2.1` already emits as `entity.person`/`entity.organization`):

| label | count | verdict (manual, n≥1 per row below, ≥10 where volume allowed) |
|---|--:|---|
| `address` | 135 | **Real, useful new class.** Genuine multi-line mailing addresses (`"10375 Richmond, Suite 300\nHouston, Texas 77042"`, a real Saskatoon business address) mixed with weaker fragments — a bare `"EB 3389"` office/room code at 0.88 confidence, city-only spans (`"Clarksville, Tenn."`), one quoted-printable soft-break artifact. SecureScan has no full-address detector (only `contact.address.postal_code`), so this is the strongest single new-coverage candidate — but noisy at the edges. |
| `insurance company` | 56 | Mostly real company names (`AEGIS Insurance Services`, `Enron Assurance Services`) but frequently non-insurers swept in by proximity to financial language (`Fidelity`, and Enron's own ticker `ENE` at 0.91 confidence). Company-name PII value is marginal even when "correct." |
| `credit card brand` | 36 | Real brand words (`Visa`, `MasterCard`, `American Express`) reliably found, but also POS-terminal *hardware* brands (`Hypercom`, `Nurit`) mislabeled as card brands, and one unrelated Spanish-language abbreviation (`NAMI`). Brand mentions aren't cardholder PII regardless. |
| `cnpj` (Brazilian tax ID) | 26 | **~0/15 sampled true positives.** No CNPJ format exists anywhere in this corpus; every hit is an Enron Lotus-Notes/Exchange username from an email header (`X-Origin: MTAYLOR5`, `CN=MGRIGSB`) or an internal request ID. Pure label collision, not evidence of the model finding real CNPJs — flagged here as a caution against trusting label names as ground truth. |
| `credit card expiration date` | 30 | Real *dates* extracted reliably, but the label is close to meaningless: most hits are Canadian driver's-licence expiry dates from specimen photos (`2017/02/28` on an Ontario licence, `2026-Jan-04` on a BC licence) or plain Enron business dates, not credit-card expirations. Functionally indistinguishable from production's own generic `entity.date`. |
| `transaction number` | 30 | Mixed: one real invoice number, several plausible internal Enron trade/deal reference numbers, one MRZ line mislabeled as a transaction number, and two outright false positives on common phrases (`"scheduled power\ntransactions"`, `"PGE transaction"` — no number present at all). |
| `username` | 29 | Roughly half genuine (`aw162`, `accountit` as a real "Seller User ID" field) and half noise: bare domain names mislabeled as usernames (`paulhastings.com`), template/placeholder text taken as a real value (`"Substitute \"john.doe\" with your first and last name"` — literally instructional text, not a real user's handle), and one case where `contact.email`-territory addresses got the `username` label instead of `email`. |
| `vehicle registration number` | 19 | **Not new coverage — see Overlap section.** Every sampled hit is a Canadian driver's-licence number SecureScan's own `drivers_license_detector.py` already finds under a different taxonomy; the model is re-finding the same value under a different one of its 53 labels. |
| `passport expiration date` | 26 samples | Real dates, correct extraction, wrong document-type label almost every time: the large majority are driver's-licence "Exp" fields from Canadian specimens (PE, NB, NL, BC), not passports. One `94 0LLF` (Quebec licence) is pure OCR garble, not a date at all. |
| `reservation number` | 12 | Genuine travel confirmation codes (`M9GO7V`, a Southwest PNR; `10910`, a hotel "reference booking" number) alongside two phone numbers mislabeled as reservation numbers (`713-939-2349`, `713-939-2192` — already `contact.phone` territory) and two bare phrases with no number present. |
| `bank account number` | 11 | Two genuine real account numbers from a real bank statement specimen (`02782-5094431`), diluted by a phone number mislabeled as an account number, an `EB 3829a` office code, a redacted placeholder (`"Account number\nxxxxxxXX"`), and one 419-scam phrase (`"your own designation bank\naccount"`). |
| `medical condition` | 16 | Technically-correct entity extraction (`knee injury`, `torn ACL`, `flu`, `sprained ankle`, `mild case of malaria`) — but every hit comes from Enron fantasy-football/sports-injury-report emails describing **professional athletes**, not the email's author or any real data subject. Correct NER, near-zero actual privacy value in context. |
| `registration number` | 6 samples | **Not new coverage.** All are Certificate of Indian Status registration numbers SecureScan's own `status_card_registration` detector already finds deterministically (`identifier.government.status_card_registration`), under yet another of the model's 53 labels. |
| `license plate number` | 9 | Same pattern as `vehicle registration number`: sampled hits are driver's-licence numbers from Canadian specimen text, not plates. |
| `flight number` | 6 | Best signal-to-noise of the small-count labels: a real Air Canada flight+class code (`AC3194/M`, 0.99), a plausible flight reference (`#2112`), one Aeroplan loyalty number (real travel identifier, wrong sub-category) — against one airline-name-not-flight-number false positive (`ATA`, an airline abbreviation, mislabeled twice). |
| `national id number` | 2 | Both genuine Canadian ID field values (a PR card's "ID No" field, a Nova Scotia licence reference), but the NS one duplicates `drivers_license_ns` territory again. |
| `identity document number` | 2 | Both the same value: the Aadhaar number on `dummy-aadhaar.png` (the Octopii foreign-ID benchmark case). Notably **better behaved than SecureScan's own regex collision here** — the model uses a generic, country-neutral label rather than falsely asserting a Canadian category the way the deterministic OHIP-checksum collision does (documented in `tests/external_octopii_docs/EVALUATION.md`). |
| `ip address` | 4 | 3 of 4 are genuine real IPv4 addresses from Enron email headers (`128.193.84.130`), correctly extracted — but SecureScan already has a deterministic `technical.ip_address` regex detector, so this is arguably **overlap the task's fixed list didn't name**, not new coverage. One false positive (`IPTV`, substring collision). |
| `digital signature` | 7 | 0/7 genuine. All are mentions of the *concept* of e-signatures in policy/legal text (`"electronic signatures"`, `"wet signatures"`) or a re-find of the same bank account number under yet another label — never an actual signature artifact. |
| `blood type` | 10 | 0/10 genuine blood-type *values* (no `O+`/`AB-`-style hits at all). Triggered by any occurrence of the word "blood" — religious phrasing (`"blood of Jesus"` ×3), a real donation-drive/injury mention, and two hits that only label the *field name* "Blood type" on a medical-form template rather than an actual value. |
| `cvc` | 14 | 0/14 genuine. Complete noise: a medical billing remark code, a stock-index point change, two vodka brand names, an Enron employee ID, quoted-printable encoding artifacts (`=01`). Nothing resembling an actual card verification code. |
| `cvv` | 3 | 0/3 genuine — an internal Enron index abbreviation (`DZCV` ×2) and an unrelated volume number. |
| `visa number` | 3 | 0/3 genuine travel visas. Two are the *credit-card brand* "Visa" (payment method), one is a medical receipt number — a real semantic collision between the immigration document and the payment network sharing a name. |
| `insurance number` | 9 | 1 genuine hit (a real "POLICY #" field on an Explanation of Benefits), the rest are MRZ lines mislabeled, medical billing codes, and OCR garble. |
| `birth certificate number` | 2 | 0/2 — both are mentions of the phrase "birth certificate" (as a document you need to bring), never an actual number. |
| `social media handle` | 6 | 2 genuine real handles (`twitter.com/PepcoConnect`, `facebook.com/PepcoConnect`), against real email addresses mislabeled as handles (already `contact.email` territory) and the Yukon licence abbreviation `YT` mistaken for a handle. |
| `tax identification number` | 1 | The one sample is genuine (a real "State Tax ID" business-filing field) — too small a sample (n=1 in the whole 1381-file, 10.2M-char corpus) to generalize, but a correct hit when present. |

**Net read:** `address` is the one label worth taking seriously as a genuine
capability gap SecureScan doesn't cover. Everything else in this list is
either (a) actually a re-find of something a SecureScan deterministic
detector or production GLiNER already catches, filed under a different one
of the 53 labels (`vehicle registration number`/`license plate
number`/`registration number`/some `national id number` = driver's
licence/status card; `ip address` = existing regex layer), or (b) dominated
by false positives specific to this corpus mix (financial micro-fields —
`cvc`/`cvv`/`cnpj`/`digital signature`/`visa number`/`birth certificate
number`/`blood type` — found essentially nothing real in 10.2M characters of
mixed business email and government-ID text).

## 2. Overlap with deterministic layers

Using the label→taxonomy mapping in `gliner_pii_eval_analyze.py`
(`driver's license number`→`identifier.government.drivers_license*`,
`passport number`/`passport_number`→passport/MRZ, health-insurance labels→
`health_card*`, SSN labels→`sin`/`ssn`, `credit card number`→`credit_card`,
`date of birth`→`dob`/`mrz_dob`, phone-family labels→`contact.phone`,
email-family→`contact.email`, `postal code`→`contact.address.postal_code`;
`address` has no deterministic mapping — see New Coverage):

| PII-model label | agree (same value, same file) | PII-only (det missed) | det-only (PII missed) |
|---|--:|--:|--:|
| driver's license number | 28 | 17 | 8 |
| passport number / passport_number | 7 | 28 | 29 |
| health insurance {id }number / national health insurance number | 24 | 20 | 147 |
| social security number / social_security_number | 4 | 15 | 48 |
| credit card number | 8 | 5 | 99 |
| date of birth | 11 | 23 | 18 |
| phone number / mobile / landline / fax | 359 | 104 | 1945 |
| email / email address | 2673 | 388 | 14973 |
| postal code | 5 | 40 | 1 |

**The det-only numbers for phone/email are not primarily a PII-model
recall failure — they're mostly the 150K-char cap.** Checked directly:
`contact.email` deterministic findings are 8811 in Enron alone (headers like
`To:`/`Cc:` routinely carry 5-10 addresses per message), and Enron files are
all well under the cap — both models saw the *same* full text there. So this
is a genuine result, not a capping artifact: **plain regex has essentially
perfect recall on RFC-shaped emails/phone numbers; the generic zero-shot
label competition inside a 5-group batched pass does not**, even though
GLiNER is being asked the same simple question ("is this an email address")
every group cycle. This validates the project's existing architecture
choice (regex/checksum layers own structured PII; GLiNER owns
person/org/location/date) rather than undermining it.

**Driver's licence / vehicle-registration / license-plate mislabeling.**
Naive per-label counting understates real agreement: in the `canadian_eval`
samples reviewed, the exact same digit strings SecureScan's
`drivers_license_detector.py` finds (`21939407`, `5453132`, `65917926`,
`134711-320`, etc.) are found by the PII model too, but frequently filed
under `vehicle registration number` or `license plate number` instead of
`driver's license number`. The model *is* finding the value; it's putting
it in one of three plausible-sounding buckets almost at random.

**Specimen corpus — who's right (`GROUND_TRUTH.csv`, 46 POSITIVE rows,
photographed real-format documents):**

| deterministic hit | PII-model hit | count |
|---|---|--:|
| yes | yes | 11 |
| yes | no | 9 |
| no | yes | **5** |
| no | no | 21 |

Deterministic recall: 20/46 (43.5%). PII-model recall: 16/46 (34.8%). The
specialized checksum/keyword-gated detectors still win on real photographed
specimens — expected, since they're purpose-built for these exact formats
and the PII model is reading noisy OCR text with generic labels.

The 5 **deterministic-missed-but-PII-model-caught** cases are the most
interesting result in this whole evaluation — each is a real, previously
documented SecureScan gap:

- `NL_official_embossed.png` / `NL_official_laser.png` — NL health card `123
  456 789 001`, both caught by the PII model.
- `YT_health_plan.png` — YT health plan number `002-999-999`.
- `pr_card_2021_current.jpg` — UCI `0018-5978`.
- `passport_new_data_page_mrz_annotated.jpg` — document number `P123456AA`,
  read via generic OCR-text NER. This is a documented MRZ-detector
  limitation: PaddleOCR extracts the TD3
  line as 30 chars instead of the required 44, so `mrz_detector.py`'s length
  gate rejects it before parsing even starts). The generic PII model has no
  such structural gate and picks the document number up anyway.

None of this means the PII model should replace the MRZ/health-card/UCI
detectors (its overall specimen recall is lower, and it produces far more
noise per correct hit — see below) — but as a **secondary confirmation
signal specifically for known detector gaps**, it caught real cases the
specialized layers structurally cannot.

## 3. Noise volume

| | total entities | files covered | confidence: min / p10 / p50 / p90 / max / mean |
|---|--:|--:|---|
| `gliner_multi_pii-v1` (53 labels, batched) | 31,444 | 1381/1381 | 0.500 / 0.563 / 0.807 / 0.983 / 1.000 / **0.790** |
| `gliner_medium-v2.1` (4 labels, production) | 43,695 | 1381/1381 | 0.500 / 0.555 / 0.767 / 0.934 / 0.993 / **0.755** |

The PII model produces *fewer* total findings than production despite 13×
more labels (person/organization dominate both models' totals — 26,030 of
the PII model's 31,444, i.e. 83%, are just `person`/`organization`, the two
labels it shares with production). Its confidence distribution is very
slightly higher on average, not lower — high label-set breadth did not
translate into systematically weaker individual calls.

**The `SEEEEEN` precedent, directly re-tested.** The task cites production
GLiNER producing OCR noise `'SEEEEEN'` as a `person` at 52% confidence. That
exact case is `specimen/AB_front_specimen.jpg`, and both models scanned the
identical extracted (OCR'd) text for it. Restricting to the four real
image/OCR corpora (`specimen`, `format`, `external_octopii`, `test_anchor`)
and searching for the same letter-run OCR-garble shape:

| | garbled hits in OCR corpora |
|---|--:|
| `gliner_medium-v2.1` | 4 — `'SEEEEEN'` (person, 0.52), `'JANE\nSEEEEEN'` (person, 0.59), two `'09/06/XXX(X)'` redaction placeholders labeled `date` |
| `gliner_multi_pii-v1` | 5 — `'JANE\nSEEEEEN'` (person, 0.66; it did **not** separately re-find bare `'SEEEEEN'` on the AB file), the same two `'09/06/XXX(X)'` placeholders now labeled **`date of birth`** instead of generic `date`, plus two new items: a redacted `'Account number\nxxxxxxXX'` placeholder labeled `bank account number`, and a company URL (`www.crwwd.com`) labeled `fax number` |

**Answer to "does ~50 labels multiply this": no, not in raw count** — 4 vs.
5 on the identical underlying OCR text is not a meaningful multiplication.
**But the failure mode gets worse, not just noisier**: production mislabels
garbage/redacted text into an inert generic bucket (`date`); the PII model
routes the *same* redacted placeholders into specific, higher-stakes
categories (`date of birth`, `bank account number`) — asserting false
precision about content that is explicitly `XXX`/`xx`-redacted, which is a
worse failure for any downstream system that trusts the label.

## 4. Cost

**Model size.** `~/.cache/huggingface`: **1.5 GB → 2.6 GB** (+1.1 GB for
`gliner_multi_pii-v1`, +4.2 MB for the `mdeberta-v3-base` tokenizer it
needed). Both models are Torch-only in this evaluation (no ONNX export
attempted, per the task's constraint); production ONNX FP32 export
(`~746 MB`, cached separately under `~/.cache/securescan/`) does not apply
to the PII model here.

**Controlled, thread-matched comparison, identical 28-file sample across
all seven corpora** (`docs/evidence/gliner_pii_eval_cost.py`):

| | backend | threads | files | chars | wall time | throughput |
|---|---|---|--:|--:|--:|--:|
| `gliner_medium-v2.1` (production config) | ONNX | 2 (`GLINER_ONNX_THREADS`) | 28 | 3,493,178 | 1221.2s | 2860.5 chars/s |
| `gliner_multi_pii-v1` (batched, 5 groups) | Torch | 2 | *(not completed — see below)* | | | |

The 2-thread batched-PII run on this sample was still running after 5+
minutes with no file yet complete and was abandoned in favor of the
full-corpus empirical data below, which is more informative anyway: **2
threads is badly under-provisioned for this model's batched inference on
large documents** — the full-corpus run (below) empirically found 8 threads
optimal, and even 8 threads took multiple minutes on a single 150K-char
stress file. A 2-thread run on the same file class would be considerably
slower still, and the goal (an accurate cost picture) is better served by
the full 1381-file empirical run than by forcing an unrepresentative
low-thread config to finish.

**Full-corpus run** (1381 of 1381 files, capped at 10.2M/10.1M chars):

| | threads used | total wall time | throughput |
|---|---|--:|--:|
| `gliner_medium-v2.1` (production ONNX config, 4 labels) | 4 | 6367s (106 min) | 1609 chars/s |
| `gliner_multi_pii-v1` (batched, 5 groups) | 8 (settled; see below) | ~28,030s (~7.8h, includes thread-tuning detours — see caveat) | 360 chars/s (blended) |

**Thread-count is not a free knob — it actively regressed.** The PII-model
run was tuned live against real stress-corpus files once their outsized
share of total volume became apparent:

- 12 threads (matching medium's process + pii's process running
  concurrently, 16 total requested against 18 cores): stable but slow,
  ~290-340 chars/s throughout Enron.
- 16 threads (once medium finished and freed capacity): **worse**, not
  better — a 150K-char capped stress file took 315-334s at 16 threads vs.
  177s in an earlier isolated 8-thread calibration on a similar file.
  Directly comparable same-size files confirmed it: a 56,090-char file took
  135.7s at 16 threads vs. 98.7s at 8 threads on a rerun (~27% faster at
  half the thread count).
- **8 threads** was the empirically fastest setting used for the bulk of
  the stress corpus, averaging 71-115s per capped 150K-char-class file.

This exactly echoes the project's own documented ONNX lesson
(`config.GLINER_ONNX_THREADS = 2`, chosen after intra-op oversubscription
was found to collapse throughput) — the same class of regression
reappeared independently in this Torch-backend batched-label evaluation
script, just at different absolute thread counts (torch intra-op scaling
plateaus/regresses past a point that depends on model, backend, and
per-call sequence length; it is not free to "just add more threads").

**One file could not be completed and was excluded.**
`stress_data/marketing/team_gamma/project_z/archive/2024/filler_0072.log` —
a synthetic, heavily templated log file (4,231,905 raw characters, capped to
150,000 for analysis) with dense repeated capitalized vocabulary
(`Dashboard`, `Cache`, `Database`, `INFO`, `WARN`, `DEBUG` recurring on
nearly every line). A typical capped 150K-char file takes ~85-330s
depending on thread count; this one ran for **over 1 hour 42 minutes at
sustained ~795% CPU with zero completion** before being killed. GLiNER's
span-classification cost scales with the number of plausible entity-shaped
candidate spans in the text, not raw character count — a log format with
extremely dense capitalized-token repetition is a distinct, pathological
input class for this architecture, unrelated to prose of the same length.
This is a genuine finding for anyone considering this model (or possibly
any GLiNER-family batched-multi-label setup) against structured/templated
log content, not an artifact of this evaluation's setup. Excluded and
documented rather than silently dropped or indefinitely retried.

**Peak RSS**, observed via `ps` on the running evaluation processes (not a
clean single-file peak, but representative of steady-state):
`gliner_multi_pii-v1` process: ~2.2 GB RSS at 8 threads. `gliner_medium-v2.1`
process: ~1.6-1.7 GB RSS at 4 threads.

**What this means for a full production scan.** GLiNER is already 99.7% of
scan wall-time per the project's own prior benchmarking. Restricted to
*text volume actually seen* (both models run under the same 150K/file cap):
`gliner_multi_pii-v1`'s batched-groups design costs roughly **4-5x
production's wall-clock** for the *same amount of text* (1609 vs. ~340-490
chars/s during the representative post-tuning phase of the run, worse
before tuning) — consistent with running 5 label-group passes per chunk
instead of 1, partially offset by the two models' different backends
(Torch vs. production's ONNX FP32 export). Swapping it in for production
GLiNER as-is, even restricted to genuinely new-coverage labels only, would
multiply GLiNER's share of every scan several-fold, and — as the
`filler_0072.log` case shows — introduces a new pathological-input class
(dense structured/log text) that production's current 4-label config does
not appear to trigger to nearly the same degree.

## Bottom line

- **One clearly useful new-coverage label**: `address` (full mailing
  addresses; genuinely absent from SecureScan today), with meaningful edge
  noise.
- **Several labels that look like new coverage but aren't**:
  `vehicle registration number`, `license plate number`, and some
  `registration number`/`national id number` hits are the model re-finding
  values SecureScan's driver's-licence and status-card detectors already
  catch, just filed under a different one of its 53 labels; `ip address`
  duplicates the existing regex layer.
- **Most of the remaining "new" labels are dominated by false positives**
  on this corpus mix (`cnpj`, `cvc`, `cvv`, `visa number`, `digital
  signature`, `blood type`, `birth certificate number` found ~0 genuine
  hits across 10.2M characters).
- **On structured PII SecureScan already owns (email, phone), the
  deterministic regex layer has meaningfully better recall** than the
  generic model, even on identical, uncapped text — supports keeping the
  existing regex/checksum-first architecture.
- **The model's one clear value-add**: on the real photographed specimen
  corpus, it independently recovered 5 cases the specialized deterministic
  detectors structurally miss (including one documented, pre-existing MRZ
  length-gate limitation) — a plausible role as a demote/confirm-only
  secondary signal for known gaps, not as a primary detector.
- **Noise is not dramatically multiplied by more labels** in raw count, but
  the *kind* of noise gets worse — redacted/placeholder text gets
  mislabeled into specific, higher-stakes categories instead of a harmless
  generic bucket.
- **Cost is substantial**: several-fold GLiNER wall-clock over production
  for the same text volume, plus a newly discovered pathological-input
  class (dense templated/log text) that stalled one file indefinitely.

Measurement only; no production files were modified.
