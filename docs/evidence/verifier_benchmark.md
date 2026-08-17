# LLM verifier benchmark against ground truth

Measured 2026-08-05 at commit `3426424` with the production verifier model
`qwen2.5:3b`, production prompt, routing, timeout, thread count, and thresholds
unchanged. This is measurement evidence only. No verifier default, prompt,
threshold, detector, reconciliation rule, or score was changed.

## Scope and method

- Real-document corpus: all 27 files under
  the external showcase corpus (23 image files and four source
  PDFs).
- Typed corpus: the requested historical 88-file Canadian set. The current
  working tree contains 91 files; Git history shows that the original 88 is
  the current set excluding `ab_licence_display_01.txt`,
  `mb_licence_display_01.txt`, and `yk_licence_corroborating_02.txt`.
- Detection ran through the current production path with semantic NER enabled
  and verification disabled during the detection pass. Each resulting finding
  for which `detectors.llm_verifier.is_routable()` returned true was then sent
  through the unchanged sequential production verifier.
- The production finding annotation retains a reason only for
  `FALSE_POSITIVE`; it discards the model's reason for `LEGITIMATE`. A second
  verifier-only pass over the exact saved payloads captured all raw reasons.
  All 42 decisions matched the first pass (zero decision drift).
- “Photographed image” below means a finding extracted from one of the 23
  `.jpg`/`.png`/`.webp` showcase files. “Typed text” means a finding from the
  88-file Canadian `.txt`/`.md` corpus. The two routed source-PDF findings are
  reported separately because the four PDFs are neither photographs nor part
  of the typed Canadian harness; one source PDF also required OCR on two pages.
- `OWNER GROUND TRUTH` is deliberately empty for owner adjudication. Until it
  is filled, verifier decisions are observations, not correctness labels.

## Measurement summary

| Input arm | Files | Files with routed findings | Routed findings | LEGITIMATE | FALSE_POSITIVE (demoted) | Demotion rate |
|---|---:|---:|---:|---:|---:|---:|
| Photographed images | 23 | 14 | 20 | 8 | 12 | 60.0% |
| Canadian typed corpus | 88 | 20 | 20 | 17 | 3 | 15.0% |
| Showcase source PDFs | 4 | 2 | 2 | 0 | 2 | 100.0% |
| **Total** | **115** | **36** | **42** | **25** | **17** | **40.5%** |

The complete detection-plus-verification measurement took 745.66 seconds.
The production verification pass itself took 412.67 seconds: 223.89 seconds
for photographed-image findings, 159.92 seconds for Canadian typed-text
findings, and 28.86 seconds for source-PDF findings. The separate raw-reason
capture took 380.38 seconds and is excluded from those benchmark timings.

Routing produced 30 driver's-licence findings, eight keyword-context DOB
findings, and four passport findings. Regex/checksummed findings were not
routed, consistent with the locked production routing contract.

## Hypothesis result: noisy OCR versus clean context

**Supported, with a qualification.** The strongest controlled pair is the
same value and taxonomy in the two requested contexts:

| Context | File | Value and type | Decision | Resulting risk |
|---|---|---|---|---|
| Photographed/OCR | `YT/YT_front_sample.webp` | `504896` — `identifier.government.drivers_license_yt` | FALSE_POSITIVE | LOW |
| Typed harness | `yk_licence_compact_01.txt` | `504896` — `identifier.government.drivers_license_yt` | LEGITIMATE | HIGH |

The photo context explicitly contains `Yukon`, `OPERATOR'S LICENCE`, `PERMIS
DE CONDUIRE`, `CANADA`, the value, a name, and an address, yet the verifier
reason says the context did not provide enough information. The clean typed
context (`Synthetic Yukon record / Yukon driver's licence: 504896`) retained
the identical finding as legitimate.

A second exact-value pair behaves the same way:

- `PUBLI020220005` / Nova Scotia licence was demoted in
  `NS/NS_front_specimen.jpg` but retained in
  `ns_licence_compact_01.txt`.

