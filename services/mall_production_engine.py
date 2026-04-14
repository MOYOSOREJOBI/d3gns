"""
Mall Production Engine — high-volume business automation for mall bots.

Architecture:
  - Runs all 10 MALL bots continuously in parallel
  - Target: thousands of micro-operations per day (content, listings, leads)
  - Each bot self-funds from its $5 allocation and grows via compounding revenue
  - Revenue tracked per bot → fed back into PortfolioAllocator
  - Hot bots get more capital; cold bots get minimum allocation
  - Daily output report: units produced, revenue, cost, net margin

Mall bot roles:
  shopify_ops      — Product listing optimisation + store management
  etsy_pod         — Print-on-demand listing creation and SEO
  ebay_flip        — Arbitrage opportunity scouting + listing
  affiliate_content — Affiliate link content generation
  digital_downloads — eBook/template/asset creation and listing
  newsletter        — Email list content + automation
  youtube_content   — Script generation + upload metadata
  podcast_content   — Episode scripts + distribution metadata
  freelance_lead_scout — Lead scraping + outreach generation
  job_board_scanner — Job posting analysis + matching

Revenue model per bot:
  Content bots (youtube, podcast, newsletter, affiliate):
    Revenue = impressions × CTR × conversion × avg_order
  Commerce bots (shopify, etsy, ebay, digital_downloads):
    Revenue = listings × views × conversion × avg_price
  Lead bots (freelance, job_board):
    Revenue = leads_found × qualification_rate × avg_deal_value
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Mall bot registry ─────────────────────────────────────────────────────────

# (bot_module, bot_class, category, cycle_seconds, daily_op_target)
MALL_BOT_REGISTRY: list[tuple[str, str, str, float, int]] = [
    # Commerce bots — run frequently for listing volume
    ("bots.shopify_ops_bot",         "ShopifyOpsBot",         "commerce",  30.0,   500),
    ("bots.etsy_pod_bot",            "EtsyPodBot",            "commerce",  30.0,   300),
    ("bots.ebay_flip_bot",           "EbayFlipBot",           "commerce",  20.0,   200),
    ("bots.digital_downloads_bot",   "DigitalDownloadsBot",   "commerce",  60.0,   100),
    # Content bots — medium frequency, high output value
    ("bots.affiliate_content_bot",   "AffiliateContentBot",   "content",   120.0,  100),
    ("bots.newsletter_bot",          "NewsletterBot",         "content",   300.0,   20),
    ("bots.youtube_content_bot",     "YoutubeContentBot",     "content",   600.0,   10),
    ("bots.podcast_content_bot",     "PodcastContentBot",     "content",   600.0,    5),
    # Lead generation bots
    ("bots.freelance_lead_scout_bot","FreelanceLeadScoutBot", "leads",      60.0,  200),
    ("bots.job_board_scanner_bot",   "JobBoardScannerBot",    "leads",      60.0,  150),
]

# Revenue estimation per operation by category
REVENUE_PER_OP = {
    "commerce": 0.15,   # avg $0.15 per listing/view
    "content":  0.30,   # avg $0.30 per piece published
    "leads":    0.50,   # avg $0.50 per qualified lead
}

COST_PER_OP = {
    "commerce": 0.02,
    "content":  0.05,
    "leads":    0.03,
}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class MallBotRun:
    bot_class:    str
    category:     str
    ops_produced: int     = 0
    revenue_est:  float   = 0.0
    cost_est:     float   = 0.0
    latency_ms:   float   = 0.0
    signal_taken: bool    = False
    error:        str     = ""

    @property
    def net_margin(self) -> float:
        if self.revenue_est == 0:
            return 0.0
        return (self.revenue_est - self.cost_est) / self.revenue_est * 100


@dataclass
class MallCycleResult:
    cycle_id:         int
    ts:               float = field(default_factory=time.time)
    runs:             list[MallBotRun] = field(default_factory=list)
    total_ops:        int   = 0
    total_revenue:    float = 0.0
    total_cost:       float = 0.0
    cycle_ms:         float = 0.0
    errors:           int   = 0

    @property
    def net_revenue(self) -> float:
        return self.total_revenue - self.total_cost

    @property
    def margin_pct(self) -> float:
        if self.total_revenue == 0:
            return 0.0
        return self.net_revenue / self.total_revenue * 100


# ── Mall Production Engine ────────────────────────────────────────────────────

class MallProductionEngine:
    """
    Coordinates all mall bots for high-volume, low-cost content/commerce production.
    Target: 1,000+ operations/day generating self-funded revenue.
    """

    DAILY_OP_TARGET    = 1585  # sum of daily_op_target across all bots
    BOT_TIMEOUT_S      = 20.0
    MAX_WORKERS        = 6
    REPORT_EVERY_N     = 48    # report every N cycles (~every 24h at 30min cycles)

    def __init__(
        self,
        mall_capital:  float = 50.0,
        paper_mode:    bool  = True,
    ) -> None:
        self.mall_capital = mall_capital
        self.paper_mode   = paper_mode
        self._running     = False
        self._cycle_n     = 0
        self._pool        = ThreadPoolExecutor(max_workers=self.MAX_WORKERS, thread_name_prefix="mall")
        self._lock        = threading.Lock()
        self._history:    list[MallCycleResult] = []
        self._bot_cache:  dict[str, Any]        = {}
        self._daily_ops:  dict[str, int]        = {}   # bot_class → ops today
        self._daily_rev:  dict[str, float]      = {}   # bot_class → revenue today
        self._session_start = time.time()
        self._last_cycle_ts: dict[str, float]   = {}   # bot_class → last run

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        t = threading.Thread(target=self._main_loop, daemon=True, name="mall-engine")
        t.start()
        logger.info("MallProductionEngine started — capital=$%.0f paper=%s", self.mall_capital, self.paper_mode)

    def stop(self) -> None:
        self._running = False
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _main_loop(self) -> None:
        while self._running:
            t0 = time.monotonic()
            try:
                self._cycle_n += 1
                now = time.time()

                # Select bots due for a run
                bots_due = [
                    (mod, cls, cat, cyc, daily_t)
                    for mod, cls, cat, cyc, daily_t in MALL_BOT_REGISTRY
                    if (now - self._last_cycle_ts.get(cls, 0)) >= cyc
                ]

                if bots_due:
                    result = self._run_cycle(bots_due)
                    with self._lock:
                        self._history.append(result)
                        if len(self._history) > 2000:
                            self._history.pop(0)

                    # Feed revenue back into allocator
                    self._feed_revenue_to_allocator(result)

                    # Periodic report
                    if self._cycle_n % self.REPORT_EVERY_N == 0:
                        self._send_daily_report()

            except Exception as exc:
                logger.error("MallProductionEngine loop error: %s", exc)

            elapsed = time.monotonic() - t0
            time.sleep(max(5.0, 20.0 - elapsed))

    def _run_cycle(self, bots: list[tuple]) -> MallCycleResult:
        t0 = time.monotonic()
        cycle = MallCycleResult(cycle_id=self._cycle_n)
        now   = time.time()

        futures = {}
        for mod, cls, cat, _, daily_t in bots:
            f = self._pool.submit(self._run_bot_safe, mod, cls, cat, daily_t)
            futures[f] = (mod, cls, cat)

        for fut in as_completed(futures, timeout=self.BOT_TIMEOUT_S + 2):
            mod, cls, cat = futures[fut]
            try:
                run = fut.result(timeout=0.5)
                cycle.runs.append(run)
                cycle.total_ops     += run.ops_produced
                cycle.total_revenue += run.revenue_est
                cycle.total_cost    += run.cost_est
                if run.error:
                    cycle.errors += 1
                # Update last-run timestamp
                self._last_cycle_ts[cls] = now
                # Accumulate daily stats
                with self._lock:
                    self._daily_ops[cls] = self._daily_ops.get(cls, 0) + run.ops_produced
                    self._daily_rev[cls] = self._daily_rev.get(cls, 0.0) + run.revenue_est
            except Exception as exc:
                cycle.errors += 1
                logger.debug("Mall bot %s error: %s", cls, exc)

        cycle.cycle_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.debug(
            "MallCycle #%d: %d ops $%.2f rev %.0fms",
            cycle.cycle_id, cycle.total_ops, cycle.total_revenue, cycle.cycle_ms
        )
        return cycle

    def _run_bot_safe(self, mod_path: str, cls_name: str, category: str, daily_target: int) -> MallBotRun:
        t0 = time.monotonic()
        run = MallBotRun(bot_class=cls_name, category=category)
        try:
            import importlib
            key = f"{mod_path}.{cls_name}"
            if key not in self._bot_cache:
                mod = importlib.import_module(mod_path)
                self._bot_cache[key] = getattr(mod, cls_name)()
            bot = self._bot_cache[key]
            raw = bot.run_one_cycle()

            # Extract operation count from signal
            data        = raw.get("data", {}) or {}
            ops         = int(data.get("ops_count", data.get("items_found",
                          data.get("leads_found", data.get("listings_created", 1)))))
            run.ops_produced  = max(ops, 1) if raw.get("signal_taken") else 0
            run.signal_taken  = bool(raw.get("signal_taken"))

            # Revenue estimation
            if run.ops_produced > 0:
                rev_per_op  = float(data.get("revenue_per_op", REVENUE_PER_OP.get(category, 0.10)))
                cost_per_op = COST_PER_OP.get(category, 0.02)
                run.revenue_est = run.ops_produced * rev_per_op
                run.cost_est    = run.ops_produced * cost_per_op

        except Exception as exc:
            run.error = str(exc)
        finally:
            run.latency_ms = round((time.monotonic() - t0) * 1000, 1)
        return run

    # ── Revenue → Allocator ───────────────────────────────────────────────────

    def _feed_revenue_to_allocator(self, cycle: MallCycleResult) -> None:
        try:
            from services.portfolio_allocator import get_allocator
            allocator = get_allocator()
            for run in cycle.runs:
                if run.revenue_est > 0:
                    bot_id = "bot_" + run.bot_class.lower().replace("bot", "").strip("_")
                    # Revenue is net margin as P&L signal
                    pnl = run.revenue_est - run.cost_est
                    allocator.record_result(bot_id, pnl)
        except Exception:
            pass

    # ── Daily report ──────────────────────────────────────────────────────────

    def _send_daily_report(self) -> None:
        stats = self.get_daily_stats()
        try:
            from notifier_telegram import send_message
            lines = [
                "MALL BOTS — Daily Report",
                f"Total ops: {stats['total_ops_today']:,}",
                f"Revenue: ${stats['total_revenue_today']:.2f}",
                f"Net margin: ${stats['net_revenue_today']:.2f} ({stats['margin_pct']:.0f}%)",
                "",
                "Top bots:",
            ]
            for b in stats.get("top_bots", [])[:5]:
                lines.append(f"  {b['bot']}: {b['ops']} ops ${b['rev']:.2f}")
            send_message("\n".join(lines))
        except Exception:
            pass

    # ── Status / stats ────────────────────────────────────────────────────────

    def get_daily_stats(self) -> dict[str, Any]:
        with self._lock:
            total_ops = sum(self._daily_ops.values())
            total_rev = sum(self._daily_rev.values())
            # Estimate total cost from ratio
            total_cost = sum(
                self._daily_ops.get(cls, 0) * COST_PER_OP.get(cat, 0.02)
                for _, cls, cat, _, _ in MALL_BOT_REGISTRY
            )
            top_bots = sorted(
                [
                    {"bot": cls, "ops": self._daily_ops.get(cls, 0),
                     "rev": self._daily_rev.get(cls, 0.0)}
                    for _, cls, _, _, _ in MALL_BOT_REGISTRY
                ],
                key=lambda x: x["rev"], reverse=True,
            )

        net = total_rev - total_cost
        return {
            "total_ops_today":     total_ops,
            "total_revenue_today": round(total_rev, 2),
            "total_cost_today":    round(total_cost, 2),
            "net_revenue_today":   round(net, 2),
            "margin_pct":          round(net / max(total_rev, 0.01) * 100, 1),
            "daily_op_target":     self.DAILY_OP_TARGET,
            "completion_pct":      round(total_ops / self.DAILY_OP_TARGET * 100, 1),
            "top_bots":            top_bots,
        }

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            recent = self._history[-50:] if self._history else []

        total_ops  = sum(c.total_ops    for c in recent)
        total_rev  = sum(c.total_revenue for c in recent)
        avg_ms     = sum(c.cycle_ms     for c in recent) / max(len(recent), 1)
        uptime_h   = round((time.time() - self._session_start) / 3600, 2)

        return {
            "running":           self._running,
            "paper_mode":        self.paper_mode,
            "mall_capital":      round(self.mall_capital, 2),
            "cycle_count":       self._cycle_n,
            "registered_bots":   len(MALL_BOT_REGISTRY),
            "cached_bots":       len(self._bot_cache),
            "uptime_h":          uptime_h,
            "recent_cycles":     len(recent),
            "recent_ops":        total_ops,
            "recent_revenue":    round(total_rev, 2),
            "avg_cycle_ms":      round(avg_ms, 1),
            "daily_stats":       self.get_daily_stats(),
        }

    def get_bot_leaderboard(self) -> list[dict[str, Any]]:
        """Returns all mall bots ranked by daily revenue."""
        with self._lock:
            return sorted(
                [
                    {
                        "bot":      cls,
                        "category": cat,
                        "ops":      self._daily_ops.get(cls, 0),
                        "revenue":  round(self._daily_rev.get(cls, 0.0), 2),
                        "last_run_ago_s": round(time.time() - self._last_cycle_ts.get(cls, 0), 0),
                    }
                    for _, cls, cat, _, _ in MALL_BOT_REGISTRY
                ],
                key=lambda x: x["revenue"], reverse=True,
            )

    def reset_daily(self) -> None:
        """Reset daily counters at start of each day."""
        with self._lock:
            self._daily_ops.clear()
            self._daily_rev.clear()


# ── Global singleton ──────────────────────────────────────────────────────────
_mall_engine: MallProductionEngine | None = None


def get_mall_engine(mall_capital: float = 50.0, paper_mode: bool = True) -> MallProductionEngine:
    global _mall_engine
    if _mall_engine is None:
        _mall_engine = MallProductionEngine(mall_capital=mall_capital, paper_mode=paper_mode)
    return _mall_engine
