#!/usr/bin/env python3
"""Build-time model baking, invoked twice from docker/Dockerfile.

Downloads and prepares the exact model artifacts the running app needs
(GLiNER's Hugging Face snapshot + its exported FP32 ONNX graph, and
PaddleOCR's three pinned models) into a SEED location rooted at $HOME,
instead of the real runtime cache paths (/root/.cache/huggingface,
/root/.cache/securescan). The Dockerfile runs this with HOME redirected to
$SECURESCAN_MODEL_SEED so the result lands there; docker/entrypoint.sh
copies it into the real cache paths at container startup, but only for
whatever is missing there -- see that file's comment for why the seed is
kept separate rather than baked directly into /root/.cache.

Two passes, each its own process so HF_HUB_OFFLINE (set as a side effect of
importing detectors.gliner_detector) can never leak backwards into the
download pass:

  download  -- network required. Fetches the GLiNER snapshot and the three
               PaddleOCR models via the app's own model names/constants.
  export    -- offline (HF_HUB_OFFLINE=1). Calls the app's own
               _prepare_onnx_model()/_onnx_cache_dir(), so the baked ONNX
               cache is guaranteed to satisfy the exact four-file contract
               detectors/gliner_detector.py enforces at runtime, and this
               pass itself proves the export needs no network.
"""
import json
import sys
from pathlib import Path


def download() -> None:
    from huggingface_hub import snapshot_download

    # Mirrors detectors/gliner_detector.py's MODEL_NAME. Kept as a literal,
    # not an import, so this pass never imports gliner_detector (which sets
    # HF_HUB_OFFLINE=1 as an import side effect) before the download below
    # completes. If the two ever drift, the offline export pass fails
    # loudly (it re-resolves the snapshot with local_files_only=True under
    # the real constant) instead of silently baking the wrong model.
    snapshot_dir = Path(snapshot_download("urchade/gliner_medium-v2.1"))

    # GLiNER's tokenizer loading (gliner.model._load_tokenizer) resolves the
    # encoder's tokenizer via transformers.AutoTokenizer against the repo
    # named in gliner_config.json's "model_name" field (currently
    # "microsoft/deberta-v3-base") -- a SEPARATE Hugging Face repo. The raw
    # upstream snapshot genuinely contains no tokenizer.json/
    # tokenizer_config.json of its own (verified directly: a fresh
    # snapshot_download() here produces exactly five files -- .gitattributes,
    # README.md, gliner_config.json, model.safetensors, pytorch_model.bin --
    # confirming detectors/gliner_detector.py's _load_torch_model() docstring
    # is accurate for a genuinely pristine snapshot). A host that has since
    # run _load_torch_model() has a *mutated* snapshot: that function copies
    # gliner_config.json from the ONNX cache directory over the snapshot's
    # own copy, expanding "model_name" into a full nested "encoder_config"
    # object -- easy to mistake for the raw upstream shape when inspecting
    # an already-used cache, which is what happened while debugging this.
    # On a genuinely first-ever export (this bake), the separate
    # microsoft/deberta-v3-base repo isn't cached anywhere yet, and
    # detectors/gliner_detector.py's _prepare_onnx_model() would otherwise
    # need network for it during the (supposedly offline) export pass.
    gliner_config = json.loads((snapshot_dir / "gliner_config.json").read_text())
    encoder_repo = gliner_config.get("model_name")
    if encoder_repo:
        from transformers import AutoTokenizer

        AutoTokenizer.from_pretrained(encoder_repo)

    # The Dockerfile runs this pass while the CPU paddlepaddle package from
    # requirements.txt is still installed. The GPU overlay comes afterwards:
    # paddlepaddle-gpu imports libcuda.so.1 even for a CPU-configured
    # PaddleOCR engine, but the downloaded weights are device-agnostic.
    from extractors.image_extractor import _create_ocr_engine

    _create_ocr_engine()


def export() -> None:
    from detectors.gliner_detector import _onnx_cache_dir, _prepare_onnx_model

    _prepare_onnx_model(_onnx_cache_dir())


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("download", "export"):
        raise SystemExit(f"Usage: {sys.argv[0]} download|export")
    {"download": download, "export": export}[sys.argv[1]]()
