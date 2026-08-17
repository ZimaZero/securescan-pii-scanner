#!/usr/bin/env python3
import json
import sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from detectors import mrz_detector as mrz

FLAGS=(
 'GATE_A_REQUIRE_CHEVRON','GATE_B_VALID_ISSUING_STATE',
 'GATE_C_VALID_DOC_TYPE','GATE_D_CORROBORATED_UNVERIFIED',
)

ARMS={
 'baseline':(),
 'A':('GATE_A_REQUIRE_CHEVRON',),
 'B':('GATE_B_VALID_ISSUING_STATE',),
 'C':('GATE_C_VALID_DOC_TYPE',),
 'D':('GATE_D_CORROBORATED_UNVERIFIED',),
 'A+B+D':('GATE_A_REQUIRE_CHEVRON','GATE_B_VALID_ISSUING_STATE','GATE_D_CORROBORATED_UNVERIFIED'),
}

TP={
 ('external_octopii','dummy-passport-ukraine.jpg','mrz_unverified','EK000001'),
 ('external_octopii','dummy-passport-ukraine.jpg','mrz_dob','830725'),
 ('external_octopii','dummy-passport-ukraine.jpg','mrz_expiry','190803'),
 ('specimen','Canada_passport-data-page-large_2023.jpeg','mrz_document_number','P123456AA'),
 ('specimen','Canada_passport-data-page-large_2023.jpeg','mrz_dob','900801'),
 ('specimen','Canada_passport-data-page-large_2023.jpeg','mrz_expiry','330114'),
 ('test','specimen_pr_card_01.jpg','mrz_document_number','PD0001234'),
 ('test','specimen_pr_card_01.jpg','mrz_dob','900201'),
 ('test','specimen_pr_card_01.jpg','mrz_expiry','300201'),
 ('test','specimen_passport_01.jpg','mrz_document_number','12345678'),
 ('test','specimen_passport_01.jpg','mrz_dob','900201'),
 ('test','specimen_passport_01.jpg','mrz_expiry','300401'),
}

def key(corpus,path,kind,value):
 return corpus,Path(path).name,kind,value

def run(enabled):
 for flag in FLAGS:setattr(mrz,flag,flag in enabled)
 out={}
 for line in Path('/tmp/mrz_corpus_capture.jsonl').read_text(encoding='utf-8').splitlines():
  row=json.loads(line)
  for kind,items in mrz.detect_mrz(row['text']).items():
   for value,conf,meta in items:
    k=(row['corpus'],row['path'],kind,value)
    out[k]={'corpus':row['corpus'],'path':row['path'],'kind':kind,'value':value,
            'confidence':conf,'format':meta.get('format'),'issuing_state':meta.get('issuing_state'),
            'doc_type':meta.get('doc_type'),'truth':'TP' if key(*k) in TP else 'FP'}
 return out

results={name:run(flags) for name,flags in ARMS.items()}
base=results['baseline']
report={'arms':{},'baseline_count':len(base),'baseline_tp':sum(v['truth']=='TP' for v in base.values())}
for name,current in results.items():
 removed=[base[k] for k in sorted(base.keys()-current.keys())]
 added=[current[k] for k in sorted(current.keys()-base.keys())]
 report['arms'][name]={
  'retained':len(current),'removed_count':len(removed),'true_positives_lost':sum(x['truth']=='TP' for x in removed),
  'removed':removed,'added':added,
  'by_corpus_retained':dict(Counter(x['corpus'] for x in current.values())),
  'by_corpus_removed':dict(Counter(x['corpus'] for x in removed)),
 }
Path('/tmp/mrz_gate_measurement.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({name:{k:v for k,v in data.items() if k in ('retained','removed_count','true_positives_lost','by_corpus_removed')} for name,data in report['arms'].items()},indent=2))
for name,data in report['arms'].items():
 if name=='baseline':continue
 print('\n',name)
 for r in data['removed']: print(r['truth'],r['corpus'],Path(r['path']).name,r['kind'],r['value'],r['format'],r['issuing_state'],r['doc_type'])
