# PaddleOCR + MRZ A/B anchor re-derivation

Measured on 2026-08-05. This is a proposal only; no stored anchor was updated.

- Committed baseline: `dbfc43f3c65bf4f756c56a5249dfbabe92bff9da` exported to an isolated tree.
- Baseline OCR: committed Tesseract production path.
- Candidate: current production PaddleOCR with MRZ gates A and B enabled.
- Both runs: LLM verification OFF; normal production NER policy ON; same 211 files.
- Extraction failures: zero in both runs.
- Finding identity for ADDED/REMOVED is exact `(file, type, value, risk)`.
- CHANGED pairs are limited to plainly corresponding semantic spans; uncertain pairs remain separate exact rows.

## Proposed anchor numbers

| Anchor | Baseline findings | Paddle+A/B findings | Finding risks before → after | File risks before → after | PII files before → after |
|---|---:|---:|---|---|---:|
| `tests/stress_data` | 132 | 132 | H/M/L/U 16/7/109/0 → 16/7/109/0 | H/M/L/None/U 16/6/43/109/0 → 16/6/43/109/0 | 65 → 65 |
| `tests/format_data` | 75 | 74 | H/M/L/U 21/39/15/0 → 22/39/13/0 | H/M/L/None/U 15/0/3/0/0 → 15/0/2/1/0 | 18 → 17 |
| `tests/external_octopii` | 45 | 51 | H/M/L/U 2/8/35/0 → 1/6/44/0 | H/M/L/None/U 2/3/2/1/0 → 1/2/5/0/0 | 7 → 8 |
| external photographed-document anchor | 75 | 76 | H/M/L/U 4/6/65/0 → 2/9/65/0 | H/M/L/None/U 4/3/3/1/0 → 2/6/3/0/0 | 10 → 11 |

The committed run reproduced the stored totals exactly: stress 132, format 75, Octopii 45, and Test 75.

## Exhaustive finding deltas

No delta was caused by MRZ gate A or B: the committed Tesseract finding set contained no block that those gates newly reject. The Ukrainian MRZ additions below are OCR-derived; Paddle reconstructed the printed MRZ lines differently and the valid block passes both gates.

### `tests/stress_data`

Exact delta: **0 added / 0 removed / 0 changed**.

#### ADDED

None.

#### REMOVED

None.

#### CHANGED

None.

### `tests/format_data`

Exact delta: **1 added / 2 removed / 0 changed**.

#### ADDED

| File | Value | Type | Old risk | New risk | Cause |
|---|---|---|---|---|---|
| `pdf_scanned_2page.pdf` | `A1234-56789-01234` | `identifier.government.drivers_license_on` | — | HIGH | driver's licence format change |

#### REMOVED

| File | Value | Type | Old risk | New risk | Cause |
|---|---|---|---|---|---|
| `face_public_domain_astronaut.jpg` | `INGA` | `entity.organization` | LOW | — | Paddle reads text differently than Tesseract |
| `face_public_domain_astronaut.jpg` | `oS` | `entity.organization` | LOW | — | Paddle reads text differently than Tesseract |

#### CHANGED

None.

### `tests/external_octopii`

Exact delta: **27 added / 21 removed / 8 changed**.

#### ADDED

