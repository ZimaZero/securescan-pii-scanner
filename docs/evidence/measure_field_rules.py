from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


PROJECT = Path("/workspaces/securescan")
CAPTURE = Path("/tmp/mrz_corpus_capture.jsonl")
EXCLUDED_CANADIAN = {
    "ab_licence_display_01.txt",
    "mb_licence_display_01.txt",
    "yk_licence_corroborating_02.txt",
}
TARGET_TYPES = {
    "identifier.personal.dob",
    "identifier.government.drivers_license_yt",
}


def finding_rows(matches):
    rows = []
    for category, items in matches.items():
        if category not in TARGET_TYPES or not isinstance(items, list):
            continue
        for finding in items:
            rows.append(
                {
                    "type": category,
                    "value": str(finding.get("value", "")),
                    "risk": str(finding.get("risk_level", "")),
                    "source": str(finding.get("source", "")),
                }
            )
    return sorted(rows, key=lambda row: (row["type"], row["value"], row["source"]))


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: measure_field_rules.py OUTPUT.json")
    sys.path.insert(0, str(PROJECT))
    from detectors.hybrid_detector import detect_pii_hybrid

    inputs = []
    for line in CAPTURE.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        inputs.append((row["corpus"], row["path"], row.get("text", "")))

    canadian_root = PROJECT / "tests/canadian_eval_data"
    for path in sorted(canadian_root.iterdir()):
        if path.is_file() and path.name not in EXCLUDED_CANADIAN:
            inputs.append(("canadian_88", str(path), path.read_text(encoding="utf-8")))

    rows = []
    for index, (corpus, path, text) in enumerate(inputs, 1):
        matches = detect_pii_hybrid(text, run_ner=False, verify=False)
        findings = finding_rows(matches)
        if findings:
            rows.append({"corpus": corpus, "path": path, "findings": findings})
        if index % 500 == 0:
            print(f"{index}/{len(inputs)}", flush=True)

    counts = Counter()
    for row in rows:
        for finding in row["findings"]:
            counts[(row["corpus"], finding["type"])] += 1
    payload = {
        "input_counts": dict(Counter(corpus for corpus, _path, _text in inputs)),
        "finding_counts": {
            f"{corpus}|{category}": count
            for (corpus, category), count in sorted(counts.items())
        },
        "rows": rows,
    }
    Path(sys.argv[1]).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload["input_counts"], sort_keys=True))
    print(json.dumps(payload["finding_counts"], sort_keys=True))


if __name__ == "__main__":
    main()