This is not universal. The photographed Nunavut value `A1234 5678-004` was
retained in both photo and typed contexts, and the photographed Quebec value
`L1531-171274-08` was retained while the same value in `QC/QC_source.pdf` was
demoted. The evidence therefore supports context-sensitive judgment failure,
especially on noisy OCR, but does not show that OCR noise is the only cause.
Owner ground truth is required before computing correct- versus wrong-demotion
rates.

## Findings that should have been demoted and were not

The two owner-specified licence-back barcode findings remain MEDIUM and were
not offered to the verifier:

| File | Value | Detected type | Source | Original/final risk | Verifier result | Why it was not demoted |
|---|---|---|---|---|---|---|
| `NS/NS_back_specimen.jpg` | `0000000000006650` | `identifier.financial_unverified.credit_card` | regex | MEDIUM / MEDIUM | NOT ROUTED | Regex sources are excluded by `ROUTABLE_SOURCES`. |
| `NT/NT_back_specimen.jpg` | `0010 1000 0935 3612` | `identifier.financial_unverified.credit_card` | regex | MEDIUM / MEDIUM | NOT ROUTED | Regex sources are excluded by `ROUTABLE_SOURCES`. |

These are Luhn-valid barcode numbers printed on licence backs, not payment
cards. No additional owner-confirmed missed demotions can be asserted until
the ground-truth column below is filled. This benchmark does not propose a
routing or detector change.

## Photographed-image findings

