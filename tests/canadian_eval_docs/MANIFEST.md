# Canadian identifier evaluation corpus

All values are synthetic and generated, tied to no real person. The generator is the single source of truth for this document and `manifest.json`.

Summary: 91 files — 72 POSITIVE, 19 NEGATIVE.

Verdict × status: POSITIVE/OK=65; POSITIVE/GAP-MISS=3; POSITIVE/UNVERIFIED=4; NEGATIVE/OK=19.

A NEGATIVE asserts zero findings whose taxonomy category starts with `identifier.`. It does **not** assert zero findings overall; `entity.*` and `contact.*` findings from surrounding text are allowed.

`GAP-FALSE-POSITIVE` means the detector should have found nothing. `GAP-TIER` means existence is correct but trust/risk is wrong; `GAP-PARTIAL` means only part of the public identifier span was captured. These distinctions prevent a harness from scoring tier or span disagreements as absence failures.

Microsoft Purview is the format authority for all ten provinces, with each pattern corroborated against a photographed specimen. NT, NU, and YK are not covered by Purview and are explicitly marked as weaker specimen-derived cases.

**Audit correction:** Audit section 1.1's negative contract for ON/BC checksum failure is incorrect; verified against `detectors/health_card_detector.py`.

## SIN

Valid and invalid SINs prove the ratified Luhn and first-digit rules.

filename                                      | verdict  | status     | expected
--------------------------------------------- | -------- | ---------- | --------
sin_valid_01.txt                              | POSITIVE | OK         | 1x SIN, checksum valid, HIGH (audit item 23, owner-ratified)
sin_valid_02.txt                              | POSITIVE | OK         | 1x SIN, checksum valid, HIGH (audit item 23, owner-ratified)
sin_valid_03.txt                              | POSITIVE | OK         | 1x SIN, checksum valid, HIGH (audit item 23, owner-ratified)
sin_invalid_luhn_01.txt                       | NEGATIVE | OK         | no identifier finding; SIN checksum invalid
sin_invalid_luhn_02.txt                       | NEGATIVE | OK         | no identifier finding; SIN checksum invalid

## Health cards

Provincial compact/display forms cover every jurisdiction and every trust tier supported by its real format.

