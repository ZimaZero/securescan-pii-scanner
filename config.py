# config.py
"""
Central configuration constants for the PII Scanner.
Used to avoid circular imports between scanner.py and discovery.py.
"""

import os

# Single source of truth for the version shown in the GUI header and the
# HTML report footer (report provenance). Bump this when shipping a release;
# settings below reflect the current shipped paths.
SECURESCAN_VERSION = "2.4.1"

# Default worker threads for multithreaded scanning.
# To adjust thread count, modify this single value.
#
# Full-corpus measurements favored 4 workers for the text-heavy workload that
# dominates normal use, with materially lower RAM pressure on the shared VM.
# OCR remains independently bounded by DEFAULT_OCR_WORKERS below.
DEFAULT_MAX_WORKERS = 4

# Separate, tighter cap on how many files may run OCR (PaddleOCR) at the
# same time, regardless of DEFAULT_MAX_WORKERS/max_workers. Enforced via a
# semaphore in discovery.py around image/PDF extraction — see the same
# measured table above. Adjust with scan_folder(ocr_workers=...).
DEFAULT_OCR_WORKERS = 4

# SECURESCAN_PADDLEOCR_DEVICE overrides the PaddleOCR inference device (e.g.
# "gpu:0" in the GPU container — see docker-compose.yml). Unset/bare-host
# runs are unaffected: the default is byte-identical to the previous
# hardcoded "cpu" value.
PADDLEOCR_DEVICE = os.environ.get("SECURESCAN_PADDLEOCR_DEVICE", "cpu")

# ---------------------------------------------------------------------------
# Console noise suppression escape hatch
# ---------------------------------------------------------------------------
# SECURESCAN_VERBOSE=1 (any value other than unset/""/"0") restores every
# third-party log line this codebase otherwise suppresses for a clean scan
# console (ONNX Runtime CUDA advisories, Paddle's "No ccache found" warning,
# PaddleX model-loading chatter — see docs/evidence/console_noise_suppression.md).
# Default off, so bare-host/unset-env-var behavior is the quiet path; every
# suppression site below must check this flag rather than suppressing
# unconditionally.
SECURESCAN_VERBOSE = os.environ.get("SECURESCAN_VERBOSE", "") not in ("", "0")

# ---------------------------------------------------------------------------
# GLiNER file-type gating
# ---------------------------------------------------------------------------
# The GLiNER layer performs semantic NER (person/org/location/date). It adds
# little value on code and structured/machine-generated files, where names and
# organizations are rare and false positives are common — while it is the
# slowest detection layer. Skip GLiNER for these extensions.
#
# All other detection layers (regex/keyword/secrets/health/passport/DL) still
# run on ALL files, so emails, IPs, and credentials in code/logs are still
# detected. GLiNER DOES run on document-like content (.txt, .md, .docx, .pdf,
# .xlsx, and all image/OCR types) — this list is genuinely code/structured
# formats only. Semantic NER remains enabled for document and image formats.
#
# Adjust this list to change which file types skip semantic NER.
GLINER_SKIP_EXTENSIONS = {
    ".py",
    ".js",
    ".json",
    ".log",
    ".csv",
    ".xml",
    ".yml",
    ".yaml",
}

# Inference backend only; chunking, routing, taxonomy, and confidence filtering
# are identical across both paths. ONNX is the benchmark-ratified default.
GLINER_BACKEND = "onnx"  # "onnx" | "torch"
GLINER_ONNX_THREADS = 2

# SECURESCAN_GLINER_DEVICE overrides the ONNX Runtime execution provider for
# GLiNER's session ("cuda" in the GPU container — see docker-compose.yml;
# mirrors the SECURESCAN_PADDLEOCR_DEVICE pattern above). Unset/bare-host/
# CPU-container runs default to the CPU provider. The CUDA provider is safe
# only because
# gliner_detector.py's CUDA session swap wraps it with a process-wide lock —
# concurrent CUDAExecutionProvider Run() calls under this codebase's normal
# file-level threading were measured to silently corrupt findings. Never use
# "cuda" in an environment that bypasses
# that lock.
GLINER_DEVICE = os.environ.get("SECURESCAN_GLINER_DEVICE", "cpu")

# ---------------------------------------------------------------------------
# LLM verification layer (qwen2.5:3b judge)
# ---------------------------------------------------------------------------
# Re-checks ambiguous findings (no-checksum formats, semantic NER) against a
# local Ollama model and may demote them to LOW risk. See
# detectors/llm_verifier.py and tests/ollama_benchmark/RESULTS.md for the
# model choice and routing rationale.
LLM_VERIFICATION_ENABLED = False  # opt-in master switch; --verify can enable per run
# SECURESCAN_OLLAMA_HOST overrides where the Ollama client points (e.g. a
# container may need "http://host.docker.internal:11434" to reach a host
# Ollama service). The default targets localhost.
OLLAMA_HOST = os.environ.get("SECURESCAN_OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = "qwen2.5:3b"
OLLAMA_NUM_THREAD = 8  # Caps model threads to limit scheduler contention.
OLLAMA_TIMEOUT_S = 30  # per-call
OLLAMA_NUM_PREDICT = 150

# ---------------------------------------------------------------------------
# GLiNER input size cap
# ---------------------------------------------------------------------------
# Names/organizations recur throughout a real document, so scanning the
# whole file for semantic entities has rapidly diminishing returns — while
# a multi-MB machine-generated text file is the worst case for GLiNER's
# runtime (many chunks) for approximately zero added value (little to no
# real prose/entities in it). Cap the amount of text GLiNER analyzes per
# file; every other detection layer (regex/keyword/secrets/health/passport/
# DL) still runs on the FULL text regardless of this cap.
NER_MAX_CHARS = 150_000
