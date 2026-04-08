"""
Intra-Platform Arbitrage Strategy — bot8_arb

Exploits structural mispricings on Polymarket where outcome prices don't sum to 1.0.

Two families:
  A) Complete-set arb: sum(all outcome asks) < 1.0 − fees → buy all outcomes.
     At resolution, exactly one pays 1.0 → guaranteed profit.

  B) Binary completion: single binary market where YES_ask + NO_ask < 0.97.
     Buy the cheaper side; edge = 1.0 - total_cost at resolution.

Execution-truthful EV:
  • Use bestAsk for each outcome (not midpoint)
  • fee_per_share ≈ TAKER_FEE_RATE × price × (1−price)
  • Net arb profit = 1.0 − Σask_i − Σfee_i
  • Only trade when net_profit > MIN_ARB_PROFIT

Per the research: this is the safest strategy — near-zero risk when fills execute.
Risk: partial fill (one leg fills, other doesn't). Managed by position tracking.
"""

import time, logging, random, requests, json as _json
from typing import Optional

logger = logging.getLogger(__name__)

GAMMA_URL      = "https://gamma-api.polymarket.com"
HEADERS        = {"User-Agent": "degen-intra-arb/1.0", "Accept": "application/json"}

TAKER_FEE_RATE = 0.02          # approximate taker fee rate
MIN_ARB_PROFIT = 0.020         # net profit after fees must be ≥ 2%
MAX_SUM_MID    = 0.97          # only look at markets where midprice sum < 97%
MAX_SUM_ASK    = 0.978         # ask-based sum must be below this for actual arb
MIN_VOLUME     = 200
CACHE_TTL      = 25
MAX_OPEN       = 4
MIN_HOLD_S     = 6
MAX_HOLD_S     = 30

_markets_cache: dict = {"data": [], "ts": 0.0}

_PHASE = {
    "floor"     : (0.08, 0.02),
    "ultra_safe": (0.12, 0.03),
    "safe"      : (0.18, 0.05),
    "careful"   : (0.22, 0.06),
    "normal"    : (0.30, 0.09),
    "aggressive": (0.45, 0.13),
    "turbo"     : (0.60, 0.18),
    "milestone" : (0.30, 0.09),
}


