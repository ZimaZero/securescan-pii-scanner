# SecureScan CPU/GPU container image. The CUDA runtime supports the optional
# NVIDIA paths for GLiNER and PaddleOCR; CPU builds use the same base so the
# two images differ only in their inference packages.
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=America/Edmonton \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# HF_HUB_OFFLINE=1 (set in detectors/gliner_detector.py before the GLiNER
# import) means a wrong/unresolved cache path fails outright instead of
# silently falling back to a network download. huggingface_hub's own
# resolution order is HUGGINGFACE_HUB_CACHE > HF_HOME (models land under
# $HF_HOME/hub) > XDG_CACHE_HOME > ~/.cache/huggingface -- set both HF_HOME
# and XDG_CACHE_HOME explicitly so that resolution isn't left to whatever
# $HOME happens to be for whoever/however this image is run, and so it
# lines up exactly with where docker-compose.yml bind-mounts the host's
# ~/.cache/huggingface (confirmed layout: .../huggingface/hub/models--...).
#
# The securescan ONNX export cache (~/.cache/securescan/gliner_onnx-torch
# <version>/, see detectors/gliner_detector.py's _onnx_cache_dir()) is
# deliberately NOT pinned via its SECURESCAN_GLINER_ONNX_CACHE override
# here: that override replaces the whole path, including the
# torch-version-suffixed subfolder used to prevent reuse across incompatible
# Torch versions. Hardcoding it here would defeat that property. It resolves
# correctly via Path.home(), since this image runs as root with the
# base image's default $HOME=/root, matching where the securescan cache
# is bind-mounted.
ENV HF_HOME=/root/.cache/huggingface \
    XDG_CACHE_HOME=/root/.cache

# --- Python 3.13 via deadsnakes (Ubuntu 24.04 ships 3.12 by default; ------
# --- deadsnakes publishes 3.13 packages for "noble") ----------------------
# --- Plus the apt packages required by two of the pinned Python deps: -----
# ---   libgomp1                          -> paddlepaddle import needs it
# ---   libgl1 libglib2.0-0 libx11-6      -> opencv-contrib-python (the
# ---                                        pinned, non-headless build;
# ---                                        do NOT swap to *-headless)
# ---   fonts-dejavu-core                 -> tests/test_multiframe_tiff.py
# ---                                        renders a synthetic multi-frame
# ---                                        TIFF fixture with PIL's
# ---                                        ImageFont.truetype("DejaVuSans.ttf",
# ---                                        ...)
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common \
        ca-certificates \
        gnupg \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3.13 \
        python3.13-venv \
        python3.13-dev \
        tzdata \
        libgomp1 \
        libgl1 \
        libglib2.0-0 \
        libx11-6 \
        git \
        fonts-dejavu-core \
    && ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime \
    && printf '%s\n' "${TZ}" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# --- venv at /opt/venv, ahead of the bind-mounted /app project dir --------
RUN python3.13 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
RUN pip install --no-cache-dir --upgrade pip

# --- requirements.txt installed unmodified, as its own cached layer -------
# Only requirements.txt is copied in at build time; the rest of the repo is
# bind-mounted at runtime (see docker-compose.yml), not COPYed, so edits on
# the host are visible in the container without a rebuild.
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ============================================================================
# GPU overlay #1: swap onnxruntime -> onnxruntime-gpu (ONNX Runtime, GLiNER's
# default backend). torch is deliberately left as the pinned CPU wheel from
# requirements.txt: GLiNER's ONNX backend is the default
# (config.GLINER_BACKEND = "onnx"), torch is only the automatic fail-safe
# fallback path, so it does not need a CUDA build for this migration.
#
# Version note: onnxruntime==1.27.0 (the CPU pin in requirements.txt) and
# later were rebuilt against CUDA 13 (PyPI metadata: onnxruntime-gpu 1.27.0's
# "cuda" extra requires nvidia-cuda-nvrtc-cu13 / nvidia-cuda-runtime-cu13),
# which does not match this image's CUDA 12.6.3 runtime. onnxruntime-gpu
# 1.22.0-1.26.0 are the CUDA-12-targeted line (extras require
# nvidia-*-cu12~=12.0 / nvidia-cudnn-cu12~=9.0). onnxruntime-gpu==1.26.0 is
# pinned here as the newest release still on that CUDA 12 line, and its
# cp313-manylinux_2_27/2_28 wheel is compatible with Ubuntu 24.04's glibc.
# If a future bump is needed, re-check the target onnxruntime-gpu release's
# PyPI "requires_dist" for its CUDA major version before changing this pin.
#
# The CPU (ENABLE_GPU=0) leg is downgraded from the 1.27.0 CPU package in
# requirements.txt to the plain onnxruntime==1.26.0 CPU package (its cp313
# manylinux wheel exists too) so a GPU-vs-CPU benchmark run only varies the
# GPU/CPU package choice, not the onnxruntime version as a second variable.
# ============================================================================
ARG ENABLE_GPU=1
RUN if [ "$ENABLE_GPU" = "1" ]; then \
        pip uninstall -y onnxruntime && \
        pip install --no-cache-dir onnxruntime-gpu==1.26.0; \
    else \
        pip install --no-cache-dir onnxruntime==1.26.0; \
    fi

