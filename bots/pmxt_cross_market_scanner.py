"""
bots/pmxt_cross_market_scanner.py

Cross-platform prediction market scanner using the pmxt sidecar.
Scans Kalshi + Limitless simultaneously, looking for:
  1. Questions present on both platforms with divergent prices (arb opportunity)
  2. High-confidence single-platform signals (extreme odds suggesting mispricing)
  3. Volume spikes on either platform

This is a RESEARCH-ONLY bot — no execution, no real money.
Truth label: PAPER / RESEARCH DATA ONLY
"""

from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from typing import Any

from adapters.pmxt_adapter import PmxtAdapter


# Minimum similarity to count two questions as the "same" market
MATCH_THRESHOLD = 0.55
# Price divergence worth flagging (e.g. 0.08 = 8 cents spread)
ARB_THRESHOLD = 0.08
# Extreme odds flag: price < LOW or > HIGH
EXTREME_LOW = 0.05
EXTREME_HIGH = 0.95


def _normalize_question(q: str) -> str:
    q = q.lower().strip()
    q = re.sub(r"[^a-z0-9 ]", " ", q)
    q = re.sub(r"\s+", " ", q)
    return q


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize_question(a), _normalize_question(b)).ratio()


def _extract_price(market: dict) -> float | None:
    """Pull a single yes-probability float from a pmxt market dict."""
    for key in ("lastPrice", "last_price", "price", "midpointOdds", "probability", "bestBid", "yes_bid"):
        v = market.get(key)
        if v is not None:
            try:
                f = float(v)
                # Normalize: Kalshi uses 0-100 cents; Polymarket/Limitless use 0-1
                return f / 100.0 if f > 1.0 else f
            except (TypeError, ValueError):
                pass
    return None


def _extract_question(market: dict) -> str:
    for key in ("title", "question", "name", "subtitle", "marketTitle"):
        v = market.get(key)
        if v and isinstance(v, str):
            return v.strip()
    return market.get("id", "?")


