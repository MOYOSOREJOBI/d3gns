"""
bots/poly_adaptive_trend_bot.py

Adaptive trend-following bot for Polymarket 15-minute crypto up/down markets.
Ported from the AdaptivePricePredictor TypeScript implementation found in
Polymarket-Arbitrage-Trading-Bot (GIT BOTS collection).

Strategy:
  - Scans BTC, ETH, SOL, XRP 15-min up/down markets on Polymarket
  - Builds online multi-feature linear regression (momentum, volatility, trend, price lags)
  - Noise-filters small moves (< 2¢), detects poles (peaks/troughs) before acting
  - Signals BUY_UP or BUY_DOWN based on predicted next-period direction
  - PAPER mode only — no real execution

Truth label: PAPER / RESEARCH DATA ONLY
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Any

import requests

# ── Constants ────────────────────────────────────────────────────────────────
GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API  = "https://data-api.polymarket.com"

NOISE_THRESHOLD   = 0.02   # ignore price changes < 2¢
ARB_THRESHOLD     = 0.08   # price swing worth acting on
EXTREME_LOW       = 0.05
EXTREME_HIGH      = 0.95
MAX_HISTORY       = 10
MIN_POLE_WINDOW   = 3

# 15-min market slug template: {market}-updown-15m-{unix_slot_start}
TRACKED_MARKETS = ["btc", "eth", "sol", "xrp"]


def _slot_start() -> int:
    """Unix timestamp (seconds) of the start of the current 15-min slot."""
    t = int(time.time())
    return t - (t % 900)


def _current_slug(market: str) -> str:
    return f"{market}-updown-15m-{_slot_start()}"


# ── Online linear regression predictor ───────────────────────────────────────

class AdaptivePricePredictor:
    """
    Online gradient-descent multi-feature linear regression.
    Predicts direction of next price move from momentum/volatility/trend features.
    """

    def __init__(self):
        self._history:    deque[float] = deque(maxlen=MAX_HISTORY)
        self._timestamps: deque[float] = deque(maxlen=MAX_HISTORY)
        self._smoothed:   float | None = None
        self._last_added: float | None = None
        self._alpha_smooth = 0.5

        # Learned weights
        self._w = {
            "intercept":  0.5,
            "lag1":       0.25,
            "lag2":       0.08,
            "lag3":       0.04,
            "momentum":   0.35,
            "volatility": -0.20,
            "trend":      0.45,
        }
        self._lr = 0.05

        # EMA components
        self._ema_short = 0.5
        self._ema_long  = 0.5
        self._alpha_short = 2 / (2 + 1)
        self._alpha_long  = 2 / (5 + 1)

        # Pole detection
        self._pole_history: list[dict] = []
        self._last_pole_price: float | None = None
        self._last_pole_type:  str | None   = None  # "peak" or "trough"

        # Accuracy tracking
        self._predictions_made  = 0
        self._correct           = 0
        self._price_mean        = 0.5
        self._price_std         = 0.1

    # ── Public API ────────────────────────────────────────────────────────────

    def update(self, price: float, ts: float) -> dict | None:
        """
        Feed a new price tick. Returns a prediction dict or None if not ready.
        Prediction dict keys: predictedPrice, confidence, direction, signal
        """
        if price < EXTREME_LOW or price > EXTREME_HIGH:
            return None

        if self._smoothed is None:
            self._smoothed   = price
            self._last_added = price
            self._history.append(price)
            self._timestamps.append(ts)
            return None

        # Noise filter
        if self._last_added is not None and abs(price - self._last_added) < NOISE_THRESHOLD:
            return None

        # EMA smooth
        self._smoothed = self._alpha_smooth * price + (1 - self._alpha_smooth) * self._smoothed
        if self._smoothed < EXTREME_LOW or self._smoothed > EXTREME_HIGH:
            return None

        self._history.append(self._smoothed)
        self._timestamps.append(ts)
        self._last_added = self._smoothed

        if len(self._history) < 3:
            return None

        # Only act at poles
        if not self._detect_pole(self._smoothed, ts):
            return None

        self._update_stats()
        feats  = self._features()
        pred_p = self._predict(feats)
        self._update_ema(self._smoothed)

        direction  = "up" if pred_p > self._smoothed else "down"
        spread     = abs(pred_p - self._smoothed)
        confidence = min(spread / 0.10, 0.9) * self._recent_accuracy()

        signal = "HOLD"
        if spread >= ARB_THRESHOLD and confidence >= 0.4:
            signal = "BUY_UP" if direction == "up" else "BUY_DOWN"

        self._predictions_made += 1
        return {
            "predictedPrice": round(pred_p, 4),
            "confidence":     round(confidence, 3),
            "direction":      direction,
            "signal":         signal,
            "features":       feats,
        }

    def feedback(self, was_correct: bool):
        if was_correct:
            self._correct += 1

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _detect_pole(self, price: float, ts: float) -> bool:
        self._pole_history.append({"price": price, "ts": ts})
        n = len(self._pole_history)
        if n < 2 * MIN_POLE_WINDOW + 1:
            return False
        mid = n - MIN_POLE_WINDOW - 1
        window = [e["price"] for e in self._pole_history]
        c = window[mid]
        before = window[mid - MIN_POLE_WINDOW:mid]
        after  = window[mid + 1:mid + MIN_POLE_WINDOW + 1]
        if len(before) < MIN_POLE_WINDOW or len(after) < MIN_POLE_WINDOW:
            return False
        is_peak   = c > max(before) and c > max(after)
        is_trough = c < min(before) and c < min(after)
        if is_peak or is_trough:
            pole_type = "peak" if is_peak else "trough"
            # Alternate peaks and troughs
            if self._last_pole_type and self._last_pole_type == pole_type:
                return False
            self._last_pole_price = c
            self._last_pole_type  = pole_type
            # Trim history to keep memory bounded
            if len(self._pole_history) > 50:
                self._pole_history = self._pole_history[-30:]
            return True
        return False

    def _features(self) -> dict:
        h = list(self._history)
        n = len(h)
        if n < 3:
            return {"momentum": 0.0, "volatility": 0.0, "trend": 0.0}
        momentum   = h[-1] - h[-3]
        diffs      = [h[i+1] - h[i] for i in range(n - 1)]
        volatility = math.sqrt(sum(d**2 for d in diffs) / len(diffs)) if diffs else 0.0
        trend      = self._ema_short - self._ema_long
        return {"momentum": momentum, "volatility": volatility, "trend": trend}

    def _predict(self, feats: dict) -> float:
        h = list(self._history)
        n = len(h)
        lag1 = (h[-1] - self._price_mean) / (self._price_std or 0.1)
        lag2 = (h[-2] - self._price_mean) / (self._price_std or 0.1) if n >= 2 else 0.0
        lag3 = (h[-3] - self._price_mean) / (self._price_std or 0.1) if n >= 3 else 0.0
        raw = (
            self._w["intercept"]
            + self._w["lag1"]       * lag1
            + self._w["lag2"]       * lag2
            + self._w["lag3"]       * lag3
            + self._w["momentum"]   * feats["momentum"]
            + self._w["volatility"] * feats["volatility"]
            + self._w["trend"]      * feats["trend"]
        )
        return max(EXTREME_LOW, min(EXTREME_HIGH, raw))

    def _update_ema(self, price: float):
        self._ema_short = self._alpha_short * price + (1 - self._alpha_short) * self._ema_short
        self._ema_long  = self._alpha_long  * price + (1 - self._alpha_long)  * self._ema_long

    def _update_stats(self):
        h = list(self._history)
        if len(h) < 2:
            return
        self._price_mean = sum(h) / len(h)
        var = sum((x - self._price_mean) ** 2 for x in h) / len(h)
        self._price_std  = math.sqrt(var) if var > 0 else 0.01

    def _recent_accuracy(self) -> float:
        if self._predictions_made == 0:
            return 0.5
        return self._correct / self._predictions_made


# ── Price fetch helpers ───────────────────────────────────────────────────────

def _fetch_market_price(slug: str) -> float | None:
    """Fetch the YES token mid-price from Polymarket gamma API."""
    try:
        r = requests.get(f"{GAMMA_API}/markets/slug/{slug}", timeout=8)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        # outcomePrices is a JSON-encoded list like '["0.55","0.45"]'
        import json as _json
        prices = data.get("outcomePrices") or data.get("prices")
        if prices:
            if isinstance(prices, str):
                prices = _json.loads(prices)
            if isinstance(prices, list) and prices:
                return float(prices[0])  # UP token price
    except Exception:
        pass
    return None


def _fetch_top_markets_prices() -> dict[str, float]:
    """Fetch current UP prices for all tracked 15-min markets."""
    results = {}
    for mkt in TRACKED_MARKETS:
        slug  = _current_slug(mkt)
        price = _fetch_market_price(slug)
        if price is not None:
            results[mkt] = price
    return results


# ── Bot class ─────────────────────────────────────────────────────────────────

class PolyAdaptiveTrendBot:
    bot_id   = "bot_poly_adaptive_trend_paper"
    platform = "polymarket"
    platform_truth_label = "PAPER / RESEARCH DATA ONLY"
    execution_enabled    = False
    live_capable         = False
    display_name = "Poly Adaptive Trend"
    mode = "PAPER"
    signal_type = "adaptive_trend"
    paper_only = True
    delayed_only = False
    research_only = False
    watchlist_only = False
    demo_only = False
    default_enabled = False
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "C"
    risk_tier = "medium"
    description = "Online linear regression trend-follower for Polymarket 15-min BTC/ETH/SOL/XRP up/down markets. C-tier: unvalidated edge, research only."
    edge_source = "Polymarket 15-min crypto market price momentum (unvalidated)"
    opp_cadence_per_day = 8.0
    avg_hold_hours = 0.25
    fee_drag_bps = 150
    fill_rate = 0.6
    platforms = ["polymarket"]

    def __init__(self, adapter=None):
        # adapter ignored — uses public Polymarket REST directly
        self._predictors: dict[str, AdaptivePricePredictor] = {
            mkt: AdaptivePricePredictor() for mkt in TRACKED_MARKETS
        }
        self._last_slot: int | None = None
        self._signal_history: list[dict] = []

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
        slot = _slot_start()

        # Reset predictors at new 15-min slot
        if self._last_slot is not None and slot != self._last_slot:
            self._predictors = {mkt: AdaptivePricePredictor() for mkt in TRACKED_MARKETS}
        self._last_slot = slot

        prices = _fetch_top_markets_prices()
        if not prices:
            return self._degraded("Polymarket gamma API returned no 15-min market prices")

        signals: list[dict] = []
        ts = time.time()
        for mkt, price in prices.items():
            pred = self._predictors[mkt].update(price, ts)
            if pred and pred["signal"] != "HOLD":
                signals.append({
                    "market":          mkt,
                    "slug":            _current_slug(mkt),
                    "current_price":   round(price, 4),
                    "predicted_price": pred["predictedPrice"],
                    "direction":       pred["direction"],
                    "signal":          pred["signal"],
                    "confidence":      pred["confidence"],
                })

        signal_taken = len(signals) > 0
        top = signals[0] if signals else None

        if signals:
            best   = max(signals, key=lambda s: s["confidence"])
            summary = (
                f"{best['signal']} {best['market'].upper()} @ {best['current_price']:.3f}"
                f" → {best['predicted_price']:.3f} | conf {best['confidence']:.2f}"
            )
        else:
            mkt_prices = ", ".join(
                f"{k.upper()}={v:.3f}" for k, v in prices.items()
            )
            summary = f"No pole signals — {mkt_prices}"

        return {
            "bot_id":               self.bot_id,
            "platform":             self.platform,
            "platform_truth_label": self.platform_truth_label,
            "title":                "Adaptive Trend (Polymarket 15-min)",
            "summary":              summary,
            "signal_taken":         signal_taken,
            "degraded_reason":      None,
            "signals":              signals,
            "markets_polled":       list(prices.keys()),
            "prices":               {k: round(v, 4) for k, v in prices.items()},
            "confidence":           round(max((s["confidence"] for s in signals), default=0.0), 3),
            "top_signal":           top,
            "elapsed_s":            round(time.time() - t0, 2),
        }

    def _degraded(self, reason: str) -> dict[str, Any]:
        return {
            "bot_id":               self.bot_id,
            "platform":             self.platform,
            "platform_truth_label": self.platform_truth_label,
            "title":                "Adaptive Trend (Polymarket 15-min)",
            "summary":              f"DEGRADED: {reason}",
            "signal_taken":         False,
            "degraded_reason":      reason,
            "signals":              [],
            "confidence":           0.0,
        }
