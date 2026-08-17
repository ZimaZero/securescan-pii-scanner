#!/usr/bin/env python3
"""
Downloads an English-only slice of the ai4privacy PII-masking dataset via
Hugging Face's datasets-server /rows API (no `datasets` library dependency,
No full multi-GB parquet download is required; the harness needs only a few
thousand rows).

Dataset: ai4privacy/pii-masking-openpii-1.5m — the current flagship variant
(1.6M samples, 30 languages, 19 PII classes; supersedes the older
pii-masking-300k). Rows are interleaved across languages in the underlying
parquet (not grouped in blocks), so this pages through sequentially and
keeps only rows with language == "en" until TARGET_COUNT is reached.

Keeps only the fields the evaluation actually needs (source_text,
masked_text, privacy_mask, uid, language, region) — drops the bulky
mbert_tokens/mbert_token_classes fields, which are a tokenized duplicate of
the same unused content.

Output: tests/external_ai4privacy/ai4privacy_en_sample.json (gitignored —
see tests/external_ai4privacy/.gitignore).

Usage:
    docker compose run --rm securescan-cpu python tests/external_ai4privacy/download_ai4privacy_sample.py
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

DATASET = "ai4privacy/pii-masking-openpii-1.5m"
CONFIG = "default"
SPLIT = "train"
PAGE_SIZE = 100  # datasets-server's hard max for /rows
TARGET_COUNT = 4000
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai4privacy_en_sample.json")

BASE_URL = "https://datasets-server.huggingface.co/rows"

KEEP_FIELDS = {"source_text", "masked_text", "privacy_mask", "uid", "language", "region"}


REQUEST_DELAY = 0.6  # seconds between requests, to stay under datasets-server's rate limit


def fetch_page(offset: int, retries: int = 6):
    params = (
        f"?dataset={DATASET.replace('/', '%2F')}&config={CONFIG}&split={SPLIT}"
        f"&offset={offset}&length={PAGE_SIZE}"
    )
    url = BASE_URL + params
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = float(e.headers.get("Retry-After", 5 * (attempt + 1)))
                print(f"[!] 429 rate-limited at offset={offset}, waiting {wait:.0f}s (attempt {attempt + 1})")
                time.sleep(wait)
                continue
            if attempt == retries - 1:
                raise
            print(f"[!] retry {attempt + 1} for offset={offset}: {e}")
            time.sleep(2)
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == retries - 1:
                raise
            print(f"[!] retry {attempt + 1} for offset={offset}: {e}")
            time.sleep(2)
    raise RuntimeError(f"Failed to fetch offset={offset} after {retries} retries")


def main():
    collected = []
    offset = 0
    total_rows_seen = 0
    start = time.time()

    while len(collected) < TARGET_COUNT:
        page = fetch_page(offset)
        rows = page.get("rows", [])
        if not rows:
            print(f"[!] Ran out of rows at offset={offset} (dataset exhausted or error: {page.get('error')})")
            break

        for r in rows:
            row = r["row"]
            total_rows_seen += 1
            if row.get("language") == "en":
                collected.append({k: v for k, v in row.items() if k in KEEP_FIELDS})

        offset += PAGE_SIZE
        if offset % 2000 == 0:
            elapsed = time.time() - start
            print(f"[i] offset={offset} seen={total_rows_seen} collected={len(collected)} elapsed={elapsed:.0f}s")

        time.sleep(REQUEST_DELAY)

    elapsed = time.time() - start
    print(f"[✓] Collected {len(collected)} English samples from {total_rows_seen} rows scanned in {elapsed:.0f}s")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(collected, f, ensure_ascii=False)

    size_mb = os.path.getsize(OUT_PATH) / (1024 * 1024)
    print(f"[✓] Wrote {OUT_PATH} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
