# New Brunswick Medicare detection fix — evidence and findings

Scope: `detectors/health_card_detector.py` Tier-3 grouped-digit gap for New
Brunswick. Working tree only — nothing staged, committed, or pushed.

## Change 1 — NB display-form pattern (implemented)

`_NB_DISPLAY_RE` accepts NB's printed 3-3-3 layout plus the 6-3/3-6 groupings
PaddleOCR produces when it merges adjacent digit groups (single space or
hyphen separator only). It strips separators, requires exactly 9 digits, and
keeps the existing context gate (`_province_in_window(..., ["nb"])` or
`_has_health_keyword`) unchanged. Contiguous 9-digit runs are deliberately
**not** folded into this pattern — they're left to the existing generic
`LEN_PROVINCES` loop, which still lists `nb` among the length-9 provinces.
Folding contiguous digits in too would have mistagged AB/SK/MB/NU/YK's own
compact 9-digit cards as `health_card_nb` whenever only a generic health
keyword (not a province name) was nearby — verified as a real bug during
development (see "double-fire" note in the code comment) before being
designed out.

### Real OCR evidence (PaddleOCR, this repo's extractor, not reconstructed)

All three images are real photographs, sourced from
`C:\Users\aleks\OneDrive\Desktop\Shared folder\DEMO` (accessible at
`/mnt/c/Users/aleks/OneDrive/Desktop/Shared folder/DEMO` from WSL) and now
also mirrored into `/mnt/demo` (`telechargement.jpg` and
`12516554_10207519563300831_634996911_n.jpg` were copied in during this
session; the other 14 files, including `NB_medicare.png`, were already
there).

| File | Real OCR text |
|---|---|
| `telechargement.jpg` | `Medicare\nT Bruwic\nAssurance-maladie\n999999 999\nLOUISE SMIT\n31/12/1993 expiration 11/2020` |
| `12516554_10207519563300831_634996911_n.jpg` | `Assurance-maladie\nMedicare\nrunwick\nNew\nNouveau\n916 915 916\nPAULM LEWIS\nO\n15/04/1981\n63/2019\nexpirationy` |
| `NB_medicare.png` | `Assurance-mala\nMedicare\nTBrun\nNew\n99999999\nLOUISE SMITH` |

Reproduced via `docs/evidence/ocr_dump_nb.py`.

### Full-pipeline before/after (`scanner.py --path /mnt/demo`)

Captured via a working-tree swap between the pre-change and post-change
`detectors/health_card_detector.py` (`docs/evidence/health_card_detector_before.py`
is the pre-change copy; the real fix is what's now in
`detectors/health_card_detector.py`). Full reports:
`docs/evidence/demo_scan_BEFORE.json` / `demo_scan_AFTER.json`, extracted with
`docs/evidence/extract_nb_findings.py`.

| File | Before | After |
|---|---|---|
| `telechargement.jpg` | no identifier finding, score 7 (LOW) | `identifier.government.health_card_nb` = `999999999`, score 75 (HIGH) |
| `12516554_..._n.jpg` | no identifier finding, score 22 (LOW) | `identifier.government.health_card_nb` = `916915916`, score 77 (HIGH) |
| `NB_medicare.png` | `identifier.government.health_card_ca` = `99999999`, score 76 (HIGH) | **unchanged** — same false generic finding, same score |

`telechargement.jpg`'s recovered value (`999999999`) matches the fixture
expectation exactly. `NB_medicare.png` stays broken by design — Change 1 only
handles *grouped* forms with a separator; its 8-digit contiguous OCR output
has no separator to detect, and is the Change-3 problem below, not this one.

Note on the middle image: PaddleOCR reads the middle group as `915`. There is
no independent source of truth for what the card actually prints there, so
`916915916` is reported as real detector output, not asserted as ground
truth — consistent with why it isn't a scored fixture (see Change 4 below).

