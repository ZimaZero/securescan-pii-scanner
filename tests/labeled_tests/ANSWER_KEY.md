# Labeled Test Set — Answer Key

This is the ground truth for measuring the regex (Layer 1) and hybrid detector.
Every SIN below was verified against real rules: 9 digits, first digit NOT 0 or 8, passes Luhn.
Every credit card was verified against the Luhn checksum.

For each file: what SHOULD be detected (true positives) and what must NOT be (true negatives).

---

## 01_bare_sin_list.txt
**The core test — bare SINs with no keyword nearby.**
SHOULD detect (SIN):
- 193 456 787  (valid)
- 130 692 544  (valid)
- 480 184 514  (valid)
Must NOT detect: nothing else.

## 02_invalid_sins.txt
**Invalid 9-digit numbers — must all be rejected.**
SHOULD detect: NOTHING.
Must NOT detect as SIN:
- 123 456 789  (fails Luhn)
- 046 454 286  (starts with 0)
- 812 345 678  (starts with 8)

## 03_email_phone.txt
SHOULD detect:
- email: alex@example.com
- phone: 403-555-0123
- phone: (587) 234 5678
Must NOT detect: no SIN, no credit card.

## 04_phone_not_sin.txt
**Overlap test — phone numbers must not be misread as SINs.**
SHOULD detect:
- phone: 403 555 0123
- phone: 587 234 5678
Must NOT detect: NO SIN. (These are 10-digit phone numbers, not 9-digit SINs.)

## 05_credit_cards.txt
SHOULD detect (credit_card):
- 4111 1111 1111 1111  (valid Luhn)
- 4532 0151 1283 0366  (valid Luhn)
Must NOT detect:
- 1234 5678 9012 3456  (fails Luhn — must be rejected)

## 06_sin_with_keyword.txt
SHOULD detect:
- SIN: 132 677 360  (valid, WITH keyword → should be HIGHER confidence than a bare SIN)
- person (GLiNER): Jane Smith
Must NOT detect: "SIN" or "Name" as an entity.

## 07_clean_no_pii.txt
**False-positive test — a normal document with no PII.**
SHOULD detect: NOTHING.
Must NOT detect: any SIN, email, phone, card, or spurious entity.

## 08_mixed.txt
SHOULD detect:
- person (GLiNER): Robert Chen
- email: rchen@acme.com
- phone: 403-555-9988
- SIN: 500 978 820  (valid)
- credit_card: 5500 0000 0000 0004  (valid Luhn)
- dob: 1985-07-22
Must NOT detect: "SIN", "Email", "Card", "Client" as entities/orgs.

## 09_dates.txt
SHOULD detect (dob/date):
- 1990-03-15
- 2026-01-10
- 15/06/2025
Must NOT detect: no SIN, no phone.

## 10_ssn.txt
SHOULD detect:
- ssn: 123-45-6789  (US SSN format — currently SSN regex is commented out, so this
  documents EXPECTED behavior if/when SSN is re-enabled; today it may be missed)
Must NOT detect: not as a SIN (it's 3-2-4 format, 9 digits but US SSN).

---

## Scoring notes
- **True Positive (TP):** detector found something the answer key says SHOULD be there.
- **False Positive (FP):** detector found something the answer key says should NOT be there.
- **False Negative (FN):** detector MISSED something the answer key says SHOULD be there.
- **Precision** = TP / (TP + FP)  → "of what it flagged, how much was real"
- **Recall** = TP / (TP + FN)  → "of what was real, how much did it catch"

Goal: high precision (few false alarms) AND high recall (few misses).
Known weak spots include SIN false positives, GLiNER tagging field labels
("SIN", "Email") as entities, credit card regex over-matching.
