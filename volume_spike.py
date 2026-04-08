"""
Volume Spike Detector Strategy — bot10_volume

Catches markets where sudden volume surges signal news-driven repricing.
Large orders take 5–30 minutes to fully reprice → early movers profit.

Flow detection:
  • Cache market snapshots every 5 minutes
  • volume_ratio = current_volume / prev_5min_volume
  • If ratio > SPIKE_THRESHOLD AND price is moving: follow the direction
  • Require price change > 2% to confirm directional flow (not noise)
  • Stop: if price reverses > REVERSAL_STOP, exit (not implemented in paper — use hold timeout)

Execution-truthful EV (per deep-research):
  • Use bestAsk for buy, not midpoint
  • fee_per_share ≈ TAKER_FEE_RATE × price × (1−price)
  • EV = momentum_edge − ask_premium − fee
"""

import time, logging, random, requests, json as _json
from typing import Optional
import database as db

logger = logging.getLogger(__name__)

GAMMA_URL       = "https://gamma-api.polymarket.com"
HEADERS         = {"User-Agent": "degen-vol-spike/1.0", "Accept": "application/json"}

TAKER_FEE_RATE  = 0.02
SPIKE_THRESHOLD = 4.0          # volume must be 4x the 5-min baseline
MIN_PRICE_MOVE  = 0.018        # price must have moved ≥ 1.8% to confirm direction
MIN_EV          = 0.022        # net EV after ask + fee
MIN_VOLUME      = 300
CACHE_TTL       = 18           # market cache TTL seconds
SNAPSHOT_EVERY  = 300          # snapshot every 5 minutes
MAX_OPEN        = 4
MIN_HOLD_S      = 8
MAX_HOLD_S      = 40

_markets_cache: dict = {"data": [], "ts": 0.0}
_vol_snapshots: dict = {}      # market_id → {vol, yes_price, ts}
_last_snapshot: float = 0.0

