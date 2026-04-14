"""
VADER Sentiment Analysis service — runs fully locally, zero API calls.
Uses the vaderSentiment library (lexicon-based, tuned for social media text).
Falls back to a lightweight keyword scorer if vaderSentiment is not installed.
"""
from __future__ import annotations

import re
from typing import Any


# ── VADER (preferred) ────────────────────────────────────────────────────────
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer as _VADER
    _VADER_AVAILABLE = True
except ImportError:  # pragma: no cover
    _VADER_AVAILABLE = False


# ── Lightweight fallback lexicon ─────────────────────────────────────────────
_BULL_WORDS = {
    "bull", "bullish", "moon", "pump", "surge", "rally", "gain", "rise", "profit",
    "win", "winning", "ath", "high", "record", "buy", "long", "green", "up",
    "growth", "boom", "soar", "skyrocket", "explode", "breakout", "outperform",
    "approve", "approval", "advance", "positive", "optimistic", "exceed", "beat",
    "above", "recover", "rebound", "strong", "strength", "confidence",
}
_BEAR_WORDS = {
    "bear", "bearish", "dump", "crash", "decline", "fall", "loss", "drop", "sell",
    "short", "red", "down", "recession", "panic", "fear", "correction", "collapse",
    "plunge", "tank", "miss", "fail", "failure", "weak", "concern", "risk",
    "warning", "warn", "reject", "rejection", "ban", "restrict", "regulation",
    "lawsuit", "hack", "breach", "fraud", "scam", "rug", "rekt", "liquidation",
}


class VaderSentimentService:
    """
    Local sentiment scoring service.
    Primary: VADER (vaderSentiment library).
    Fallback: lightweight keyword lexicon.
    """

    def __init__(self) -> None:
        self._analyzer = _VADER() if _VADER_AVAILABLE else None

    @property
    def backend(self) -> str:
        return "vader" if self._analyzer else "keyword_fallback"

    def score_text(self, text: str) -> dict[str, Any]:
        """
        Score a single text string.
        Returns compound score in [-1.0, +1.0] plus pos/neu/neg breakdown.
        """
        if not text or not text.strip():
            return {"compound": 0.0, "pos": 0.0, "neu": 1.0, "neg": 0.0, "classification": "neutral", "backend": self.backend}

        if self._analyzer:
            scores = self._analyzer.polarity_scores(text)
            compound = scores["compound"]
        else:
            compound = self._keyword_score(text)
            scores = {}

        classification = (
            "positive" if compound >= 0.05
            else "negative" if compound <= -0.05
            else "neutral"
        )
        return {
            "compound": round(compound, 4),
            "pos": round(scores.get("pos", max(compound, 0)), 4),
            "neu": round(scores.get("neu", 1 - abs(compound)), 4),
            "neg": round(scores.get("neg", max(-compound, 0)), 4),
            "classification": classification,
            "backend": self.backend,
        }

    def score_batch(self, texts: list[str]) -> list[dict[str, Any]]:
        """Score a list of texts. Returns list of score dicts."""
        return [self.score_text(t) for t in texts]

    def score_headlines(self, headlines: list[str]) -> dict[str, Any]:
        """
        Score a list of news headlines and return aggregate statistics.
        Useful for news sentiment aggregation across many articles.
        """
        if not headlines:
            return {
                "count": 0, "avg_compound": 0.0, "positive_pct": 0.0,
                "negative_pct": 0.0, "neutral_pct": 0.0,
                "classification": "neutral", "backend": self.backend,
                "scores": [],
            }
        scores = self.score_batch(headlines)
        compounds = [s["compound"] for s in scores]
        avg = sum(compounds) / len(compounds)
        pos_count = sum(1 for s in scores if s["classification"] == "positive")
        neg_count = sum(1 for s in scores if s["classification"] == "negative")
        neu_count = len(scores) - pos_count - neg_count
        n = len(scores)
        return {
            "count": n,
            "avg_compound": round(avg, 4),
            "positive_pct": round(pos_count / n * 100, 1),
            "negative_pct": round(neg_count / n * 100, 1),
            "neutral_pct": round(neu_count / n * 100, 1),
            "max_positive": max(scores, key=lambda s: s["compound"])["compound"],
            "max_negative": min(scores, key=lambda s: s["compound"])["compound"],
            "classification": "positive" if avg >= 0.05 else "negative" if avg <= -0.05 else "neutral",
            "backend": self.backend,
            "scores": scores,
        }

    def score_reddit_posts(self, posts: list[dict[str, Any]], title_key: str = "title", body_key: str = "selftext") -> dict[str, Any]:
        """Score Reddit posts using title + body text."""
        texts = []
        for p in posts:
            title = str(p.get(title_key) or "")
            body  = str(p.get(body_key) or "")[:200]
            texts.append(f"{title} {body}".strip())
        return self.score_headlines(texts)

    def compare_sources(self, source_texts: dict[str, list[str]]) -> dict[str, Any]:
        """
        Compare sentiment across multiple sources.
        source_texts: {source_name: [list of headlines/texts]}
        Returns per-source scores + cross-source agreement signal.
        """
        per_source: dict[str, Any] = {}
        for source, texts in source_texts.items():
            per_source[source] = self.score_headlines(texts)

        compounds = [v["avg_compound"] for v in per_source.values()]
        if not compounds:
            return {"per_source": per_source, "agreement": "no_data", "overall_compound": 0.0}

        overall = sum(compounds) / len(compounds)
        all_positive = all(c > 0.05 for c in compounds)
        all_negative = all(c < -0.05 for c in compounds)
        agreement = "strong_positive" if all_positive else "strong_negative" if all_negative else "mixed"
        return {
            "per_source": per_source,
            "overall_compound": round(overall, 4),
            "agreement": agreement,
            "source_count": len(per_source),
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _keyword_score(self, text: str) -> float:
        """Lightweight keyword-based fallback scorer."""
        words = set(re.findall(r"\b[a-z]+\b", text.lower()))
        bull = len(words & _BULL_WORDS)
        bear = len(words & _BEAR_WORDS)
        total = bull + bear
        if total == 0:
            return 0.0
        return round((bull - bear) / total, 4)


# Module-level singleton
_service: VaderSentimentService | None = None


def get_service() -> VaderSentimentService:
    global _service
    if _service is None:
        _service = VaderSentimentService()
    return _service


def score(text: str) -> dict[str, Any]:
    """Convenience: score a single text string."""
    return get_service().score_text(text)


def score_headlines(headlines: list[str]) -> dict[str, Any]:
    """Convenience: score a list of headlines."""
    return get_service().score_headlines(headlines)
