#!/usr/bin/env python3
"""
.eml extractor (RFC 822 / MIME email messages)

Responsibilities
---------------
1) Full-text extraction for .eml files: labeled From/To/Cc/Subject/Date
   headers, the plain-text body, the HTML body (tags stripped), and
   attachment FILENAMES listed as text (e.g. "Attachment: invoice.pdf").
2) Lightweight metadata extraction when `metadata_only=True`.

Design notes
------------
- stdlib only (email package): `email.policy.default` decodes RFC 2047
  encoded-word headers, RFC 2231 encoded filenames, and per-part
  Content-Transfer-Encoding (base64/quoted-printable) and charset
  automatically. No new dependency.
- Attachment CONTENT recursion (e.g. scanning inside a .docx/.pdf
  attachment) is out of scope for v2.2 — it needs a zip-bomb/size policy
  first — so only the filename is surfaced, as one labeled text line per
  attachment.
- A part that cannot be decoded (unknown charset, etc.) is recorded in
  `eml_parts_failed`/`eml_part_failure_reasons` rather than silently
  dropped or aborting the whole message — mirrors pdf_extractor.py's
  per-page OCR-failure contract. The message as a whole only raises
  ExtractionError when the file itself cannot be read/parsed at all.
- The binary-content guard (text_extractor.looks_like_binary) does not
  apply here: discovery.py only runs it for TEXT_EXTENSIONS, and the text
  this module returns is already decoded, not raw file bytes.
"""

from __future__ import annotations

import os
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html.parser import HTMLParser
from typing import Any, Dict, List, Tuple

from .errors import ExtractionError

# (header name, display label) — order controls the order labeled lines
# appear in the extracted text.
_HEADER_FIELDS = (
    ("From", "From"),
    ("To", "To"),
    ("Cc", "Cc"),
    ("Subject", "Subject"),
    ("Date", "Date"),
)


class _HTMLTextExtractor(HTMLParser):
    """Strips tags and keeps visible text; <script>/<style> bodies are dropped."""

    _SKIPPED_TAGS = ("script", "style")

    def __init__(self) -> None:
        super().__init__()
        self._chunks: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIPPED_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self._chunks.append(data)

    def get_text(self) -> str:
        return " ".join(self._chunks)


def _strip_html(html: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Malformed HTML: keep whatever text was recovered before the parser
        # gave up rather than losing the part entirely.
        pass
    return parser.get_text()


def _safe_header(msg: EmailMessage, name: str) -> "str | None":
    """A malformed individual header must never abort the whole extraction."""
    try:
        value = msg.get(name)
    except Exception:
        return None
    return str(value) if value is not None else None


def _extract_header_lines(msg: EmailMessage) -> List[str]:
    lines = []
    for header_name, label in _HEADER_FIELDS:
        value = _safe_header(msg, header_name)
        if value:
            lines.append(f"{label}: {value}")
    return lines


def _extract_header_metadata(msg: EmailMessage) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    for header_name, label in _HEADER_FIELDS:
        value = _safe_header(msg, header_name)
        if value:
            meta[label.lower()] = value
    return meta


def _iter_leaf_parts(msg: EmailMessage):
    for part in msg.walk():
        if not part.is_multipart():
            yield part


def _part_filename(part) -> "str | None":
    try:
        return part.get_filename()
    except Exception:
        return None


def _part_disposition(part) -> "str | None":
    try:
        return part.get_content_disposition()
    except Exception:
        return None


def _extract_body_and_attachments(
    msg: EmailMessage,
) -> Tuple[List[str], List[str], int, List[str]]:
    """Returns (body_text_chunks, attachment_filenames, parts_failed, reasons)."""
    body_chunks: List[str] = []
    attachments: List[str] = []
    parts_failed = 0
    failure_reasons: List[str] = []

    for part in _iter_leaf_parts(msg):
        content_type = part.get_content_type()
        disposition = _part_disposition(part)

        if content_type in ("text/plain", "text/html") and disposition != "attachment":
            try:
                content = part.get_content()
            except Exception as exc:
                parts_failed += 1
                failure_reasons.append(
                    f"{content_type}: {type(exc).__name__}: {exc}"
                )
                continue
            if not isinstance(content, str):
                content = str(content)
            if content_type == "text/html":
                content = _strip_html(content)
            content = content.strip()
            if content:
                body_chunks.append(content)
            continue

        filename = _part_filename(part)
        if filename:
            attachments.append(filename)

    return body_chunks, attachments, parts_failed, failure_reasons


def _parse_eml(path: str) -> EmailMessage:
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except Exception as exc:
        raise ExtractionError.from_exception(exc) from exc

    try:
        return BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as exc:
        raise ExtractionError.from_exception(exc) from exc


def extract_eml(path: str, metadata_only: bool = False, return_details: bool = False):
    """
    Public API used by discovery.py.

    - metadata_only=True  -> Dict[str, Any] of normalized header metadata
      (from/to/cc/subject/date, when present) plus attachment_count.
    - metadata_only=False -> str: labeled headers, then plain-text body,
      then tag-stripped HTML body, then one "Attachment: <filename>" line
      per attachment.
    - return_details=True (text mode only) additionally returns a dict with
      eml_parts_failed / eml_part_failure_reasons / eml_attachment_count,
      present only when nonzero — mirrors extract_pdf()'s return_details
      contract.

    HARDENED: validates input type/existence like the other extractors;
    raises ExtractionError only when the file itself can't be read or
    isn't parseable as an email at all. Individual undecodable parts are
    degraded, not fatal — see module docstring.
    """
    if not isinstance(path, str):
        raise ExtractionError(f"TypeError: path must be str, got {type(path).__name__}")
    if not path or not path.strip():
        raise ExtractionError("ValueError: path cannot be empty")
    if not os.path.isfile(path):
        raise ExtractionError("FileNotFoundError: file does not exist")

    msg = _parse_eml(path)

    if metadata_only:
        meta = _extract_header_metadata(msg)
        attachment_count = sum(
            1 for part in _iter_leaf_parts(msg) if _part_filename(part)
        )
        if attachment_count:
            meta["attachment_count"] = attachment_count
        return meta

    header_lines = _extract_header_lines(msg)
    body_chunks, attachments, parts_failed, failure_reasons = (
        _extract_body_and_attachments(msg)
    )

    lines = list(header_lines)
    lines.extend(body_chunks)
    lines.extend(f"Attachment: {name}" for name in attachments)
    text = "\n".join(lines)

    if not return_details:
        return text

    details: Dict[str, Any] = {}
    if parts_failed:
        details["eml_parts_failed"] = parts_failed
        details["eml_part_failure_reasons"] = failure_reasons
    if attachments:
        details["eml_attachment_count"] = len(attachments)
    return text, details
