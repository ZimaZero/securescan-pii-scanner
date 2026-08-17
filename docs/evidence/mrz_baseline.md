# MRZ block-admission baseline

Measured before any gate implementation against the current Paddle extraction
path and current detectors. Run date: 2026-08-04. The scan covered 1,292 files:
stress 174, format 18, Test 11, external_octopii 8, specimen 81, and the canonical
seeded Enron sample 1,000. All files extracted successfully. NER and LLM
verification were disabled because this inventory concerns the deterministic
MRZ layer only.

The specimen paths were resolved with the same legacy-prefix resolver used by
`tests/run_specimen_eval.py`: CSV paths beginning `Negative_Control_Documents/`
map to the mounted `Negative_Control_Document_Samples/` directory.

## Measured truth split

| Scope | Findings | True positive | False positive |
|---|---:|---:|---:|
| All MRZ tiers | 44 | 12 | 32 |
| `identifier.government_unverified.mrz` only | 31 | 1 | 30 |
| Validated MRZ fields | 13 | 11 | 2 |

The inherited “11 false of 14” figure was not a measurement of the requested
corpus set. It omitted Enron and also omitted a checksum-valid prose collision.
The only true `mrz_unverified` finding is `EK000001` in the Ukrainian passport;
its DOB and expiry validate and the source is visibly an MRZ. True validated
findings are the three fields on each of the Canadian passport specimen,
Test PR card, and Test passport, plus the Ukrainian DOB and expiry. The two
validated false positives are Enron `10NMEM` as an MRZ DOB and utility-bill
`NTHISCOUP` as an MRZ document number.

`source line` is the raw extracted physical line used for the emitted field.
`⏎` marks two physical OCR lines joined by the detector's line-wrap repair.

## Complete finding inventory

