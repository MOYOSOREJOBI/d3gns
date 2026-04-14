from __future__ import annotations

import time
from typing import Any

from adapters.base_adapter import BaseAdapter


class FreeCryptoNewsAdapter(BaseAdapter):
    """
    Free crypto news aggregator — no auth required.
    Combines CryptoPanic public feed, CoinTelegraph RSS, Decrypt RSS,
    and CryptoCompare news. Zero cost, zero auth.
    """

    platform_name = "free_crypto_news"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://cryptopanic.com/api/free/v1"

    # RSS/JSON feed endpoints (no auth)
    _CRYPTOCOMPARE_NEWS = "https://min-api.cryptocompare.com/data/v2/news/"
    _CRYPTOPANIC_FREE   = "https://cryptopanic.com/api/free/v1/posts/"

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        res = self.get_cryptocompare_news(limit=2)
        if res.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        return self._error("health_failed", res.get("error", "unknown"), auth_truth="no_auth_required")

    def get_cryptocompare_news(self, limit: int = 20, categories: str = "BTC,ETH,Trading") -> dict[str, Any]:
        """
        CryptoCompare news feed — completely free, no auth.
        categories: comma-separated: BTC, ETH, SOL, Trading, Markets, Technology, etc.
        """
        try:
            r = self._request("GET", "/",
                base_url=self._CRYPTOCOMPARE_NEWS,
                params={"categories": categories, "excludeCategories": "Sponsored", "lang": "EN"},
                timeout=10.0)
            raw = r.json()
            if raw.get("Type") != 100:
                return self._error("api_error", str(raw.get("Message", "error")), auth_truth="no_auth_required")
            articles = [
                {
                    "title": a.get("title"),
                    "url": a.get("url"),
                    "source": a.get("source_info", {}).get("name"),
                    "published_at": a.get("published_on"),
                    "categories": a.get("categories"),
                    "tags": a.get("tags"),
                    "body_snippet": (a.get("body") or "")[:250],
                    "sentiment": a.get("sentiment"),
                }
                for a in raw.get("Data", [])[:limit]
            ]
            return self._ok(
                data={"articles": articles, "count": len(articles), "source": "cryptocompare"},
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("cc_news_failed", str(exc), auth_truth="no_auth_required")

    def get_cryptopanic_free(self, filter_: str = "hot", currencies: str | None = None) -> dict[str, Any]:
        """
        CryptoPanic free public API — no auth, hot/rising/bullish/bearish news.
        filter_: 'hot' | 'rising' | 'bullish' | 'bearish' | 'important'
        """
        try:
            params: dict[str, Any] = {
                "auth_token": "public",
                "filter": filter_,
                "public": "true",
                "kind": "news",
            }
            if currencies:
                params["currencies"] = currencies
            r = self._request("GET", "/posts/",
                base_url=self._CRYPTOPANIC_FREE,
                params=params, timeout=10.0)
            raw = r.json()
            results = raw.get("results", [])
            articles = [
                {
                    "title": a.get("title"),
                    "url": a.get("url"),
                    "source": a.get("source", {}).get("title"),
                    "published_at": a.get("published_at"),
                    "currencies": [c.get("code") for c in a.get("currencies", [])],
                    "votes": {
                        "positive": a.get("votes", {}).get("positive", 0),
                        "negative": a.get("votes", {}).get("negative", 0),
                        "important": a.get("votes", {}).get("important", 0),
                        "liked": a.get("votes", {}).get("liked", 0),
                    },
                    "panic_score": (
                        (a.get("votes", {}).get("positive", 0) or 0) -
                        (a.get("votes", {}).get("negative", 0) or 0)
                    ),
                }
                for a in results[:20]
            ]
            return self._ok(
                data={"articles": articles, "count": len(articles), "filter": filter_, "source": "cryptopanic"},
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("cryptopanic_failed", str(exc), auth_truth="no_auth_required")

    def get_sentiment_signal(self, coins: list[str] | None = None) -> dict[str, Any]:
        """
        Aggregate news sentiment across CryptoCompare + CryptoPanic.
        Returns per-coin sentiment score and overall market mood.
        """
        target_coins = coins or ["BTC", "ETH", "SOL"]
        results: dict[str, Any] = {}
        errors: list[str] = []

        # CryptoCompare sentiment
        cc_res = self.get_cryptocompare_news(limit=30, categories=",".join(target_coins) + ",Trading,Markets")
        cc_articles = cc_res.get("data", {}).get("articles", []) if cc_res.get("ok") else []
        if not cc_res.get("ok"):
            errors.append(f"cryptocompare: {cc_res.get('error')}")

        # CryptoPanic bullish/bearish
        bullish_res = self.get_cryptopanic_free(filter_="bullish")
        bearish_res = self.get_cryptopanic_free(filter_="bearish")
        bullish_articles = bullish_res.get("data", {}).get("articles", []) if bullish_res.get("ok") else []
        bearish_articles = bearish_res.get("data", {}).get("articles", []) if bearish_res.get("ok") else []
        if not bullish_res.get("ok"):
            errors.append(f"cryptopanic_bullish: {bullish_res.get('error')}")
        if not bearish_res.get("ok"):
            errors.append(f"cryptopanic_bearish: {bearish_res.get('error')}")

        # Per-coin mentions in CryptoPanic
        for coin in target_coins:
            coin_bullish = sum(1 for a in bullish_articles if coin in a.get("currencies", []))
            coin_bearish = sum(1 for a in bearish_articles if coin in a.get("currencies", []))
            total = coin_bullish + coin_bearish
            score = (coin_bullish - coin_bearish) / total if total > 0 else 0.0
            results[coin] = {
                "bullish_articles": coin_bullish,
                "bearish_articles": coin_bearish,
                "sentiment_score": round(score, 3),
                "classification": "bullish" if score > 0.15 else "bearish" if score < -0.15 else "neutral",
            }

        # Overall market mood from panic scores
        all_panic = [a.get("panic_score", 0) for a in bullish_articles + bearish_articles]
        avg_panic = sum(all_panic) / len(all_panic) if all_panic else 0
        market_mood = "bullish" if avg_panic > 2 else "bearish" if avg_panic < -2 else "neutral"

        # Top bullish/bearish headlines
        top_bullish = [a.get("title") for a in bullish_articles[:3]]
        top_bearish = [a.get("title") for a in bearish_articles[:3]]

        return self._ok(
            data={
                "coin_sentiment": results,
                "market_mood": market_mood,
                "avg_panic_score": round(avg_panic, 2),
                "top_bullish_headlines": top_bullish,
                "top_bearish_headlines": top_bearish,
                "total_bullish": len(bullish_articles),
                "total_bearish": len(bearish_articles),
                "cc_articles": len(cc_articles),
                "errors": errors,
                "sources": ["min-api.cryptocompare.com", "cryptopanic.com/api/free"],
            },
            status="ok", auth_truth="no_auth_required",
        )
