# Compiled Keyword/Context Matcher Probe

Date: 2026-07-20  
Runtime: Python 3.13.7  
Candidate B dependency: `pyahocorasick==2.3.1`

This throwaway `/tmp` probe compared the existing keyword/context detector with
two locate-first implementations. Candidate A used one compiled alternation
regular expression; Candidate B used an Aho-Corasick automaton. Both candidates
retained the existing fuzzy keyword candidate set, confidence calculations,
validation, context distance, placeholder suppression, and negation behavior.
Pattern verification ran only in bounded candidate neighborhoods.

The current detector does not expose source offsets in its public result, so the
comparison reconstructed every literal occurrence span for each emitted value.
Parity was then evaluated as exact `(file, type, value, start, end)` sets.

| Corpus | Files | Characters | Current | Compiled regex | Aho-Corasick | Exact diff (A / B) |
|---|---:|---:|---:|---:|---:|---:|
| Deterministic stress | 174 | 44,079,619 | 30.78 s | 7.44 s | 4.82 s | 0 / 0 |
| Enron deterministic slice | 200 | 558,038 | 0.619 s | 0.668 s | 0.642 s | 0 / 0 |
| Extracted Test images | 11 | 6,175 | 0.0089 s | 0.0117 s | 0.0117 s | 0 / 0 |

On the 44-million-character workload that motivated the change, Candidate A was
approximately 4.1 times faster and Candidate B was approximately 6.4 times
faster. Small-corpus timings are dominated by fixed setup overhead.

## Fuzzy-matching ablation

Fuzzy matching cannot be removed without detection drift:

| Corpus | Missing occurrence findings with fuzzy matching disabled |
|---|---:|
| Deterministic stress | 0 |
| Enron deterministic slice | 33 |
| Extracted Test images | 2 |

The two Test-corpus misses were date-of-birth findings reached through an
OCR-tolerant keyword. The selected design therefore shares one tokenization and
vocabulary per document and applies a mathematically safe
`SequenceMatcher.real_quick_ratio()` upper-bound gate before the unchanged fuzzy
comparison; it does not silently remove typo tolerance.

An initial bounded-window prototype exposed a left-boundary truncation that
could turn `w..white@enron.com` into a synthetic `..white@enron.com` match.
Expanding verification windows through token boundaries eliminated the artifact
before the reported measurements. The final comparison contained no added or
missing findings.

## Ratification

The project owner selected Candidate B. Production uses Aho-Corasick when the
pinned dependency imports successfully and falls back to Candidate A with one
visible warning if that runtime import is unavailable.