| File | Value | Type | Original risk | Verifier decision | Verifier reason | OWNER GROUND TRUTH |
|---|---|---|---|---|---|---|
| AB/AB_front_specimen.jpg | 134711-320 | identifier.government.drivers_license_ab | HIGH | LEGITIMATE | The value matches a valid Alberta drivers license format and context. | CORRECT |
| BC/BC_front_back_specimen.jpg | 2222222 | identifier.government.drivers_license_bc | HIGH | FALSE_POSITIVE | The value is a sequence of digits and does not match the typical format of a BC drivers license number. | CORRECT(AS PER PURVIEW: bc LICENSE IS SEVEN DIGITS) |
| MB/MB_front_sample.jpg | 2000/08/08 | identifier.personal.dob | MEDIUM | FALSE_POSITIVE | The value is a date of birth but the context contains multiple other personal identifiers and details, suggesting it was not isolated as a standalone DOB. | DOB |
| MB/MB_front_sample.jpg | PU-BL-IJ-Q008NH | identifier.government.drivers_license_mb | HIGH | LEGITIMATE | The value matches a valid MB drivers license number and is part of a typical driver's license format. | CORRECT |
| NB/NB_front_sample.jpg | 12345 | identifier.government.drivers_license_nb | HIGH | FALSE_POSITIVE | The value is a numeric license number but the context does not strongly indicate it's from a Canadian drivers license. | CORRECT, IT IS A PLACEHOLDER FOR DRIVING LICENSE THE FULL PLACEHOLDER IS SAMPLE_12345 |
| NL/NL_front_sample_with_DL_number.jpg | 2023/08/30 | identifier.personal.dob | MEDIUM | FALSE_POSITIVE | The value appears to be a date of birth but the context suggests it might be part of a driver's license or passport information, which could include dates in different formats. | FALSE, THIS IS EXPIRY DATE |
| NL/NL_front_sample_with_DL_number.jpg | 1991/01/01 | identifier.personal.dob | MEDIUM | LEGITIMATE | The DOB is explicitly stated in the context and matches the detected type. | CORRECT |
| NS/NS_front_sample_with_DL_number.jpg | 2019/08/30 | identifier.personal.dob | MEDIUM | FALSE_POSITIVE | The value appears to be a date of birth but is surrounded by context that suggests it might be part of a driver's license or passport number. | FALSE- THIS IS EXP DATE |
| NS/NS_front_sample_with_DL_number.jpg | 1980/02/02 | identifier.personal.dob | MEDIUM | LEGITIMATE | The DOB is clearly stated in the context and matches the detected type. | CORRECT |
| NS/NS_front_specimen.jpg | 2021/08/29 | identifier.personal.dob | MEDIUM | FALSE_POSITIVE | The value appears to be a date of birth but is surrounded by context that suggests it might be part of a driver's license or vehicle registration information. | FALSE THIS IS EXP |
| NS/NS_front_specimen.jpg | 1960/04/01 | identifier.personal.dob | MEDIUM | FALSE_POSITIVE | The value appears to be a date of birth but is surrounded by context that suggests it might be part of an ID document or record, not the actual DOB itself. | CORRECT, THIS IS A DATE OF BIRTH |
| NS/NS_front_specimen.jpg | PUBLI020220005 | identifier.government.drivers_license_ns | HIGH | FALSE_POSITIVE | The value does not match typical drivers license formats and contains non-standard characters. | CORRECT THIS IS DRIVING LICENSE PLACEHODLER, EXTRACTED CORRECTLY |
| NU/NU_front_sample.jpg | A1234 5678-004 | identifier.government.drivers_license_nu | HIGH | LEGITIMATE | The value matches a valid Canadian drivers license number and is part of the expected context. | CORRECT |
| ON/ON_front_sample_with_DL_number.jpg | D0123-45678-90123 | identifier.government.drivers_license_on | HIGH | LEGITIMATE | The value matches a valid drivers license format and context. | CORRECT |
| PE/PE_front_sample_with_DL_number.jpg | 2023/08/30 | identifier.personal.dob | MEDIUM | FALSE_POSITIVE | The value appears to be a date of birth but is surrounded by context that suggests it might be part of a name or address field. | FALSE THIS IS EXP |
| QC/QC_front_sample.jpg | L1531-171274-08 | identifier.government.drivers_license_qc | HIGH | LEGITIMATE | The value matches a valid Quebec drivers license number and is part of the expected context. | CORRECT |
| SK/SK_front_specimens.png | 20000030 | identifier.government.drivers_license_sk | HIGH | LEGITIMATE | The value matches a valid Saskatchewan drivers license number. | CORRECT |
| YT/YT_front_sample.webp | 504896 | identifier.government.drivers_license_yt | HIGH | FALSE_POSITIVE | The value is a driver's license number, but the context does not provide enough information to verify it as legitimate. | CORRECT IT IS DRIVING LICENSE NUMBER AND CORRECT NO CONTEXT RIGHT BESIDE THE NUMBER TO VERIFY |
| YT/YT_front_sample.webp | 202404 | identifier.government.drivers_license_yt | HIGH | FALSE_POSITIVE | The value 202404 does not appear to be a valid drivers license number and is too short for a typical one. | FALSE, ISSUE DATE |
| YT/YT_front_sample_with_DL_number.jpg | 129804 | identifier.government.drivers_license_yt | HIGH | FALSE_POSITIVE | The value '129804' does not appear to be a valid drivers license number in any jurisdiction. | ORRECT IT IS DRIVING LICENSE NUMBER AND CORRECT NO CONTEXT RIGHT BESIDE THE NUMBER TO VERIFY |

## Typed-text findings

These 20 findings are from the historical 88-file Canadian typed corpus.

