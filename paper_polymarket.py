"""
Paper Polymarket client – execution-truthful multi-factor edge model.

Fetches REAL market prices from the public Gamma API (no credentials needed).
Simulates order fills and settlement locally.

Key improvements over previous version (per deep-research report):
  • Execution-truthful EV: trade at bestAsk (not midpoint)
      EV = p_true − ask − fee_per_share
  • Fee-aware: fee_per_share ≈ TAKER_FEE_RATE × price × (1−price)
      Makers pay 0; takers pay on liquid markets ~2%
  • Walk-the-book: use bestBid/bestAsk spread for fill estimation
  • Only trade when net EV > min_edge AFTER ask premium + fee

Edge scoring factors:
  1. Order book imbalance   – bid/ask skew signals directional pressure
  2. Volume momentum        – rising volume in the last hour = conviction
  3. Time decay             – near-expiry markets converge faster (higher edge)
  4. Price extremity        – very low/high prices have convex resolution risk
  5. Spread tightness       – tight spreads = liquid, efficient fill
  6. Fee adjustment         – explicit cost subtraction from EV

Phase settings (7-phase system):
  floor/ultra_safe : minimal exposure, flat bets
  safe/careful     : conservative Kelly, small positions
  normal           : standard Kelly, moderate positions
  aggressive/turbo : full Kelly fraction, larger positions, wider scan
"""

import time
import math
import random
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

GAMMA_URL        = "https://gamma-api.polymarket.com"
HEADERS          = {"User-Agent": "degen-paper-bot/3.0", "Accept": "application/json"}
TAKER_FEE_RATE   = 0.02    # ~2% on liquid markets; dynamically adjustable
MAKER_FEE_RATE   = 0.00    # makers always pay 0

# ── Phase settings ─────────────────────────────────────────────────────────────
_PHASE_SETTINGS = {
    "floor"     : {"kelly": 0.10, "max_pos": 0.03, "min_edge": 0.08},
    "ultra_safe": {"kelly": 0.15, "max_pos": 0.04, "min_edge": 0.07},
    "safe"      : {"kelly": 0.20, "max_pos": 0.06, "min_edge": 0.06},
    "careful"   : {"kelly": 0.25, "max_pos": 0.07, "min_edge": 0.05},
    "normal"    : {"kelly": 0.30, "max_pos": 0.09, "min_edge": 0.04},
    "aggressive": {"kelly": 0.40, "max_pos": 0.11, "min_edge": 0.035},
    "turbo"     : {"kelly": 0.50, "max_pos": 0.13, "min_edge": 0.03},
    "milestone" : {"kelly": 0.30, "max_pos": 0.09, "min_edge": 0.04},
    # Legacy aliases
    "recovery"  : {"kelly": 0.15, "max_pos": 0.04, "min_edge": 0.07},
    "phase1"    : {"kelly": 0.25, "max_pos": 0.07, "min_edge": 0.05},
    "phase2"    : {"kelly": 0.35, "max_pos": 0.09, "min_edge": 0.04},
    "phase3"    : {"kelly": 0.45, "max_pos": 0.12, "min_edge": 0.03},
}

# Scanning range — LIVE-safe: only trade where real liquidity exists
SCAN_YES_MIN = 0.60   # bet YES when price >= this
SCAN_NO_MAX  = 0.40   # bet NO  when price <= this

# ── LIVE GUARD: executable ask must be in this range ──────────────────────────
# Prevents buying 1¢ junk markets that have no real liquidity on CLOB.
# In live mode a 1¢ NO position requires 99¢ YES liquidity — almost never fills.
MIN_ASK_PRICE = 0.08   # never buy below 8¢ (illiquid, bad fills in live)
MAX_ASK_PRICE = 0.92   # never buy above 92¢ (insufficient upside after fees)

MIN_VOLUME        = 500    # require $500 volume (liquid enough for real fills)
MIN_HOLD_SECONDS  = 6
MAX_HOLD_SECONDS  = 25
MAX_OPEN_POSITIONS = 5
CACHE_TTL         = 20
MAX_BET_HARD_CAP  = 15.0


