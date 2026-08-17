#!/usr/bin/env python3
"""Real production discovery.scan_file() over the four established anchors,
across four label-set arms (see ARM_LABELS below).

Mirrors docs/evidence/scan_anchor_findings.py's exact methodology (same four
anchors, same schema, same ThreadPoolExecutor(max_workers=4) pattern) so
output is diffable the same way docs/evidence/anchor_rederivation.md diffed
its own before/after anchor captures. The only variable changed per arm:
detectors.gliner_detector.LABELS gains the arm's extra label(s), and
detectors.hybrid_detector.PII_TAXONOMY gains one "entity.<label>" mapping per
new label (mirroring person/organization/location/date's existing pattern),
so each new label lands in a real taxonomy bucket (LOW, via the existing
"entity" parent-category default) instead of falling through to
"uncategorized.<label>"/UNKNOWN. Nothing is written back to detectors/*.py;
the patch lives only in this process's imported module objects.

Usage:
    docker compose run --rm securescan-cpu python docs/evidence/gliner_address_anchor_scan.py --out /tmp/anchor_A.json --label A --arm A
    docker compose run --rm securescan-cpu python docs/evidence/gliner_address_anchor_scan.py --out /tmp/anchor_B.json --label B --arm B
    docker compose run --rm securescan-cpu python docs/evidence/gliner_address_anchor_scan.py --out /tmp/anchor_C.json --label C --arm C
    docker compose run --rm securescan-cpu python docs/evidence/gliner_address_anchor_scan.py --out /tmp/anchor_D.json --label D --arm D
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# docs/evidence/gliner_address_siblings.md arms. A is the unmodified
# production baseline (person/organization/location/date). Extra labels are
# appended in the order shown; each gets its own "entity.<slug>" taxonomy
# bucket (see main()).
ARM_LABELS = {
    'A': [],
    'B': ['address'],
    'C': ['address', 'email address'],
    'D': ['address', 'email address', 'postal code', 'phone number'],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--arm', choices=sorted(ARM_LABELS), default='A',
                         help='Which docs/evidence/gliner_address_siblings.md arm to run.')
    args = parser.parse_args()

    project = ROOT
    sys.path.insert(0, str(project))
    import detectors.gliner_detector as gd
    import detectors.hybrid_detector as hd

    extra_labels = ARM_LABELS[args.arm]
    if extra_labels:
        gd.LABELS = gd.LABELS + extra_labels
        taxonomy_additions = {
            label: f"entity.{label.replace(' ', '_')}" for label in extra_labels
        }
        hd.PII_TAXONOMY = dict(hd.PII_TAXONOMY, **taxonomy_additions)
        print(f"[*] arm={args.arm} gd.LABELS={gd.LABELS}, "
              f"taxonomy additions={taxonomy_additions}", flush=True)
    else:
        print(f"[*] arm={args.arm} (baseline, gd.LABELS={gd.LABELS} unmodified)", flush=True)

    from discovery import SUPPORTED_EXTENSIONS, scan_file

    anchors = {
        'stress': project / 'tests/stress_data',
        'format': project / 'tests/format_data',
        'external_octopii': project / 'tests/external_octopii',
        'test': Path('/external_corpus/test_anchor'),
    }

    work = []
    for anchor, root in anchors.items():
        files = sorted(
            p for p in root.rglob('*')
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        for path in files:
            work.append((anchor, root, path))
    print(json.dumps({'label': args.label, 'files': len(work),
                      'by_anchor': dict(Counter(a for a, _r, _p in work))}), flush=True)

    def scan(item):
        anchor, root, path = item
        result = scan_file(
            str(path), verify=False, run_ner=None, return_text=True
        )
        findings = []
        for category, items in (result.get('matches') or {}).items():
            if category == '_metadata' or not isinstance(items, list):
                continue
            for finding in items:
                if not isinstance(finding, dict):
                    continue
                findings.append({
                    'type': str(category),
                    'value': str(finding.get('value', '')),
                    'risk': str(finding.get('risk_level', 'UNKNOWN')),
                    'source': str(finding.get('source', 'unknown')),
                    'confidence': finding.get('confidence'),
                })
        score = result.get('score')
        if not isinstance(score, (int, float)):
            file_risk = 'UNKNOWN'
        elif score >= 70:
            file_risk = 'HIGH'
        elif score >= 30:
            file_risk = 'MEDIUM'
        elif score > 0:
            file_risk = 'LOW'
        else:
            file_risk = 'NONE'
        return {
            'anchor': anchor,
            'file': path.relative_to(root).as_posix(),
            'absolute_path': str(path),
            'scan_status': result.get('scan_status'),
            'failure_reason': result.get('failure_reason'),
            'score': score,
            'file_risk': file_risk,
            'findings': sorted(findings, key=lambda x: (x['type'], x['value'], x['risk'], x['source'])),
        }

    started = time.perf_counter()
    rows = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(scan, item): item for item in work}
        for n, future in enumerate(as_completed(futures), 1):
            rows.append(future.result())
            if n % 25 == 0 or n == len(work):
                print(f'{args.label}: {n}/{len(work)}', flush=True)
    rows.sort(key=lambda x: (x['anchor'], x['file']))

    summaries = {}
    for anchor in anchors:
        selected = [r for r in rows if r['anchor'] == anchor]
        all_findings = [f for r in selected for f in r['findings']]
        summaries[anchor] = {
            'files': len(selected),
            'failed': sum(r['scan_status'] != 'scanned' for r in selected),
            'total_findings': len(all_findings),
            'finding_risk': dict(Counter(f['risk'] for f in all_findings)),
            'file_risk': dict(Counter(r['file_risk'] for r in selected)),
            'pii_files': sum(bool(r['findings']) for r in selected),
        }
    payload = {
        'label': args.label,
        'arm': args.arm,
        'extra_labels': extra_labels,
        'project': str(project),
        'wall_seconds': time.perf_counter() - started,
        'summaries': summaries,
        'rows': rows,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'wall_seconds': payload['wall_seconds'], 'summaries': summaries}, indent=2), flush=True)


if __name__ == '__main__':
    main()