| Truth | Corpus / file | Taxonomy | Value | Format | State | Type | Source line |
|---|---|---|---|---|---|---|---|
| FP | Enron `allen-p__deleted_items__428.txt` | `identifier.government_unverified.mrz` | `RRUCTINGA` | TD1 | `NST` | `CO` | `Constrructing a cross commodity swap` |
| FP | Enron `giron-d__inbox__444.txt` | `identifier.government_unverified.mrz` | `ANDRETAIN` | TD3 | `ASE` | `LE` | `AND RETAIN YOUR SOUTHWEST AIRLINES PASSENGER RECEIPT` |
| FP | Enron `hayslett-r__projects__hold__25.txt` | `identifier.government_unverified.mrz` | `ROGERSHER` | TD2 | `DHA` | `RO` | `Rogers Herndon  Michael Mann  Emilio Vicens` |
| FP | same | `identifier.government_unverified.mrz` | `JANETDIET` | TD2 | `ELL` | `SH` | `Janet Dietrich  Richard Lewis  Jeff Shankman` |
| FP | same | `identifier.government_unverified.mrz` | `LDENBENGL` | TD1 | `MBE` | `TI` | `Tim Belden  Ben Glisan  Danny McCarty` |
| FP | same | `identifier.government_unverified.mrz` | `GALLAGHER` | TD1 | `VID` | `DA` | `David Gallagher  Roy Poyntz  Ron Slimp` |
| FP | same | `identifier.government_unverified.mrz` | `BECKTROYH` | TD1 | `LLY` | `SA` | `Sally Beck  Troy Henry  Kevin Presto` |
| FP | same | `identifier.government_unverified.mrz` | `FFNERSCOT` | TD1 | `EDE` | `JO` | `Joe Deffner  Scott Neal  Jim Steffes` |
| FP | Enron `kaminski-v__all_documents__9228.txt` | `identifier.government_unverified.mrz` | `BROUGHTTO` | TD1 | `WAS` | `IT` | `It was brought to my attention that you` |
| FP | Enron `kaminski-v__sent__4004.txt` | `identifier.government_unverified.mrz` | `SUNDAYMAY` | TD1 | `MON` | `7P` | `7 pm on Sunday May 30 for the speakers` |
| FP | Enron `king-j__deleted_items__64.txt` | `identifier.government_unverified.mrz` | `2030INAPL` | TD3 | `LON` | `BE` | `2030 in a plane crash and the fund has been dormant in` |
| FP | same | `identifier.government_unverified.mrz` | `FUNDINOUR` | TD3 | `SAC` | `HI` | `fund in our custody either from his family or relation` |
| FP | same | `identifier.government_unverified.mrz` | `ANDIDEABE` | TD3 | `THI` | `WI` | `and idea be Profitable and successful during the time` |
| FP | same | `identifier.government_unverified.mrz` | `FOREIGNAC` | TD3 | `TTH` | `GE` | `foreign account has been put in place and directives` |
| FP | same | `identifier.government_unverified.mrz` | `EVENTUALL` | TD3 | `ENO` | `AR` | `eventually raise an eye brow on my side during the ⏎ time` |
| FP | same | `identifier.government_unverified.mrz` | `PARTYORFE` | TD3 | `ISI` | `TH` | `party or fellow who will forward claims as the next of` |
| FP | same | `identifier.government_unverified.mrz` | `CORRESPON` | TD3 | `YBE` | `MA` | `correspondent branch of the bank where the whole money` |
| FP | Enron `saibi-e__inbox__800.txt` | `identifier.government_unverified.mrz` | `ECANRICEG` | TD1 | `LDP` | `WI` | `Wild Pecan Rice ⏎ Green Beans Almondine` |
| FP | Enron `shackleton-s__notes_inbox__2585.txt` | `identifier.government_unverified.mrz` | `ALLNYMEXD` | TD2 | `LCO` | `AL` | `ALL NYMEX DIVISION MEMBERS AND MEMBER FIRMS` |
| FP | same | `identifier.personal.mrz_dob` | `10NMEM` | TD2 | `LCO` | `AL` | `ALL NYMEX DIVISION MEMBERS AND MEMBER FIRMS` |
| TP | Octopii `dummy-passport-ukraine.jpg` | `identifier.government_unverified.mrz` | `EK000001` | TD3 | `UKR` | `P` | `EK000001<7UKR8307255M19080333052125257<<<<00` |
| TP | same | `identifier.personal.mrz_dob` | `830725` | TD3 | `UKR` | `P` | same MRZ line |
| TP | same | `identifier.government_low.mrz_expiry` | `190803` | TD3 | `UKR` | `P` | same MRZ line |
| FP | specimen `AB_front_specimen.jpg` | `identifier.government_unverified.mrz` | `LACESTREE` | TD1 | `MYP` | `24` | `24 My Place Street ⏎ Anywhere AB T5J 2M6` |
| FP | specimen `NU_back_sample.jpg` | `identifier.government_unverified.mrz` | `CECLASSES` | TD1 | `CEN` | `LI` | `Licence classes ⏎ A Adequate lenses` |
| TP | specimen `Canada_passport-data-page-large_2023.jpeg` | `identifier.government.mrz_document_number` | `P123456AA` | TD3 | `CAN` | `PP` | `P123456AA0CAN9008010F3301144<<<<<<<<<<<<<<06` |
| TP | same | `identifier.personal.mrz_dob` | `900801` | TD3 | `CAN` | `PP` | same MRZ line |
| TP | same | `identifier.government_low.mrz_expiry` | `330114` | TD3 | `CAN` | `PP` | same MRZ line |
| FP | specimen `scis_certificate.png` | `identifier.government_unverified.mrz` | `AFFAIRESI` | TD3 | `LIN` | `ON` | `Affaires indiennes ⏎ CERTIFICATE OF INDIAN STATUS` |
| FP | same | `identifier.government_unverified.mrz` | `CERTIFICA` | TD2 | `DIA` | `IN` | `CERTIFICATE OF INDIAN STATUS ⏎ Affairs Canada` |
| FP | specimen `scis_certificate_sample_no_numbers.jpg` | `identifier.government_unverified.mrz` | `CERTIFICA` | TD2 | `DIA` | `IN` | `CERTIFICATE OF INDIAN STATUS ⏎ Affairs Canada` |
| FP | specimen `Study_permit_notobfuscated.jpg` | `identifier.government_unverified.mrz` | `RYOFBLIRT` | TD1 | `UNT` | `CO` | `Country of BlirtuPays do naissanca` |
| FP | specimen `eob_mtcounties_01.png` | `identifier.government_unverified.mrz` | `OBTAINED2` | TD3 | `ATU` | `ST` | `obtained 24 hours a day by accessing our website at` |
| FP | specimen `invoice_canadapost_01.png` | `identifier.government_unverified.mrz` | `EBUSINESS` | TD1 | `MPL` | `SA` | `SAMPLE BUSINESS NM ⏎ 2701 RIVERSIDE DR` |
| FP | specimen `paystub_cfpb_01.png` | `identifier.government_unverified.mrz` | `READAPAYC` | TD1 | `WTO` | `HO` | `How to read a paycheck ⏎ BIG BOX STORE` |
| FP | specimen `utility_dc_01.png` | `identifier.government.mrz_document_number` | `NTHISCOUP` | TD1 | `TUR` | `RE` | `Return this coupon with your payment` |
| FP | Test `specimen_licence_02.jpg` | `identifier.government_unverified.mrz` | `SAMPLEPK` | TD1 | `SAM` | `I6` | `16 Samplewood Pk SW ⏎ Sampletown AB T0A 0A0` |
| FP | Test `specimen_licence_01.jpg` | `identifier.government_unverified.mrz` | `SAMPLEPK` | TD1 | `SAM` | `I6` | same source line |
| TP | Test `specimen_pr_card_01.jpg` | `identifier.government.mrz_document_number` | `PD0001234` | TD1 | `CAN` | `CA` | `CACANPD00012347<90290562<<<<<5` |
| TP | same | `identifier.personal.mrz_dob` | `900201` | TD1 | `CAN` | `CA` | `9002011M3002018LTU<210714<01<2` |
| TP | same | `identifier.government_low.mrz_expiry` | `300201` | TD1 | `CAN` | `CA` | same MRZ line |
| TP | Test `specimen_passport_01.jpg` | `identifier.government.mrz_document_number` | `12345678` | TD3 | `LTU` | `P` | `12345678<1LTU9002011M300401939002011053<<<52` |
| TP | same | `identifier.personal.mrz_dob` | `900201` | TD3 | `LTU` | `P` | same MRZ line |
| TP | same | `identifier.government_low.mrz_expiry` | `300401` | TD3 | `LTU` | `P` | same MRZ line |

