from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

PROJECT = Path("/workspaces/securescan")
sys.path.insert(0, str(PROJECT))
from detectors.keyword_detector import detect_pii_keywords

EXCLUDED = {
    "ab_licence_display_01.txt",
    "mb_licence_display_01.txt",
    "yk_licence_corroborating_02.txt",
}

inputs = []
for line in Path("/tmp/mrz_corpus_capture.jsonl").read_text(encoding="utf-8").splitlines():
    row = json.loads(line)
    inputs.append((row["corpus"], row["path"], row.get("text", "")))
for path in sorted((PROJECT / "tests/canadian_eval_data").iterdir()):
    if path.is_file() and path.name not in EXCLUDED:
        inputs.append(("canadian_88", str(path), path.read_text(encoding="utf-8")))

changed = []
future_candidates = [
    item for item in inputs
    if re.search(r"(?<!\d)(?:202[7-9]|20[3-9]\d)[-/]", item[2])
]
for corpus, path, text in future_candidates:
    bounded = {
        value for value, _confidence in detect_pii_keywords(
            text, as_of_date=date(2026, 8, 6)
        ).get("dob_context", [])
    }
    no_future_gate = {
        value for value, _confidence in detect_pii_keywords(
            text, as_of_date=date(9999, 12, 31)
        ).get("dob_context", [])
    }
    for value in sorted(no_future_gate - bounded):
        changed.append((corpus, path, value))

lines = [
    "INPUTS " + repr(dict(Counter(corpus for corpus, _path, _text in inputs))),
    "FILES_WITH_POST_2026_DATE_TEXT " + str(len(future_candidates)),
    f"FUTURE_SIGNAL_CONTRIBUTION {len(changed)}",
    *(repr(item) for item in changed),
]
Path("/tmp/future_signal_result.txt").write_text("\n".join(lines), encoding="utf-8")
print(*lines, sep="\n")
