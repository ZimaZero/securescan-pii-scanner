"""Bounded, line-aware association between extracted values and field labels.

Detectors receive the extractor's preserved text lines, not OCR geometry.  This
module ranks labels without falling back to a flat character window:

1. label before the value on the same line;
2. label on the immediately preceding non-empty line;
3. label after the value on the same line (lowest confidence).

Only evidence at the best rank decides.  Opposing labels tied at that rank are
ambiguous, allowing callers to retain findings when recall is the priority.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from bisect import bisect_right
import re
from typing import Iterable, Literal, Optional, Sequence


Decision = Literal["positive", "negative", "ambiguous", "none"]

DOB_FIELD_LABELS = (
    "dob", "ddn", "d.o.b", "date of birth", "birth date", "birth", "birthday",
    "born", "ne le", "nee le", "naissance",
)
NON_DOB_DATE_LABELS = (
    "exp", "expiry", "expires", "issue", "issued", "iss", "valid until",
    "date de delivrance", "expire le",
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_OCR_CONFUSABLES = str.maketrans({"0": "o", "1": "i", "5": "s", "8": "s"})


@dataclass(frozen=True)
class LabelEvidence:
    kind: Literal["positive", "negative"]
    label: str
    observed: str
    start: int
    end: int
    line: int
    rank: int
    distance: int


@dataclass(frozen=True)
class Association:
    decision: Decision
    evidence: tuple[LabelEvidence, ...] = ()


@dataclass(frozen=True)
class FieldLabelIndex:
    text_length: int
    line_spans: tuple[tuple[int, int], ...]
    line_starts: tuple[int, ...]
    value_spans: tuple[tuple[int, int], ...]
    matches: tuple[tuple[Literal["positive", "negative"], str, str, int, int, int], ...]


def _line_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    offset = 0
    for line in text.splitlines(keepends=True):
        content_end = offset + len(line.rstrip("\r\n"))
        spans.append((offset, content_end))
        offset += len(line)
    if not spans or offset < len(text) or text.endswith(("\n", "\r")):
        spans.append((offset, len(text)))
    return spans


def _line_index(
    spans: Sequence[tuple[int, int]],
    position: int,
    starts: Optional[Sequence[int]] = None,
) -> int:
    line_starts = starts if starts is not None else tuple(start for start, _end in spans)
    return max(0, min(len(spans) - 1, bisect_right(line_starts, position) - 1))


def _neighbor_nonempty(
    text: str, spans: Sequence[tuple[int, int]], line: int, direction: int
) -> Optional[int]:
    candidate = line + direction
    while 0 <= candidate < len(spans):
        start, end = spans[candidate]
        # OCR sometimes emits a punctuation-only line (commonly ':' between a
        # field label and its value).  It is layout noise, not an intervening
        # field, so only an alphanumeric-bearing line counts as a neighbor.
        if any(char.isalnum() for char in text[start:end]):
            return candidate
        candidate += direction
    return None


def _exact_label_matches(text: str, label: str):
    parts = [re.escape(part) for part in label.split()]
    body = r"[ \t]+".join(parts)
    # A label must begin outside an alphanumeric token.  At the right edge,
    # letters are forbidden but digits are allowed, deliberately accepting
    # joined field/value forms such as DOB1980 and EXP2024.  This rejects EXP
    # inside EXPERIENCE.
    pattern = re.compile(
        r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z])", re.IGNORECASE
    )
    yield from pattern.finditer(text)


def _normalize_fuzzy_token(token: str) -> str:
    joined = re.match(r"^([A-Za-z][A-Za-z0-9]*?)(\d{4,})$", token)
    if joined:
        token = joined.group(1)
    return token.lower().translate(_OCR_CONFUSABLES)


def _all_label_matches(text: str, labels: Iterable[str]):
    labels = tuple(dict.fromkeys(label.lower() for label in labels))
    exact_spans = set()
    for label in labels:
        for match in _exact_label_matches(text, label):
            exact_spans.add(match.span())
            yield label, match.group(0), match.start(), match.end()

    simple_labels = {
        re.sub(r"[^a-z]", "", label): label
        for label in labels
        if " " not in label and re.sub(r"[^a-z]", "", label)
    }
    comparable_labels = tuple(simple_labels)
    for match in _TOKEN_RE.finditer(text):
        if any(match.start() < end and start < match.end() for start, end in exact_spans):
            continue
        observed = match.group(0)
        normalized = _normalize_fuzzy_token(observed)
        for comparable, label in simple_labels.items():
            repeated = normalized == comparable * 2
            compound = any(
                normalized == comparable + other
                or normalized == other + comparable
                for other in comparable_labels
            )
            if len(comparable) <= 3:
                matched = normalized == comparable or repeated or compound
            else:
                matched = repeated or compound or (
                    abs(len(normalized) - len(comparable)) <= 2
                    and SequenceMatcher(None, normalized, comparable).ratio() >= 0.80
                )
            if matched:
                yield label, observed, match.start(), match.end()
                break


def build_field_label_index(
    text: str,
    *,
    positive_labels: Iterable[str] = (),
    negative_labels: Iterable[str] = (),
    value_spans: Optional[Iterable[tuple[int, int]]] = None,
) -> FieldLabelIndex:
    """Precompute label locations once for candidate-adjacent extracted lines."""
    spans = tuple(_line_spans(text))
    line_starts = tuple(start for start, _end in spans)
    candidate_spans = tuple(sorted(value_spans or ()))
    if value_spans is None:
        segments = ((0, text),)
    else:
        relevant_lines = set()
        for start, _end in candidate_spans:
            line = _line_index(spans, start, line_starts)
            relevant_lines.add(line)
            previous = _neighbor_nonempty(text, spans, line, -1)
            following = _neighbor_nonempty(text, spans, line, 1)
            if previous is not None:
                relevant_lines.add(previous)
            if following is not None:
                relevant_lines.add(following)
        segments = tuple(
            (spans[line][0], text[spans[line][0] : spans[line][1]])
            for line in sorted(relevant_lines)
        )
    matches = []
    for kind, labels in (
        ("positive", positive_labels),
        ("negative", negative_labels),
    ):
        for offset, segment in segments:
            for label, observed, start, end in _all_label_matches(segment, labels):
                absolute_start, absolute_end = offset + start, offset + end
                matches.append(
                    (
                        kind,
                        label,
                        observed,
                        absolute_start,
                        absolute_end,
                        _line_index(spans, absolute_start, line_starts),
                    )
                )
    return FieldLabelIndex(
        len(text), spans, line_starts, candidate_spans, tuple(matches)
    )


def associate_field_label(
    text: str,
    value_start: int,
    value_end: int,
    *,
    positive_labels: Iterable[str] = (),
    negative_labels: Iterable[str] = (),
    label_index: Optional[FieldLabelIndex] = None,
) -> Association:
    """Return the strongest bounded label association for one value span."""
    if not isinstance(text, str) or not (0 <= value_start < value_end <= len(text)):
        return Association("none")

    index = label_index or build_field_label_index(
        text,
        positive_labels=positive_labels,
        negative_labels=negative_labels,
    )
    if index.text_length != len(text):
        return Association("none")
    spans = index.line_spans
    value_line = _line_index(spans, value_start, index.line_starts)
    previous_line = _neighbor_nonempty(text, spans, value_line, -1)
    evidence = []

    for kind, label, observed, start, end, line in index.matches:
        blocked = any(
            (end <= other_start and other_end <= value_start)
            or (value_end <= other_start and other_end <= start)
            for other_start, other_end in index.value_spans
            if (other_start, other_end) != (value_start, value_end)
        )
        if blocked:
            continue
        if line == value_line and end <= value_start:
            rank, distance = 0, value_start - end
        elif line == previous_line:
            rank, distance = 1, value_start - end
        elif line == value_line and start >= value_end:
            rank, distance = 2, start - value_end
        else:
            continue
        evidence.append(
            LabelEvidence(
                kind=kind,
                label=label,
                observed=observed,
                start=start,
                end=end,
                line=line,
                rank=rank,
                distance=abs(distance),
            )
        )

    if not evidence:
        return Association("none")

    best_rank = min(item.rank for item in evidence)
    best = tuple(
        sorted(
            (item for item in evidence if item.rank == best_rank),
            key=lambda item: (item.distance, item.start, item.label),
        )
    )
    kinds = {item.kind for item in best}
    if len(kinds) > 1:
        return Association("ambiguous", best)
    return Association(next(iter(kinds)), best)
