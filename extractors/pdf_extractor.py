#!/usr/bin/env python3
"""
PDF extractor with OCR fallback

Responsibilities
---------------
1) Full-text extraction using PyMuPDF (fitz) for speed/quality.
2) Automatic OCR fallback for scanned or sparse-text pages.
3) Metadata extraction when `metadata_only=True`.

Design notes
------------
- Single public function: `extract_pdf(path, metadata_only=False)`.
- Hybrid approach: Try text extraction first, fall back to OCR for scanned pages.
- Per-page detection: Only OCR pages that need it (< 50 chars).
- Raises ExtractionError when PyMuPDF is unavailable or the document cannot
  be opened. Individual page OCR failures are reported in extraction details.
"""

from __future__ import annotations

from typing import Dict, Any, Optional
import os
import io
import tempfile

from .errors import ExtractionError

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None  # graceful fallback

# Import image_extractor for OCR capability
try:
    from .image_extractor import (
        extract_image,
        _extract_image_text,
    )  # package import (when run via discovery)
    from PIL import Image

    OCR_AVAILABLE = True
except ImportError:
    try:
        from image_extractor import extract_image, _extract_image_text  # standalone
        from PIL import Image

        OCR_AVAILABLE = True
    except Exception:
        OCR_AVAILABLE = False
except Exception:
    OCR_AVAILABLE = False


def _normalize_pdf_date(raw: Optional[str]) -> Optional[str]:
    """
    Normalize PDF date string (e.g., 'D:20240101123000-05' or '20240101...')
    into a human-friendly ISO string when possible.
    """
    if not raw:
        return None

    # Trim leading 'D:' if present
    s = raw.strip()
    if s.startswith("D:"):
        s = s[2:]

    # The string can be variable length; try several supported layouts.
    # Minimal 'YYYY' present?
    try:
        year = s[0:4]
        month = s[4:6] if len(s) >= 6 else "01"
        day = s[6:8] if len(s) >= 8 else "01"
        hour = s[8:10] if len(s) >= 10 else "00"
        minute = s[10:12] if len(s) >= 12 else "00"
        second = s[12:14] if len(s) >= 14 else "00"
        # Ignore the optional timezone offset; reports also retain the raw value.
        return f"{year}-{month}-{day}T{hour}:{minute}:{second}"
    except Exception:
        # As a last resort, return the raw string
        return raw


def _extract_pdf_text(doc) -> str:
    """Concatenate plain text from all pages (text layer only)."""
    chunks = []
    try:
        for page in doc:
            # 'text' gives good layout; switch to "text" (old get_text("text")) on newer PyMuPDF
            chunks.append(page.get_text("text"))
    except Exception:
        pass
    return "\n".join(chunks).strip()


def _extract_page_with_ocr(page, dpi: int = 300) -> str:
    """
    Render a PDF page as an image and extract text using OCR.

    Args:
        page: PyMuPDF page object
        dpi: Resolution for rendering (300 is good for OCR)

    Returns:
        Extracted text from OCR
    """
    if not OCR_AVAILABLE:
        raise ExtractionError("OCR unavailable/failed on all attempts")

    try:
        # Render page as image at specified DPI
        # zoom = dpi / 72 (72 is default PDF DPI)
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)

        # Hand the pixmap straight to PIL in-memory — no temp PNG round-trip.
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = _extract_image_text(img)

        return text

    except Exception as e:
        # Preserve the page failure in extraction details.
        print(f"[!] OCR error: {e}")
        raise ExtractionError("OCR unavailable/failed on all attempts") from e


def _combine_native_and_ocr_text(native_text: str, ocr_text: str) -> str:
    """Preserve both sources unless one is wholly contained in the other."""
    native_normalized = " ".join(native_text.split()).casefold()
    ocr_normalized = " ".join(ocr_text.split()).casefold()

    if not native_normalized:
        return ocr_text
    if not ocr_normalized:
        return native_text
    if native_normalized in ocr_normalized:
        return ocr_text
    if ocr_normalized in native_normalized:
        return native_text
    return f"{native_text}\n{ocr_text}"


def _extract_pdf_metadata(doc) -> Dict[str, Any]:
    """Return a small, normalized metadata dict."""
    meta: Dict[str, Any] = {}
    try:
        raw = doc.metadata or {}
        # Common keys exposed by fitz: title, author, subject, keywords,
        # creator, producer, creationDate, modDate, trapped
        mapping = {
            "title": raw.get("title"),
            "author": raw.get("author"),
            "subject": raw.get("subject"),
            "keywords": raw.get("keywords"),
            "creator": raw.get("creator"),
            "producer": raw.get("producer"),
            "created": _normalize_pdf_date(raw.get("creationDate")),
            "modified": _normalize_pdf_date(raw.get("modDate")),
        }
        for k, v in mapping.items():
            if v not in (None, "", "None"):
                meta[k] = v
    except Exception:
        pass
    return meta


