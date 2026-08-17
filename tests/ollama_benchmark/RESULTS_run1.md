# Ollama Verification-Judge Benchmark Results

Benchmarks `llama3.2:3b`, `qwen2.5:3b`, and `phi3.5` as candidate judges for the optional Ollama verification layer. Each model received the same 16 cases (8 expected FALSE_POSITIVE and 8 expected LEGITIMATE) twice, with one model resident in RAM at a time and unloaded (`keep_alive: 0`) before the next model. `format: "json"` was deliberately omitted so JSON reliability measures instruction-following rather than a forced grammar.

**Environment note:** this box intermittently froze background/foreground processes for tens of seconds up to several hours during benchmarking (observed via `ollama`'s own server logs showing multi-hour gaps between prompt processing and completion, with CPU otherwise idle) — a sandbox/VM scheduling artifact, not real model inference speed. Any single measured latency over 300s is excluded from the median/p95 figures below as environment noise (the call's verdict/JSON-parse outcome still counts normally); the exclusion count is reported per model. Treat latency numbers as directional, not authoritative wall-clock guarantees.

**Method note:** every request across the whole run used `num_thread: 6` in the Ollama call options, capped deliberately to avoid oversubscribing the VM's vCPUs — this did not change. The VM's vCPU allocation did change mid-run: `llama3.2:3b` and most of `qwen2.5:3b` ran under an 8-vCPU allocation and hit a hard VM freeze (the resumable `raw_results.jsonl` log took a corrupted trailing write — a null-byte line consistent with an unclean kill — which was stripped before resuming; no valid records were lost). The VM was then given more vCPUs (18) and more disk, and the remainder of `qwen2.5:3b` plus all of `phi3.5` completed cleanly under that configuration without further freezes. Latency figures above are therefore not from a single uniform hardware configuration; combined with the environment note above, treat them as directional only. Accuracy/reliability figures (FP-catch, TP-preserve, JSON reliability) are unaffected by vCPU count.

## Results table

| Model | FP-catch % | TP-preserve % | Overall % | JSON reliability % | Retries | Env timeouts | Median latency (s) | p95 latency (s) | Latency outliers excluded | RAM (MB) |
|---|---|---|---|---|---|---|---|---|---|---|
| `llama3.2:3b` | 12% (1/8) | 100% (8/8) | 56% | 91% | 2 | 2 | 2.44 | 65.25 | 5 | 2442.9 |
| `qwen2.5:3b` | 88% (7/8) | 88% (7/8) | 88% | 100% | 0 | 0 | 3.26 | 65.38 | 0 | 2063.9 |
| `phi3.5:latest` | 0% (0/8) | 88% (7/8) | 44% | 88% | 2 | 0 | 7.34 | 62.76 | 2 | 3680.8 |

FP-catch % = fraction of the 8 FALSE_POSITIVE cases where **both** runs correctly said FALSE_POSITIVE. TP-preserve % = same, for the 8 LEGITIMATE cases — this is the more important number, since a judge that kills real detections is worse than one that lets a few false positives through unverified. "Env timeouts" = number of (case, run) calls where at least one attempt hit a transport-level timeout/connection error (counted separately from JSON parse failures, which fall under JSON reliability).

## Per-case verdict matrix

| Case | Expected | llama3.2:3b | qwen2.5:3b | phi3.5:latest |
|---|---|---|---|---|
| 1. Ukrainian passport number matches the 2- | FALSE_POSITIVE | FAIL (LEGITIMATE/PARSE_FAIL) | PASS (FALSE_POSITIVE/FALSE_POSITIVE) | FAIL (LEGITIMATE/LEGITIMATE) |
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

**`qwen2.5:3b` is the clear choice for the verification layer.** It's the only model that
actually does the job: 88% FP-catch (7/8) alongside 88% TP-preserve (7/8), 100% JSON
reliability with zero retries and zero env timeouts across all 32 calls. The other two
candidates fail for opposite reasons:

- **`llama3.2:3b`** preserves every true positive (100%) but only catches 1 of 8
  documented false positives (12%) — it essentially rubber-stamps everything
  `LEGITIMATE`, which makes it useless as a verifier: a judge that never disagrees adds
  latency and zero value.
- **`phi3.5`** is worse in the opposite direction on top of being slower (median 7.34s vs.
  qwen's 3.26s) and heavier (3.68GB RAM vs. qwen's 2.06GB): 0% FP-catch (misses all 8),
  a total parse failure on case 11 (both runs), and the highest JSON-reliability failure
  rate of the three (88%).

**One finding to fix before shipping qwen2.5:3b as the judge:** it flips case 9 — a real
SIN with explicit keyword context, expected `LEGITIMATE` — to `FALSE_POSITIVE` on both
runs. Per the framing above, TP-preserve matters more than FP-catch (a judge that kills
real detections is worse than one that lets some false positives through unverified), so
this is the one regression worth chasing: either a system-prompt tweak emphasizing that
keyword-context-backed matches should default to `LEGITIMATE` absent a concrete reason to
doubt them, or a rule that the verification layer never downgrades `HIGH`-risk
regex/checksum-backed findings (SIN, health card) without corroborating evidence in the
model's own reasoning.

Also note: none of the three models caught case 2 (the Aadhaar/OHIP-checksum collision) —
all three said `LEGITIMATE`. This is the hardest case in the set (it requires knowing an
Aadhaar number's format well enough to distinguish it from a Canadian health card, which
none of these general-purpose 3B-class models appear to know), so it's a candidate for a
few-shot example in the system prompt rather than a sign qwen2.5:3b is unfit.

**Suggested next steps:** wire `qwen2.5:3b` into the verification layer with
`format: "json"` added as a safety net (deliberately withheld from this benchmark so JSON
reliability measured real instruction-following — production doesn't need that
constraint), address the case 9 regression via prompt tuning, and re-run this same
benchmark script afterward to confirm TP-preserve returns to 100% without regressing
FP-catch.
