# Does "address" need disambiguating siblings? Arms B/C/D vs. baseline A

Measurement only. `detectors/gliner_detector.py`'s production `LABELS`,
`detectors/hybrid_detector.py`'s `PII_TAXONOMY`, and `config.py` were never
edited on disk. This tests the owner's hypothesis against
`docs/evidence/gliner_address_label.md`'s two rejection reasons — (1) 52.7%
of `address` findings were email/domain confusion, (2) all four anchors lost
findings net (11 added, 37 removed) — by giving `gliner_medium-v2.1` the
disambiguating sibling labels `gliner_multi_pii-v1` was fine-tuned with, and
re-measuring on the same four anchors.

**Verdict: all three arms (B, C, D) are REJECTED under the pre-registered
acceptance criterion.** Every arm still loses anchor findings net (B: -26,
C: -23, D: -7) — the displacement problem the criterion says is
blocking regardless of email-confusion fixes. `'16 Samplewood Pk SW'`, the
named regression check, stays lost under every label in all three arms,
full sibling set included. The owner's mechanism hypothesis about *why*
`address` misfires is directionally correct as far as it goes (§3), but
fixing that mechanism does not fix the thing that actually disqualifies
adoption.

## Methodology

- **Arms**, all `gliner_medium-v2.1`, production ONNX backend,
  `config.GLINER_ONNX_THREADS` unchanged:

  | Arm | `gd.LABELS` |
  |---|---|
  | A (baseline) | `person, organization, location, date` |
  | B | + `address` |
  | C | + `address, email address` |
  | D | + `address, email address, postal code, phone number` |

- **Harness.** `docs/evidence/gliner_address_anchor_scan.py` gained an
  `--arm {A,B,C,D}` flag (replacing the earlier single `--address`
  flag) driving a table, `ARM_LABELS`, of which extra labels to append.
  Each extra label gets one `entity.<label_with_underscores>` taxonomy
  mapping patched into `hd.PII_TAXONOMY` in-process (mirroring
  `person`/`organization`/`location`/`date`'s existing pattern; LOW risk via
  the existing `entity` parent-category default) so e.g. `"phone number"`
  lands at `entity.phone_number` instead of falling through to
  `uncategorized.phone number`/UNKNOWN. Nothing is written to
  `detectors/*.py`; identical to the earlier measurement approach, but
  parameterized over 4 arms instead of 2.
- **Anchors only**, real `discovery.scan_file()` production path,
  `verify=False`, normal NER policy, same 211 files (`stress` 174,
  `format` 18, `external_octopii` 8, `test` 11) as every prior anchor
  measurement in this evidence set. Per the task, the full 1381-file corpus
  was **not** rerun. Wall time was flat across all four arms (782-800s for
  211 files) — consistent with the prior finding that GLiNER label-count
  changes cost approximately nothing.
- **A machine reboot occurred mid-run** (uncorrelated infrastructure event,
  `/tmp` wiped, `.git` also threw transient object errors that cleared after
  the reboot) partway through the first attempt at this measurement; all
  four arms were restarted from scratch afterward on the unmodified,
  already-committed harness script. The numbers below are from that
  complete, uninterrupted rerun.

## 1. Anchor totals vs. arm A — every changed finding

| Arm | Added | Removed | Net |
|---|--:|--:|--:|
| B (+address) | 11 | 37 | **-26** |
| C (+address, email address) | 19 | 42 | **-23** |
| D (+address, email address, postal code, phone number) | 51 | 58 | **-7** |

More sibling labels shrink the net loss (B worst, D least bad) but **never
cross zero** — D still removes more than it adds. This is the pre-registered
concern realized: more labels split the same per-chunk span-classification
budget further, so even as the *target* label (`address`) gets cleaner
disambiguation from its siblings, the *other* four labels
(`person`/`organization`/`location`/`date`) lose more classifications to
the growing competition, and arm D's own three new sibling labels introduce
their own separate misfires (see the `Phone`/`000` cases in §5).

### Arm B — every changed finding (11 added / 37 removed)

Identical to `gliner_address_label.md`'s anchor section, reproduced here as
the arm-B baseline for comparison with C/D:

