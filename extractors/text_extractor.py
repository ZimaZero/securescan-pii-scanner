# extractors/text_extractor.py
"""
Plain-text and structured-text extractor.

Handles:
- .txt (plain text)
- .csv (reads as text)
- .json (reads as text)
- .py, .md, .log and other text-like files

Design:
- Keep it simple and robust: open with utf-8 and ignore errors to avoid crashes on binary content.
- Return a single string with extracted text for detectors to scan.
"""

from typing import Optional
import os

from .errors import ExtractionError

TEXT_EXTENSIONS = {".txt", ".csv", ".json", ".py", ".md", ".log"}

# How much of a file to sample when guarding against binary content masquerading
# as text (e.g. a multi-megabyte NUL-filled file, which previously cost ~4
# minutes of replacement-char fuzzy-matching downstream). 8KB is enough to
# reliably distinguish text from binary without reading large files in full.
BINARY_SNIFF_BYTES = 8192
BINARY_SNIFF_THRESHOLD = 0.30

# Bytes considered "textual" for the sniff: printable ASCII, common whitespace,
# and byte ranges that occur in valid UTF-8 continuation/lead bytes (also
# covers Latin-1/ANSI text, whose high-bit single-byte accented characters
# fall in the same ranges).
_TEXTUAL_BYTES = frozenset(b"\t\n\r\x0b\x0c") | frozenset(range(0x20, 0x7F))


def looks_like_binary(filepath: str, sniff_bytes: int = BINARY_SNIFF_BYTES) -> bool:
    """Sniff the first `sniff_bytes` raw bytes of `filepath` for binary content.

    Returns True if the sample contains a NUL byte, or if more than
    BINARY_SNIFF_THRESHOLD of sampled bytes fall outside printable ASCII +
    common whitespace + valid UTF-8 continuation/lead ranges. Never raises —
    an unreadable file is treated as "not binary" so the normal extraction
    path (and its own error handling) still runs.
    """
    try:
        with open(filepath, "rb") as fh:
            sample = fh.read(sniff_bytes)
    except OSError:
        return False
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    bad = 0
    for byte in sample:
        if byte in _TEXTUAL_BYTES:
            continue
        if 0x80 <= byte <= 0xBF or 0xC2 <= byte <= 0xF4:
            continue  # UTF-8 continuation byte or lead byte
        bad += 1
    return (bad / len(sample)) > BINARY_SNIFF_THRESHOLD


def extract_text(filepath: str) -> str:
    """
    Read file at `filepath` as text and return string.
    On any error, return empty string (fail-safe).

    HARDENED VERSION - Now handles:
    - None input
    - Non-string types (int, bool, list, dict)
    - Empty strings
    - Invalid file paths
    """
    # FIX 1: Validate input type
    if not isinstance(filepath, str):
        raise ExtractionError(f"TypeError: filepath must be str, got {type(filepath).__name__}")

    # FIX 2: Validate non-empty string
    if not filepath or not filepath.strip():
        raise ExtractionError("ValueError: filepath cannot be empty")

    # Original logic continues
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in TEXT_EXTENSIONS:
        raise ExtractionError(f"ValueError: unsupported extension {ext}")

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except Exception as exc:
        raise ExtractionError.from_exception(exc) from exc


# quick local test
if __name__ == "__main__":
    print(extract_text("tests/sample_data/example.txt"))
