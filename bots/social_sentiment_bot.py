from __future__ import annotations

from typing import Any

from bots.base_research_bot import BaseResearchBot


class SocialSentimentBot(BaseResearchBot):
    bot_id = "bot_social_sentiment"
    display_name = "Social Sentiment Monitor"
    platform = "polymarket"
    mode = "RESEARCH"
    signal_type = "sentiment_divergence"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "C"
    risk_tier = "medium"
    description = (
        "Monitors Reddit public JSON feeds (no auth), HackerNews Algolia API (no auth), "
        "and WallStreetBets via tradestie.com public API. Detects rapid sentiment velocity "
        "spikes that may precede prediction market repricing. High noise, use as filter only."
    )
    edge_source = "Social velocity spikes as weak leading indicators for prediction market repricing"
    opp_cadence_per_day = 6.0
    avg_hold_hours = 0.5
    fee_drag_bps = 200
    fill_rate = 0.35
    platforms = ["polymarket", "reddit_public", "hackernews", "wsb_sentiment"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        from adapters.reddit_public import RedditPublicAdapter
        from adapters.hackernews import HackerNewsAdapter
        from adapters.wsb_sentiment import WSBSentimentAdapter

        reddit = RedditPublicAdapter()
        hn = HackerNewsAdapter()
        wsb = WSBSentimentAdapter()

        errors: list[str] = []
        source_status: dict[str, str] = {}
        all_signals: list[dict] = []

        # --- Reddit: scan multiple categories ---
        reddit_res = reddit.scan_categories(categories=["crypto", "finance", "politics"])
        if reddit_res.get("ok"):
            cats = reddit_res["data"].get("categories", {})
            for cat, data in cats.items():
                score = data.get("score")
                if score is not None:
                    all_signals.append({
                        "source": f"reddit/{data.get('subreddit', cat)}",
                        "topic": cat,
                        "sentiment_score": score,
                        "classification": data.get("classification", "neutral"),
                    })
            source_status["reddit"] = "live"
        else:
            errors.append(f"reddit: {reddit_res.get('error', 'failed')}")
            source_status["reddit"] = "error"

        # --- HackerNews: tech signal ---
        hn_res = hn.get_tech_signal(topics=["AI", "bitcoin", "OpenAI", "recession", "interest rate"])
        hn_trending = None
        if hn_res.get("ok"):
            signals = hn_res["data"].get("signals", [])
            hn_trending = hn_res["data"].get("trending_topic")
            for s in signals[:3]:
                all_signals.append({
                    "source": "hackernews",
                    "topic": s.get("topic"),
                    "sentiment_score": 0.05,  # HN is info-neutral
                    "velocity": s.get("velocity", 0),
                    "top_story": s.get("top_story"),
                })
            source_status["hackernews"] = "live"
        else:
            errors.append(f"hackernews: {hn_res.get('error', 'failed')}")
            source_status["hackernews"] = "error"

        # --- WallStreetBets: combined signal ---
        wsb_res = wsb.get_combined_signal()
        wsb_signal = "neutral"
        wsb_ratio = None
        if wsb_res.get("ok"):
            wsb_signal = wsb_res["data"].get("composite_signal", "neutral")
            wsb_ratio = wsb_res["data"].get("api_bull_bear_ratio")
            reddit_wsb_score = wsb_res["data"].get("reddit_sentiment_score", 0) or 0
            all_signals.append({
                "source": "wsb_tradestie",
                "topic": "wallstreetbets",
                "sentiment_score": reddit_wsb_score,
                "composite_signal": wsb_signal,
                "bull_bear_ratio": wsb_ratio,
            })
            source_status["wsb"] = "live"
        else:
            errors.append(f"wsb: {wsb_res.get('error', 'failed')}")
            source_status["wsb"] = "error"

        # Reddit specific: crypto and politics deep scan
        crypto_sub = reddit.get_sentiment_score(subreddit="CryptoCurrency", limit=50)
        if crypto_sub.get("ok"):
            all_signals.append({
                "source": "reddit/CryptoCurrency",
                "topic": "crypto_deep",
                "sentiment_score": crypto_sub["data"].get("sentiment_score", 0),
                "classification": crypto_sub["data"].get("classification"),
                "posts_analyzed": crypto_sub["data"].get("posts_analyzed"),
            })

        politics_sub = reddit.get_sentiment_score(subreddit="politics", limit=25)
        if politics_sub.get("ok"):
            all_signals.append({
                "source": "reddit/politics",
                "topic": "politics_deep",
                "sentiment_score": politics_sub["data"].get("sentiment_score", 0),
                "classification": politics_sub["data"].get("classification"),
            })

        # Aggregate
        scores = [s.get("sentiment_score", 0) for s in all_signals if isinstance(s.get("sentiment_score"), (int, float))]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        positive = sum(1 for s in scores if s > 0.1)
        negative = sum(1 for s in scores if s < -0.1)
        live_count = sum(1 for v in source_status.values() if v == "live")

        # Velocity spike detection
        confidence = 0.0
        signal_taken = False
        degraded_reason = ""

        if live_count == 0:
            degraded_reason = "All social sources failed. Check network connectivity."
        else:
            # Only signal on extreme consensus (high-noise source requires more agreement)
            if positive >= 3 and avg_score > 0.25 and wsb_signal == "risk_on":
                confidence = 0.22 + avg_score * 0.1
            elif negative >= 3 and avg_score < -0.25 and wsb_signal == "risk_off":
                confidence = 0.22 + abs(avg_score) * 0.1
            else:
                degraded_reason = "No high-velocity social sentiment spike detected. Normal noise level."

        summary = (
            f"Social sentiment scan: {len(all_signals)} signals from {live_count} live sources. "
            f"Avg score: {avg_score:.3f} (pos: {positive}, neg: {negative}). "
            f"WSB composite: {wsb_signal} (bull/bear ratio: {wsb_ratio}). "
            f"HN trending: {hn_trending}. "
            f"Note: Very high noise — use as filter only, not primary signal."
        )

        return self.emit_signal(
            title="Social Sentiment Monitor",
            summary=summary,
            confidence=confidence,
            signal_taken=signal_taken,
            degraded_reason=degraded_reason,
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={
                "signals": all_signals,
                "avg_sentiment_score": round(avg_score, 4),
                "positive_signals": positive,
                "negative_signals": negative,
                "wsb_composite_signal": wsb_signal,
                "wsb_bull_bear_ratio": wsb_ratio,
                "hn_trending_topic": hn_trending,
                "source_status": source_status,
                "errors": errors,
                "noise_warning": "Very high false-positive rate — use as weak corroborating filter only.",
                "sources": [
                    "reddit.com (public JSON, no auth)",
                    "hn.algolia.com/api/v1 (no auth)",
                    "tradestie.com/api/v1/apps/reddit/wsb (no auth)",
                ],
            },
        )
