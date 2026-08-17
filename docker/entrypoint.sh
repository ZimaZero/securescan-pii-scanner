#!/bin/sh
set -eu

# Reports may predate the container migration or have been written through a
# user-namespace mapping. Repair only the bind-mounted report tree; the model
# caches under /root keep their existing ownership and four-file ONNX layout.
output_dir=/app/outputs
mkdir -p -- "$output_dir"
if [ "$(id -u)" = "0" ]; then
    chown -R 1000:1000 -- "$output_dir"
fi

# Seed the model caches from the image's build-time-baked copy (see
# docker/Dockerfile's bake stage and docker/bake_models.py) whenever the
# real cache path is missing something they'd provide -- most notably a
# fresh clone with no ~/.cache/{huggingface,securescan} on the host at all,
# where docker-compose.yml's bind mount would otherwise auto-create an
# empty host directory and shadow anything baked directly into
# /root/.cache. `cp -rn` only adds files that do not already exist at the
# destination, so an existing populated host cache is neither touched nor regenerated. Baked models
# are a fallback, not a replacement. Plain `docker run` invocations with no
# cache bind mounts at all (e.g. no `-v ~/.cache/...`) get the same
# treatment: the destination starts empty either way.
seed_root="${SECURESCAN_MODEL_SEED:-/opt/securescan-model-seed}"
hf_home="${HF_HOME:-/root/.cache/huggingface}"
if [ -d "$seed_root/.cache/huggingface" ]; then
    mkdir -p -- "$hf_home"
    cp -rn -- "$seed_root/.cache/huggingface/." "$hf_home/" 2>/dev/null || true
fi
if [ -d "$seed_root/.cache/securescan" ]; then
    mkdir -p -- /root/.cache/securescan
    cp -rn -- "$seed_root/.cache/securescan/." /root/.cache/securescan/ 2>/dev/null || true
fi

exec "$@"
