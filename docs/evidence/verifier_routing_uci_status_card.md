# Routing uci/status_card into the verifier — fix + measurement

Measured 2026-08-09. Verification stays OFF by default
(`config.LLM_VERIFICATION_ENABLED = False`, unchanged). **The routing fix
below is shipped in this commit-to-be regardless of the measurement
outcome** — it corrects a real inconsistency and cannot affect production
output while verification is off. The measurement answers a separate
question: is the verifier actually useful/safe on these two types. Nothing
was staged.

## The fix

`ROUTABLE_SOURCES` routed `passport`/`drivers_license`/`keyword_context` on
the stated principle that checksum-less findings need a second opinion, but
left out `uci` and `status_card` — both equally checksum-less
(`tests/canadian_eval_docs/manifest.json`: "no public checksum exists")
— with no reasoning recorded anywhere for the omission.

**Verified exact source strings, not guessed:** `detectors/hybrid_detector.py`
normalizes both detectors' raw output with `"source": "uci"`
(`normalize_uci_results`, line 378) and `"source": "status_card"`
(`normalize_status_card_results`, line 397) — these are also the literal
`SOURCE_PRIORITY` keys (`"uci": 2`, `"status_card": 2`). **No discrepancy**:
the strings the task assumed are exactly the strings the detectors emit.

`detectors/llm_verifier.py`:

```python
ROUTABLE_SOURCES = {"passport", "drivers_license", "uci", "status_card", "keyword_context"}
```

The routing-rules comment block was updated to document `uci`/`status_card`
alongside `passport`/`drivers_license` and to record why they were missing
(no reasoning was ever written down — this is a plain gap-close, not a
judgment call being reversed). Nothing else in the file changed: same
prompt, same model, same thresholds, same `KEYWORD_CONTEXT_ROUTABLE_CATEGORIES`,
same `config.LLM_VERIFICATION_ENABLED`.

## Confirming nothing else is missing — every source in `SOURCE_PRIORITY`

| Source | Priority | Validation | Routed (before) | Routed (after) | Note |
|---|---:|---|---|---|---|
| `secrets` | 4 | Signature/structure-based | No | No | Consistent — validated, judge is redundant |
| `regex` — `sin`/`credit_card` (context-tiered) | 3 | Luhn checksum | No | No | Consistent |
| `regex` — `phone`/`email`/`postal_code`/`ip`/`url` | 3 | **None** — format-only | No | No | **Further inconsistency, not fixed.** Same checksum-less status as `passport`/`drivers_license`, but blanket-excluded because it shares the `regex` source label with checksummed categories. The exact same category (e.g. `contact.phone`) IS routed when produced by `keyword_context` instead — routing currently depends on which layer happened to also match, not on the category's own validation status. |
| `regex` — `sin`/`credit_card`, checksum-invalid `_unverified` tier | 3 | **Checksum FAILED** | No | No | **Further inconsistency, not fixed.** A checksum that failed arguably needs a second opinion *more* than a checksum that was never attempted, but the predicate operates at the source level, not the per-finding validation outcome, so this tier is silently swept into the same exclusion as the checksum-valid tier. |
| `health_card` (valid tier) | 3 | Checksum (ON/BC formulas) | No | No | Consistent |
| `health_card_unverified` (checksum-invalid, same source) | 3 | **Checksum FAILED** | No | No | Same further inconsistency as the regex unverified tier above — one more instance of it, not a new mechanism. |
| `mrz` | 3 | ICAO 9303 7-3-1 checksum | No | No | Consistent |
| `passport` | 2 | None | Yes | Yes | Consistent |
| `uci` | 2 | None | **No** | **Yes** | Added |
| `status_card` | 2 | None | **No** | **Yes** | Added |
| `ocr_recovery` | 2 | Checksum (gated by construction — only emits on exactly one validating reconstruction) | No | No | Consistent |
| `keyword_context` — `dob`/`email`/`phone`/`postal_code` | 2 | None | Yes | Yes | Consistent |
| `keyword_context` — `sin`/`ssn`/`credit_card` | 2 | Checksum | No (category-filtered) | No | Consistent — deliberate `KEYWORD_CONTEXT_ROUTABLE_CATEGORIES` exclusion |
| `gliner` | 1 | None | No | No | Different mechanism, not a checksum inconsistency: always `entity.*` → LOW severity floor, excluded before source is even checked. Structurally unreachable regardless of `ROUTABLE_SOURCES` membership. |
| `drivers_license` | 1 | None | Yes | Yes | Consistent |

