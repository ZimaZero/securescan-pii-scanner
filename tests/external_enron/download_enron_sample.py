#!/usr/bin/env python3
"""
Downloads the CMU Enron email corpus archive and extracts a deterministic
random sample of N message files. By default this regenerates the fixed
1000-message anchor at tests/external_enron/sample/. A different size must use
an explicit output directory so the anchor cannot be overwritten accidentally.

Source: https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz — a ~423MB
gzip archive (the corpus itself, uncompressed, is the ~1.7GB commonly-cited
figure; the harness never extracts the full archive). Cached at
tests/external_enron/enron_raw/enron_mail_20150507.tar.gz (gitignored) so
re-runs don't re-download.

Sampling: SEED = 1337 (project convention, see tests/make_stress_data.py).
The full member list is enumerated first (population), then
random.sample(population, N) — NOT reservoir sampling — so results depend
only on N and SEED, not on read order. Re-running with the same SEED/N
produces the identical 1000 files (verified by the caller running this
script twice and diffing the sorted file list).

Extraction is member-by-member via TarFile.extractfile() (no .extractall())
so only the sampled files are ever materialized on disk, not the full
~500K-message corpus.

Output: tests/external_enron/sample/<user>__<folder>__<msgnum>.txt, e.g.
skilling-j__inbox__42.txt — flattened maildir path so origin is traceable
from the filename alone. Files are written verbatim (byte-for-byte via a
latin-1 round trip, which never raises and preserves every byte's ordinal —
Enron messages are RFC-822-ish and predominantly ASCII/Latin-1, so this
avoids silently dropping or mangling header bytes the way utf-8-with-
replacement would). Headers are kept: they contain emails/phone numbers/
names and are exactly what a DLP tool scans.

Usage:
    docker compose run --rm securescan-cpu python tests/external_enron/download_enron_sample.py
    docker compose run --rm securescan-cpu python tests/external_enron/download_enron_sample.py \
        --sample-size 5000 --output-dir sample_5000
"""

import argparse
import os
import random
import shutil
import sys
import tarfile
import time
import urllib.request

SEED = 1337
SAMPLE_N = 1000

ARCHIVE_URL = "https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz"
HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "enron_raw")
ARCHIVE_PATH = os.path.join(RAW_DIR, "enron_mail_20150507.tar.gz")
SAMPLE_DIR = os.path.join(HERE, "sample")

MIN_FREE_BYTES_FOR_DOWNLOAD = 2 * 1024 * 1024 * 1024  # 2GB headroom


def check_disk_space():
    usage = shutil.disk_usage(HERE)
    free_gb = usage.free / (1024 ** 3)
    print(f"[i] Free disk space at {HERE}: {free_gb:.1f} GB")
    if usage.free < MIN_FREE_BYTES_FOR_DOWNLOAD:
        print(
            f"[!] Only {free_gb:.1f} GB free, need at least "
            f"{MIN_FREE_BYTES_FOR_DOWNLOAD / (1024**3):.0f} GB headroom. Stopping."
        )
        sys.exit(1)


def download_archive():
    if os.path.exists(ARCHIVE_PATH):
        size_mb = os.path.getsize(ARCHIVE_PATH) / (1024 * 1024)
        print(f"[i] Using cached archive: {ARCHIVE_PATH} ({size_mb:.1f} MB)")
        return

    os.makedirs(RAW_DIR, exist_ok=True)
    print(f"[+] Downloading {ARCHIVE_URL}")
    start = time.time()
    tmp_path = ARCHIVE_PATH + ".part"
    try:
        with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024
            with open(tmp_path, "wb") as out:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = 100 * downloaded / total
                        elapsed = time.time() - start
                        mb = downloaded / (1024 * 1024)
                        print(f"\r[+] {mb:.0f} MB ({pct:.0f}%) in {elapsed:.0f}s", end="", flush=True)
        print()
    except OSError as e:
        print(f"\n[!] Download failed: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        sys.exit(1)

    os.rename(tmp_path, ARCHIVE_PATH)
    elapsed = time.time() - start
    size_mb = os.path.getsize(ARCHIVE_PATH) / (1024 * 1024)
    print(f"[✓] Downloaded {size_mb:.1f} MB in {elapsed:.0f}s")


def flatten_name(member_name):
    """'maildir/skilling-j/inbox/42.' -> 'skilling-j__inbox__42.txt'"""
    parts = member_name.split("/")
    if parts and parts[0] == "maildir":
        parts = parts[1:]
    flat = "__".join(p for p in parts if p)
    flat = flat.rstrip(".")
    return flat + ".txt"


def extract_sample(sample_n=SAMPLE_N, sample_dir=SAMPLE_DIR):
    os.makedirs(sample_dir, exist_ok=True)
    for f in os.listdir(sample_dir):
        path = os.path.join(sample_dir, f)
        if os.path.isfile(path):
            os.remove(path)

    print(f"[+] Indexing archive members: {ARCHIVE_PATH}")
    start = time.time()
    with tarfile.open(ARCHIVE_PATH, "r:gz") as tf:
        members = tf.getmembers()
        population = sorted(m.name for m in members if m.isfile())
        print(f"[✓] Indexed {len(members)} members ({len(population)} files) in {time.time() - start:.0f}s")

        random.seed(SEED)
        sampled_names = random.sample(population, sample_n)
        sampled_name_set = set(sampled_names)

        used_flat_names = set()
        total_bytes = 0
        written = 0
        # Keep random.sample() as the selection contract, but materialize the
        # chosen names in archive order. Random seeks in a gzip tar repeatedly
        # decompress the stream and make larger samples impractically slow.
        for member in members:
            name = member.name
            if not member.isfile() or name not in sampled_name_set:
                continue
            fh = tf.extractfile(member)
            if fh is None:
                continue
            raw = fh.read()
            text = raw.decode("latin-1")

            flat = flatten_name(name)
            if flat in used_flat_names:
                base, ext = os.path.splitext(flat)
                i = 2
                while f"{base}__{i}{ext}" in used_flat_names:
                    i += 1
                flat = f"{base}__{i}{ext}"
            used_flat_names.add(flat)

            out_path = os.path.join(sample_dir, flat)
            with open(out_path, "w", encoding="latin-1") as out:
                out.write(text)
            total_bytes += len(raw)
            written += 1

    print(f"[✓] Wrote {written} files, {total_bytes} bytes total, to {sample_dir}")
    return written, total_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=SAMPLE_N)
    parser.add_argument(
        "--output-dir",
        default="sample",
        help="directory name under tests/external_enron (default: sample)",
    )
    args = parser.parse_args()

    if args.sample_size <= 0:
        parser.error("--sample-size must be positive")
    if os.path.basename(args.output_dir) != args.output_dir:
        parser.error("--output-dir must be a single directory name")
    if args.output_dir == "sample" and args.sample_size != SAMPLE_N:
        parser.error(
            "the fixed sample/ anchor must remain 1000 messages; "
            "use a separate --output-dir"
        )

    sample_dir = os.path.join(HERE, args.output_dir)
    check_disk_space()
    download_archive()
    written, total_bytes = extract_sample(args.sample_size, sample_dir)
    print(
        f"[MANIFEST] files={written} total_bytes={total_bytes} "
        f"seed={SEED} output_dir={args.output_dir}"
    )


if __name__ == "__main__":
    main()
