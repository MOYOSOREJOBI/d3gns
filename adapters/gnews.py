from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class GNewsAdapter(BaseAdapter):
    """
    GNews API — free tier (100 req/day, 10 articles/req). Requires GNEWS_API_KEY.
    Alternative to NewsAPI with broader international coverage.
    """

    platform_name = "gnews"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False  # degraded without key
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://gnews.io/api/v4"

    def is_configured(self) -> bool:
        return bool(self._setting("GNEWS_API_KEY", "").strip())

    def healthcheck(self) -> dict[str, Any]:
        if not self.is_configured():
            return self._ok(
                data={"status": "degraded", "note": "Set GNEWS_API_KEY. Free: 100 req/day at gnews.io"},
                status="degraded",
                auth_truth="missing",
                degraded_reason="GNEWS_API_KEY not configured. Register free at gnews.io.",
            )
        result = self.get_top_headlines(lang="en", max_articles=1)
        if result.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="validated")
        return self._error("health_failed", result.get("error", "unknown"), auth_truth="invalid")

    def get_top_headlines(self, topic: str | None = None, lang: str = "en", country: str | None = None, max_articles: int = 10) -> dict[str, Any]:
        """Fetch top headlines by topic. topic: breaking-news, world, nation, business, technology, entertainment, sports, science, health."""
        if not self.is_configured():
            return self._error("no_key", "GNEWS_API_KEY not set. Register free at gnews.io", auth_truth="missing")
        params: dict[str, Any] = {
            "lang": lang,
            "max": min(max_articles, 10),
            "apikey": self._setting("GNEWS_API_KEY"),
        }
        if topic:
            params["topic"] = topic
        if country:
            params["country"] = country
        try:
            r = self._request("GET", "/top-headlines", params=params)
            raw = r.json()
            if "errors" in raw:
                return self._error("api_error", str(raw.get("errors")), auth_truth="invalid")
            articles = [
                {
                    "title": a.get("title"),
                    "description": (a.get("description") or "")[:200],
                    "content": (a.get("content") or "")[:300],
                    "url": a.get("url"),
                    "image": a.get("image"),
                    "published_at": a.get("publishedAt"),
                    "source_name": a.get("source", {}).get("name"),
                    "source_url": a.get("source", {}).get("url"),
                }
                for a in raw.get("articles", [])
            ]
            return self._ok(
                data={"articles": articles, "total": raw.get("totalArticles"), "topic": topic, "lang": lang},
                status="ok",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error("fetch_failed", str(exc), auth_truth="validated")

    def search(self, query: str, lang: str = "en", max_articles: int = 10, sort_by: str = "publishedAt") -> dict[str, Any]:
        """Search for news articles matching a query."""
        if not self.is_configured():
            return self._error("no_key", "GNEWS_API_KEY not set.", auth_truth="missing")
        params: dict[str, Any] = {
            "q": query,
            "lang": lang,
            "max": min(max_articles, 10),
            "sortby": sort_by,
            "apikey": self._setting("GNEWS_API_KEY"),
        }
        try:
            r = self._request("GET", "/search", params=params)
            raw = r.json()
            if "errors" in raw:
                return self._error("api_error", str(raw.get("errors")), auth_truth="invalid")
            articles = [
                {
                    "title": a.get("title"),
                    "description": (a.get("description") or "")[:200],
                    "url": a.get("url"),
                    "published_at": a.get("publishedAt"),
                    "source_name": a.get("source", {}).get("name"),
                }
                for a in raw.get("articles", [])
            ]
            return self._ok(
                data={"articles": articles, "total": raw.get("totalArticles"), "query": query},
                status="ok",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error("search_failed", str(exc), auth_truth="validated")

    def get_market_signal(self, topics: list[str] | None = None) -> dict[str, Any]:
        """Get news sentiment signal for prediction market topics."""
        targets = topics or ["crypto bitcoin", "US election", "federal reserve rate", "war", "AI technology"]
        results = []
        for topic in targets[:4]:
            res = self.search(query=topic, max_articles=10)
            if not res.get("ok"):
                continue
            articles = res["data"].get("articles", [])
            bull_kw = {"win", "surge", "gain", "rise", "record", "approve", "advance", "growth"}
            bear_kw = {"crash", "fail", "decline", "drop", "loss", "reject", "fall", "concern"}
            bull = sum(1 for a in articles for kw in bull_kw if kw in (a.get("title") or "").lower())
            bear = sum(1 for a in articles for kw in bear_kw if kw in (a.get("title") or "").lower())
            total = bull + bear
            score = (bull - bear) / total if total > 0 else 0.0
            results.append({
                "topic": topic,
                "articles": len(articles),
                "sentiment_score": round(score, 3),
                "top_headline": articles[0].get("title") if articles else None,
            })
        return self._ok(data={"signals": results}, status="ok", auth_truth="validated" if self.is_configured() else "missing")
