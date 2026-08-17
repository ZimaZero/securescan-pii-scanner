# Ollama Verification-Judge Benchmark Results

Benchmarks `llama3.2:3b`, `qwen2.5:3b`, and `phi3.5` as candidate judges for the optional Ollama verification layer. Each model received the same 16 cases (8 expected FALSE_POSITIVE and 8 expected LEGITIMATE) twice, with one model resident in RAM at a time and unloaded (`keep_alive: 0`) before the next model. `format: "json"` was deliberately omitted so JSON reliability measures instruction-following rather than a forced grammar.

**Clean rerun:** this is run 2, executed end-to-end in a single stable environment (18 vCPUs throughout, `num_thread: 6` unchanged) with no VM freezes, no corrupted writes, and no excluded latency outliers — contrast with [RESULTS_run1.md](RESULTS_run1.md) / [raw_results_run1.jsonl](raw_results_run1.jsonl), which spanned an 8-vCPU period that hit a hard VM crash mid-run. Env timeouts are 0 for all three models here, and `llama3.2:3b`'s JSON reliability recovers from 91% (run 1) to 100% (run 2) now that no calls are timing out. **Verdict agreement with run 1:** of all 96 (model, case, run) results, only one raw verdict differs — `llama3.2:3b` case 1, run 2 (`PARSE_FAIL` in run 1 → `LEGITIMATE` in run 2), an artifact of the run-1 timeout/retry, not a model behavior change; it doesn't move that case out of its FAIL bucket (expected `FALSE_POSITIVE`, got `LEGITIMATE` either way). Every other cell is byte-identical between the two runs, so accuracy is fully reproduced and only latency cleaned up, exactly as expected.

## Results table

| Model | FP-catch % | TP-preserve % | Overall % | JSON reliability % | Retries | Env timeouts | Median latency (s) | p95 latency (s) | Latency outliers excluded | RAM (MB) |
|---|---|---|---|---|---|---|---|---|---|---|
| `llama3.2:3b` | 12% (1/8) | 100% (8/8) | 56% | 100% | 0 | 0 | 2.66 | 4.39 | 0 | 2442.9 |
| `qwen2.5:3b` | 88% (7/8) | 88% (7/8) | 88% | 100% | 0 | 0 | 2.65 | 4.40 | 0 | 2063.9 |
| `phi3.5:latest` | 0% (0/8) | 88% (7/8) | 44% | 88% | 2 | 0 | 6.70 | 13.81 | 0 | 3680.8 |

FP-catch % = fraction of the 8 FALSE_POSITIVE cases where **both** runs correctly said FALSE_POSITIVE. TP-preserve % = same, for the 8 LEGITIMATE cases — this is the more important number, since a judge that kills real detections is worse than one that lets a few false positives through unverified. "Env timeouts" = number of (case, run) calls where at least one attempt hit a transport-level timeout/connection error (counted separately from JSON parse failures, which fall under JSON reliability).

## Per-case verdict matrix

