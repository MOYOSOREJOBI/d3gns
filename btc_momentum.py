"""
BTC Momentum Strategy — bot7_momentum

Edge: Binance BTC spot price vs Polymarket BTC threshold markets.
Polymarket prices lag Binance by 2–30 seconds. When BTC clearly crossed a
threshold (with buffer), the YES/NO price hasn't fully converged → buy it.

Execution-truthful EV (per deep-research guidance):
  • Use bestAsk for buy orders, NOT midpoint
  • fee_per_share ≈ TAKER_FEE_RATE × price × (1 - price)
  • EV = p_true − ask − fee_per_share
  • Only trade when EV > min_edge threshold
"""

import re, time, logging, random, requests, json as _json
from typing import Optional

logger = logging.getLogger(__name__)

GAMMA_URL   = "https://gamma-api.polymarket.com"
BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"
HEADERS     = {"User-Agent": "degen-btc-momentum/1.0", "Accept": "application/json"}

TAKER_FEE_RATE  = 0.02        # ~2% on liquid markets (fetch dynamically in live mode)
MIN_EV          = 0.025       # 2.5% net EV after ask spread + fee
BTC_BUFFER_PCT  = 0.003       # BTC must be 0.3% past threshold to trade
MAX_PRICE       = 0.92        # don't buy above 92¢ (too little upside)
MIN_PRICE       = 0.08        # don't buy below 8¢
CACHE_TTL_BTC   = 4           # seconds between Binance price refreshes
CACHE_TTL_MKTS  = 30          # seconds between market scan refreshes
MAX_OPEN        = 3
MIN_HOLD_S      = 8
MAX_HOLD_S      = 35

# Phase → (kelly_frac, max_pos_pct)
_PHASE = {
    "floor"     : (0.08, 0.02),
    "ultra_safe": (0.12, 0.03),
    "safe"      : (0.18, 0.05),
    "careful"   : (0.22, 0.06),
    "normal"    : (0.35, 0.10),
    "aggressive": (0.50, 0.14),
    "turbo"     : (0.65, 0.18),
    "milestone" : (0.35, 0.10),
}

_btc_price_cache: dict = {"price": None, "ts": 0.0}
_markets_cache: dict   = {"data": [], "ts": 0.0}


def _get_btc_price() -> Optional[float]:
    now = time.time()
    if now - _btc_price_cache["ts"] < CACHE_TTL_BTC and _btc_price_cache["price"]:
        return _btc_price_cache["price"]
    try:
        r = requests.get(f"{BINANCE_URL}?symbol=BTCUSDT", headers=HEADERS, timeout=5)
        r.raise_for_status()
        price = float(r.json()["price"])
        _btc_price_cache.update({"price": price, "ts": now})
        return price
    except Exception as e:
        logger.debug(f"BTC price fetch: {e}")
        return _btc_price_cache.get("price")


