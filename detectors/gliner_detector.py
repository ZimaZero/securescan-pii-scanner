#!/usr/bin/env python3
# gliner_detector.py
"""GLiNER semantic-entity detector.

GLiNER-medium provides person, organization, location, and date detection.
The model selection evidence is recorded under ``tests/ner_benchmark/``.

Emits ONLY:  person, organization, location, date
  -> taxonomy: entity.person / entity.organization / entity.location / entity.date

Does not emit email / phone / ssn / credit card / ip / iban / url /
address — those are owned by the regex + checksum layers, and having GLiNER emit
them would create cross-layer collisions.

Output shape mirrors the other detectors:
    { type: [(value, confidence), ...], ... }

Memory discipline: the model is a process-wide SINGLETON (load once, share
across threads) — loading a copy per thread would exhaust RAM.
"""

import os
import threading
import re
import gc
import shutil
from pathlib import Path
from typing import Dict, List, Tuple
import warnings

# Runtime model loading is cache-only. The Docker build bakes the required
# model artifacts into the image seed cache.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

for warning_category in (DeprecationWarning, UserWarning):
    warnings.filterwarnings(
        "ignore",
        message=r".*resume_download.*deprecated.*",
        category=warning_category,
        module=r"huggingface_hub\.utils\._validators",
    )

try:
    from transformers.utils import logging as transformers_logging
    from transformers import AutoTokenizer

    # This advisory is generic to the tokenizer class; identical scan results
    # were verified before/after on the real corpus.
    transformers_logging.set_verbosity_error()
except Exception:
    AutoTokenizer = None

try:
    from gliner import GLiNER
    GLINER_AVAILABLE = True
except Exception:
    GLINER_AVAILABLE = False

try:
    import torch
    TORCH_AVAILABLE = True
except Exception:
    torch = None
    TORCH_AVAILABLE = False

try:
    import onnxruntime  # noqa: F401 - availability gates the ONNX backend
    ONNX_RUNTIME_AVAILABLE = True
except Exception:
    onnxruntime = None
    ONNX_RUNTIME_AVAILABLE = False

import config


# ============================================================
# CONFIGURATION
# ============================================================

MODEL_NAME = "urchade/gliner_medium-v2.1"

# Only semantic entities; the regex/checksum layers own everything structured.
LABELS = ["person", "organization", "location", "date"]

# Drop weak guesses so they don't leak into results.
MIN_CONFIDENCE = 0.50

# Words, characters, and subword tokens are three different quantities.
# GLiNER's internal 384-word truncation limit uses its own words_splitter, so
# the 350-word bound remains mandatory. Native ONNX allocation, however, is
# driven by subword-token count: wrapped base64 made one 350-word chunk about
# 18,700 tokens and OOM-killed the scan. A character cap is not a valid proxy:
# the measured 1,536-char experiment sat below ordinary prose density, changed
# stress from 132 to 239 findings, and cost 60.6% runtime. In contrast, 5,295
# normal 350-word stress chunks measured 350/354/358/376 tokens at
# min/median/p95/max. _chunks() therefore retains the word boundary for normal
# text and applies the tokenizer's 1,024-token limit only when needed.
MAX_WORDS_PER_CHUNK = 350  # margin below the real 384-word limit
MAX_TOKENS_PER_CHUNK = 1024
CHUNK_OVERLAP_WORDS = 50   # so an entity spanning a chunk boundary isn't lost
CHUNK_OVERLAP_TOKENS = (
    MAX_TOKENS_PER_CHUNK * CHUNK_OVERLAP_WORDS // MAX_WORDS_PER_CHUNK
)


# ============================================================
# SINGLETON INSTANCE (prevents RAM explosion under threading)
# ============================================================

