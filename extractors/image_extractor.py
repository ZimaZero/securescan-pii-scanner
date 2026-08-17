#!/usr/bin/env python3
"""Extract image text with the configured PaddleOCR CPU or GPU pipeline.

OCR models are loaded lazily, cached on disk, and reused once per process.
EXIF correction and PaddleOCR's text-line orientation model handle image
orientation; no card-specific preprocessing path is applied.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from importlib.util import find_spec
import os
import threading
from typing import Any, Dict, Tuple

import config
from .errors import ExtractionError
from .face_detector import detect_faces


# PaddleX honors these settings during import/model initialization.  The
# source check is unnecessary after installation and would otherwise make a
# network request; model artifacts remain in a stable, user-local cache.
os.environ.setdefault(
    "PADDLE_PDX_CACHE_HOME",
    os.path.join(os.path.expanduser("~"), ".cache", "securescan", "paddleocr"),
)
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

try:
    from PIL import Image, ImageOps
    import PIL.Image

    PIL_AVAILABLE = True
except Exception:
    Image = None
    ImageOps = None
    PIL_AVAILABLE = False

try:
    import numpy as np

    NUMPY_AVAILABLE = True
except Exception:
    np = None
    NUMPY_AVAILABLE = False


PADDLEOCR_AVAILABLE = (
    find_spec("paddleocr") is not None and find_spec("paddle") is not None
)
PADDLE_DETECTION_MODEL = "PP-OCRv5_mobile_det"
PADDLE_RECOGNITION_MODEL = "en_PP-OCRv5_mobile_rec"
PADDLE_TEXTLINE_ORIENTATION_MODEL = "PP-LCNet_x1_0_textline_ori"
_EXIF_ORIENTATION_TAG = 0x0112
_OCR_ENGINE = None
_OCR_INIT_LOCK = threading.Lock()
_OCR_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paddle-ocr")


def _non_ws_len(text: str) -> int:
    return len("".join((text or "").split()))


def _exif_transpose(img: PIL.Image.Image) -> Tuple[PIL.Image.Image, bool]:
    """Apply an EXIF orientation transform and report whether it changed."""
    if not PIL_AVAILABLE or img is None:
        return img, False
    try:
        orientation = img.getexif().get(_EXIF_ORIENTATION_TAG, 1)
        if orientation in (None, 1):
            return img, False
        transposed = ImageOps.exif_transpose(img)
        return (transposed if transposed is not None else img), True
    except Exception:
        return img, False


def _build_paddle_pipeline(device: str):
    from paddleocr import PaddleOCR

    return PaddleOCR(
        text_detection_model_name=PADDLE_DETECTION_MODEL,
        textline_orientation_model_name=PADDLE_TEXTLINE_ORIENTATION_MODEL,
        text_recognition_model_name=PADDLE_RECOGNITION_MODEL,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=True,
        device=device,
        enable_mkldnn=False,
    )


def _create_ocr_engine():
    """Construct the pinned, benchmarked PaddleOCR pipeline.

    Device selection comes from ``config.PADDLEOCR_DEVICE``: ``cpu`` for the
    CPU service and ``gpu:0`` for the GPU service. Model and preprocessing
    settings remain identical across devices.

    A non-"cpu" device that fails to construct (no NVIDIA GPU actually
    visible to the container — e.g. the GPU compose service started without
    a real GPU/without the NVIDIA Container Toolkit) falls back to CPU
    rather than leaving OCR unavailable for the process. The fallback emits
    one visible warning.
    """
    device = config.PADDLEOCR_DEVICE
    if device == "cpu":
        return _build_paddle_pipeline(device)
    try:
        return _build_paddle_pipeline(device)
    except Exception as exc:
        print(
            f"[!] PaddleOCR device {device!r} unavailable ({type(exc).__name__}: "
            f"{exc}); falling back to CPU."
        )
        return _build_paddle_pipeline("cpu")


def _get_ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        with _OCR_INIT_LOCK:
            if _OCR_ENGINE is None:
                _OCR_ENGINE = _create_ocr_engine()
    return _OCR_ENGINE


def _paddle_payload(result: Any) -> Dict[str, Any]:
    """Normalize PaddleOCR's Result/dict representations across 3.x."""
    if isinstance(result, dict):
        payload = result
    else:
        try:
            payload = dict(result)
        except Exception:
            payload = getattr(result, "json", {})
            if callable(payload):
                payload = payload()
    if isinstance(payload, dict) and isinstance(payload.get("res"), dict):
        payload = payload["res"]
    return payload if isinstance(payload, dict) else {}


