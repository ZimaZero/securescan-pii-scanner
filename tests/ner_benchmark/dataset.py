#!/usr/bin/env python3
# tests/ner_benchmark/dataset.py
"""
Single source of truth for the NER benchmark: the mixed-PII documents and their
ground-truth answer keys. ANSWER_KEY.md documents the same items in prose.

Every ground-truth `value` appears VERBATIM in its document so span matching
works. `kind` controls matching: "numeric" compares alphanumeric-stripped
strings, "text" compares whitespace-normalized lowercased substrings.
"""

# ============================================================
#  DOCUMENT 1 — onboarding memo (clean, well-formed PII)
# ============================================================

DOC1 = """\
From: Sarah Johnson <sarah.johnson@northwind.com>
To: David Chen
Date: March 14, 2024

Hi David,

Following up on the onboarding for our new hire at Northwind Trading Inc.
Please find the details below.

Employee: Michael Rodriguez
Home address: 1425 Maple Avenue, Springfield, IL 62704
Personal email: m.rodriguez88@gmail.com
Mobile: (415) 555-0182
Office line: 403-555-9910

US SSN: 536-90-4399
Canadian SIN: 046 454 286
Corporate card (Visa): 4539 1488 0343 6467
Backup card: 5500 0000 0000 0004

He'll be relocating from Vancouver to our Chicago office on 2024-04-01.
His onboarding portal is at https://portal.northwind.com/welcome
The internal server is at 192.168.1.42, external gateway 203.0.113.75.
IBAN for reimbursements: GB29 NWBK 6016 1331 9268 19

Let me know if you need anything else.

Best,
Sarah Johnson
Northwind Trading Inc.
"""

GT1 = [
    {"value": "Sarah Johnson",                            "category": "PERSON",      "kind": "text"},
    {"value": "David Chen",                               "category": "PERSON",      "kind": "text"},
    {"value": "Michael Rodriguez",                        "category": "PERSON",      "kind": "text"},
    {"value": "Northwind Trading Inc.",                   "category": "ORG",         "kind": "text"},
    {"value": "1425 Maple Avenue, Springfield, IL 62704", "category": "ADDRESS",     "kind": "text"},
    {"value": "Vancouver",                                "category": "LOCATION",    "kind": "text"},
    {"value": "Chicago",                                  "category": "LOCATION",    "kind": "text"},
    {"value": "March 14, 2024",                           "category": "DATE",        "kind": "text"},
    {"value": "2024-04-01",                               "category": "DATE",        "kind": "text"},
    {"value": "sarah.johnson@northwind.com",              "category": "EMAIL",       "kind": "text"},
    {"value": "m.rodriguez88@gmail.com",                  "category": "EMAIL",       "kind": "text"},
    {"value": "(415) 555-0182",                           "category": "PHONE",       "kind": "numeric"},
    {"value": "403-555-9910",                             "category": "PHONE",       "kind": "numeric"},
    {"value": "536-90-4399",                              "category": "SSN",         "kind": "numeric"},
    {"value": "046 454 286",                              "category": "SIN",         "kind": "numeric"},
    {"value": "4539 1488 0343 6467",                      "category": "CREDIT_CARD", "kind": "numeric"},
    {"value": "5500 0000 0000 0004",                      "category": "CREDIT_CARD", "kind": "numeric"},
    {"value": "https://portal.northwind.com/welcome",     "category": "URL",         "kind": "text"},
    {"value": "192.168.1.42",                             "category": "IP",          "kind": "numeric"},
    {"value": "203.0.113.75",                             "category": "IP",          "kind": "numeric"},
    {"value": "GB29 NWBK 6016 1331 9268 19",              "category": "IBAN",        "kind": "numeric"},
]

# ============================================================
#  DOCUMENT 2 — incident report (multi-page, adversarial)
# ============================================================
# Hard on purpose: intl phone formats, IPv6, tagged emails, ordinal/ISO/slash
# dates, a hyphenated name, an org that reads like two names, a Canadian health
# card, AND decoy look-alikes that must NOT be flagged (order # that resembles a
# card, PIN that resembles an SSN, a partial IP). Decoys use digits DISTINCT from
# the real values so a hit on them is a genuine false positive.

DOC2 = """\
========================= PAGE 1 =========================
INCIDENT REPORT - Ref #INC-2023-88421
Filed: 07/09/2023 14:32 UTC
Analyst: Dr. Priya Ramaswamy, Cloudstrike Systems LLP

Summary
On the evening of Sept 7th, 2023, an account belonging to customer
Jordan Baker was compromised. Jordan had last logged in from Amman the
previous week, but this session originated from 45.83.201.19
(IPv6 2001:0db8:85a3:0000:0000:8a2e:0370:7334).

The attacker attempted to change the recovery email from
j.baker+secure@fastmail.io to hacker_9x@protonmail.com.

Contact
- Primary: +1 (604) 555-2381
- Alt (intl): +44 20 7946 0958
- Fax (do NOT dial): 1-800-000-0000

========================= PAGE 2 =========================
Financial exposure
Card on file ending 4467 - full PAN captured in logs:
4556-7376-5624-4467. A second card 6011 0009 9013 9424 was also present.
Wire details: IBAN DE89 3704 0044 0532 0130 00 (Deutsche Bank).

Identity documents referenced in the ticket:
- SSN 078-05-1120
- SIN 193 456 787
- Ontario health card 5544 332 211-XH

Do NOT confuse these with internal identifiers:
- Order number 4556737656244470 (looks like a card, is NOT)
- Support PIN 078051121 (looks like an SSN, is NOT)
- Rack unit 192.168 (not an IP)

========================= PAGE 3 =========================
Resolution
Escalated to Marcus Webb-O'Connor at the Toronto office on
2023-09-11T09:15:00-04:00. See the postmortem at
https://status.cloudstrike.example/incidents/88421?ref=email&x=1

The customer's mailing address is
Flat 2B, 221B Baker Street, London NW1 6XE, United Kingdom.

Signed,
Priya Ramaswamy
VP, Trust & Safety - Cloudstrike Systems LLP
"""

