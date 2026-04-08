"""
Polymarket CLOB bot – 3 parallel instances, each targeting 10x.

Strategy:
  1. Scan all active binary markets every POLY_SCAN_INTERVAL seconds
  2. Score each market with an edge model:
       - Only bet YES on markets priced 0.65–0.92 (high confidence)
       - Only bet NO  on markets priced 0.08–0.35 (high confidence against)
       - Skip mid-range (0.35–0.65) – no clear edge without real info
       - Prefer markets resolving in ≤ 12 hours (fast profit lock)
       - Require bid-ask spread ≤ 4 %
  3. Size using fractional Kelly (50 %) capped at 20 % of bankroll
  4. Reinvest profits immediately (compound toward 10x)

Phase adjustments:
  phase1  → 50 % Kelly, max 15 % per position
  phase2  → 50 % Kelly, max 20 % per position
  phase3  → 60 % Kelly, max 25 % per position (push for 10x)
  recovery→ 25 % Kelly, max 8 %  per position (protect)

NOTE: Pass in an EXTERNAL RiskManager so the orchestrator can track the RM.
"""

import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
    POLY_AVAILABLE = True
except ImportError:
    logger.warning("py-clob-client not installed. Run: pip install py-clob-client")
    POLY_AVAILABLE = False

from config import (
    POLY_PRIVATE_KEY, POLY_API_KEY, POLY_API_SECRET, POLY_API_PASSPHRASE,
    POLY_HOST, POLY_CHAIN_ID,
    POLY_MAX_SPREAD, POLY_MIN_VOLUME, POLY_MIN_EDGE,
    POLY_KELLY_FRACTION, POLY_MAX_POSITION_PCT,
    BET_DELAY_SECONDS,
)
from risk_manager import RiskManager


# Phase-specific Kelly / position-size overrides
_PHASE_SETTINGS = {
    "recovery": {"kelly": 0.25, "max_pos": 0.08},
    "phase1"  : {"kelly": 0.50, "max_pos": 0.15},
    "phase2"  : {"kelly": 0.50, "max_pos": 0.20},
    "phase3"  : {"kelly": 0.60, "max_pos": 0.25},
}


