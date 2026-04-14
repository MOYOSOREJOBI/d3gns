from __future__ import annotations

import time
from typing import Any

from adapters.base_adapter import BaseAdapter


class RedditPublicAdapter(BaseAdapter):
    """Reddit public JSON API — no auth required, real-time social sentiment feeds."""

    platform_name = "reddit_public"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://www.reddit.com"

    _HEADERS = {
        "User-Agent": "DeGensResearchBot/1.0 (research tool; +https://github.com)",
    }

    # Subreddits relevant to prediction markets and research
    SUBREDDITS = {
        "crypto": ["CryptoCurrency", "Bitcoin", "ethereum", "CryptoMarkets", "CryptoMoonShots"],
        "politics": ["politics", "worldnews", "news", "PoliticalDiscussion", "uspolitics"],
        "finance": ["wallstreetbets", "investing", "stocks", "options", "SecurityAnalysis"],
        "sports": ["sports", "nfl", "nba", "soccer", "baseball", "hockey"],
        "prediction_markets": ["PredictionMarkets", "Polymarket", "Kalshi"],
        "macro": ["Economics", "MacroEconomics", "economy", "personalfinance"],
        "ai_tech": ["MachineLearning", "artificial", "technology", "singularity"],
        "elections": ["politics", "moderatepolitics", "NeutralPolitics"],
    }

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        result = self.get_hot(subreddit="news", limit=2)
        if result.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        return self._error("health_failed", result.get("error", "unknown"), auth_truth="no_auth_required")

    def get_hot(self, subreddit: str = "worldnews", limit: int = 25) -> dict[str, Any]:
        """Fetch hot posts from a subreddit."""
        try:
            r = self._request(
                "GET", f"/r/{subreddit}/hot.json",
                params={"limit": limit},
                headers=self._HEADERS,
                timeout=8.0,
            )
            raw = r.json()
            posts = [
                {
                    "title": p["data"].get("title"),
                    "score": p["data"].get("score"),
                    "upvote_ratio": p["data"].get("upvote_ratio"),
                    "num_comments": p["data"].get("num_comments"),
                    "url": p["data"].get("url"),
                    "created_utc": p["data"].get("created_utc"),
                    "flair": p["data"].get("link_flair_text"),
                    "selftext": (p["data"].get("selftext") or "")[:200],
                }
                for p in raw.get("data", {}).get("children", [])
                if p.get("kind") == "t3"
            ]
            return self._ok(
                data={"subreddit": subreddit, "posts": posts, "count": len(posts)},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("hot_failed", str(exc), auth_truth="no_auth_required")

    def get_new(self, subreddit: str = "worldnews", limit: int = 25) -> dict[str, Any]:
        """Fetch newest posts from a subreddit."""
        try:
            r = self._request(
                "GET", f"/r/{subreddit}/new.json",
                params={"limit": limit},
                headers=self._HEADERS,
                timeout=8.0,
            )
            raw = r.json()
            posts = [
                {
                    "title": p["data"].get("title"),
                    "score": p["data"].get("score"),
                    "num_comments": p["data"].get("num_comments"),
                    "created_utc": p["data"].get("created_utc"),
                    "url": p["data"].get("url"),
                }
                for p in raw.get("data", {}).get("children", [])
                if p.get("kind") == "t3"
            ]
            return self._ok(
                data={"subreddit": subreddit, "posts": posts, "count": len(posts)},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("new_failed", str(exc), auth_truth="no_auth_required")

    def search(self, query: str, subreddit: str | None = None, limit: int = 25) -> dict[str, Any]:
        """Search Reddit for posts matching a query."""
        path = f"/r/{subreddit}/search.json" if subreddit else "/search.json"
        try:
            r = self._request(
                "GET", path,
                params={"q": query, "sort": "relevance", "t": "day", "limit": limit},
                headers=self._HEADERS,
                timeout=8.0,
            )
            raw = r.json()
            posts = [
                {
                    "title": p["data"].get("title"),
                    "subreddit": p["data"].get("subreddit"),
                    "score": p["data"].get("score"),
                    "num_comments": p["data"].get("num_comments"),
                    "created_utc": p["data"].get("created_utc"),
                    "url": p["data"].get("url"),
                }
                for p in raw.get("data", {}).get("children", [])
                if p.get("kind") == "t3"
            ]
            return self._ok(
                data={"query": query, "posts": posts, "count": len(posts)},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("search_failed", str(exc), auth_truth="no_auth_required")

    def get_sentiment_score(self, subreddit: str = "CryptoCurrency", limit: int = 50) -> dict[str, Any]:
        """
        Lightweight keyword-based sentiment score from hot posts.
        Returns a -1.0 to +1.0 score based on bullish/bearish term frequency.
        """
        result = self.get_hot(subreddit=subreddit, limit=limit)
        if not result.get("ok"):
            return result
        posts = result["data"].get("posts", [])
        bullish_terms = {"bull", "moon", "pump", "surge", "ath", "buy", "bullish", "rally", "green", "up", "profit", "gain"}
        bearish_terms = {"bear", "dump", "crash", "sell", "bearish", "down", "loss", "fear", "panic", "correction", "red"}
        bull_count = 0
        bear_count = 0
        for post in posts:
            text = (post.get("title") or "").lower() + " " + (post.get("selftext") or "").lower()
            bull_count += sum(1 for t in bullish_terms if t in text)
            bear_count += sum(1 for t in bearish_terms if t in text)
        total = bull_count + bear_count
        score = (bull_count - bear_count) / total if total > 0 else 0.0
        avg_score = sum(p.get("score", 0) for p in posts) / len(posts) if posts else 0
        return self._ok(
            data={
                "subreddit": subreddit,
                "posts_analyzed": len(posts),
                "bullish_signals": bull_count,
                "bearish_signals": bear_count,
                "sentiment_score": round(score, 3),
                "classification": "bullish" if score > 0.1 else "bearish" if score < -0.1 else "neutral",
                "avg_post_score": round(avg_score, 1),
                "noise_warning": "High false-positive rate — use as weak signal only.",
            },
            status="ok",
            auth_truth="no_auth_required",
        )

    def scan_categories(self, categories: list[str] | None = None) -> dict[str, Any]:
        """Scan multiple category subreddits and return aggregated sentiment."""
        cats = categories or ["crypto", "finance", "politics"]
        results = {}
        for cat in cats:
            subs = self.SUBREDDITS.get(cat, [])
            if not subs:
                continue
            # Only scan first subreddit per category to stay within rate limits
            sub = subs[0]
            res = self.get_sentiment_score(subreddit=sub, limit=25)
            results[cat] = {
                "subreddit": sub,
                "score": res["data"].get("sentiment_score") if res.get("ok") else None,
                "classification": res["data"].get("classification") if res.get("ok") else "error",
                "error": res.get("error") if not res.get("ok") else None,
            }
            time.sleep(0.5)  # polite rate limiting
        return self._ok(data={"categories": results}, status="ok", auth_truth="no_auth_required")
