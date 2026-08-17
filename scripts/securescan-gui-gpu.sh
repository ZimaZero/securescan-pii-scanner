#!/bin/bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
repo_dir=$(dirname -- "$script_dir")
source "$script_dir/securescan-gui-common.sh"

# GPU service's published port (see docker-compose.yml). See
# scripts/securescan-gui.sh for the CPU counterpart, published on 8081.
run_securescan_gui "$repo_dir" 8080 securescan-gpu
