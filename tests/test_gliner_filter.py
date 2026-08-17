#!/usr/bin/env python3
# tests/test_gliner_filter.py
"""
Test suite for detectors/gliner_detector.py's _is_structured() shape filter.

Tests the filter function directly — no GLiNER model load, no inference,
just the regex/exact-match shape checks that decide whether a raw entity
span GLiNER returned should be kept or dropped as structured PII/noise
already owned by the regex/checksum layers.

Run directly for a pass/fail table + summary:
    docker compose run --rm securescan-cpu python tests/test_gliner_filter.py

Also importable / pytest-compatible (test_should_filter / test_should_keep).
"""

import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors import gliner_detector
from detectors.gliner_detector import (
    CHUNK_OVERLAP_TOKENS,
    CHUNK_OVERLAP_WORDS,
    MAX_TOKENS_PER_CHUNK,
    MAX_WORDS_PER_CHUNK,
    _chunks,
    _is_structured,
)
from detectors import hybrid_detector
import discovery

# Confirmed junk leaks from /tmp/gliner_probe.py (all cleared the 0.50 gate
# before this fix) plus the digit-heavy/domain/label shapes described in the
# fix itself.
SHOULD_FILTER = [
    "403-555-1234",
    "132-677-360",
    "4111 1111 1111 1111",
    "example.com",
    "portal.example.com",
    "192.168.1.1",
    "https://x.com/y",
    "SIN",
    "ssn",
]

# Real entities that must survive — including "St. John's", which contains a
# period but is a genuine Canadian location, not a domain.
SHOULD_KEEP = [
    "Toronto",
    "John Smith",
    "Acme Corporation",
    "Northern Lights Consulting Ltd",
    "Dr. Patel",
    "2026-08-01",
    "Vancouver",
    "St. John's",
]


def run_suite():
    rows, failed = [], 0

    for value in SHOULD_FILTER:
        ok = _is_structured(value) is True
        rows.append(("FILTER", value, ok))
        failed += not ok

    for value in SHOULD_KEEP:
        ok = _is_structured(value) is False
        rows.append(("KEEP", value, ok))
        failed += not ok

    cap_rows, cap_failures = _check_ner_cap()
    rows.extend(cap_rows)
    failed += len(cap_failures)

    chunk_rows, chunk_failures = _check_chunk_bounds()
    rows.extend(chunk_rows)
    failed += len(chunk_failures)

    concurrency_rows, concurrency_failures = _check_chunk_concurrency()
    rows.extend(concurrency_rows)
    failed += len(concurrency_failures)

    print(f"{'GRP':8} {'VALUE':<40} RESULT")
    print("-" * 60)
    for grp, value, ok in rows:
        print(f"{grp:8} {value!r:<40} {'PASS' if ok else 'FAIL'}")
    print("-" * 60)
    print(f"SUMMARY: {len(rows) - failed}/{len(rows)} passed, {failed} failed")
    return failed


def _check_ner_cap():
    rows, failures = [], []
    original = hybrid_detector.detect_entities_gliner

    def fake_gliner(text):
        return {
            "person": [
                (name, 0.99)
                for name in ("Alice", "Zelda")
                if name in text
            ]
        }

    hybrid_detector.detect_entities_gliner = fake_gliner
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "long.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("Alice " + ("x" * 100) + " Zelda")

            capped = discovery.scan_file(
                path,
                verify=False,
                run_ner=True,
                ner_max_chars=50,
            )
            people = [
                finding["value"]
                for finding in capped["matches"].get("entity.person", [])
            ]
            actual = (
                people,
                capped["matches"]["_metadata"]["ner_truncated"],
                capped["metadata"].get("ner_truncated"),
                capped["metadata"].get("ner_analyzed_chars"),
            )
            expected = (["Alice"], True, True, 50)
            ok = actual == expected
            rows.append(("NER_CAP", "custom cap analyzes prefix only", ok))
            if not ok:
                failures.append(("custom cap", actual, expected))

            omitted = discovery.scan_file(path, verify=False, run_ner=True)
            explicit_none = discovery.scan_file(
                path,
                verify=False,
                run_ner=True,
                ner_max_chars=None,
            )
            ok = omitted == explicit_none
            rows.append(("NER_CAP", "None is identical to omitted cap", ok))
            if not ok:
                failures.append(("None parity", explicit_none, omitted))
    finally:
        hybrid_detector.detect_entities_gliner = original
    return rows, failures