# ============================================================================
# Bake models into the image at build time (network required here -- the
# only place this Dockerfile needs egress beyond pip/apt), so the first
# `docker run` or `docker compose run` succeeds without runtime downloads.
# An incomplete GLiNER ONNX cache
# produces zero findings silently, so this must be complete, not best-effort.
#
# This is a FALLBACK, not a replacement for the host-cache bind mounts
# in docker-compose.yml (${HOME}/.cache/huggingface,
# ${HOME}/.cache/securescan): docker/entrypoint.sh copies these seed files
# into the real runtime cache paths at container startup, and only for
# whatever's missing there, so an existing, already-populated host cache
# is neither touched nor regenerated.
#
# Baked to $SECURESCAN_MODEL_SEED, deliberately NOT directly to
# /root/.cache/{huggingface,securescan}: baking straight into those paths
# would still get shadowed at run time by docker-compose.yml's bind mounts
# over those exact paths -- Docker auto-creates an empty host directory for
# a bind-mount source that doesn't exist yet, and an empty directory bound
# over a populated image directory hides everything under it. That
# shadowing would make the baked fallback unavailable to a fresh clone.
#
# Uses a minimal, temporary copy of just the modules the bake needs (not
# the full repo -- the real source is bind-mounted at /app at run time, see
# the comment above requirements.txt) and calls the app's own
# _prepare_onnx_model()/_create_ocr_engine() rather than reimplementing the
# export, so the baked ONNX cache is guaranteed to satisfy the same
# four-file contract (model.onnx, gliner_config.json, tokenizer.json,
# tokenizer_config.json) the app itself enforces at runtime.
# ============================================================================
ENV SECURESCAN_MODEL_SEED=/opt/securescan-model-seed

WORKDIR /build/bake
COPY config.py .
COPY detectors/gliner_detector.py detectors/gliner_detector.py
COPY extractors/__init__.py extractors/errors.py extractors/face_detector.py extractors/image_extractor.py extractors/
COPY docker/bake_models.py .

# Pass 1 (online): HOME redirected so huggingface_hub's and PaddleX's cache
# resolution (both ultimately keyed off "~") lands under the seed root
# instead of the real /root/.cache. HF_HUB_OFFLINE is deliberately not set
# yet -- see bake_models.py's own docstring for why this is two processes.
RUN HOME="$SECURESCAN_MODEL_SEED" \
    HF_HOME="$SECURESCAN_MODEL_SEED/.cache/huggingface" \
    python3 bake_models.py download

# Pass 2 (offline): proves the export step genuinely needs no network, the
# same guarantee HF_HUB_OFFLINE=1 gives the real app at runtime.
RUN HOME="$SECURESCAN_MODEL_SEED" \
    HF_HOME="$SECURESCAN_MODEL_SEED/.cache/huggingface" \
    HF_HUB_OFFLINE=1 \
    python3 bake_models.py export

WORKDIR /
RUN rm -rf /build/bake

# ============================================================================
# GPU overlay #2: swap paddlepaddle -> paddlepaddle-gpu (PaddleOCR's
# backend), from PaddlePaddle's own CUDA 12.6 wheel index. This must remain
# AFTER the model-baking passes above: paddlepaddle-gpu imports libcuda.so.1
# even when PaddleOCR is configured for CPU, while plain docker build has no
# GPU driver mounted. The downloaded PaddleOCR weights are device-agnostic,
# so the GPU runtime uses the files baked with CPU PaddlePaddle.
#
# Verified directly against the index (not guessed): paddlepaddle-gpu ships
# a cp313-linux_x86_64 wheel on the cu126 channel matching the exact version
# pinned in requirements.txt --
#   https://www.paddlepaddle.org.cn/packages/stable/cu126/
#     -> paddlepaddle_gpu-3.3.1-cp313-cp313-linux_x86_64.whl
# so the GPU swap can pin the identical 3.3.1 version rather than drifting
# to a different release.
# ============================================================================
ARG ENABLE_PADDLE_GPU=1
RUN if [ "$ENABLE_PADDLE_GPU" = "1" ]; then \
        pip uninstall -y paddlepaddle && \
        pip install --no-cache-dir paddlepaddle-gpu==3.3.1 \
            -i https://www.paddlepaddle.org.cn/packages/stable/cu126/; \
    else \
        echo "ENABLE_PADDLE_GPU=0: keeping CPU paddlepaddle from requirements.txt"; \
    fi

WORKDIR /app

COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/securescan-entrypoint
ENTRYPOINT ["/usr/local/bin/securescan-entrypoint"]

CMD ["/opt/venv/bin/python", "--version"]