filename                                      | verdict  | status     | expected
--------------------------------------------- | -------- | ---------- | --------
on_healthcard_compact_01.txt                  | POSITIVE | OK         | 1x ON health card, compact, HIGH
on_healthcard_compact_02.txt                  | POSITIVE | OK         | 1x ON health card, compact, MEDIUM
bc_healthcard_compact_01.txt                  | POSITIVE | OK         | 1x BC health card, compact, HIGH
bc_healthcard_compact_02.txt                  | POSITIVE | OK         | 1x BC health card, compact, MEDIUM
ab_healthcard_compact_01.txt                  | POSITIVE | OK         | 1x AB health card, compact, HIGH
sk_healthcard_compact_01.txt                  | POSITIVE | OK         | 1x SK health card, compact, HIGH
mb_healthcard_compact_01.txt                  | POSITIVE | OK         | 1x MB health card, compact, HIGH
nb_healthcard_compact_01.txt                  | POSITIVE | OK         | 1x NB health card, compact, HIGH
ns_healthcard_compact_01.txt                  | POSITIVE | OK         | 1x NS health card, compact, HIGH
pe_healthcard_compact_01.txt                  | POSITIVE | OK         | 1x PE health card, compact, HIGH
nt_healthcard_compact_01.txt                  | POSITIVE | OK         | 1x NT health card, compact, HIGH (audit item 4)
nl_healthcard_compact_01.txt                  | POSITIVE | OK         | 1x NL health card, compact, HIGH
nu_healthcard_compact_01.txt                  | POSITIVE | OK         | 1x NU health card, compact, HIGH
yk_healthcard_compact_01.txt                  | POSITIVE | OK         | 1x YK health card, compact, HIGH
qc_healthcard_compact_01.txt                  | POSITIVE | OK         | 1x QC health card, compact, HIGH
on_healthcard_version_2letter_01.txt          | POSITIVE | OK         | 1x ON health card with two-letter version code, HIGH
on_healthcard_version_1letter_01.txt          | POSITIVE | OK         | 1x complete ON health card with one-letter version code, HIGH (audit item 1) — The detector captures the public one-letter version in the same finding span.
on_healthcard_leading_zero_01.txt             | NEGATIVE | OK         | no identifier finding; Ontario does not issue a zero-leading number (audit item 2)
bc_healthcard_mod11_result11_01.txt           | POSITIVE | OK         | 1x BC health card, MOD-11 result 11, MEDIUM unverified (audit item 3) — Both BC checksum-invalid fixtures exercise MOD-11 result 11. This value ends in 0, which the old 11-to-0 mapping accepted; the named checksum-invalid fixture ends in 1 and fell through. Both deliberately expect BC unverified at MEDIUM as branch coverage.
on_healthcard_checksum_invalid_generic_01.txt | POSITIVE | OK         | 1x generic Canadian health card, checksum not province-attributed, HIGH — No province is named; a 10-digit value can legitimately be a Nova Scotia health number, so generic detection is defensible.
on_healthcard_checksum_invalid_named_01.txt   | POSITIVE | OK         | 1x ON health card, checksum failed, MEDIUM unverified (audit section 1.1 correction) — The named province whose checksum failed remains visible at the province-specific MEDIUM-unverified tier.
bc_healthcard_checksum_invalid_generic_01.txt | POSITIVE | OK         | 1x generic Canadian health card, checksum not province-attributed, HIGH — No province is named; a 10-digit value can legitimately be a Nova Scotia health number, so generic detection is defensible.
bc_healthcard_checksum_invalid_named_01.txt   | POSITIVE | OK         | 1x BC health card, checksum failed, MEDIUM unverified (audit section 1.1 correction) — Both BC checksum-invalid fixtures exercise MOD-11 result 11; this value's printed check digit differs from the old 11-to-0 mapping while the dedicated result-11 fixture's printed check digit matched it. Both deliberately expect BC unverified at MEDIUM as branch coverage.
ab_healthcard_display_01.txt                  | POSITIVE | OK         | 1x AB health card, displayed 99999-9999 form, HIGH (audit item 6, owner-ratified display coverage)
qc_healthcard_display_01.txt                  | POSITIVE | OK         | 1x QC health card, printed AAAA 0000 0000 form, HIGH (audit item 5)

## Driver's licences

Ten provincial formats are sourced to Microsoft Purview and corroborated against photographed specimens. Territory cases explicitly identify their weaker specimen-derived authority.

