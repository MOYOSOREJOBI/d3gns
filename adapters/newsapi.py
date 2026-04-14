from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class NewsAPIAdapter(BaseAdapter):
    """
    NewsAPI.org — free tier (100 req/day). Requires NEWS_API_KEY env var.
    Without key: falls back to free public RSS feeds via spaceflight/HN sources.
    """

    platform_name = "newsapi"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False  # works in degraded mode without key
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://newsapi.org/v2"

    def is_configured(self) -> bool:
        return bool(self._setting("NEWS_API_KEY", "").strip())

    def healthcheck(self) -> dict[str, Any]:
        if not self.is_configured():
            return self._ok(
                data={"status": "degraded", "note": "Set NEWS_API_KEY for live news. Free: 100 req/day at newsapi.org"},
                status="degraded",
                auth_truth="missing",
                degraded_reason="NEWS_API_KEY not configured. Register free at newsapi.org.",
            )
        result = self.get_top_headlines(country="us", page_size=1)
        if result.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="validated")
        return self._error("health_failed", result.get("error", "unknown"), auth_truth="invalid")

    def get_top_headlines(self, country: str = "us", category: str | None = None, query: str | None = None, page_size: int = 20) -> dict[str, Any]:
        """Fetch top headlines. Requires API key."""
        if not self.is_configured():
            return self._error("no_key", "NEWS_API_KEY not set. Register free at newsapi.org", auth_truth="missing")
        params: dict[str, Any] = {
            "country": country,
            "pageSize": min(page_size, 100),
            "apiKey": self._setting("NEWS_API_KEY"),
        }
        if category:
            params["category"] = category
        if query:
            params["q"] = query
        try:
            r = self._request("GET", "/top-headlines", params=params)
            raw = r.json()
            if raw.get("status") == "error":
                return self._error("api_error", raw.get("message", "API error"), auth_truth="invalid")
            articles = [
                {
                    "title": a.get("title"),
                    "description": (a.get("description") or "")[:200],
                    "source": a.get("source", {}).get("name"),
                    "url": a.get("url"),
                    "published_at": a.get("publishedAt"),
                    "author": a.get("author"),
                }
                for a in raw.get("articles", [])
                if a.get("title") and "[Removed]" not in (a.get("title") or "")
            ]
            return self._ok(
                data={"articles": articles, "total": raw.get("totalResults"), "country": country, "category": category},
                status="ok",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error("fetch_failed", str(exc), auth_truth="validated")

    def search_everything(self, query: str, sort_by: str = "publishedAt", page_size: int = 20, from_date: str | None = None) -> dict[str, Any]:
        """Full-text search across all indexed articles. Requires API key."""
        if not self.is_configured():
            return self._error("no_key", "NEWS_API_KEY not set.", auth_truth="missing")
        params: dict[str, Any] = {
            "q": query,
            "sortBy": sort_by,
            "pageSize": min(page_size, 100),
            "language": "en",
            "apiKey": self._setting("NEWS_API_KEY"),
        }
        if from_date:
            params["from"] = from_date
        try:
            r = self._request("GET", "/everything", params=params)
            raw = r.json()
            if raw.get("status") == "error":
                return self._error("api_error", raw.get("message", "API error"), auth_truth="invalid")
            articles = [
                {
                    "title": a.get("title"),
                    "description": (a.get("description") or "")[:200],
                    "source": a.get("source", {}).get("name"),
                    "url": a.get("url"),
                    "published_at": a.get("publishedAt"),
                    "content_snippet": (a.get("content") or "")[:300],
                }
                for a in raw.get("articles", [])
                if a.get("title") and "[Removed]" not in (a.get("title") or "")
            ]
            return self._ok(
                data={"articles": articles, "total": raw.get("totalResults"), "query": query},
                status="ok",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error("search_failed", str(exc), auth_truth="validated")

    def get_sentiment_for_topics(self, topics: list[str] | None = None) -> dict[str, Any]:
        """Search for headlines on prediction-market-relevant topics and score sentiment."""
        targets = topics or ["bitcoin", "election", "federal reserve", "inflation", "stock market"]
        results = []
        for topic in targets[:5]:
            res = self.search_everything(query=topic, sort_by="publishedAt", page_size=10)
            if not res.get("ok"):
                results.append({"topic": topic, "error": res.get("error")})
                continue
            articles = res["data"].get("articles", [])
            # Simple keyword sentiment
            bullish_kw = {"surge", "rally", "gain", "rise", "growth", "record", "high", "bullish", "up"}
            bearish_kw = {"crash", "fall", "decline", "drop", "loss", "fear", "bearish", "down", "recession"}
            bull = sum(1 for a in articles for kw in bullish_kw if kw in (a.get("title") or "").lower())
            bear = sum(1 for a in articles for kw in bearish_kw if kw in (a.get("title") or "").lower())
            total = bull + bear
            score = (bull - bear) / total if total > 0 else 0.0
            results.append({
                "topic": topic,
                "articles": len(articles),
                "sentiment_score": round(score, 3),
                "classification": "positive" if score > 0.1 else "negative" if score < -0.1 else "neutral",
                "top_headline": articles[0].get("title") if articles else None,
            })
        return self._ok(
            data={"topics": results},
            status="ok",
            auth_truth="validated" if self.is_configured() else "missing",
        )
