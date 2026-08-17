# OCR viability figure resolution

Resolved on 2026-08-05. The public production OCR-viability figure is
**35/46 (76.1%)**. The earlier **39/46 (84.8%)** remains valid only as the
result of the standalone OCR-engine benchmark arm; it must not be presented as
the current production-harness result.

## What each number counts

Both percentages use the same denominator: the **46 rows with
`verdict=POSITIVE`** in `tests/specimen_eval_docs/GROUND_TRUTH.csv`. The 35
negative-control rows are excluded from OCR viability.

| Figure | Numerator | Denominator | Definition |
|---|---:|---:|---|
| Historical Tesseract baseline | 24 | 46 | The 24 GUARD rows whose expected value Tesseract recovered. This is 52.2%. |
| Standalone Paddle benchmark | 39 | 46 | All 24 GUARD rows retained plus 15 of the 22 TARGET rows recovered. This is 84.8%. |
| Current production harness | 35 | 46 | Positive rows for which `tests/run_specimen_eval.py` finds the normalized expected value in the raw text returned by production `scan_file()`. This is 76.1%. |

The harness computes its numerator as:

```text
sum(row.expected_in_text for every POSITIVE row)
```

It normalizes the expected value and extracted text by removing whitespace and
hyphens and applying case-folding. Its denominator is always all 46 positive
rows.

## Why 39 and 35 differ

The 39/46 number came from the standalone engine-comparison experiment: its
TARGET/GUARD arms directly measured the candidate OCR engine. The 35/46 number
comes from the maintained end-to-end harness and current production extraction
contract.

One scoring-path difference is explicit in the current harness. Three positive
rows whose notes begin `EXTRACTION-LIMITED` are deliberately not scanned; they
remain in the 46-row denominator and contribute zero to the numerator. Of the
remaining 43 attempted positive rows, 35 contain the normalized expected value
and eight do not. The resulting production ceiling is therefore:

```text
35 present + 8 absent after scanning + 3 documented limits = 46 positives
35 / 46 = 76.1%
```

The standalone 39/46 result and maintained 35/46 result are consequently not
interchangeable measurements, even though they share a corpus denominator.
The production harness is reproducible, exercises `scan_file()` with current
defaults, and is the scoring authority. README now presents **35/46 (76.1%)**
as the public end-to-end viability number and labels **39/46 (84.8%)** only as
the historical standalone bake-off result.

No ground truth, detector, extractor, or stored regression anchor was changed
as part of this resolution.