| Case | Expected | llama3.2:3b | qwen2.5:3b | phi3.5:latest |
|---|---|---|---|---|
| 1. Ukrainian passport number matches the 2- | FALSE_POSITIVE | FAIL (LEGITIMATE/LEGITIMATE) | PASS (FALSE_POSITIVE/FALSE_POSITIVE) | FAIL (LEGITIMATE/LEGITIMATE) |
| 2. Aadhaar reference number happens to sati | FALSE_POSITIVE | FAIL (LEGITIMATE/LEGITIMATE) | FAIL (LEGITIMATE/LEGITIMATE) | FAIL (LEGITIMATE/LEGITIMATE) |
| 3. Indian (Maharashtra) driver's licence nu | FALSE_POSITIVE | FAIL (LEGITIMATE/LEGITIMATE) | PASS (FALSE_POSITIVE/FALSE_POSITIVE) | FAIL (LEGITIMATE/LEGITIMATE) |
| 4. OCR misread of "Email" as "Emall" tagged | FALSE_POSITIVE | PASS (FALSE_POSITIVE/FALSE_POSITIVE) | PASS (FALSE_POSITIVE/FALSE_POSITIVE) | FAIL (LEGITIMATE/LEGITIMATE) |
| 5. GLiNER tags the bare word "employee" as  | FALSE_POSITIVE | FAIL (LEGITIMATE/LEGITIMATE) | PASS (FALSE_POSITIVE/FALSE_POSITIVE) | FAIL (LEGITIMATE/LEGITIMATE) |
| 6. Regex misreads a SOCIALNUM (SSN-shaped)  | FALSE_POSITIVE | FAIL (LEGITIMATE/LEGITIMATE) | PASS (FALSE_POSITIVE/FALSE_POSITIVE) | FAIL (LEGITIMATE/LEGITIMATE) |
| 7. Bare 10-digit SOCIALNUM swept into drive | FALSE_POSITIVE | FAIL (LEGITIMATE/LEGITIMATE) | PASS (FALSE_POSITIVE/FALSE_POSITIVE) | FAIL (LEGITIMATE/LEGITIMATE) |
| 8. A bare domain name gets tagged as an org | FALSE_POSITIVE | FAIL (LEGITIMATE/LEGITIMATE) | PASS (FALSE_POSITIVE/FALSE_POSITIVE) | FAIL (LEGITIMATE/LEGITIMATE) |
| 9. A real SIN with explicit keyword context | LEGITIMATE | PASS (LEGITIMATE/LEGITIMATE) | FAIL (FALSE_POSITIVE/FALSE_POSITIVE) | PASS (LEGITIMATE/LEGITIMATE) |
| 10. A real person name in an unambiguous aut | LEGITIMATE | PASS (LEGITIMATE/LEGITIMATE) | PASS (LEGITIMATE/LEGITIMATE) | PASS (LEGITIMATE/LEGITIMATE) |
| 11. A real organization name in the same aut | LEGITIMATE | PASS (LEGITIMATE/LEGITIMATE) | PASS (LEGITIMATE/LEGITIMATE) | FAIL (PARSE_FAIL/PARSE_FAIL) |
| 12. A real email address in ordinary prose | LEGITIMATE | PASS (LEGITIMATE/LEGITIMATE) | PASS (LEGITIMATE/LEGITIMATE) | PASS (LEGITIMATE/LEGITIMATE) |
| 13. A real formatted phone number with an ex | LEGITIMATE | PASS (LEGITIMATE/LEGITIMATE) | PASS (LEGITIMATE/LEGITIMATE) | PASS (LEGITIMATE/LEGITIMATE) |
| 14. A real (Luhn-valid) credit card number w | LEGITIMATE | PASS (LEGITIMATE/LEGITIMATE) | PASS (LEGITIMATE/LEGITIMATE) | PASS (LEGITIMATE/LEGITIMATE) |
| 15. A real person name in meeting-notes cont | LEGITIMATE | PASS (LEGITIMATE/LEGITIMATE) | PASS (LEGITIMATE/LEGITIMATE) | PASS (LEGITIMATE/LEGITIMATE) |
| 16. A real, checksum-valid Ontario health ca | LEGITIMATE | PASS (LEGITIMATE/LEGITIMATE) | PASS (LEGITIMATE/LEGITIMATE) | PASS (LEGITIMATE/LEGITIMATE) |

## Hallucinated justifications