_GLOBAL_MODEL = None
_GLOBAL_BACKEND = None
# "cpu" or "cuda" — the execution provider actually resolved for this
# process, which can differ from config.GLINER_DEVICE == "cuda" when the
# CUDA session swap fails and _load_onnx_model() falls back to CPU. None
# until the model has been loaded once (that is, GLiNER has not run in this
# scan) — see active_device().
_GLOBAL_DEVICE = None
_MODEL_LOCK = threading.Lock()
# Incremented once per call that actually reaches a loaded model (see
# detect_entities_gliner()). The singleton above persists across scans in a
# long-lived GUI process, so _GLOBAL_DEVICE alone can't tell a caller whether
# GLiNER ran in THIS scan or is just echoing a stale value from an earlier
# one — discovery.py snapshots run_counter() before a scan and compares it
# after to decide whether active_device() reflects this scan at all.
_RUN_COUNTER = 0
_RUN_COUNTER_LOCK = threading.Lock()
_COUNTING_TOKENIZER = None
# This lock protects only the separately loaded chunk-counting tokenizer.
# GLiNER's model-owned tokenizer and predict_entities() inference path never
# enter it, so file-level inference remains fully concurrent.
_COUNTING_TOKENIZER_LOCK = threading.Lock()

# Serializes onnxruntime session.run() calls when config.GLINER_DEVICE ==
# "cuda". Measured root cause: onnxruntime's CUDAExecutionProvider silently
# corrupted output (near-100%-confidence findings on random tokens, and
# occasional hard exceptions) under this module's normal file-level
# concurrent inference, reproduced in a minimal onnxruntime-only script with
# no GLiNER code involved -- so this is an ORT/CUDA-EP concurrency issue, not
# a bug in this file. CPUExecutionProvider was proven safe under the
# identical concurrency pattern (522 file-comparisons at 16 threads, zero
# mismatches). A process-wide lock around Run() was the only fix proven
# correct by construction (0 mismatches, max_abs_diff=0.0 across repeated
# corpus runs) -- it also cost no more wall-clock time than the alternative
# (use_ep_level_unified_stream=1) at this project's real worker count, and
# unlike that alternative doesn't rely on undocumented ORT internals. Never
# remove this lock without re-proving GPU concurrency safety end to end.
_CUDA_RUN_LOCK = threading.Lock()

ONNX_MODEL_FILENAME = "model.onnx"
ONNX_CACHE_FILENAMES = (
    ONNX_MODEL_FILENAME,
    "gliner_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
)


def _onnx_cache_dir() -> Path:
    """Persistent local cache for the exported FP32 graph."""
    override = os.environ.get("SECURESCAN_GLINER_ONNX_CACHE")
    torch_version = (
        str(getattr(torch, "__version__", "unknown")).split("+", 1)[0]
        if TORCH_AVAILABLE
        else "unavailable"
    )
    return (
        Path(override).expanduser()
        if override
        else Path.home()
        / ".cache"
        / "securescan"
        / f"gliner_onnx-torch{torch_version}"
    )


def _load_torch_model():
    """Load the cached PyTorch model with the exported local tokenizer.

    The upstream GLiNER snapshot does not contain tokenizer files; it points
    at a separate Hugging Face model instead. SecureScan's four-file ONNX
    cache already contains the exact tokenizer and the expanded GLiNER config
    created during export. Make those three small files available beside the
    cached PyTorch weights rather than requiring a second model cache or a
    network request. The expanded config matters because it embeds the
    DeBERTa backbone config that the original Hub snapshot otherwise resolves
    as a separate dependency.
    """
    from huggingface_hub import snapshot_download

    snapshot = Path(snapshot_download(MODEL_NAME, local_files_only=True))
    tokenizer_source = _onnx_cache_dir()
    for filename in (
        "gliner_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
    ):
        source = tokenizer_source / filename
        destination = snapshot / filename
        if not source.is_file():
            raise RuntimeError(
                "PyTorch GLiNER fallback is unavailable: the local ONNX "
                f"cache is missing {filename} ({source})"
            )
        shutil.copy2(source, destination)
    return GLiNER.from_pretrained(str(snapshot), local_files_only=True)


