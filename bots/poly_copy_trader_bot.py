"""
bots/poly_copy_trader_bot.py

Polymarket copy-trading bot.
Ported from Polymarket-Copy-Trading-Bot (GIT BOTS collection).

Strategy:
  1. Fetch leaderboard from Polymarket data API — rank traders by ROI / profit
  2. For each top trader, pull their recent TRADE activity
  3. Mirror any trade placed in the last cycle using PERCENTAGE / FIXED / ADAPTIVE sizing
  4. Track positions + PnL, filter out stale trades (> 24 h old)

PAPER mode — signals are logged, no real orders placed.
Truth label: PAPER / RESEARCH DATA ONLY
"""
from __future__ import annotations

import time
from typing import Any

import requests

# ── Polymarket public data API ────────────────────────────────────────────────
DATA_API   = "https://data-api.polymarket.com"
GAMMA_API  = "https://gamma-api.polymarket.com"

# How many top traders to follow
TOP_N_TRADERS    = 5
# Ignore trades older than this (seconds)
MAX_TRADE_AGE_S  = 86_400   # 24 h
# Minimum USD size to mirror
MIN_MIRROR_USD   = 1.0

# Copy sizing strategies
COPY_STRATEGY    = "PERCENTAGE"   # PERCENTAGE | FIXED | ADAPTIVE
COPY_PCT         = 0.10           # 10 % of their trade size
COPY_FIXED_USD   = 5.0
BANKROLL_USD     = 200.0          # simulated paper bankroll

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "DeGeNs-Research-Bot/1.0"


