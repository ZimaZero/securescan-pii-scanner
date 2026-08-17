#!/bin/bash
# Shared launch logic for scripts/securescan-gui.sh (CPU) and
# scripts/securescan-gui-gpu.sh (GPU). Sourced by both; not meant to run
# directly, and not itself an entry point (no `set -euo pipefail` here --
# each wrapper sets that before sourcing this file).
#
# run_securescan_gui <repo_dir> <gui_port> <compose_service> brings up the
# optional verifier LLM, starts the Windows host bridge (Open Output Folder /
# Browse) and opens the report page in the Windows browser when running under
# WSL, and runs the GUI in the foreground via the given compose service,
# published on the given port.

# WSL2's kernel release string contains "microsoft" (e.g.
# "6.6.87.2-microsoft-standard-WSL2") -- the same check gui.py's
# windows_bridge_available() uses on the app side. On plain Linux this is
# false, and every WSL-only step below (the Windows bridge process, the
# Explorer browser-open, and docker-compose.wsl.yml's /mnt/c-/mnt mounts) is
# skipped instead of erroring: explorer.exe/powershell.exe don't exist there,
# and /mnt/c has no Windows drive behind it to mount.
is_wsl() {
    grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null
}

# True only when a real NVIDIA GPU is both visible to the host (nvidia-smi)
# and reachable through Docker (the NVIDIA Container Toolkit registered with
# the daemon) -- both are required for docker-compose.gpu.yml's device
# reservation to actually succeed rather than failing container start with
# "could not select device driver ... with capabilities: [[gpu]]". Absent
# either, securescan-gpu/ollama still start fine without the reservation
# (see docker-compose.yml's top-of-file comment) -- this only controls
# whether the launcher additionally asks for real GPU acceleration.
gpu_available() {
    command -v nvidia-smi >/dev/null 2>&1 || return 1
    nvidia-smi -L >/dev/null 2>&1 || return 1
    docker info 2>/dev/null | grep -qi nvidia
}

run_securescan_gui() {
    local repo_dir="$1"
    local gui_port="$2"
    local compose_service="$3"

    # Deliberately NOT `local`: the EXIT trap fires after run_securescan_gui
    # has already returned (it's the last statement of the sourcing wrapper
    # script), by which point any `local` variable of this function is out
    # of scope. With `set -u` that turns a reference inside cleanup() into
    # an "unbound variable" error instead of running the intended cleanup.
    # These must be plain (script-global) variables, same as the original
    # pre-refactor script, so cleanup() can still see them at that point.
    windows_bridge_pid=""
    ollama_started_here=""

    cleanup() {
        if [[ -n "$ollama_started_here" ]]; then
            docker compose stop ollama 2>/dev/null || true
        fi
        if [[ -n "$windows_bridge_pid" ]] && kill -0 "$windows_bridge_pid" 2>/dev/null; then
            kill "$windows_bridge_pid" 2>/dev/null || true
            wait "$windows_bridge_pid" 2>/dev/null || true
        fi
    }
    trap cleanup EXIT INT TERM

    cd "$repo_dir"

    # Compose override files are additive (see docker-compose.yml's
    # top-of-file comment): docker-compose.wsl.yml grants Windows drive
    # visibility only under WSL, docker-compose.gpu.yml requests the real
    # GPU device only when one is actually usable. Every `docker compose`
    # call below uses this same file set.
    local compose_args=(-f "$repo_dir/docker-compose.yml")
    if is_wsl; then
        compose_args+=(-f "$repo_dir/docker-compose.wsl.yml")
    fi
    if gpu_available; then
        compose_args+=(-f "$repo_dir/docker-compose.gpu.yml")
    else
        echo "securescan-gui: no NVIDIA GPU detected; $compose_service will run CPU-only." >&2
    fi

    # Bring up the optional verifier LLM before the GUI starts, so a scan run
    # with verification on doesn't race Ollama's own startup. "created" and
    # Treat "restarting" as already existing to avoid a second `up`
    # on top of a container that's mid-transition; only a genuinely absent/
    # stopped container gets started here.
    local ollama_state
    ollama_state=$(docker inspect -f '{{.State.Status}}' ollama 2>/dev/null || true)
    case "$ollama_state" in
        running|restarting|created)
            ;;
        *)
            docker compose "${compose_args[@]}" up -d ollama
            ollama_started_here=1
            ;;
    esac

    # Poll until Ollama actually answers, or give up after the cap. Without
    # this, a scan started with verification on during Ollama's startup window
    # gets connection-refused on a service that's about to work -- a failure
    # indistinguishable from a real misconfiguration. If it never comes up,
    # verification just degrades to skipped, same as if Ollama were down.
    local ollama_ready=0
    for _ in $(seq 1 30); do
        if curl -sf --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
            ollama_ready=1
            break
        fi
        sleep 1
    done
    if [[ "$ollama_ready" -ne 1 ]]; then
        echo "securescan-gui: Ollama did not become ready within 30s; verification will be skipped until it is." >&2
    fi

    if is_wsl; then
        # Host-side bridge for both Open Output Folder and Browse (Windows).
        # Meaningless on plain Linux (no explorer.exe/powershell.exe), so
        # gui.py's windows_bridge_available() also independently disables the
        # in-app "Browse (Windows)" button there -- this and that are two
        # sides of the same degradation, one host-side and one app-side.
        "$repo_dir/scripts/open-output-folder-helper.sh" &
        windows_bridge_pid=$!
        (sleep 8 && explorer.exe "http://localhost:$gui_port") &
    else
        echo "securescan-gui: not running under WSL; skipping Explorer auto-open and the Windows bridge. Open http://localhost:$gui_port yourself." >&2
    fi

    docker compose "${compose_args[@]}" run --rm --service-ports "$compose_service" python gui.py
}
