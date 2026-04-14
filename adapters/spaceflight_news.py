from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class SpaceflightNewsAdapter(BaseAdapter):
    """Spaceflight News API — no auth, space/tech/science news for prediction markets."""

    platform_name = "spaceflight_news"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://api.spaceflightnewsapi.net/v4"

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        result = self.get_articles(limit=2)
        if result.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        return self._error("health_failed", result.get("error", "unknown"), auth_truth="no_auth_required")

    def get_articles(self, limit: int = 20, search: str | None = None) -> dict[str, Any]:
        """Fetch latest spaceflight news articles."""
        params: dict[str, Any] = {"limit": limit, "ordering": "-published_at"}
        if search:
            params["search"] = search
        try:
            r = self._request("GET", "/articles", params=params)
            raw = r.json()
            articles = [
                {
                    "id": a.get("id"),
                    "title": a.get("title"),
                    "url": a.get("url"),
                    "image_url": a.get("image_url"),
                    "news_site": a.get("news_site"),
                    "summary": (a.get("summary") or "")[:300],
                    "published_at": a.get("published_at"),
                    "launches": [la.get("launch_id") for la in a.get("launches", [])],
                    "events": [ev.get("event_id") for ev in a.get("events", [])],
                }
                for a in raw.get("results", [])
            ]
            return self._ok(
                data={"articles": articles, "count": len(articles), "total": raw.get("count")},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("articles_failed", str(exc), auth_truth="no_auth_required")

    def get_launches(self, limit: int = 10) -> dict[str, Any]:
        """Fetch upcoming/recent launch-related news."""
        return self.get_articles(limit=limit, search="launch")

    def get_reports(self, limit: int = 10) -> dict[str, Any]:
        """Fetch spaceflight reports (more detailed than news articles)."""
        params: dict[str, Any] = {"limit": limit, "ordering": "-published_at"}
        try:
            r = self._request("GET", "/reports", params=params)
            raw = r.json()
            reports = [
                {
                    "title": a.get("title"),
                    "url": a.get("url"),
                    "news_site": a.get("news_site"),
                    "summary": (a.get("summary") or "")[:400],
                    "published_at": a.get("published_at"),
                }
                for a in raw.get("results", [])
            ]
            return self._ok(
                data={"reports": reports, "count": len(reports)},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("reports_failed", str(exc), auth_truth="no_auth_required")

    def get_signal_for_markets(self, topics: list[str] | None = None) -> dict[str, Any]:
        """
        Search for space/tech news relevant to prediction market topics.
        Topics like 'SpaceX', 'NASA', 'Starship', 'Mars', 'Moon', 'satellite'.
        """
        targets = topics or ["SpaceX", "NASA", "Starship", "lunar", "Mars", "satellite launch"]
        signals = []
        for topic in targets[:4]:
            res = self.get_articles(limit=5, search=topic)
            if res.get("ok") and res["data"].get("count", 0) > 0:
                signals.append({
                    "topic": topic,
                    "article_count": res["data"].get("count"),
                    "latest": res["data"]["articles"][0] if res["data"]["articles"] else None,
                })
        return self._ok(
            data={"signals": signals, "topics_searched": len(targets)},
            status="ok",
            auth_truth="no_auth_required",
        )
