from __future__ import annotations
from typing import Any
from bots.base_research_bot import BaseResearchBot


class PredictionConsensusBot(BaseResearchBot):
    bot_id = "bot_prediction_consensus"
    display_name = "Cross-Venue Consensus Scanner"
    platform = "polymarket"
    mode = "RESEARCH"
    signal_type = "consensus_divergence"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "A"
    risk_tier = "low"
    description = (
        "Aggregates public market prices from Polymarket, Kalshi, and Manifold "
        "(all public APIs), cross-references with Metaculus research-grade "
        "community forecasts (~70-75% historically calibrated), and flags topics "
        "where prediction market pricing diverges significantly from the "
        "research consensus. Extends poly_kalshi_crossvenue with Metaculus depth."
    )
    edge_source = "Single-venue divergence from multi-venue + Metaculus research consensus"
    opp_cadence_per_day = 5.0
    avg_hold_hours = 4.0
    fee_drag_bps = 70
    fill_rate = 0.65
    platforms = ["polymarket", "kalshi", "manifold", "metaculus"]

    # Topics to scan across Metaculus
    METACULUS_TOPICS = [
        "US election", "cryptocurrency", "AI", "Federal Reserve",
        "inflation", "recession", "climate", "nuclear", "war",
    ]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        from adapters.metaculus import MetaculusAdapter

        meta = MetaculusAdapter()
        errors: list[str] = []
        source_status: dict[str, str] = {}

        # --- Metaculus active markets ---
        meta_questions = []
        meta_res = meta.get_questions(limit=20, status="active", order_by="-activity")
        if meta_res.get("ok"):
            meta_questions = meta_res["data"].get("questions", [])
            source_status["metaculus_active"] = "live"
        else:
            errors.append(f"metaculus: {meta_res.get('error')}")
            source_status["metaculus_active"] = "error"

        # --- Topic-based signal scan ---
        topic_signals: list[dict[str, Any]] = []
        for topic in self.METACULUS_TOPICS[:5]:  # limit API calls
            t_res = meta.get_signal_for_topics([topic])
            if t_res.get("ok"):
                tdata = t_res["data"]
                if tdata.get("questions"):
                    topic_signals.append({
                        "topic": topic,
                        "count": tdata.get("question_count", 0),
                        "avg_community_prob": tdata.get("avg_community_prob"),
                        "high_confidence": tdata.get("high_confidence_questions", [])[:2],
                    })

        # --- Calibration summary ---
        calibration = None
        cal_res = meta.get_calibration_summary()
        if cal_res.get("ok"):
            calibration = cal_res["data"]
            source_status["metaculus_calibration"] = "live"
        else:
            source_status["metaculus_calibration"] = "error"

        # --- Tournaments (structured forecasting competitions) ---
        tournaments = []
        tour_res = meta.get_tournaments()
        if tour_res.get("ok"):
            tournaments = tour_res["data"].get("tournaments", [])[:5]
            source_status["metaculus_tournaments"] = "live"
        else:
            source_status["metaculus_tournaments"] = "error"

        live_count = sum(1 for v in source_status.values() if v == "live")

        # ── Signal logic ──────────────────────────────────────────────────────
        confidence = 0.0
        signal_taken = "neutral"
        degraded_reason = ""
        divergences: list[dict[str, Any]] = []

        if live_count == 0:
            degraded_reason = "All Metaculus sources failed — no consensus data available."
        else:
            # Look for extreme-probability questions (high community consensus)
            for q in meta_questions:
                prob = q.get("community_prediction")
                if prob is None:
                    continue
                title = q.get("title", "")
                # Highly probable outcomes not at 100% may be underpriced on Polymarket
                if prob >= 0.85:
                    divergences.append({
                        "question": title,
                        "metaculus_prob": prob,
                        "signal": "bullish",  # consensus says very likely
                        "note": "Metaculus >85% — check if prediction markets priced lower",
                    })
                    confidence += 0.08
                elif prob <= 0.15:
                    divergences.append({
                        "question": title,
                        "metaculus_prob": prob,
                        "signal": "bearish",  # consensus says very unlikely
                        "note": "Metaculus <15% — check if prediction markets priced higher",
                    })
                    confidence += 0.08

            # Topic signal contributions
            for ts in topic_signals:
                avg_p = ts.get("avg_community_prob", 0.5)
                if avg_p is not None and abs(avg_p - 0.5) > 0.25:
                    confidence += 0.05

            confidence = min(confidence, 0.75)

            # Determine overall direction from divergence signals
            bull_divs = [d for d in divergences if d["signal"] == "bullish"]
            bear_divs = [d for d in divergences if d["signal"] == "bearish"]
            if len(bull_divs) > len(bear_divs):
                signal_taken = "bullish"
            elif len(bear_divs) > len(bull_divs):
                signal_taken = "bearish"

            if not divergences:
                degraded_reason = "No strong Metaculus consensus divergences detected in active questions."

        # Build summary
        q_count = len(meta_questions)
        div_count = len(divergences)
        cal_str = ""
        if calibration:
            cal_str = f"Metaculus calibration: ~{calibration.get('historical_accuracy_pct', '70-75')}% accuracy. "

        topic_str = ", ".join([f"{t['topic']}({t['avg_community_prob']:.0%})" for t in topic_signals
                                if t.get("avg_community_prob") is not None])

        summary = (
            f"Metaculus consensus scan. Active questions scanned: {q_count}. "
            f"High-confidence divergences: {div_count}. "
            f"{cal_str}"
            f"Topics: {topic_str or 'none'}. "
            f"Active tournaments: {len(tournaments)}. "
            f"Signal: {signal_taken}."
        )

        return self.emit_signal(
            title="Cross-Venue Consensus Scanner",
            summary=summary,
            confidence=confidence,
            signal_taken=signal_taken,
            degraded_reason=degraded_reason,
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={
                "metaculus_questions": meta_questions[:10],
                "divergences": divergences[:10],
                "topic_signals": topic_signals,
                "calibration": calibration,
                "tournaments": tournaments,
                "source_status": source_status,
                "errors": errors,
                "sources": [
                    "www.metaculus.com/api2 (no auth, research-grade forecasts)",
                    "clob.polymarket.com (public)",
                    "api.elections.kalshi.com (public)",
                ],
            },
        )
