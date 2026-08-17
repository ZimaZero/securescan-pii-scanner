#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from discovery import SUPPORTED_EXTENSIONS, scan_file
from detectors import mrz_detector as mrz
from tests.run_specimen_eval import _resolve_corpus_file

SPECIMEN = Path('/external_corpus/specimen_corpus')


def files_under(path: Path):
    return sorted(
        p for p in path.rglob('*')
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def corpus_files():
    corpora = {
        'stress': files_under(ROOT / 'tests/stress_data'),
        'format': files_under(ROOT / 'tests/format_data'),
        'test': files_under(Path('/external_corpus/test_anchor')),
        'external_octopii': files_under(ROOT / 'tests/external_octopii'),
        'enron': files_under(ROOT / 'tests/external_enron/sample'),
    }
    with (ROOT / 'tests/specimen_eval_docs/GROUND_TRUTH.csv').open(
        encoding='utf-8-sig', newline=''
    ) as handle:
        corpora['specimen'] = [
            _resolve_corpus_file(SPECIMEN, row['file']) for row in csv.DictReader(handle)
        ]
    return corpora


def candidate_with_source(raw_lines, prepared, idx, target):
    if idx >= len(prepared):
        return None
    single = prepared[idx]
    if mrz._is_candidate(single, target):
        return ((idx, idx), single, raw_lines[idx])
    if (
        single and mrz._MRZ_CHARS_RE.match(single)
        and len(single) < target - mrz.LEN_TOLERANCE
        and idx + 1 < len(prepared)
    ):
        joined = single + prepared[idx + 1]
        if mrz._is_candidate(joined, target):
            return ((idx, idx + 1), joined, raw_lines[idx] + ' ⏎ ' + raw_lines[idx + 1])
    return None


def pair_blocks(raw_lines, prepared, target):
    blocks=[]; i=0
    while i < len(prepared):
        first=candidate_with_source(raw_lines,prepared,i,target)
        if first:
            second=candidate_with_source(raw_lines,prepared,first[0][1]+1,target)
            if second:
                blocks.append((first,second)); i=second[0][1]+1; continue
        i += 1
    return blocks


def triple_blocks(raw_lines, prepared, target):
    blocks=[]; i=0; n=len(prepared)
    while i<n:
        anchor=candidate_with_source(raw_lines,prepared,i,target)
        if not anchor:
            i += 1; continue
        roles=[anchor]; matched=1; cursor=anchor[0][1]+1; last=anchor[0][1]
        for _ in range(2):
            cand=candidate_with_source(raw_lines,prepared,cursor,target) if cursor<n else None
            if cand:
                roles.append(cand); matched += 1; last=cand[0][1]; cursor=cand[0][1]+1
            else:
                roles.append(None); last=max(last,cursor); cursor += 1
        if matched>=2:
            blocks.append(tuple(roles)); i=last+1
        else: i += 1
    return blocks


def block_inventory(text):
    raw=text.splitlines(); prepared=[mrz._prep_line(x) for x in raw]; rows=[]
    for fmt,target,parser in [('TD3',mrz.TD3_LINE_LEN,mrz._parse_td3),('TD2',mrz.TD2_LINE_LEN,mrz._parse_td2)]:
        for first,second in pair_blocks(raw,prepared,target):
            parsed=parser(first[1],second[1]); doc=parsed.get('doc_number')
            if doc and doc.get('value'):
                kind='mrz_document_number' if doc.get('valid') is True else ('mrz_unverified' if doc.get('valid') is False else None)
                if kind:
                    rows.append({'kind':kind,'value':doc['value'],'format':fmt,
                                 'issuing_state':parsed.get('issuing_state'),'doc_type':parsed.get('doc_type'),
                                 'source_line':second[2],'block_lines':[first[2],second[2]],
                                 'dob_valid':(parsed.get('dob') or {}).get('valid') is True,
                                 'expiry_valid':(parsed.get('expiry') or {}).get('valid') is True})
            for field,kind in [('dob','mrz_dob'),('expiry','mrz_expiry')]:
                item=parsed.get(field)
                if item and item.get('valid') is True and item.get('value'):
                    rows.append({'kind':kind,'value':item['value'],'format':fmt,
                                 'issuing_state':parsed.get('issuing_state'),'doc_type':parsed.get('doc_type'),
                                 'source_line':second[2],'block_lines':[first[2],second[2]],
                                 'dob_valid':(parsed.get('dob') or {}).get('valid') is True,
                                 'expiry_valid':(parsed.get('expiry') or {}).get('valid') is True})
    for roles in triple_blocks(raw,prepared,mrz.TD1_LINE_LEN):
        parsed=mrz._parse_td1(*(x[1] if x else None for x in roles)); doc=parsed.get('doc_number')
        source=roles[0][2] if roles[0] else ''
        block=[x[2] for x in roles if x]
        if doc and doc.get('value'):
            kind='mrz_document_number' if doc.get('valid') is True else ('mrz_unverified' if doc.get('valid') is False else None)
            if kind:
                rows.append({'kind':kind,'value':doc['value'],'format':'TD1',
                             'issuing_state':parsed.get('issuing_state'),'doc_type':parsed.get('doc_type'),
                             'source_line':source,'block_lines':block,
                             'dob_valid':(parsed.get('dob') or {}).get('valid') is True,
                             'expiry_valid':(parsed.get('expiry') or {}).get('valid') is True})
        line2=roles[1][2] if roles[1] else source
        for field,kind in [('dob','mrz_dob'),('expiry','mrz_expiry')]:
            item=parsed.get(field)
            if item and item.get('valid') is True and item.get('value'):
                rows.append({'kind':kind,'value':item['value'],'format':'TD1',
                             'issuing_state':parsed.get('issuing_state'),'doc_type':parsed.get('doc_type'),
                             'source_line':line2,'block_lines':block,
                             'dob_valid':(parsed.get('dob') or {}).get('valid') is True,
                             'expiry_valid':(parsed.get('expiry') or {}).get('valid') is True})
    # Match detector's per-type/value deduplication.
    dedup={}
    for row in rows: dedup.setdefault((row['kind'],row['value']),row)
    return list(dedup.values())


def scan_one(corpus,path):
    result=scan_file(str(path),verify=False,run_ner=False,return_text=True)
    text=result.get('_text','')
    return {'corpus':corpus,'path':str(path),'scan_status':result.get('scan_status'),
            'failure_reason':result.get('failure_reason'),'text':text,
            'mrz':block_inventory(text)}


def main():
    items=[(c,p) for c,paths in corpus_files().items() for p in paths]
    print(json.dumps({'counts':{c:len(ps) for c,ps in corpus_files().items()},'total':len(items)}),flush=True)
    out=Path('/tmp/mrz_corpus_capture.jsonl')
    prior={}
    if out.exists():
        for line in out.read_text(encoding='utf-8').splitlines():
            row=json.loads(line)
            if row.get('scan_status') == 'scanned':
                prior[(row['corpus'],row['path'])]=row
    completed=list(prior.values())
    items=[(c,p) for c,p in items if (c,str(p)) not in prior]
    print(json.dumps({'reused':len(completed),'to_scan':len(items)}),flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(scan_one,c,p):(c,p) for c,p in items}
        for n,fut in enumerate(as_completed(futures),1):
            completed.append(fut.result())
            if n%50==0: print(f'{n}/{len(items)}',flush=True)
    completed.sort(key=lambda r:(r['corpus'],r['path']))
    out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in completed),encoding='utf-8')
    findings=[dict(corpus=r['corpus'],path=r['path'],**m) for r in completed for m in r['mrz']]
    Path('/tmp/mrz_baseline_inventory.json').write_text(json.dumps(findings,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'files':len(completed),'failed':sum(r['scan_status']!='scanned' for r in completed),'findings':len(findings),'out':str(out)}),flush=True)

if __name__=='__main__': main()
