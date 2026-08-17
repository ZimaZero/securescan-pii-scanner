# SecureScan

SecureScan is a local PII scanner focused on Canadian identity data. It scans
files and directories, assigns banded risk scores, and writes HTML, Markdown,
and JSON reports with finding provenance.

Current release: **v2.4.1** · Python 3.13 · Linux/WSL2 · MIT

## Highlights

- Coordinates eleven detection layers with an explicit source-trust hierarchy.
- Validates SINs, payment cards, supported health cards, and ICAO 9303 MRZ
  fields with checksums where available.
- Detects Canadian passports, IRCC UCIs, Certificate of Indian Status
  registration numbers, and provincial or territorial driver's licences with
  format-and-context rules.
- Extracts native document text and applies PaddleOCR to images and scanned PDF
  pages.
- Runs GLiNER semantic NER through ONNX Runtime, with an automatic PyTorch
  fallback.
- Supports CPU execution and opt-in NVIDIA GPU acceleration for PaddleOCR,
  GLiNER, and Ollama.
- Reports extraction failures, skipped files, degraded detector execution, and
  possible silent misses instead of treating them as clean scans.
- Provides a browser dashboard with progress, cancellation, per-extension
  filtering, configurable worker limits, and recent-report access.

## Architecture

```text
file or directory
      │
      ▼
discovery.py ──► format-specific extraction
      │          text · DOCX · XLSX · PDF · image · EML · PPTX
      │                         │
      │                         └─► PaddleOCR for images/scanned PDF pages
      ▼
detectors/hybrid_detector.py
      ├─  1 regex and checksum validation
      ├─  2 keyword/context detection
      ├─  3 GLiNER semantic NER
      ├─  4 secrets and credentials
      ├─  5 Canadian health cards
      ├─  6 passports
      ├─  7 IRCC UCI
      ├─  8 status-card registration numbers
      ├─  9 deterministic OCR recovery
      ├─ 10 driver's licences
      └─ 11 ICAO 9303 MRZ parsing
      │
      ▼
collision reconciliation ──► optional demote-only LLM verification
      │
      ▼
banded scoring ──► Markdown · JSON · HTML reports
```

All eleven layers receive the full extracted text except GLiNER, which may be
disabled by file type and is limited to a configurable text prefix. Detection
results are normalized into a shared taxonomy before collision reconciliation.
Renderers consume existing findings and scores; they do not perform detection
or rescoring.

The optional Ollama verifier is disabled by default. It may annotate and demote
selected checksum-less findings, but it cannot create, delete,
promote, or retype findings.

## Supported input

SecureScan recognizes 19 extensions across seven families:

| Family | Extensions and behavior |
|---|---|
| Text and structured text | `.txt`, `.md`, `.csv`, `.json`, `.log`, `.py` |
| Word | `.docx` body text, tables, headers, footers, and core metadata |
| Spreadsheets | `.xlsx` calculated values and cell comments |
| PDF | `.pdf` native text with per-page PaddleOCR fallback |
| Images | `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`, `.gif`, `.webp`; multi-page TIFF supported |
| Email | `.eml` headers, text/HTML bodies, and attachment filenames; attachment content is not recursed |
| Presentations | `.pptx` text frames, grouped shapes, tables, and speaker notes; embedded images and objects are not parsed |

PaddleOCR uses EXIF correction and its text-line orientation model. OCR models
are pinned and baked into the container image. Recognized text is not filtered
by confidence before detection.

## Requirements

- Docker Engine with Compose v2 (`docker compose`)
- A supported Linux distribution or WSL2
- Sufficient disk space for the CUDA base image and baked GLiNER/PaddleOCR
  models
- Optional NVIDIA driver and NVIDIA Container Toolkit for GPU acceleration

No host-side Python environment is required. Application Python and all runtime
dependencies remain inside the containers.

## Download and build

```bash
git clone https://github.com/ZimaZero/securescan-pii-scanner.git
cd securescan-pii-scanner
docker compose build securescan-cpu
```

The first CPU build downloads Python packages and model artifacts. The
Dockerfile bakes the GLiNER snapshot, complete FP32 ONNX export, and three
PaddleOCR models into an image seed cache. Runtime CPU scans then operate
without model downloads. Existing host caches mounted from `~/.cache/huggingface` and
`~/.cache/securescan` take precedence and are never overwritten.

Build the GPU image when NVIDIA acceleration is required:

```bash
docker compose build securescan-gpu
```

The GPU build bakes the PaddleOCR weights while the CPU PaddlePaddle package is
still installed, then replaces it with the GPU package. This works on build
hosts without NVIDIA GPU access because the downloaded weights are
device-agnostic.

## Run the GUI

Run the CPU launcher:

```bash
scripts/securescan-gui.sh
```

Run the GPU launcher:

```bash
scripts/securescan-gui-gpu.sh
```

The launchers select `docker-compose.wsl.yml` under WSL2 and select
`docker-compose.gpu.yml` only when an NVIDIA GPU is visible both to the host
and Docker. The GPU service falls back to CPU when PaddleOCR or GLiNER cannot
initialize its requested GPU provider.

Optionally create convenience symlinks in the home directory:

