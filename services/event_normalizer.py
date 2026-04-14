from __future__ import annotations

"""
Event Normalizer — Phase 4 crossvenue matching foundation.

Provides text normalization and similarity scoring for comparing
market/event titles across different prediction market venues.

Design principles:
- Conservative: prefer false negatives (misses) over false positives (bad matches).
- Never assume two events are equivalent based on title alone when time data is absent.
- All scores are 0.0–1.0; consumers choose their own thresholds.
- No side effects: pure functions only. No DB writes here.
"""

import re
import unicodedata
from typing import Any


# ── Sport/category tokens to strip from titles ───────────────────────────────
# These are common noise tokens that appear in titles across all venues but
# carry little discriminative power for matching purposes.
_NOISE_TOKENS: frozenset[str] = frozenset([
    "nfl", "nba", "mlb", "nhl", "mls", "ncaa", "college",
    "soccer", "football", "basketball", "baseball", "hockey",
    "ufc", "mma", "boxing", "tennis", "golf", "formula", "f1",
    "the", "will", "who", "what", "when", "does", "did",
    "win", "wins", "lose", "loses",
])

# Outcome synonym groups for yes/no normalization
_YES_SYNONYMS: frozenset[str] = frozenset(["yes", "will", "does", "is", "true", "over", "above", "higher"])
_NO_SYNONYMS: frozenset[str] = frozenset(["no", "wont", "will not", "is not", "false", "under", "below", "lower"])

# Team/entity normalization: maps known aliases to canonical form
# Keep this minimal to avoid false-positive match amplification.
_CANONICAL_NAMES: dict[str, str] = {
    "new york city": "new york",
    "nyc": "new york",
    "la ": "los angeles ",
    "l.a.": "los angeles",
    "sf ": "san francisco ",
    "s.f.": "san francisco",
    "uk": "united kingdom",
    "us ": "united states ",
    "u.s.": "united states",
    "usa": "united states",
    "dem": "democrat",
    "rep": "republican",
    "gop": "republican",
}


# ── Text normalization ────────────────────────────────────────────────────────

def _strip_accents(text: str) -> str:
    """Remove accents/diacritics via Unicode normalization."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def normalize_text(text: str) -> str:
    """
    Produce a canonical normalized string:
    - lowercase
    - accent-stripped
    - punctuation → space
    - whitespace collapsed
    - known aliases replaced
    """
    text = _strip_accents(text or "").lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for alias, canonical in _CANONICAL_NAMES.items():
        text = text.replace(alias, canonical)
    return text


def normalize_market_title(title: str) -> str:
    """Public wrapper: normalize a raw market title for comparison."""
    return normalize_text(title)


def normalize_outcome(outcome: str) -> str:
    """
    Map raw outcome strings to canonical 'yes' / 'no' / original.
    Used when comparing YES/NO sides across venues.
    """
    normalized = normalize_text(outcome)
    if normalized in _YES_SYNONYMS:
        return "yes"
    if normalized in _NO_SYNONYMS:
        return "no"
    return normalized


def extract_title_tokens(normalized_title: str) -> list[str]:
    """
    Split normalized title into meaningful tokens.
    - strips noise tokens
    - keeps tokens of length > 2
    """
    return [
        t for t in normalized_title.split()
        if len(t) > 2 and t not in _NOISE_TOKENS
    ]


# ── Similarity scoring ────────────────────────────────────────────────────────

def title_similarity(title_a: str, title_b: str) -> float:
    """
    Jaccard similarity between the token sets of two titles (0.0–1.0).
    Both inputs are raw strings; normalization is applied internally.

    Returns 0.0 when either title is empty or produces no tokens.
    """
    tokens_a = set(extract_title_tokens(normalize_market_title(title_a)))
    tokens_b = set(extract_title_tokens(normalize_market_title(title_b)))
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return round(intersection / union, 4) if union else 0.0


def substring_overlap_score(title_a: str, title_b: str) -> float:
    """
    Secondary signal: check if the shorter title's tokens are a subset of the longer.
    Returns fraction of shorter-title tokens present in the longer title.
    Useful for catching asymmetric rephrasing between venues.
    """
    tokens_a = set(extract_title_tokens(normalize_market_title(title_a)))
    tokens_b = set(extract_title_tokens(normalize_market_title(title_b)))
    if not tokens_a or not tokens_b:
        return 0.0
    smaller, larger = (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    covered = len(smaller & larger)
    return round(covered / len(smaller), 4) if smaller else 0.0


def time_overlap_score(
    start_a: float | None,
    end_a: float | None,
    start_b: float | None,
    end_b: float | None,
) -> float:
    """
    Score (0.0–1.0) representing how much the resolution/event time windows overlap.

    - Returns 0.5 when either window is unknown (neutral: not good, not bad).
    - Returns 0.0 when windows are mutually exclusive.
    - Returns 1.0 when one window is fully contained within the other.
    """
    if None in (start_a, end_a, start_b, end_b):
        return 0.5  # unknown — neutral
    overlap_start = max(start_a, start_b)  # type: ignore[arg-type]
    overlap_end = min(end_a, end_b)        # type: ignore[arg-type]
    if overlap_end <= overlap_start:
        return 0.0
    overlap = overlap_end - overlap_start
    span = max(end_a - start_a, end_b - start_b, 1.0)  # type: ignore[operator]
    return round(min(1.0, overlap / span), 4)


def composite_score(
    title_a: str,
    title_b: str,
    start_a: float | None = None,
    end_a: float | None = None,
    start_b: float | None = None,
    end_b: float | None = None,
) -> dict[str, Any]:
    """
    Compute a composite match confidence from title similarity and time overlap.

    Weights:
    - If time data is present:   60% title Jaccard + 20% substring + 20% time
    - If time data is absent:    70% title Jaccard + 30% substring

    Returns a dict with individual scores and the weighted composite.
    """
    jac = title_similarity(title_a, title_b)
    sub = substring_overlap_score(title_a, title_b)
    time_data_present = None not in (start_a, end_a, start_b, end_b)
    t_score = time_overlap_score(start_a, end_a, start_b, end_b)

    if time_data_present:
        comp = round(0.60 * jac + 0.20 * sub + 0.20 * t_score, 4)
    else:
        comp = round(0.70 * jac + 0.30 * sub, 4)

    return {
        "title_jaccard": jac,
        "substring_overlap": sub,
        "time_overlap_score": t_score,
        "time_data_present": time_data_present,
        "composite_confidence": comp,
        "normalized_a": normalize_market_title(title_a),
        "normalized_b": normalize_market_title(title_b),
    }
