# Detection-layer selection — implementation + measurement

Measured 2026-08-09. Nothing staged (`git status` shows only working-tree
changes; see the task instruction to stage nothing).

## What shipped

**Layer registry, not a hand-maintained list.** `detectors/hybrid_detector.py`
gained `ALL_LAYERS = frozenset(SOURCE_PRIORITY.keys())` — the 11 layer names
(`secrets`, `regex`, `health_card`, `mrz`, `passport`, `uci`, `status_card`,
`ocr_recovery`, `keyword_context`, `gliner`, `drivers_license`) are read from
the same dict `deduplicate()` already uses as a structural guarantee (a
detector emitting a `source` string with no `SOURCE_PRIORITY` entry
`KeyError`s at merge time on its first real finding). A newly added detector
becomes selectable everywhere (GUI, CLI, `detect_pii_hybrid()`) the moment it
gets a `SOURCE_PRIORITY` entry — nothing else needs updating.

**`detect_pii_hybrid()`** gained `enabled_layers: Optional[FrozenSet[str]] = None`.
`None` (every caller before this parameter existed) runs every layer,
byte-identical to prior behavior — proven both by unit test
(`tests/test_layer_selection.py::test_none_is_byte_identical_to_omitted`,
comparing the full merged-findings dict) and by the full 28-suite loop and
both eval-harness anchors staying green (below). A provided set is
intersected with `ALL_LAYERS`; unrecognized names are silently ignored at
this layer (the CLI/GUI validate and warn before reaching it). Each of the
11 layer blocks in `detect_pii_hybrid()` is now individually gated
(`if "regex" in active_layers: try: ...`) instead of running unconditionally
— a deselected layer's detector function is never called, so a targeted
scan is genuinely faster, not just quieter (see Speed below). GLiNER keeps
its existing `run_ner` file-type gate AND is now also gated by
`enabled_layers` — both must allow it.

