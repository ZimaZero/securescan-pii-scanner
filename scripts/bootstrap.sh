#!/usr/bin/env bash
# SecureScan bootstrap: takes a clean Debian/Ubuntu shell (native or WSL2) to a
# built, verified install. Does not install WSL2 itself — run `wsl --install`
# on the Windows side first.
#
# Usage: scripts/bootstrap.sh [--cpu-only]
set -euo pipefail

CPU_ONLY=0
[ "${1:-}" = "--cpu-only" ] && CPU_ONLY=1

log()  { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] && die "Do not run as root. Run as your normal user; sudo is used where needed."
command -v sudo >/dev/null || die "sudo not found."

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
[ -f docker-compose.yml ] || die "docker-compose.yml not found; run this from inside the SecureScan repo."

# ---------- Docker Engine ----------
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  log "Docker Engine with Compose v2 already present"
else
  log "Installing Docker Engine"
  curl -fsSL https://get.docker.com | sudo sh
fi

log "Starting the Docker daemon"
sudo service docker start >/dev/null 2>&1 || sudo systemctl start docker

NEEDS_RELOGIN=0
if id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
  log "User '$USER' is already in the docker group"
else
  log "Adding '$USER' to the docker group"
  sudo usermod -aG docker "$USER"
  NEEDS_RELOGIN=1
fi

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  warn "Docker not reachable as '$USER' yet (group change needs a new login). Using sudo for this run."
  DOCKER="sudo docker"
fi

# ---------- Optional GPU ----------
GPU=0
if [ "$CPU_ONLY" -eq 1 ]; then
  log "--cpu-only given; skipping GPU setup"
elif command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
  GPU=1
  log "NVIDIA GPU is visible"
  if command -v nvidia-ctk >/dev/null; then
    log "NVIDIA Container Toolkit already present"
  elif ! command -v apt-get >/dev/null; then
    warn "Not an apt-based system; install the NVIDIA Container Toolkit manually. Continuing CPU-only."
    GPU=0
  else
    log "Installing the NVIDIA Container Toolkit"
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
      | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
      | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
      | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
    sudo apt-get update
    sudo apt-get install -y nvidia-container-toolkit
    sudo nvidia-ctk runtime configure --runtime=docker
    log "Restarting the Docker daemon (required after nvidia-ctk)"
    sudo service docker restart >/dev/null 2>&1 || sudo systemctl restart docker
    sleep 3
  fi
else
  warn "No usable NVIDIA GPU detected; continuing CPU-only."
fi

# ---------- Disk check ----------
AVAIL_GB="$(df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9')"
if [ -n "${AVAIL_GB:-}" ] && [ "$AVAIL_GB" -lt 40 ]; then
  warn "Only ${AVAIL_GB}GB free on /. The CPU image alone is ~14GB; 40GB free is recommended."
fi

# ---------- Build ----------
log "Building securescan-cpu (first run downloads packages and bakes models)"
$DOCKER compose build securescan-cpu

if [ "$GPU" -eq 1 ]; then
  log "Building securescan-gpu"
  $DOCKER compose build securescan-gpu
fi

# ---------- Verify ----------
log "Verifying the CPU image with the format-coverage suite"
$DOCKER compose run --rm securescan-cpu python tests/test_format_coverage.py

if [ "$GPU" -eq 1 ]; then
  log "Verifying the GPU image with the format-coverage suite"
  $DOCKER compose -f docker-compose.yml -f docker-compose.gpu.yml \
    run --rm securescan-gpu python tests/test_format_coverage.py
fi

log "Bootstrap complete."
echo
echo "  CPU GUI:  scripts/securescan-gui.sh      -> http://localhost:8081"
if [ "$GPU" -eq 1 ]; then
  echo "  GPU GUI:  scripts/securescan-gui-gpu.sh  -> http://localhost:8080"
fi
echo
if [ "$NEEDS_RELOGIN" -eq 1 ]; then
  warn "Close and reopen this shell so docker works without sudo."
fi