## Corpus totals

| Corpus | TP | FP | Total |
|---|---:|---:|---:|
| stress | 0 | 0 | 0 |
| format | 0 | 0 | 0 |
| Test | 6 | 2 | 8 |
| external_octopii | 3 | 0 | 3 |
| specimen | 3 | 11 | 14 |
| Enron | 0 | 20 | 20 |
| **Total** | **12** | **32** | **44** |

## Independent gate measurement

All arms replayed the identical freshly extracted text captured for the
baseline. No arm added a finding.

| Arm | Removed | Retained | True positives lost |
|---|---:|---:|---:|
| Baseline, all flags off | 0 | 44 | 0 |
| A — require chevron | 32 | 12 | 0 |
| B — valid issuing state | 31 | 13 | 0 |
| C — valid document type | 23 | 21 | 0 |
| D — corroborate unverified with DOB/expiry | 29 | 15 | 0 |
| Recommended A+B+D | 32 | 12 | 0 |

### Every changed finding

The lists below name every removed finding. “Same as” references are exact set
relationships, not summaries that omit findings.

**Gate A (32):**

- Enron `allen-p__deleted_items__428.txt`: `RRUCTINGA` (unverified).
- Enron `giron-d__inbox__444.txt`: `ANDRETAIN` (unverified).
- Enron `hayslett-r__projects__hold__25.txt`: `ROGERSHER`, `JANETDIET`,
  `LDENBENGL`, `GALLAGHER`, `BECKTROYH`, `FFNERSCOT` (unverified).
- Enron `kaminski-v__all_documents__9228.txt`: `BROUGHTTO` (unverified).
- Enron `kaminski-v__sent__4004.txt`: `SUNDAYMAY` (unverified).
- Enron `king-j__deleted_items__64.txt`: `2030INAPL`, `FUNDINOUR`,
  `ANDIDEABE`, `FOREIGNAC`, `EVENTUALL`, `PARTYORFE`, `CORRESPON`
  (unverified).
- Enron `saibi-e__inbox__800.txt`: `ECANRICEG` (unverified).
- Enron `shackleton-s__notes_inbox__2585.txt`: `ALLNYMEXD` (unverified) and
  `10NMEM` (validated DOB false positive).
- Specimen `AB_front_specimen.jpg`: `LACESTREE` (unverified).
- Specimen `NU_back_sample.jpg`: `CECLASSES` (unverified).
- Specimen `scis_certificate.png`: `AFFAIRESI`, `CERTIFICA` (unverified).
- Specimen `scis_certificate_sample_no_numbers.jpg`: `CERTIFICA`
  (unverified).
- Specimen `Study_permit_notobfuscated.jpg`: `RYOFBLIRT` (unverified).
- Specimen `eob_mtcounties_01.png`: `OBTAINED2` (unverified).
- Specimen `invoice_canadapost_01.png`: `EBUSINESS` (unverified).
- Specimen `paystub_cfpb_01.png`: `READAPAYC` (unverified).
- Specimen `utility_dc_01.png`: `NTHISCOUP` (checksum-valid document-number
  false positive).
- Test `specimen_licence_02.jpg`: `SAMPLEPK` (unverified).
- Test `specimen_licence_01.jpg`: `SAMPLEPK` (unverified).

**Gate B (31):** exactly Gate A's list except specimen
`utility_dc_01.png` / `NTHISCOUP`, whose accidental issuing state `TUR` is a
valid ISO code and therefore survives B.

**Gate C (23):**

- Enron `giron-d__inbox__444.txt`: `ANDRETAIN`.
- Enron `hayslett-r__projects__hold__25.txt`: `ROGERSHER`, `JANETDIET`,
  `LDENBENGL`, `GALLAGHER`, `BECKTROYH`, `FFNERSCOT`.
