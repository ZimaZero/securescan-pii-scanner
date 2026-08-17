#!/usr/bin/env python3
"""Regression coverage for the v2.2 .eml extractor.

Covers: plain-text body, multipart/alternative with an HTML body (tags
stripped), a base64-encoded body, attachment filenames listed as text, a
malformed header line (extraction must degrade gracefully, never crash),
an undecodable part (unknown charset — reported via return_details, not
silently dropped), and end-to-end detection of PII planted in the Subject
header and inside the HTML body via discovery.scan_path().

No real emails are used anywhere in this file — every fixture is
synthetic, built inline with the stdlib email package.
"""

import json
import os
import sys
import tempfile
from email.message import EmailMessage as StdlibEmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import discovery
from extractors.eml_extractor import extract_eml

# Reused from tests/make_format_data.py's fixed, pre-validated PII set.
SIN = "132-677-360"
CREDIT_CARD = "4111 1111 1111 1111"
PHONE = "416-555-0199"


def _write(path: Path, msg: StdlibEmailMessage) -> None:
    path.write_bytes(msg.as_bytes())


def _plain_email() -> StdlibEmailMessage:
    msg = StdlibEmailMessage()
    msg["From"] = "Alice Example <alice@example.org>"
    msg["To"] = "bob@example.org"
    msg["Cc"] = "carol@example.org"
    msg["Subject"] = "Quarterly update"
    msg["Date"] = "Mon, 01 Jun 2026 12:00:00 -0000"
    msg.set_content("This is a plain-text status update with no attachments.")
    return msg


def _multipart_html_email() -> StdlibEmailMessage:
    msg = StdlibEmailMessage()
    msg["From"] = "Dana Example <dana@example.org>"
    msg["To"] = "erin@example.org"
    msg["Subject"] = "Formatted notice"
    msg["Date"] = "Tue, 02 Jun 2026 09:30:00 -0000"
    msg.set_content("Plain fallback: please view in an HTML-capable client.")
    msg.add_alternative(
        "<html><body><p>Hello <b>Erin</b>,</p>"
        "<p>See the <a href='#'>attached</a> notice.</p>"
        "<script>trackOpen();</script></body></html>",
        subtype="html",
    )
    return msg


def _base64_body_email() -> StdlibEmailMessage:
    msg = StdlibEmailMessage()
    msg["From"] = "sender@example.org"
    msg["To"] = "receiver@example.org"
    msg["Subject"] = "Base64 body"
    msg.set_content(f"Confirmation code on file: {SIN}")
    raw_text = msg.get_content()
    # Force base64 transfer-encoding for the body (set_content defaults to
    # quoted-printable/8bit for plain ASCII-ish text).
    import base64

    msg.set_payload(base64.b64encode(raw_text.encode("utf-8")).decode("ascii"))
    msg.replace_header("Content-Transfer-Encoding", "base64")
    return msg


def _attachment_email() -> StdlibEmailMessage:
    msg = StdlibEmailMessage()
    msg["From"] = "sender@example.org"
    msg["To"] = "receiver@example.org"
    msg["Subject"] = "Documents attached"
    msg.set_content("Please find the requested documents attached.")
    msg.add_attachment(
        b"%PDF-1.4 fake statement body",
        maintype="application",
        subtype="pdf",
        filename="statement.pdf",
    )
    msg.add_attachment(
        b"fake image bytes",
        maintype="image",
        subtype="png",
        filename="logo.png",
    )
    return msg


def _malformed_header_bytes() -> bytes:
    # A stray header-shaped line with no colon breaks RFC-822 header parsing
    # partway through (a MissingHeaderBodySeparatorDefect): everything from
    # that line onward — including the intended Subject line — becomes part
    # of the body instead of being parsed as headers. Extraction must still
    # succeed rather than raising.
    return (
        b"From: weird@example.org\r\n"
        b"ThisLineHasNoColonAndBreaksHeaderParsing\r\n"
        b"Subject: this never becomes a real header\r\n"
        b"\r\n"
        b"Body text after the broken header block.\r\n"
    )


def _unknown_charset_bytes() -> bytes:
    return (
        b"From: a@example.org\r\n"
        b"To: b@example.org\r\n"
        b'Subject: Bad charset part\r\n'
        b'Content-Type: text/plain; charset="totally-bogus-charset"\r\n'
        b"Content-Transfer-Encoding: 8bit\r\n"
        b"\r\n"
        b"Hello world\r\n"
    )


def _end_to_end_email() -> StdlibEmailMessage:
    msg = StdlibEmailMessage()
    msg["From"] = "format.test@example.com"
    msg["To"] = "recipient@example.org"
    msg["Subject"] = f"Callback requested re: SIN {SIN}"
    msg["Date"] = "Wed, 03 Jun 2026 15:00:00 -0000"
    msg.set_content(f"Please call back regarding phone {PHONE}.")
    msg.add_alternative(
        f"<html><body><p>Card on file: {CREDIT_CARD}</p></body></html>",
        subtype="html",
    )
    return msg