class PmxtCrossMarketScannerBot:
    bot_id = "bot_pmxt_cross_market_scanner"
    platform = "pmxt_multi"
    platform_truth_label = "PAPER / RESEARCH DATA ONLY"
    execution_enabled = False
    live_capable = False
    display_name = "PMXT Cross-Market Scanner"
    mode = "RESEARCH"
    signal_type = "crossvenue_arb"
    paper_only = True
    research_only = True
    watchlist_only = False
    demo_only = False
    delayed_only = False
    default_enabled = False
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "low"
    description = "Scans Kalshi + Limitless via pmxt sidecar for cross-platform arb (≥8¢ spread) and extreme odds. Research-only, no execution."
    edge_source = "Cross-platform price divergence between Kalshi and Limitless prediction markets"
    opp_cadence_per_day = 6.0
    avg_hold_hours = 4.0
    fee_drag_bps = 100
    fill_rate = 0.5
    platforms = ["kalshi", "limitless", "pmxt"]

    def __init__(
        self,
        kalshi_adapter: PmxtAdapter | None = None,
        limitless_adapter: PmxtAdapter | None = None,
    ):
        self._kalshi = kalshi_adapter or PmxtAdapter("kalshi")
        self._limitless = limitless_adapter or PmxtAdapter("limitless")

    def metadata(self) -> dict:
        return {
            "bot_id": self.bot_id,
            "display_name": self.display_name,
            "platform": self.platform,
            "mode": self.mode,
            "signal_type": self.signal_type,
            "paper_only": self.paper_only,
            "delayed_only": self.delayed_only,
            "research_only": self.research_only,
            "watchlist_only": self.watchlist_only,
            "demo_only": self.demo_only,
            "default_enabled": self.default_enabled,
            "implemented": self.implemented,
            "truth_label": self.truth_label,
            "quality_tier": self.quality_tier,
            "risk_tier": self.risk_tier,
            "description": self.description,
            "edge_source": self.edge_source,
            "opp_cadence_per_day": self.opp_cadence_per_day,
            "avg_hold_hours": self.avg_hold_hours,
            "fee_drag_bps": self.fee_drag_bps,
            "fill_rate": self.fill_rate,
            "platforms": self.platforms,
            "platform_truth_label": self.platform_truth_label,
            "execution_enabled": self.execution_enabled,
            "live_capable": self.live_capable,
        }

    def run_one_cycle(self) -> dict[str, Any]:
        t0 = time.time()

        # Fetch from both platforms
        k_result = self._kalshi.fetch_markets(limit=80)
        l_result = self._limitless.fetch_markets(limit=80)

        k_markets: list[dict] = k_result.get("data", []) if k_result.get("ok") else []
        l_markets: list[dict] = l_result.get("data", []) if l_result.get("ok") else []

        if not k_markets and not l_markets:
            return self._degraded("Both pmxt exchanges returned no data")

        # ── 1. Find cross-platform matches ────────────────────────────────────
        arb_signals: list[dict] = []
        for km in k_markets:
            kq = _extract_question(km)
            kp = _extract_price(km)
            if kp is None:
                continue
            for lm in l_markets:
                lq = _extract_question(lm)
                lp = _extract_price(lm)
                if lp is None:
                    continue
                sim = _similarity(kq, lq)
                if sim >= MATCH_THRESHOLD:
                    spread = abs(kp - lp)
                    if spread >= ARB_THRESHOLD:
                        arb_signals.append({
                            "type": "cross_platform_arb",
                            "kalshi_question": kq,
                            "limitless_question": lq,
                            "kalshi_price": round(kp, 4),
                            "limitless_price": round(lp, 4),
                            "spread": round(spread, 4),
                            "similarity": round(sim, 3),
                            "kalshi_id": km.get("id", km.get("marketId", "?")),
                            "limitless_id": lm.get("id", "?"),
                        })

        # ── 2. Extreme odds (single-platform) ─────────────────────────────────
        extreme_signals: list[dict] = []
        for source, markets in [("kalshi", k_markets), ("limitless", l_markets)]:
            for m in markets:
                p = _extract_price(m)
                if p is None:
                    continue
                if p <= EXTREME_LOW or p >= EXTREME_HIGH:
                    extreme_signals.append({
                        "type": "extreme_odds",
                        "platform": source,
                        "question": _extract_question(m),
                        "price": round(p, 4),
                        "direction": "near_certain_no" if p <= EXTREME_LOW else "near_certain_yes",
                        "id": m.get("id", m.get("marketId", "?")),
                    })

        all_signals = arb_signals + extreme_signals
        signal_taken = len(arb_signals) > 0
        top = arb_signals[0] if arb_signals else (extreme_signals[0] if extreme_signals else None)

        summary_parts = []
        if arb_signals:
            best = max(arb_signals, key=lambda x: x["spread"])
            summary_parts.append(
                f"ARB: {best['kalshi_question'][:50]} | spread {best['spread']:.2f}"
            )
        if extreme_signals:
            summary_parts.append(f"{len(extreme_signals)} extreme odds flagged")
        if not summary_parts:
            summary_parts.append(
                f"No signals — {len(k_markets)} Kalshi + {len(l_markets)} Limitless markets scanned"
            )

        return {
            "bot_id": self.bot_id,
            "platform": self.platform,
            "platform_truth_label": self.platform_truth_label,
            "title": "Cross-Platform Scanner (pmxt)",
            "summary": " | ".join(summary_parts),
            "signal_taken": signal_taken,
            "degraded_reason": None,
            "signals": all_signals,
            "arb_count": len(arb_signals),
            "extreme_count": len(extreme_signals),
            "kalshi_market_count": len(k_markets),
            "limitless_market_count": len(l_markets),
            "confidence": round(min(len(arb_signals) * 0.15, 0.85), 3) if arb_signals else 0.0,
            "elapsed_s": round(time.time() - t0, 2),
            "top_signal": top,
        }

    def _degraded(self, reason: str) -> dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "platform": self.platform,
            "platform_truth_label": self.platform_truth_label,
            "title": "Cross-Platform Scanner (pmxt)",
            "summary": f"DEGRADED: {reason}",
            "signal_taken": False,
            "degraded_reason": reason,
            "signals": [],
            "confidence": 0.0,
        }
