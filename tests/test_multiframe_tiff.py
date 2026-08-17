#!/usr/bin/env python3
"""Regression coverage for audit finding #8 multi-page TIFF extraction."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import extractors.image_extractor as image_extractor


SINGLE_FRAME_FIXTURE = (
    Path(__file__).resolve().parent / "format_data" / "image_clean.png"
)


def _font(size: int = 64):
    return ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size
    )


def _page(text: str) -> Image.Image:
    image = Image.new("RGB", (1600, 1000), "white")
    ImageDraw.Draw(image).multiline_text(
        (100, 150), text, fill="black", font=_font(), spacing=30
    )
    return image


def _make_multiframe(path: Path, image_format: str = "TIFF") -> None:
    first = _page("PAGE ONE\nAccount holder: SAMPLE")
    second = _page("PAGE TWO\nSIN: 132-677-360")
    first.save(
        path,
        format=image_format,
        save_all=True,
        append_images=[second],
    )


def main() -> int:
    rows = []

    with Image.open(SINGLE_FRAME_FIXTURE) as image:
        expected = image_extractor._extract_image_text_with_confidence(
            image, preprocess=True
        )
    actual = image_extractor.extract_image(
        str(SINGLE_FRAME_FIXTURE),
        return_confidence=True,
        return_details=True,
    )
    rows.append(
        (
            "single-frame result is bit-identical to legacy path",
            actual == expected,
            f"confidence={actual[1]:.2f} details={actual[2]}",
        )
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tiff_path = root / "two-pages.tiff"
        _make_multiframe(tiff_path)

        text, confidence, details = image_extractor.extract_image(
            str(tiff_path), return_confidence=True, return_details=True
        )
        rows.append(
            (
                "multi-page TIFF OCR returns both pages in order",
                "PAGE ONE" in text
                and "PAGE TWO" in text
                and text.index("PAGE ONE") < text.index("PAGE TWO")
                and "132-677-360" in text,
                repr(text),
            )
        )
        rows.append(
            (
                "multi-page details contain every frame",
                details.get("frame_count") == 2
                and [frame["frame_index"] for frame in details.get("frames", [])]
                == [0, 1],
                repr(details),
            )
        )

        with patch.object(image_extractor, "detect_faces", side_effect=[1, 2]):
            metadata = image_extractor.extract_image(
                str(tiff_path), metadata_only=True
            )
        rows.append(
            (
                "multi-page TIFF metadata sums per-frame faces",
                metadata.get("faces_detected") == 3,
                f"faces={metadata.get('faces_detected')}",
            )
        )

        mocked = [
            ("AAAA", 80.0, {"psm_used": 3}),
            ("B B", 20.0, {"psm_used": 11}),
        ]
        with patch.object(
            image_extractor,
            "_extract_image_text_with_confidence",
            side_effect=mocked,
        ):
            weighted = image_extractor.extract_image(
                str(tiff_path), return_confidence=True, return_details=True
            )
        rows.append(
            (
                "confidence is weighted by non-whitespace text length",
                weighted[1] == 60.0,
                f"confidence={weighted[1]}",
            )
        )

        with patch.object(
            image_extractor,
            "_extract_image_text_with_confidence",
            side_effect=[
                ("", 70.0, {"psm_used": 3}),
                ("", 30.0, {"psm_used": 11}),
            ],
        ):
            empty = image_extractor.extract_image(
                str(tiff_path), return_confidence=True, return_details=True
            )
        rows.append(
            (
                "all-empty frames fall back to mean confidence",
                empty[0] == "" and empty[1] == 50.0,
                f"text={empty[0]!r} confidence={empty[1]}",
            )
        )

        gif_path = root / "animated.gif"
        _make_multiframe(gif_path, image_format="GIF")
        with patch.object(
            image_extractor,
            "_extract_image_text_with_confidence",
            return_value=("FRAME ZERO", 91.0, {"psm_used": 3}),
        ) as mocked_ocr:
            gif_result = image_extractor.extract_image(
                str(gif_path), return_confidence=True, return_details=True
            )
        rows.append(
            (
                "animated GIF remains frame-zero-only",
                mocked_ocr.call_count == 1
                and gif_result == ("FRAME ZERO", 91.0, {"psm_used": 3}),
                f"calls={mocked_ocr.call_count} result={gif_result}",
            )
        )

    print(f"{'CASE':<58} {'RESULT':<7} DETAIL")
    print("-" * 110)
    for name, passed, detail in rows:
        print(f"{name:<58} {'PASS' if passed else 'FAIL':<7} {detail}")
    passed_count = sum(passed for _, passed, _ in rows)
    print("-" * 110)
    print(f"SUMMARY: {passed_count}/{len(rows)} passed")
    return 0 if passed_count == len(rows) else 1


def test_multiframe_tiff():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
