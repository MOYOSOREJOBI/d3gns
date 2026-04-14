"""
Portfolio Allocator — dynamic capital allocation across all 74 bots.

Strategy:
  - Each bot starts with a base allocation (equal-weight or Kelly-weighted)
  - Performance tracking: Sharpe, win rate, recent P&L
  - Top performers get MORE capital (momentum allocation)
  - Underperformers get LESS (but never zero — keep them running for diversification)
  - Rebalances every N decisions or on significant portfolio events
  - Handles both LAB bots (trading/betting) and MALL bots (business)

$500 Allocation model:
  $50   → Tools reserve (API keys, hosting)
  $50   → Mall bots (business capital, must self-fund)
  $400  → Lab bots (trading/betting/prediction)

  Lab bot distribution ($400 across ~25 active lab bots):
    Top 5 performers:    $20 each = $100 (25%)
    Middle 10:           $15 each = $150 (37.5%)
    Bottom 10:           $5 each  = $50  (12.5%)
    Reserve buffer:      $100     (25%) — deploy on new high-confidence signals
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BotAllocation:
    bot_id:           str
    bot_type:         str       # "lab" | "mall" | "wealth"
    allocated_usd:    float
    min_allocation:   float = 1.0
    max_allocation:   float = 100.0

    # Performance tracking
    total_pnl:        float = 0.0
    total_bets:       int   = 0
    wins:             int   = 0
    losses:           int   = 0
    last_pnl:         float = 0.0
    streak:           int   = 0   # positive = win streak, negative = loss streak
    last_active:      float = field(default_factory=time.time)
    sharpe:           float = 0.0
    recent_returns:   list  = field(default_factory=list)  # last 20

    @property
    def win_rate(self) -> float:
        if self.total_bets == 0:
            return 0.5
        return self.wins / self.total_bets

    @property
    def expectancy(self) -> float:
        if not self.recent_returns:
            return 0.0
        return sum(self.recent_returns[-20:]) / len(self.recent_returns[-20:])

    @property
    def is_hot(self) -> bool:
        return self.streak >= 3 and self.win_rate > 0.60

    @property
    def is_cold(self) -> bool:
        return self.streak <= -3 or self.win_rate < 0.35

    def record_result(self, pnl: float) -> None:
        self.total_pnl += pnl
        self.total_bets += 1
        self.last_pnl = pnl
        self.last_active = time.time()
        self.recent_returns.append(pnl)
        if len(self.recent_returns) > 50:
            self.recent_returns.pop(0)
        if pnl > 0:
            self.wins += 1
            self.streak = max(1, self.streak + 1)
        else:
            self.losses += 1
            self.streak = min(-1, self.streak - 1)
        # Update Sharpe
        if len(self.recent_returns) >= 5:
            import statistics
            mean = statistics.mean(self.recent_returns[-20:])
            try:
                std = statistics.stdev(self.recent_returns[-20:])
                self.sharpe = round(mean / std * math.sqrt(252), 3) if std > 0 else 0.0
            except Exception:
                self.sharpe = 0.0


class PortfolioAllocator:
    """
    Dynamic capital allocator for the full bot fleet.

    Allocation algorithm:
      1. Score each bot: score = win_rate * 0.3 + sharpe_normalised * 0.3
                                + expectancy_normalised * 0.2 + streak_bonus * 0.2
      2. Convert scores to weights (softmax with temperature)
      3. Apply min/max constraints per bot type
      4. Rebalance when drift > REBALANCE_THRESHOLD or on circuit break event
    """

    # Rebalance when any bot drifts more than this from target
    REBALANCE_THRESHOLD = 0.15  # 15%

    # Softmax temperature (lower = more concentrated, higher = more equal)
    TEMPERATURE = 2.0

    def __init__(
        self,
        total_capital: float = 400.0,
        lab_pct:       float = 0.80,  # 80% to lab bots
        mall_pct:      float = 0.12,  # 12% to mall bots
        reserve_pct:   float = 0.08,  # 8% reserve
    ) -> None:
        self.total_capital  = total_capital
        self.lab_pct        = lab_pct
        self.mall_pct       = mall_pct
        self.reserve_pct    = reserve_pct
        self._bots:    dict[str, BotAllocation] = {}
        self._reserve: float = total_capital * reserve_pct
        self._last_rebalance: float = 0.0

        # Pre-defined LAB and MALL bot groups
        self.LAB_BOTS = [
            # Prediction market bots (highest priority — real edge)
            "bot_kalshi_pair_spread", "bot_kalshi_resolution_decay",
            "bot_kalshi_orderbook_imbalance", "bot_poly_adaptive_trend",
            "bot_polymarket_microstructure", "bot_poly_kalshi_crossvenue",
            "bot_crossvenue_arb_watchlist", "bot_prediction_consensus",
            # Crypto/finance bots
            "bot_crypto_momentum", "bot_defi_yield_arb", "bot_crypto_funding_rate",
            "bot_volatility_regime", "bot_tech_signal", "bot_sp500_momentum_tracker",
            "bot_gold_price_momentum", "bot_gold_funding_basis",
            # Macro/event bots
            "bot_macro_indicator", "bot_news_sentiment", "bot_social_sentiment",
            "bot_geopolitical_risk", "bot_oil_inventory_shock",
            "bot_earnings_surprise", "bot_insider_filing",
            "bot_congress_trades", "bot_kalshi_macro_shock_sniper",
            # Sports/alternative
            "bot_sports_momentum", "bot_f1_odds_latency",
            "bot_soccer_consensus_latency", "bot_oddsapi_clv_tracker",
        ]
        self.MALL_BOTS = [
            "bot_shopify_ops", "bot_etsy_pod", "bot_ebay_flip",
            "bot_affiliate_content", "bot_digital_downloads",
            "bot_newsletter", "bot_youtube_content", "bot_podcast_content",
            "bot_freelance_lead_scout", "bot_job_board_scanner",
        ]

    def initialise(self, total_capital: float | None = None) -> dict[str, BotAllocation]:
        """Set up initial allocations across all bots."""
        if total_capital:
            self.total_capital = total_capital
            self._reserve = total_capital * self.reserve_pct

        lab_capital  = self.total_capital * self.lab_pct
        mall_capital = self.total_capital * self.mall_pct

        # Equal-weight initial allocation
        per_lab  = lab_capital  / max(len(self.LAB_BOTS),  1)
        per_mall = mall_capital / max(len(self.MALL_BOTS), 1)

        for bot_id in self.LAB_BOTS:
            self._bots[bot_id] = BotAllocation(
                bot_id=bot_id, bot_type="lab",
                allocated_usd=round(per_lab, 4),
                min_allocation=2.0, max_allocation=50.0,
            )
        for bot_id in self.MALL_BOTS:
            self._bots[bot_id] = BotAllocation(
                bot_id=bot_id, bot_type="mall",
                allocated_usd=round(per_mall, 4),
                min_allocation=2.0, max_allocation=30.0,
            )
        return self._bots

    def record_result(self, bot_id: str, pnl: float) -> None:
        """Record a trade outcome for a bot."""
        if bot_id in self._bots:
            self._bots[bot_id].record_result(pnl)
            # Update allocation if bot is hot/cold
            if self._bots[bot_id].is_hot:
                self._scale_allocation(bot_id, 1.15)
            elif self._bots[bot_id].is_cold:
                self._scale_allocation(bot_id, 0.80)

    def rebalance(self) -> dict[str, Any]:
        """
        Rebalance capital allocation based on performance scores.
        Call periodically (every 100 trades or every 4 hours).
        """
        if not self._bots:
            self.initialise()

        lab_bots  = [b for b in self._bots.values() if b.bot_type == "lab"]
        mall_bots = [b for b in self._bots.values() if b.bot_type == "mall"]

        lab_capital  = self.total_capital * self.lab_pct
        mall_capital = self.total_capital * self.mall_pct

        new_allocs_lab  = self._score_and_allocate(lab_bots,  lab_capital)
        new_allocs_mall = self._score_and_allocate(mall_bots, mall_capital)

        changes: list[dict] = []
        for bot_id, new_alloc in {**new_allocs_lab, **new_allocs_mall}.items():
            old = self._bots[bot_id].allocated_usd
            if abs(new_alloc - old) > 0.50:
                changes.append({"bot_id": bot_id, "old": round(old, 2), "new": round(new_alloc, 2),
                                 "delta": round(new_alloc - old, 2)})
            self._bots[bot_id].allocated_usd = new_alloc

        self._last_rebalance = time.time()
        logger.info("Portfolio rebalanced: %d bot allocations changed", len(changes))

        return {
            "rebalanced":       True,
            "changes":          changes,
            "change_count":     len(changes),
            "total_allocated":  round(sum(b.allocated_usd for b in self._bots.values()), 2),
            "reserve":          round(self._reserve, 2),
            "timestamp":        self._last_rebalance,
        }

    def get_allocation(self, bot_id: str) -> float:
        """Get current dollar allocation for a bot."""
        return self._bots.get(bot_id, BotAllocation("unknown", "lab", 5.0)).allocated_usd

    def deploy_reserve(self, bot_id: str, amount: float) -> dict[str, Any]:
        """Deploy reserve capital to a specific bot (high-confidence opportunity)."""
        if amount > self._reserve:
            amount = self._reserve
        if amount < 0.50:
            return {"deployed": False, "reason": "insufficient_reserve"}
        self._reserve -= amount
        if bot_id in self._bots:
            self._bots[bot_id].allocated_usd += amount
        return {"deployed": True, "amount": round(amount, 4), "reserve_remaining": round(self._reserve, 4)}

    def return_to_reserve(self, bot_id: str, amount: float) -> None:
        """Return unused capital from a bot to the reserve pool."""
        self._reserve += amount
        if bot_id in self._bots:
            self._bots[bot_id].allocated_usd = max(
                self._bots[bot_id].min_allocation,
                self._bots[bot_id].allocated_usd - amount,
            )

    def get_top_performers(self, n: int = 5, bot_type: str = "lab") -> list[dict[str, Any]]:
        """Get top N performing bots."""
        bots = [b for b in self._bots.values() if b.bot_type == bot_type and b.total_bets >= 5]
        sorted_bots = sorted(bots, key=lambda b: b.sharpe, reverse=True)
        return [
            {
                "bot_id": b.bot_id, "allocated_usd": round(b.allocated_usd, 2),
                "win_rate": round(b.win_rate, 3), "total_pnl": round(b.total_pnl, 2),
                "sharpe": b.sharpe, "streak": b.streak, "total_bets": b.total_bets,
            }
            for b in sorted_bots[:n]
        ]

    def get_status(self) -> dict[str, Any]:
        lab_bots  = [b for b in self._bots.values() if b.bot_type == "lab"]
        mall_bots = [b for b in self._bots.values() if b.bot_type == "mall"]
        return {
            "total_capital":      self.total_capital,
            "total_allocated":    round(sum(b.allocated_usd for b in self._bots.values()), 2),
            "reserve":            round(self._reserve, 2),
            "lab_bots":           len(lab_bots),
            "mall_bots":          len(mall_bots),
            "lab_allocated":      round(sum(b.allocated_usd for b in lab_bots), 2),
            "mall_allocated":     round(sum(b.allocated_usd for b in mall_bots), 2),
            "hot_bots":           [b.bot_id for b in self._bots.values() if b.is_hot],
            "cold_bots":          [b.bot_id for b in self._bots.values() if b.is_cold],
            "top_performers":     self.get_top_performers(5),
            "last_rebalance":     self._last_rebalance,
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _score_bot(self, bot: BotAllocation) -> float:
        """Score a bot 0–1 for allocation purposes."""
        if bot.total_bets < 3:
            return 0.50  # neutral until data accumulates

        wr_score    = bot.win_rate
        sharpe_score = min(max((bot.sharpe + 2) / 6, 0), 1)  # normalise -2 to +4 → 0 to 1
        exp_score   = min(max((bot.expectancy + 10) / 20, 0), 1)  # normalise
        streak_bonus = 0.10 if bot.streak >= 3 else (-0.10 if bot.streak <= -3 else 0)

        return wr_score * 0.30 + sharpe_score * 0.30 + exp_score * 0.20 + (0.50 + streak_bonus) * 0.20

    def _score_and_allocate(self, bots: list[BotAllocation], total: float) -> dict[str, float]:
        if not bots:
            return {}
        scores = {b.bot_id: self._score_bot(b) for b in bots}
        # Softmax
        max_score = max(scores.values())
        exp_scores = {bid: math.exp((s - max_score) / self.TEMPERATURE) for bid, s in scores.items()}
        sum_exp = sum(exp_scores.values())
        weights = {bid: v / sum_exp for bid, v in exp_scores.items()}
        # Apply weights
        result = {}
        for b in bots:
            raw = total * weights[b.bot_id]
            result[b.bot_id] = max(b.min_allocation, min(b.max_allocation, raw))
        return result

    def _scale_allocation(self, bot_id: str, factor: float) -> None:
        if bot_id not in self._bots:
            return
        b = self._bots[bot_id]
        new_alloc = max(b.min_allocation, min(b.max_allocation, b.allocated_usd * factor))
        delta = new_alloc - b.allocated_usd
        if delta > self._reserve:
            delta = self._reserve
        b.allocated_usd += delta
        self._reserve   -= max(0, delta)


# ── Global singleton ──────────────────────────────────────────────────────────
_allocator: PortfolioAllocator | None = None


def get_allocator(total_capital: float = 400.0) -> PortfolioAllocator:
    global _allocator
    if _allocator is None:
        _allocator = PortfolioAllocator(total_capital=total_capital)
        _allocator.initialise()
    return _allocator