filename                                      | verdict  | status     | expected
--------------------------------------------- | -------- | ---------- | --------
on_licence_compact_01.txt                     | POSITIVE | OK         | 1x ON driver's licence, HIGH (Microsoft Purview Canada driver's licence definition)
on_licence_compact_02.txt                     | POSITIVE | OK         | 1x ON driver's licence, HIGH (Microsoft Purview Canada driver's licence definition)
qc_licence_compact_01.txt                     | POSITIVE | OK         | 1x QC driver's licence, HIGH (Microsoft Purview Canada driver's licence definition)
bc_licence_compact_01.txt                     | POSITIVE | OK         | 1x BC driver's licence, HIGH (Microsoft Purview Canada driver's licence definition)
ab_licence_compact_01.txt                     | POSITIVE | OK         | 1x AB driver's licence, HIGH (Microsoft Purview Canada driver's licence definition)
sk_licence_compact_01.txt                     | POSITIVE | OK         | 1x SK driver's licence, HIGH (Microsoft Purview Canada driver's licence definition)
mb_licence_compact_01.txt                     | POSITIVE | OK         | 1x MB driver's licence, HIGH (Microsoft Purview Canada driver's licence definition)
ns_licence_compact_01.txt                     | POSITIVE | OK         | 1x NS driver's licence, HIGH (Microsoft Purview Canada driver's licence definition)
nb_licence_compact_01.txt                     | POSITIVE | OK         | 1x NB driver's licence, HIGH (Microsoft Purview Canada driver's licence definition)
nl_licence_compact_01.txt                     | POSITIVE | OK         | 1x NL driver's licence, HIGH (Microsoft Purview Canada driver's licence definition)
pe_licence_compact_01.txt                     | POSITIVE | OK         | 1x PE driver's licence, HIGH (Microsoft Purview Canada driver's licence definition)
nt_licence_compact_01.txt                     | POSITIVE | UNVERIFIED | 1x NT driver's licence, HIGH, no public grammar (specimen field 4d; one photographed specimen) — SPECIMEN-DERIVED: 10 digits from one specimen. Single-specimen evidence is weaker than corroboration and is not an established grammar.
nu_licence_compact_01.txt                     | POSITIVE | UNVERIFIED | 1x NU driver's licence, HIGH, no public grammar (specimen field 5; one photographed specimen) — SPECIMEN-DERIVED: one letter plus 4-4-3 digits from one specimen, printed A1234 5678-004. Not an established grammar.
yk_licence_compact_01.txt                     | POSITIVE | UNVERIFIED | 1x YK driver's licence, HIGH, no public grammar (two independent photographed specimens) — SPECIMEN-DERIVED and corroborated by two independent six-digit specimens (504896 and 129804); still not an issuing-authority grammar.
yk_licence_corroborating_02.txt               | POSITIVE | UNVERIFIED | 1x YK six-digit driver's licence, second specimen-derived case, HIGH (second independent photographed specimen) — SPECIMEN-DERIVED and corroborating: this second six-digit case strengthens the Yukon shape but does not make it an issuing-authority grammar.
ab_licence_display_01.txt                     | POSITIVE | OK         | 1x AB driver's licence, hyphenated Purview form, HIGH (Microsoft Purview; photographed specimen corroboration)
on_licence_invalid_suffix_01.txt              | NEGATIVE | OK         | no identifier finding; ON Purview-constrained digit is outside 0-3 (audit item 7)
qc_licence_display_01.txt                     | POSITIVE | OK         | 1x QC driver's licence, printed hyphenated form, HIGH (audit item 8)
bc_licence_current_8digit_01.txt              | NEGATIVE | OK         | no identifier finding; BC Purview format is seven digits (audit item 9)
ns_licence_display_01.txt                     | POSITIVE | OK         | 1x NS Master Number with surname separators/padding, HIGH (audit item 11)
mb_licence_asterisk_01.txt                    | NEGATIVE | OK         | no identifier finding; asterisk form is superseded by Purview (audit item 10) — Microsoft Purview supersedes the former implementation-defined asterisk grammar.
mb_licence_display_01.txt                     | POSITIVE | OK         | 1x MB hyphenated Purview driver's licence, HIGH (Microsoft Purview; photographed specimen corroboration)

## Passports

Canadian passport shapes require context; compact and displayed forms are separate.

filename                                      | verdict  | status     | expected
--------------------------------------------- | -------- | ---------- | --------
passport_scan_01.txt                          | POSITIVE | OK         | 1x Canadian passport, compact, HIGH — ID-bearing filename: mismatch trigger A and content trigger B.
IMG_20260724_142233_01.txt                    | POSITIVE | OK         | 1x Canadian passport, compact, HIGH — Neutral camera filename: content trigger B only.
passport_display_space_01.txt                 | POSITIVE | OK         | 1x Canadian passport, displayed with one space, HIGH

## MRZ exact

Exact ICAO TD1 and TD3 blocks assert field-specific checksums and tiers.

filename                                      | verdict  | status     | expected
--------------------------------------------- | -------- | ---------- | --------
mrz_td3_exact_01.txt                          | POSITIVE | OK         | 1x exact ICAO TD3 block: document HIGH, DOB MEDIUM, expiry LOW
mrz_td1_exact_01.txt                          | POSITIVE | OK         | 1x exact ICAO TD1 block: document HIGH, DOB MEDIUM, expiry LOW

## MRZ invalid

Corrupted document check digits retain an unverified document number while independently valid DOB and expiry fields keep their own tiers.

filename                                      | verdict  | status     | expected
--------------------------------------------- | -------- | ---------- | --------
mrz_td3_bad_document_check_01.txt             | POSITIVE | OK         | 1x unverified MRZ document MEDIUM, DOB MEDIUM, expiry LOW (audit item 22, owner-ratified)
mrz_td1_bad_document_check_01.txt             | POSITIVE | OK         | 1x unverified MRZ document MEDIUM, DOB MEDIUM, expiry LOW (audit item 22, owner-ratified)