def main() -> int:
    rows = []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # --- Case 1: plain-text body + labeled headers -----------------
        plain_path = root / "plain.eml"
        _write(plain_path, _plain_email())
        text = extract_eml(str(plain_path))
        rows.append((
            "plain body + labeled headers",
            "From: Alice Example <alice@example.org>" in text
            and "To: bob@example.org" in text
            and "Cc: carol@example.org" in text
            and "Subject: Quarterly update" in text
            and "This is a plain-text status update" in text,
            repr(text[:120]),
        ))

        # --- Case 2: multipart + HTML body, tags stripped ----------------
        html_path = root / "multipart_html.eml"
        _write(html_path, _multipart_html_email())
        text = extract_eml(str(html_path))
        rows.append((
            "multipart/alternative HTML body: tags stripped, script dropped",
            "Hello" in text
            and "Erin" in text
            and "attached" in text
            and "notice" in text
            and "<p>" not in text
            and "<html>" not in text
            and "trackOpen" not in text
            and "Plain fallback" in text,
            repr(text),
        ))

        # --- Case 3: base64-encoded body decodes correctly ----------------
        b64_path = root / "base64_body.eml"
        _write(b64_path, _base64_body_email())
        text = extract_eml(str(b64_path))
        rows.append((
            "base64 body decodes to plain text",
            SIN in text,
            repr(text),
        ))

        # --- Case 4: attachment filenames listed, content not recursed --
        att_path = root / "attachments.eml"
        _write(att_path, _attachment_email())
        text = extract_eml(str(att_path))
        rows.append((
            "attachment filenames listed as labeled text",
            "Attachment: statement.pdf" in text
            and "Attachment: logo.png" in text
            and "%PDF" not in text
            and "fake image bytes" not in text,
            repr(text),
        ))

        # --- Case 5: malformed header degrades gracefully, never crashes -
        malformed_path = root / "malformed_header.eml"
        malformed_path.write_bytes(_malformed_header_bytes())
        try:
            text = extract_eml(str(malformed_path))
            crashed = False
        except Exception as exc:  # pragma: no cover - failure path only
            text = ""
            crashed = True
            exc_detail = f"{type(exc).__name__}: {exc}"
        rows.append((
            "malformed header does not crash extraction",
            not crashed and "Body text after the broken header block" in text,
            "no exception" if not crashed else exc_detail,
        ))

        # --- Case 6: undecodable part reported via return_details, ---------
        #     never silently dropped
        bad_charset_path = root / "bad_charset.eml"
        bad_charset_path.write_bytes(_unknown_charset_bytes())
        text, details = extract_eml(str(bad_charset_path), return_details=True)
        rows.append((
            "undecodable part surfaced via eml_parts_failed, not silently dropped",
            details.get("eml_parts_failed") == 1
            and bool(details.get("eml_part_failure_reasons"))
            and "Hello world" not in text,
            json.dumps(details),
        ))

        # --- Case 7: metadata_only returns labeled header dict ------------
        meta = extract_eml(str(plain_path), metadata_only=True)
        rows.append((
            "metadata_only returns header dict",
            meta.get("subject") == "Quarterly update"
            and meta.get("from") == "Alice Example <alice@example.org>",
            json.dumps(meta),
        ))
        att_meta = extract_eml(str(att_path), metadata_only=True)
        rows.append((
            "metadata_only reports attachment_count",
            att_meta.get("attachment_count") == 2,
            json.dumps(att_meta),
        ))

        # --- Case 8: end-to-end via discovery.scan_path() -----------------
        e2e_dir = root / "e2e"
        e2e_dir.mkdir()
        _write(e2e_dir / "notice.eml", _end_to_end_email())
        html_report = discovery.scan_path(str(e2e_dir), verify=False, run_ner=False)
        json_report_path = os.path.splitext(html_report)[0] + ".json"
        with open(json_report_path, "r", encoding="utf-8") as fh:
            report = json.load(fh)
        file_entry = report["files"][0]
        matches = file_entry["matches"]
        found_sin = any(
            d.get("risk_level") == "HIGH"
            for d in matches.get("identifier.financial.sin", [])
        )
        found_card = any(
            d.get("value") == CREDIT_CARD and d.get("risk_level") == "MEDIUM"
            for d in matches.get(
                "identifier.financial_unverified.credit_card", []
            )
        )
        rows.append((
            "scan_path detects SIN planted in Subject header",
            found_sin,
            json.dumps(matches.get("identifier.financial.sin", [])),
        ))
        rows.append((
            "scan_path detects credit card planted in HTML body",
            found_card,
            json.dumps(
                matches.get("identifier.financial_unverified.credit_card", [])
            ),
        ))
        rows.append((
            "scan_path scan_status is 'scanned' (not extraction_failed)",
            file_entry.get("scan_status") == "scanned",
            file_entry.get("scan_status"),
        ))

    print(f"{'CASE':<68} {'RESULT':<7} DETAIL")
    print("-" * 110)
    for name, passed, detail in rows:
        print(f"{name:<68} {'PASS' if passed else 'FAIL':<7} {detail}")
    passed_count = sum(passed for _, passed, _ in rows)
    print("-" * 110)
    print(f"SUMMARY: {passed_count}/{len(rows)} passed")
    return 0 if passed_count == len(rows) else 1


def test_eml_extractor():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