| File | Value | Type | Original risk | Verifier decision | Verifier reason | OWNER GROUND TRUTH |
|---|---|---|---|---|---|---|
| IMG_20260724_142233_01.txt | AB123456 | identifier.government.passport_ca | HIGH | LEGITIMATE | The context clearly indicates it is a Canadian passport number. | CORRECT |
| ab_licence_compact_01.txt | 65917926 | identifier.government.drivers_license_ab | HIGH | LEGITIMATE | The value matches a valid Alberta drivers license number. | CORRECT |
| bc_licence_compact_01.txt | 5453132 | identifier.government.drivers_license_bc | HIGH | LEGITIMATE | The value matches a valid BC drivers license number. | CORRECT |
| foreign_passport_generic_01.txt | 123456789 | identifier.government.passport_generic | HIGH | FALSE_POSITIVE | The value is a synthetic ID number and not a real passport number. | CORRECT |
| mb_licence_compact_01.txt | PUBLIJQ008NH | identifier.government.drivers_license_mb | HIGH | LEGITIMATE | The value matches a known drivers license number format for Manitoba | CORRECT |
| nb_licence_compact_01.txt | 279774 | identifier.government.drivers_license_nb | HIGH | LEGITIMATE | The value matches a valid drivers license number in New Brunswick. | CORRECT |
| nl_licence_compact_01.txt | N251849238 | identifier.government.drivers_license_nl | HIGH | LEGITIMATE | The value matches a valid NL drivers license number format. | CORRECT |
| ns_licence_compact_01.txt | PUBLI020220005 | identifier.government.drivers_license_ns | HIGH | LEGITIMATE | The value matches a known drivers license format from Nova Scotia. | CORRECT |
| ns_licence_display_01.txt | PUBLI-020220005 | identifier.government.drivers_license_ns | HIGH | LEGITIMATE | The value matches a valid Nova Scotia drivers license number format. | CORRECT |
| nt_licence_compact_01.txt | 1234567890 | identifier.government.drivers_license_nt | HIGH | LEGITIMATE | The value matches a valid drivers license number format for the Northwest Territories. | CORRECT |
| nu_licence_compact_01.txt | A1234 5678-004 | identifier.government.drivers_license_nu | HIGH | LEGITIMATE | The value matches a valid Nunavut drivers license format. | CORRECT |
| on_licence_compact_01.txt | A64542704850023 | identifier.government.drivers_license_on | HIGH | LEGITIMATE | The value matches a valid drivers license number from Ontario | CORRECT |
| on_licence_compact_02.txt | A1990-50592-90423 | identifier.government.drivers_license_on | HIGH | LEGITIMATE | The value matches a valid Ontario drivers license number format. | CORRECT |
| passport_display_space_01.txt | CD654321 | identifier.government.passport_ca | HIGH | FALSE_POSITIVE | The value appears to be a Canadian passport number but the context suggests it's not real. | CORRECT |
| passport_scan_01.txt | AB123456 | identifier.government.passport_ca | HIGH | LEGITIMATE | The context clearly indicates it is a Canadian passport number. | CORRECT |
| pe_licence_compact_01.txt | 733727 | identifier.government.drivers_license_pe | HIGH | LEGITIMATE | The value matches a valid drivers license number for PEI. | CORRECT |
| qc_licence_compact_01.txt | Q426857674025 | identifier.government.drivers_license_qc | HIGH | FALSE_POSITIVE | The value is a synthetic ID number, not a real drivers license | CORRECT |
| qc_licence_display_01.txt | L1531-171274-08 | identifier.government.drivers_license_qc | HIGH | LEGITIMATE | The value matches a valid drivers license number format for QC | CORRECT |
| sk_licence_compact_01.txt | 20494624 | identifier.government.drivers_license_sk | HIGH | LEGITIMATE | The value matches a valid Saskatchewan drivers license number. | CORRECT |
| yk_licence_compact_01.txt | 504896 | identifier.government.drivers_license_yt | HIGH | LEGITIMATE | The value matches a valid drivers license number in the Yukon region. | CORRECT |

## Showcase source-PDF findings

| File | Value | Type | Original risk | Verifier decision | Verifier reason | OWNER GROUND TRUTH |
|---|---|---|---|---|---|---|
| NT/NT_source.pdf | 1234567890 | identifier.government.drivers_license_nt | HIGH | FALSE_POSITIVE | The value is a driver's license number but the context contains other identifying information such as address and class, suggesting it may be from a different document. | CORRECT |
| QC/QC_source.pdf | L1531-171274-08 | identifier.government.drivers_license_qc | HIGH | FALSE_POSITIVE | The value is a license plate number, not a drivers license ID. | CORRECT |

## Gate for Task 3

Task 3 must not run until the owner fills every `OWNER GROUND TRUTH` cell.
Once filled, the labels in this document are the fixed comparison set for the
six model/prompt runs; no production default, prompt, or threshold needs to be
changed to perform that measurement.
