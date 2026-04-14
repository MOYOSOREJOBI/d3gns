from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class WSBSentimentAdapter(BaseAdapter):
    """
    WallStreetBets sentiment via public Reddit JSON + dedicated WSB sentiment API.
    No auth required. Tracks retail options/equity sentiment for macro signals.
    """

    platform_name = "wsb_sentiment"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://tradestie.com/api/v1"

    _REDDIT_BASE = "https://www.reddit.com"
    _REDDIT_HEADERS = {
        "User-Agent": "DeGensResearchBot/1.0 (research; non-commercial)",
    }

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        result = self.get_wsb_stocks()
        if result.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        return self._error("health_failed", result.get("error", "unknown"), auth_truth="no_auth_required")

    def get_wsb_stocks(self) -> dict[str, Any]:
        """
        Fetch WallStreetBets sentiment from tradestie.com (free public API
        that aggregates WSB mentions and sentiment daily).
        """
        try:
            r = self._request("GET", "/apps/reddit/wsb", timeout=10.0)
            raw = r.json()
            stocks = [
                {
                    "ticker": item.get("ticker"),
                    "sentiment": item.get("sentiment"),
                    "sentiment_score": item.get("sentiment_score"),
                    "no_of_comments": item.get("no_of_comments"),
                }
                for item in (raw if isinstance(raw, list) else [])
            ]
            stocks.sort(key=lambda x: x.get("no_of_comments") or 0, reverse=True)
            bullish = [s for s in stocks if s.get("sentiment") == "Bullish"]
            bearish = [s for s in stocks if s.get("sentiment") == "Bearish"]
            return self._ok(
                data={
                    "stocks": stocks[:30],
                    "total": len(stocks),
                    "bullish_count": len(bullish),
                    "bearish_count": len(bearish),
                    "bull_bear_ratio": round(len(bullish) / max(len(bearish), 1), 2),
                    "top_bullish": bullish[:5],
                    "top_bearish": bearish[:5],
                },
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("wsb_fetch_failed", str(exc), auth_truth="no_auth_required")

    def get_wsb_hot_posts(self, limit: int = 25) -> dict[str, Any]:
        """Fallback: fetch WSB hot posts directly from Reddit public JSON."""
        try:
            r = self._request(
                "GET", "/r/wallstreetbets/hot.json",
                base_url=self._REDDIT_BASE,
                params={"limit": limit},
                headers=self._REDDIT_HEADERS,
                timeout=8.0,
            )
            raw = r.json()
            posts = [
                {
                    "title": p["data"].get("title"),
                    "score": p["data"].get("score"),
                    "upvote_ratio": p["data"].get("upvote_ratio"),
                    "num_comments": p["data"].get("num_comments"),
                    "created_utc": p["data"].get("created_utc"),
                    "flair": p["data"].get("link_flair_text"),
                }
                for p in raw.get("data", {}).get("children", [])
                if p.get("kind") == "t3"
            ]
            # Simple keyword sentiment pass
            bullish_kw = {"call", "buy", "yolo", "bull", "moon", "long", "profit", "gain", "squeeze"}
            bearish_kw = {"put", "sell", "short", "bear", "crash", "dump", "loss", "puts", "rekt"}
            bull_count = sum(
                1 for p in posts
                for kw in bullish_kw
                if kw in (p.get("title") or "").lower()
            )
            bear_count = sum(
                1 for p in posts
                for kw in bearish_kw
                if kw in (p.get("title") or "").lower()
            )
            total = bull_count + bear_count
            score = (bull_count - bear_count) / total if total > 0 else 0.0
            return self._ok(
                data={
                    "posts": posts,
                    "count": len(posts),
                    "bullish_mentions": bull_count,
                    "bearish_mentions": bear_count,
                    "sentiment_score": round(score, 3),
                    "classification": "bullish" if score > 0.1 else "bearish" if score < -0.1 else "neutral",
                },
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("wsb_hot_failed", str(exc), auth_truth="no_auth_required")

    def get_combined_signal(self) -> dict[str, Any]:
        """Combine tradestie API + Reddit hot posts for a composite WSB signal."""
        api_res = self.get_wsb_stocks()
        reddit_res = self.get_wsb_hot_posts(limit=25)
        combined: dict[str, Any] = {}
        if api_res.get("ok"):
            combined["api_bull_bear_ratio"] = api_res["data"].get("bull_bear_ratio")
            combined["api_top_bullish"] = api_res["data"].get("top_bullish", [])[:3]
            combined["api_top_bearish"] = api_res["data"].get("top_bearish", [])[:3]
        if reddit_res.get("ok"):
            combined["reddit_sentiment_score"] = reddit_res["data"].get("sentiment_score")
            combined["reddit_classification"] = reddit_res["data"].get("classification")
        # Composite: bias toward API signal, Reddit as tie-breaker
        ratio = combined.get("api_bull_bear_ratio", 1.0)
        reddit_score = combined.get("reddit_sentiment_score", 0.0)
        if ratio and ratio > 1.5 and (reddit_score or 0) > 0:
            combined["composite_signal"] = "risk_on"
        elif ratio and ratio < 0.66 and (reddit_score or 0) < 0:
            combined["composite_signal"] = "risk_off"
        else:
            combined["composite_signal"] = "neutral"
        return self._ok(data=combined, status="ok", auth_truth="no_auth_required")