```bash
scripts/install.sh
~/securescan-gui.sh
~/securescan-gui-gpu.sh
```

Open `http://localhost:8081` for the CPU service or
`http://localhost:8080` for the GPU service. WSL2 launchers open the URL in
the Windows browser and enable the Windows folder bridge. Plain Linux skips
the Windows integration and uses the in-app browser.

Run Compose directly when launcher integration is unnecessary:

```bash
# CPU
docker compose run --rm --service-ports securescan-cpu python gui.py

# GPU
docker compose -f docker-compose.yml -f docker-compose.gpu.yml \
  run --rm --service-ports securescan-gpu python gui.py

# WSL2 CPU with Windows-drive visibility
docker compose -f docker-compose.yml -f docker-compose.wsl.yml \
  run --rm --service-ports securescan-cpu python gui.py
```

The base Compose file mounts the repository at `/app`. The WSL2 overlay also
mounts `/mnt/c` and `/mnt` read-only. On plain Linux, mount any external scan
root explicitly before selecting it in the GUI:

```bash
docker compose run --rm --service-ports \
  -v /host/data:/scan:ro securescan-cpu python gui.py
```

## Run the CLI

Scan repository content through the existing `/app` mount:

```bash
docker compose run --rm securescan-cpu \
  python scanner.py --path /app/tests/sample_data --no-open
```

Scan an arbitrary host directory through an explicit read-only mount:

```bash
docker compose run --rm -v /host/data:/scan:ro securescan-cpu \
  python scanner.py --path /scan --no-open
```

Run the interactive terminal entry point:

```bash
docker compose run --rm securescan-cpu python main.py
```

Reports are written under `outputs/YYYY-MM-DD/`. Each scan produces matching
`report_<timestamp>.md`, `.json`, and `.html` files, and `outputs/latest.html`
tracks the newest HTML report.

## Optional local verification

Start Ollama and pull the configured model before enabling verification:

```bash
docker compose up -d ollama
docker compose exec ollama ollama pull qwen2.5:3b
```

Then enable the experimental verifier in the GUI or pass `--verify` to
`scanner.py`. A missing server or model causes verification to be skipped;
deterministic scanning continues.

The verifier remains opt-in because measured Canadian-document results include
incorrect demotions. See
[`docs/evidence/verifier_model_comparison.md`](docs/evidence/verifier_model_comparison.md)
for the recorded comparison.

## CPU and GPU behavior

| Path | PaddleOCR | GLiNER | Torch fallback | Ollama |
|---|---|---|---|---|
| `securescan-cpu` | CPU | ONNX CPU provider | CPU | CPU unless a GPU overlay grants a device |
| `securescan-gpu` with GPU overlay | PaddlePaddle GPU | locked ONNX CUDA provider with CPU fallback | CPU | NVIDIA GPU |
| `securescan-gpu` without usable GPU | automatic CPU fallback | automatic CPU fallback | CPU | CPU |

GLiNER serializes CUDA `session.run()` calls because concurrent ONNX Runtime
CUDA execution produced corrupt outputs during validation. The CPU service is
the deterministic baseline; GPU confidence values can differ slightly because
of floating-point kernel differences while retaining the same observed finding
structure in the recorded corpus comparison.

## Security and privacy

- Runtime model loading is offline after the image seed or mounted caches are
  populated. Network access is required during image preparation, a first GPU
  OCR run when PaddleOCR baking was skipped, and an optional Ollama model pull.
- Symlink entries are rejected, the active report tree is excluded from input
  discovery, oversized files are reported as skipped, and text-like binary
  content is rejected.
- Reports contain sensitive unmasked data and are created under a
  permission-restricted output directory.
- The HTML privacy-reduced export masks finding values and metadata, but keeps
  filenames and paths visible so affected files remain identifiable. Review
  every exported report before sharing it.
- Compose currently publishes GUI ports `8080`/`8081` and Ollama port `11434`
  without an explicit loopback host address. Apply host firewall restrictions
  or bind those ports to `127.0.0.1` before running on an untrusted network.

## Known limitations

- Handwritten identifiers remain outside the evaluated OCR capability.
- Security printing and holograms can prevent non-MRZ card fields from being
  extracted reliably.
- Metadata is displayed and masked but is not passed through PII detection.
- XLSX formula source text is not scanned when no cached value is present.
- Embedded email attachments and presentation images are not recursively
  scanned.
- Detector degradation is reported, but health is not summarized as a full
  per-layer preflight matrix.
- A single large file runs on one worker and can dominate total scan time.

## Tests

Run project tests inside the CPU container:

```bash
docker compose run --rm securescan-cpu python tests/test_format_coverage.py
docker compose run --rm securescan-cpu python tests/test_orchestration_audit.py
```

Each test module is also executable directly with the same container command.

## Build release archives

```bash
scripts/build-release.sh
```

The script writes `securescan-<version>.tar.gz` and
`securescan-<version>.zip` under `dist/` by default.

## License

SecureScan is available under the [MIT License](LICENSE).

SecureScan is a detection aid, not a compliance guarantee. Validate results
against applicable legal, security, and data-handling requirements.
