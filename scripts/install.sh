#!/bin/bash
# One-time setup for a fresh clone: create the two home-directory launcher
# symlinks described in README.md (~/securescan-gui.sh and
# ~/securescan-gui-gpu.sh). Safe to rerun;
# an existing symlink already pointing at this clone's scripts is left
# alone, and anything else in the way is reported, not overwritten.
#
# Not required: scripts/securescan-gui.sh and scripts/securescan-gui-gpu.sh
# work identically run directly (they resolve their own repo_dir from their
# own location either way — see their own top comments) — this script only
# saves typing the full path from $HOME.
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
repo_dir=$(dirname -- "$script_dir")

link_launcher() {
    local target="$1"
    local link_name="$2"

    if [[ -L "$link_name" ]]; then
        if [[ "$(readlink -f -- "$link_name")" == "$(readlink -f -- "$target")" ]]; then
            echo "install.sh: $link_name already points at $target — leaving it."
            return
        fi
        echo "install.sh: $link_name is a symlink to something else ($(readlink -- "$link_name")) — not touching it." >&2
        return
    fi
    if [[ -e "$link_name" ]]; then
        echo "install.sh: $link_name already exists and isn't a symlink — not touching it." >&2
        return
    fi
    ln -s -- "$target" "$link_name"
    echo "install.sh: created $link_name -> $target"
}

link_launcher "$repo_dir/scripts/securescan-gui.sh" "$HOME/securescan-gui.sh"
link_launcher "$repo_dir/scripts/securescan-gui-gpu.sh" "$HOME/securescan-gui-gpu.sh"
