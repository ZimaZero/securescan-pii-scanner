# Fresh-machine install test

Date: 2026-08-18
Operator: Aleks Jesenik
Repo state: commit `a0c3753` (public HTTPS clone)

## Purpose

Verify that a clean machine can clone, build, and run SecureScan using only
the README, with no host caches, no pre-pulled images, and no prior project
artifacts. This had never been performed before this run.

## Test machine

MSI laptop, factory Windows 11 (build 10.0.26200.8973), NVIDIA RTX 4060.
No WSL, no Docker, no git, no Python installed at the start.

## Procedure and result

1. `wsl --install` (PowerShell, Administrator) — FAILED on first attempt with
   a catastrophic-failure error. Succeeded after a reboot and retry. WSL
   version 2.7.11. The command installed WSL but no distribution; a separate
   `wsl --install -d Ubuntu` was required.
2. Docker Engine installed via `curl -fsSL https://get.docker.com | sudo sh`.
   Compose plugin reported v5.5.0.
3. `git clone` over public HTTPS. No authentication needed.
4. `docker compose build securescan-cpu` — SUCCESS. No host cache present at
   `~/.cache/huggingface` or `~/.cache/securescan`, confirming the baked
   image seed is sufficient on its own.
5. `docker system df` — 1 image, 14.19 GB. Build cache 31 entries, 14.19 GB.
6. `tests/test_format_coverage.py` on `securescan-cpu` — 16/16 files passed,
   0 failed.
7. `nvidia-smi` inside WSL2 printed a valid table (RTX 4060), confirming the
   Windows driver alone is visible to WSL.
8. NVIDIA Container Toolkit 1.20.0-1 installed, `nvidia-ctk runtime configure
   --runtime=docker` applied, Docker daemon restarted.
9. `docker compose build securescan-gpu` — SUCCESS in 527.9s. Base image
   `nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04`, onnxruntime-gpu 1.26.0,
   paddlepaddle-gpu 3.3.1. Layers 2-7 were reused from the CPU build.
10. `tests/test_format_coverage.py` on `securescan-gpu` with the GPU overlay —
    16/16 files passed, 0 failed.
11. `scripts/securescan-gui-gpu.sh` launched, GUI reachable, scan completed,
    GPU confirmed in use rather than silently falling back to CPU.

## Parity

Per-file scores were identical on the fresh CPU build, the fresh GPU build,
and the established ws01 baseline:

| Fixture | Score |
|---|---|
| docx_full.docx | 79 |
| docx_no_pii.docx | 0 |
| xlsx_multisheet.xlsx | 91 |
| pdf_text_layer.pdf | 79 |
| pdf_no_pii.pdf | 0 |
| pdf_scanned_2page.pdf | 87 |
| image_clean.png | 78 |
| image_small.png | 78 |
| image_rotated.png | 78 |
| image_rotated_180.png | 78 |
| image_rotated_270.png | 78 |
| image_exif_rotated.png | 78 |
| data.csv | 80 |
| data.json | 80 |
| email_notice.eml | 83 |
| deck_summary.pptx | 79 |

No score moved. Parity holds across a machine that had never run the project.

## README gaps found

1. Docker Engine is named as a requirement with no installation path.
2. NVIDIA Container Toolkit is named as a requirement with no installation
   path. The mandatory `sudo service docker restart` after `nvidia-ctk
   runtime configure` was also undocumented; without it the GPU service
   builds and runs but falls back to CPU silently.
3. "Sufficient disk space" was stated without a figure. Measured: 14.19 GB
   for the CPU image alone.
4. `wsl --install` needing a reboot-and-retry, and not installing a
   distribution, were both undocumented.

All four are addressed in commit `bdb5e9a`.

## Not covered by this run

- Plain Linux (non-WSL2) installation. Still never exercised.
- The full 33-module test loop on the fresh machine. Only
  `test_format_coverage.py` was run there; the full loop was run on ws01.
- The optional Ollama verifier path.
- `scripts/install.sh` home-directory symlinks.
- A cold GPU build with no CPU layers cached. The measured 527.9s benefits
  from layers 2-7 already being present.