class _WhitespaceSplitter:
    @staticmethod
    def words_splitter(text):
        import re

        return [
            (match.group(), match.start(), match.end())
            for match in re.finditer(r"\S+", text)
        ]


class _CharacterTokenizer:
    @staticmethod
    def encode(text, add_special_tokens=False):
        return list(text)


class _FakeModel:
    data_processor = _WhitespaceSplitter()


def _with_counting_tokenizer(tokenizer, callback):
    original = gliner_detector._COUNTING_TOKENIZER
    gliner_detector._COUNTING_TOKENIZER = tokenizer
    try:
        return callback()
    finally:
        gliner_detector._COUNTING_TOKENIZER = original


def _check_chunk_bounds():
    rows, failures = [], []
    model = _FakeModel()
    tokenizer = _CharacterTokenizer()

    def run():
        base64_text = "\n".join(["A" * 76] * (MAX_WORDS_PER_CHUNK + 25))
        base64_chunks = list(_chunks(base64_text, model))
        token_counts = [
            len(tokenizer.encode(chunk, add_special_tokens=False))
            for chunk in base64_chunks
        ]
        word_counts = [
            len(model.data_processor.words_splitter(chunk))
            for chunk in base64_chunks
        ]
        ok = (
            len(base64_chunks) > 1
            and max(token_counts) <= MAX_TOKENS_PER_CHUNK
            and max(word_counts) <= MAX_WORDS_PER_CHUNK
        )
        rows.append(("CHUNKS", "base64 respects word and token bounds", ok))
        if not ok:
            failures.append(("base64 bounds", token_counts, word_counts))

        ok = all(
            left[-CHUNK_OVERLAP_TOKENS:] == right[:CHUNK_OVERLAP_TOKENS]
            for left, right in zip(base64_chunks, base64_chunks[1:])
            if len(right) == MAX_TOKENS_PER_CHUNK
        )
        rows.append(("CHUNKS", "token-bound chunks retain overlap", ok))
        if not ok:
            failures.append(("token overlap", CHUNK_OVERLAP_TOKENS))

        prose = " ".join(["a"] * (MAX_WORDS_PER_CHUNK + 75))
        prose_chunks = list(_chunks(prose, model))
        first_words = prose_chunks[0].split()
        second_words = prose_chunks[1].split()
        ok = (
            len(first_words) == MAX_WORDS_PER_CHUNK
            and first_words[-CHUNK_OVERLAP_WORDS:]
            == second_words[:CHUNK_OVERLAP_WORDS]
        )
        rows.append(("CHUNKS", "normal prose remains word-bound", ok))
        if not ok:
            failures.append(("word overlap", [len(chunk) for chunk in prose_chunks]))

    _with_counting_tokenizer(tokenizer, run)
    return rows, failures


class _BorrowCheckedTokenizer:
    def __init__(self):
        self._borrowed = threading.Lock()

    def encode(self, text, add_special_tokens=False):
        if not self._borrowed.acquire(blocking=False):
            raise RuntimeError("Already borrowed")
        try:
            time.sleep(0.001)
            return list(text)
        finally:
            self._borrowed.release()


def _check_chunk_concurrency():
    tokenizer = _BorrowCheckedTokenizer()
    model = _FakeModel()
    text = "\n".join(["A" * 76] * (MAX_WORDS_PER_CHUNK + 25))
    failures = []

    def run():
        with ThreadPoolExecutor(max_workers=8) as executor:
            return list(executor.map(
                lambda _index: list(_chunks(text, model)),
                range(16),
            ))

    try:
        results = _with_counting_tokenizer(tokenizer, run)
        ok = all(results)
    except Exception as exc:
        ok = False
        failures.append(("concurrent tokenizer", repr(exc)))
    rows = [("CHUNKS", "counting tokenizer access is serialized", ok)]
    if not ok and not failures:
        failures.append(("concurrent tokenizer", "invalid chunks"))
    return rows, failures


def test_should_filter():
    for value in SHOULD_FILTER:
        assert _is_structured(value) is True, f"{value!r} should be filtered (structured)"


def test_should_keep():
    for value in SHOULD_KEEP:
        assert _is_structured(value) is False, f"{value!r} should be kept (real entity)"


def test_ner_cap():
    _, failures = _check_ner_cap()
    assert not failures, failures


def test_chunk_bounds():
    _, failures = _check_chunk_bounds()
    assert not failures, failures


def test_chunk_concurrency():
    _, failures = _check_chunk_concurrency()
    assert not failures, failures


if __name__ == "__main__":
    sys.exit(1 if run_suite() else 0)
