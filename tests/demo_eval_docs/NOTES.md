# DEMO folder ground truth (scope)

`GROUND_TRUTH.csv` here follows the same schema as
`tests/specimen_eval_docs/GROUND_TRUTH.csv`. The images themselves are real
photographed cards and are not committed to this repository; they live in
the external DEMO folder the owner maintains outside the repo (currently
mirrored at `/mnt/demo` on this machine). There is no automated runner for
this corpus yet (unlike `tests/run_canadian_eval.py` /
`tests/run_specimen_eval.py`) — evaluating it is a manual, ad hoc scan, same
as the `/mnt/demo`-based acceptance comparisons already referenced in
`CLAUDE.md`.

Only one New Brunswick Medicare card is scored here:

- **telechargement.jpg** — one row, `health_card_nb` = `999999999`, POSITIVE.
  PaddleOCR reads its printed 3-3-3 groups merged 6-3 (`"999999 999"`).

Two related NB images are deliberately **not** scored fixtures:

- **12516554_10207519563300831_634996911_n.jpg** — PaddleOCR reads its
  middle 3-digit group as `915`; the true printed value is unconfirmed (no
  source-of-truth for the actual card), so an exact-value assertion would be
  unverifiable. Referenced as evidence only — see `docs/evidence/`.
- **NB_medicare.png** — cropped; OCR recovers only 8 of the card's 9 digits
  (`99999999`). Unusable as a fixture for the same reason. This is the file
  that currently reaches the generic `health_card_ca` fallback described in
  Change 3 of the NB detection fix (see `docs/evidence/`).
