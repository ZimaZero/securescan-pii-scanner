# Comparative LLM verifier benchmark

Measured 2026-08-06 against the 42 owner-labelled findings in
`docs/evidence/verifier_benchmark.md`. Production defaults, prompt, thresholds,
routing, and code were unchanged. Each arm used the production request path
(temperature 0, 150-token cap, 8 threads, 30-second timeout and one retry),
with one excluded warm-up call before timing the sequential 42-finding pass.

## Ground-truth gate

The required baseline reproduced exactly: 37 CORRECT and 5 FALSE findings;
qwen2.5:3b Arm 1 made 17 demotions, of which 5 were correct and 12 were wrong.
Dates were 6 demotions (4 correct, 2 wrong); identifiers were 11 (1 correct,
10 wrong).

“Correct demotion” means an owner-labelled FALSE finding was demoted.
“Wrong demotion” means an owner-labelled CORRECT finding was demoted—a true
positive lost. No-decision/parse failures are not demotions.

## Models and disk cost

| Model | Role and reason | Ollama parameters / quantization | Exact bytes | GiB | After run |
|---|---|---|---:|---:|---|
| `qwen2.5:3b` | Production baseline | 3.1B / Q4_K_M | 1,929,912,432 | 1.80 | Retained (production model) |
| `qwen2.5:7b` | Same-family size step | 7.6B / Q4_K_M | 4,683,087,332 | 4.36 | Removed |
| `llama3.1:8b` | Strong established 8B counterpoint; tests whether capacity fixes the rejected Llama 3.2 3B family's rubber-stamping | 8.0B / Q4_K_M | 4,920,753,328 | 4.58 | Removed |
| `ministral-3:3b` | New compact Mistral-family judge, absent from prior bake-offs; avoids repeating rejected `llama3.2:3b` or `phi3.5` | 3.8B / Q4_K_M | 2,953,840,808 | 2.75 | Removed |

The benchmark record (`tests/ollama_benchmark/RESULTS.md`) shows that
`llama3.2:3b` previously scored 56% overall and caught only 1/8 false
positives, while `phi3.5` scored 44%, caught 0/8 false positives, and had JSON
failures. Neither was reused. Models were pulled and removed one at a time;
the final `ollama list` contained only `qwen2.5:3b`.

## Headline results

| Model | Arm | Correct demotions | **WRONG demotions** | Missed demotions | No decision | Wall time |
|---|---|---:|---:|---:|---:|---:|
| qwen2.5:3b | 1 production | 5 | **12** | 0 | 0 | 267.29 s |
| qwen2.5:3b | 2 reference | 5 | **36** | 0 | 0 | 226.89 s |
| qwen2.5:7b | 1 production | 5 | **18** | 0 | 0 | 272.62 s |
| qwen2.5:7b | 2 reference | 5 | **30** | 0 | 0 | 366.51 s |
| llama3.1:8b | 1 production | 0 | **0** | 5 | 0 | 405.00 s |
| llama3.1:8b | 2 reference | 0 | **2** | 5 | 0 | 419.25 s |
| ministral-3:3b | 1 production | 5 | **27** | 0 | 1 | 246.24 s |
| ministral-3:3b | 2 reference | 5 | **20** | 0 | 1 | 285.25 s |

The production qwen2.5:3b arm has the best useful trade-off, but 12 wrong
demotions out of 37 true findings is still unsafe. Llama's zero-wrong result is
not success: it demoted nothing and missed every false finding.

## Date versus identifier split

Cells are `correct demotions / wrong demotions / missed demotions`.

| Model | Arm | Dates (8 findings; 4 FALSE) | Identifiers (34 findings; 1 FALSE) |
|---|---|---|---|
| qwen2.5:3b | 1 | 4 / 2 / 0 | 1 / 10 / 0 |
| qwen2.5:3b | 2 | 4 / 4 / 0 | 1 / 32 / 0 |
| qwen2.5:7b | 1 | **4 / 0 / 0** | 1 / 18 / 0 |
| qwen2.5:7b | 2 | 4 / 1 / 0 | 1 / 29 / 0 |
| llama3.1:8b | 1 | 0 / 0 / 4 | 0 / 0 / 1 |
| llama3.1:8b | 2 | 0 / 0 / 4 | 0 / 2 / 1 |
| ministral-3:3b | 1 | 4 / 4 / 0 | 1 / 23 / 0 |
| ministral-3:3b | 2 | 4 / 4 / 0 | 1 / 16 / 0 |