**Known OCR-accuracy limitation of the `12516554_..._n.jpg` fixture:**
Change 1 converts this card from a silent miss into a confident wrong
value. Before the change it produced no health-card finding at all
(silent miss); after the change it produces `health_card_nb = 916915916`
at HIGH — but the card is understood to actually print `916 916 916`
(repeated final group), and PaddleOCR misreads the middle group as `915`.
Flagging the file as containing an NB health card is correct; the specific
digit string reported is not. This is a known OCR-accuracy limitation of
this fixture, not a detector-logic defect — the detector faithfully reports
what PaddleOCR extracted, and PaddleOCR extracted the wrong middle group.
This is exactly why this image is excluded from `GROUND_TRUTH.csv` as a
scored pass/fail row (Change 4) and is documented as evidence-only.

## Change 2 — SIN collision (investigated, guard implemented)

### Follow-up: why didn't `916 915 916` produce a SIN finding?

Before adding any guard, the pre-change DEMO scan was checked for whether
`12516554_..._n.jpg`'s OCR'd `916 915 916` — nine digits, 3-3-3, the exact
shape `SIN_RE` matches — had produced a `sin`/`sin_unverified` finding.  It
hadn't. The mechanism, traced through both places a SIN finding can
originate:

- `detectors/detectors.py`'s regex layer (~line 288): `for m in
  SIN_RE.finditer(text): ... if validate_sin(candidate) and (has_context or
  _sin_stands_alone(...)): key = "sin_9digits" if has_context else
  "sin_unverified"`. `validate_sin()` (Luhn + first-digit-not-0/8) is checked
  **first**, unconditionally — the `has_context`/standalone branch only
  decides which of the two keys a value lands in *after* it already passed
  Luhn. A Luhn failure short-circuits both.
- `detectors/keyword_detector.py`'s context layer (~line 691): `if pii_type
  == "sin" and not validate_sin(value): continue` — the exact same gate,
  applied before a `sin_context` candidate (which normalizes to `sin` in
  `hybrid_detector.py`) is added at all.

So **every** path to a `sin` or `sin_unverified` finding — regardless of
layer, regardless of context — requires `validate_sin()` (Luhn) to pass
first. `"_unverified"` here means "no SIN-keyword context nearby," exactly
like `health_card_*_unverified` means "no health keyword nearby" — in both
naming schemes, `_unverified` is never a synonym for "checksum not
checked." Confirmed directly:

```
validate_sin('999999999') -> False
validate_sin('916915916') -> False
validate_sin('916916916') -> False   # the presumed correctly-OCR'd value
detect_pii(<telechargement.jpg OCR text>)   -> {'date': [...]}   # no sin key at all
detect_pii(<12516554 image OCR text>)       -> {'date': [...]}   # no sin key at all
```

`916 915 916` produced no SIN finding for the mundane reason that it fails
Luhn — not because of any context/keyword gap. This directly answers the
open question: NB numbers do **not** "routinely" land in `sin_unverified`.
They can only ever reach `sin` *or* `sin_unverified` in the same narrow
case — a coincidental Luhn pass, the same ~1-in-10 collision rate already
documented elsewhere in this repo (the Nova Scotia Master Number case) —
and both keys carry that identical checksum guarantee. There is no looser,
unchecksummed variant of either key that the guard below could be
mistakenly triggered by. Keying the guard on both `sin` and `sin_unverified`
therefore does **not** need narrowing to `sin` only — both are equally
"checksum-validated SIN" in the sense the guard's own rationale requires,
so the guard was shipped exactly as drafted, unchanged from the earlier
proposal in this document.

**Current reconciliation for `sin` vs. `health_card_*`, before this
change: there wasn't any.** Traced through `hybrid_detector.py`:

- `deduplicate()` (line ~476) only resolves collisions *within* one merged
  key (e.g. two `health_card_nb` hits on the same value) using
  `SOURCE_PRIORITY`. `sin` and `health_card_nb` are different top-level keys
  in `merged`, so `deduplicate()` never compares them.
- `detect_pii_hybrid()`'s reconciliation blocks (lines ~753-834) explicitly
  cover: DOB-vs-date, health_card-vs-phone, **SIN-vs-`passport_generic`**,
  MRZ-vs-passport, and DL-vs-everything-stronger. There is no SIN-vs-health_card
  block.
- `SOURCE_PRIORITY = {"secrets": 4, "regex": 3, "health_card": 3, "mrz": 3,
  "passport": 2, "uci": 2, "status_card": 2, "ocr_recovery": 2,
  "keyword_context": 2, "gliner": 1, "drivers_license": 1}` — confirmed:
  `regex` (where checksum-gated SIN findings originate) and `health_card`
  sit at the identical priority, 3. Even if they were compared, priority
  alone wouldn't break the tie.

**Confirmed reproducible with the new NB pattern in place** (both cases below
run against the actual code, not hypothetical):

```
text = "New Brunswick Medicare\nEmployee SIN: 757 036 793 is on file"
-> identifier.financial.sin              757 036 793   (HIGH,   source=keyword_context)
-> identifier.government.health_card_nb  757036793     (HIGH,   source=health_card)

