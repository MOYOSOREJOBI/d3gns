"""
Resolution Sniper Strategy — bot9_sniper

Targets Polymarket markets about to resolve where the outcome is near-certain
but the price hasn't fully converged to 1.0.

Edge mechanism: time-decay convergence.
  • "Will it rain in NYC today?" at 3pm with confirmed rain → YES at 0.92, should be 0.99
  • Collect the spread between current price and resolution value
  • Markets resolving in 0.5–6 hours with high-confidence pricing

Execution-truthful EV:
  • Trade at bestAsk, not midpoint
  • fee_per_share ≈ TAKER_FEE_RATE × price × (1−price)
  • EV = p_true − ask − fee
  • Only trade when price > NEAR_CERTAIN_THRESHOLD AND EV > MIN_EV

UMA oracle awareness (per deep-research):
  • Resolution is NOT instant — takes time through oracle process
  • Use conservative probability with "dispute haircut"
  • Default hold time reflects oracle delay
"""

import time, logging, random, requests, json as _json
from typing import Optional

logger = logging.getLogger(__name__)

GAMMA_URL            = "https://gamma-api.polymarket.com"
HEADERS              = {"User-Agent": "degen-sniper/1.0", "Accept": "application/json"}

TAKER_FEE_RATE       = 0.02
MIN_EV               = 0.020       # 2% net EV after ask + fee
NEAR_CERTAIN_MIN     = 0.87        # price must be above this to be "near certain"
NEAR_CERTAIN_MAX     = 0.97        # don't buy above 97¢ (too little upside)
HOURS_MIN            = 0.0         # any future market (already-resolved excluded via active check)
HOURS_MAX            = 720.0       # up to 30 days — collect near-certain discount over time
MIN_VOLUME           = 150
CACHE_TTL            = 20
MAX_OPEN             = 4
MIN_HOLD_S           = 10
MAX_HOLD_S           = 45
DISPUTE_HAIRCUT      = 0.04        # shave 4% from certainty to account for oracle risk

_markets_cache: dict = {"data": [], "ts": 0.0}

_PHASE = {
    "floor"     : (0.10, 0.02),
    "ultra_safe": (0.15, 0.03),
    "safe"      : (0.20, 0.05),
    "careful"   : (0.25, 0.06),
    "normal"    : (0.30, 0.08),
    "aggressive": (0.40, 0.11),
    "turbo"     : (0.50, 0.14),
    "milestone" : (0.30, 0.08),
}


def _fetch_markets() -> list[dict]:
    now = time.time()
    if now - _markets_cache["ts"] < CACHE_TTL and _markets_cache["data"]:
        return _markets_cache["data"]
    try:
        r = requests.get(
            f"{GAMMA_URL}/markets",
            params={"active": "true", "closed": "false", "limit": 300},
            headers=HEADERS, timeout=10,
        )
        r.raise_for_status()
        data    = r.json()
        markets = data if isinstance(data, list) else data.get("markets", [])
        _markets_cache.update({"data": markets, "ts": now})
        return markets
    except Exception as e:
        logger.debug(f"Sniper market fetch: {e}")
        return _markets_cache.get("data", [])


def _parse_op(market: dict) -> list:
    op = market.get("outcomePrices", "[]")
    if isinstance(op, list):
        try:
            return [float(x) for x in op]
        except Exception:
            return []
    try:
        return [float(x) for x in _json.loads(op)]
    except Exception:
        return []


def _hours_until(end_str: Optional[str]) -> Optional[float]:
    if not end_str:
        return None
    try:
        from datetime import datetime, timezone
        s   = end_str[:19].rstrip("Z")
        fmt = "%Y-%m-%dT%H:%M:%S" if "T" in s else "%Y-%m-%d"
        end = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max((end - now).total_seconds() / 3600, 0.0)
    except Exception:
        return None


def _fee_per_share(price: float) -> float:
    return TAKER_FEE_RATE * price * (1.0 - price)


def _executable_ask(market: dict, mid: float) -> float:
    best_ask = market.get("bestAsk")
    if best_ask:
        try:
            return min(float(best_ask), 0.999)
        except Exception:
            pass
    best_bid = market.get("bestBid")
    if best_bid and best_ask:
        try:
            spread = float(best_ask) - float(best_bid)
            return mid + spread / 2
        except Exception:
            pass
    return min(mid + 0.012, 0.999)