def _fetch_btc_markets() -> list[dict]:
    now = time.time()
    if now - _markets_cache["ts"] < CACHE_TTL_MKTS and _markets_cache["data"]:
        return _markets_cache["data"]
    try:
        r = requests.get(
            f"{GAMMA_URL}/markets",
            params={"active": "true", "closed": "false", "limit": 300},
            headers=HEADERS, timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        markets = data if isinstance(data, list) else data.get("markets", [])
        btc_markets = [m for m in markets if _is_btc_threshold_market(m)]
        _markets_cache.update({"data": btc_markets, "ts": now})
        logger.debug(f"BTC momentum: {len(btc_markets)} threshold markets in {len(markets)} total")
        return btc_markets
    except Exception as e:
        logger.debug(f"BTC market fetch: {e}")
        return _markets_cache.get("data", [])


def _parse_op(market: dict) -> list:
    """Parse outcomePrices from market — handles both list and JSON string."""
    op = market.get("outcomePrices", "[]")
    if isinstance(op, list):
        try:
            return [float(x) for x in op]
        except Exception:
            return []
    try:
        parsed = _json.loads(op)
        return [float(x) for x in parsed]
    except Exception:
        return []


def _is_btc_threshold_market(market: dict) -> bool:
    q = (market.get("question") or "").lower()
    return ("btc" in q or "bitcoin" in q) and _parse_threshold(market.get("question", "")) is not None


def _parse_threshold(question: str) -> Optional[float]:
    """
    Extract BTC/crypto price threshold from question text.
    Handles: $1m / $1M / $75k / $75,000 / $75000
    Returns value in dollars.
    """
    patterns = [
        (r'\$\s*([\d\.]+)\s*[mM]\b',   1_000_000),   # $1m, $1.5M
        (r'\$\s*([\d,]+)\s*[kK]\b',    1_000),        # $75k, $100K
        (r'\$\s*([\d]{1,3}(?:,\d{3})+)', 1),          # $75,000
        (r'\$\s*([\d]{4,9})\b',          1),           # $75000
        (r'\b([\d\.]+)\s*[mM]\s*(?:usd|usdt|dollars?)?\b', 1_000_000),  # 1m usd
        (r'\b([\d]+)\s*[kK]\s*(?:usd|usdt|dollars?)?\b',   1_000),      # 100k usd
    ]
    for pat, mult in patterns:
        m = re.search(pat, question, re.IGNORECASE)
        if m:
            raw = m.group(1).replace(",", "")
            try:
                val = float(raw) * mult
                if 1_000 < val < 100_000_000:
                    return val
            except ValueError:
                continue
    return None


def _fee_per_share(price: float) -> float:
    """Taker fee per share: fee_rate × p × (1−p). Makers pay 0."""
    return TAKER_FEE_RATE * price * (1.0 - price)


def _executable_ask(market: dict, midprice: float) -> float:
    """
    Estimate executable ask price (what we actually pay).
    Use bestAsk if available; else mid + half estimated spread.
    Spread estimated from bestBid/bestAsk; fallback 2%.
    """
    best_ask = market.get("bestAsk")
    if best_ask:
        try:
            return float(best_ask)
        except Exception:
            pass
    best_bid = market.get("bestBid")
    if best_bid and best_ask:
        try:
            spread = float(best_ask) - float(best_bid)
            return midprice + spread / 2
        except Exception:
            pass
    # Fallback: add 1.5% estimated spread
    return min(midprice + 0.015, 0.99)


def _score_btc_market(market: dict, btc_price: float) -> Optional[dict]:
    try:
        if market.get("closed") or not market.get("active", True):
            return None

        question  = market.get("question", "")
        threshold = _parse_threshold(question)
        if threshold is None:
            return None

        op = _parse_op(market)
        if len(op) < 2:
            return None
        yes_mid = op[0]
        no_mid  = op[1]

        # Determine direction: is BTC clearly above or below threshold?
        btc_vs_threshold = (btc_price - threshold) / threshold

        if btc_vs_threshold >= BTC_BUFFER_PCT:
            # BTC is above threshold → YES should win
            direction = "yes"
            midprice  = yes_mid
            ask       = _executable_ask(market, yes_mid)
            # True prob: BTC is above threshold now → very likely YES
            certainty = min(0.97, 0.80 + abs(btc_vs_threshold) * 5.0)
        elif btc_vs_threshold <= -BTC_BUFFER_PCT:
            # BTC is below threshold → NO should win
            direction = "no"
            midprice  = no_mid
            ask       = _executable_ask(market, no_mid)
            certainty = min(0.97, 0.80 + abs(btc_vs_threshold) * 5.0)
        else:
            return None  # too close to threshold — uncertain

        if not (MIN_PRICE < ask < MAX_PRICE):
            return None

        fee = _fee_per_share(ask)
        ev  = certainty - ask - fee

        if ev < MIN_EV:
            return None

        # Kelly sizing
        b          = (1.0 / ask) - 1.0
        kelly_full = (b * certainty - (1.0 - certainty)) / b
        if kelly_full <= 0:
            return None

        return {
            "token_id"   : str(market.get("conditionId") or market.get("id") or "") + f"_{direction}",
            "direction"  : direction,
            "price"      : ask,
            "midprice"   : round(midprice, 4),
            "yes_price"  : yes_mid,
            "model_prob" : round(certainty, 4),
            "edge"       : round(ev, 4),
            "kelly_full" : round(kelly_full, 4),
            "btc_price"  : round(btc_price, 2),
            "threshold"  : round(threshold, 2),
            "btc_gap_pct": round(btc_vs_threshold * 100, 2),
            "fee_share"  : round(fee, 4),
            "question"   : question[:80],
            "_true_prob" : round(certainty if direction == "yes" else 1.0 - certainty, 4),
        }

    except Exception as exc:
        logger.debug(f"_score_btc_market: {exc}")
        return None


def _live_place_order(client, token_id: str, price: float, size: float, bot_id: str) -> bool:
    """Place real market order on Polymarket CLOB. Returns True on success."""
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        order = client.create_market_order(OrderArgs(
            token_id   = token_id,
            price      = price,
            size       = size,
            side       = "BUY",
            order_type = OrderType.MARKET,
        ))
        client.post_order(order)
        logger.info(f"[{bot_id}] LIVE ORDER PLACED token={token_id[:12]} size={size:.2f} @ {price:.4f}")
        return True
    except Exception as e:
        logger.error(f"[{bot_id}] Live order failed: {e}")
        return False


class BtcMomentumBot:
    """
    BTC momentum bot — real Binance/Gamma data with paper execution only.
    Uses real Binance BTC price + real Polymarket Gamma market data.
    """

    def __init__(self, rm, live_client=None):
        self.rm             = rm
        if live_client is not None:
            logger.warning(f"[{rm.bot_id}] live_client ignored; paper execution only")
        self.live_client    = None
        self.open_positions = {}
        self.client         = True
        self.strategy_name  = "btc_momentum"
        self.execution_mode = "paper"
        self.data_mode      = "real_market_data"
        self.last_scan_summary = {
            "btc_price": None,
            "opportunity_count": 0,
            "best_edge": None,
            "best_question": "",
            "last_scan_ts": 0.0,
            "open_positions": 0,
        }
        self.last_opportunities = []

    def scan_markets(self) -> list[dict]:
        btc = _get_btc_price()
        if btc is None:
            self.last_scan_summary = {
                **self.last_scan_summary,
                "btc_price": None,
                "opportunity_count": 0,
                "best_edge": None,
                "best_question": "",
                "last_scan_ts": time.time(),
                "open_positions": len(self.open_positions),
            }
            return []
        markets = _fetch_btc_markets()
        candidates = []
        for m in markets:
            scored = _score_btc_market(m, btc)
            if scored:
                candidates.append(scored)
        candidates.sort(key=lambda x: x["edge"], reverse=True)
        top = candidates[:3]
        self.last_scan_summary = {
            "btc_price": round(btc, 2),
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
                "btc_price": opp["btc_price"],
                "threshold": opp["threshold"],
            }
            for opp in top[:3]
        ]
        return top

    def _try_settle(self) -> float:
        now       = time.time()
        to_settle = []
        for tid, pos in self.open_positions.items():
            age = now - pos["opened_at"]
            if age >= MIN_HOLD_S:
                prob = min(0.40 + (age - MIN_HOLD_S) / MAX_HOLD_S, 1.0)
                if random.random() < prob or age >= MAX_HOLD_S:
                    to_settle.append(tid)
        if not to_settle:
            return 0.0
        total = 0.0
        for tid in to_settle:
            pos       = self.open_positions.pop(tid)
            true_prob = pos["_true_prob"]
            direction = pos["direction"]
            yes_wins  = random.random() < true_prob
            our_wins  = (yes_wins and direction == "yes") or (not yes_wins and direction == "no")
            if our_wins:
                net = pos["bet_size"] / pos["price"] - pos["bet_size"]
            else:
                net = -pos["bet_size"]
            total += net
            logger.info(
                f"[{self.rm.bot_id}] BTC SETTLE | "
                f"{direction.upper()} {'WIN✓' if our_wins else 'LOSS✗'} | "
                f"BTC=${pos['btc_price']:.0f} vs ${pos['threshold']:.0f} | "
                f"net={net:+.2f} edge={pos.get('edge', 0):.3f}"
            )
        return total

    def place_bet(self, opp: dict) -> float:
        if self.rm.is_halted:
            return 0.0
        phase_cfg = _PHASE.get(self.rm.phase, _PHASE["normal"])
        kelly_use = opp["kelly_full"] * phase_cfg[0]
        bet_pct   = min(kelly_use, phase_cfg[1])
        bet_size  = max(round(self.rm.current_bankroll * bet_pct, 2), 0.50)
        bet_size  = min(bet_size, 20.0)

        # ── Live CLOB execution ───────────────────────────────────────────────
        tid = opp["token_id"]
        self.open_positions[tid] = {
            "direction" : opp["direction"],
            "bet_size"  : bet_size,
            "price"     : opp["price"],
            "midprice"  : opp["midprice"],
            "threshold" : opp["threshold"],
            "btc_price" : opp["btc_price"],
            "_true_prob": opp["_true_prob"],
            "edge"      : opp["edge"],
            "opened_at" : time.time(),
            "live"      : self.live_client is not None,
        }
        mode = "PAPER"
        logger.info(
            f"[{self.rm.bot_id}] [{mode}] BTC BET | "
            f"{opp['direction'].upper()} ${bet_size:.2f}@{opp['price']:.3f} | "
            f"BTC=${opp['btc_price']:.0f} vs thr=${opp['threshold']:.0f} "
            f"(gap={opp['btc_gap_pct']:+.1f}%) | ev={opp['edge']:.3f}"
        )
        return 0.0

    def run_one_cycle(self) -> float:
        if self.rm.is_halted:
            return 0.0

        net = self._try_settle()
        if net != 0.0:
            return net

        if len(self.open_positions) >= MAX_OPEN:
            return 0.0

        if self.rm.current_bankroll < self.rm.floor_amount * 1.05:
            return 0.0

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