class PolymarketBot:
    def __init__(self, rm: RiskManager):
        self.rm             = rm
        self.client         = None
        self.open_positions: dict[str, dict] = {}  # token_id → position info
        self._init_client()

    def _init_client(self):
        if not POLY_AVAILABLE:
            return
        if not POLY_PRIVATE_KEY:
            logger.warning(
                f"[{self.rm.bot_id}] POLY_PRIVATE_KEY not set – "
                "Polymarket bot disabled."
            )
            return
        try:
            creds = ApiCreds(
                api_key        = POLY_API_KEY,
                api_secret     = POLY_API_SECRET,
                api_passphrase = POLY_API_PASSPHRASE,
            )
            self.client = ClobClient(
                host     = POLY_HOST,
                chain_id = POLY_CHAIN_ID,
                key      = POLY_PRIVATE_KEY,
                creds    = creds,
            )
            logger.info(f"[{self.rm.bot_id}] Polymarket CLOB client initialised.")
        except Exception as exc:
            logger.error(f"[{self.rm.bot_id}] Polymarket init failed: {exc}")
            self.client = None

    # ── Market scanning ───────────────────────────────────────────────────────

    def scan_markets(self) -> list[dict]:
        if self.client is None:
            return []
        try:
            markets    = self.client.get_markets()
            candidates = []
            for m in markets:
                scored = self._score_market(m)
                if scored:
                    candidates.append(scored)
            candidates.sort(key=lambda x: x["edge"], reverse=True)
            return candidates[:5]   # top 5 per scan
        except Exception as exc:
            logger.error(f"[{self.rm.bot_id}] scan_markets: {exc}")
            return []

    def _score_market(self, market: dict) -> Optional[dict]:
        try:
            if not market.get("active") or market.get("closed"):
                return None

            volume = float(market.get("volume") or 0)
            if volume < POLY_MIN_VOLUME:
                return None

            tokens = market.get("tokens", [])
            if len(tokens) < 2:
                return None

            yes_token = next(
                (t for t in tokens if str(t.get("outcome", "")).lower() == "yes"),
                tokens[0],
            )
            no_token = next(
                (t for t in tokens if str(t.get("outcome", "")).lower() == "no"),
                tokens[1] if len(tokens) > 1 else None,
            )

            yes_price = float(yes_token.get("price") or 0)
            if yes_price <= 0 or yes_price >= 1:
                return None

            # ── Spread filter ─────────────────────────────────────
            ob = self._safe_orderbook(yes_token["token_id"])
            if ob is None:
                return None
            spread = self._spread(ob)
            if spread > POLY_MAX_SPREAD:
                return None

            # ── Resolution time (prefer fast) ─────────────────────
            hours_left = self._hours_until(
                market.get("endDateIso") or market.get("endDate")
            )
            if hours_left is None or hours_left < 0.5:
                return None
            time_bonus = max(0.0, 1.0 - hours_left / 24.0)  # 1.0 if now, 0 if 24h

            # ── Edge model ────────────────────────────────────────
            # We assume a slight lean toward the extreme already priced in.
            # Real edge comes from domain knowledge – bot adds a 5–10 % boost.
            if yes_price >= 0.65:
                # Bet YES: we believe true prob is higher than market price
                direction  = "yes"
                token_id   = yes_token["token_id"]
                model_prob = min(yes_price + 0.07 + 0.04 * time_bonus, 0.97)
                price      = yes_price
            elif yes_price <= 0.35:
                # Bet NO: we believe yes will NOT happen
                direction  = "no"
                token_id   = no_token["token_id"] if no_token else yes_token["token_id"]
                no_price   = 1.0 - yes_price
                model_prob = min(no_price + 0.07 + 0.04 * time_bonus, 0.97)
                price      = no_price
            else:
                return None   # uncertain zone – skip

            # ── Kelly sizing ──────────────────────────────────────
            b   = (1.0 / price) - 1.0    # net odds
            p   = model_prob
            q   = 1.0 - p
            kelly_full = (b * p - q) / b
            if kelly_full <= 0:
                return None

            edge = b * p - q
            if edge < POLY_MIN_EDGE:
                return None

            settings  = _PHASE_SETTINGS.get(self.rm.phase, _PHASE_SETTINGS["phase1"])
            kelly_use = kelly_full * settings["kelly"]
            bet_pct   = min(kelly_use, settings["max_pos"])
            bet_size  = max(round(self.rm.current_bankroll * bet_pct, 2), 1.0)

            return {
                "token_id"   : token_id,
                "direction"  : direction,
                "yes_price"  : yes_price,
                "price"      : price,
                "model_prob" : round(model_prob, 4),
                "edge"       : round(edge, 4),
                "kelly_full" : round(kelly_full, 4),
                "bet_size"   : bet_size,
                "hours_left" : round(hours_left, 2),
                "spread"     : round(spread, 4),
                "question"   : market.get("question", ""),
            }

        except Exception as exc:
            logger.debug(f"[{self.rm.bot_id}] _score_market: {exc}")
            return None

    def _safe_orderbook(self, token_id: str) -> Optional[dict]:
        try:
            return self.client.get_order_book(token_id)
        except Exception:
            return None

    def _spread(self, ob: dict) -> float:
        try:
            bids = ob.get("bids", [])
            asks = ob.get("asks", [])
            if not bids or not asks:
                return 1.0
            best_bid = float(max(bids, key=lambda x: float(x["price"]))["price"])
            best_ask = float(min(asks, key=lambda x: float(x["price"]))["price"])
            return best_ask - best_bid
        except Exception:
            return 1.0

    def _hours_until(self, end_str: Optional[str]) -> Optional[float]:
        if not end_str:
            return None
        try:
            from datetime import datetime, timezone
            s = end_str[:19].rstrip("Z")
            fmt = "%Y-%m-%dT%H:%M:%S" if "T" in s else "%Y-%m-%d"
            end = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return (end - now).total_seconds() / 3600
        except Exception:
            return None

    # ── Order placement ───────────────────────────────────────────────────────

    def place_bet(self, opp: dict) -> float:
        """Place market order. Returns net cost (negative = money spent)."""
        if self.rm.is_halted or self.client is None:
            return 0.0
        try:
            side = "BUY"  # On CLOB, always BUY the token (YES token or NO token)
            # On Polymarket CLOB, BUY always means you're buying shares.
            # YES position: buy YES token.  NO position: buy NO token.
            order = self.client.create_market_order(
                OrderArgs(
                    token_id  = opp["token_id"],
                    price     = opp["price"],
                    size      = opp["bet_size"],
                    side      = side,
                    order_type= OrderType.MARKET,
                )
            )
            self.client.post_order(order)

            self.open_positions[opp["token_id"]] = {
                "direction" : opp["direction"],
                "bet_size"  : opp["bet_size"],
                "price"     : opp["price"],
                "edge"      : opp["edge"],
            }

            logger.info(
                f"[{self.rm.bot_id}] POLY BET | "
                f"{opp['direction'].upper()} ${opp['bet_size']:.2f} | "
                f"price={opp['price']:.3f} edge={opp['edge']:.3f} | "
                f"resolves in {opp['hours_left']:.1f}h | "
                f"\"{opp['question'][:60]}\""
            )
            return -opp["bet_size"]

        except Exception as exc:
            logger.error(f"[{self.rm.bot_id}] place_bet: {exc}")
            return 0.0

    # ── Main cycle ────────────────────────────────────────────────────────────

    def run_one_cycle(self) -> float:
        """
        Scan → place best bet if edge found → return net spend.
        Polymarket settlement happens on-chain; payout credited to wallet balance.
        """
        if self.rm.is_halted or self.client is None:
            return 0.0

        opps = self.scan_markets()
        if not opps:
            logger.debug(f"[{self.rm.bot_id}] POLY | no opportunities this scan")
            return 0.0

        for opp in opps:
            if opp["token_id"] not in self.open_positions:
                net = self.place_bet(opp)
                time.sleep(BET_DELAY_SECONDS)
                return net   # one bet per cycle

        return 0.0

    def get_balance(self) -> Optional[float]:
        if self.client is None:
            return None
        try:
            return float(self.client.get_balance())
        except Exception as exc:
            logger.error(f"[{self.rm.bot_id}] get_balance: {exc}")
            return None