def _score_market(market: dict) -> Optional[dict]:
    try:
        if market.get("closed") or not market.get("active", True):
            return None

        volume = float(market.get("volumeNum") or market.get("volume") or 0)
        if volume < MIN_VOLUME:
            return None

        end_str    = market.get("endDateIso") or market.get("endDate")
        hours_left = _hours_until(end_str)
        if hours_left is None or not (HOURS_MIN <= hours_left <= HOURS_MAX):
            return None

        op = _parse_op(market)
        if len(op) < 2:
            return None

        yes_mid = op[0]
        no_mid  = op[1]

        # Pick the highest-confidence outcome
        if yes_mid >= NEAR_CERTAIN_MIN:
            direction = "yes"
            mid       = yes_mid
        elif no_mid >= NEAR_CERTAIN_MIN:
            direction = "no"
            mid       = no_mid
        else:
            return None

        ask = _executable_ask(market, mid)
        if ask > NEAR_CERTAIN_MAX:
            return None  # too expensive, upside too small

        # Certainty model:
        # Base from current price + time decay bonus + spread tightness
        # Apply UMA oracle dispute haircut
        time_bonus  = max(0.0, 0.05 * (1.0 - hours_left / HOURS_MAX))  # up to +5% near expiry
        spread_val  = float(market.get("bestAsk") or mid+0.01) - float(market.get("bestBid") or mid-0.01)
        spread_pen  = min(0.03, spread_val * 1.0)  # penalise wide spreads

        certainty   = mid + time_bonus - spread_pen - DISPUTE_HAIRCUT
        certainty   = max(mid + 0.005, min(certainty, 0.97))

        fee = _fee_per_share(ask)
        ev  = certainty - ask - fee

        if ev < MIN_EV:
            return None

        # Kelly sizing
        b          = (1.0 / ask) - 1.0
        kelly_full = (b * certainty - (1.0 - certainty)) / b
        if kelly_full <= 0.001:
            return None

        true_prob_yes = certainty if direction == "yes" else (1.0 - certainty)

        return {
            "token_id"   : str(market.get("conditionId") or market.get("id") or "") + f"_snipe_{direction}",
            "direction"  : direction,
            "price"      : round(ask, 4),
            "midprice"   : round(mid, 4),
            "yes_price"  : round(yes_mid, 4),
            "model_prob" : round(certainty, 4),
            "edge"       : round(ev, 4),
            "kelly_full" : round(kelly_full, 4),
            "hours_left" : round(hours_left, 2),
            "fee_share"  : round(fee, 4),
            "_true_prob" : round(true_prob_yes, 4),
            "question"   : (market.get("question") or "")[:80],
        }
    except Exception as e:
        logger.debug(f"Sniper _score_market: {e}")
        return None


def _live_place_order(client, token_id: str, price: float, size: float, bot_id: str) -> bool:
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        order = client.create_market_order(OrderArgs(
            token_id=token_id, price=price, size=size,
            side="BUY", order_type=OrderType.MARKET,
        ))
        client.post_order(order)
        logger.info(f"[{bot_id}] LIVE ORDER token={token_id[:12]} size={size:.2f} @ {price:.4f}")
        return True
    except Exception as e:
        logger.error(f"[{bot_id}] Live order failed: {e}")
        return False


class ResolutionSniperBot:
    """
    Resolution sniper bot — real Gamma data with paper execution only.
    Finds markets where outcome is near-certain but price hasn't converged.
    """

    def __init__(self, rm, live_client=None):
        self.rm             = rm
        if live_client is not None:
            logger.warning(f"[{rm.bot_id}] live_client ignored; paper execution only")
        self.live_client    = None
        self.open_positions = {}
        self.client         = True
        self.strategy_name  = "resolution_sniper"
        self.execution_mode = "paper"
        self.data_mode      = "real_market_data"
        self.last_scan_summary = {
            "opportunity_count": 0,
            "best_edge": None,
            "best_question": "",
            "best_hours_left": None,
            "last_scan_ts": 0.0,
            "open_positions": 0,
        }
        self.last_opportunities = []

    def scan_markets(self) -> list[dict]:
        markets    = _fetch_markets()
        candidates = []
        for m in markets:
            scored = _score_market(m)
            if scored:
                candidates.append(scored)
        # Sort by hours_left ASC (most urgent first), then edge DESC
        candidates.sort(key=lambda x: (x["hours_left"], -x["edge"]))
        top = candidates[:5]
        self.last_scan_summary = {
            "opportunity_count": len(candidates),
            "best_edge": round(top[0]["edge"], 4) if top else None,
            "best_question": top[0]["question"] if top else "",
            "best_hours_left": round(top[0]["hours_left"], 2) if top else None,
            "last_scan_ts": time.time(),
            "open_positions": len(self.open_positions),
        }
        self.last_opportunities = [
            {
                "question": opp["question"],
                "direction": opp["direction"],
                "edge": opp["edge"],
                "price": opp["price"],
                "hours_left": opp["hours_left"],
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
                f"[{self.rm.bot_id}] SNIPE SETTLE | "
                f"{direction.upper()} {'WIN✓' if our_wins else 'LOSS✗'} | "
                f"{pos['hours_left']:.1f}h left | "
                f"net={net:+.2f} edge={pos.get('edge',0):.3f} | "
                f"\"{pos['question'][:45]}\""
            )
        return total

    def place_bet(self, opp: dict) -> float:
        if self.rm.is_halted:
            return 0.0
        phase_cfg = _PHASE.get(self.rm.phase, _PHASE["normal"])
        kelly_use = opp["kelly_full"] * phase_cfg[0]
        bet_pct   = min(kelly_use, phase_cfg[1])
        bet_size  = max(round(self.rm.current_bankroll * bet_pct, 2), 0.50)
        bet_size  = min(bet_size, 18.0)

        tid = opp["token_id"]
        self.open_positions[tid] = {
            "direction" : opp["direction"],
            "bet_size"  : bet_size,
            "price"     : opp["price"],
            "_true_prob": opp["_true_prob"],
            "hours_left": opp["hours_left"],
            "edge"      : opp["edge"],
            "question"  : opp["question"],
            "opened_at" : time.time(),
            "live"      : self.live_client is not None,
        }
        mode = "PAPER"
        logger.info(
            f"[{self.rm.bot_id}] [{mode}] SNIPE BET | "
            f"{opp['direction'].upper()} ${bet_size:.2f}@{opp['price']:.3f} | "
            f"ev={opp['edge']:.3f} | {opp['hours_left']:.1f}h left | "
            f"\"{opp['question'][:50]}\""
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
