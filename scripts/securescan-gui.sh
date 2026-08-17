#!/bin/bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
repo_dir=$(dirname -- "$script_dir")
source "$script_dir/securescan-gui-common.sh"

# CPU service's published port (see docker-compose.yml). See
# scripts/securescan-gui-gpu.sh for the GPU counterpart, published on 8080.
run_securescan_gui "$repo_dir" 8081 securescan-cpu