No source string appears in any detector's output without a `SOURCE_PRIORITY`
entry — confirmed by grep; a missing entry would `KeyError` at merge time
(`hybrid_detector.py`'s `SOURCE_PRIORITY[d["source"]]` lookup), so this is a
structural guarantee, not just an audit.

**Two further inconsistencies are reported, not fixed**, per the task's
instruction: (1) checksum-less `regex`-sourced categories (phone/email/
postal/IP/URL) are excluded for sharing a source label with checksummed
ones, and (2) checksum-*failed* `_unverified` tiers within `regex` and
`health_card` are excluded by the same source-level blanket rule, despite
arguably needing a second opinion more than a checksum-less finding does.
Both stem from the same root cause: `is_routable()` decides on `source`
(and, only for `keyword_context`, `category`) — never on the per-finding
validation outcome. Fixing either would be a materially different, larger
change than adding two missing strings to a set, and is left for separate
authorization.

## Suite loop and anchors

All 27 standalone suites green after the fix (`test_llm_verifier.py`
included — no test hardcodes `ROUTABLE_SOURCES`' exact contents, confirmed
by grep before running).

**Anchors are unaffected — confirmed, not assumed.** `config.LLM_VERIFICATION_ENABLED = False`
(unchanged), and every anchor harness (`run_canadian_eval.py` resolves
`verify_enabled` from this same config default absent `--verify`;
`run_specimen_eval.py` hardcodes `scan_file(..., verify=False)` directly,
not even config-driven) never reaches `is_routable()` — `detect_pii_hybrid()`
only calls `verify_findings()` inside `if verify:`
(`hybrid_detector.py`). `ROUTABLE_SOURCES` is unreachable code on every
anchor's actual execution path. This was confirmed by reading the gate
before relying on it, not assumed — the 27-suite loop above is the only
run needed against this change.

## Measurement 1 — three IRCC negative-control specimens, `--verify`

`study_permit_2022.jpg`, `visitor_record_2022.jpg`, `work_permit_previous_2022.jpg`
— all ground-truth NEGATIVE (real UCI field blurred/blank on each per
`GROUND_TRUTH.csv`); each carries the specimen template's own format-example
legend text as three UCI-shaped strings.

```
routed=9  demoted=6  errors=0  legitimate=3
```

| File | Value | Verdict | Reason |
|---|---|---|---|
| `study_permit_2022.jpg` | `0123-4567` | FALSE_POSITIVE | "part of an application form, not a government-issued ID" |
| `study_permit_2022.jpg` | `0123456789` | FALSE_POSITIVE | "does not appear to be formatted as an UCI...in the given context" |
| `study_permit_2022.jpg` | `12345678` | **LEGITIMATE (wrong)** | *(none — kept without demotion)* |
| `visitor_record_2022.jpg` | `0123-4567` | **LEGITIMATE (wrong)** | *(none)* |
| `visitor_record_2022.jpg` | `0123456789` | FALSE_POSITIVE | "does not match the typical format...as described in the context" |
| `visitor_record_2022.jpg` | `12345678` | FALSE_POSITIVE | "context does not strongly indicate it's from a Canadian government document" |
| `work_permit_previous_2022.jpg` | `0123-4567` | FALSE_POSITIVE | "part of client information and not a government-issued ID" |
| `work_permit_previous_2022.jpg` | `0123456789` | FALSE_POSITIVE | "usually includes letters and spaces" |
| `work_permit_previous_2022.jpg` | `12345678` | **LEGITIMATE (wrong)** | *(none)* |

**6/9 correct, 3/9 wrong (missed) — not the best-case 9/9, reported as-is.**
The three misses aren't clustered on one "hard" value: `0123-4567`,
`0123456789`, and `12345678` each get both verdicts somewhere across the
three files — the model isn't applying a stable rule, it's reading local
context per-call and landing inconsistently on identical strings. This
reproduced byte-identical (same 6/9 split, same reasons) in Measurement 2's
independent full-corpus run below.

## Measurement 2 — full 81-row specimen corpus, `--verify`

Scanned all 81 `GROUND_TRUTH.csv`-referenced files (`run_specimen_eval.py`'s
own path resolution, so old→new negative-control path remapping was
honored) — 81 files, 1179.20s, `routed=38 demoted=18 errors=0`.

**Every `uci`/`status_card`-sourced finding, all 11 that exist in this
corpus, with correctness against `GROUND_TRUTH.csv`:**

| File | Value | Verdict | Ground truth | Correct? |
|---|---|---|---|---|
| `Work_Permit_withUCI.jpg` | `1127041820` | LEGITIMATE | POSITIVE, genuine primary UCI | **Correct** |
| `Work_Permit_withUCI.jpg` | `1104062315` | LEGITIMATE | POSITIVE, genuine second UCI (Remarks line) | **Correct** |
| `study_permit_2022.jpg` | `0123-4567` | FALSE_POSITIVE | NEGATIVE (legend text) | Correct |
| `study_permit_2022.jpg` | `0123456789` | FALSE_POSITIVE | NEGATIVE | Correct |
| `study_permit_2022.jpg` | `12345678` | LEGITIMATE | NEGATIVE | **Wrong (missed)** |
| `visitor_record_2022.jpg` | `0123-4567` | LEGITIMATE | NEGATIVE | **Wrong (missed)** |
| `visitor_record_2022.jpg` | `0123456789` | FALSE_POSITIVE | NEGATIVE | Correct |
| `visitor_record_2022.jpg` | `12345678` | FALSE_POSITIVE | NEGATIVE | Correct |
| `work_permit_previous_2022.jpg` | `0123-4567` | FALSE_POSITIVE | NEGATIVE | Correct |
| `work_permit_previous_2022.jpg` | `0123456789` | FALSE_POSITIVE | NEGATIVE | Correct |
| `work_permit_previous_2022.jpg` | `12345678` | LEGITIMATE | NEGATIVE | **Wrong (missed)** |

**Independent reproduction of Measurement 1: identical 6/9 split, identical
reasons, on the three shared files** — the model's verdicts are stable
across separate process runs (temperature 0), not just within one.

**No `status_card`-sourced finding exists anywhere in this corpus.** The
genuine registration number `9997001801` (`scis_certificate.jpeg`/`.png`,
POSITIVE) is detected, but as `identifier.government_unverified.health_card_bc`
via the `health_card` source — a pre-existing BC-health-card-checksum
collision on the SCIS registration number's digit shape, unrelated to this
task and untouched by it. Since `health_card` is (correctly) never routed,
this genuine value never reaches `is_routable()` as a `status_card` finding
at all — it's a detection/taxonomy question, not a verifier-routing one, and
outside verifier-routing scope.
`scis_certificate_sample_no_numbers.jpg` legitimately yields nothing
(field is blurred, per ground-truth note "may legitimately yield nothing").
The one other genuine positive, `Visitors_record_notobfuscated.jpg`'s UCI
`87557383`, is **not detected at all** — a pure detector/extraction miss,
also outside verifier routing because nothing reaches the routing stage.

**Net effect: only 1 of 5 ground-truth-positive UCI/status_card rows in this
81-file corpus actually reaches a routing decision.** It passed cleanly
(2/2 correct). This is real, positive evidence, but it's thinner than "81
files" suggests — most of the positive coverage never gets far enough to
test the verifier at all, for reasons outside verifier routing.

## Measurement 3 — 88-file Canadian typed corpus, `--verify`

`tests/canadian_eval_data` (91 files on disk; 88 of them `.txt`). 485.75s,
`routed=29 demoted=5 errors=0`.

| File | Value | Category | Verdict | Ground truth (`manifest.json`) | Correct? |
|---|---|---|---|---|---|
| `uci_compact_10_03.txt` | `1234567890` | `identifier.government.uci` | LEGITIMATE | genuine synthetic UCI | **Correct** |
| `uci_compact_8_01.txt` | `12345678` | `identifier.government.uci` | LEGITIMATE | genuine synthetic UCI | **Correct** |
| `uci_display_2_4_4_04.txt` | `12-3456-7890` | `identifier.government.uci` | LEGITIMATE | genuine synthetic UCI | **Correct** |
| `uci_display_4_4_02.txt` | `1234-5678` | `identifier.government.uci` | LEGITIMATE | genuine synthetic UCI | **Correct** |
| `status_card_registration_01.txt` | `1234567890` | `identifier.government.status_card_registration` | **FALSE_POSITIVE** | genuine synthetic registration, POSITIVE, HIGH expected | **Wrong** |
| `DSC_20260724_084512_01.txt` | `1234567890` | `identifier.government.status_card_registration` | **FALSE_POSITIVE** | genuine synthetic registration, POSITIVE, HIGH expected | **Wrong** |

**UCI: 4/4 correct, 0 wrongly demoted. status_card: 0/2 correct, 2/2
wrongly demoted** — both reasoned the same way ("a numeric string" / "does
not appear to be formatted as a...status card registration number"),
demoting a real, correctly-detected identifier both times it was tested.

## Combined safety picture — uci vs. status_card are not the same result

| Type | Genuine values tested | Wrongly demoted | Fake/negative values tested | Correctly caught |
|---|---:|---:|---:|---:|
| `uci` | 6 (4 typed + 2 specimen) | **0/6** | 9 (3 IRCC specimens × 3, reproduced twice) | 6/9 (twice) |
| `status_card` | 2 (typed only) | **2/2** | 0 tested | — |

Under the stated safety bar — "genuine UCI values must not be demoted —
a lost real UCI is worse than nine retained false positives" — **uci clears
it (0 losses across every genuine value reachable in this measurement) and
status_card fails it outright (2/2 genuine losses on the only real-world
test available).** These are opposite results produced by the identical
one-line fix, because the fix routes by source label, not by type-specific
performance — which is exactly why this section is reported separately
instead of folded into one aggregate "the fix works" number.

## Acceptance

Per the pre-registration: **the routing fix ships regardless** — it closes
a real, undocumented inconsistency, and with verification off by default it
cannot change production output. The measurement does not recommend
enabling verification: `uci`'s result is encouraging but thin (n=6 genuine
values, all from synthetic/specimen sources, no real-world corpus
equivalent to `verifier_specimen_vs_real.md`'s real photographed test yet
exists for this type), and `status_card`'s result is actively bad on the
only two real tests available. One favourable-looking number for one of the
two newly-routed types is not a basis for enabling anything — consistent
with the standing recommendation across every prior verifier measurement in
this repo: **leave verification off.**

## On telling the verifier "this is a test, accept mock documents"

This was already tried and measured, not guessed at:
`docs/evidence/verifier_model_comparison.md`'s Arm 2 added exactly this
instruction to the system prompt — "synthetic, specimen, sample,
placeholder-looking, repeated-digit, or fabricated values remain legitimate
when they match the claimed type; the judge evaluates type, not document
authenticity" — plus the ten provincial format definitions, across all four
tested models. Result: **it made every single model worse**, not better —
wrong demotions rose 12→36 (qwen 3B), 18→30 (qwen 7B), 27→20 was the only
partial improvement (Ministral, still far from safe), and 0→2 (Llama 8B,
which wasn't "safe," it was refusing to demote anything at all). The
document's own conclusion: *"Format knowledge alone is not a viable fix"* —
models sometimes reasoned a value failed a rule that was supplied to them
verbatim in the prompt. Do not repeat this specific change: the recorded
measurement is directly unfavorable.

## Method notes

- Changed: `detectors/llm_verifier.py` (`ROUTABLE_SOURCES` + its comment
  block only). No prompt, model, threshold, `KEYWORD_CONTEXT_ROUTABLE_CATEGORIES`,
  or `config.LLM_VERIFICATION_ENABLED` change.
- `run_specimen_eval.py`/`run_canadian_eval.py` were not modified; both were
  bypassed via direct `scanner.py --verify` runs since neither harness script
  exposes a verify flag (the specimen harness hardcodes `verify=False`).
- Corpora scanned from real copies (not symlinks — `discovery.py` denies
  symlinks unconditionally, confirmed before building the scratch corpus).
- Nothing staged (`git add`). `git status` shows only the modified
  `detectors/llm_verifier.py` and this evidence file.