The split is stable for models that actually demote: identifiers dominate
wrong demotions. qwen2.5:7b Arm 1 perfectly handled the date subset (all four
expiry/issue misclassifications demoted, all four DOBs retained) but lost 18
correct identifiers. This supports measuring a future date-only routing arm;
it does not authorize changing routing here.

## Findings that should have been demoted and were not

Only the two Llama arms missed demotions. Both missed the same five findings:

| File | Value | Ground truth |
|---|---|---|
| `NL/NL_front_sample_with_DL_number.jpg` | `2023/08/30` | FALSE — expiry date, not DOB |
| `NS/NS_front_sample_with_DL_number.jpg` | `2019/08/30` | FALSE — expiry date, not DOB |
| `NS/NS_front_specimen.jpg` | `2021/08/29` | FALSE — expiry date, not DOB |
| `PE/PE_front_sample_with_DL_number.jpg` | `2023/08/30` | FALSE — expiry date, not DOB |
| `YT/YT_front_sample.webp` | `202404` | FALSE — issue date, not a Yukon licence |

All other arms demoted all five FALSE findings. The two licence-back barcode
false positives from Task 2 remain outside routing and therefore outside this
fixed 42-finding comparison.

## Input-source split

Cells are `correct / wrong / missed demotions`.

| Model / arm | Photographed images (20) | Typed text (20) | Source PDFs (2) |
|---|---|---|---|
| qwen2.5:3b / 1 | 5 / 7 / 0 | 0 / 3 / 0 | 0 / 2 / 0 |
| qwen2.5:3b / 2 | 5 / 14 / 0 | 0 / 20 / 0 | 0 / 2 / 0 |
| qwen2.5:7b / 1 | 5 / 3 / 0 | 0 / 14 / 0 | 0 / 1 / 0 |
| qwen2.5:7b / 2 | 5 / 8 / 0 | 0 / 20 / 0 | 0 / 2 / 0 |
| llama3.1:8b / 1 | 0 / 0 / 5 | 0 / 0 / 0 | 0 / 0 / 0 |
| llama3.1:8b / 2 | 0 / 0 / 5 | 0 / 2 / 0 | 0 / 0 / 0 |
| ministral-3:3b / 1 | 5 / 11 / 0 | 0 / 15 / 0 | 0 / 1 / 0 |
| ministral-3:3b / 2 | 5 / 15 / 0 | 0 / 4 / 0 | 0 / 1 / 0 |

## Prompt arms

Arm 1 used `detectors.llm_verifier._BASE_SYSTEM_PROMPT` unchanged. Arm 2
appended only the requested reference/instruction material: synthetic,
specimen, sample, placeholder-looking, repeated-digit, or fabricated values
remain legitimate when they match the claimed type; the judge evaluates type,
not document authenticity. It then listed the scanner's formats: AB 6-3 or
5–9 digits; BC 7 digits; MB `AA-AA-AA-A999AA`; NB 5–7 digits; NL letter+9;
NS five letters plus the documented digit shape; ON letter plus its documented
13-digit/day-sex shape; PE 5–6; QC letter+12 (optional 4-6-2 hyphens); SK 8;
NT 10; NU letter+4-4-3; YT 6; Canadian passport two letters+6; generic passport
9 digits in passport context. The ten provincial formats are Purview-derived;
NT/NU/YT were explicitly identified in the prompt as specimen-derived because
Purview does not publish territorial formats.

## Answers

**Q1 — knowledge or judgment? Judgment.** Arm 2 did not fix any model overall.
Wrong demotions changed 12→36, 18→30, 0→2, and 27→20. Even when a supplied
rule exactly matched a value, models sometimes reasoned that it did not (for
example, qwen2.5:3b said seven digits failed the supplied exactly-seven-digit
BC rule). Format knowledge alone is not a viable fix.

**Q2 — does size help? No general size effect.** qwen 7B was worse than qwen
3B (18 versus 12 wrong demotions in Arm 1). Llama 8B repeated Llama 3B's prior
rubber-stamping failure mode. Family/judgment behavior dominated size.

**Q3 — does the date/identifier split hold? Yes.** For every non-rubber-stamp
production arm, wrong identifier demotions greatly exceeded wrong date
demotions. qwen2.5:7b Arm 1 was perfect on dates and unsafe on identifiers.
Date-only routing is therefore a viable next experiment, not a change made by
this benchmark.
