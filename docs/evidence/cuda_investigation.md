# CUDA/GPU investigation: rejections, adoptions, and the corrected record

Compiled 2026-08-16 from repository history, `README.md`, and the current
implementation. No new experiments were run for this summary. Each claim
below cites a file or commit. Unsupported quantities are identified as gaps.

**Two important scope notes:**

1. The words "CUDA" and "GPU" cover **two independent detectors** in this
   codebase — PaddleOCR (image/PDF text extraction) and GLiNER (semantic
   NER) — with separate adoption histories, separate evidence, and separate
   "no GPU available at investigation time" constraints. Treat the later
   GLiNER GPU work separately from the PaddleOCR investigation.
2. The raw probe scripts referenced by the historical record
   (`/tmp/containment_measurement.md`, `/tmp/containment_narrowing.md`, the
   standalone ONNX Runtime CUDA-corruption repro, the disposable INT8
   benchmark) no longer exist on disk. Temporary probe artifacts are not
   durable evidence. The summary therefore relies on committed measurements,
   `README.md`, and repository history rather than re-running those scripts.

---

## 1. The four rejections

### 1.1 INT8 quantization (GLiNER ONNX backend)

**Rejected.** Source: `README.md`, "ONNX backend adoption" (still current,
unchanged since commit `002ac43`, 2026-08-12):

> The FP32 ONNX build was adopted only after producing identical (file,
> type, value, span) finding sets across three corpora. An INT8 quantized
> variant was 4× faster and produced **zero findings** — caught and
> disqualified by the exact-comparison rule.

This predates the GPU work entirely — it's a CPU-only ONNX precision
choice, not a GPU decision — but it is the origin of the "byte-identical
output is the adoption bar" rule that the later, corrected CUDA rationale
(§3) explicitly invokes as the standard CUDA/GPU adoption would have to
clear. INT8 failed that bar outright (0 findings instead of the expected
set); no GPU/CUDA variant has ever been measured against it, a distinction
§3 makes explicit ("no such test has been run").

### 1.2 Span-containment reconciliation

**Rejected — twice.** Source: commit `a3f92d7`:

