# Console noise suppression

Scope: suppress third-party console chatter that made scan output noisy,
without ever touching SecureScan's own output (`[+]`, `[i]`, `[!]`, `[✓]`,
the `→` per-file/progress lines, the `===== SCAN SUMMARY =====` block) or
any exception. Every suppression below is gated behind one new escape hatch,
`SECURESCAN_VERBOSE`, and is skipped entirely when it's set.

## Escape hatch

`config.py`:

```python
SECURESCAN_VERBOSE = os.environ.get("SECURESCAN_VERBOSE", "") not in ("", "0")
```

Default off (unset or `"0"`): every suppression below is active, matching the
new quiet-console behavior. `SECURESCAN_VERBOSE=1` (or any other non-`""`,
non-`"0"` value): every suppression below is skipped, restoring the exact
pre-change console output. Nothing in this change is unconditional — each
site below is wrapped in `if not config.SECURESCAN_VERBOSE:`.

## Suppressed messages

### 1. ONNX Runtime `transformer_memcpy` warnings

- **Source**: ONNX Runtime's CUDA execution provider, emitted once per
  Memcpy-node graph transform when GLiNER's CUDA session is built (e.g.
  `47 Memcpy nodes are added to the graph main_graph for
  CUDAExecutionProvider. It might have negative impact on performance...`).
  13 of these fire on this repo's GLiNER graph (one per sub-graph).
- **Why benign**: a performance advisory about the CUDA graph structure, not
  a correctness signal. It fires identically on every run with the CUDA
  provider and does not indicate anything is wrong with a given scan.