text = "New Brunswick record\nNumber on file: 757 036 793"
-> identifier.financial_unverified.sin   757 036 793   (MEDIUM, source=regex)
-> identifier.government.health_card_nb  757036793     (HIGH,   source=health_card)
```

`757 036 793` is a genuinely Luhn-valid SIN already used elsewhere in
`tests/test_financial_identifier_tiers.py`. Nothing is lost — the SIN finding
still survives — but a real SIN now *also* gets reported as a New Brunswick
health card in any document where a Luhn-valid, correctly-3-3-3-grouped
9-digit number sits within 50 characters of the literal phrase "new
brunswick" or one of the generic health keywords (`medicare`, `health card`,
`health number`, `phn`, `uli`, `care card`, `ramq`, `msi`, `ohip`). This is a
narrow but real scenario — e.g., any form listing both a SIN and an NB
address, or a SIN near unrelated "Medicare" boilerplate. Roughly 1 in 10
random 9-digit strings pass Luhn (documented elsewhere in this repo, re: the
Nova Scotia Master Number/embedded-SIN case), so this is not vanishingly
rare once NB context and a SIN-shaped number legitimately co-occur.

None of the task's three real demo images trigger the collision itself —
`999999999`, `916915916`, and `916916916` (the presumed correct value) all
fail Luhn — so the required fixture and the DEMO re-scan are unaffected by
whether the guard is present or not.

**Shipped:** `hybrid_detector.py`, immediately before the MRZ-vs-passport
reconciliation block, mirroring the existing SIN-vs-`passport_generic`
precedent (~line 785):

```python
# Reconcile: a checksum-validated SIN beats a same-digit health_card_nb
# match, for the same reason it already beats passport_generic above — NB's
# printed 3-3-3 grouping is indistinguishable from a SIN's own printed
# form, and health_card_nb (Tier 3: format + context, no checksum) is
# weaker evidence than a Luhn-validated SIN. Both "sin" and "sin_unverified"
# require validate_sin() (Luhn) to have already passed — every path that
# can populate either key (detectors.py's regex layer and
# keyword_detector.py's context layer) gates on it before emitting either
# one; "_unverified" here means "no SIN-keyword context nearby", not
# "checksum not checked" (mirrors health_card's own *_unverified naming).
# So both keys are equally legitimate checksum evidence for this trade.
if "health_card_nb" in merged and ("sin" in merged or "sin_unverified" in merged):
    sin_digits = {
        re.sub(r"\D", "", d["value"])
        for k in ("sin", "sin_unverified") if k in merged
        for d in merged[k]
    }
    merged["health_card_nb"] = [
        d for d in merged["health_card_nb"]
        if re.sub(r"\D", "", d["value"]) not in sin_digits
    ]
    if not merged["health_card_nb"]:
        del merged["health_card_nb"]