| File | Removed | Added |
|---|---|---|
| `external_octopii/dummy-aadhaar.png` | `entity.location`: `D.N.Singh Road`, `Hathibaug Mazgaon`, `Hendre Buldg No.17`, `Salarpuria Touchstone` | `entity.address`: `P.O. Box No. 1947`, `Room no.3` |
| `external_octopii/dummy-debit-card.jpg` | `entity.location`: `5422`, `BE` | — |
| `external_octopii/dummy-drivers-license-maharashtra.jpg` | `entity.location`: `BABUKHAN`, `BAIGANWADI`, `GOVANDI` | `entity.address`: `BABUKHAN`, `BAIGANWADI`, `GOVANDI` (clean relabel) |
| `external_octopii/dummy-hong-kong-resident-id.png` | `entity.date`: `26-11-18` | — |
| `external_octopii/dummy-ssn.jpg` | `entity.location`: `USA` | — |
| `format/deck_summary.pptx` | `entity.location`: `T2X 1V4` | — |
| `format/pdf_no_pii.pdf` | `entity.location`: `project board` | — |
| `format/pdf_scanned_2page.pdf` | `entity.location`: `BC`, `Ontario` | — |
| `stress/edge_cases/very_long_single_line.txt` | `entity.date`: `sprint`; `entity.location`: `payment region`, `revenue region`; `entity.organization`: `logistics vendor` | — |
| `stress/finance/.../filler_0091.md` | `entity.person`: `vendor`, `worker` | — |
| `test/specimen_sin_01.jpg` | `entity.location`: `PO Box 000` | `entity.address`: `PO Box 000` (relabel); `entity.organization`: `El Program`, `Service` |
| `test/specimen_licence_02.jpg` | `entity.location`: `Sampletown AB` | — |
| `test/specimen_licence_01.jpg` | `entity.location`: `Sampletown AB` | — |
| `test/specimen_pr_card_01.jpg` | `entity.location`: `Place of Landing`, `Taile`; `entity.person`: `EXAMPLE`, `Sample` | `entity.location`: `BRUN`, `JUIN` |
| `test/specimen_benefits_02.jpg` | `entity.location`: **`16 Samplewood Pk SW`** | — |
| `test/specimen_licence_03.jpg` | `entity.location`: `Alberta` | `entity.date`: `2030` |
| `test/specimen_pr_card_02.jpg` | `entity.location`: `Nam`; `entity.person`: `Sample` | — |
| `test/specimen_passport_01.jpg` | `entity.location`: `PASAS`, `Place d bith`, `Rodas` | — |
| `test/specimen_benefits_01.jpg` | `entity.location`: `DENTAL OFFICE` | — |
| `test/specimen_benefits_03.jpg` | `entity.person`: `Dr. Example` | — |

### Arm C — every changed finding (19 added / 42 removed)