| File | Value | Type | Old risk | New risk | Cause |
|---|---|---|---|---|---|
| `dummy-PAN-India.jpg` | `LOOA` | `entity.person` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-PAN-India.jpg` | `RANN` | `entity.person` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-aadhaar.png` | `1967` | `entity.date` | — | LOW | Paddle reads text differently than Tesseract |
| `dummy-aadhaar.png` | `D.N.Singh Road` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-aadhaar.png` | `Hathibaug Mazgaon` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-aadhaar.png` | `Hendre Buldg No.17` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-aadhaar.png` | `Mumbai` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-aadhaar.png` | `Salarpuria Touchstone` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-aadhaar.png` | `Government of India` | `entity.organization` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-aadhaar.png` | `Deepak Vasant Surve` | `entity.person` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-aadhaar.png` | `Doepak Vasant Surve` | `entity.person` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-debit-card.jpg` | `5422` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-debit-card.jpg` | `BE` | `entity.location` | — | LOW | Paddle reads text differently than Tesseract |
| `dummy-debit-card.jpg` | `Pda` | `entity.organization` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-drivers-license-maharashtra.jpg` | `GOVANDI` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-passport-britain.jpg` | `20 SEP/SEP 06` | `entity.date` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-passport-britain.jpg` | `UNITED KINGDOM` | `entity.organization` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-passport-britain.jpg` | `BRITISH CITIZEN` | `entity.person` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-passport-britain.jpg` | `M` | `entity.person` | — | LOW | Paddle reads text differently than Tesseract |
| `dummy-passport-ukraine.jpg` | `AUG 19` | `entity.date` | — | LOW | Paddle reads text differently than Tesseract |
| `dummy-passport-ukraine.jpg` | `IBAH` | `entity.person` | — | LOW | Paddle reads text differently than Tesseract |
| `dummy-passport-ukraine.jpg` | `PPMLEHKO` | `entity.person` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-passport-ukraine.jpg` | `190803` | `identifier.government_low.mrz_expiry` | — | LOW | Paddle reads text differently than Tesseract |
| `dummy-passport-ukraine.jpg` | `EK000001` | `identifier.government_unverified.mrz` | — | MEDIUM | Paddle reads text differently than Tesseract |
| `dummy-passport-ukraine.jpg` | `830725` | `identifier.personal.mrz_dob` | — | MEDIUM | Paddle reads text differently than Tesseract |
| `dummy-ssn.jpg` | `USA` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `dummy-ssn.jpg` | `JOHN H. DOE` | `entity.person` | — | LOW | Paddle reads text Tesseract missed |

#### REMOVED

| File | Value | Type | Old risk | New risk | Cause |
|---|---|---|---|---|---|
| `dummy-aadhaar.png` | `IRATE TROT` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-aadhaar.png` | `Marathanadi Sajepur` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-aadhaar.png` | `Yeor of Bith` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-aadhaar.png` | `OSCAR` | `entity.person` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-aadhaar.png` | `2907 2881906701` | `identifier.financial_unverified.credit_card` | MEDIUM | — | Paddle reads text differently than Tesseract |
| `dummy-aadhaar.png` | `1145002075` | `identifier.government_unverified.health_card_on` | MEDIUM | — | Paddle reads text differently than Tesseract |
| `dummy-aadhaar.png` | `2881906701` | `identifier.government_unverified.health_card_on` | MEDIUM | — | Paddle reads text differently than Tesseract |
| `dummy-debit-card.jpg` | `m2 40/13` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-debit-card.jpg` | `wee ts` | `entity.organization` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-debit-card.jpg` | `BIJAY BEHERA` | `entity.person` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-drivers-license-maharashtra.jpg` | `KHAN RAMAN NAGAR` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-hong-kong-resident-id.png` | `Wing Ching` | `entity.person` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-passport-britain.jpg` | `6 JAN/JAN 55` | `entity.date` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-passport-britain.jpg` | `ENFIELD` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-passport-britain.jpg` | `023477812` | `identifier.government.passport_generic` | HIGH | — | Paddle reads text differently than Tesseract |
| `dummy-passport-ukraine.jpg` | `KKK` | `entity.organization` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-passport-ukraine.jpg` | `Dara` | `entity.person` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-passport-ukraine.jpg` | `GRYTSENKO` | `entity.person` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-passport-ukraine.jpg` | `MUKOSIAIB` | `entity.person` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-passport-ukraine.jpg` | `Sammie ina` | `entity.person` | LOW | — | Paddle reads text differently than Tesseract |
| `dummy-passport-ukraine.jpg` | `TPYLIEHKO` | `entity.person` | LOW | — | Paddle reads text differently than Tesseract |

#### CHANGED