```

Note (not fixed here, out of scope): the existing SIN-vs-`passport_generic`
block it mirrors only checks the `sin` key, not `sin_unverified` — given the
mechanism finding above, that block has the same latent gap this one closes,
but touching it wasn't asked for and isn't part of this change.

### Guard verification (all three required scenarios, real runs)

| Check | Result |
|---|---|
| `telechargement.jpg` | `health_card_nb = 999999999`, score 75 — unchanged |
| `12516554_..._n.jpg` | `health_card_nb = 916915916`, score 77 — unchanged |
| `"New Brunswick Medicare\nEmployee SIN: 757 036 793 is on file"` | `identifier.financial.sin = 757 036 793` (HIGH); **no** `health_card_nb` finding |

Full re-scan captured in `docs/evidence/demo_scan_AFTER_guard.json`.

`tests/test_financial_identifier_tiers.py` and `tests/test_health_cards.py`
both stay green with the guard applied (neither exercises this collision,
and the guard only ever removes `health_card_nb` findings — it never touches
`sin`/`sin_unverified`).

## Change 3 — 8-digit fallback (investigated, NOT changed)

`NB_medicare.png`'s `99999999` reaches `detect_health_cards()`'s generic Tier-3
fallback (`health_card_detector.py`, the `elif _has_health_keyword(window):
add("health_card_ca", ...)` branch) because 8 digits falls in
`LEN_PROVINCES[8] = ["pe", "nt"]`, neither province is named nearby, and
"Medicare" is a generic health keyword — so it lands at HIGH via
`identifier.government.health_card_ca` from an 8-digit OCR fragment of what
was really a 9-digit NB card.

**Blast radius of the elif branch this bug uses (measured, not guessed):**
this is the *same* branch used across all four `LEN_PROVINCES` lengths
(8, 9, 10, 12), and it is deliberately exercised — not accidentally hit — by
five existing, passing assertions:

- `tests/test_health_cards.py::SHOULD_MATCH` — `"Generic keyword only"`
  (`"health card: 123456789"`, 9 digits) expects `health_card_ca` HIGH.
- `tests/test_health_cards.py::SHOULD_MATCH` — `"Unnamed checksum mismatch
  stays generic Canadian"` (`"Health card number: 9123456781"`, 10 digits)
  expects `health_card_ca` HIGH.
- `tests/test_health_cards.py::SHOULD_MATCH` — `"Province context does not
  cross an intervening health value"` also asserts a `health_card_ca` HIGH
  finding (10 digits).
- `tests/canadian_eval_docs/manifest.json` — `on_healthcard_checksum_invalid_generic_01.txt`
  and `bc_healthcard_checksum_invalid_generic_01.txt` (both 10 digits),
  each with the explicit note: *"No province is named; a 10-digit value can
  legitimately be a Nova Scotia health number, so generic detection is
  defensible."* Both are scored `OK` in the 87/87 Canadian-eval baseline.
- `tests/format_data/manifest.json`'s `pdf_scanned_2page.pdf` fixture also
  expects `identifier.government.health_card_ca` HIGH at value
  `9123456780` (10 digits) — this is one of the exact scores the task
  requires to stay unchanged (87 in the CPU list).

Separately, `tests/external_enron/EVALUATION.md` records 19 `health_card_ca`
findings firing on a 1000-message Enron slice (US corporate email, no
Canadian content) — already documented there as an accepted
foreign-format-collision class, same shape as the Octopii/ai4privacy
collisions, not previously flagged as something to fix.