def _prepare_onnx_model(cache_dir: Path) -> Path:
    """Export the cached PyTorch model to a persistent FP32 ONNX graph."""
    model_path = cache_dir / ONNX_MODEL_FILENAME

    def cache_complete():
        return all((cache_dir / name).is_file() for name in ONNX_CACHE_FILENAMES)

    if cache_complete():
        return model_path

    # Linux-only project runtime: the file lock prevents two local GUI/CLI
    # processes from exporting the same ~746 MB graph concurrently.
    import fcntl
    from huggingface_hub import snapshot_download

    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(cache_dir, 0o700)
    lock_path = cache_dir / ".export.lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if cache_complete():
            return model_path

        print("[*] Preparing ONNX model (one-time)...")
        temporary = cache_dir / f".export-{os.getpid()}"
        shutil.rmtree(temporary, ignore_errors=True)
        temporary.mkdir(mode=0o700)
        torch_model = None
        try:
            # local_files_only plus HF_HUB_OFFLINE above is the local-only
            # guarantee for both source weights and tokenizer/config assets.
            snapshot = Path(
                snapshot_download(MODEL_NAME, local_files_only=True)
            )
            shutil.copy2(
                snapshot / "gliner_config.json",
                temporary / "gliner_config.json",
            )
            # Do not call _load_torch_model() here: it expects the exported
            # tokenizer/config artifacts that this function creates. Load
            # directly from the resolved source snapshot when preparing an
            # empty ONNX cache.
            torch_model = GLiNER.from_pretrained(str(snapshot), local_files_only=True)
            exported = torch_model.export_to_onnx(
                temporary,
                onnx_filename=ONNX_MODEL_FILENAME,
                quantize=False,
            )
            exported_path = Path(exported["onnx_path"])
            if exported_path != temporary / ONNX_MODEL_FILENAME:
                os.replace(exported_path, temporary / ONNX_MODEL_FILENAME)
            for filename in ONNX_CACHE_FILENAMES:
                os.replace(temporary / filename, cache_dir / filename)
        finally:
            del torch_model
            gc.collect()
            shutil.rmtree(temporary, ignore_errors=True)
    return model_path


class _LockedCUDASession:
    """onnxruntime.InferenceSession proxy that serializes Run() calls.

    Forwards only the methods gliner.onnx.model.BaseORTModel actually calls
    (get_inputs/get_outputs once at construction, run() per inference) --
    deliberately not a generic __getattr__ passthrough, so any future gliner
    internals that touch a session method this proxy doesn't know about fail
    loudly instead of silently bypassing the lock.
    """

    def __init__(self, session, lock):
        self._session = session
        self._lock = lock

    def get_inputs(self):
        return self._session.get_inputs()

    def get_outputs(self):
        return self._session.get_outputs()

    def get_providers(self):
        return self._session.get_providers()

    def run(self, output_names, input_feed, run_options=None):
        with self._lock:
            return self._session.run(output_names, input_feed, run_options)