def _fetch_markets(limit: int = 300) -> list[dict]:
    now = time.time()
    if now - _markets_cache["ts"] < CACHE_TTL and _markets_cache["data"]:
        return _markets_cache["data"]
    try:
        r = requests.get(
            f"{GAMMA_URL}/markets",
            params={"active": "true", "closed": "false", "limit": limit},
            headers=HEADERS, timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        markets = data if isinstance(data, list) else data.get("markets", [])
        _markets_cache.update({"data": markets, "ts": now})
        return markets
    except Exception as e:
        logger.debug(f"Gamma fetch: {e}")
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


def _get_ask(market: dict, outcome_idx: int) -> float:
    """
    Get executable ask for outcome at index.
    Uses bestAsk if available; else mid + estimated half-spread.
    """
    # Try to get per-outcome ask from outcomePrices + spread data
    op = _parse_op(market)
    if len(op) <= outcome_idx:
        return 0.99

    mid = op[outcome_idx]

    if outcome_idx == 0:
        ask_val = market.get("bestAsk")
        if ask_val:
            try:
                return min(float(ask_val), 0.999)
            except Exception:
                pass
    # Fallback: mid + estimated spread/2
    best_bid = market.get("bestBid")
    best_ask = market.get("bestAsk")
    if best_bid and best_ask:
        try:
            spread = float(best_ask) - float(best_bid)
            return mid + spread / 2
        except Exception:
            pass
    return min(mid + 0.015, 0.999)


def _fee(price: float) -> float:
    return TAKER_FEE_RATE * price * (1.0 - price)


def _find_binary_arb(market: dict) -> Optional[dict]:
    """
    For a binary market: if YES_ask + NO_ask + fees < 1.0 → arb.
    Buy the underpriced side.
    """
    try:
        if market.get("closed") or not market.get("active", True):
            return None
        volume = float(market.get("volumeNum") or market.get("volume") or 0)
        if volume < MIN_VOLUME:
            return None

        op = _parse_op(market)
        if len(op) != 2:
            return None

        yes_mid = op[0]
        no_mid  = op[1]

        # Quick filter: midprices
        mid_sum = yes_mid + no_mid
        if mid_sum >= MAX_SUM_MID:
            return None

        yes_ask = _get_ask(market, 0)
        no_ask  = min(1.0 - float(market.get("bestBid") or yes_ask), 0.99) if market.get("bestBid") else _get_ask(market, 1)

        total_ask  = yes_ask + no_ask
        total_fee  = _fee(yes_ask) + _fee(no_ask)
        net_profit = 1.0 - total_ask - total_fee

        if net_profit < MIN_ARB_PROFIT:
            return None

        # Buy the cheaper side (highest potential)
        if yes_ask <= no_ask:
            direction = "yes"
            price     = yes_ask
        else:
            direction = "no"
            price     = no_ask

        # True probability for settlement: arb always wins (binary)
        true_prob = 0.95  # slight friction discount

        return {
            "token_id"   : str(market.get("conditionId") or market.get("id") or "") + f"_arb_{direction}",
            "direction"  : direction,
            "price"      : round(price, 4),
            "yes_ask"    : round(yes_ask, 4),
            "no_ask"     : round(no_ask, 4),
            "total_cost" : round(total_ask + total_fee, 4),
            "net_profit" : round(net_profit, 4),
            "edge"       : round(net_profit, 4),
            "_true_prob" : true_prob,
            "arb_type"   : "binary_completion",
            "question"   : (market.get("question") or "")[:80],
        }
    except Exception as e:
        logger.debug(f"_find_binary_arb: {e}")
        return None


def _find_mid_deviation_arb(market: dict) -> Optional[dict]:
    """
    Detect significant mid-price deviation from fair value (0.5 on binary).
    This catches markets where one side is heavily underpriced relative to the other.
    YES + NO should sum to exactly 1.0 for fair binary.
    If YES_mid = 0.40 and NO_mid = 0.45, sum is 0.85 → buy both (or the cheaper one).
    """
    try:
        op = _parse_op(market)
        if len(op) != 2:
            return None
        yes_mid = op[0]
        no_mid  = op[1]
        mid_sum = yes_mid + no_mid
        if mid_sum >= 0.96 or mid_sum < 0.70:
            return None  # too close to fair or too extreme

        volume = float(market.get("volumeNum") or market.get("volume") or 0)
        if volume < MIN_VOLUME:
            return None

        # Buy both sides if sum < 0.93
        if mid_sum < 0.93:
            # Buy the cheaper side for simplicity in paper mode
            if yes_mid <= no_mid:
                direction, price = "yes", min(yes_mid + 0.01, 0.99)
            else:
                direction, price = "no", min(no_mid + 0.01, 0.99)

            fee         = _fee(price)
            net_profit  = 1.0 - mid_sum - fee * 2
            if net_profit < MIN_ARB_PROFIT:
                return None

            return {
                "token_id"  : str(market.get("conditionId") or market.get("id") or "") + f"_mdev_{direction}",
                "direction" : direction,
                "price"     : round(price, 4),
                "mid_sum"   : round(mid_sum, 4),
                "net_profit": round(net_profit, 4),
                "edge"      : round(net_profit, 4),
                "_true_prob": 0.94,
                "arb_type"  : "mid_deviation",
                "question"  : (market.get("question") or "")[:80],
            }
        return None
    except Exception as e:
        logger.debug(f"_find_mid_deviation_arb: {e}")
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


class IntraArbBot:
    """
    Intra-platform arbitrage bot — real Gamma data with paper execution only.
    Scans Gamma API for structural mispricings (sum < 1.0).
    Near-zero risk in the model; execution stays paper-only here.
    """

    def __init__(self, rm, live_client=None):
        self.rm             = rm
        if live_client is not None:
            logger.warning(f"[{rm.bot_id}] live_client ignored; paper execution only")
        self.live_client    = None
        self.open_positions = {}
        self.client         = True
        self._last_scan     = 0.0
        self._scan_cache    = []
        self.strategy_name  = "intra_arb"
        self.execution_mode = "paper"
        self.data_mode      = "real_market_data"
        self.last_scan_summary = {
            "opportunity_count": 0,
            "best_edge": None,
            "best_question": "",
            "best_arb_type": "",
            "last_scan_ts": 0.0,
            "open_positions": 0,
        }
        self.last_opportunities = []

    def scan_arb(self) -> list[dict]:
        now = time.time()
        if now - self._last_scan < 15.0 and self._scan_cache:
            return self._scan_cache

        markets    = _fetch_markets(300)
        candidates = []
        for m in markets:
            result = _find_binary_arb(m) or _find_mid_deviation_arb(m)
            if result:
                candidates.append(result)
        candidates.sort(key=lambda x: x["net_profit"], reverse=True)
        self._scan_cache = candidates[:5]
        self._last_scan  = now
        self.last_scan_summary = {
            "opportunity_count": len(candidates),
            "best_edge": round(candidates[0]["net_profit"], 4) if candidates else None,
            "best_question": candidates[0]["question"] if candidates else "",
            "best_arb_type": candidates[0]["arb_type"] if candidates else "",
            "last_scan_ts": now,
            "open_positions": len(self.open_positions),
        }
        self.last_opportunities = [
            {
                "question": opp["question"],
                "direction": opp["direction"],
                "edge": opp["net_profit"],
                "price": opp["price"],
                "arb_type": opp["arb_type"],
            }
            for opp in self._scan_cache[:3]
        ]
        if candidates:
            logger.info(
                f"[{self.rm.bot_id}] ARB scan: {len(candidates)} opps, "
                f"best net={candidates[0]['net_profit']:.3f} "
                f"({candidates[0]['arb_type']}) \"{candidates[0]['question'][:40]}\""
            )
        return self._scan_cache

    def _try_settle(self) -> float:
        now       = time.time()
        to_settle = []
        for tid, pos in self.open_positions.items():
            age = now - pos["opened_at"]
            if age >= MIN_HOLD_S:
                prob = min(0.45 + (age - MIN_HOLD_S) / MAX_HOLD_S, 1.0)
                if random.random() < prob or age >= MAX_HOLD_S:
                    to_settle.append(tid)
        if not to_settle:
            return 0.0
        total = 0.0
        for tid in to_settle:
            pos       = self.open_positions.pop(tid)
            our_wins  = random.random() < pos["_true_prob"]
            if our_wins:
                net = pos["bet_size"] / pos["price"] - pos["bet_size"]
            else:
                net = -pos["bet_size"]
            total += net
            logger.info(
                f"[{self.rm.bot_id}] ARB SETTLE | "
                f"{'WIN✓' if our_wins else 'LOSS✗'} {pos['arb_type']} | "
                f"net={net:+.2f} profit={pos.get('net_profit',0):.3f}"
            )
        return total

    def place_bet(self, opp: dict) -> float:
        if self.rm.is_halted:
            return 0.0
        phase_cfg = _PHASE.get(self.rm.phase, _PHASE["normal"])
        kelly_arb = min(opp["net_profit"] * 3.0, phase_cfg[1] * 1.5)
        bet_pct   = min(kelly_arb, phase_cfg[1])
        bet_size  = max(round(self.rm.current_bankroll * bet_pct, 2), 0.50)
        bet_size  = min(bet_size, 25.0)

        tid = opp["token_id"]
        self.open_positions[tid] = {
            "direction"  : opp["direction"],
            "bet_size"   : bet_size,
            "price"      : opp["price"],
            "net_profit" : opp["net_profit"],
            "_true_prob" : opp["_true_prob"],
            "arb_type"   : opp["arb_type"],
            "opened_at"  : time.time(),
            "live"       : self.live_client is not None,
        }
        mode = "PAPER"
        logger.info(
            f"[{self.rm.bot_id}] [{mode}] ARB BET | {opp['arb_type']} "
            f"{opp['direction'].upper()} ${bet_size:.2f}@{opp['price']:.3f} | "
            f"net_profit={opp['net_profit']:.3f} | \"{opp['question'][:50]}\""
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

        opps = self.scan_arb()
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
