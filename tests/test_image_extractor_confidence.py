#!/usr/bin/env python3
"""PaddleOCR image-extraction contract and WebP coverage.

Run directly:
    docker compose run --rm securescan-cpu python tests/test_image_extractor_confidence.py
"""

import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image

import config
from extractors import image_extractor
from extractors.image_extractor import extract_image


FIXTURE = PROJECT_ROOT / "tests" / "format_data" / "image_clean.png"


class _FakeEngine:
    def predict(self, image_array):
        assert image_array.ndim == 3 and image_array.shape[2] == 3
        return [
            {
                "rec_texts": ["AB 12", "low confidence evidence"],
                "rec_scores": [0.90, 0.25],
            }
        ]


def test_engine_configuration_is_cpu_with_textline_orientation():
    fake_engine = object()
    with patch("paddleocr.PaddleOCR", return_value=fake_engine) as constructor:
        assert image_extractor._create_ocr_engine() is fake_engine
    kwargs = constructor.call_args.kwargs
    # config.PADDLEOCR_DEVICE is the source of truth: "cpu" by default, but
    # SECURESCAN_PADDLEOCR_DEVICE (set to "gpu:0" in the GPU container, see
    # docker-compose.yml) overrides it, so this suite must pass in both.
    assert kwargs["device"] == config.PADDLEOCR_DEVICE
    assert kwargs["use_textline_orientation"] is True
    assert kwargs["textline_orientation_model_name"] == "PP-LCNet_x1_0_textline_ori"
    assert kwargs["use_doc_orientation_classify"] is False
    assert kwargs["use_doc_unwarping"] is False
    assert kwargs["enable_mkldnn"] is False


def test_gpu_device_falls_back_to_cpu_on_construction_failure():
    """The GPU compose service sets SECURESCAN_PADDLEOCR_DEVICE=gpu:0
    unconditionally; on a host with no NVIDIA GPU actually visible to the
    container, constructing the pipeline with that device raises. This must
    degrade to CPU rather than leaving OCR permanently unavailable for the
    process — mirroring gliner_detector.py's existing CUDA-session
    fallback for the identical situation on the semantic-NER layer."""
    seen_devices = []

    def fake_paddleocr(**kwargs):
        seen_devices.append(kwargs["device"])
        if kwargs["device"] != "cpu":
            raise RuntimeError("no CUDA device visible")
        return object()

    with patch("paddleocr.PaddleOCR", side_effect=fake_paddleocr), patch.object(
        config, "PADDLEOCR_DEVICE", "gpu:0"
    ):
        engine = image_extractor._create_ocr_engine()

    assert engine is not None
    assert seen_devices == ["gpu:0", "cpu"]


def test_cpu_device_construction_failure_is_not_retried():
    """A plain CPU construction failure (the default, non-GPU path) must
    raise normally — there's no further fallback to attempt."""

    def fake_paddleocr(**kwargs):
        raise RuntimeError("boom")

    with patch("paddleocr.PaddleOCR", side_effect=fake_paddleocr), patch.object(
        config, "PADDLEOCR_DEVICE", "cpu"
    ):
        try:
            image_extractor._create_ocr_engine()
            raise AssertionError("expected RuntimeError to propagate")
        except RuntimeError as exc:
            assert str(exc) == "boom"


def test_paddle_text_is_unfiltered_and_confidence_maps_to_existing_scale():
    image = Image.new("RGB", (80, 40), "white")
    with patch.object(image_extractor, "_get_ocr_engine", return_value=_FakeEngine()):
        text, confidence, line_count = image_extractor._run_paddle_ocr(image)

    assert text == "AB 12\nlow confidence evidence"
    assert line_count == 2
    weights = [4, len("lowconfidenceevidence")]
    expected = 100.0 * (0.90 * weights[0] + 0.25 * weights[1]) / sum(weights)
    assert abs(confidence - expected) < 0.001
    assert "low confidence evidence" in text, "low-confidence OCR evidence was filtered"


def test_default_and_confidence_paths_share_identical_text():
    fake_result = (
        "SAME RAW TEXT",
        87.5,
        {
            "ocr_engine": "paddleocr",
            "ocr_character_count": 13,
            "confidence_mapping": "character_weighted_mean_rec_score_x100",
        },
    )
    with patch.object(
        image_extractor, "_extract_image_text_with_confidence", return_value=fake_result
    ):
        default_text = extract_image(str(FIXTURE))
        confidence_text, confidence, details = extract_image(
            str(FIXTURE), return_confidence=True, return_details=True
        )
    assert default_text == confidence_text == "SAME RAW TEXT"
    assert confidence == 87.5
    assert details["ocr_character_count"] == len(confidence_text)


def test_real_paddleocr_contract():
    text, confidence, details = extract_image(
        str(FIXTURE), return_confidence=True, return_details=True
    )
    assert text.strip(), "PaddleOCR extracted no text from the clean fixture"
    assert 0.0 <= confidence <= 100.0
    assert details["ocr_engine"] == "paddleocr"
    assert details["confidence_mapping"] == "character_weighted_mean_rec_score_x100"
    assert details["ocr_character_count"] == len(text)
    assert details["preprocessing_applied"] is False


def test_webp_is_ocr_supported():
    with tempfile.TemporaryDirectory() as tmpdir:
        webp_path = os.path.join(tmpdir, "fixture.webp")
        with Image.open(FIXTURE) as source:
            source.convert("RGB").save(webp_path, "WEBP", quality=95)
        text, confidence, details = extract_image(
            webp_path, return_confidence=True, return_details=True
        )
        assert text.strip(), "PaddleOCR extracted no text from the WebP fixture"
        assert 0.0 <= confidence <= 100.0
        assert details["ocr_engine"] == "paddleocr"
        metadata = extract_image(webp_path, metadata_only=True)
        assert metadata["format"] == "WEBP"


def run_suite():
    tests = [
        test_engine_configuration_is_cpu_with_textline_orientation,
        test_gpu_device_falls_back_to_cpu_on_construction_failure,
        test_cpu_device_construction_failure_is_not_retried,
        test_paddle_text_is_unfiltered_and_confidence_maps_to_existing_scale,
        test_default_and_confidence_paths_share_identical_text,
        test_real_paddleocr_contract,
        test_webp_is_ocr_supported,
    ]
    failures = []
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
        except Exception as exc:
            failures.append((test.__name__, exc))
            print(f"[FAIL] {test.__name__}: {exc}")
    print(f"SUMMARY: {len(tests) - len(failures)}/{len(tests)} passed")
    return failures


if __name__ == "__main__":
    sys.exit(1 if run_suite() else 0)
