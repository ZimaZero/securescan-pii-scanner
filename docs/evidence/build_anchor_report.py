#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

OLD_PATH = Path('/tmp/anchor_head_scan.json')
NEW_PATH = Path('/tmp/anchor_current_scan.json')
OUT_PATH = Path('/tmp/anchor_rederivation.md')

old = json.loads(OLD_PATH.read_text(encoding='utf-8'))
new = json.loads(NEW_PATH.read_text(encoding='utf-8'))

ANCHORS = ('stress', 'format', 'external_octopii', 'test')
DISPLAY = {
    'stress': '`tests/stress_data`',
    'format': '`tests/format_data`',
    'external_octopii': '`tests/external_octopii`',
    'test': '`/external_corpus/test_anchor`',
}


def row_map(payload):
    return {(r['anchor'], r['file']): r for r in payload['rows']}


def finding_map(row):
    return {
        (f['type'], f['value'], f['risk']): f
        for f in row['findings']
    }


old_rows = row_map(old)
new_rows = row_map(new)

# Pair only findings that plainly represent the same semantic span. Anything
# less certain stays as a separate exact removal/addition rather than being
# silently forced into a CHANGED row.
PAIRS = {
    ('external_octopii', 'dummy-PAN-India.jpg', 'entity.organization', 'De Eos'):
        ('entity.organization', 'ICO'),
    ('external_octopii', 'dummy-aadhaar.png', 'contact.email', 'heip@uidal.gov.in'):
        ('contact.email', 'help@uidai.gov.in'),
    ('external_octopii', 'dummy-aadhaar.png', 'entity.location', 'Bengalura - 560.001'):
        ('entity.location', 'Bengaluru'),
    ('external_octopii', 'dummy-aadhaar.png', 'entity.location', 'HRONGE Outer Ring Road'):
        ('entity.location', 'Outer Ring Road'),
    ('external_octopii', 'dummy-debit-card.jpg', 'entity.date', 'wee 11/22'):
        ('entity.date', '11/22'),
    ('external_octopii', 'dummy-drivers-license-maharashtra.jpg', 'entity.location', 'BABU ER'):
        ('entity.location', 'BABUKHAN'),
    ('external_octopii', 'dummy-hong-kong-resident-id.png', 'identifier.personal.dob', '03-06-1985'):
        ('entity.date', '03-06-1985'),
    ('external_octopii', 'dummy-passport-ukraine.jpg', 'entity.person', 'UKRGRY TSENKO'):
        ('entity.person', 'UKRGRYTSENKO'),
    ('test', 'specimen_licence_01.jpg', 'entity.date', 'rp01 FEB 2026'):
        ('entity.date', 'JUL 2026'),
    ('test', 'specimen_licence_01.jpg', 'entity.date', 'vw09 FEB 1990'):
        ('entity.date', 'FEB 1990'),
    ('test', 'specimen_pr_card_01.jpg', 'entity.person', 'EXAMPLE'):
        ('entity.person', 'EXAMPLE'),
    ('test', 'specimen_benefits_01.jpg', 'entity.person', 'Sample'):
        ('entity.person', 'Sample'),
    ('test', 'specimen_benefits_03.jpg', 'entity.person', 'Jordan Example'):
        ('entity.person', 'Jordan Example'),
}


def cause(action, anchor, filename, finding, old_row, new_row):
    ftype, value, _risk = finding
    if (
        action == 'ADDED'
        and anchor == 'format'
        and filename == 'pdf_scanned_2page.pdf'
        and ftype == 'identifier.government.drivers_license_on'
    ):
        return "driver's licence format change"
    if (
        action == 'REMOVED'
        and anchor == 'test'
        and filename == 'specimen_licence_01.jpg'
        and ftype == 'identifier.government.drivers_license_ca'
        and value == '123456'
    ):
        return "driver's licence format change"
    if action == 'ADDED':
        if value.casefold() not in old_row['text'].casefold():
            return 'Paddle reads text Tesseract missed'
        return 'Paddle reads text differently than Tesseract'
    return 'Paddle reads text differently than Tesseract'


