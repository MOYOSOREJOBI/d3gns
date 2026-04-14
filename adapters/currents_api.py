from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class CurrentsAPIAdapter(BaseAdapter):
    """
    Currents API — free tier (600 req/day). Requires CURRENTS_API_KEY.
    Provides latest news from various sources, blogs, and forums worldwide.
    """

    platform_name = "currents_api"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False  # degraded without key
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://api.currentsapi.services/v1"

    def is_configured(self) -> bool:
        return bool(self._setting("CURRENTS_API_KEY", "").strip())

    def healthcheck(self) -> dict[str, Any]:
        if not self.is_configured():
            return self._ok(
                data={"status": "degraded", "note": "Set CURRENTS_API_KEY. Free 600 req/day at currentsapi.services"},
                status="degraded",
                auth_truth="missing",
                degraded_reason="CURRENTS_API_KEY not configured. Register free at currentsapi.services.",
            )
        result = self.get_latest(limit=1)
        if result.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="validated")
        return self._error("health_failed", result.get("error", "unknown"), auth_truth="invalid")

    def get_latest(self, language: str = "en", limit: int = 20, category: str | None = None) -> dict[str, Any]:
        """Fetch latest news articles."""
        if not self.is_configured():
            return self._error("no_key", "CURRENTS_API_KEY not set.", auth_truth="missing")
        params: dict[str, Any] = {
            "language": language,
            "page_size": min(limit, 200),
            "apiKey": self._setting("CURRENTS_API_KEY"),
        }
        if category:
            params["category"] = category
        try:
            r = self._request("GET", "/latest-news", params=params)
            raw = r.json()
            if raw.get("status") != "ok":
                return self._error("api_error", raw.get("message", "API error"), auth_truth="invalid")
            articles = [
                {
                    "title": a.get("title"),
                    "description": (a.get("description") or "")[:200],
                    "url": a.get("url"),
                    "author": a.get("author"),
                    "image": a.get("image"),
                    "language": a.get("language"),
                    "category": a.get("category"),
                    "published": a.get("published"),
                }
                for a in raw.get("news", [])
            ]
            return self._ok(
                data={"articles": articles, "count": len(articles), "category": category},
                status="ok",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error("fetch_failed", str(exc), auth_truth="validated")

    def search(self, keywords: str, language: str = "en", limit: int = 20) -> dict[str, Any]:
        """Search news by keywords."""
        if not self.is_configured():
            return self._error("no_key", "CURRENTS_API_KEY not set.", auth_truth="missing")
        params: dict[str, Any] = {
            "keywords": keywords,
            "language": language,
            "page_size": min(limit, 200),
            "apiKey": self._setting("CURRENTS_API_KEY"),
        }
        try:
            r = self._request("GET", "/search", params=params)
            raw = r.json()
            if raw.get("status") != "ok":
                return self._error("api_error", raw.get("message", "API error"), auth_truth="invalid")
            articles = [
                {
                    "title": a.get("title"),
                    "description": (a.get("description") or "")[:200],
                    "url": a.get("url"),
                    "category": a.get("category"),
                    "published": a.get("published"),
                    "language": a.get("language"),
                }
                for a in raw.get("news", [])
            ]
            return self._ok(
                data={"articles": articles, "count": len(articles), "keywords": keywords},
                status="ok",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error("search_failed", str(exc), auth_truth="validated")

    def get_available_categories(self) -> dict[str, Any]:
        """Get available news categories."""
        categories = [
            "regional", "technology", "lifestyle", "business", "general",
            "programming", "science", "entertainment", "world", "sports",
            "finance", "academia", "politics", "health", "opinion",
            "food", "game", "fashion", "travel", "culture", "environment",
        ]
        return self._ok(data={"categories": categories, "count": len(categories)}, status="ok", auth_truth="no_auth_required")

    def get_finance_signal(self) -> dict[str, Any]:
        """Aggregate finance/business/politics news for prediction market signal."""
        signals = []
        for cat in ["finance", "business", "politics", "technology", "world"]:
            res = self.get_latest(category=cat, limit=10)
            if not res.get("ok"):
                continue
            articles = res["data"].get("articles", [])
            bull_kw = {"record", "growth", "approve", "win", "surge", "rally", "gain", "advance"}
            bear_kw = {"crash", "fall", "reject", "lose", "decline", "drop", "risk", "warn", "concern"}
            bull = sum(1 for a in articles for kw in bull_kw if kw in (a.get("title") or "").lower())
            bear = sum(1 for a in articles for kw in bear_kw if kw in (a.get("title") or "").lower())
            total = bull + bear
            score = (bull - bear) / total if total > 0 else 0.0
            signals.append({
                "category": cat,
                "articles": len(articles),
                "sentiment_score": round(score, 3),
                "classification": "positive" if score > 0.1 else "negative" if score < -0.1 else "neutral",
            })
        return self._ok(data={"categories": signals}, status="ok", auth_truth="validated" if self.is_configured() else "missing")