> The blanket rule ("when one finding's source span strictly contains
> another, the outer finding wins regardless of source priority") was
> measured twice and disqualified twice; the probe reports are
> `/tmp/containment_measurement.md` and `/tmp/containment_narrowing.md`.
> Across the measured corpora, 4,839 containment pairs exist, and 26 Enron
> cases would discard a correct contained finding in favor of an incorrect
> containing one. A narrowed measurement found that the Nova Scotia case is
> descriptively separable using at most two properties, but the most
> natural two-property separator exposes a further Enron failure: a false
> Luhn-passing credit-card span suppresses a correct contained phone
> number.

Committed by `a3f92d7` ("docs: park span-containment reconciliation",
2026-07-25). This is not GPU-related; it belongs to the detector-precedent
track. Its underlying temporary probe reports are no longer available.

### 1.3 Card-oriented OCR preprocessing

**Rejected.** Source: the committed PaddleOCR replacement record:

> Three rejected approaches are recorded permanently: (1) card-oriented
> Tesseract preprocessing recovered 0/22 TARGET cases and broke 20/24 GUARD
> cases; (2) pre-OCR card-versus-document routing has no reliable signal;
> and (3) CUDA was not adopted. [see §3 for the corrected wording of (3)]

Also not a GPU decision on its own — a preprocessing-pipeline rejection
that happens to sit in the same three-item list as the original (later
corrected) CUDA rejection. The 0/22 recovery against 20/24 regressions is an
unambiguous net-negative result; no further mitigation was attempted.

### 1.4 GLiNER CUDA, pre-lock (unlocked concurrent CUDAExecutionProvider)

**Rejected in its original (unlocked) form; the corrected, locked form was
later adopted — see §2.2.** This is the one genuine CUDA-specific rejection
in the whole set, and it long predates the "was not adopted" line in §1.3:
those wordings covered a period during which no GPU was even available to
test against (see §3). This rejection is different — it happened *after* a
GPU became available, during the work that shipped the locked GPU GLiNER
session in commit `7025fca` (2026-08-15):

> A prior investigation measured that onnxruntime's CUDAExecutionProvider
> silently corrupts output under this codebase's normal file-level
> concurrent inference: 10-13 of 64 concurrent calls returned
> garbage-magnitude logits (max abs diff up to ~3×10⁷ — near-100%-confidence
> findings on random tokens, e.g. every word in a file tagged `entity.date`
> at ~1.0 confidence) in a minimal onnxruntime-only repro built from real
> captured input tensors with zero GLiNER code involved, plus an
> intermittent hard crash (`terminate called recursively`).

See §4 for the standalone repro's own characteristics, §5 for the
hypotheses this result eliminates, and §6 for the fixes tried before the
lock was adopted.

---

## 2. The two adoptions

### 2.1 PaddleOCR GPU

**Adopted**, via `SECURESCAN_PADDLEOCR_DEVICE` (`config.py`), wired to
`gpu:0` on the `securescan-gpu` compose service by commit `67c511b` ("Add
configurable PaddleOCR device, GPU-enabled on securescan-gpu service",
2026-08-14). The commit's diffstat is `config.py`, `docker-compose.yml`,
`extractors/image_extractor.py`, `tests/test_image_extractor_confidence.py`
— device-selection plumbing, not a byte-identical comparison — and its
commit message carries no evidence body.

**This adoption is evidenced far more thinly than §2.2.** Unlike GLiNER GPU
(six-corpus, 319-file, byte-identical-or-quantified comparison — see §2.2),
the repository's record contains no PaddleOCR CPU-vs-GPU output-parity
measurement, no throughput benchmark, and no corpus-level finding-set
comparison. `PADDLEOCR_DEVICE` simply threads through to PaddleOCR's own
`device=` constructor argument with every other benchmarked setting
(detector/recognizer/orientation models, `enable_mkldnn=False`, disabled
doc-orientation/unwarping) held fixed. The current device
selection additionally gained a CPU fallback on construction failure (see
`extractors/image_extractor.py`'s `_create_ocr_engine()`) so the GPU
compose service degrades instead of failing outright on a machine with no
GPU. This fallback is infrastructure behavior, not new GPU-vs-CPU output
evidence. Treat PaddleOCR GPU output equivalence as unverified,
not as established the way GLiNER's GPU parity is.

### 2.2 GLiNER GPU with lock

**Adopted**, with the most extensive evidence in this document. Source:
commit `7025fca` (2026-08-15). Summary of the recorded acceptance evidence:

- Six corpora, 319 files total (`tests/stress_data` 174, `tests/canadian_eval_data`
  91, `tests/format_data` 18, `tests/sample_data` 14, `/mnt/demo` 14,
  `tests/external_octopii` 8), PaddleOCR forced to CPU in both runs so the
  comparison isolates GLiNER alone.
- **0 structural mismatches** (identical type/value/risk/source finding
  sets on every file); 217/319 files byte-identical including confidence;
  102/319 carrying only small confidence deltas (max observed 0.001436); 0
  file-level score mismatches; 0 `scan_status` mismatches.
- Wall time: 347.68s GPU vs. 986.99s CPU (2.84×) at 8 workers.
- Explicitly **not** byte-identical, and documented as unable to be made
  so via session configuration — this is the residual CPU/GPU float32
  kernel rounding difference, present identically whether or not GPU calls
  are serialized (confirmed by comparing locked and
  unlocked-but-mitigated runs side by side).

See §7 for the two specific findings the margin analysis names as sitting
close enough to the 0.50 confidence cutoff that this residual gap could
matter.

---

## 3. Correcting the unsupported original CUDA rejection

The correction is preserved directly in repository history.

**Original wording** (present before commit `002ac43`, 2026-08-12 — the
line was already in place when the Docker-migration commit touched this
paragraph, so its introduction predates that commit and the file's own
history before the Docker migration is not preserved verbatim elsewhere;
the wording immediately before correction is preserved in commit `002ac43`):

> (3) CUDA is not a transparent accelerator because CPU/GPU output is not
> guaranteed byte-identical. CPU remains the required/default execution
> path; there is no routing, dual-run, custom cardinal-rotation ladder, or
> CUDA path.

**Corrected wording**, introduced in the same commit (`002ac43`,
"Migrate to Docker with WSL2 GPU support, container-wide host mounts, and
Ollama service", 2026-08-12), and unchanged since:

> (3) CUDA was not adopted. Correction: the previously recorded rationale
> ("CPU/GPU output is not guaranteed byte-identical") is not supported by
> evidence in this repository — the only CUDA comparison on record is the
> ONNX-backend CPU/CUDA 2x2, which found CPU and CUDA produced identical
> results (9/9 with working code, 0/9 with the incomplete artifact, in both
> environments) and identified a missing tokenizer file, not a GPU parity
> failure. The accurate rationale: no GPU is available to the scanner's
> environment, and adoption would require demonstrating byte-identical
> output under the same rule applied to INT8 quantization; no such test has
> been run.

**The "ONNX-backend CPU/CUDA 2×2" referenced in the correction** is commit
`984fd0d` ("Adopt benchmark-ratified ONNX FP32 GLiNER backend",
2026-07-19), whose own commit body contains the 2×2 that the correction
cites:

> The first production export returned zero fixture findings. Torch 2.13
> initially appeared causal, but a CPU/CUDA 2×2 ruled the runtime out:
> benchmark code found 9/9 in both environments while the production
> artifact found 0/9 in both. Artifact ablation isolated the root cause:
>
> ```text
> no tokenizer artifacts:        0/9
> tokenizer.json only:           0/9
> tokenizer_config.json only:    0/9
> both tokenizer artifacts:      9/9
> ```

In other words: the original claim ("CPU/GPU output is not guaranteed
byte-identical") had been used to justify rejecting CUDA outright, but the
*only* CPU/CUDA comparison that had actually been run at that point
(`984fd0d`) was not testing GPU parity at all — it was diagnosing a missing
tokenizer file in an incomplete ONNX export artifact, and it found CPU and
CUDA in agreement (9/9 both, or 0/9 both, depending on artifact
completeness — never a CPU/CUDA split). The byte-identical-parity claim
about GPU specifically had no supporting experiment anywhere in the
repository. The correction replaces it with the honest reason CUDA hadn't
been adopted at that time: no GPU was available to test against, full
stop — and explicitly states the adoption bar that would need clearing
(the same exact-match rule that disqualified INT8, §1.1) without claiming
that bar had already been tested and failed.

**Timeline:** the corrected paragraph is explicit that it is *not*
superseded by the later, separate GLiNER GPU adoption (§2.2, commit
`7025fca`, over three weeks after the correction): "This paragraph is
about PaddleOCR/OCR specifically and is otherwise unchanged by the later
GPU GLiNER work... that is a separate adoption, on a separate detector,
with its own measured byte-identical gap; it does not supersede the 'no
such [INT8-style] test has been run' note here for OCR." As of this
document, PaddleOCR GPU (§2.1) still has no such test — the "no such test
has been run" statement remains true for OCR specifically, three commits
and two days after PaddleOCR GPU was wired up (`67c511b`, one day after
`002ac43`), because that commit added device plumbing, not a parity
measurement.

---

## 4. The standalone ONNX Runtime repro

Recorded in commit `7025fca` as "a minimal onnxruntime-only repro
built from real captured input tensors with zero GLiNER code involved."
The script itself no longer exists (per the note at the top of this
document — disposable `/tmp` probes are not durable), so what
follows is the repro's documented characteristics, not a re-run:

- **Inputs:** real tensors captured from an actual GLiNER inference call
  (not synthetic/random data), so the repro reproduces the exact input
  shapes/values the production code path produces.
- **Code under test:** raw `onnxruntime.InferenceSession` calls with
  `CUDAExecutionProvider` — no `gliner` package import, no
  `detectors/gliner_detector.py` code at all. This is what lets the
  finding be attributed to ONNX Runtime's CUDA execution provider itself
  rather than to this project's code or GLiNER's wrapper (see §5).
- **Concurrency pattern:** the same file-level concurrent inference pattern
  this codebase's `DEFAULT_MAX_WORKERS` threading produces in normal
  operation — not a synthetic stress pattern invented for the repro.
- **Result:** 10-13 of 64 concurrent calls returned corrupted output (max
  absolute difference from the correct value up to ~3×10⁷ — logits at a
  magnitude that turns into near-100%-confidence findings on essentially
  random tokens), plus an intermittent hard crash reported as
  `terminate called recursively`.
- **Control:** the identical concurrency pattern against
  `CPUExecutionProvider` was separately measured safe: 522 file-comparisons
  at 16 threads, zero mismatches.

---

## 5. Hypotheses the repro eliminates

The recorded conclusion states that this is "an
ORT/CUDA-EP concurrency issue, not a bug in this file or in gliner's
wrapper." Unpacked, the repro's design (§4) is built to eliminate three
specific alternative explanations for the corruption. The three-way split
below is a synthesis of that evidence, not a direct quotation:

1. **A bug in this project's own code** (`detectors/gliner_detector.py` or
   its threading/chunking logic) — eliminated by construction: the repro
   contains zero code from this file. Corruption reproduced with only raw
   `onnxruntime` calls, so nothing this codebase does to inputs, chunking,
   or thread management can be the cause.
2. **A bug in `gliner`'s own ORT wrapper** (the `UniEncoderSpanORTModel`
   class this project's session-swap code depends on and version-guards —
   see `gliner_detector.py`'s `_swap_to_cuda_session()`) — eliminated the
   same way: the repro imports no `gliner` code either.
3. **The file-level concurrency pattern itself being inherently unsafe**
   (independent of CUDA) — eliminated by the CPUExecutionProvider control:
   the identical concurrency pattern against CPU, at a higher thread count
   (16 vs. this project's `DEFAULT_MAX_WORKERS = 4`), produced zero
   mismatches across 522 comparisons. If the concurrency pattern itself
   were the problem, CPU should have shown it too.

What remains after eliminating all three supports the recorded conclusion:
the defect is specific to `onnxruntime`'s `CUDAExecutionProvider`
under concurrent `Run()` calls, not to anything above it in the stack.

---

## 6. Fixes tried before the lock

Source: commit `7025fca`.
Four distinct session configurations are named on record:

| Attempt | Result |
|---|---|
| `use_ep_level_unified_stream=1` | Reduced but did **not** eliminate corruption — "still small nonzero mismatches every round, plus one crash under sustained multi-process GPU contention." |
| `enable_mem_pattern=False` | Made things **worse**. |
| `arena_extend_strategy=kSameAsRequested` | **No difference.** |
| Process-wide lock around `session.run()` | **Adopted.** 0/64 mismatches, `max_abs_diff=0.0`, across repeated rounds — "proven correct by construction," and measured to cost no more wall-clock time than the `use_ep_level_unified_stream=1` alternative at this project's real worker counts. |

**Note on this table's count:** the committed record names **four** distinct
session-configuration attempts, not five: three rejected configurations and
the adopted lock. No fifth named attempt appears in repository history.

---

## 7. Margin analysis: the two near-boundary findings

Source: commit `7025fca`, following the byte-identical-gap discussion (§2.2):

> Two real findings across all six corpora sit closer to the GLiNER 0.50
> confidence cutoff than the largest measured delta:
> `edge_cases/very_long_single_line.txt`'s "logistics vendor" (stress_data)
> at 0.5003029-0.5003759 depending on run, margin as small as 0.0003;
> `marketing/team_gamma/project_x/archive/filler_0075.md`'s "Worker"
> (stress_data) at ~0.5007-0.5010, margin ~0.0007. Neither has flipped
> sides of 0.50 in any run captured so far, but a differently-signed
> rounding difference of the measured magnitude could flip either one — a
> real, quantified, low-probability risk, accepted knowingly rather than
> assumed away.

Both findings are in `tests/stress_data`, both are GLiNER `entity.*`
(semantic NER) findings sitting just above `MIN_CONFIDENCE = 0.50`
(`detectors/gliner_detector.py`), and both have been observed only on the
accept side of the cutoff across every run captured so far — the risk is
that the CPU/GPU float32 rounding gap (§2.2's "largest measured delta,"
0.001436) is larger than either finding's margin above the cutoff
(0.0003 and 0.0007 respectively), so a differently-signed rounding
difference on a future run could in principle push either finding below
`MIN_CONFIDENCE` and drop it from GPU output while CPU output keeps it (or
vice versa). This was accepted as a known, quantified, low-probability
risk — not treated as disqualifying, since the alternative (CPU-only,
2.84× slower) has no such risk but also no reproduced instance of the flip
has ever been observed.

---

## Sources referenced in this document

- `README.md` — "ONNX backend adoption" (INT8 quantization).
- Commit `002ac43` — the correction record.
- `git log -1 --format=%B 984fd0d` — the CPU/CUDA 2×2 ablation table.
- `git log -1 --format=%B a3f92d7`, `67c511b`, `7025fca` — commit dates and
  (where present) bodies for the span-containment park, PaddleOCR GPU
  config, and GLiNER GPU lock commits respectively.
- `detectors/gliner_detector.py` — `_swap_to_cuda_session()`,
  `_LockedCUDASession`, `_CUDA_RUN_LOCK`, `MIN_CONFIDENCE`.
- `extractors/image_extractor.py` — `_create_ocr_engine()` device selection
  and CPU fallback; this is not GPU-vs-CPU output evidence.