| File | Old value → new value | Old type → new type | Old risk → new risk | Cause |
|---|---|---|---|---|
| `dummy-PAN-India.jpg` | `De Eos` → `ICO` | `entity.organization` → `entity.organization` | LOW → LOW | Paddle reads text differently than Tesseract |
| `dummy-aadhaar.png` | `heip@uidal.gov.in` → `help@uidai.gov.in` | `contact.email` → `contact.email` | MEDIUM → MEDIUM | Paddle reads text differently than Tesseract |
| `dummy-aadhaar.png` | `Bengalura - 560.001` → `Bengaluru` | `entity.location` → `entity.location` | LOW → LOW | Paddle reads text differently than Tesseract |
| `dummy-aadhaar.png` | `HRONGE Outer Ring Road` → `Outer Ring Road` | `entity.location` → `entity.location` | LOW → LOW | Paddle reads text differently than Tesseract |
| `dummy-debit-card.jpg` | `wee 11/22` → `11/22` | `entity.date` → `entity.date` | LOW → LOW | Paddle reads text differently than Tesseract |
| `dummy-drivers-license-maharashtra.jpg` | `BABU ER` → `BABUKHAN` | `entity.location` → `entity.location` | LOW → LOW | Paddle reads text differently than Tesseract |
| `dummy-hong-kong-resident-id.png` | `03-06-1985` → `03-06-1985` | `identifier.personal.dob` → `entity.date` | MEDIUM → LOW | Paddle reads text differently than Tesseract |
| `dummy-passport-ukraine.jpg` | `UKRGRY TSENKO` → `UKRGRYTSENKO` | `entity.person` → `entity.person` | LOW → LOW | Paddle reads text differently than Tesseract |

### External photographed-document anchor

Exact delta: **27 added / 26 removed / 5 changed**.

#### ADDED

| File | Value | Type | Old risk | New risk | Cause |
|---|---|---|---|---|---|
| `specimen_sin_01.jpg` | `Sampletown AB` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_sin_01.jpg` | `PO Box 000` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_sin_01.jpg` | `Sample` | `entity.person` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_licence_02.jpg` | `AbertaDRIVER` | `entity.organization` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_licence_02.jpg` | `123456-789` | `identifier.financial_unverified.sin` | — | MEDIUM | Paddle reads text differently than Tesseract |
| `specimen_licence_01.jpg` | `Samplewood Pk` | `entity.location` | — | LOW | Paddle reads text differently than Tesseract |
| `specimen_licence_01.jpg` | `123456-789` | `identifier.financial_unverified.sin` | — | MEDIUM | Paddle reads text Tesseract missed |
| `specimen_pr_card_01.jpg` | `Place of Landing` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_pr_card_01.jpg` | `Taile` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_pr_card_01.jpg` | `Sample` | `entity.person` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_benefits_02.jpg` | `06-Feb-2030` | `entity.date` | — | LOW | Paddle reads text differently than Tesseract |
| `specimen_benefits_02.jpg` | `6-Feb-30` | `entity.date` | — | LOW | Paddle reads text differently than Tesseract |
| `specimen_benefits_02.jpg` | `Sample Dental` | `entity.organization` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_licence_03.jpg` | `FEB 1990` | `entity.date` | — | LOW | Paddle reads text differently than Tesseract |
| `specimen_licence_03.jpg` | `FEB 90` | `entity.date` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_licence_03.jpg` | `Alberta` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_licence_03.jpg` | `654321-987` | `identifier.financial_unverified.sin` | — | MEDIUM | Paddle reads text Tesseract missed |
| `specimen_pr_card_02.jpg` | `Nam` | `entity.location` | — | LOW | Paddle reads text differently than Tesseract |
| `specimen_pr_card_02.jpg` | `Jordan` | `entity.person` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_pr_card_02.jpg` | `Example` | `entity.person` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_pr_card_02.jpg` | `Sample` | `entity.person` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_passport_01.jpg` | `PASAS` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_passport_01.jpg` | `Place d bith` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_passport_01.jpg` | `Rodas` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `PassportrandompagenoPIInoface.jpg` | `OESSUS` | `entity.location` | — | LOW | Paddle reads text Tesseract missed |
| `specimen_benefits_03.jpg` | `Canadian Dental Care Plan` | `entity.organization` | — | LOW | Paddle reads text differently than Tesseract |
| `specimen_benefits_03.jpg` | `http://www.sunlife.ca/CDCP` | `technical.url` | — | LOW | Paddle reads text Tesseract missed |