## MRZ robustness

Text-level OCR recovery is separate from exact ICAO conformance and covers common character confusions within the detector's tolerance.

filename                                      | verdict  | status     | expected
--------------------------------------------- | -------- | ---------- | --------
mrz_ocr_o_for_zero_01.txt                     | POSITIVE | OK         | recoverable MRZ OCR confusion (O/0 in document number); three tiered findings (audit item 21 robustness class)
mrz_ocr_i_for_one_02.txt                      | POSITIVE | OK         | recoverable MRZ OCR confusion (I/l/1 in numeric DOB); three tiered findings (audit item 21 robustness class)
mrz_ocr_s_for_five_03.txt                     | POSITIVE | OK         | recoverable MRZ OCR confusion (S/5 in numeric DOB); three tiered findings (audit item 21 robustness class)
mrz_ocr_b_for_eight_04.txt                    | POSITIVE | OK         | recoverable MRZ OCR confusion (B/8 in numeric DOB); three tiered findings (audit item 21 robustness class)
mrz_ocr_rn_for_m_05.txt                       | POSITIVE | OK         | recoverable MRZ OCR confusion (rn/m in name line, +1 tolerance); three tiered findings (audit item 21 robustness class)

## Ordinary identifier OCR

Text-level OCR corruption on ordinary identifiers measures whether non-MRZ detectors recover common photographed-card confusions.

filename                                      | verdict  | status     | expected
--------------------------------------------- | -------- | ---------- | --------
ocr_sin_o_for_zero_01.txt                     | POSITIVE | OK         | 1x sin, OCR-corrupted O/0; expected to be reconstructed, MEDIUM (owner-requested non-MRZ OCR robustness) — Known-valid synthetic source 318507522; OCR text 3185O7522. Deterministic confusion substitution is accepted only because the reconstructed value passes the type's published checksum; the finding is capped at MEDIUM and preserves the original OCR token.
ocr_sin_i_for_one_02.txt                      | POSITIVE | OK         | 1x sin, OCR-corrupted I/l/1; expected to be reconstructed, MEDIUM (non-MRZ OCR robustness) — Known-valid synthetic source 318507522; OCR text 3I8507522. Deterministic confusion substitution is accepted only because the reconstructed value passes the type's published checksum; the finding is capped at MEDIUM and preserves the original OCR token.
ocr_health_card_on_b_for_eight_01.txt         | POSITIVE | OK         | 1x health_card_on, OCR-corrupted B/8; expected to be reconstructed, MEDIUM (owner-requested non-MRZ OCR robustness) — Known-valid synthetic source 8327932763; OCR text B327932763. Deterministic confusion substitution is accepted only because the reconstructed value passes the type's published checksum; the finding is capped at MEDIUM and preserves the original OCR token.
ocr_health_card_bc_o_for_zero_01.txt          | POSITIVE | OK         | 1x health_card_bc, OCR-corrupted O/0; expected to be reconstructed, MEDIUM (owner-requested non-MRZ OCR robustness) — Known-valid synthetic source 9683128301; OCR text 96831283O1. Deterministic confusion substitution is accepted only because the reconstructed value passes the type's published checksum; the finding is capped at MEDIUM and preserves the original OCR token.
ocr_health_card_ab_s_for_five_01.txt          | POSITIVE | GAP-MISS   | 1x health_card_ab, OCR-corrupted S/5; expected to be recovered, HIGH (owner-requested non-MRZ OCR robustness) — Known-valid synthetic source 123456789; OCR text 1234S6789. No public checksum exists for this identifier type, so deterministic OCR recovery is intentionally not attempted.
ocr_drivers_license_on_i_for_one_01.txt       | POSITIVE | GAP-MISS   | 1x drivers_license_on, OCR-corrupted I/l/1; expected to be recovered, HIGH (non-MRZ OCR robustness) — Known-valid synthetic source A12345678901231; OCR text A123456I8901231. No public checksum exists for this identifier type, so deterministic OCR recovery is intentionally not attempted.
ocr_passport_ca_eight_for_b_01.txt            | POSITIVE | GAP-MISS   | 1x passport_ca, OCR-corrupted B/8; expected to be recovered, HIGH (owner-requested non-MRZ OCR robustness) — Known-valid synthetic source AB123456; OCR text A8123456. No public checksum exists for this identifier type, so deterministic OCR recovery is intentionally not attempted.