def _get(url: str, params: dict | None = None, timeout: int = 10) -> dict | list | None:
    try:
        r = _SESSION.get(url, params=params or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ── Leaderboard helpers ───────────────────────────────────────────────────────

def _fetch_leaderboard(limit: int = 20) -> list[dict]:
    """
    Fetch Polymarket leaderboard (public, sorted by profit).
    Returns list of dicts with proxyWallet, profit, roi, volume keys.
    """
    data = _get(f"{DATA_API}/leaderboard", params={"limit": limit, "window": "1m"})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data", data.get("leaderboard", []))
    return []


def _rank_traders(entries: list[dict]) -> list[dict]:
    """Sort leaderboard by ROI desc, filter out bots / very small traders."""
    scored = []
    for e in entries:
        roi    = float(e.get("roi", e.get("percentageProfit", 0)) or 0)
        profit = float(e.get("profit", e.get("totalProfit", 0)) or 0)
        vol    = float(e.get("volume", e.get("tradingVolume", 0)) or 0)
        addr   = e.get("proxyWallet") or e.get("address") or e.get("user")
        if not addr or vol < 50:      # skip dust traders
            continue
        scored.append({
            "address": addr.lower(),
            "roi":     roi,
            "profit":  profit,
            "volume":  vol,
        })
    scored.sort(key=lambda x: x["roi"], reverse=True)
    return scored


# ── Activity helpers ──────────────────────────────────────────────────────────

def _fetch_recent_trades(address: str, limit: int = 50) -> list[dict]:
    """Fetch recent TRADE activity for a wallet address."""
    data = _get(
        f"{DATA_API}/activity",
        params={"user": address, "type": "TRADE", "limit": limit},
    )
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data", [])
    return []


def _is_fresh(trade: dict, now: float) -> bool:
    ts = trade.get("timestamp", trade.get("createdAt", 0))
    if isinstance(ts, str):
        # ISO string — convert
        import datetime
        try:
            dt  = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ts  = dt.timestamp()
        except Exception:
            return False
    return (now - float(ts)) <= MAX_TRADE_AGE_S


# ── Sizing ────────────────────────────────────────────────────────────────────

def _mirror_size(their_usd: float, bankroll: float) -> float:
    if COPY_STRATEGY == "FIXED":
        return min(COPY_FIXED_USD, bankroll * 0.05)
    if COPY_STRATEGY == "ADAPTIVE":
        # Scale by trader's trade size relative to expected range $10–$500
        factor = min(max(their_usd / 100.0, 0.5), 2.0)
        return min(bankroll * COPY_PCT * factor, bankroll * 0.10)
    # PERCENTAGE (default)
    return min(their_usd * COPY_PCT, bankroll * 0.10)


# ── Bot ───────────────────────────────────────────────────────────────────────

class PolyCopyTraderBot:
    bot_id   = "bot_poly_copy_trader_paper"
    platform = "polymarket"
    platform_truth_label = "PAPER / RESEARCH DATA ONLY"
    execution_enabled    = False
    live_capable         = False
    display_name = "Poly Copy Trader"
    mode = "PAPER"
    signal_type = "copy_trading"
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
    description = "Mirrors top Polymarket traders by ROI. Follows leaderboard top-5 and copies their trades at 10% size. C-tier: mirrors public information, no structural edge."
    edge_source = "Polymarket leaderboard public trade activity mirroring"
    opp_cadence_per_day = 5.0
    avg_hold_hours = 24.0
    fee_drag_bps = 200
    fill_rate = 0.5
    platforms = ["polymarket"]

    def __init__(self, adapter=None):
        # Keeps a set of (address, transactionHash) we've already mirrored
        self._seen:        set[str]        = set()
        self._paper_pnl:   float           = 0.0
        self._trade_count: int             = 0
        self._bankroll:    float           = BANKROLL_USD
        self._followed:    list[dict]      = []   # last known top traders
        self._positions:   dict[str, dict] = {}   # conditionId → position

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
        t0  = time.time()
        now = t0

        # Step 1: refresh leaderboard every cycle
        board = _fetch_leaderboard(limit=30)
        ranked = _rank_traders(board)
        top_traders = ranked[:TOP_N_TRADERS]
        self._followed = top_traders

        if not top_traders:
            return self._degraded("Polymarket leaderboard returned no ranked traders")

        # Step 2: scan each trader's recent activity
        new_signals: list[dict] = []
        trader_summaries: list[dict] = []

        for trader in top_traders:
            addr   = trader["address"]
            trades = _fetch_recent_trades(addr, limit=20)
            fresh  = [t for t in trades if _is_fresh(t, now)]

            for trade in fresh:
                tx = trade.get("transactionHash", "")
                key = f"{addr}:{tx}"
                if key in self._seen:
                    continue
                self._seen.add(key)

                usd_size  = float(trade.get("usdcSize", trade.get("size", 0)) or 0)
                if usd_size < MIN_MIRROR_USD:
                    continue

                our_size  = _mirror_size(usd_size, self._bankroll)
                side      = trade.get("side", "BUY").upper()
                outcome   = trade.get("outcome", trade.get("name", "YES"))
                price     = float(trade.get("price", 0.5) or 0.5)
                title     = trade.get("title", trade.get("slug", "?"))
                condition = trade.get("conditionId", "")

                new_signals.append({
                    "type":         "copy_trade",
                    "trader":       addr[:10] + "…",
                    "trader_roi":   round(trader["roi"], 2),
                    "market":       title[:60],
                    "condition_id": condition,
                    "side":         side,
                    "outcome":      outcome,
                    "their_usd":    round(usd_size, 2),
                    "our_usd":      round(our_size, 2),
                    "price":        round(price, 4),
                    "strategy":     COPY_STRATEGY,
                })
                self._trade_count += 1
                self._bankroll    -= our_size  # paper-deduct

            trader_summaries.append({
                "address":     addr[:14] + "…",
                "roi":         round(trader["roi"], 2),
                "profit":      round(trader["profit"], 2),
                "fresh_trades": len(fresh),
            })

        signal_taken = len(new_signals) > 0
        top = new_signals[0] if new_signals else None

        if new_signals:
            best    = max(new_signals, key=lambda s: s["our_usd"])
            summary = (
                f"MIRROR {best['side']} {best['outcome']} in '{best['market'][:40]}'"
                f" ${best['our_usd']:.2f} (trader ROI {best['trader_roi']:.1f}%)"
            )
        else:
            summary = (
                f"No new trades — following {len(top_traders)} traders, "
                f"seen {len(self._seen)} total"
            )

        return {
            "bot_id":               self.bot_id,
            "platform":             self.platform,
            "platform_truth_label": self.platform_truth_label,
            "title":                "Copy Trader (Polymarket)",
            "summary":              summary,
            "signal_taken":         signal_taken,
            "degraded_reason":      None,
            "signals":              new_signals,
            "top_traders":          trader_summaries,
            "paper_trades_total":   self._trade_count,
            "paper_bankroll":       round(self._bankroll, 2),
            "confidence":           round(min(len(new_signals) * 0.2, 0.9), 3),
            "top_signal":           top,
            "elapsed_s":            round(time.time() - t0, 2),
        }

    def _degraded(self, reason: str) -> dict[str, Any]:
        return {
            "bot_id":               self.bot_id,
            "platform":             self.platform,
            "platform_truth_label": self.platform_truth_label,
            "title":                "Copy Trader (Polymarket)",
            "summary":              f"DEGRADED: {reason}",
            "signal_taken":         False,
            "degraded_reason":      reason,
            "signals":              [],
            "confidence":           0.0,
        }