#### REMOVED

| File | Value | Type | Old risk | New risk | Cause |
|---|---|---|---|---|---|
| `specimen_sin_01.jpg` | `RNS` | `entity.organization` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_licence_02.jpg` | `FEB 2024` | `entity.date` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_licence_02.jpg` | `SC5` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_licence_02.jpg` | `1234567` | `identifier.government.drivers_license_ca` | HIGH | — | Paddle reads text differently than Tesseract |
| `specimen_licence_01.jpg` | `Jordan` | `entity.person` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_licence_01.jpg` | `123456` | `identifier.government.drivers_license_ca` | HIGH | — | driver's licence format change |
| `specimen_pr_card_01.jpg` | `LTU` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_pr_card_01.jpg` | `OTTAWA` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_pr_card_01.jpg` | `PDN` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_pr_card_01.jpg` | `UL` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_benefits_02.jpg` | `200 Example Ave SW` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_licence_03.jpg` | `Samplewood Pk` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_licence_03.jpg` | `Government Gouvernement` | `entity.organization` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_licence_03.jpg` | `Taylor Ann` | `entity.person` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_pr_card_02.jpg` | `01 FEB` | `entity.date` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_pr_card_02.jpg` | `04 FEB` | `entity.date` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_pr_card_02.jpg` | `Canada` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_pr_card_02.jpg` | `FEVR` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_pr_card_02.jpg` | `Government` | `entity.organization` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_pr_card_02.jpg` | `EXAMPLE` | `entity.person` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_passport_01.jpg` | `KKK` | `entity.organization` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_passport_01.jpg` | `EXAMPLE` | `entity.person` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_passport_01.jpg` | `EXAMPLE` | `entity.person` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_benefits_03.jpg` | `Sampletown` | `entity.location` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_benefits_03.jpg` | `Sample Dental` | `entity.organization` | LOW | — | Paddle reads text differently than Tesseract |
| `specimen_benefits_03.jpg` | `Sample Person` | `entity.person` | LOW | — | Paddle reads text differently than Tesseract |

#### CHANGED

| File | Old value → new value | Old type → new type | Old risk → new risk | Cause |
|---|---|---|---|---|
| `specimen_licence_01.jpg` | `rp01 FEB 2026` → `JUL 2026` | `entity.date` → `entity.date` | LOW → LOW | Paddle reads text differently than Tesseract |
| `specimen_licence_01.jpg` | `vw09 FEB 1990` → `FEB 1990` | `entity.date` → `entity.date` | LOW → LOW | Paddle reads text differently than Tesseract |
| `specimen_pr_card_01.jpg` | `EXAMPLE` → `EXAMPLE` | `entity.person` → `entity.person` | LOW → LOW | Paddle reads text differently than Tesseract |
| `specimen_benefits_01.jpg` | `Sample` → `Sample` | `entity.person` → `entity.person` | LOW → LOW | Paddle reads text differently than Tesseract |
| `specimen_benefits_03.jpg` | `Jordan Example` → `Jordan Example` | `entity.person` → `entity.person` | LOW → LOW | Paddle reads text differently than Tesseract |

## Attribution audit

- MRZ gate A/B rejections: **0 finding deltas** against the committed anchor sets.
- Driver's-licence-format deltas: the new Ontario finding in `pdf_scanned_2page.pdf`, and removal of the `123456` fragment in `specimen_licence_01.jpg`.
- All remaining rows are tied to captured OCR-text differences. “Missed” is used only when the new finding value is absent from the committed Tesseract text; otherwise the row says Paddle read the text differently.
- **UNEXPLAINED: 0.** No text-corpus finding moved, and every non-driver delta occurs in an OCR-backed file whose captured extracted text differs between the two production runs.

These values are not ratified and were not written into any stored anchor.

## SIN grouping correction remeasurement

