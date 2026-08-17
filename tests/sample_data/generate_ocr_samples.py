#!/usr/bin/env python3
"""
Regenerates the OCR-dependent sample files in tests/sample_data/:
  - test_pii_image.png    (plain image, OCR'd by image_extractor.py)
  - scanned_document.pdf  (image-only PDF page, OCR'd by pdf_extractor.py)

Both are rendered large/clear on purpose (big DejaVu Sans font, white
background, ~200 DPI for the PDF) so PaddleOCR has clear input — these are
detector tests, not OCR-robustness tests. scanned_document_v2.pdf is left
alone; it already OCRs cleanly.

Re-run to regenerate:
    docker compose run --rm securescan-cpu python tests/sample_data/generate_ocr_samples.py
"""

import os

from PIL import Image, ImageDraw, ImageFont

SAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Known-good values, verified against the project's own validators
# (detectors.detectors.luhn_check / detectors.keyword_detector.validate_sin)
# rather than trusted from memory — a SIN must pass Luhn AND not start with
# 0 or 8.
IMAGE_SIN = "132-677-360"
PDF_SIN = "500-978-820"


def render_lines(lines, out_path, size, font_size, line_spacing=1.6):
    img = Image.new("RGB", size, color="white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_REGULAR, font_size)

    y = font_size
    for line in lines:
        draw.text((60, y), line, fill="black", font=font)
        y += int(font_size * line_spacing)

    img.save(out_path)
    return img


def make_test_pii_image():
    lines = [
        f"SIN: {IMAGE_SIN}",
        "Email: john.doe@example.com",
        "Phone: 403-555-1234",
        "Prepared by John Smith, Acme Corporation",
    ]
    out_path = os.path.join(SAMPLE_DIR, "test_pii_image.png")
    render_lines(lines, out_path, size=(1000, 320), font_size=32)
    print(f"[✓] Wrote {out_path}")


def make_scanned_document_pdf():
    import fitz  # PyMuPDF

    lines = [
        "CONFIDENTIAL - INTERNAL RECORD",
        "",
        "Employee: Jane Doe",
        f"SIN: {PDF_SIN}",
        "Email: jane.doe@example.org",
        "Phone: 604-555-7788",
    ]

    # Letter page at ~200 DPI: 8.5x11in -> 1700x2200px.
    render_path = os.path.join(SAMPLE_DIR, "_scanned_document_render.png")
    img = render_lines(lines, render_path, size=(1700, 2200), font_size=48, line_spacing=1.8)
    os.remove(render_path)

    # Save as JPEG (quality 85) before embedding — inserting the raw PNG
    # bitmap directly bloated the PDF past MAX_FILE_SIZE_MB (10MB), causing
    # discovery.py to skip the file outright. A real scanned-document PDF is
    # a compressed JPEG page, not a raw bitmap; match that.
    jpeg_path = os.path.join(SAMPLE_DIR, "_scanned_document_render.jpg")
    img.convert("RGB").save(jpeg_path, "JPEG", quality=85)

    # Embed the rendered page as an image on a Letter-sized PDF page (612x792pt)
    # with NO text layer, so pdf_extractor.py's OCR fallback actually engages —
    # this is what makes it a "scanned document" test rather than a text PDF.
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(fitz.Rect(0, 0, 612, 792), filename=jpeg_path)

    out_path = os.path.join(SAMPLE_DIR, "scanned_document.pdf")
    doc.save(out_path)
    doc.close()
    os.remove(jpeg_path)
    print(f"[✓] Wrote {out_path} ({os.path.getsize(out_path) / 1024:.0f} KB)")


if __name__ == "__main__":
    make_test_pii_image()
    make_scanned_document_pdf()
