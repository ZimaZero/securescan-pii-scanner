#!/usr/bin/env python3
"""Fault-injection coverage for subsystem-blindness audit findings."""

import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import discovery
from detectors import hybrid_detector
from extractors import image_extractor, pdf_extractor
from extractors.errors import ExtractionError
from report_generator import generate_markdown
from report_html import generate_html
from report_json import generate_json


class _FakePage:
    def __init__(self, text):
        self.text = text

    def get_text(self, _kind):
        return self.text


class _FakeDoc:
    def __init__(self, texts):
        self.pages = [_FakePage(text) for text in texts]
        self.metadata = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self.pages)

    def __len__(self):
        return len(self.pages)


def _pdf_open(texts):
    return lambda _path: _FakeDoc(texts)


def _raise_ocr(*_args, **_kwargs):
    raise RuntimeError("simulated OCR backend failure")


def main():
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        image_path = Path("tests/format_data/image_clean.png").resolve()
        with patch.object(image_extractor, "_run_paddle_ocr", _raise_ocr):
            image_result = discovery.scan_file(str(image_path), verify=False)
        image_ok = (
            image_result.get("scan_status") == "extraction_failed"
            and image_result.get("score") is None
            and image_result.get("failure_reason")
            == "OCR unavailable/failed on all attempts"
        )
        rows.append(("PaddleOCR dead is extraction_failed", image_ok,
                     image_result.get("failure_reason")))

        pdf_path = root / "scanned.pdf"
        pdf_path.write_bytes(b"synthetic placeholder")
        with (
            patch.object(pdf_extractor.fitz, "open", _pdf_open([""])),
            patch.object(
                pdf_extractor,
                "_extract_page_with_ocr",
                side_effect=ExtractionError("OCR unavailable/failed on all attempts"),
            ),
        ):
            pdf_result = discovery.scan_file(str(pdf_path), verify=False)
        pdf_ok = (
            pdf_result.get("scan_status") == "extraction_failed"
            and pdf_result.get("score") is None
            and pdf_result.get("failure_reason")
            == "OCR unavailable/failed on all attempts"
        )
        rows.append(("All scanned-PDF OCR dead is extraction_failed", pdf_ok,
                     pdf_result.get("failure_reason")))

        mixed_path = root / "mixed.pdf"
        mixed_path.write_bytes(b"synthetic placeholder")
        native = "Readable native text " * 20
        with (
            patch.object(pdf_extractor.fitz, "open", _pdf_open([native, ""])),
            patch.object(
                pdf_extractor,
                "_extract_page_with_ocr",
                side_effect=ExtractionError("one page failed"),
            ),
        ):
            mixed_result = discovery.scan_file(str(mixed_path), verify=False)
        md_path = root / "mixed.md"
        html_path = root / "mixed.html"
        json_path = root / "mixed.json"
        md = generate_markdown([mixed_result], str(md_path))
        html = generate_html([mixed_result], str(html_path))
        json_report = generate_json([mixed_result], str(json_path))
        mixed_meta = mixed_result.get("metadata", {})
        mixed_ok = (
            mixed_result.get("scan_status") == "scanned"
            and mixed_meta.get("ocr_attempted") is True
            and mixed_meta.get("pages_failed") == 1
            and mixed_meta.get("pages_ocr_attempted") == 1
            and "Pages Failed" in md
            and "Pages Failed" in html
            and json_report["files"][0]["metadata"].get("pages_failed") == 1
        )
        rows.append(("One PDF page failure is recorded, scan proceeds", mixed_ok,
                     str(mixed_meta)))

        faint_path = root / "neutral.pdf"
        faint_path.write_bytes(b"synthetic placeholder")
        with (
            patch.object(pdf_extractor.fitz, "open", _pdf_open([""])),
            patch.object(pdf_extractor, "_extract_page_with_ocr", return_value="faint"),
        ):
            faint_result = discovery.scan_file(str(faint_path), verify=False)
        alarm = faint_result.get("mismatch_alarm") or {}
        alarm_ok = (
            faint_result.get("scan_status") == "scanned"
            and faint_result.get("metadata", {}).get("ocr_attempted") is True
            and alarm.get("triggered_by") == "unreadable"
            and "Document yielded" in alarm.get("reason", "")
        )
        rows.append(("Trigger D covers OCR-attempted scanned PDFs", alarm_ok,
                     alarm.get("triggered_by")))

        text_path = root / "contains_pii.txt"
        text_path.write_text("Email: audit@example.com", encoding="utf-8")
        partial_warning = io.StringIO()
        with (
            patch.object(
                hybrid_detector,
                "detect_entities_gliner",
                side_effect=RuntimeError("simulated GLiNER failure"),
            ),
            contextlib.redirect_stdout(partial_warning),
        ):
            partial_result = discovery.scan_file(
                str(text_path), verify=False, run_ner=True
            )
        partial_md = generate_markdown(
            [partial_result], str(root / "partial-degraded.md")
        )
        partial_html = generate_html(
            [partial_result], str(root / "partial-degraded.html")
        )
        partial_json = generate_json(
            [partial_result], str(root / "partial-degraded.json")
        )
        partial_ok = (
            partial_result.get("scan_status") == "scanned"
            and partial_result.get("detection_degraded") is True
            and partial_result.get("failed_layers") == ["gliner"]
            and partial_result.get("matches", {})
            .get("_metadata", {})
            .get("failed_layers") == ["gliner"]
            and partial_result.get("matches", {}).get("contact.email")
            and "Detection degraded" in partial_warning.getvalue()
            and "gliner" in partial_md
            and "audit@example.com" in partial_md
            and "gliner" in partial_html
            and partial_json["files"][0]["failed_layers"] == ["gliner"]
        )
        rows.append(("One GLiNER failure is reported; other findings survive",
                     partial_ok, str(partial_result.get("failed_layers"))))

        layer_names = (
            "detect_regex",
            "detect_pii_keywords",
            "detect_entities_gliner",
            "detect_secrets",
            "detect_health_cards",
            "detect_passports",
            "detect_uci",
            "detect_status_card",
            "detect_ocr_recovery",
            "detect_drivers_licenses",
            "detect_mrz",
        )
        stack = contextlib.ExitStack()
        warning = io.StringIO()
        with stack:
            for name in layer_names:
                stack.enter_context(
                    patch.object(
                        hybrid_detector,
                        name,
                        side_effect=RuntimeError(f"{name} dead"),
                    )
                )
            with contextlib.redirect_stdout(warning):
                degraded_result = discovery.scan_file(
                    str(text_path), verify=False, run_ner=True
                )
        degraded_md = generate_markdown(
            [degraded_result], str(root / "degraded.md")
        )
        degraded_html = generate_html(
            [degraded_result], str(root / "degraded.html")
        )
        degraded_json = generate_json(
            [degraded_result], str(root / "degraded.json")
        )
        degraded_ok = (
            degraded_result.get("scan_status") == "scanned"
            and degraded_result.get("detection_degraded") is True
            and degraded_result.get("matches", {})
            .get("_metadata", {})
            .get("detection_degraded")
            is True
            and "Detection degraded" in warning.getvalue()
            and "Detection degraded" in degraded_md
            and "Detection degraded" in degraded_html
            and degraded_json["files"][0]["detection_degraded"] is True
        )
        rows.append(("All detectors dead emits degraded warning/report note",
                     degraded_ok, f"score={degraded_result.get('score')}"))

    print(f"{'CASE':<58} {'RESULT':<7} DETAIL")
    print("-" * 100)
    for name, passed, detail in rows:
        print(f"{name:<58} {'PASS' if passed else 'FAIL':<7} {detail}")
    passed_count = sum(passed for _, passed, _ in rows)
    print("-" * 100)
    print(f"SUMMARY: {passed_count}/{len(rows)} passed")
    return 0 if passed_count == len(rows) else 1


def test_backend_fault_matrix():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