- **Suppressed by**: `detectors/gliner_detector.py`'s `_load_onnx_model()`
  sets `session_options.log_severity_level = 3` (ORT's Error threshold) on
  the single `SessionOptions` object that is passed to both the initial CPU
  session (`GLiNER.from_pretrained(..., session_options=session_options)`)
  and the CUDA session built later in `_swap_to_cuda_session()`
  (`onnxruntime.InferenceSession(str(model_path), session_options,
  providers=[...])`) — one object, one setting, both sessions covered.
  Provider selection and the `get_providers()` sanity check right after CUDA
  session construction are untouched; a real CUDA-unavailable failure still
  raises and still prints SecureScan's own
  `[!] CUDA GLiNER session unavailable (...)` line.
- **Restore**: `SECURESCAN_VERBOSE=1`.

### 2. ONNX Runtime `ScatterNDWithAtomicReduction` warning

- **Source**: ONNX Runtime's CUDA `ScatterND` kernel:
  `ScatterND with reduction=='none' only guarantees to be correct if indices
  are not duplicated.` Fires twice per GLiNER load on the CUDA session.
- **This is a CUDA correctness caveat, not a purely cosmetic one.** The
  warning is real: `ScatterND` with `reduction=='none'` is only guaranteed
  correct when indices are not duplicated, and this codebase does not
  independently verify GLiNER's ONNX graph never produces duplicate scatter
  indices for CUDA execution.
- **Why treated as benign here**: the CPU/GPU parity evidence already on
  record for this exact GLiNER CUDA path (see `CLAUDE.md`, "Shipped:
  GPU-accelerated GLiNER...") is the reason to believe the condition this
  warning guards against is not currently occurring: across all six named
  corpora (319 files), CPU and locked-CUDA GLiNER inference produced **0
  structural finding mismatches** — identical type/value/risk/source finding
  sets on every file — with only ordinary float32 rounding-magnitude
  confidence deltas (max observed 0.001436), not the kind of gross
  divergence duplicated-index scatter corruption would produce. That parity
  run is the evidence that this warning is not currently affecting output on
  this codebase's workload — it does not prove the underlying condition can
  never occur on a different input distribution.
- **This is display-suppressed, not resolved.** Setting ORT's log severity
  hides the line; it does not change GLiNER's graph, does not add an
  index-uniqueness check, and does not alter CUDA kernel behavior in any
  way. If GLiNER's exported ONNX graph or onnxruntime's kernel ever changes
  such that duplicate scatter indices really do occur, this suppression
  would hide the only warning ONNX Runtime gives about it. Anyone revisiting
  the CUDA GLiNER path should re-read this note and re-run the six-corpus
  parity comparison rather than assume silence means safety.
- **Suppressed by**: **not** the `session_options.log_severity_level` setting
  above — verified empirically that setting only `session_options.
  log_severity_level = 3` left this warning printing. The message is tagged
  `[W:onnxruntime:Default, ...]`, i.e. emitted through ONNX Runtime's
  process-wide `Default` logger rather than the session-specific one that
  `SessionOptions.log_severity_level` controls. `_load_onnx_model()`
  additionally calls `onnxruntime.set_default_logger_severity(3)`, gated by
  the same `if not config.SECURESCAN_VERBOSE:` block.
- **Restore**: `SECURESCAN_VERBOSE=1`.

### 3. Paddle "No ccache found" `UserWarning`

- **Source**: `paddle.utils.cpp_extension.extension_utils`, warned once the
  first time Paddle's C++ extension machinery is touched:
  `No ccache found. Please be aware that recompiling all source files may be
  required. You can download and install ccache from: ...`
- **Why benign**: purely a build-cache advisory for Paddle's rarely-exercised
  JIT extension compilation path; the image has no ccache installed and
  none is needed for this codebase's use of PaddleOCR (inference only).
- **Suppressed by**: `extractors/image_extractor.py`, module level, following
  the identically shaped precedent already in `gliner_detector.py` for the
  `huggingface_hub` `resume_download` warning — a narrow
  `warnings.filterwarnings("ignore", message=..., category=UserWarning,
  module=...)`, not a blanket `warnings.simplefilter("ignore")`:

  ```python
  warnings.filterwarnings(
      "ignore",
      message=r".*No ccache found.*",
      category=UserWarning,
      module=r"paddle\.utils\.cpp_extension\.extension_utils",
  )
  ```

- **Restore**: `SECURESCAN_VERBOSE=1`.

### 4. PaddleX model-loading chatter (`Creating model: ...`, `Model files already exist...`)

- **Source**: determined empirically, not guessed. Both lines come from
  **`logging.info()` calls through one standard-library logger named
  `"paddlex"`** (`paddlex/utils/logging.py`: `LOGGER_NAME = "paddlex"`,
  `_logger = logging.getLogger(LOGGER_NAME)`) — **not** `print()`:
  - `"Creating model: %s"` — `paddlex/inference/pipelines/base.py:138`
  - `"Model files already exist. Using cached files. ..."` —
    `paddlex/inference/utils/official_models.py:945,957`

  `paddlex/__init__.py` calls `setup_logging()` at import time, which sets
  this logger to `INFO` and attaches a `colorlog` `StreamHandler` (hence the
  green-colored lines in the raw scan output) with `propagate = False`.
  Because `import paddlex` happens transitively and lazily, the first time
  `from paddleocr import PaddleOCR` executes inside
  `_build_paddle_pipeline()`, suppression has to be applied **after** that
  import, not before — setting the level earlier would just be overwritten
  by `paddlex`'s own `setup_logging()` call when the import runs.
- **Why benign**: purely informational progress/cache-status messages about
  which on-disk model files PaddleOCR is loading; not a warning or error.
- **Suppressed by**: `extractors/image_extractor.py`'s
  `_build_paddle_pipeline()`, immediately after the `from paddleocr import
  PaddleOCR` line and before constructing the pipeline:

  ```python
  if not config.SECURESCAN_VERBOSE:
      logging.getLogger("paddlex").setLevel(logging.WARNING)
  ```

  This raises the level only; it does not touch or remove the handler, so
  real `WARNING`/`ERROR`-level PaddleX output still reaches the console.
- **Restore**: `SECURESCAN_VERBOSE=1`.

## What was confirmed NOT to be print()-based stdout redirection

Item 4 above was the one place the task asked to check for `print()` rather
than a logger before proposing any fix, specifically to avoid the trap of
redirecting stdout globally (which would have swallowed SecureScan's own
`print()`-based `[+]`/`[i]`/`[✓]`/`→` output too). Both messages were traced
to source and confirmed to go through Python's standard `logging` module via
one named logger (`"paddlex"`), so a targeted `logger.setLevel()` was used
instead — no stdout/stderr redirection anywhere in this change.

## Verification

All commands run from the repository root per `CLAUDE.md`.

**Format coverage, CPU** — `docker compose run --rm securescan-cpu python
tests/test_format_coverage.py`: 16/16 files fully passed, scores exactly
`79, 0, 91, 79, 0, 87, 78, 78, 78, 78, 78, 78, 80, 80, 83, 79` (file order:
docx_full, docx_no_pii, xlsx_multisheet, pdf_text_layer, pdf_no_pii,
pdf_scanned_2page, image_clean, image_small, image_rotated,
image_rotated_180, image_rotated_270, image_exif_rotated, data.csv,
data.json, email_notice, deck_summary).

**Format coverage, GPU overlay** —
`docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm
securescan-gpu python tests/test_format_coverage.py`: identical, 16/16,
same score list, same order.

**All 33 `tests/test_*.py` modules** — CPU
(`docker compose run --rm securescan-cpu python tests/test_<name>.py` for
each) and the GPU overlay
(`docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm
securescan-gpu python tests/test_<name>.py`) both ran all 33 files
end-to-end (verified by diffing the set of executed filenames against
`ls tests/test_*.py`) with **zero failures on either container** — every
suite's own pass/fail table showed 0 failed, and a scan of both full run
logs for stray "fail"/"error"/"traceback"/"exception" tokens outside
expected test-label text and intentional-failure fixtures (e.g.
`test_backend_faults.py`'s injected-fault cases, `extraction_failed`
status) came back empty. No suite's pass count or scored value changed as a
result of this work.

**Before/after console output of a real GPU scan of `tests/format_data`**
(`docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm
securescan-gpu python scanner.py --path tests/format_data --no-open`,
18/18 files scanned, 0 extraction failures, identical score set before and
after):

Before (excerpt — full raw capture also on record for this session):

```
[*] Loading GLiNER NER engine (urchade/gliner_medium-v2.1, backend=onnx, one-time)...
2026-08-19 20:04:35.493374192 [W:onnxruntime:, transformer_memcpy.cc:111 ApplyImpl] 47 Memcpy nodes are added to the graph main_graph for CUDAExecutionProvider. It might have negative impact on performance (including unable to run CUDA graph). Set session_options.log_severity_level=1 to see the detail logs before this message.
... (12 more transformer_memcpy lines, one per sub-graph) ...
2026-08-19 20:04:35.780097239 [W:onnxruntime:Default, scatter_nd.h:50 ScatterNDWithAtomicReduction] ScatterND with reduction=='none' only guarantees to be correct if indices are not duplicated.
2026-08-19 20:04:35.780170134 [W:onnxruntime:Default, scatter_nd.h:50 ScatterNDWithAtomicReduction] ScatterND with reduction=='none' only guarantees to be correct if indices are not duplicated.
[✓] GLiNER ready
  → 3/18 files (16%) | 0.6 files/sec | ETA: 0m ... Creating model: ('PP-LCNet_x1_0_textline_ori', None, None)
Model files already exist. Using cached files. To redownload, please delete the directory manually: `/root/.cache/securescan/paddleocr/official_models/PP-LCNet_x1_0_textline_ori`.
/opt/venv/lib/python3.13/site-packages/paddle/utils/cpp_extension/extension_utils.py:712: UserWarning: No ccache found. Please be aware that recompiling all source files may be required. You can download and install ccache from: https://github.com/ccache/ccache/blob/master/doc/INSTALL.md
  warnings.warn(warning_message)
Creating model: ('PP-OCRv5_mobile_det', None, None)
Model files already exist. Using cached files. To redownload, please delete the directory manually: `/root/.cache/securescan/paddleocr/official_models/PP-OCRv5_mobile_det`.
Creating model: ('en_PP-OCRv5_mobile_rec', None, None)
Model files already exist. Using cached files. To redownload, please delete the directory manually: `/root/.cache/securescan/paddleocr/official_models/en_PP-OCRv5_mobile_rec`.
  → 7/18 files (38%) | 0.9 files/sec | ETA: 0m ...
```

After:

```
[*] Loading GLiNER NER engine (urchade/gliner_medium-v2.1, backend=onnx, one-time)...
[✓] GLiNER ready
  → 3/18 files (16%) | 0.8 files/sec | ETA: 0m  → 4/18 files (22%) | 1.0 files/sec | ETA: 0m  → 5/18 files (27%) | 1.2 files/sec | ETA: 0m  → 6/18 files (33%) | 1.5 files/sec | ETA: 0m  → 7/18 files (38%) | 1.2 files/sec | ETA: 0m  → 8/18 files (44%) | 1.3 files/sec | ETA: 0m  → 9/18 files (50%) | 1.5 files/sec | ETA: 0m  → 10/18 files (55%) | 1.6 files/sec | ETA: 0m  → 11/18 files (61%) | 1.8 files/sec | ETA: 0m  → 12/18 files (66%) | 1.9 files/sec | ETA: 0m  [OCR] Page 1/2 of pdf_scanned_2page.pdf
  → 13/18 files (72%) | 2.1 files/sec | ETA: 0m  → 14/18 files (77%) | 2.2 files/sec | ETA: 0m  → 15/18 files (83%) | 2.3 files/sec | ETA: 0m  → 16/18 files (88%) | 2.5 files/sec | ETA: 0m  → 17/18 files (94%) | 2.6 files/sec | ETA: 0m  [OCR] Page 2/2 of pdf_scanned_2page.pdf
  [i] Applied OCR to 2/2 pages
  → 18/18 files (100%) | 2.4 files/sec | ETA: 0m

[i] 18 files scanned, 0 failed, 0 skipped → 17 contained PII.

[i] Peak memory: 2,840 MB · 4 workers · OCR GPU:0 · GLiNER CUDA · no network (peak process CPU 118% — 100% is one core; multi-threaded scans commonly exceed that)
```

Every third-party line (ONNX Runtime warnings, Paddle's ccache warning,
PaddleX's `Creating model:`/`Model files already exist...` lines) is gone
from the after console; every SecureScan-native `[+]`/`[i]`/`[✓]`/`→` line
and the scan summary are unchanged and in the same relative positions.

**`SECURESCAN_VERBOSE=1` restores the old output** — confirmed directly:
`docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm
-e SECURESCAN_VERBOSE=1 securescan-gpu python scanner.py --path
tests/format_data --no-open` reproduces all 22 previously-suppressed lines
(13 `transformer_memcpy` + 2 `ScatterNDWithAtomicReduction` + 1 ccache + 6
PaddleX `Creating model:`/`Model files already exist...` lines across the
three models PaddleOCR loads) byte-for-byte, in the same run that otherwise
scans identically.