## UCI

All four publicly sourced UCI compact/display forms require nearby IRCC context.

filename                                      | verdict  | status     | expected
--------------------------------------------- | -------- | ---------- | --------
uci_compact_8_01.txt                          | POSITIVE | OK         | 1x IRCC UCI, HIGH; confidence 0.60, source priority 2 (audit section 3.1) — No public checksum exists; context is required and no _unverified tier applies.
uci_display_4_4_02.txt                        | POSITIVE | OK         | 1x IRCC UCI, HIGH; confidence 0.60, source priority 2 (audit section 3.1) — No public checksum exists; context is required and no _unverified tier applies.
uci_compact_10_03.txt                         | POSITIVE | OK         | 1x IRCC UCI, HIGH; confidence 0.60, source priority 2 (audit section 3.1) — No public checksum exists; context is required and no _unverified tier applies.
uci_display_2_4_4_04.txt                      | POSITIVE | OK         | 1x IRCC UCI, HIGH; confidence 0.60, source priority 2 (audit section 3.1) — No public checksum exists; context is required and no _unverified tier applies.

## Status registration

Ten-digit status-card registration numbers are context-gated and have no public checksum.

filename                                      | verdict  | status     | expected
--------------------------------------------- | -------- | ---------- | --------
status_card_registration_01.txt               | POSITIVE | OK         | 1x status-card registration number, HIGH; confidence 0.60, source priority 2 (audit section 3.2) — ID-bearing filename: mismatch triggers A and B. No public checksum exists; no _unverified tier applies.
DSC_20260724_084512_01.txt                    | POSITIVE | OK         | 1x status-card registration number, HIGH; confidence 0.60, source priority 2 (audit section 3.2) — Neutral camera filename: mismatch trigger B only. No public checksum exists; no _unverified tier applies.

## Context-free negatives

Bare format-shaped values without checksum or nearby context provide no identifier evidence.

filename                                      | verdict  | status     | expected
--------------------------------------------- | -------- | ---------- | --------
bare_9digit_nocontext_01.txt                  | NEGATIVE | OK         | no identifier finding
bare_10digit_nocontext_02.txt                 | NEGATIVE | OK         | no identifier finding
bare_8digit_nocontext_03.txt                  | NEGATIVE | OK         | no identifier finding
bare_uci_4_4_nocontext_04.txt                 | NEGATIVE | OK         | no identifier finding
bare_uci_2_4_4_nocontext_05.txt               | NEGATIVE | OK         | no identifier finding

## Adjacent negatives

Business identifiers and phone numbers near ordinary filler must not be promoted into government identifiers.

filename                                      | verdict  | status     | expected
--------------------------------------------- | -------- | ---------- | --------
adjacent_invoice_01.txt                       | NEGATIVE | OK         | no identifier finding
adjacent_employee_02.txt                      | NEGATIVE | OK         | no identifier finding
adjacent_order_03.txt                         | NEGATIVE | OK         | no identifier finding
adjacent_part_04.txt                          | NEGATIVE | OK         | no identifier finding
adjacent_phone_05.txt                         | NEGATIVE | OK         | no identifier finding

## Contextual negatives

Abstract discussion of identity documents contains no identifier value.

filename                                      | verdict  | status     | expected
--------------------------------------------- | -------- | ---------- | --------
contextual_healthcard_01.md                   | NEGATIVE | OK         | no identifier finding
contextual_licence_02.md                      | NEGATIVE | OK         | no identifier finding
contextual_passport_03.md                     | NEGATIVE | OK         | no identifier finding

## Scope boundary

A generic foreign passport is real PII and is documented here without claiming it is a Canadian issuing format.

filename                                      | verdict  | status     | expected
--------------------------------------------- | -------- | ---------- | --------
foreign_passport_generic_01.txt               | POSITIVE | OK         | 1x generic foreign passport, HIGH (audit item 20, owner-ratified scope boundary) — Scope-boundary documentation, not a Canadian format claim.
