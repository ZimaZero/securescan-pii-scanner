#!/usr/bin/env python3
"""Regression coverage for count-only Haar face detection."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extractors.face_detector import detect_faces
import extractors.image_extractor as image_extractor


FORMAT_DATA = Path(__file__).resolve().parent / "format_data"
POSITIVE = FORMAT_DATA / "face_public_domain_astronaut.jpg"
NEGATIVE = FORMAT_DATA / "image_clean.png"
EXIF_ROTATED = FORMAT_DATA / "image_exif_rotated.png"


def _run_cases():
    rows = []

    with Image.open(POSITIVE) as image:
        count = detect_faces(image)
    rows.append(
        (
            "public-domain portrait contains a detectable face",
            count >= 1,
            f"faces={count}",
        )
    )

    with Image.open(NEGATIVE) as image:
        count = detect_faces(image)
    rows.append(
        (
            "existing text-document image contains no face",
            count == 0,
            f"faces={count}",
        )
    )

    with Image.open(POSITIVE) as image, patch(
        "extractors.face_detector.importlib.import_module",
        side_effect=ImportError("cv2 unavailable"),
    ):
        count = detect_faces(image)
    rows.append(
        (
            "OpenCV import failure returns unavailable sentinel",
            count == -1,
            f"faces={count}",
        )
    )

    with patch(
        "extractors.face_detector.importlib.import_module",
        side_effect=ImportError("cv2 unavailable"),
    ):
        metadata = image_extractor.extract_image(str(NEGATIVE), metadata_only=True)
    rows.append(
        (
            "metadata extraction survives unavailable OpenCV",
            isinstance(metadata, dict) and "faces_detected" not in metadata,
            f"faces_detected={metadata.get('faces_detected', 'absent')}",
        )
    )

    observed_sizes = []
    with patch.object(
        image_extractor,
        "detect_faces",
        side_effect=lambda image: observed_sizes.append(image.size) or 0,
    ) as mocked_detector:
        metadata = image_extractor.extract_image(
            str(EXIF_ROTATED), metadata_only=True
        )
    rows.append(
        (
            "single-frame metadata detects once after EXIF transpose",
            mocked_detector.call_count == 1
            and observed_sizes == [(1000, 260)]
            and metadata.get("faces_detected") == 0,
            f"calls={mocked_detector.call_count} sizes={observed_sizes}",
        )
    )

    return rows


def main():
    rows = _run_cases()
    print(f"{'CASE':<58} {'RESULT':<7} DETAIL")
    print("-" * 90)
    for name, passed, detail in rows:
        print(f"{name:<58} {'PASS' if passed else 'FAIL':<7} {detail}")
    passed = sum(ok for _, ok, _ in rows)
    print("-" * 90)
    print(f"SUMMARY: {passed}/{len(rows)} passed")
    return 0 if passed == len(rows) else 1


def test_face_detector_cases():
    assert all(passed for _, passed, _ in _run_cases())


if __name__ == "__main__":
    raise SystemExit(main())
