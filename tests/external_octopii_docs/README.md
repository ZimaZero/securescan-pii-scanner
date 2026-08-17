# External validation corpus — Octopii dummy PII

These 8 images are copied verbatim from the `dummy-pii/` folder of
[RedHuntLabs/Octopii](https://github.com/redhuntlabs/Octopii) (MIT License,
Copyright (c) 2023 Owais Shaikh / Research @ RedHunt Labs Pvt Ltd), pinned at
commit `ff96709ba9957fc221da4a93980b983c289b932f` (2025-01-22).

Octopii is RedHunt Labs' own open-source PII scanner; this `dummy-pii/`
folder is their published set of **synthetic, non-real** specimen documents
(fake Aadhaar, PAN, SSN, driver's licence, passports, debit card, Hong Kong
resident ID) used to demonstrate and test that tool. SecureScan reuses
it here as a read-only, third-party evaluation corpus — an outside check on
its OCR and detection pipeline against document shapes the project generators
(`tests/make_stress_data.py`, `tests/make_format_data.py`) never produced,
rather than another project-authored corpus susceptible to tuning bias.

Files (unmodified from upstream):

- `dummy-aadhaar.png` — Indian Aadhaar card (12-digit national ID)
- `dummy-debit-card.jpg` — generic debit card
- `dummy-drivers-license-maharashtra.jpg` — Indian (Maharashtra) driver's licence
- `dummy-hong-kong-resident-id.png` — Hong Kong resident ID card
- `dummy-PAN-India.jpg` — Indian PAN (Permanent Account Number) card
- `dummy-passport-britain.jpg` — UK passport
- `dummy-passport-ukraine.jpg` — Ukrainian passport
- `dummy-ssn.jpg` — US Social Security Number card

## Scope note

SecureScan targets **Canadian + generic US** PII (SIN, SSN, Canadian
health cards/driver's licences/passports, credit cards, email/phone/postal
code). Aadhaar, PAN, Hong Kong resident ID, and non-US/CA passport number
formats are foreign ID types **outside SecureScan's scope by
design**, not detection failures. See `EVALUATION.md` in this directory for
the full per-file results and the DETECTED / OUT_OF_SCOPE / GAP
classification.

## License

Upstream Octopii is MIT-licensed; the full license text is reproduced below
per its terms.

```
MIT License

Copyright (c) 2023 Owais Shaikh
Research @ RedHunt Labs Pvt Ltd
Email: owais.shaikh@redhuntlabs.com | me@0x4f.in

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