Measured on 2026-08-05 after replacing the independently optional SIN
separators with three explicit accepted layouts: `#########`, `###-###-###`,
and `### ### ###`. This comparison uses the immediately preceding PaddleOCR +
MRZ A/B scan above as its baseline. Both scans used LLM verification OFF and
the normal production NER policy ON.

### Resulting anchor totals

| Anchor | Before findings | After findings | Finding risks before → after | File risks before → after | PII files before → after |
|---|---:|---:|---|---|---:|
| `tests/stress_data` | 132 | 132 | H/M/L/U 16/7/109/0 → 16/7/109/0 | H/M/L/None/U 16/6/43/109/0 → 16/6/43/109/0 | 65 → 65 |
| `tests/format_data` | 74 | 74 | H/M/L/U 22/39/13/0 → 22/39/13/0 | H/M/L/None/U 15/0/2/1/0 → 15/0/2/1/0 | 17 → 17 |
| `tests/external_octopii` | 51 | 51 | H/M/L/U 1/6/44/0 → 1/6/44/0 | H/M/L/None/U 1/2/5/0/0 → 1/2/5/0/0 | 8 → 8 |
| external photographed-document anchor | 76 | 75 | H/M/L/U 2/9/65/0 → 4/6/65/0 | H/M/L/None/U 2/6/3/0/0 → 4/3/4/0/0 | 11 → 11 |

The production-path rescan covered the same 211 files and had zero extraction
failures. No stored anchor value was changed.

### Exhaustive finding delta from the SIN fix

#### `tests/stress_data`

Exact delta: **0 added / 0 removed / 0 changed**.

#### `tests/format_data`

Exact delta: **0 added / 0 removed / 0 changed**.

#### `tests/external_octopii`

Exact delta: **0 added / 0 removed / 0 changed**.

#### External photographed-document anchor

Exact delta: **0 added / 1 removed / 2 changed**.

##### REMOVED

| File | Value | Type | Old risk | New risk | Cause |
|---|---|---|---|---|---|
| `specimen_licence_02.jpg` | `123456-789` | `identifier.financial_unverified.sin` | MEDIUM | — | SIN grammar corrected: the invalid 6-3 grouping is rejected; this file emits no replacement licence finding. |

##### CHANGED

| File | Old value → new value | Old type → new type | Old risk → new risk | Cause |
|---|---|---|---|---|
| `specimen_licence_01.jpg` | `123456-789` → `123456-789` | `identifier.financial_unverified.sin` → `identifier.government.drivers_license_ab` | MEDIUM → HIGH | SIN grammar corrected: the invalid 6-3 SIN no longer outranks the Alberta licence finding during reconciliation. |
| `specimen_licence_03.jpg` | `654321-987` → `654321-987` | `identifier.financial_unverified.sin` → `identifier.government.drivers_license_ab` | MEDIUM → HIGH | SIN grammar corrected: the invalid 6-3 SIN no longer outranks the Alberta licence finding during reconciliation. |

There are no added findings and no unexplained deltas.

### Regression and harness results

- All 27 established standalone suites passed.
- Focused financial-identifier coverage passed 8/8, including the photographed
  Alberta value `123456-789`, both other invalid groupings, mixed separators,
  and all three valid SIN layouts.
- Canadian harness: **87/87 expectation conformance**, 84 OK agreements, zero
  regressions, three predicted gaps, and four unscored territory cases. No
  delta from the preceding result.
- Specimen harness: passes **37 → 41**; detector misses **16 → 16**;
  extraction-limited **11 → 11**; value mismatches **1 → 1**; category
  mismatches **4 → 4**; negative false positives **11 → 7**; unscored **1 →
  1**; OCR viability **35/46 → 35/46**. The four additional passes are
  negative controls whose invalidly grouped SIN findings disappeared; the
  positive-row split did not change. The harness still reports one previously
  passing MRZ-row regression, outside the SIN path.

## Known losses

`dummy-passport-britain.jpg` prints British passport number `023477812`.
Tesseract found it; PaddleOCR did not. This is a plain PaddleOCR loss and was
not investigated further in this round.
