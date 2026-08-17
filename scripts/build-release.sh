#!/bin/bash
# Builds distributable release archives of this repository: a .tar.gz and a
# .zip, both containing one top-level securescan-<version>/ directory so
# extracting either produces the same layout. A person downloads one,
# extracts it, and runs the Docker build from inside it -- see README.md's
# setup instructions (docker compose build, then scripts/install.sh and a
# launcher, or scripts/ directly).
#
# Usage: scripts/build-release.sh [output-dir]
#   output-dir defaults to ./dist (created if missing).
#
# Exclude development state, generated output, caches, and crash dumps:
# Development metadata, outputs, caches, virtual environments,
# core.*, and the model caches (~/.cache/huggingface, ~/.cache/securescan --
# never inside the repo tree, excluded defensively in case one was ever
# copied in by hand).
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
repo_dir=$(dirname -- "$script_dir")

requested_output_dir="${1:-$repo_dir/dist}"
mkdir -p -- "$requested_output_dir"
output_dir=$(CDPATH= cd -- "$requested_output_dir" && pwd)

version=$(sed -n 's/^SECURESCAN_VERSION = "\(.*\)"/\1/p' "$repo_dir/config.py")
if [[ -z "$version" ]]; then
    echo "build-release.sh: could not read SECURESCAN_VERSION from config.py" >&2
    exit 1
fi

archive_name="securescan-${version}"
staging_root=$(mktemp -d)
trap 'rm -rf -- "$staging_root"' EXIT

staging_dir="$staging_root/$archive_name"
mkdir -p -- "$staging_dir"

exclude_args=(
    --exclude=.git
    --exclude=.claude
    --exclude=.codex
    --exclude=.agents
    --exclude=outputs
    --exclude=__pycache__
    --exclude=.venv
    --exclude='core.*'
    # This script's own default output directory -- excluded so re-running
    # it doesn't fold a previous run's archives into the next one.
    --exclude=dist
    # Model caches never live inside the repo tree (they're bind-mounted
    # from $HOME at run time -- see docker-compose.yml), but excluded here
    # too in case one was ever copied in by hand.
    --exclude='.cache/huggingface'
    --exclude='.cache/securescan'
)

echo "build-release.sh: staging ${archive_name} ..."
tar -C "$repo_dir" "${exclude_args[@]}" -cf - . | tar -C "$staging_dir" -xf -

tar_path="$output_dir/${archive_name}.tar.gz"
zip_path="$output_dir/${archive_name}.zip"

echo "build-release.sh: writing $tar_path"
tar -C "$staging_root" -czf "$tar_path" "$archive_name"

echo "build-release.sh: writing $zip_path"
rm -f -- "$zip_path"
if command -v zip >/dev/null 2>&1; then
    (cd "$staging_root" && zip -rq "$zip_path" "$archive_name")
else
    # Use the standard-library fallback when the host does not provide zip.
    # This release-packaging helper is separate from the containerized
    # application runtime.
    (cd "$staging_root" && python3 -m zipfile -c "$zip_path" "$archive_name")
fi

echo "build-release.sh: done."
du -h -- "$tar_path" "$zip_path"