def extract_pdf(
    path: str,
    metadata_only: bool = False,
    min_text_threshold: int = 50,
    return_details: bool = False,
):
    """
    Public API used by discovery.py

    HYBRID APPROACH:
    1. Try text extraction from PDF text layer (fast)
    2. For pages with < min_text_threshold chars, apply OCR
    3. Combine results from both methods

    Args:
        path: File path to PDF
        metadata_only: If True, returns only metadata dict
        min_text_threshold: Minimum chars per page to consider "has text layer"
                           (default 50 - pages with less are assumed scanned)

    Returns:
        - metadata_only=True: Dict[str, Any] with normalized metadata
        - metadata_only=False: str with concatenated text (text layer + OCR)

    HARDENED VERSION - Handles:
    - None input
    - Non-string types (int, bool, list, dict, etc.)
    - Empty strings
    - Invalid file paths
    - PDFs with mixed text/scanned pages
    - Missing OCR library (falls back to text-only)
    """
    # FIX 1: Validate input type
    if not isinstance(path, str):
        raise ExtractionError(f"TypeError: path must be str, got {type(path).__name__}")

    # FIX 2: Validate non-empty path
    if not path or not path.strip():
        raise ExtractionError("ValueError: path cannot be empty")

    # FIX 3: Check library is installed
    if fitz is None:
        raise ExtractionError("RuntimeError: PyMuPDF is not installed")

    # FIX 4: Verify file exists before opening
    if not os.path.isfile(path):
        raise ExtractionError("FileNotFoundError: file does not exist")

    try:
        with fitz.open(path) as doc:
            # Metadata mode
            if metadata_only:
                meta = _extract_pdf_metadata(doc)
                # Add page count
                meta["page_count"] = len(doc)
                return meta

            # HYBRID EXTRACTION: Text layer + OCR for scanned pages
            combined_text = []
            pages_with_ocr = 0
            pages_failed = 0
            native_text_recovered = False
            total_pages = len(doc)

            for page_num, page in enumerate(doc):
                # Try text extraction first (fast)
                page_text = page.get_text("text").strip()
                if page_text:
                    native_text_recovered = True

                # If page has little/no text, it's likely scanned
                if len(page_text) < min_text_threshold:
                    # Apply OCR to this page
                    print(
                        f"  [OCR] Page {page_num + 1}/{total_pages} of {os.path.basename(path)}"
                    )
                    pages_with_ocr += 1
                    try:
                        ocr_text = _extract_page_with_ocr(page)
                        combined_text.append(
                            _combine_native_and_ocr_text(page_text, ocr_text)
                        )
                    except ExtractionError:
                        pages_failed += 1
                        combined_text.append(page_text)
                else:
                    # Page has text layer, use it (faster and more accurate)
                    combined_text.append(page_text)

            # Log OCR usage
            if pages_with_ocr > 0:
                print(f"  [i] Applied OCR to {pages_with_ocr}/{total_pages} pages")

            if (
                pages_with_ocr > 0
                and pages_failed == pages_with_ocr
                and not native_text_recovered
            ):
                raise ExtractionError("OCR unavailable/failed on all attempts")

            text = "\n\n".join(combined_text)
            if return_details:
                return text, {
                    "ocr_attempted": pages_with_ocr > 0,
                    "pages_ocr_attempted": pages_with_ocr,
                    "pages_failed": pages_failed,
                }
            return text

    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError.from_exception(exc) from exc


# Backwards compatibility: keep old function name
def extract_pdf_with_ocr_fallback(path: str, metadata_only: bool = False):
    """
    Alias for extract_pdf() for backwards compatibility.
    New code should use extract_pdf() directly.
    """
    return extract_pdf(path, metadata_only)


# -------------------------------------------------------------------
# SELF-TEST
# -------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    print("=== PDF Extractor with OCR Test ===\n")

    # Check if OCR is available
    if OCR_AVAILABLE:
        print("✓ OCR support available (image_extractor found)")
    else:
        print("⚠ OCR support unavailable (image_extractor not found)")
        print("  PDFs with text layers will still work\n")

    # Test with a file if provided
    if len(sys.argv) > 1:
        test_file = sys.argv[1]
        if os.path.exists(test_file):
            print(f"Testing: {test_file}\n")

            # Extract metadata
            meta = extract_pdf(test_file, metadata_only=True)
            print("Metadata:")
            for k, v in meta.items():
                print(f"  {k}: {v}")

            # Extract text
            print("\nExtracting text...")
            text = extract_pdf(test_file)
            print(f"\nExtracted {len(text)} characters")
            print("\nFirst 500 chars:")
            print(text[:500])
        else:
            print(f"File not found: {test_file}")
    else:
        print("Usage: python3 pdf_extractor.py <path_to_pdf>")
        print("\nExample:")
        print("  python3 pdf_extractor.py tests/sample_data/example_pdf.pdf")
