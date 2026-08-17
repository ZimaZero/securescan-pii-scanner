#!/usr/bin/env python3
"""Regression coverage for audit finding #2 PDF native-text preservation."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extractors.pdf_extractor import extract_pdf


def _make_pdf(path: Path, text: str) -> None:
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), text)
        doc.save(path)


def main() -> int:
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "short-native.pdf"
        native = "SIN 132-677-360"
        _make_pdf(pdf_path, native)

        cases = [
            (
                "empty OCR preserves short native text",
                "",
                native,
            ),
            (
                "native contained in OCR is not duplicated",
                f"Identity record: {native}",
                f"Identity record: {native}",
            ),
            (
                "OCR contained in native is not duplicated",
                "132-677-360",
                native,
            ),
            (
                "distinct native and OCR text are both retained",
                "Document photograph",
                f"{native}\nDocument photograph",
            ),
        ]

        for name, ocr_text, expected in cases:
            with patch(
                "extractors.pdf_extractor._extract_page_with_ocr",
                return_value=ocr_text,
            ):
                actual = extract_pdf(str(pdf_path))
            rows.append((name, actual == expected, repr(actual)))

    print(f"{'CASE':<50} {'RESULT':<7} DETAIL")
    print("-" * 90)
    for name, passed, detail in rows:
        print(f"{name:<50} {'PASS' if passed else 'FAIL':<7} {detail}")
    passed_count = sum(passed for _, passed, _ in rows)
    print("-" * 90)
    print(f"SUMMARY: {passed_count}/{len(rows)} passed")
    return 0 if passed_count == len(rows) else 1


def test_pdf_native_text_preservation():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