| File | Removed | Added |
|---|---|---|
| `external_octopii/dummy-aadhaar.png` | `entity.location`: `D.N.Singh Road`, `Hathibaug Mazgaon`, `Hendre Buldg No.17`; `entity.person`: `Deepak Vasant Surve` | `entity.address`: `P.O. Box No. 1947`; `entity.email_address`: `help@ uidai.gov.`, `help@uidai.gov.` |
| `external_octopii/dummy-debit-card.jpg` | `entity.location`: `5422`, `BE` | `entity.date`: `VALD` |
| `external_octopii/dummy-hong-kong-resident-id.png` | `entity.date`: `26-11-18` | — |
| `external_octopii/dummy-passport-britain.jpg` | `entity.date`: `20 SEP/SEP 06` | — |
| `external_octopii/dummy-passport-ukraine.jpg` | `entity.date`: `AUG 19` | — |
| `external_octopii/dummy-ssn.jpg` | `entity.location`: `USA` | — |
| `format/deck_summary.pptx` | `entity.location`: `T2X 1V4` | `entity.address`: `T2X 1V4` (relabel — into the **wrong** sibling; `postal code` was available in this arm and not used) |
| `format/docx_full.docx` | — | `entity.email_address`: `test@example.com` |
| `format/email_notice.eml` | `entity.organization`: `records@example.org` | `entity.email_address`: `records@example.org` (correct relabel) |
| `format/pdf_no_pii.pdf` | `entity.location`: `project board` | — |
| `format/pdf_scanned_2page.pdf` | `entity.location`: `BC`; `entity.person`: `AB123456` | — |
| `format/xlsx_multisheet.xlsx` | — | `entity.email_address`: `test@example.com` |
| `stress/edge_cases/very_long_single_line.txt` | `entity.date`: `sprint`; `entity.location`: `payment region`, `revenue region`; `entity.organization`: `logistics vendor` | `entity.organization`: `vendor inventory platform` |
| `stress/.../salted_email_0168.txt` | — | `entity.email_address`: `noah.kaplan@acme-industries.net` (correct — new, arm A never found this under any label) |
| `stress/.../salted_email_0166.md` | `entity.person`: `noah.kaplan` | `entity.email_address`: `noah.kaplan` (wrong span — dropped the `@domain`) |
| `stress/finance/.../filler_0091.md` | `entity.person`: `vendor`, `worker` | — |
| `stress/.../salted_aws_secret_key_0164.txt` | `entity.organization`: `logistics` | — |
| `stress/sales/.../filler_0078.md` | `entity.person`: `customer` | — |
| `test/specimen_sin_01.jpg` | `entity.location`: `PO Box 000` | `entity.address`: `PO Box 000` (relabel); `entity.organization`: `Service` |
| `test/specimen_licence_02.jpg` | — | `entity.date`: `10 FEB 2024` |
| `test/specimen_licence_01.jpg` | `entity.location`: `Sampletown AB` | `entity.date`: `10 FEB 2024` |
| `test/specimen_pr_card_01.jpg` | `entity.location`: `Place of Landing`; `entity.person`: `EXAMPLE`, `Sample` | `entity.location`: `JUIN` |
| `test/specimen_benefits_02.jpg` | `entity.date`: `06-Feb-2030`, `6-Feb-30`, `Thu Jan 8, 2031`; `entity.location`: **`16 Samplewood Pk SW`** | — |
| `test/specimen_licence_03.jpg` | — | `entity.date`: `2030` |
| `test/specimen_pr_card_02.jpg` | `entity.location`: `Nam` | — |
| `test/specimen_passport_01.jpg` | `entity.location`: `PASAS`, `Place d bith`, `Rodas` | — |
| `test/PassportrandompagenoPIInoface.jpg` | `entity.location`: `OESSUS` | `entity.date`: `2022` |
| `test/specimen_benefits_01.jpg` | `entity.location`: `DENTAL OFFICE`; `entity.organization`: `Sample Dental` | — |
| `test/specimen_benefits_03.jpg` | `entity.location`: `200 Example Ave SW`; `entity.person`: `Dr. Example` | `entity.email_address`: `000` (garbage) |

### Arm D — every changed finding (51 added / 58 removed)

Arm D's diff is the largest — most of the new volume is in `stress`, where
adding `postal code`/`phone number` visibly increases relabeling churn
across `entity.person`/`entity.organization` on short filler files
(`'customer'`, `'worker'`, `'vendor'`, `'Headcount'`, `'Sprint'` appearing
or disappearing) with no relationship to addresses at all — direct evidence
that the fourth and fifth extra labels are still displacing the core four,
not just soaking up address-related noise:

