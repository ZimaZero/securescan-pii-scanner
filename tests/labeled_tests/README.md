# Labeled Test Set for SecureScan

A small ground-truth corpus for measuring detection quality.

## What's here
- `files/` — 10 small text files with KNOWN PII content
- `ANSWER_KEY.md` — the ground truth: what should/shouldn't be detected in each file

## How to use it
1. Build the CPU image with `docker compose build securescan-cpu`.
2. Scan it with `docker compose run --rm securescan-cpu python scanner.py --path tests/labeled_tests/files --no-open`.
3. Compare the scan output against `ANSWER_KEY.md`
4. Count: True Positives, False Positives, False Negatives
5. Compute precision and recall (formulas in the answer key)

## Why it matters
Re-run the scan after a detector change and compare precision and recall with
the answer key.

## The deliberately tricky cases
- File 01: bare SINs with NO keyword (the core thing that was being missed)
- File 02: invalid 9-digit numbers that MUST be rejected (Luhn + prefix rule)
- File 04: phone numbers that must NOT be misread as SINs
- File 05: an invalid credit card that must fail the Luhn check
- File 07: a clean file with NO PII (tests for false positives)

All SINs and credit cards were verified against their real validation rules
before being placed in the answer key.