_PHASE = {
    "floor"     : (0.08, 0.02),
    "ultra_safe": (0.12, 0.03),
    "safe"      : (0.18, 0.05),
    "careful"   : (0.22, 0.06),
    "normal"    : (0.30, 0.09),
    "aggressive": (0.45, 0.12),
    "turbo"     : (0.55, 0.16),
    "milestone" : (0.30, 0.09),
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
        logger.debug(f"Volume spike market fetch: {e}")
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


def _get_volume(market: dict) -> float:
    return float(market.get("volumeNum") or market.get("volume") or 0)


def _get_yes_mid(market: dict) -> Optional[float]:
    op = _parse_op(market)
    if op:
        return op[0]
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
    if best_bid:
        try:
            spread = float(market.get("bestAsk", mid + 0.02)) - float(best_bid)
            return mid + max(spread / 2, 0.01)
        except Exception:
            pass
    return min(mid + 0.015, 0.999)


def _update_snapshots(markets: list[dict]):
    """Update in-memory and DB volume snapshots."""
    global _last_snapshot
    now = time.time()
    if now - _last_snapshot < SNAPSHOT_EVERY:
        return
    _last_snapshot = now
    for m in markets:
        mid_id  = str(m.get("conditionId") or m.get("id") or "")
        if not mid_id:
            continue
        vol      = _get_volume(m)
        yes_price = _get_yes_mid(m)
        if yes_price is None:
            continue
        _vol_snapshots[mid_id] = {"vol": vol, "yes_price": yes_price, "ts": now}
        # Persist to DB for cross-bot sharing
        try:
            db.upsert_volume_cache(mid_id, vol, 0, 1.0,
                                   (m.get("question") or "")[:80], yes_price)
        except Exception:
            pass


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


def _score_spike(market: dict) -> Optional[dict]:
    """
    Score a market for volume spike signal.
    Returns opportunity dict if spike + directional confirmation found.
    """
    try:
        if market.get("closed") or not market.get("active", True):
            return None

        mid_id   = str(market.get("conditionId") or market.get("id") or "")
        cur_vol  = _get_volume(market)
        yes_mid  = _get_yes_mid(market)

        if cur_vol < MIN_VOLUME or yes_mid is None:
            return None
        if not (0.05 < yes_mid < 0.95):
            return None

        snap = _vol_snapshots.get(mid_id)
        if snap is None or snap["vol"] <= 0:
            return None  # no baseline yet

        prev_vol       = snap["vol"]
        prev_yes_price = snap["yes_price"]
        volume_ratio   = cur_vol / prev_vol if prev_vol > 0 else 1.0
        price_change   = yes_mid - prev_yes_price

        if volume_ratio < SPIKE_THRESHOLD:
            return None

        # Direction from price movement (must be meaningful)
        if abs(price_change) < MIN_PRICE_MOVE:
            return None  # volume without direction = noise

        if price_change > 0:
            direction = "yes"
            mid       = yes_mid
        else:
            direction = "no"
            mid       = 1.0 - yes_mid

        ask = _executable_ask(market, mid)
        if not (0.05 < ask < 0.93):
            return None

        fee = _fee_per_share(ask)

        # Momentum model probability:
        # If price already moved 2%+ with 4x+ volume → expect continued movement
        momentum_bonus = min(0.12, abs(price_change) * 2.0 + (volume_ratio - SPIKE_THRESHOLD) * 0.01)
        model_prob     = min(mid + momentum_bonus, 0.97)

        b          = (1.0 / ask) - 1.0
        p          = model_prob
        q          = 1.0 - p
        kelly_full = (b * p - q) / b
        if kelly_full <= 0:
            return None

        ev = b * p - q
        if ev < MIN_EV:
            return None

        true_prob_yes = model_prob if direction == "yes" else (1.0 - model_prob)

        return {
            "token_id"    : mid_id + f"_vol_{direction}",
            "direction"   : direction,
            "price"       : round(ask, 4),
            "midprice"    : round(mid, 4),
            "yes_price"   : round(yes_mid, 4),
            "model_prob"  : round(model_prob, 4),
            "edge"        : round(ev, 4),
            "kelly_full"  : round(kelly_full, 4),
            "volume_ratio": round(volume_ratio, 2),
            "price_change": round(price_change, 4),
            "fee_share"   : round(fee, 4),
            "_true_prob"  : round(true_prob_yes, 4),
            "question"    : (market.get("question") or "")[:80],
        }
    except Exception as e:
        logger.debug(f"_score_spike: {e}")
        return None


class VolumeSpikeBot:
    """
    Paper volume spike bot.
    Detects volume surges on Polymarket markets that indicate news-driven repricing,
    then follows the momentum direction before full price convergence.
    """

    def __init__(self, rm, live_client=None):
        self.rm             = rm
        self.live_client    = live_client
        self.open_positions = {}
        self.client         = live_client or True
        self.strategy_name  = "volume_spike"
        self.execution_mode = "live" if live_client else "paper"
        self.data_mode      = "real_market_data"
        self.last_scan_summary = {
            "opportunity_count": 0,
            "best_edge": None,
            "best_question": "",
            "best_volume_ratio": None,
            "last_scan_ts": 0.0,
            "open_positions": 0,
        }
        self.last_opportunities = []

    def scan_spikes(self) -> list[dict]:
        markets = _fetch_markets()
        _update_snapshots(markets)

        candidates = []
        for m in markets:
            scored = _score_spike(m)
            if scored:
                candidates.append(scored)
        candidates.sort(key=lambda x: (x["volume_ratio"] * x["edge"]), reverse=True)
        self.last_scan_summary = {
            "opportunity_count": len(candidates),
            "best_edge": round(candidates[0]["edge"], 4) if candidates else None,
            "best_question": candidates[0]["question"] if candidates else "",
            "best_volume_ratio": round(candidates[0]["volume_ratio"], 2) if candidates else None,
            "last_scan_ts": time.time(),
            "open_positions": len(self.open_positions),
        }
        self.last_opportunities = [
            {
                "question": opp["question"],
                "direction": opp["direction"],
                "edge": opp["edge"],
                "price": opp["price"],
                "volume_ratio": opp["volume_ratio"],
            }
            for opp in candidates[:3]
        ]
        if candidates:
            top = candidates[0]
            logger.debug(
                f"[{self.rm.bot_id}] VOL SPIKE: {len(candidates)} spikes | "
                f"best ratio={top['volume_ratio']:.1f}x ev={top['edge']:.3f}"
            )
        return candidates[:4]

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
                f"[{self.rm.bot_id}] VOL SETTLE | "
                f"{direction.upper()} {'WIN✓' if our_wins else 'LOSS✗'} | "
                f"ratio={pos.get('volume_ratio',0):.1f}x net={net:+.2f}"
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

        if self.live_client:
            ok = _live_place_order(self.live_client, opp["token_id"],
                                   opp["price"], bet_size, self.rm.bot_id)
            if not ok:
                return 0.0

        tid = opp["token_id"]
        self.open_positions[tid] = {
            "direction"   : opp["direction"],
            "bet_size"    : bet_size,
            "price"       : opp["price"],
            "_true_prob"  : opp["_true_prob"],
            "volume_ratio": opp["volume_ratio"],
            "edge"        : opp["edge"],
            "opened_at"   : time.time(),
            "live"        : self.live_client is not None,
        }
        mode = "LIVE" if self.live_client else "PAPER"
        logger.info(
            f"[{self.rm.bot_id}] [{mode}] VOL BET | "
            f"{opp['direction'].upper()} ${bet_size:.2f}@{opp['price']:.3f} | "
            f"spike={opp['volume_ratio']:.1f}x Δprice={opp['price_change']:+.3f} | "
            f"ev={opp['edge']:.3f} | \"{opp['question'][:45]}\""
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

        opps = self.scan_spikes()
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