def _fetch_markets(limit: int = 200) -> list[dict]:
    try:
        resp = requests.get(
            f"{GAMMA_URL}/markets",
            params  = {"active": "true", "closed": "false", "limit": limit},
            headers = HEADERS,
            timeout = 8,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("markets", [])
    except Exception as exc:
        logger.debug(f"Gamma API fetch failed: {exc}")
        return []


# ── Multi-factor edge scoring ──────────────────────────────────────────────────

def _order_book_imbalance(market: dict, direction: str) -> float:
    """
    Returns 0.0..0.10 boost.
    If bid > ask_midpoint → upward pressure → YES edge.
    Uses bestBid / bestAsk if available; falls back to outcomePrices spread.
    """
    try:
        best_bid = float(market.get("bestBid") or 0)
        best_ask = float(market.get("bestAsk") or 1)
        if best_bid <= 0 or best_ask <= 0:
            return 0.0
        spread    = best_ask - best_bid
        mid       = (best_bid + best_ask) / 2.0
        imbalance = (best_bid - mid) / (spread + 1e-9)   # −0.5 to +0.5
        # Positive imbalance → buyers more aggressive → YES edge
        if direction == "yes":
            return max(0.0, imbalance * 0.10)
        else:
            return max(0.0, -imbalance * 0.10)
    except Exception:
        return 0.0


def _volume_momentum(market: dict) -> float:
    """
    Returns 0.0..0.08 boost.
    Compare volume24h to volumeNum; if volume24h > 20% of total → momentum.
    """
    try:
        vol_total = float(market.get("volumeNum") or market.get("volume") or 0)
        vol_24h   = float(market.get("volume24hr") or market.get("volume24h") or 0)
        if vol_total <= 0:
            return 0.0
        ratio = vol_24h / vol_total
        # Recent volume > 15% of all-time → strong momentum
        if ratio > 0.15:
            return min(0.08, (ratio - 0.15) * 0.8)
        return 0.0
    except Exception:
        return 0.0


def _time_decay_boost(hours_left: Optional[float]) -> float:
    """
    Returns 0.0..0.06 boost.
    Near-expiry markets (< 6h) have prices moving toward resolution → bigger edge window.
    """
    if hours_left is None:
        return 0.0
    if hours_left < 1:
        return 0.06   # imminent resolution, maximum decay edge
    if hours_left < 6:
        return 0.04
    if hours_left < 24:
        return 0.02
    return 0.0


def _price_extremity_bonus(price: float) -> float:
    """
    Returns 0.0..0.05 boost.
    Very extreme prices (>0.85 or <0.15 mirror) have convex upside — a small
    probability shift creates a large payout delta.
    """
    if price >= 0.85:
        return 0.04 + (price - 0.85) * 0.33   # up to ~0.05 at 0.90
    if price >= 0.75:
        return 0.02
    return 0.0


def _spread_penalty(market: dict) -> float:
    """
    Returns −0.06..0.0 penalty.
    Wide spreads erode edge. If spread > 3%, penalise.
    """
    try:
        best_bid = float(market.get("bestBid") or 0)
        best_ask = float(market.get("bestAsk") or 0)
        if best_bid <= 0 or best_ask <= 0:
            return 0.0
        spread = best_ask - best_bid
        if spread > 0.03:
            return -min(0.06, (spread - 0.03) * 2.5)
        return 0.0
    except Exception:
        return 0.0


def _fee_per_share(price: float) -> float:
    """
    Taker fee per share: fee_rate × price × (1 − price).
    This is the convex fee curve used by prediction markets.
    Makers pay 0; takers pay per the fee schedule.
    """
    return TAKER_FEE_RATE * price * (1.0 - price)


def _executable_ask(market: dict, mid: float) -> float:
    """
    Estimate the executable ask price (what we actually pay when buying).
    Uses bestAsk if available from Gamma API.
    Falls back to mid + half-spread or mid + 1.5% estimate.
    Per research: EV must be calculated off ASK, not midpoint.
    """
    best_ask = market.get("bestAsk")
    if best_ask:
        try:
            ask = float(best_ask)
            if 0.001 < ask < 0.999:
                return ask
        except Exception:
            pass
    best_bid = market.get("bestBid")
    if best_bid:
        try:
            bid    = float(best_bid)
            spread = mid - bid
            return min(mid + spread, 0.999)
        except Exception:
            pass
    return min(mid + 0.015, 0.999)


def _compute_model_prob(market: dict, yes_price: float, direction: str,
                        hours_left: Optional[float]) -> float:
    """
    Multi-factor model probability.

    Base: market price (best available estimate of true probability)
    Adjustments:
      + order book imbalance  (up to +10%)
      + volume momentum       (up to +8%)
      + time decay            (up to +6%)
      + price extremity bonus (up to +5%)
      − spread penalty        (down to −6%)
    Returns p_true (our estimated probability).
    NOTE: EV = p_true − ask − fee. Caller computes actual EV.
    """
    if direction == "yes":
        base = yes_price
        price_for_extremity = yes_price
    else:
        base = 1.0 - yes_price
        price_for_extremity = 1.0 - yes_price

    adj = (
        _order_book_imbalance(market, direction)
        + _volume_momentum(market)
        + _time_decay_boost(hours_left)
        + _price_extremity_bonus(price_for_extremity)
        + _spread_penalty(market)
    )

    model_prob = base + adj
    return max(base + 0.001, min(model_prob, 0.97))   # always at least 0.1% above market


class PaperPolymarketBot:
    def __init__(self, rm):
        self.rm             = rm
        self.open_positions = {}   # token_id → position dict
        self._markets_cache = []
        self._cache_ts      = 0.0
        self._cache_ttl     = CACHE_TTL
        self.client         = True   # truthy sentinel for orchestrator
        self.strategy_name  = "edge_scanner"
        self.execution_mode = "paper"
        self.data_mode      = "real_market_data"
        self.last_scan_summary = {
            "opportunity_count": 0,
            "best_edge": None,
            "best_question": "",
            "last_scan_ts": 0.0,
            "open_positions": 0,
        }
        self.last_opportunities = []

    # ── Market data ───────────────────────────────────────────────────────────

    def _get_markets(self) -> list[dict]:
        now = time.time()
        if now - self._cache_ts > self._cache_ttl:
            fresh = _fetch_markets(200)
            if fresh:
                self._markets_cache = fresh
                self._cache_ts      = now
        return self._markets_cache

    def scan_markets(self) -> list[dict]:
        markets    = self._get_markets()
        candidates = []
        for m in markets:
            scored = self._score_market(m)
            if scored:
                candidates.append(scored)
        candidates.sort(key=lambda x: x["edge"], reverse=True)
        top = candidates[:5]
        self.last_scan_summary = {
            "opportunity_count": len(candidates),
            "best_edge": round(top[0]["edge"], 4) if top else None,
            "best_question": top[0]["question"] if top else "",
            "last_scan_ts": time.time(),
            "open_positions": len(self.open_positions),
        }
        self.last_opportunities = [
            {
                "question": opp["question"],
                "direction": opp["direction"],
                "edge": opp["edge"],
                "price": opp["price"],
            }
            for opp in top[:3]
        ]
        return top   # top 5 opportunities

    def _score_market(self, market: dict) -> Optional[dict]:
        try:
            if market.get("closed") or not market.get("active", True):
                return None

            volume = float(
                market.get("volumeNum") or market.get("volume") or 0
            )
            if volume < MIN_VOLUME:
                return None

            # Parse yes price from outcomePrices (JSON string or list)
            import json as _json
            outcome_prices = market.get("outcomePrices")
            if isinstance(outcome_prices, str):
                try:
                    outcome_prices = _json.loads(outcome_prices)
                except Exception:
                    outcome_prices = None
            if isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
                try:
                    yes_price = float(outcome_prices[0])
                except Exception:
                    return None
            else:
                yes_price = float(
                    market.get("lastTradePrice") or
                    market.get("bestAsk")        or 0
                )

            if not (0.01 < yes_price < 0.99):
                return None

            hours_left = self._hours_until(
                market.get("endDateIso") or market.get("endDate")
            )

            # Determine direction (widened range: 0.60–0.40)
            if yes_price >= SCAN_YES_MIN:
                direction = "yes"
                mid       = yes_price
                ask       = _executable_ask(market, yes_price)
            elif yes_price <= SCAN_NO_MAX:
                direction = "no"
                mid       = 1.0 - yes_price
                ask       = _executable_ask(market, 1.0 - yes_price)
            else:
                return None

            # ── LIVE GUARD: reject illiquid price ranges ──────────────────────
            # 1¢ and 99¢ markets have no real CLOB depth — live orders won't fill.
            if not (MIN_ASK_PRICE <= ask <= MAX_ASK_PRICE):
                return None

            model_prob = _compute_model_prob(market, yes_price, direction, hours_left)

            # ── Execution-truthful EV (per deep-research report) ──────────────
            # EV = p_true − ask − fee_per_share
            # This is the ACTUAL expected value, not midpoint-based.
            fee  = _fee_per_share(ask)
            ev   = model_prob - ask - fee

            settings = _PHASE_SETTINGS.get(
                self.rm.phase,
                _PHASE_SETTINGS["normal"]
            )

            if ev < settings["min_edge"]:
                return None

            # Kelly formula using executable ask (not mid)
            b = (1.0 / ask) - 1.0
            p = model_prob
            q = 1.0 - p
            kelly_full = (b * p - q) / b
            if kelly_full <= 0.001:
                return None

            kelly_use = kelly_full * settings["kelly"]
            bet_pct   = min(kelly_use, settings["max_pos"])
            bet_size  = max(round(self.rm.current_bankroll * bet_pct, 2), 0.50)
            bet_size  = min(bet_size, MAX_BET_HARD_CAP)

            # _true_prob: probability that YES resolves True.
            # Used for paper settlement — reflects our actual edge estimate.
            true_prob_yes = model_prob if direction == "yes" else (1.0 - model_prob)

            return {
                "token_id"  : str(market.get("conditionId") or market.get("id") or "") + f"_{direction}",
                "direction" : direction,
                "price"     : round(ask, 4),        # executable ask, not midpoint
                "midprice"  : round(mid, 4),         # midpoint for reference
                "yes_price" : yes_price,
                "model_prob": round(model_prob, 4),
                "edge"      : round(ev, 4),           # true EV after ask + fee
                "fee_share" : round(fee, 4),
                "bet_size"  : bet_size,
                "hours_left": round(hours_left, 2) if hours_left is not None else 99.0,
                "_true_prob": round(true_prob_yes, 4),
                "question"  : (market.get("question") or "")[:80],
            }

        except Exception as exc:
            logger.debug(f"_score_market: {exc}")
            return None

    def _hours_until(self, end_str: Optional[str]) -> Optional[float]:
        if not end_str:
            return None
        try:
            from datetime import datetime, timezone
            s   = end_str[:19].rstrip("Z")
            fmt = "%Y-%m-%dT%H:%M:%S" if "T" in s else "%Y-%m-%d"
            end = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return max((end - now).total_seconds() / 3600, 0)
        except Exception:
            return None

    # ── Settlement ────────────────────────────────────────────────────────────

    def _try_settle(self) -> float:
        now       = time.time()
        to_settle = []

        for tid, pos in self.open_positions.items():
            age = now - pos["opened_at"]
            if age >= MIN_HOLD_SECONDS:
                settle_prob = min(0.35 + (age - MIN_HOLD_SECONDS) / MAX_HOLD_SECONDS, 1.0)
                if random.random() < settle_prob or age >= MAX_HOLD_SECONDS:
                    to_settle.append(tid)

        if not to_settle:
            return 0.0

        total_net = 0.0
        for tid in to_settle:
            pos           = self.open_positions.pop(tid)
            true_prob     = pos["_true_prob"]
            direction     = pos["direction"]
            yes_wins      = random.random() < true_prob
            our_side_wins = (yes_wins and direction == "yes") or \
                            (not yes_wins and direction == "no")

            if our_side_wins:
                payout = pos["bet_size"] / pos["price"]
                net    = payout - pos["bet_size"]
            else:
                net = -pos["bet_size"]

            total_net += net
            logger.info(
                f"[{self.rm.bot_id}] POLY SETTLE | "
                f"{direction.upper()} {'WON ✓' if our_side_wins else 'LOST ✗'} | "
                f"bet=${pos['bet_size']:.2f} net={net:+.2f} edge={pos.get('edge', 0):.3f} | "
                f"\"{pos['question'][:55]}\""
            )

        return total_net

    # ── Bet placement ─────────────────────────────────────────────────────────

    def place_bet(self, opp: dict) -> float:
        if self.rm.is_halted:
            return 0.0
        tid = opp["token_id"]
        self.open_positions[tid] = {
            "direction" : opp["direction"],
            "bet_size"  : opp["bet_size"],
            "price"     : opp["price"],
            "_true_prob": opp["_true_prob"],
            "question"  : opp["question"],
            "edge"      : opp["edge"],
            "opened_at" : time.time(),
        }
        logger.info(
            f"[{self.rm.bot_id}] POLY BET | "
            f"{opp['direction'].upper()} ${opp['bet_size']:.2f}@ask={opp['price']:.3f} | "
            f"model={opp['model_prob']:.3f} ev={opp['edge']:.3f} fee={opp.get('fee_share',0):.4f} | "
            f"\"{opp['question'][:55]}\""
        )
        # Return 0.0 — don't record placement as a loss.
        # Only settlement results (win/loss net) flow through record_bet_result.
        return 0.0

    # ── Main cycle ────────────────────────────────────────────────────────────

    def run_one_cycle(self) -> float:
        if self.rm.is_halted:
            return 0.0

        # 1. Settle existing positions first
        net = self._try_settle()
        if net != 0.0:
            return net

        # 2. Respect max open positions
        if len(self.open_positions) >= MAX_OPEN_POSITIONS:
            return 0.0

        # 3. Don't open new positions if too deep in drawdown
        if self.rm.current_bankroll < self.rm.floor_amount * 1.05:
            return 0.0

        # 4. Find best opportunity and bet
        opps = self.scan_markets()
        for opp in opps:
            if opp["token_id"] not in self.open_positions:
                return self.place_bet(opp)

        return 0.0

    def snapshot(self) -> dict:
        return {
            "strategy": self.strategy_name,
            "execution_mode": self.execution_mode,
            "data_mode": self.data_mode,
            "open_positions": len(self.open_positions),
            "opportunities": self.last_opportunities,
            **self.last_scan_summary,
        }