**Why I didn't change it:** the demo false positive and the "defensible"
10-digit test cases are the *same code path* exercising the *same
ambiguity* — "a bare N-digit number near a generic health keyword, with no
province named, could belong to any of the provinces that use that length."
The manifest's own reasoning for keeping 10-digit generic hits at HIGH
("could legitimately be Nova Scotia") applies just as well to 8-digit hits
("could legitimately be PE or NT") — the detector has no way to distinguish
a genuine 8-digit PE/NT card from an OCR-truncated 9-digit NB card, since
both produce an indistinguishable bare 8-digit string next to "Medicare."
A blanket severity change (e.g., demoting the whole elif branch to a
`_ca_unverified`-style MEDIUM tier) would directly regress the three
`test_health_cards.py` cases and the two Canadian-eval `OK` rows above, and
would change `pdf_scanned_2page.pdf`'s score in `test_format_coverage.py`
— which the task requires to stay at 87. An 8-digit-only carve-out is
possible but arbitrary: no test or eval fixture currently establishes
whether an unattributed 8-digit generic hit *should* be HIGH or not (unlike
the 10-digit case, which has an explicit documented rationale either way),
so narrowing just that one length would be an undocumented, unmeasured
policy call rather than a fix grounded in existing test intent.

**Recommendation:** leave the elif branch as-is; this is a judgment call for
the owner, not something to ship silently in this change. If it's worth
revisiting, the concrete options are (a) accept it as a known,
already-documented OCR-degradation limitation (same treatment as
`NB_medicare.png` being "cropped, unusable as a fixture" already implies),
or (b) split length 8 into its own reduced-confidence tier with an
owner-approved new manifest row establishing the intended behavior first,
so the change has the same kind of explicit test backing the 10-digit case
already has.

## Verification results

- `tests/test_health_cards.py`: 43/43 passed.
- All 33 `tests/test_*.py` suites: 33/33 green (28 run via
  `docs/evidence/run_all_tests.sh`, plus `test_layer_selection.py`,
  `test_ocr_recovery.py`, `test_status_card.py`, `test_uci.py` run
  separately, plus `test_format_coverage.py` below). Re-run after the
  Change-2 guard landed; still 33/33.
- `test_format_coverage.py`, CPU container: 16/16 passed, scores
  `79, 0, 91, 79, 0, 87, 78, 78, 78, 78, 78, 78, 80, 80, 83, 79` —
  **exact match** to the required list.
- `test_format_coverage.py`, GPU container: 16/16 passed, **same exact
  score list**, using
  `docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm
  securescan-gpu python tests/test_format_coverage.py`.
  **Correction to an earlier report in this document/session:** a prior run
  without `-f docker-compose.gpu.yml` failed at PaddleOCR's CUDA import
  (`ImportError: libcuda.so.1`), which I incorrectly reported as "this
  machine has no physical GPU." That was wrong — `nvidia-smi` confirms a
  real NVIDIA GeForce RTX 4070 is present and working via WSL2 GPU
  passthrough. Without `docker-compose.gpu.yml`'s device reservation, the
  container has no access to the host's GPU libraries at all, so
  PaddleOCR's `gpu:0` request (set by `docker-compose.yml`'s
  `SECURESCAN_PADDLEOCR_DEVICE` for the GPU service) fails to even find
  `libcuda.so.1` and errors out instead of falling back cleanly. With the
  correct override supplying the actual device, GPU format coverage runs
  clean with no fallback warnings.
- `tests/run_canadian_eval.py` (not in the 33-suite loop, but this repo's
  own convention is to run it before any detector change): 84 OK
  agreements, **0 regressions**, 3 known gaps behaving as predicted, 0
  unpredicted gaps, 4 unverified, 87/87 expectation-conformance. Re-run
  after the Change-2 guard landed; identical numbers.
  `nb_healthcard_compact_01.txt` (the corpus's one NB fixture, contiguous
  9-digit form) is among the OK agreements, confirming `nb` staying in
  `LEN_PROVINCES[9]` preserved its existing behavior exactly.
- DEMO folder re-scan: see the before/after table above, plus the
  Change-2 guard-verification table (`demo_scan_AFTER_guard.json`).