None detected by the watch-phrase heuristic (cases 1 and 2 were checked for invented checksum/format claims that don't apply to passport or Aadhaar-collision matches).

## Recommendation

**`qwen2.5:3b` is confirmed as the choice for the verification layer**, now on
reproducible data: 88% FP-catch (7/8), 88% TP-preserve (7/8), 100% JSON reliability,
zero retries and zero env timeouts, and the fastest median latency of the three (2.65s,
effectively tied with llama3.2:3b's 2.66s) once environment noise is removed. The other
two candidates fail for the same opposite reasons as run 1, and the clean environment
only sharpens the contrast:

- **`llama3.2:3b`** still preserves every true positive (100%) but catches only 1 of 8
  false positives (12%) — it rubber-stamps `LEGITIMATE` almost regardless of input, which
  is disqualifying for a verifier regardless of its now-perfect 100% JSON reliability.
- **`phi3.5`** remains the worst on every axis that matters: 0% FP-catch, a repeatable
  total parse failure on case 11, and now unambiguously the slowest (6.70s median, 13.81s
  p95 — both roughly 2.5x qwen's, with no environment noise left to blame it on) and
  heaviest (3.68GB RAM vs. qwen's 2.06GB).

The one open issue carried over from run 1 is unchanged and reproducible: qwen2.5:3b
flips case 9 (a real SIN with explicit keyword context, expected `LEGITIMATE`) to
`FALSE_POSITIVE` on both runs, both times. Since a judge that kills real detections is
worse than one that lets a few false positives through, this is the one thing to fix
before wiring qwen2.5:3b into the verification layer — a system-prompt tweak or a rule
that the layer never downgrades `HIGH`-risk checksum/keyword-backed findings (SIN, health
card) without corroborating evidence. Case 2 (Aadhaar/OHIP collision) is likewise still
missed by all three models on both runs and remains a good few-shot-example candidate
rather than a strike against qwen specifically.

**Next steps:** wire `qwen2.5:3b` into the verification layer with `format: "json"`
layered on top as a safety net (withheld here so JSON reliability measured real
instruction-following), fix the case 9 regression, and spot-check with a smaller re-run
after the prompt change rather than a full 96-call benchmark.

## Prompt v2 attempt (reverted)

`qwen2.5:3b` was wired into `detectors/llm_verifier.py` as the verification layer
(demote-only, routing passport/drivers_license/gliner/non-checksum keyword_context
findings — see that module for the full rationale). As part of that work, a one-line
addition to `SYSTEM_PROMPT` was attempted to fix case 9 above:

> "If the value's format or checksum has already been validated by the scanner, do not
> dispute its validity; judge FALSE_POSITIVE only when the surrounding context
> contradicts the claimed type (for example, the value appears inside a foreign
> country's document or is labeled as a different ID type)."

`benchmark.py` gained a `--prompt-from {benchmark,verifier}` flag (default `benchmark`,
i.e. this file's own prompt is unchanged) to regression-test a candidate prompt from
`detectors/llm_verifier.py` against these same 16 cases without touching the module's
own default. A `qwen2.5:3b`-only rerun (2 runs/case, same cases, written to a scratch
results/raw-results path so it wouldn't clobber this file or `raw_results.jsonl`) gave:

| Model | FP-catch % | TP-preserve % | Overall % | JSON reliability % | Median latency (s) |
|---|---|---|---|---|---|
| `qwen2.5:3b` (prompt v2) | 100% (8/8) | 62% (5/8) | 81% | 100% | 2.28 |

**Verdict: regression, reverted.** Case 9 did **not** flip to `LEGITIMATE` as intended,
and the same instruction newly broke two previously-correct `LEGITIMATE` cases:

| Case | Expected | Prompt v1 (above) | Prompt v2 |
|---|---|---|---|
| 9. Real SIN, explicit keyword context | LEGITIMATE | FALSE_POSITIVE (known bug) | **FALSE_POSITIVE (unchanged)** |
| 12. Real email in ordinary prose | LEGITIMATE | LEGITIMATE | **FALSE_POSITIVE (new regression)** |
| 16. Real, checksum-valid OHIP card | LEGITIMATE | LEGITIMATE | **FALSE_POSITIVE (new regression)** |

The model's own stated reasons for the new failures are confused rather than
principled — e.g. case 16 ("The value appears to be a Canadian health card number, but
is found in an American context" — the context is unambiguously a Canadian OHIP intake
form) and case 12 ("The context suggests this is an email address for a person, not a
contact email" — a self-contradiction). Telling the model to trust checksum-validated
values appears to have made it *more* willing to manufacture a contradiction to justify
`FALSE_POSITIVE`, not less — the opposite of the intent.

The instruction was reverted; `detectors/llm_verifier.py`'s `SYSTEM_PROMPT` is
byte-identical to this file's again. Case 9 remains open, documented in
`detectors/llm_verifier.py` as a known, unresolved model limitation of the
`keyword_context` routing rather than something fixed by this prompt. Future attempts
should treat this as a fresh prompt-design problem rather than a one-line patch, and
re-run the same `--prompt-from verifier` regression check (small, 16-case) before
touching the live prompt.