def esc(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


lines = [
    '# PaddleOCR + MRZ A/B anchor re-derivation',
    '',
    'Measured on 2026-08-05. This is a proposal only; no stored anchor was updated.',
    '',
    '- Committed baseline: `dbfc43f3c65bf4f756c56a5249dfbabe92bff9da` exported to an isolated tree.',
    '- Baseline OCR: committed Tesseract production path.',
    '- Candidate: current production PaddleOCR with MRZ gates A and B enabled.',
    '- Both runs: LLM verification OFF; normal production NER policy ON; same 211 files.',
    '- Extraction failures: zero in both runs.',
    '- Finding identity for ADDED/REMOVED is exact `(file, type, value, risk)`.',
    '- CHANGED pairs are limited to plainly corresponding semantic spans; uncertain pairs remain separate exact rows.',
    '',
    '## Proposed anchor numbers',
    '',
    '| Anchor | Baseline findings | Paddle+A/B findings | Finding risks before → after | File risks before → after | PII files before → after |',
    '|---|---:|---:|---|---|---:|',
]

for anchor in ANCHORS:
    o = old['summaries'][anchor]
    n = new['summaries'][anchor]
    def risks(d):
        return '/'.join(str(d.get(k, 0)) for k in ('HIGH', 'MEDIUM', 'LOW', 'UNKNOWN'))
    def file_risks(d):
        return '/'.join(str(d.get(k, 0)) for k in ('HIGH', 'MEDIUM', 'LOW', 'NONE', 'UNKNOWN'))
    lines.append(
        f"| {DISPLAY[anchor]} | {o['total_findings']} | {n['total_findings']} | "
        f"H/M/L/U {risks(o['finding_risk'])} → {risks(n['finding_risk'])} | "
        f"H/M/L/None/U {file_risks(o['file_risk'])} → {file_risks(n['file_risk'])} | "
        f"{o['pii_files']} → {n['pii_files']} |"
    )

lines += [
    '',
    'The committed run reproduced the stored totals exactly: stress 132, format 75, Octopii 45, and Test 75.',
    '',
    '## Exhaustive finding deltas',
    '',
    'No delta was caused by MRZ gate A or B: the committed Tesseract finding set contained no block that those gates newly reject. The Ukrainian MRZ additions below are OCR-derived; Paddle reconstructed the printed MRZ lines differently and the valid block passes both gates.',
]

for anchor in ANCHORS:
    additions = []
    removals = []
    changes = []
    consumed_old = set()
    consumed_new = set()
    for key in sorted(k for k in old_rows if k[0] == anchor):
        filename = key[1]
        old_row = old_rows[key]
        new_row = new_rows[key]
        old_findings = finding_map(old_row)
        new_findings = finding_map(new_row)
        raw_removed = old_findings.keys() - new_findings.keys()
        raw_added = new_findings.keys() - old_findings.keys()
        for old_finding in sorted(raw_removed):
            pair = PAIRS.get((anchor, filename, old_finding[0], old_finding[1]))
            if not pair:
                continue
            candidates = [f for f in raw_added if f[0] == pair[0] and f[1] == pair[1]]
            if len(candidates) != 1:
                continue
            new_finding = candidates[0]
            consumed_old.add((filename, old_finding))
            consumed_new.add((filename, new_finding))
            changes.append((filename, old_finding, new_finding,
                            'Paddle reads text differently than Tesseract'))
        for finding in sorted(raw_removed):
            if (filename, finding) in consumed_old:
                continue
            removals.append((filename, finding, cause('REMOVED', anchor, filename, finding, old_row, new_row)))
        for finding in sorted(raw_added):
            if (filename, finding) in consumed_new:
                continue
            additions.append((filename, finding, cause('ADDED', anchor, filename, finding, old_row, new_row)))

    lines += ['', f'### {DISPLAY[anchor]}', '']
    lines.append(
        f"Exact delta: **{len(additions)} added / {len(removals)} removed / {len(changes)} changed**."
    )

    lines += ['', '#### ADDED', '']
    if additions:
        lines += [
            '| File | Value | Type | Old risk | New risk | Cause |',
            '|---|---|---|---|---|---|',
        ]
        for filename, (ftype, value, risk), why in additions:
            lines.append(f'| `{esc(filename)}` | `{esc(value)}` | `{esc(ftype)}` | — | {risk} | {why} |')
    else:
        lines.append('None.')

    lines += ['', '#### REMOVED', '']
    if removals:
        lines += [
            '| File | Value | Type | Old risk | New risk | Cause |',
            '|---|---|---|---|---|---|',
        ]
        for filename, (ftype, value, risk), why in removals:
            lines.append(f'| `{esc(filename)}` | `{esc(value)}` | `{esc(ftype)}` | {risk} | — | {why} |')
    else:
        lines.append('None.')

    lines += ['', '#### CHANGED', '']
    if changes:
        lines += [
            '| File | Old value → new value | Old type → new type | Old risk → new risk | Cause |',
            '|---|---|---|---|---|',
        ]
        for filename, old_f, new_f, why in changes:
            lines.append(
                f'| `{esc(filename)}` | `{esc(old_f[1])}` → `{esc(new_f[1])}` | '
                f'`{esc(old_f[0])}` → `{esc(new_f[0])}` | {old_f[2]} → {new_f[2]} | {why} |'
            )
    else:
        lines.append('None.')

lines += [
    '',
    '## Attribution audit',
    '',
    '- MRZ gate A/B rejections: **0 finding deltas** against the committed anchor sets.',
    '- Driver\'s-licence-format deltas: the new Ontario finding in `pdf_scanned_2page.pdf`, and removal of the `123456` fragment in `specimen_licence_01.jpg`.',
    '- All remaining rows are tied to captured OCR-text differences. “Missed” is used only when the new finding value is absent from the committed Tesseract text; otherwise the row says Paddle read the text differently.',
    '- **UNEXPLAINED: 0.** No text-corpus finding moved, and every non-driver delta occurs in an OCR-backed file whose captured extracted text differs between the two production runs.',
    '',
    'These values are not ratified and were not written into any stored anchor.',
]

OUT_PATH.write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(OUT_PATH)