def _swap_to_cuda_session(instance, model_path, session_options):
    """Replace the CPU session GLiNER.from_pretrained() built with a locked
    CUDAExecutionProvider session.

    gliner==0.2.27's own from_pretrained() ONNX branch already does exactly
    this (build a providers list, construct InferenceSession, wrap in
    cls.ort_model_class) when map_location="cuda" -- but only after checking
    torch.cuda.is_available(), which the image's inference-only torch build
    reports as False. GLiNER GPU acceleration uses
    onnxruntime-gpu's CUDAExecutionProvider, which
    needs no torch CUDA build at all. Route around that check by loading
    normally first (CPU, map_location defaults to "cpu", so the gate never
    triggers) and swapping the session afterward, reusing gliner's own
    already-selected ORT wrapper class via type(instance.model) rather than
    hardcoding a constructor call.

    VERSION FRAGILITY: gliner.onnx.model.UniEncoderSpanORTModel did not exist
    before gliner 0.2.23 -- the 0.2.22->0.2.23 release (Aug->Nov 2025) renamed
    it from SpanORTModel and restructured the ORT wrapper hierarchy around
    per-architecture classes. The isinstance check below is what actually
    depends on that name; a gliner downgrade below 0.2.23 fails it loudly
    rather than silently applying an assumption that no longer holds.
    requirements.txt currently pins gliner==0.2.27; UniEncoderSpanORTModel's
    body was verified byte-identical from 0.2.23 through 0.2.28.
    """
    from gliner.onnx.model import UniEncoderSpanORTModel

    if not isinstance(instance.model, UniEncoderSpanORTModel):
        raise RuntimeError(
            "expected gliner's UniEncoderSpanORTModel wrapper, got "
            f"{type(instance.model).__name__} -- the model architecture or "
            "installed gliner version changed; refusing to swap in an "
            "unverified CUDA session"
        )

    cuda_session = onnxruntime.InferenceSession(
        str(model_path),
        session_options,
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    if "CUDAExecutionProvider" not in cuda_session.get_providers():
        raise RuntimeError(
            "onnxruntime selected "
            f"{cuda_session.get_providers()} instead of CUDAExecutionProvider"
            " -- is onnxruntime-gpu installed and is a GPU visible to this"
            " container?"
        )

    instance.model = type(instance.model)(
        _LockedCUDASession(cuda_session, _CUDA_RUN_LOCK)
    )
    return instance


def _load_onnx_model():
    """Load (or first prepare) the benchmark-ratified FP32 ONNX backend."""
    global _GLOBAL_DEVICE
    if not ONNX_RUNTIME_AVAILABLE:
        raise RuntimeError("onnxruntime is unavailable")
    cache_dir = _onnx_cache_dir()
    model_path = _prepare_onnx_model(cache_dir)
    session_options = onnxruntime.SessionOptions()
    # Bound ONNX Runtime's internal pools so file-level concurrency does not
    # multiply into all-core inference pools and starve the GUI.
    session_options.intra_op_num_threads = config.GLINER_ONNX_THREADS
    session_options.inter_op_num_threads = config.GLINER_ONNX_THREADS
    instance = GLiNER.from_pretrained(
        str(cache_dir),
        local_files_only=True,
        load_onnx_model=True,
        onnx_model_file=ONNX_MODEL_FILENAME,
        session_options=session_options,
    )
    _GLOBAL_DEVICE = "cpu"
    if config.GLINER_DEVICE == "cuda":
        try:
            instance = _swap_to_cuda_session(instance, model_path, session_options)
            _GLOBAL_DEVICE = "cuda"
        except Exception as exc:
            print(
                f"[!] CUDA GLiNER session unavailable ({type(exc).__name__}: "
                f"{exc}); staying on the CPU ONNX session."
            )
    return instance


def _get_model():
    """Return the shared GLiNER model, loading it once (thread-safe)."""
    global _GLOBAL_MODEL, _GLOBAL_BACKEND, _GLOBAL_DEVICE

    if _GLOBAL_MODEL is not None:
        return _GLOBAL_MODEL

    with _MODEL_LOCK:
        # Double-check in case another thread just created it.
        if _GLOBAL_MODEL is None:
            # File-level concurrency already supplies parallelism. Restrict
            # Torch's intra-op pool to one thread to avoid oversubscription.
            if TORCH_AVAILABLE:
                torch.set_num_threads(1)
            requested_backend = config.GLINER_BACKEND
            print(
                f"[*] Loading GLiNER NER engine "
                f"({MODEL_NAME}, backend={requested_backend}, one-time)..."
            )
            if requested_backend == "torch":
                _GLOBAL_MODEL = _load_torch_model()
                _GLOBAL_BACKEND = "torch"
                # The fallback Torch package is CPU-targeted; CUDA acceleration
                # is provided by the ONNX Runtime GPU path.
                _GLOBAL_DEVICE = "cpu"
            else:
                try:
                    _GLOBAL_MODEL = _load_onnx_model()
                    _GLOBAL_BACKEND = "onnx"
                except Exception as exc:
                    print(
                        f"[!] ONNX GLiNER unavailable ({type(exc).__name__}: "
                        f"{exc}); falling back to PyTorch."
                    )
                    _GLOBAL_MODEL = _load_torch_model()
                    _GLOBAL_BACKEND = "torch"
                    _GLOBAL_DEVICE = "cpu"
            print("[✓] GLiNER ready")

    return _GLOBAL_MODEL


def active_device():
    """Return "cpu"/"cuda" reflecting the execution provider GLiNER actually
    resolved to in this process, or None if the model hasn't been loaded yet
    (i.e. GLiNER hasn't run in this scan). Can read "cpu" even when
    config.GLINER_DEVICE == "cuda" if the CUDA session swap failed and
    _load_onnx_model() fell back — report headers should call this rather
    than echoing the config value, which only reflects intent."""
    return _GLOBAL_DEVICE


def run_counter() -> int:
    """Monotonic count of completed detect_entities_gliner() model calls,
    process-wide. Callers snapshot this before a scan and compare after to
    tell whether GLiNER actually executed during that scan, independent of
    whether the singleton model/device were already resolved by an earlier
    scan."""
    return _RUN_COUNTER


def _get_counting_tokenizer(model=None):
    """Return an isolated tokenizer used only to bound chunk token counts."""
    global _COUNTING_TOKENIZER
    if _COUNTING_TOKENIZER is not None:
        return _COUNTING_TOKENIZER
    with _COUNTING_TOKENIZER_LOCK:
        if _COUNTING_TOKENIZER is None:
            if AutoTokenizer is None:
                raise RuntimeError("Transformers tokenizer support is unavailable")
            model_tokenizer = getattr(
                getattr(model, "data_processor", None),
                "transformer_tokenizer",
                None,
            )
            tokenizer_source = getattr(model_tokenizer, "name_or_path", None)
            if not tokenizer_source:
                tokenizer_source = str(_onnx_cache_dir())
            _COUNTING_TOKENIZER = AutoTokenizer.from_pretrained(
                tokenizer_source,
                local_files_only=True,
            )
    return _COUNTING_TOKENIZER


# ============================================================
# HELPERS
# ============================================================

def _chunks(text: str, model=None):
    """Yield overlapping chunks respecting both word and token limits.

    Measured with the model's own word splitter when available
    (model.data_processor.words_splitter) — the EXACT same word counting
    GLiNER uses internally for its 384-word limit, so this guarantees no
    chunk can trigger the truncation warning. Token counts use a separate
    tokenizer instance so chunking cannot race with concurrent inference.
    Token-bound chunks retain proportional token overlap; word-bound chunks
    retain the existing CHUNK_OVERLAP_WORDS overlap.
    """
    words = None
    if model is not None:
        try:
            words = list(model.data_processor.words_splitter(text))
        except Exception:
            words = None

    if words is None:
        words = [
            (match.group(), match.start(), match.end())
            for match in re.finditer(r"\S+", text)
        ]

    if not words:
        return

    tokenizer = _get_counting_tokenizer(model)

    def token_count(value):
        with _COUNTING_TOKENIZER_LOCK:
            return len(tokenizer.encode(value, add_special_tokens=False))

    if (
        len(words) <= MAX_WORDS_PER_CHUNK
        and token_count(text) <= MAX_TOKENS_PER_CHUNK
    ):
        yield text
        return

    def token_bounded_end(start, candidate_end):
        low, high = start + 1, candidate_end
        while low < high:
            midpoint = (low + high + 1) // 2
            if token_count(text[start:midpoint]) <= MAX_TOKENS_PER_CHUNK:
                low = midpoint
            else:
                high = midpoint - 1
        end = low
        while end > start and token_count(text[start:end]) > MAX_TOKENS_PER_CHUNK:
            end -= 1
        return max(start + 1, end)

    def token_overlap_start(start, end):
        low, high = start + 1, end
        while low < high:
            midpoint = (low + high) // 2
            if token_count(text[midpoint:end]) <= CHUNK_OVERLAP_TOKENS:
                high = midpoint
            else:
                low = midpoint + 1
        return low

    word_index = 0
    n = len(words)
    while word_index < n:
        start = words[word_index][1]
        word_limit_index = min(word_index + MAX_WORDS_PER_CHUNK - 1, n - 1)
        word_end = words[word_limit_index][2]
        candidate = text[start:word_end]

        if token_count(candidate) <= MAX_TOKENS_PER_CHUNK:
            yield candidate
            if word_limit_index == n - 1:
                break
            word_index += MAX_WORDS_PER_CHUNK - CHUNK_OVERLAP_WORDS
            continue

        end = token_bounded_end(start, word_end)
        yield text[start:end]
        if end >= words[-1][2]:
            break

        overlap_start = token_overlap_start(start, end)
        next_word = next(
            (i for i in range(word_index, n) if words[i][2] > overlap_start),
            n,
        )
        if next_word >= n:
            break
        if words[next_word][1] < overlap_start:
            words.insert(
                next_word,
                (
                    text[overlap_start:words[next_word][2]],
                    overlap_start,
                    words[next_word][2],
                ),
            )
            n += 1
            word_index = next_word
        else:
            word_index = max(word_index + 1, next_word)


def _clean(value: str) -> str:
    """Strip multi-line span bleed and surrounding whitespace."""
    return value.split("\n")[0].strip()


# IP-/URL-shaped strings must never be emitted as a semantic entity (GLiNER likes
# to call IPs "locations"). Those are structured PII owned by the regex layer.
_IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_IPV6 = re.compile(r"^(?:[0-9A-Fa-f]{1,4}:){2,}[0-9A-Fa-f]{1,4}$")

# Digit-heavy-with-separators (phone/SIN/SSN/credit-card shape): GLiNER
# regularly mislabels these as "location". Owned by the regex/checksum
# layers, never by semantic NER.
#
# Date-shaped strings (YYYY-MM-DD / DD-MM-YYYY etc) are explicitly excluded:
# unlike phone/SIN/credit-card, dates ARE legitimately GLiNER's job here
# (LABELS includes "date") — an 8-digit ISO date would otherwise trip the
# same "mostly digits" heuristic.
_DATE_SHAPE_RE = re.compile(r"^(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})$")
_SEPARATOR_RE = re.compile(r"[-.\s()]")
_MIN_STRUCTURED_DIGITS = 7
_MIN_DIGIT_RATIO = 0.6  # digits must be the large majority of the token


def _is_digit_heavy(value: str) -> bool:
    if _DATE_SHAPE_RE.match(value):
        return False
    digits = re.sub(r"\D", "", value)
    if len(digits) < _MIN_STRUCTURED_DIGITS:
        return False
    return (len(digits) / max(1, len(value))) >= _MIN_DIGIT_RATIO


# Bare-domain shape (no http(s):// scheme required): GLiNER calls these
# "organization" or "location". Owned by the regex/URL layer.
_TLDS = "com|org|net|ca|io|gov|edu|co|us|uk|info|biz|me"
_BARE_DOMAIN_RE = re.compile(
    r"^(?:[a-zA-Z0-9-]+\.)+(?:" + _TLDS + r")$", re.IGNORECASE
)

# PII field-label words GLiNER sometimes misreads as an entity in their own
# right (e.g. "SIN" tagged as a location). EXACT token match only — a real
# entity that merely CONTAINS one of these as a substring (e.g. a surname
# like "Sinclair") must not be filtered.
_FIELD_LABEL_STOPLIST = {"sin", "ssn", "dob", "phn", "ohip"}


def _is_structured(value: str) -> bool:
    """True if the value is structured PII (owned by the regex/checksum
    layers) or a bare field-label token — not a genuine person/org/location/
    date entity."""
    v = value.strip()
    if not v:
        return False
    if v.lower().startswith(("http://", "https://")):
        return True
    if _IPV4.match(v) or _IPV6.match(v):
        return True
    if _is_digit_heavy(v):
        return True
    if _BARE_DOMAIN_RE.match(v):
        return True
    if v.lower() in _FIELD_LABEL_STOPLIST:
        return True
    return False


# ============================================================
# MAIN DETECTOR
# ============================================================

def detect_entities_gliner(text: str) -> Dict[str, List[Tuple[str, float]]]:
    """Detect semantic entities with GLiNER. Returns {type: [(value, conf), ...]}."""
    if not GLINER_AVAILABLE:
        raise RuntimeError("GLiNER is unavailable")
    if not isinstance(text, str) or not text.strip():
        return {}

    try:
        model = _get_model()
    except Exception as e:
        raise RuntimeError(f"GLiNER load failed: {e}") from e

    global _RUN_COUNTER
    with _RUN_COUNTER_LOCK:
        _RUN_COUNTER += 1

    # Collect best confidence per (label, value).
    best: Dict[str, Dict[str, float]] = {}

    try:
        for chunk in _chunks(text, model):
            entities = model.predict_entities(chunk, LABELS, threshold=MIN_CONFIDENCE)
            for e in entities:
                conf = float(e["score"])
                if conf < MIN_CONFIDENCE:
                    continue
                label = e["label"]
                if label not in LABELS:
                    continue
                value = _clean(e["text"])
                if not value or _is_structured(value):
                    continue  # IP/URL belong to the regex layer, not entity.*
                bucket = best.setdefault(label, {})
                if value not in bucket or conf > bucket[value]:
                    bucket[value] = conf
    except Exception as e:
        raise RuntimeError(f"GLiNER detection failed: {e}") from e

    return {
        label: [(v, c) for v, c in values.items()]
        for label, values in best.items()
        if values
    }


# ============================================================
# SELF-TEST
# ============================================================

if __name__ == "__main__":
    print("\n=== GLiNER NER Detector Self-Test ===\n")
    sample = (
        "John Doe works at ACME Corp in Sampletown. Visit https://acme.com or call "
        "403-555-9999 on March 3, 2024. ID: 123456789"
    )
    from pprint import pprint
    pprint(detect_entities_gliner(sample))