def _predict_on_ocr_thread(img: PIL.Image.Image):
    """Convert and infer on Paddle's single predictor-owning worker thread."""
    image_array = np.asarray(img.convert("RGB"))[:, :, ::-1].copy()
    return list(_get_ocr_engine().predict(image_array))


def _run_paddle_ocr(img: PIL.Image.Image) -> Tuple[str, float, int]:
    """Single PaddleOCR entry point, kept explicit for fault injection.

    Returns text, a 0..100 character-weighted recognition confidence, and
    the number of recognized lines.  No recognized text is confidence-
    filtered before downstream detectors see it.
    """
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    elif img.mode == "RGBA":
        background = Image.new("RGB", img.size, "white")
        background.paste(img, mask=img.getchannel("A"))
        img = background

    # Paddle predictors are not documented as thread-safe or cross-thread
    # portable, and one predictor per scan worker multiplies the runtime's
    # substantial resident footprint.  Construction, input conversion, and
    # inference all stay on one dedicated thread; callers may be any scan
    # worker and wait without holding an extractor lock.
    predictions = _OCR_EXECUTOR.submit(_predict_on_ocr_thread, img).result()

    lines = []
    weighted_score = 0.0
    total_weight = 0
    for prediction in predictions:
        payload = _paddle_payload(prediction)
        texts = payload.get("rec_texts") or []
        scores = payload.get("rec_scores") or []
        for index, raw_text in enumerate(texts):
            line = str(raw_text).strip()
            if not line:
                continue
            try:
                score = float(scores[index])
            except (IndexError, TypeError, ValueError):
                score = 0.0
            weight = max(1, _non_ws_len(line))
            lines.append(line)
            weighted_score += max(0.0, min(1.0, score)) * weight
            total_weight += weight

    confidence = 100.0 * weighted_score / total_weight if total_weight else 0.0
    return "\n".join(lines).strip(), confidence, len(lines)


def _extract_image_text_with_confidence(
    img: PIL.Image.Image, preprocess: bool = True
) -> Tuple[str, float, Dict[str, Any]]:
    """Extract text while preserving the extractor's confidence contract.

    ``preprocess`` remains accepted for API compatibility.  It deliberately
    does not enable the rejected card-oriented preprocessing arm.
    """
    if not (PADDLEOCR_AVAILABLE and PIL_AVAILABLE and NUMPY_AVAILABLE) or img is None:
        return "", 0.0, {}
    try:
        corrected, exif_transposed = _exif_transpose(img)
        text, confidence, line_count = _run_paddle_ocr(corrected)
        details = {
            "ocr_engine": "paddleocr",
            "confidence_mapping": "character_weighted_mean_rec_score_x100",
            "ocr_character_count": len(text),
            "ocr_line_count": line_count,
            "textline_orientation": True,
            "rotation_applied": 0,
            "rotation_method": "exif" if exif_transposed else "none",
            "upscaled": False,
            "exif_transposed": exif_transposed,
            "preprocessing_applied": False,
        }
        return text, confidence, details
    except Exception as exc:
        raise ExtractionError("OCR unavailable/failed on all attempts") from exc


def _extract_image_text(img: PIL.Image.Image, preprocess: bool = True) -> str:
    text, _confidence, _details = _extract_image_text_with_confidence(
        img, preprocess=preprocess
    )
    return text


def _tiff_frames(img: PIL.Image.Image):
    """Yield independent copies of every page in a multi-page TIFF."""
    for frame_index in range(img.n_frames):
        img.seek(frame_index)
        yield frame_index, img.copy()


def _extract_multiframe_tiff_text(img: PIL.Image.Image, preprocess: bool) -> str:
    texts = [
        _extract_image_text(frame, preprocess=preprocess)
        for _, frame in _tiff_frames(img)
    ]
    return "\n\n".join(text for text in texts if text)


def _extract_multiframe_tiff_with_confidence(
    img: PIL.Image.Image, preprocess: bool
) -> Tuple[str, float, Dict[str, Any]]:
    frame_results = []
    weighted_confidence = 0.0
    total_weight = 0
    for frame_index, frame in _tiff_frames(img):
        text, confidence, details = _extract_image_text_with_confidence(
            frame, preprocess=preprocess
        )
        weight = _non_ws_len(text)
        weighted_confidence += confidence * weight
        total_weight += weight
        frame_results.append(
            {
                "frame_index": frame_index,
                "text": text,
                "avg_confidence": confidence,
                "details": details,
            }
        )
    confidence = (
        weighted_confidence / total_weight
        if total_weight
        else (
            sum(frame["avg_confidence"] for frame in frame_results)
            / len(frame_results)
            if frame_results
            else 0.0
        )
    )
    text = "\n\n".join(frame["text"] for frame in frame_results if frame["text"])
    return text, confidence, {
        "ocr_engine": "paddleocr",
        "ocr_character_count": len(text),
        "frame_count": len(frame_results),
        "frames": frame_results,
    }