| File | Removed | Added |
|---|---|---|
| `external_octopii/dummy-PAN-India.jpg` | `entity.person`: `LOOA` | — |
| `external_octopii/dummy-aadhaar.png` | `entity.location`: `Bengaluru`, `D.N.Singh Road`, `Hathibaug Mazgaon`, `Hendre Buldg No.17`, `Mumbai`, `Outer Ring Road`, `Salarpuria Touchstone`; `entity.person`: `Deepak Vasant Surve` | `entity.email_address`: `help@ uidai.gov.`, `help@uidai.gov.` |
| `external_octopii/dummy-debit-card.jpg` | `entity.location`: `5422`, `BE` | `entity.date`: `VALD`; `entity.person`: `BIJAY BEHERA`; `entity.postal_code`: `5422` (wrong — this is card-face digits, not a postal code) |
| `external_octopii/dummy-drivers-license-maharashtra.jpg` | `entity.location`: `BAIGANWADI`, `GOVANDI`; `entity.person`: `KHAN KHAN` | — (no `address` relabel this time — `address` didn't win the competition for these values in arm D) |
| `external_octopii/dummy-hong-kong-resident-id.png` | `entity.date`: `26-11-18`; `entity.location`: `HONG KONG` | `entity.postal_code`: `Z683365` (wrong — this is the HKID document number) |
| `external_octopii/dummy-ssn.jpg` | `entity.location`: `USA` | — |
| `format/deck_summary.pptx` | `entity.location`: `T2X 1V4` | `entity.postal_code`: `T2X 1V4` (**correct this time** — unlike arm C, which put the same value under `address`) |
| `format/docx_full.docx` | `entity.person`: `Employee` | `entity.email_address`: `test@example.com` |
| `format/email_notice.eml` | `entity.date`: `Thu, 04 Jun 2026`; `entity.location`: `T2X 1V4`; `entity.organization`: `records@example.org` | `entity.date`: `04 Jun 2026`, `Thu`; `entity.email_address`: `records@example.org`; `entity.postal_code`: `T2X 1V4` |
| `format/pdf_no_pii.pdf` | `entity.location`: `project board` | — |
| `format/pdf_scanned_2page.pdf` | `entity.location`: `BC`, `Ontario`; `entity.person`: `AB123456` | — |
| `format/xlsx_multisheet.xlsx` | — | `entity.email_address`: `test@example.com`; `entity.phone_number`: `Phone` (wrong — the field-label word, not a number); `entity.postal_code`: `T2X` (truncated) |
| `stress/edge_cases/very_long_single_line.txt` | `entity.date`: `sprint`; `entity.location`: `payment region`, `revenue region`; `entity.organization`: `logistics vendor` | `entity.organization`: `vendor inventory platform` |
| `stress/.../salted_email_0168.txt` | — | `entity.email_address`: `noah.kaplan@acme-industries.net`; `entity.person`: `customer` |
| 13 more `stress` filler/salted files | assorted single `entity.person`/`entity.organization` removals (`noah.kaplan`, `worker`, `vendor`) | assorted single `entity.person`/`entity.organization` additions (`customer`, `worker`, `Worker`, `Sprint`, `logistics`, `vendor`, `Headcount`, `Customer service`) — noise churn among filler-file template words, unrelated to `address` |
| `test/specimen_sin_01.jpg` | `entity.location`: `PO Box 000` | `entity.address`: `PO Box 000` (relabel) |
| `test/specimen_licence_02.jpg` | `entity.location`: `Sampletown AB`; `entity.person`: `EXAMPLE` | `entity.date`: `10 FEB 2024`; `entity.person`: `EXAMPLE, Jordan` |
| `test/specimen_licence_01.jpg` | `entity.location`: `Sampletown AB`, `Samplewood Pk`; `entity.person`: `EXAMPLE` | `entity.date`: `10 FEB 2024`; `entity.person`: `EXAMPLE, Jordan` |
| `test/specimen_pr_card_01.jpg` | `entity.location`: `Place of Landing`, `Taile`; `entity.person`: `EXAMPLE`, `Sample` | — |
| `test/specimen_benefits_02.jpg` | `entity.date`: `06-Feb-2030`, `6-Feb-30`, `Thu Jan 8, 2031`; `entity.location`: **`16 Samplewood Pk SW`**; `entity.person`: `Jordan Example` | `entity.person`: `Mr. Jordan Example` |
| `test/specimen_licence_03.jpg` | `entity.location`: `Alberta` | `entity.date`: `2030`, `FEB 2030` |
| `test/specimen_pr_card_02.jpg` | `entity.location`: `Nam`; `entity.person`: `Example`, `Sample` | — |
| `test/specimen_passport_01.jpg` | `entity.location`: `PASAS`, `Place d bith`, `Rodas` | — |
| `test/PassportrandompagenoPIInoface.jpg` | `entity.location`: `OESSUS` | `entity.date`: `2022` |
| `test/specimen_benefits_01.jpg` | `entity.location`: `DENTAL OFFICE` | — |
| `test/specimen_benefits_03.jpg` | `entity.location`: `200 Example Ave SW`; `entity.person`: `Dr. Example` | `entity.phone_number`: `000` (wrong — the same garbage span arm C had called `email_address`) |

The `'000'` and `'T2X 1V4'` cases are the cleanest illustration of the
mechanism at work: the same underlying OCR span gets a **different wrong
label** in C vs. D (`'000'`: `email_address` in C, `phone_number` in D — a
misfire that just changes shape), while `'T2X 1V4'` genuinely **improves**
from a wrong `address` label in C to a correct `postal_code` label once
`phone number` joins the mix in D. Adding siblings measurably reduces some
specific confusions while doing nothing (`Phone`, `000`) or actively adding
new ones (`5422`→`postal_code`, `Z683365`→`postal_code`) elsewhere.

## 2. Email-confusion rate — inconclusive on anchor-only data

| Arm | `entity.address` findings on anchors | email/domain/URL-shaped |
|---|--:|--:|
| B | 6 | 0 (0.0%) |
| C | 3 | 0 (0.0%) |
| D | 1 | 0 (0.0%) |

**This does not confirm or refute the owner's mechanism hypothesis** — it
can't, on this data. Arm B's own anchor-only sample was already at 0%
email-confusion, so there is nothing for C/D to "drop from." The 52.7%
figure the task cites is from `gliner_address_label.md`'s **full 1381-file
corpus** run (already-existing data, not rerun here), where Enron's ~1000
real business emails dominate the volume of email-shaped strings competing
for the `address` label. None of the four anchors (`stress` = synthetic
seeded filler, `format` = 18 synthetic format fixtures, `external_octopii`
= 8 foreign-ID images, `test` = 11 real photographed Canadian ID documents)
contain a comparable density of real email addresses in running text, so
this specific mechanism essentially never had a chance to fire in this
corpus at any label-set size. The four-anchor scope the task specified
(explicitly to avoid an hours-long full-corpus rerun) is the right
instrument for §1's displacement question, which it answers decisively, but
is the wrong instrument for this specific sub-question.

**What the anchors *do* show, qualitatively (not a rate, a small set of
concrete cases):** in arm C, a genuine Aadhaar support address
`help@uidai.gov.in` (OCR-truncated to `help@uidai.gov.`/`help@ uidai.gov.`
by the source image, present in both C and D) was correctly captured by the
new `email address` label rather than falling into `address` the way
`gliner_address_label.md`'s full-corpus run showed email addresses doing —
directionally consistent with the hypothesis, just not a rate measurable at
this n.

## 3. Genuine address precision — sample too small for the ≥25 target

| Arm | `entity.address` findings | Real address | Office/room code | Field label | Other |
|---|--:|--:|--:|--:|--:|
| B | 6 | 5 (83.3%) | 1 (16.7%) | 0 | 0 |
| C | 3 | 2 (66.7%) | 0 | 0 | 1 (33.3% — postal code, see below) |
| D | 1 | 1 (100%) | 0 | 0 | 0 |

**None of these reach the pre-registered 25-finding minimum, and this is a
direct, structural consequence of the anchor-only scope, not a shortcut
taken here.** The four anchors together produced only 6/3/1 raw `address`
findings for B/C/D respectively — there is no way to manually classify 25
of something the arm only found single digits of without violating the
task's explicit "do not re-run the full corpus" instruction. Classified in
full below; every available finding is accounted for, not a sub-sample:

- **Arm B (6/6 classified):** `P.O. Box No. 1947` (real, PO box),
  `BABUKHAN`/`BAIGANWADI`/`GOVANDI` (real — Maharashtra locality names,
  printed address-block components on the driver's-license specimen),
  `PO Box 000` (synthetic, PO box) — genuinely 5/6 real. `Room no.3` (office
  code) is the one non-address hit. **Zero field-label or junk hits at
  this n** — a much cleaner precision picture than the full-corpus 6-11%,
  but this is 6 findings on a corpus that doesn't resemble Enron's volume
  or noise profile; it is not a like-for-like comparison and should not be
  read as "arm B's real precision is 83%."
- **Arm C (3/3 classified):** `P.O. Box No. 1947`, `PO Box 000` (synthetic) —
  same two PO boxes arm B found, plus `T2X 1V4`, a **Canadian postal code**
  labeled `address` despite `postal code` being an available sibling in
  this exact arm (see §1's `deck_summary.pptx` row) — the disambiguation
  the hypothesis predicts didn't fire here even though the competing label
  existed.
- **Arm D (1/1 classified):** `PO Box 000` (synthetic). n=1, not
  interpretable as a rate in either direction.

**The only statistically meaningful genuine-address-precision figure
available anywhere in this evidence set remains
`gliner_address_label.md`'s full-corpus arm-B number: roughly 6-11% across
921 raw findings** (n=45 manually classified sample). This measurement did not
re-derive that number and could not extend it to C/D within its anchor-only
scope.

## 4. `'16 Samplewood Pk SW'` (`test/specimen_benefits_02.jpg`)

| Arm | Found? | Under what label |
|---|---|---|
| A (baseline) | Yes | `entity.location` |
| B (+address) | **No** | — (removed, no replacement under any label) |
| C (+address, email address) | **No** | — (removed, no replacement under any label) |
| D (+address, email address, postal code, phone number) | **No** | — (removed, no replacement under any label) |

This is unchanged across every arm tested. Adding `address`'s
disambiguating siblings — including the full 4-label PII-model set — does
**not** recover this specific real street-address string in the same file
where a full sibling label set exists to correctly route it. It simply
stops being classified as anything, in all four arms after A.

## 5. Sibling labels' own behavior (supplementary, not a report requirement)

Not asked for directly, but visible in every diff above and worth recording
since it explains *why* the net-loss numbers don't improve faster than they
do. Full findings under the new sibling categories, arms C/D:

| Category | Arm | Findings | Genuinely correct | Wrong/garbage |
|---|---|--:|--:|--:|
| `entity.email_address` | C | 8 | 4 exact + 2 truncated-but-real | 2 (`noah.kaplan` missing domain, `'000'`) |
| `entity.email_address` | D | 6 | 4 exact + 2 truncated-but-real | 0 |
| `entity.postal_code` | D | 5 | 2 exact + 1 truncated | 2 (`5422` — card digits; `Z683365` — HKID document number) |
| `entity.phone_number` | D | 2 | 0 | 2 (`'Phone'` — the field-label word itself; `'000'` — garbage) |

`email address` is the best-behaved of the three new siblings on this
corpus (6/8 and 6/6 real-or-partial). `phone number` is the worst — 0/2,
both wrong — which is expected given `gliner_detector._is_structured()`'s
existing digit-heavy filter already strips genuinely phone-shaped strings
regardless of which label GLiNER assigns them (verified in
`gliner_address_label.md` §3), so whatever survives to be labeled
`phone number` is, by construction, exactly the residual text that *isn't*
phone-shaped — the label can only misfire, never correctly confirm a real
phone number, on this corpus. `postal code` sits in between (2/5 exact,
1/5 partial, 2/5 wrong).

## Acceptance criterion — applied

> An arm is adoptable only if it adds net findings across the four anchors
> without removing existing correct classifications, AND genuine address
> precision is materially better than arm B's 6-11%. An arm that fixes the
> email confusion but still loses anchor findings is REJECTED — the
> displacement problem is the blocking one, not the email confusion.

| Arm | Net anchor findings | Passes "no net loss"? | Verdict |
|---|--:|---|---|
| B | -26 | No | **REJECTED** |
| C | -23 | No | **REJECTED** |
| D | -7 | No | **REJECTED** |

All three fail the first, binary clause of the criterion outright — net
anchor findings are negative in every arm — so the precision clause is
moot for all three by the criterion's own stated logic ("An arm that fixes
the email confusion but still loses anchor findings is REJECTED"). D comes
closest to breaking even (net -7 vs. B's -26) but does not cross zero, and
its own new sibling labels (`postal code`, `phone number`) introduce
independent misfires (`5422`, `Z683365`, `'Phone'`) that partially offset
whatever disambiguation gain `address` itself received.

## Recommendation

Do not adopt `address` in production `LABELS`, with or without
disambiguating siblings. The owner's hypothesis about the *mechanism*
behind email/domain confusion is plausible and partially supported by the
qualitative anchor evidence (§2-3, §5), but the anchors — the instrument
the task specified to avoid an hours-long full-corpus rerun — cannot
actually test that mechanism at a meaningful sample size, because they
don't reproduce the email-heavy volume that produced the 52.7% figure in
the first place. What the anchors *can* test, decisively and at full
statistical weight (211 files, 11-58 findings changed per arm), is the
displacement question, and every arm fails it. Per the pre-registered
criterion, that is sufficient on its own to reject all three arms without
needing a better-powered precision measurement.

## Files

- `docs/evidence/gliner_address_anchor_scan.py` — extended from the prior
  task's single `--address` flag to `--arm {A,B,C,D}` / `ARM_LABELS`.
- `/tmp/gliner_siblings_eval/anchor_{A,B,C,D}.json` — raw anchor scan output
  per arm (not committed, `/tmp` scratch; regenerate via
  `gliner_address_anchor_scan.py --arm <X>`, ~13 min per arm).
- `/tmp/gliner_siblings_eval/diff_arm.py`, `extract_address.py` — scratch
  analysis scripts used to produce this document's tables (not committed).

Measurement only. No production files (`detectors/gliner_detector.py`,
`detectors/hybrid_detector.py`, `config.py`) were modified. LLM verification
remained disabled throughout the measurement.