**Wiring**: `discovery.scan_file()` / `scan_folder()` / `scan_path()` /
`_scan_folder_impl()` all gained `enabled_layers`, threaded through exactly
like the existing `extensions` parameter (same "intersect with the known
set, print one banner for unknown names" contract). `_scan_folder_impl()`
resolves the scan-level enabled/disabled sets once and:
- passes the resolved `frozenset` to every `scan_file()` call (or `None`
  unchanged, when the caller didn't pass anything — preserving the "omitted
  == explicit None" invariant `tests/test_scan_boundaries.py` already checks
  for `extensions`);
- records `{"enabled": [...], "disabled": [...], "total": 11}` and passes it
  to all three report renderers as `detection_layers`.

**Report header.** `report_generator.detection_layers_line()` (shared by
MD/HTML/JSON via `report_html.py`'s existing reuse-not-rederive import
block) renders one line: `"Detection layers: all 11 active (...)"` for the
default case, or `"⚠ PARTIAL SCAN — Detection layers: N/11 active (...);
DISABLED for this scan: ..."` when anything was deselected — always present,
never a display-only omission, so a filtered report can never be misread as
a full scan. The JSON report gets a new top-level `"layers"` key with the
same `{enabled, disabled, total}` shape. Verified end-to-end with a real
`scan_folder()` call (see Manual verification below).

**CLI**: `scanner.py --layers regex,secrets` (comma-separated, validated
against `ALL_LAYERS` with `argparse.ArgumentTypeError` on typos — listing
the available layers in the error message rather than failing silently).

**GUI**: a new "Detection layers" panel under Advanced, next to File types,
built the same way (checkboxes + Select all/none, persisted in
`.gui_state.json` under a new `"layers"` key with the same fail-safe-to-all
contract as `file_types`). `DETECTION_LAYERS = tuple(sorted(ALL_LAYERS))` —
read from the detector registry, not duplicated. `build_scan_kwargs()` gained
`layers` -> `enabled_layers` (all-checked maps to `None`, mirroring
`extensions_for_file_types()`). `settings_summary()` shows the selection in
the "Next scan:" line when non-default.

## Silent-miss alarm and disabled layers

`detectors/mismatch_alarm.py`'s `evaluate_mismatch_alarm()` gained
`disabled_layers`. When the alarm fires (document-type indicators present,
no identifier finding) and one of the scan's disabled layers is capable of
producing an `identifier.*` finding — i.e. every layer except `gliner` and
`secrets`, which per `PII_TAXONOMY` can only ever emit `entity.*` /
`secret.credential.*` — the reason is extended:

> "... Detector layer(s) disabled for this scan: health_card, regex — this
> scan was configured not to look, which may explain the missing identifier
> finding; it is not necessarily a detection failure. Re-scan with these
> layers enabled to check."

A new `layers_disabled` field is added to the alarm dict (empty list when
none are relevant — e.g. only `gliner`/`secrets` were off, or nothing was
disabled). `discovery.scan_file()` reads `disabled_layers` back out of
`detect_pii_hybrid()`'s `_metadata` and passes it into
`evaluate_mismatch_alarm()`. Covered by 6 new cases in
`tests/test_mismatch_alarm.py` (38/38 total) and an integration case in
`tests/test_layer_selection.py` proving the wiring survives a real
`scan_file()` call (not just the pure function in isolation).

## Reconciliation changes results — measured, not assumed

Per the task: disabling a layer changes which findings win merge/
reconciliation, so a filtered scan is not simply the full scan minus that
layer's own findings.

**Mechanism, proven with a controlled case** (`tests/test_layer_selection.py
::test_reconciliation_depends_on_selection`, mocked detector output so the
result doesn't depend on real regex/context acceptance quirks): a driver's
licence and a health card claiming the identical digit string —
- both layers on: `health_card` (`SOURCE_PRIORITY` 3) beats `drivers_license`
  (`SOURCE_PRIORITY` 1) exactly as designed — the DL finding is discarded.
- `health_card` deselected, `drivers_license` still on: the DL finding
  **survives**, because there is nothing left in the merge to lose the
  collision to. Same input text, different output, purely from layer
  selection.

**Real-corpus measurement, all layers vs. GLiNER off**, 78 specimen-corpus
files (the external specimen corpus, the same 78 non-extraction-
limited files `tests/run_specimen_eval.py` scores):

| | findings | by source |
|---|---:|---|
| all layers | 485 | gliner 342, regex 94, keyword_context 15, drivers_license 10, uci 11, health_card 9, mrz 3, passport 1 |
| GLiNER off | 143 | regex 94, keyword_context 15, drivers_license 10, uci 11, health_card 9, mrz 3, passport 1 |

Exact set diff of `(file, category, value)` triples, GLiNER-off run vs. the
all-layers run's non-GLiNER findings: **0 findings added, 0 removed.** On
this specific corpus, disabling GLiNER changes nothing beyond removing
GLiNER's own `entity.*` findings — no other layer's output shifted as a
result. Likewise `drivers_license`-only reproduced exactly the 10
`drivers_license`-sourced findings present within the all-layers run (0
lost to reconciliation on this corpus).

**This is reported as measured, not smoothed into "reconciliation doesn't
matter."** The mocked test above proves the mechanism is real and can flip a
finding's survival; this corpus's specific digit strings just don't happen
to produce a live collision between GLiNER and anything else, or between
`drivers_license` and a stronger layer. `hybrid_detector.py`'s reconciliation
blocks also don't reference `gliner` in any `stronger`/collision tuple (only
the `dob`/`date` dedup block ever touches GLiNER's raw `"date"` key, and only
to drop a GLiNER-sourced duplicate of an already-found DOB — never to affect
a non-GLiNER finding), which is *why* the empirical zero-delta result above
is structurally expected for GLiNER specifically, even though the general
principle (a deselected layer changes reconciliation) is real and demonstrated
above for `drivers_license`/`health_card`.

## Speed — measured on two corpora, one confirms the mechanism, one explains why it doesn't help there

**Real specimen corpus (78 files, real photographs, OCR-heavy), wall clock,
sequential runs, `DEFAULT_MAX_WORKERS=4`, no OCR semaphore (direct
`scan_file()` calls, not the OCR-throttled `scan_folder()` path):**

| Layers | Time | vs. all |
|---|---:|---:|
| all (11) | 792.11s | — |
| GLiNER off (10) | 795.87s | 1.00x |
| `drivers_license` only | 812.34s | 0.98x |
| `secrets` only | 990.68s | 0.80x (slower) |

**Not dramatically faster — reported as-is, not hidden.** Per the task's own
instruction ("if it isn't, the layers aren't actually being skipped — say
so"): they ARE being skipped — confirmed directly from the same run's
finding-source breakdown (`secrets`-only produced exactly 0 findings;
`drivers_license`-only produced exactly 10 findings, all `source=
drivers_license`, nothing else). The reason wall time doesn't drop is that
**OCR extraction runs before detection and is untouched by layer
selection** — `scan_file()` calls the image/PDF extractor first regardless
of `enabled_layers`, and for 78 real photographed ID documents that
extraction (PaddleOCR) is the dominant cost, not detection. The `secrets`-
only run being the *slowest* of the four (not the fastest, as layer count
alone would predict) is further evidence detection cost is noise-level here:
a ~25% spread (792–991s) across configurations whose actual detection work
differs by up to 11x is consistent with shared-VM system variance
accumulating over a ~53-minute continuous OCR/GLiNER run window, not a
real per-configuration cost signal.

**Text-only corpus (88 files, `tests/canadian_eval_data`, no OCR — isolates
detection-layer cost from extraction cost):**

| Layers | Time | vs. all |
|---|---:|---:|
| all (11) | 14.51s | — |
| GLiNER off (10) | 0.74s | **19.6x faster** |
| `secrets` only | 0.37s | **39.2x faster** |

This is the dramatic speedup the task expects, and it confirms the
mechanism: when extraction isn't the bottleneck, deselecting GLiNER (the
slowest layer, per the performance measurements below) removes ~95% of
wall time. **Conclusion, stated plainly**: layer selection makes detection
itself proportionally as fast as expected — dramatically, on light/text
input. Its benefit on the real specimen corpus specifically is capped by
OCR, which layer selection was never designed to affect and doesn't touch by
design (extraction and detection are separate pipeline stages; see
`discovery.scan_file()`).

## Correction: the actual four regression anchors, not the eval harnesses

The first pass of this section (below) re-ran the Canadian and specimen
**eval harnesses** with defaults and called that "the anchors." That was
wrong — those are two separate manual evaluation harnesses, not the four
named regression anchors this repo's history (`docs/evidence/
anchor_rederivation.md`, `gliner_address_anchor_scan.py`) actually tracks:
`tests/stress_data`, `tests/format_data`, `tests/external_octopii`, and
the external photographed-document anchor. Re-run using the established methodology
(`docs/evidence/scan_anchor_findings.py`, `scan_file(verify=False,
run_ner=None)`, no `enabled_layers` argument — i.e. the real default path),
211 files across the four anchors, once on unmodified code (isolated via a
scoped `git stash` of exactly the 12 relevant files while leaving unrelated
working-tree changes untouched) and once with the relevant changes
restored:

| Anchor | Baseline findings | Candidate findings | Delta |
|---|---:|---:|---:|
| `tests/stress_data` | 132 | 132 | 0 |
| `tests/format_data` | 74 | 74 | 0 |
| `tests/external_octopii` | 51 | 51 | 0 |
| external photographed-document anchor | 75 | 75 | 0 |

Finding-risk and file-risk breakdowns matched exactly per anchor too (e.g.
stress: HIGH 16/MEDIUM 7/LOW 109 in both runs), and the baseline run
reproduced the exact totals already on record in `anchor_rederivation.md`'s
most recent (2026-08-05) measurement — confirming this re-derivation used
the same corpus state, not a drifted one.

**Exhaustive check, not just matching summaries** (a count match alone
doesn't rule out a value swapping with another of the same risk): every one
of the 211 files was compared by exact `(type, value, risk, source)` tuple
set, plus `scan_status`, `score`, and `file_risk`.

```
baseline files: 211   candidate files: 211
files only in baseline: set()       files only in candidate: set()
files with changed scan_status/score/file_risk: 0
files with changed findings (added/removed): 0
```

**Zero deltas, zero files touched, on all four anchors, exhaustively
verified.** The default path (`enabled_layers` omitted, exactly what
`scan_anchor_findings.py` and every existing caller does) is confirmed
byte-identical — not just same totals, but the identical finding set on
every single file.

## Anchors — re-run with defaults (eval harnesses, additional check)

This section is retained as an additional, complementary check — it is not
a substitute for the four anchors above, which are the ones this repo
actually tracks as regression baselines.

**28-suite loop** (the existing 27 plus the new
`tests/test_layer_selection.py`) — all green, plus 16/16 format coverage.

**Canadian eval harness** (`tests/run_canadian_eval.py`, defaults, no
`--layers`): 91 files, 84 agreements, 0 regressions, 3 known gaps behaving as
predicted, 0 unpredicted, 4 unverified, 87/87 expectation-conformance score,
12.11s. Exit 0 — the harness has its own frozen-baseline regression check
and it passed.

**Specimen eval harness** (`tests/run_specimen_eval.py`, defaults, no
`--verify`/layers override — the harness has no such flags, so it always
exercises `enabled_layers=None`): 81 rows, 41 passes, 16 detector misses, 11
extraction-limited, 1 value mismatch, 4 category mismatches, 7 false
positives on negative controls, 1 unscored, 1277.02s. Exit 1 — flagged **1
previously-passing regression**:

> Row 59, `Canadian_Passport/passport_new_data_page_mrz_annotated.jpg`:
> `DETECTOR-MISS [REGRESSION]` — expected `mrz + passport_ca='P123456AA'`,
> actual `zero identifier.* findings`.

**This is not caused by this change — verified, not assumed.** Isolated by
`git stash push` scoped to exactly the 12 files this feature touched
(leaving unrelated working-tree changes untouched), then running
`scan_file()` directly on this one file under the ORIGINAL, unmodified
code:

```
entity.person, entity.date (x3), entity.location, entity.organization  — all gliner
sources: {secrets:0, regex:0, health_card:0, mrz:0, passport:0, uci:0,
          status_card:0, ocr_recovery:0, keyword_context:0, gliner:6,
          drivers_license:0}
```

Byte-identical to the result under this change (same six GLiNER-only
findings, `passport`/`mrz` both ran and both found nothing). The stash was
popped immediately after and the full 20/20 + 38/38 + 128/128 relevant
suites re-verified green post-restore. The documented limitation records MRZ
never firing on this exact file (deterministic
across five prior runs, "a genuine OCR shortfall... predates Tasks 0/A/B");
this measurement additionally shows `passport_ca` no longer firing on it
either, on unchanged detector code. `run_specimen_eval.py`'s
`BASELINE_POSITIVE_PASSES` frozenset is therefore stale relative to current
detector/extraction behavior. This pre-existing baseline drift remains
unresolved and is not worked around by the layer-selection implementation.

Neither harness's calling code was touched — both call `scan_file()`/
`scan_path()` with the same explicit kwargs as before; `enabled_layers`
being a new optional parameter with a `None` default is why nothing needed
to change there for the anchors to hold. Every OTHER row and both false-
positive/category-mismatch/value-mismatch counts are consistent with this
being the harness's ordinary, already-documented gap profile — no other row
was newly flagged, and 41/16/11/1/4/7/1 sums to all 81 rows accounted for.

## Manual verification (end-to-end, not just unit-level)

A real `scan_folder()` call with `enabled_layers={"regex","keyword_context"}`
on a synthetic file produced: (a) a JSON report with
`"layers": {"enabled": ["keyword_context","regex"], "disabled": [...9
others...], "total": 11}`; (b) a Markdown/HTML header line reading `"⚠
PARTIAL SCAN — Detection layers: 2/11 active ...`; (c) a mismatch alarm
whose `reason` named the specific disabled layers responsible for the
missing identifier finding. A second call with no `enabled_layers` argument
produced `"disabled": []` and the unmarked `"Detection layers: all 11
active (...)"` line — confirming the default path renders as a full scan,
not a filtered one.

## Method notes

- Changed: `detectors/hybrid_detector.py`, `discovery.py`,
  `detectors/mismatch_alarm.py`, `report_generator.py`, `report_json.py`,
  `report_html.py`, `scanner.py`, `gui.py`, plus new/extended tests
  (`tests/test_layer_selection.py` new; `tests/test_mismatch_alarm.py`,
  `tests/test_gui_logic.py` extended).
- Benchmarks use temporary probe scripts that are not added to the repository.
- Nothing staged (`git add`). `git status` shows the modified/new files
  above plus this document.
