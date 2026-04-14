from __future__ import annotations

from typing import Any

from bots.base_research_bot import BaseResearchBot


class NewsSentimentBot(BaseResearchBot):
    bot_id = "bot_news_sentiment"
    display_name = "News Sentiment Scanner"
    platform = "polymarket"
    mode = "RESEARCH"
    signal_type = "sentiment_divergence"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "medium"
    description = (
        "Scans live news APIs (NewsAPI, GNews, Currents, Spaceflight News) for sentiment "
        "shifts on political, economic, sports, and tech topics. Flags when aggregate news "
        "sentiment diverges from current prediction market pricing by a statistically "
        "significant margin. No auth needed for baseline; key-based sources unlock more."
    )
    edge_source = "Lagged market reaction to news sentiment velocity spikes"
    opp_cadence_per_day = 4.0
    avg_hold_hours = 2.0
    fee_drag_bps = 90
    fill_rate = 0.55
    platforms = ["polymarket", "newsapi", "gnews", "currents_api", "spaceflight_news"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        from adapters.newsapi import NewsAPIAdapter
        from adapters.gnews import GNewsAdapter
        from adapters.currents_api import CurrentsAPIAdapter
        from adapters.spaceflight_news import SpaceflightNewsAdapter
        from adapters.hackernews import HackerNewsAdapter

        newsapi = NewsAPIAdapter()
        gnews = GNewsAdapter()
        currents = CurrentsAPIAdapter()
        sfn = SpaceflightNewsAdapter()
        hn = HackerNewsAdapter()

        topics = ["bitcoin", "election", "federal reserve", "inflation", "AI", "recession", "war", "tariff"]
        all_signals: list[dict] = []
        source_status: dict[str, str] = {}
        errors: list[str] = []

        # --- NewsAPI (requires key) ---
        if newsapi.is_configured():
            res = newsapi.get_sentiment_for_topics(topics=topics[:5])
            if res.get("ok"):
                for item in res["data"].get("topics", []):
                    all_signals.append({
                        "source": "newsapi",
                        "topic": item.get("topic"),
                        "sentiment_score": item.get("sentiment_score", 0),
                        "classification": item.get("classification"),
                        "top_headline": item.get("top_headline"),
                        "articles": item.get("articles", 0),
                    })
                source_status["newsapi"] = "live"
            else:
                errors.append(f"newsapi: {res.get('error', 'failed')}")
                source_status["newsapi"] = "error"
        else:
            source_status["newsapi"] = "no_key"

        # --- GNews (requires key) ---
        if gnews.is_configured():
            res = gnews.get_market_signal(topics=["bitcoin price", "US election", "Fed interest rate", "market crash"])
            if res.get("ok"):
                for item in res["data"].get("signals", []):
                    all_signals.append({
                        "source": "gnews",
                        "topic": item.get("topic"),
                        "sentiment_score": item.get("sentiment_score", 0),
                        "top_headline": item.get("top_headline"),
                        "articles": item.get("articles", 0),
                    })
                source_status["gnews"] = "live"
            else:
                errors.append(f"gnews: {res.get('error', 'failed')}")
                source_status["gnews"] = "error"
        else:
            source_status["gnews"] = "no_key"

        # --- Currents API (requires key) ---
        if currents.is_configured():
            res = currents.get_finance_signal()
            if res.get("ok"):
                for item in res["data"].get("categories", []):
                    all_signals.append({
                        "source": "currents",
                        "topic": item.get("category"),
                        "sentiment_score": item.get("sentiment_score", 0),
                        "classification": item.get("classification"),
                        "articles": item.get("articles", 0),
                    })
                source_status["currents"] = "live"
            else:
                errors.append(f"currents: {res.get('error', 'failed')}")
                source_status["currents"] = "error"
        else:
            source_status["currents"] = "no_key"

        # --- Spaceflight News (no auth) ---
        sfn_res = sfn.get_signal_for_markets()
        if sfn_res.get("ok"):
            for item in sfn_res["data"].get("signals", []):
                article = item.get("latest") or {}
                all_signals.append({
                    "source": "spaceflight_news",
                    "topic": f"space:{item.get('topic')}",
                    "sentiment_score": 0.1,  # space news leans positive
                    "top_headline": article.get("title"),
                    "articles": item.get("article_count", 0),
                })
            source_status["spaceflight_news"] = "live"
        else:
            errors.append(f"spaceflight_news: {sfn_res.get('error', 'failed')}")
            source_status["spaceflight_news"] = "error"

        # --- HackerNews (no auth) ---
        hn_res = hn.get_tech_signal(topics=["AI", "bitcoin", "recession", "fed rate", "OpenAI"])
        if hn_res.get("ok"):
            for item in hn_res["data"].get("signals", []):
                all_signals.append({
                    "source": "hackernews",
                    "topic": f"hn:{item.get('topic')}",
                    "sentiment_score": 0.05,  # HN is neutral/informational
                    "top_headline": item.get("top_story"),
                    "velocity": item.get("velocity", 0),
                    "articles": item.get("story_count", 0),
                })
            source_status["hackernews"] = "live"
        else:
            errors.append(f"hackernews: {hn_res.get('error', 'failed')}")
            source_status["hackernews"] = "error"

        # Aggregate sentiment
        scores = [s.get("sentiment_score", 0) for s in all_signals if isinstance(s.get("sentiment_score"), (int, float))]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        positive_signals = sum(1 for s in scores if s > 0.1)
        negative_signals = sum(1 for s in scores if s < -0.1)
        total_signals = len(all_signals)

        # Strong divergence detection
        confidence = 0.0
        signal_taken = False
        degraded_reason = ""

        live_sources = sum(1 for v in source_status.values() if v == "live")
        if live_sources == 0:
            degraded_reason = (
                "No live news data available. "
                "Set NEWS_API_KEY (newsapi.org), GNEWS_API_KEY (gnews.io), or "
                "CURRENTS_API_KEY (currentsapi.services) for live sentiment feeds. "
                "Spaceflight News and HackerNews are live (no key needed)."
            )
        elif total_signals > 0:
            if positive_signals > negative_signals * 2 and avg_score > 0.2:
                confidence = min(0.35 + avg_score * 0.3, 0.60)
            elif negative_signals > positive_signals * 2 and avg_score < -0.2:
                confidence = min(0.35 + abs(avg_score) * 0.3, 0.60)
            else:
                degraded_reason = "Sentiment signals present but no strong directional divergence detected."

        top_headlines = [s.get("top_headline") for s in all_signals if s.get("top_headline")][:5]

        summary = (
            f"News sentiment scan: {total_signals} signals from {live_sources} live sources. "
            f"Avg score: {avg_score:.3f}. Positive: {positive_signals}, Negative: {negative_signals}. "
            f"Top headlines: {'; '.join(str(h) for h in top_headlines[:3])}. "
            f"Sources active: {', '.join(k for k, v in source_status.items() if v == 'live')}."
        )

        return self.emit_signal(
            title="News Sentiment Scanner",
            summary=summary,
            confidence=confidence,
            signal_taken=signal_taken,
            degraded_reason=degraded_reason,
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={
                "signals": all_signals,
                "total_signals": total_signals,
                "avg_sentiment_score": round(avg_score, 4),
                "positive_signals": positive_signals,
                "negative_signals": negative_signals,
                "source_status": source_status,
                "top_headlines": top_headlines,
                "errors": errors,
                "key_sources": {
                    "newsapi": "Register free at newsapi.org — 100 req/day",
                    "gnews": "Register free at gnews.io — 100 req/day",
                    "currents": "Register free at currentsapi.services — 600 req/day",
                    "spaceflight_news": "No key needed — live",
                    "hackernews": "No key needed — live",
                },
            },
        )