def _extract_image_metadata(img: PIL.Image.Image, filepath: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    if not PIL_AVAILABLE or img is None:
        return meta
    try:
        meta["width"] = img.width
        meta["height"] = img.height
        meta["format"] = img.format or "Unknown"
        meta["mode"] = img.mode
        if os.path.isfile(filepath):
            meta["size_kb"] = round(os.path.getsize(filepath) / 1024, 2)

        try:
            exif = img.getexif()
            exif_tags = {
                271: "make",
                272: "model",
                274: "orientation",
                306: "datetime",
                36867: "datetime_original",
                36868: "datetime_digitized",
            }
            for tag_id, tag_name in exif_tags.items():
                if tag_id in exif:
                    value = exif[tag_id]
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="ignore")
                    if value not in (None, "", "None"):
                        meta[tag_name] = str(value)
        except Exception:
            pass

        dpi = img.info.get("dpi")
        if dpi:
            meta["dpi"] = dpi

        ext = os.path.splitext(filepath)[1].lower()
        if ext in {".tif", ".tiff"} and getattr(img, "n_frames", 1) > 1:
            faces_detected = 0
            for _, frame in _tiff_frames(img):
                corrected, _ = _exif_transpose(frame)
                frame_count = detect_faces(corrected)
                if frame_count < 0:
                    faces_detected = -1
                    break
                faces_detected += frame_count
        else:
            corrected, _ = _exif_transpose(img)
            faces_detected = detect_faces(corrected)
        if faces_detected >= 0:
            meta["faces_detected"] = faces_detected
    except Exception:
        pass
    return meta


def extract_image(
    path: str,
    metadata_only: bool = False,
    return_confidence: bool = False,
    preprocess: bool = True,
    return_details: bool = False,
) -> str | Dict[str, Any] | Tuple[str, float] | Tuple[str, float, Dict[str, Any]]:
    """Extract an image while retaining the established external contract."""
    if not isinstance(path, str):
        raise ExtractionError(f"TypeError: path must be str, got {type(path).__name__}")
    if not path or not path.strip():
        raise ExtractionError("ValueError: path cannot be empty")
    if not PIL_AVAILABLE:
        raise ExtractionError("RuntimeError: PIL/Pillow is not installed")
    if not NUMPY_AVAILABLE and not metadata_only:
        raise ExtractionError("RuntimeError: numpy is not installed")
    if not PADDLEOCR_AVAILABLE and not metadata_only:
        raise ExtractionError("RuntimeError: PaddleOCR is not installed")
    if not os.path.isfile(path):
        raise ExtractionError("FileNotFoundError: file does not exist")

    ext = os.path.splitext(path)[1].lower()
    valid_extensions = {
        ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif", ".webp"
    }
    if ext not in valid_extensions:
        raise ExtractionError(f"ValueError: unsupported extension {ext}")

    try:
        with Image.open(path) as img:
            if metadata_only:
                return _extract_image_metadata(img, path)
            if ext in {".tif", ".tiff"} and getattr(img, "n_frames", 1) > 1:
                if return_confidence:
                    text, confidence, details = _extract_multiframe_tiff_with_confidence(
                        img, preprocess=preprocess
                    )
                    return (text, confidence, details) if return_details else (text, confidence)
                return _extract_multiframe_tiff_text(img, preprocess=preprocess)

            text, confidence, details = _extract_image_text_with_confidence(
                img, preprocess=preprocess
            )
            if return_confidence:
                return (text, confidence, details) if return_details else (text, confidence)
            return text
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError.from_exception(exc) from exc


def check_dependencies() -> Dict[str, bool]:
    """Return availability of the image extractor's runtime dependencies."""
    return {
        "pillow": PIL_AVAILABLE,
        "numpy": NUMPY_AVAILABLE,
        "paddleocr": PADDLEOCR_AVAILABLE,
        "paddle_cpu": PADDLEOCR_AVAILABLE,
    }


if __name__ == "__main__":
    print("=== Image Extractor Dependency Check ===")
    for library, available in check_dependencies().items():
        print(f"{library}: {'✓ Available' if available else '✗ Missing'}")
