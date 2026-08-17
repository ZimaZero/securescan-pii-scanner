#!/usr/bin/env python3
"""
Generate the synthetic Canadian identifier evaluation corpus and both manifests.

The corpus is ground-truth-first. ``verdict`` says whether a correct scanner
should produce an ``identifier.*`` finding; ``status`` separately records the
current detector's agreement with that truth. A NEGATIVE asserts zero
``identifier.*`` findings, not zero findings overall: GLiNER may legitimately
produce ``entity.*`` or ``contact.*`` findings from surrounding prose.

UCI and status-card registration numbers are context-gated, format-only
identifiers. They intentionally have no ``_unverified`` tier: without a public
checksum there is no "validated but missing context" state. Context absent means
no finding. Their provisional detector metadata is confidence 0.60 and source
priority 2; both carry HIGH risk because risk describes impact if real.

All values, names, dates, and prose are synthetic and generated. No source
corpus or real-person record is read. Regeneration overwrites the two generated
directories and is byte-identical for the fixed seed.

Regenerate with:
    docker compose run --rm securescan-cpu python tests/make_canadian_eval_data.py
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import random
import shutil
import string
import sys
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors.health_card_detector import bc_phn_valid, ohip_valid  # noqa: E402
from detectors.keyword_detector import validate_sin  # noqa: E402


SEED = 20260724
DATA_DIR = PROJECT_ROOT / "tests" / "canadian_eval_data"
DOCS_DIR = PROJECT_ROOT / "tests" / "canadian_eval_docs"

# Volume controls. Standard per-jurisdiction cases are generated from these
# mappings; display forms, audit discrepancies, and robustness probes are
# additional explicit cases because their semantics differ.
SIN_VALID_COUNT = 3
SIN_INVALID_COUNT = 2
HEALTH_COMPACT_COUNTS = {
    "on": 2,  # context Tier 1 and context-free Tier 2
    "bc": 2,  # context Tier 1 and context-free Tier 2
    "ab": 1,
    "sk": 1,
    "mb": 1,
    "nb": 1,
    "ns": 1,
    "pe": 1,
    "nt": 1,
    "nl": 1,
    "nu": 1,
    "yk": 1,
    "qc": 1,
}
LICENCE_COMPACT_COUNTS = {
    "on": 2,  # compact and printed
    "qc": 1,
    "bc": 1,  # legacy seven-digit form
    "ab": 1,
    "sk": 1,
    "mb": 1,
    "ns": 1,
    "nb": 1,
    "nl": 1,
    "pe": 1,
    "nt": 1,
    "nu": 1,
    "yk": 1,
}
OCR_NON_MRZ_COUNTS = {
    "sin": 2,
    "health_card_on": 1,
    "health_card_bc": 1,
    "health_card_ab": 1,
    "drivers_license_on": 1,
    "passport_ca": 1,
}
NEGATIVE_ADJACENT_COUNT = 5
NEGATIVE_CONTEXTUAL_COUNT = 3

VERDICTS = {"POSITIVE", "NEGATIVE"}
STATUSES = {
    "OK",
    "GAP-MISS",
    "GAP-TIER",
    "GAP-PARTIAL",
    "GAP-FALSE-POSITIVE",
    "UNVERIFIED",
}

UNVERIFIED_LICENCE_NOTE = (
    "No issuing-authority source establishes this implemented format. "
    "A disagreement on this file is not evidence of a detector bug, and no "
    "detector change may be justified from it."
)


@dataclass(frozen=True)
class ExpectedFinding:
    taxonomy_category: str
    trust_tier: str
    count: int = 1
    confidence: float | None = None
    source_priority: int | None = None


@dataclass(frozen=True)
class Case:
    filename: str
    group: str
    verdict: str
    status: str
    expected: str
    expected_findings: tuple[ExpectedFinding, ...]
    content: str
    audit_reference: str | None = None
    note: str | None = None
    format_authority: str | None = None


GROUP_DESCRIPTIONS = {
    "SIN": "Valid and invalid SINs prove the ratified Luhn and first-digit rules.",
    "Health cards": (
        "Provincial compact/display forms cover every jurisdiction and every "
        "trust tier supported by its real format."
    ),
    "Driver's licences": (
        "Ten provincial formats are sourced to Microsoft Purview and corroborated "
        "against photographed specimens. Territory cases explicitly identify their "
        "weaker specimen-derived authority."
    ),
    "Passports": "Canadian passport shapes require context; compact and displayed forms are separate.",
    "MRZ exact": "Exact ICAO TD1 and TD3 blocks assert field-specific checksums and tiers.",
    "MRZ invalid": (
        "Corrupted document check digits retain an unverified document number "
        "while independently valid DOB and expiry fields keep their own tiers."
    ),
    "MRZ robustness": (
        "Text-level OCR recovery is separate from exact ICAO conformance and "
        "covers common character confusions within the detector's tolerance."
    ),
    "Ordinary identifier OCR": (
        "Text-level OCR corruption on ordinary identifiers measures whether "
        "non-MRZ detectors recover common photographed-card confusions."
    ),
    "UCI": "All four publicly sourced UCI compact/display forms require nearby IRCC context.",
    "Status registration": (
        "Ten-digit status-card registration numbers are context-gated and have "
        "no public checksum."
    ),
    "Context-free negatives": (
        "Bare format-shaped values without checksum or nearby context provide "
        "no identifier evidence."
    ),
    "Adjacent negatives": (
        "Business identifiers and phone numbers near ordinary filler must not "
        "be promoted into government identifiers."
    ),
    "Contextual negatives": (
        "Abstract discussion of identity documents contains no identifier value."
    ),
    "Scope boundary": (
        "A generic foreign passport is real PII and is documented here without "
        "claiming it is a Canadian issuing format."
    ),
}


def finding(
    category: str,
    tier: str,
    count: int = 1,
    *,
    confidence: float | None = None,
    source_priority: int | None = None,
) -> ExpectedFinding:
    return ExpectedFinding(category, tier, count, confidence, source_priority)


def _digits(rng: random.Random, length: int, *, nonzero_first: bool = True) -> str:
    first = rng.choice("123456789") if nonzero_first else rng.choice(string.digits)
    return first + "".join(rng.choice(string.digits) for _ in range(length - 1))


def _on_number(rng: random.Random) -> str:
    first_nine = _digits(rng, 9)
    total = 0
    for index, char in enumerate(first_nine):
        value = int(char)
        if index % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return first_nine + str((10 - total % 10) % 10)


def _bc_number(rng: random.Random) -> str:
    weights = (2, 4, 8, 5, 10, 9, 7, 3)
    while True:
        middle = "".join(rng.choice(string.digits) for _ in range(8))
        result = 11 - (sum(int(d) * w for d, w in zip(middle, weights)) % 11)
        if 0 <= result <= 9:
            return "9" + middle + str(result)


def _sin_number(rng: random.Random) -> str:
    while True:
        value = _digits(rng, 9)
        if value[0] != "8" and validate_sin(value):
            return value


def _icao_check_digit(value: str) -> str:
    values = {"<": 0, **{str(i): i for i in range(10)}}
    values.update({char: index + 10 for index, char in enumerate(string.ascii_uppercase)})
    weights = (7, 3, 1)
    return str(sum(values[char] * weights[i % 3] for i, char in enumerate(value)) % 10)


def _pad(value: str, length: int) -> str:
    return value.ljust(length, "<")[:length]


def _build_td3() -> tuple[str, str]:
    document = _pad("AC0123456", 9)
    dob = "900101"
    expiry = "300101"
    optional = "<" * 14
    line1 = "P<CAN" + _pad("SAMPLE<<SYNTHETIC", 39)
    line2 = (
        document
        + _icao_check_digit(document)
        + "CAN"
        + dob
        + _icao_check_digit(dob)
        + "F"
        + expiry
        + _icao_check_digit(expiry)
        + optional
        + "<"
    )
    composite = (
        document
        + _icao_check_digit(document)
        + dob
        + _icao_check_digit(dob)
        + expiry
        + _icao_check_digit(expiry)
        + optional
        + "<"
    )
    line2 += _icao_check_digit(composite)
    assert len(line1) == len(line2) == 44
    return line1, line2


def _build_td1() -> tuple[str, str, str]:
    document = _pad("AC12345", 9)
    dob = "850615"
    expiry = "290615"
    line1 = "ICCAN" + document + _icao_check_digit(document) + "<" * 15
    line2_prefix = (
        dob
        + _icao_check_digit(dob)
        + "M"
        + expiry
        + _icao_check_digit(expiry)
        + "CAN"
        + "<" * 11
    )
    composite = line1[5:30] + dob + _icao_check_digit(dob) + expiry + _icao_check_digit(expiry)
    line2 = line2_prefix + _icao_check_digit(composite)
    line3 = _pad("SYNTHETIC<<CASE", 30)
    assert len(line1) == len(line2) == len(line3) == 30
    return line1, line2, line3


def _replace(value: str, index: int, char: str) -> str:
    return value[:index] + char + value[index + 1 :]


def _mrz_expected(*, unverified_document: bool = False) -> tuple[ExpectedFinding, ...]:
    return (
        finding(
            "identifier.government_unverified.mrz"
            if unverified_document
            else "identifier.government.mrz_document_number",
            "MEDIUM" if unverified_document else "HIGH",
        ),
        finding("identifier.personal.mrz_dob", "MEDIUM"),
        finding("identifier.government_low.mrz_expiry", "LOW"),
    )


def build_cases() -> list[Case]:
    rng = random.Random(SEED)
    cases: list[Case] = []

    def add(
        filename: str,
        group: str,
        verdict: str,
        status: str,
        expected: str,
        content: str,
        expected_findings: Iterable[ExpectedFinding] = (),
        *,
        audit_reference: str | None = None,
        note: str | None = None,
        format_authority: str | None = None,
    ) -> None:
        cases.append(
            Case(
                filename,
                group,
                verdict,
                status,
                expected,
                tuple(expected_findings),
                content.rstrip() + "\n",
                audit_reference,
                note,
                format_authority,
            )
        )

    # SIN
    for index in range(1, SIN_VALID_COUNT + 1):
        value = _sin_number(rng)
        displayed = (
            value
            if index == 1
            else f"{value[:3]} {value[3:6]} {value[6:]}"
            if index == 2
            else f"{value[:3]}-{value[3:6]}-{value[6:]}"
        )
        add(
            f"sin_valid_{index:02d}.txt",
            "SIN",
            "POSITIVE",
            "OK",
            "1x SIN, checksum valid, HIGH",
            f"Synthetic payroll record\nSocial Insurance Number: {displayed}",
            [finding("identifier.financial.sin", "HIGH")],
            audit_reference="audit item 23, owner-ratified",
        )
    invalid_sins = ("123456789", "046454286")
    for index in range(1, SIN_INVALID_COUNT + 1):
        value = invalid_sins[(index - 1) % len(invalid_sins)]
        add(
            f"sin_invalid_luhn_{index:02d}.txt",
            "SIN",
            "NEGATIVE",
            "OK",
            "no identifier finding; SIN checksum invalid",
            f"Synthetic rejected payroll field\nSocial Insurance Number: {value}",
        )

    # Health-card compact forms.
    health_shapes = {
        "ab": ("Alberta", 9),
        "sk": ("Saskatchewan", 9),
        "mb": ("Manitoba", 9),
        "nb": ("New Brunswick", 9),
        "ns": ("Nova Scotia", 10),
        "pe": ("Prince Edward Island", 8),
        "nl": ("Newfoundland and Labrador", 12),
        "nu": ("Nunavut", 9),
        "yk": ("Yukon", 9),
    }
    for province, count in HEALTH_COMPACT_COUNTS.items():
        for index in range(1, count + 1):
            if province == "on":
                value = _on_number(rng)
                with_context = index % 2 == 1
                category = (
                    "identifier.government.health_card_on"
                    if with_context
                    else "identifier.government_unverified.health_card_on"
                )
                tier = "HIGH" if with_context else "MEDIUM"
                content = (
                    f"Synthetic Ontario file\nOHIP health card: {value}"
                    if with_context
                    else f"Synthetic reconciliation reference\nMember value {value}"
                )
            elif province == "bc":
                value = _bc_number(rng)
                with_context = index % 2 == 1
                category = (
                    "identifier.government.health_card_bc"
                    if with_context
                    else "identifier.government_unverified.health_card_bc"
                )
                tier = "HIGH" if with_context else "MEDIUM"
                content = (
                    f"Synthetic British Columbia file\nPHN health number: {value}"
                    if with_context
                    else f"Synthetic reconciliation reference\nMember value {value}"
                )
            elif province == "qc":
                value = "TEST" + _digits(rng, 8, nonzero_first=False)
                category = "identifier.government.health_card_qc"
                tier = "HIGH"
                content = f"Synthetic Quebec record\nRAMQ health card: {value}"
            elif province == "nt":
                value = rng.choice(string.ascii_uppercase) + _digits(rng, 7)
                category = "identifier.government.health_card_nt"
                tier = "HIGH"
                content = f"Synthetic Northwest Territories record\nHealth card: {value}"
            else:
                province_name, length = health_shapes[province]
                value = _digits(rng, length)
                category = f"identifier.government.health_card_{province}"
                tier = "HIGH"
                content = f"Synthetic {province_name} record\nHealth card number: {value}"

            status = "OK"
            add(
                f"{province}_healthcard_compact_{index:02d}.txt",
                "Health cards",
                "POSITIVE",
                status,
                f"1x {province.upper()} health card, compact, {tier}",
                content,
                [finding(category, tier)],
                audit_reference="audit item 4" if province == "nt" else None,
            )

    on_version_value = _on_number(rng)
    add(
        "on_healthcard_version_2letter_01.txt",
        "Health cards",
        "POSITIVE",
        "OK",
        "1x ON health card with two-letter version code, HIGH",
        f"Synthetic Ontario health-card export\nOHIP: {on_version_value} XY",
        [finding("identifier.government.health_card_on", "HIGH")],
    )
    add(
        "on_healthcard_version_1letter_01.txt",
        "Health cards",
        "POSITIVE",
        "OK",
        "1x complete ON health card with one-letter version code, HIGH",
        f"Synthetic Ontario health-card export\nOHIP: {on_version_value} X",
        [finding("identifier.government.health_card_on", "HIGH")],
        audit_reference="audit item 1",
        note="The detector captures the public one-letter version in the same finding span.",
    )
    add(
        "on_healthcard_leading_zero_01.txt",
        "Health cards",
        "NEGATIVE",
        "OK",
        "no identifier finding; Ontario does not issue a zero-leading number",
        "Synthetic Ontario validation rejection\nOHIP health card: 0000000000",
        audit_reference="audit item 2",
    )
    add(
        "bc_healthcard_mod11_result11_01.txt",
        "Health cards",
        "POSITIVE",
        "OK",
        "1x BC health card, MOD-11 result 11, MEDIUM unverified",
        "Synthetic British Columbia validation rejection\nPHN health number: 9000000000",
        [finding("identifier.government_unverified.health_card_bc", "MEDIUM")],
        audit_reference="audit item 3",
        note=(
            "Both BC checksum-invalid fixtures exercise MOD-11 result 11. "
            "This value ends in 0, which the old 11-to-0 mapping accepted; "
            "the named checksum-invalid fixture ends in 1 and fell through. "
            "Both deliberately expect BC unverified at MEDIUM as branch coverage."
        ),
    )

    invalid_health = {"on": "1234567890", "bc": "9123456781"}
    for province, value in invalid_health.items():
        category = f"identifier.government_unverified.health_card_{province}"
        label = "Ontario" if province == "on" else "British Columbia"
        add(
            f"{province}_healthcard_checksum_invalid_generic_01.txt",
            "Health cards",
            "POSITIVE",
            "OK",
            "1x generic Canadian health card, checksum not province-attributed, HIGH",
            f"Synthetic provincial-unknown record\nHealth card number: {value}",
            [finding("identifier.government.health_card_ca", "HIGH")],
            note=(
                "No province is named; a 10-digit value can legitimately be a "
                "Nova Scotia health number, so generic detection is defensible."
            ),
        )
        add(
            f"{province}_healthcard_checksum_invalid_named_01.txt",
            "Health cards",
            "POSITIVE",
            "OK",
            f"1x {province.upper()} health card, checksum failed, MEDIUM unverified",
            f"Synthetic {label} exception record\n{label} health card number: {value}",
            [finding(category, "MEDIUM")],
            audit_reference="audit section 1.1 correction",
            note=(
                (
                    "Both BC checksum-invalid fixtures exercise MOD-11 result "
                    "11; this value's printed check digit differs from the old "
                    "11-to-0 mapping while the dedicated result-11 fixture's "
                    "printed check digit matched it. Both deliberately expect "
                    "BC unverified at MEDIUM as branch coverage."
                )
                if province == "bc"
                else (
                    "The named province whose checksum failed remains visible "
                    "at the province-specific MEDIUM-unverified tier."
                )
            ),
        )

    ab_value = _digits(rng, 9)
    add(
        "ab_healthcard_display_01.txt",
        "Health cards",
        "POSITIVE",
        "OK",
        "1x AB health card, displayed 99999-9999 form, HIGH",
        f"Synthetic AB card transcription\nHealth card: {ab_value[:5]}-{ab_value[5:]}",
        [finding("identifier.government.health_card_ab", "HIGH")],
        audit_reference="audit item 6, owner-ratified display coverage",
    )
    qc_value = "DEMO" + _digits(rng, 8, nonzero_first=False)
    add(
        "qc_healthcard_display_01.txt",
        "Health cards",
        "POSITIVE",
        "OK",
        "1x QC health card, printed AAAA 0000 0000 form, HIGH",
        f"Synthetic Quebec card transcription\nRAMQ: {qc_value[:4]} {qc_value[4:8]} {qc_value[8:]}",
        [finding("identifier.government.health_card_qc", "HIGH")],
        audit_reference="audit item 5",
    )

    # Driver's licences.
    licence_unverified = {"nt", "nu", "yk"}
    for province, count in LICENCE_COMPACT_COUNTS.items():
        for index in range(1, count + 1):
            if province == "on":
                prefix = _digits(rng, 4)
                middle = _digits(rng, 5)
                source_tail = _digits(rng, 3)
                tail = (
                    source_tail[0]
                    + "0156"[int(source_tail[1]) % 4]
                    + source_tail[2]
                    + "23"
                )
                raw = f"A{prefix}{middle}{tail}"
                value = raw if index == 1 else f"{raw[:5]}-{raw[5:10]}-{raw[10:]}"
                province_name = "Ontario"
            elif province == "qc":
                value = "Q" + _digits(rng, 12)
                province_name = "Quebec"
            elif province == "bc":
                value = _digits(rng, 7)
                province_name = "British Columbia"
            elif province == "ab":
                # Eight digits stays inside the implemented AB 1-9 range while
                # avoiding an unrelated nine-digit SIN/health-card collision.
                value = _digits(rng, 8)
                province_name = "Alberta"
            elif province == "sk":
                value = _digits(rng, 8)
                province_name = "Saskatchewan"
            elif province == "mb":
                _digits(rng, 11)  # preserve fixed-seed payloads after this case
                value = "PUBLIJQ008NH"
                province_name = "Manitoba"
            elif province == "ns":
                _digits(rng, 9)  # preserve fixed-seed payloads after this case
                value = "PUBLI020220005"
                province_name = "Nova Scotia"
            elif province == "nb":
                value = _digits(rng, 6)
                province_name = "New Brunswick"
            elif province == "nl":
                value = "N" + _digits(rng, 9)
                province_name = "Newfoundland and Labrador"
            elif province == "pe":
                value = _digits(rng, 6)
                province_name = "Prince Edward Island"
            elif province == "nt":
                _digits(rng, 6)  # preserve fixed-seed payloads after this case
                value = "1234567890"
                province_name = "Northwest Territories"
            elif province == "nu":
                _digits(rng, 6)  # preserve fixed-seed payloads after this case
                value = "A1234 5678-004"
                province_name = "Nunavut"
            else:
                _digits(rng, 6)  # preserve fixed-seed payloads after this case
                value = "504896"
                province_name = "Yukon"

            status = "UNVERIFIED" if province in licence_unverified else "OK"
            code = "yt" if province == "yk" else province
            add(
                f"{province}_licence_compact_{index:02d}.txt",
                "Driver's licences",
                "POSITIVE",
                status,
                f"1x {province.upper()} driver's licence, HIGH"
                + (", no public grammar" if status == "UNVERIFIED" else ""),
                f"Synthetic {province_name} record\n{province_name} driver's licence: {value}",
                [finding(f"identifier.government.drivers_license_{code}", "HIGH")],
                audit_reference=(
                    {
                        "nt": "specimen field 4d; one photographed specimen",
                        "nu": "specimen field 5; one photographed specimen",
                        "yk": "two independent photographed specimens",
                    }[province]
                    if status == "UNVERIFIED"
                    else "Microsoft Purview Canada driver's licence definition"
                ),
                note=(
                    {
                        "nt": (
                            "SPECIMEN-DERIVED: 10 digits from one specimen. Single-specimen "
                            "evidence is weaker than corroboration and is not an established grammar."
                        ),
                        "nu": (
                            "SPECIMEN-DERIVED: one letter plus 4-4-3 digits from one specimen, "
                            "printed A1234 5678-004. Not an established grammar."
                        ),
                        "yk": (
                            "SPECIMEN-DERIVED and corroborated by two independent six-digit "
                            "specimens (504896 and 129804); still not an issuing-authority grammar."
                        ),
                    }[province]
                    if status == "UNVERIFIED"
                    else None
                ),
                format_authority=(
                    "specimen-derived-corroborated"
                    if province == "yk"
                    else "specimen-derived-single"
                    if province in {"nt", "nu"}
                    else "microsoft-purview"
                ),
            )

    add(
        "yk_licence_corroborating_02.txt",
        "Driver's licences",
        "POSITIVE",
        "UNVERIFIED",
        "1x YK six-digit driver's licence, second specimen-derived case, HIGH",
        "Synthetic Yukon record\nYukon driver's licence: 129804",
        [finding("identifier.government.drivers_license_yt", "HIGH")],
        audit_reference="second independent photographed specimen",
        note=(
            "SPECIMEN-DERIVED and corroborating: this second six-digit case strengthens "
            "the Yukon shape but does not make it an issuing-authority grammar."
        ),
        format_authority="specimen-derived-corroborated",
    )

    add(
        "ab_licence_display_01.txt",
        "Driver's licences",
        "POSITIVE",
        "OK",
        "1x AB driver's licence, hyphenated Purview form, HIGH",
        "Synthetic Alberta card transcription\nAlberta driver's licence: 134711-320",
        [finding("identifier.government.drivers_license_ab", "HIGH")],
        audit_reference="Microsoft Purview; photographed specimen corroboration",
        format_authority="microsoft-purview",
    )

    add(
        "on_licence_invalid_suffix_01.txt",
        "Driver's licences",
        "NEGATIVE",
        "OK",
        "no identifier finding; ON Purview-constrained digit is outside 0-3",
        "Synthetic Ontario rejection\nOntario driver's licence: A1234-56789-01299",
        audit_reference="audit item 7",
        format_authority="microsoft-purview",
    )
    add(
        "qc_licence_display_01.txt",
        "Driver's licences",
        "POSITIVE",
        "OK",
        "1x QC driver's licence, printed hyphenated form, HIGH",
        "Synthetic Quebec card transcription\nQuebec driver's licence: L1531-171274-08",
        [finding("identifier.government.drivers_license_qc", "HIGH")],
        audit_reference="audit item 8",
        format_authority="microsoft-purview",
    )
    add(
        "bc_licence_current_8digit_01.txt",
        "Driver's licences",
        "NEGATIVE",
        "OK",
        "no identifier finding; BC Purview format is seven digits",
        f"Synthetic British Columbia record\nBritish Columbia driver's licence: {_digits(rng, 8)}",
        audit_reference="audit item 9",
        format_authority="microsoft-purview",
    )
    _digits(rng, 9)  # preserve the superseded display case's RNG consumption
    add(
        "ns_licence_display_01.txt",
        "Driver's licences",
        "POSITIVE",
        "OK",
        "1x NS Master Number with surname separators/padding, HIGH",
        "Synthetic Nova Scotia card transcription\nNova Scotia driver's licence: PUBLI-020220005",
        [finding("identifier.government.drivers_license_ns", "HIGH")],
        audit_reference="audit item 11",
        format_authority="microsoft-purview",
    )
    add(
        "mb_licence_asterisk_01.txt",
        "Driver's licences",
        "NEGATIVE",
        "OK",
        "no identifier finding; asterisk form is superseded by Purview",
        "Synthetic Manitoba record\nManitoba driver's licence: A1234*567890",
        audit_reference="audit item 10",
        note="Microsoft Purview supersedes the former implementation-defined asterisk grammar.",
        format_authority="microsoft-purview",
    )
    add(
        "mb_licence_display_01.txt",
        "Driver's licences",
        "POSITIVE",
        "OK",
        "1x MB hyphenated Purview driver's licence, HIGH",
        "Synthetic Manitoba card transcription\nManitoba driver's licence: PU-BL-IJ-Q008NH",
        [finding("identifier.government.drivers_license_mb", "HIGH")],
        audit_reference="Microsoft Purview; photographed specimen corroboration",
        format_authority="microsoft-purview",
    )

    # Passport pair duplicates the same content under ID-bearing and neutral
    # camera filenames, exercising mismatch-alarm filename/content triggers.
    passport_content = "Synthetic travel record\nCanadian passport number: AB123456"
    for filename, note in (
        ("passport_scan_01.txt", "ID-bearing filename: mismatch trigger A and content trigger B."),
        ("IMG_20260724_142233_01.txt", "Neutral camera filename: content trigger B only."),
    ):
        add(
            filename,
            "Passports",
            "POSITIVE",
            "OK",
            "1x Canadian passport, compact, HIGH",
            passport_content,
            [finding("identifier.government.passport_ca", "HIGH")],
            note=note,
        )
    add(
        "passport_display_space_01.txt",
        "Passports",
        "POSITIVE",
        "OK",
        "1x Canadian passport, displayed with one space, HIGH",
        "Synthetic travel record\nTravel document: CD 654321",
        [finding("identifier.government.passport_ca", "HIGH")],
    )

    # MRZ exact, invalid-check, and OCR robustness classes.
    td3_l1, td3_l2 = _build_td3()
    td1_l1, td1_l2, td1_l3 = _build_td1()
    add(
        "mrz_td3_exact_01.txt",
        "MRZ exact",
        "POSITIVE",
        "OK",
        "1x exact ICAO TD3 block: document HIGH, DOB MEDIUM, expiry LOW",
        f"{td3_l1}\n{td3_l2}",
        _mrz_expected(),
    )
    add(
        "mrz_td1_exact_01.txt",
        "MRZ exact",
        "POSITIVE",
        "OK",
        "1x exact ICAO TD1 block: document HIGH, DOB MEDIUM, expiry LOW",
        f"{td1_l1}\n{td1_l2}\n{td1_l3}",
        _mrz_expected(),
    )
    bad_td3 = _replace(td3_l2, 9, str((int(td3_l2[9]) + 1) % 10))
    bad_td1 = _replace(td1_l1, 14, str((int(td1_l1[14]) + 1) % 10))
    add(
        "mrz_td3_bad_document_check_01.txt",
        "MRZ invalid",
        "POSITIVE",
        "OK",
        "1x unverified MRZ document MEDIUM, DOB MEDIUM, expiry LOW",
        f"{td3_l1}\n{bad_td3}",
        _mrz_expected(unverified_document=True),
        audit_reference="audit item 22, owner-ratified",
    )
    add(
        "mrz_td1_bad_document_check_01.txt",
        "MRZ invalid",
        "POSITIVE",
        "OK",
        "1x unverified MRZ document MEDIUM, DOB MEDIUM, expiry LOW",
        f"{bad_td1}\n{td1_l2}\n{td1_l3}",
        _mrz_expected(unverified_document=True),
        audit_reference="audit item 22, owner-ratified",
    )

    # Each corruption remains recoverable by position-aware MRZ normalization.
    o0_line = _replace(td3_l2, 2, "O")
    i1_line = _replace(td3_l2, td3_l2.index("1", 13, 20), "I")
    s5_line = _replace(td1_l2, td1_l2.index("5", 0, 6), "S")
    b8_line = _replace(td1_l2, 0, "B")
    rn_line = td3_l1.replace("M", "RN", 1)
    robustness = [
        ("o_for_zero", f"{td3_l1}\n{o0_line}", "O/0 in document number"),
        ("i_for_one", f"{td3_l1}\n{i1_line}", "I/l/1 in numeric DOB"),
        ("s_for_five", f"{td1_l1}\n{s5_line}\n{td1_l3}", "S/5 in numeric DOB"),
        ("b_for_eight", f"{td1_l1}\n{b8_line}\n{td1_l3}", "B/8 in numeric DOB"),
        ("rn_for_m", f"{rn_line}\n{td3_l2}", "rn/m in name line, +1 tolerance"),
    ]
    for index, (slug, content, confusion) in enumerate(robustness, 1):
        add(
            f"mrz_ocr_{slug}_{index:02d}.txt",
            "MRZ robustness",
            "POSITIVE",
            "OK",
            f"recoverable MRZ OCR confusion ({confusion}); three tiered findings",
            content,
            _mrz_expected(),
            audit_reference="audit item 21 robustness class",
        )

    # Ordinary identifiers do not currently have MRZ-style position-aware OCR
    # repair. Each file preserves a known-valid source value in its manifest
    # prose and applies exactly one common photographed-card confusion.
    ordinary_ocr = {
        "sin": [
            (
                "o_for_zero",
                "318507522",
                "3185O7522",
                "Synthetic payroll OCR\nSocial Insurance Number: 3185O7522",
                "identifier.reconstructed.sin",
                "O/0",
            ),
            (
                "i_for_one",
                "318507522",
                "3I8507522",
                "Synthetic payroll OCR\nSocial Insurance Number: 3I8507522",
                "identifier.reconstructed.sin",
                "I/l/1",
            ),
        ],
        "health_card_on": [
            (
                "b_for_eight",
                "8327932763",
                "B327932763",
                "Synthetic Ontario card OCR\nOHIP health card: B327932763",
                "identifier.reconstructed.health_card_on",
                "B/8",
            )
        ],
        "health_card_bc": [
            (
                "o_for_zero",
                "9683128301",
                "96831283O1",
                "Synthetic British Columbia card OCR\nPHN health number: 96831283O1",
                "identifier.reconstructed.health_card_bc",
                "O/0",
            )
        ],
        "health_card_ab": [
            (
                "s_for_five",
                "123456789",
                "1234S6789",
                "Synthetic Alberta card OCR\nAlberta health card: 1234S6789",
                "identifier.government.health_card_ab",
                "S/5",
            )
        ],
        "drivers_license_on": [
            (
                "i_for_one",
                "A12345678901231",
                "A123456I8901231",
                "Synthetic Ontario card OCR\nOntario driver's licence: A123456I8901231",
                "identifier.government.drivers_license_on",
                "I/l/1",
            )
        ],
        "passport_ca": [
            (
                "eight_for_b",
                "AB123456",
                "A8123456",
                "Synthetic passport OCR\nCanadian passport number: A8123456",
                "identifier.government.passport_ca",
                "B/8",
            )
        ],
    }
    for identifier_type, configured_count in OCR_NON_MRZ_COUNTS.items():
        variants = ordinary_ocr[identifier_type]
        if configured_count > len(variants):
            raise SystemExit(
                f"OCR_NON_MRZ_COUNTS[{identifier_type!r}] requests "
                f"{configured_count} cases but only {len(variants)} are defined."
            )
        for index, (slug, source, corrupted, content, category, confusion) in enumerate(
            variants[:configured_count], 1
        ):
            recoverable = identifier_type in {
                "sin",
                "health_card_on",
                "health_card_bc",
            }
            add(
                f"ocr_{identifier_type}_{slug}_{index:02d}.txt",
                "Ordinary identifier OCR",
                "POSITIVE",
                "OK" if recoverable else "GAP-MISS",
                (
                    f"1x {identifier_type}, OCR-corrupted {confusion}; expected "
                    f"to be {'reconstructed, MEDIUM' if recoverable else 'recovered, HIGH'}"
                ),
                content,
                [finding(category, "MEDIUM" if recoverable else "HIGH")],
                audit_reference="owner-requested non-MRZ OCR robustness",
                note=(
                    f"Known-valid synthetic source {source}; OCR text {corrupted}. "
                    + (
                        "Deterministic confusion substitution is accepted only "
                        "because the reconstructed value passes the type's "
                        "published checksum; the finding is capped at MEDIUM "
                        "and preserves the original OCR token."
                        if recoverable
                        else
                        "No public checksum exists for this identifier type, "
                        "so deterministic OCR recovery is intentionally not "
                        "attempted."
                    )
                ),
            )

    # New future detector types.
    uci_values = (
        ("compact_8", "12345678"),
        ("display_4_4", "1234-5678"),
        ("compact_10", "1234567890"),
        ("display_2_4_4", "12-3456-7890"),
    )
    for index, (slug, value) in enumerate(uci_values, 1):
        add(
            f"uci_{slug}_{index:02d}.txt",
            "UCI",
            "POSITIVE",
            "OK",
            "1x IRCC UCI, HIGH; confidence 0.60, source priority 2",
            f"Synthetic IRCC application\nUnique Client Identifier (UCI): {value}",
            [
                finding(
                    "identifier.government.uci",
                    "HIGH",
                    confidence=0.60,
                    source_priority=2,
                )
            ],
            audit_reference="audit section 3.1",
            note="No public checksum exists; context is required and no _unverified tier applies.",
        )

    status_content = (
        "Synthetic Indigenous Services record\n"
        "Certificate of Indian Status registration number: 1234567890"
    )
    for filename, note in (
        ("status_card_registration_01.txt", "ID-bearing filename: mismatch triggers A and B."),
        ("DSC_20260724_084512_01.txt", "Neutral camera filename: mismatch trigger B only."),
    ):
        add(
            filename,
            "Status registration",
            "POSITIVE",
            "OK",
            "1x status-card registration number, HIGH; confidence 0.60, source priority 2",
            status_content,
            [
                finding(
                    "identifier.government.status_card_registration",
                    "HIGH",
                    confidence=0.60,
                    source_priority=2,
                )
            ],
            audit_reference="audit section 3.2",
            note=note + " No public checksum exists; no _unverified tier applies.",
        )

    # Bare/context-free negatives. Values are deliberately invalid for every
    # available checksum so shape alone is the only possible evidence.
    bare_values = (
        ("9digit", "123456789"),
        ("10digit", "1234567890"),
        ("8digit", "12345678"),
        ("uci_4_4", "1234-5678"),
        ("uci_2_4_4", "12-3456-7890"),
    )
    for index, (slug, value) in enumerate(bare_values, 1):
        add(
            f"bare_{slug}_nocontext_{index:02d}.txt",
            "Context-free negatives",
            "NEGATIVE",
            "OK",
            "no identifier finding",
            f"Synthetic unlabeled export\n{value}",
        )

    adjacent = (
        ("invoice", "Invoice number 123456789 is due next quarter."),
        ("employee", "Employee ID 12345678 belongs to a synthetic department."),
        ("order", "Order number 1234567890 has shipped."),
        ("part", "Part number AB123456 is stocked in warehouse C."),
        ("phone", "Call the service desk at 416-555-0199 during business hours."),
    )
    for index in range(1, NEGATIVE_ADJACENT_COUNT + 1):
        slug, sentence = adjacent[(index - 1) % len(adjacent)]
        add(
            f"adjacent_{slug}_{index:02d}.txt",
            "Adjacent negatives",
            "NEGATIVE",
            "OK",
            "no identifier finding",
            f"Synthetic business correspondence\n{sentence}",
        )

    contextual = (
        ("healthcard", "Bring your health card to the appointment; no number is required."),
        ("licence", "The policy discusses driver's licence renewal without recording a licence."),
        ("passport", "Travellers should confirm that their passport remains valid."),
    )
    for index in range(1, NEGATIVE_CONTEXTUAL_COUNT + 1):
        slug, sentence = contextual[(index - 1) % len(contextual)]
        add(
            f"contextual_{slug}_{index:02d}.md",
            "Contextual negatives",
            "NEGATIVE",
            "OK",
            "no identifier finding",
            f"# Synthetic policy note\n\n{sentence}",
        )

    add(
        "foreign_passport_generic_01.txt",
        "Scope boundary",
        "POSITIVE",
        "OK",
        "1x generic foreign passport, HIGH",
        "Synthetic foreign travel record\nPassport number: 123456789",
        [finding("identifier.government.passport_generic", "HIGH")],
        audit_reference="audit item 20, owner-ratified scope boundary",
        note="Scope-boundary documentation, not a Canadian format claim.",
    )

    return cases


def validate_cases(cases: list[Case]) -> None:
    filenames = [case.filename for case in cases]
    if len(filenames) != len(set(filenames)):
        raise SystemExit("Duplicate corpus filename.")
    if not 70 <= len(cases) <= 100:
        raise SystemExit(f"Corpus size {len(cases)} is outside the required 70-100 range.")

    for case in cases:
        if case.verdict not in VERDICTS or case.status not in STATUSES:
            raise SystemExit(f"Invalid verdict/status in {case.filename}.")
        if case.verdict == "NEGATIVE" and case.expected_findings:
            raise SystemExit(f"NEGATIVE {case.filename} has expected identifier findings.")
        if case.verdict == "POSITIVE" and not case.expected_findings:
            raise SystemExit(f"POSITIVE {case.filename} has no expected identifier findings.")
        if case.status.startswith("GAP-") and not case.audit_reference:
            raise SystemExit(f"Gap {case.filename} lacks an audit reference.")
        if case.status == "GAP-FALSE-POSITIVE" and case.expected_findings:
            raise SystemExit(
                f"GAP-FALSE-POSITIVE {case.filename} expects a finding; use "
                "GAP-TIER, GAP-PARTIAL, or GAP-MISS."
            )
        if case.status in {"GAP-TIER", "GAP-PARTIAL"} and not case.expected_findings:
            raise SystemExit(f"{case.status} {case.filename} must expect a finding.")
        if case.status == "UNVERIFIED" and case.verdict != "POSITIVE":
            raise SystemExit(f"UNVERIFIED {case.filename} must remain ground-truth POSITIVE.")

    expected_special_statuses = {}
    by_filename = {case.filename: case for case in cases}
    for filename, status in expected_special_statuses.items():
        if by_filename[filename].status != status:
            raise SystemExit(f"{filename} must remain classified {status}.")

    ocr_count = sum(case.group == "Ordinary identifier OCR" for case in cases)
    if ocr_count != sum(OCR_NON_MRZ_COUNTS.values()):
        raise SystemExit("Ordinary OCR case count does not match OCR_NON_MRZ_COUNTS.")
    if not validate_sin("318507522"):
        raise SystemExit("Ordinary OCR SIN source is not checksum-valid.")
    if not ohip_valid("8327932763"):
        raise SystemExit("Ordinary OCR Ontario source is not checksum-valid.")
    if not bc_phn_valid("9683128301"):
        raise SystemExit("Ordinary OCR BC source is not checksum-valid.")

    planted_sins = [
        case.content.split(":", 1)[-1].strip().replace(" ", "").replace("-", "")
        for case in cases
        if case.filename.startswith("sin_valid_")
    ]
    if not all(validate_sin(value) for value in planted_sins):
        raise SystemExit("A planted valid SIN failed validation.")
    on_values = [
        case.content.split()[-1]
        for case in cases
        if case.filename.startswith("on_healthcard_compact_")
    ]
    if not all(ohip_valid(value) for value in on_values):
        raise SystemExit("A planted Ontario health number failed validation.")
    bc_values = [
        case.content.split()[-1]
        for case in cases
        if case.filename.startswith("bc_healthcard_compact_")
    ]
    if not all(bc_phn_valid(value) for value in bc_values):
        raise SystemExit("A planted BC health number failed validation.")


def _manifest_entry(case: Case) -> dict:
    expected_findings = []
    for item in case.expected_findings:
        rendered = asdict(item)
        expected_findings.append(
            {key: value for key, value in rendered.items() if value is not None}
        )
    entry = {
        "filename": case.filename,
        "group": case.group,
        "verdict": case.verdict,
        "status": case.status,
        "expected": case.expected,
        "expected_findings": expected_findings,
    }
    if case.audit_reference:
        entry["audit_reference"] = case.audit_reference
    if case.note:
        entry["note"] = case.note
    if case.format_authority:
        entry["format_authority"] = case.format_authority
    return entry


def render_json(cases: list[Case]) -> str:
    payload = {
        "seed": SEED,
        "generator": "tests/make_canadian_eval_data.py",
        "assertion_semantics": (
            "NEGATIVE means zero findings whose taxonomy category starts with "
            "identifier.; entity.* and contact.* findings are permitted."
        ),
        "files": [_manifest_entry(case) for case in cases],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render_markdown(cases: list[Case]) -> str:
    verdict_counts = Counter(case.verdict for case in cases)
    cross_counts = Counter((case.verdict, case.status) for case in cases)
    filename_width = max(len("filename"), *(len(case.filename) for case in cases))
    verdict_width = max(len("verdict"), *(len(case.verdict) for case in cases))
    status_width = max(len("status"), *(len(case.status) for case in cases))
    lines = [
        "# Canadian identifier evaluation corpus",
        "",
        (
            "All values are synthetic and generated, tied to no real person. "
            "The generator is the single source of truth for this document and "
            "`manifest.json`."
        ),
        "",
        (
            f"Summary: {len(cases)} files — {verdict_counts['POSITIVE']} POSITIVE, "
            f"{verdict_counts['NEGATIVE']} NEGATIVE."
        ),
        "",
        "Verdict × status: "
        + "; ".join(
            f"{verdict}/{status}={cross_counts[(verdict, status)]}"
            for verdict in ("POSITIVE", "NEGATIVE")
            for status in (
                "OK",
                "GAP-MISS",
                "GAP-TIER",
                "GAP-PARTIAL",
                "GAP-FALSE-POSITIVE",
                "UNVERIFIED",
            )
            if cross_counts[(verdict, status)]
        )
        + ".",
        "",
        (
            "A NEGATIVE asserts zero findings whose taxonomy category starts "
            "with `identifier.`. It does **not** assert zero findings overall; "
            "`entity.*` and `contact.*` findings from surrounding text are allowed."
        ),
        "",
        (
            "`GAP-FALSE-POSITIVE` means the detector should have found nothing. "
            "`GAP-TIER` means existence is correct but trust/risk is wrong; "
            "`GAP-PARTIAL` means only part of the public identifier span was "
            "captured. These distinctions prevent a harness from scoring tier "
            "or span disagreements as absence failures."
        ),
        "",
        (
            "Microsoft Purview is the format authority for all ten provinces, "
            "with each pattern corroborated against a photographed specimen. "
            "NT, NU, and YK are not covered by Purview and are explicitly marked "
            "as weaker specimen-derived cases."
        ),
        "",
        (
            "**Audit correction:** Audit section 1.1's negative contract for "
            "ON/BC checksum failure is incorrect; verified against "
            "`detectors/health_card_detector.py`."
        ),
        "",
    ]

    for group, description in GROUP_DESCRIPTIONS.items():
        group_cases = [case for case in cases if case.group == group]
        if not group_cases:
            continue
        lines.extend(
            [
                f"## {group}",
                "",
                description,
                "",
                (
                    f"{'filename':<{filename_width}} | "
                    f"{'verdict':<{verdict_width}} | "
                    f"{'status':<{status_width}} | expected"
                ),
                (
                    f"{'-' * filename_width} | "
                    f"{'-' * verdict_width} | "
                    f"{'-' * status_width} | {'-' * len('expected')}"
                ),
            ]
        )
        for case in group_cases:
            detail = case.expected
            if case.audit_reference:
                detail += f" ({case.audit_reference})"
            if case.note:
                detail += f" — {case.note}"
            lines.append(
                f"{case.filename:<{filename_width}} | "
                f"{case.verdict:<{verdict_width}} | "
                f"{case.status:<{status_width}} | {detail}"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    cases = build_cases()
    validate_cases(cases)

    for directory in (DATA_DIR, DOCS_DIR):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)

    for case in cases:
        (DATA_DIR / case.filename).write_text(case.content, encoding="utf-8", newline="\n")

    (DOCS_DIR / "manifest.json").write_text(
        render_json(cases), encoding="utf-8", newline="\n"
    )
    (DOCS_DIR / "MANIFEST.md").write_text(
        render_markdown(cases), encoding="utf-8", newline="\n"
    )

    print(f"[✓] Wrote {len(cases)} synthetic corpus files to {DATA_DIR}")
    print(f"[✓] Wrote MANIFEST.md and manifest.json to {DOCS_DIR}")
    print(f"[✓] Fixed seed: {SEED}")


if __name__ == "__main__":
    main()