- Enron `kaminski-v__sent__4004.txt`: `SUNDAYMAY`.
- Enron `king-j__deleted_items__64.txt`: `2030INAPL`, `FUNDINOUR`,
  `ANDIDEABE`, `FOREIGNAC`, `EVENTUALL`, `PARTYORFE`, `CORRESPON`.
- Enron `saibi-e__inbox__800.txt`: `ECANRICEG`.
- Specimen `AB_front_specimen.jpg`: `LACESTREE`.
- Specimen `NU_back_sample.jpg`: `CECLASSES`.
- Specimen `scis_certificate.png`: `AFFAIRESI`.
- Specimen `eob_mtcounties_01.png`: `OBTAINED2`.
- Specimen `invoice_canadapost_01.png`: `EBUSINESS`.
- Specimen `paystub_cfpb_01.png`: `READAPAYC`.
- Specimen `utility_dc_01.png`: `NTHISCOUP`.

**Gate D (29):** exactly Gate B's list except Enron
`shackleton-s__notes_inbox__2585.txt` / `ALLNYMEXD` and `10NMEM`. A bogus DOB
check happens to validate in that prose block, so D retains both the false DOB
and its “corroborated” unverified document value. D also leaves the validated
`NTHISCOUP` false positive untouched by design.

**A+B+D (32):** exactly Gate A's complete list. It retains only the 12 true
MRZ findings and loses no measured true positive.

## Regression run under A+B+D

The three recommended flags were temporarily enabled for these commands and
then returned to OFF. No expectation or gate was weakened after the result.

- All 27 standalone suites: **green**. The expanded MRZ suite is 42/42,
  including TD2 cases because A/B/C change TD2 admission.
- Canadian harness: **87/87**, zero regressions, unchanged from baseline
  (84 OK agreements, 3 predicted gaps, 4 unverified).
- Specimen harness: exit 1, **one previously passing regression**.

| Specimen metric | Before | A+B+D run | Delta |
|---|---:|---:|---:|
| Passes (positive and negative rows combined) | 37 | 41 | +4 |
| Detector misses | 16 | 16 | 0 |
| Extraction-limited | 11 | 11 | 0 |
| Value mismatches | 1 | 1 | 0 |
| Category mismatches | 4 | 4 | 0 |
| False-positive negative rows | 11 | 7 | -4 |
| Unscored | 1 | 1 | 0 |
| OCR viability | 35/46 | 35/46 | 0 |
| Previously passing regressions | 0 | 1 | +1 |

The four negative rows repaired are the NU card back (`CECLASSES`), EOB
(`OBTAINED2`), paystub (`READAPAYC`), and DC utility bill (`NTHISCOUP`). The
Canada Post invoice remains a false-positive row because its separate SIN
finding survives after `EBUSINESS` is removed.

The regression is
`Canadian_ID_Specimens/Canadian_Passport/passport_new_data_page_mrz_annotated.jpg`
for expected `P123456AA`, reported as DETECTOR-MISS. A fixed-text isolation run
shows this is not caused by A, B, or D: that extraction produced the MRZ as
three physical fragments (`PPCANMARTIN<<SARAH<<<<<<<<`, `1`,
`P123456AA0CAN9008010F3301144<<`, `<<06`), and `detect_mrz()` returned empty
with all flags OFF as well as under A, B, D, and A+B+D. This is extraction-run
variability/line fragmentation relative to the ratified prior PASS, not a gate
delta on identical text. It is nevertheless reported as the harness
regression, exactly as required; no compensating detector or OCR change was
made.

## Owner decision

Ratified after measurement:

- **Gate A — shipped:** require at least one `<` in the candidate block. It
  removed all 32 measured false positives and lost none of 12 true positives.
- **Gate B — shipped:** require a recognized ISO/ICAO issuing-state code. It
  independently removed 31 false positives and lost no true positive. It is
  retained with A as a second structural check even though A subsumed its
  measured removals.
- **Gate C — rejected:** it had the weakest separation, removing 23 false
  positives. Its document-type prefix is trivially faked by ordinary prose.
- **Gate D — rejected:** it removed nothing Gate A did not already remove and
  retained Enron `ALLNYMEXD` plus the bogus `10NMEM` DOB. A random DOB check
  digit passes approximately one time in ten, so that validation is not sound
  corroboration by itself.

Production defaults are therefore A=True, B=True, C=False, D=False.

Ratification verification with those defaults: all 27 standalone suites green;
MRZ 42/42; Canadian 87/87 with zero regressions; specimen 41 passes, 16
detector misses, 11 extraction-limited, 1 value mismatch, 4 category
mismatches, 7 negative-control false-positive rows, 1 unscored, OCR viability
35/46, and 1 previously passing regression. The regression remains the
OCR-fragmented annotated passport documented above and is reproducible with
all gates off on that extracted text.