GT2 = [
    {"value": "Priya Ramaswamy",                                          "category": "PERSON",      "kind": "text"},
    {"value": "Jordan Baker",                                             "category": "PERSON",      "kind": "text"},
    {"value": "Marcus Webb-O'Connor",                                     "category": "PERSON",      "kind": "text"},
    {"value": "Cloudstrike Systems LLP",                                  "category": "ORG",         "kind": "text"},
    {"value": "Deutsche Bank",                                            "category": "ORG",         "kind": "text"},
    {"value": "Amman",                                                    "category": "LOCATION",    "kind": "text"},
    {"value": "Toronto",                                                  "category": "LOCATION",    "kind": "text"},
    {"value": "Flat 2B, 221B Baker Street, London NW1 6XE, United Kingdom","category": "ADDRESS",    "kind": "text"},
    {"value": "07/09/2023",                                               "category": "DATE",        "kind": "text"},
    {"value": "Sept 7th, 2023",                                           "category": "DATE",        "kind": "text"},
    {"value": "2023-09-11T09:15:00-04:00",                                "category": "DATE",        "kind": "text"},
    {"value": "j.baker+secure@fastmail.io",                               "category": "EMAIL",       "kind": "text"},
    {"value": "hacker_9x@protonmail.com",                                 "category": "EMAIL",       "kind": "text"},
    {"value": "+1 (604) 555-2381",                                        "category": "PHONE",       "kind": "numeric"},
    {"value": "+44 20 7946 0958",                                         "category": "PHONE",       "kind": "numeric"},
    {"value": "1-800-000-0000",                                           "category": "PHONE",       "kind": "numeric"},
    {"value": "078-05-1120",                                              "category": "SSN",         "kind": "numeric"},
    {"value": "193 456 787",                                              "category": "SIN",         "kind": "numeric"},
    {"value": "4556-7376-5624-4467",                                      "category": "CREDIT_CARD", "kind": "numeric"},
    {"value": "6011 0009 9013 9424",                                      "category": "CREDIT_CARD", "kind": "numeric"},
    {"value": "5544 332 211-XH",                                          "category": "HEALTH_CARD", "kind": "numeric"},
    {"value": "https://status.cloudstrike.example/incidents/88421?ref=email&x=1", "category": "URL", "kind": "text"},
    {"value": "45.83.201.19",                                             "category": "IP",          "kind": "numeric"},
    {"value": "2001:0db8:85a3:0000:0000:8a2e:0370:7334",                  "category": "IP",          "kind": "numeric"},
    {"value": "DE89 3704 0044 0532 0130 00",                              "category": "IBAN",        "kind": "numeric"},
]

# Decoys present in DOC2 that are NOT PII — flagging any of these is a genuine
# false positive (used for the written analysis, not scored specially):
DOC2_DECOYS = ["INC-2023-88421", "4556737656244470", "078051121", "192.168", "4467"]

# ============================================================
#  REGISTRY + CATEGORY / TYPE MAPS
# ============================================================

DOCUMENTS = [
    {"name": "doc1_onboarding_memo (clean)",   "text": DOC1, "ground_truth": GT1},
    {"name": "doc2_incident_report (adversarial)", "text": DOC2, "ground_truth": GT2},
]

# Back-compat aliases (doc1) for anything importing the originals.
DOCUMENT = DOC1
GROUND_TRUTH = GT1

CANONICAL = [
    "PERSON", "ORG", "LOCATION", "ADDRESS", "DATE", "EMAIL", "PHONE",
    "SSN", "SIN", "CREDIT_CARD", "HEALTH_CARD", "URL", "IP", "IBAN",
]

# Structured/regex-style categories — the ones GLiNER struggled with in round 1.
STRUCTURED = ["EMAIL", "PHONE", "SSN", "CREDIT_CARD", "URL", "IP", "IBAN"]

PRESIDIO_MAP = {
    "PERSON": "PERSON",
    "ORGANIZATION": "ORG",
    "LOCATION": "LOCATION",
    "GPE": "LOCATION",
    "DATE_TIME": "DATE",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "US_SSN": "SSN",
    "CREDIT_CARD": "CREDIT_CARD",
    "URL": "URL",
    "IP_ADDRESS": "IP",
    "IBAN_CODE": "IBAN",
    # everything else reported under its raw type -> false positive
}

GLINER_LABELS = [
    "person", "organization", "location", "address", "date", "email",
    "phone number", "credit card number", "social security number",
    "url", "ip address", "iban",
]
GLINER_MAP = {
    "person": "PERSON",
    "organization": "ORG",
    "location": "LOCATION",
    "address": "ADDRESS",
    "date": "DATE",
    "email": "EMAIL",
    "phone number": "PHONE",
    "credit card number": "CREDIT_CARD",
    "social security number": "SSN",
    "url": "URL",
    "ip address": "IP",
    "iban": "IBAN",
}

# NOTE: neither model has a native SIN or Canadian HEALTH_CARD type — those are
# deliberate discriminators that only country-specific validated detectors catch.
