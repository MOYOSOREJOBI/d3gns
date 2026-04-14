from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class HackerNewsAdapter(BaseAdapter):
    """HackerNews Algolia API — no auth, tech/startup/finance sentiment from HN."""

    platform_name = "hackernews"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://hn.algolia.com/api/v1"

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        result = self.get_top_stories(limit=2)
        if result.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        return self._error("health_failed", result.get("error", "unknown"), auth_truth="no_auth_required")

    def get_top_stories(self, limit: int = 30) -> dict[str, Any]:
        """Fetch top HN stories by score."""
        try:
            r = self._request(
                "GET", "/search",
                params={
                    "tags": "story",
                    "hitsPerPage": limit,
                    "numericFilters": "points>50",
                },
            )
            raw = r.json()
            stories = [
                {
                    "title": h.get("title"),
                    "url": h.get("url"),
                    "score": h.get("points"),
                    "num_comments": h.get("num_comments"),
                    "author": h.get("author"),
                    "created_at": h.get("created_at"),
                    "hn_id": h.get("objectID"),
                }
                for h in raw.get("hits", [])
            ]
            return self._ok(
                data={"stories": stories, "count": len(stories)},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("top_stories_failed", str(exc), auth_truth="no_auth_required")

    def search(self, query: str, limit: int = 25, hours_back: int = 24) -> dict[str, Any]:
        """Search HN stories and comments for a query."""
        import time
        since = int(time.time()) - (hours_back * 3600)
        try:
            r = self._request(
                "GET", "/search",
                params={
                    "query": query,
                    "tags": "story",
                    "hitsPerPage": limit,
                    "numericFilters": f"created_at_i>{since}",
                },
            )
            raw = r.json()
            hits = [
                {
                    "title": h.get("title"),
                    "url": h.get("url"),
                    "score": h.get("points"),
                    "num_comments": h.get("num_comments"),
                    "created_at": h.get("created_at"),
                }
                for h in raw.get("hits", [])
            ]
            return self._ok(
                data={"query": query, "hits": hits, "count": len(hits), "hours_back": hours_back},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("search_failed", str(exc), auth_truth="no_auth_required")

    def get_tech_signal(self, topics: list[str] | None = None) -> dict[str, Any]:
        """Scan HN for trending tech/market topics relevant to prediction markets."""
        targets = topics or ["AI", "OpenAI", "bitcoin", "recession", "fed rate", "election", "inflation"]
        signals = []
        for topic in targets[:5]:  # limit to avoid excessive requests
            res = self.search(query=topic, limit=10, hours_back=24)
            if res.get("ok"):
                hits = res["data"].get("hits", [])
                total_score = sum(h.get("score") or 0 for h in hits)
                total_comments = sum(h.get("num_comments") or 0 for h in hits)
                if hits:
                    signals.append({
                        "topic": topic,
                        "story_count": len(hits),
                        "total_points": total_score,
                        "total_comments": total_comments,
                        "velocity": total_score + total_comments,
                        "top_story": hits[0].get("title") if hits else None,
                    })
        signals.sort(key=lambda x: x.get("velocity", 0), reverse=True)
        return self._ok(
            data={"signals": signals, "trending_topic": signals[0]["topic"] if signals else None},
            status="ok",
            auth_truth="no_auth_required",
        )
