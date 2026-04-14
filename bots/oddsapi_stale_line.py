from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any
import uuid

from bots.base_research_bot import BaseResearchBot
from models.proposal import Proposal
from services import quota_budgeter


def _to_implied(price: Any) -> float | None:
    try:
        d = float(price)
        return round(1.0 / d, 6) if d > 1.0 else None
    except Exception:
        return None


class OddsApiStaleLineBot(BaseResearchBot):
    """
    Bot 6 — OddsAPI Stale Line Scanner.

    Detects bookmakers whose lines have not moved with the consensus.
    A "stale" line is one where one book is significantly far from the
    median of all books on the same event/outcome.

    Mode:     PAPER — no order placement. Signal detection only.
    Platform: The Odds API (public, quota-aware)
    Truth:    PUBLIC DATA ONLY / RATE LIMITED POSSIBLE
    """

    bot_id = "bot_oddsapi_stale_line_scanner"
    display_name = "OddsAPI Stale Line Scanner"
    platform = "oddsapi"
    mode = "PAPER"
    signal_type = "stale_line"
    paper_only = True
    implemented = True

    # Minimum deviation from median consensus to flag as stale
    STALE_THRESHOLD = 0.035

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        if self.adapter is None:
            return self.disabled_result("OddsAPI adapter is not wired. Bot cannot run without a configured adapter.")

        # Quota gate
        reason = quota_budgeter.get_budget().degraded_reason("oddsapi")
        if reason:
            return self.disabled_result(f"QUOTA LIMITED — {reason}")

        health = self.adapter.healthcheck()
        if not health.get("ok"):
            return self.disabled_result(health.get("degraded_reason", "OddsAPI adapter is not healthy."))

        resp = self.adapter.list_markets(
            sport="upcoming", regions="us,uk", markets="h2h", oddsFormat="decimal"
        )
        if not resp.get("ok"):
            return self.disabled_result(resp.get("degraded_reason", "OddsAPI markets unavailable."))

        # Record quota from response
        quota = (resp.get("data") or {}).get("quota", {})
        remaining = quota.get("remaining")
        used = quota.get("used")
        quota_budgeter.record(
            "oddsapi",
            remote_remaining=int(remaining) if remaining is not None else None,
            remote_used=int(used) if used is not None else None,
        )

        events = (resp.get("data") or {}).get("events", [])
        best_result = None

        for event in events[:15]:
            # Collect implied probabilities per outcome per bookmaker
            by_outcome: dict[str, list[tuple[str, float]]] = defaultdict(list)

            for bm in event.get("bookmakers", []):
                bm_key = bm.get("key", "unknown")
                for mkt in bm.get("markets", []):
                    if mkt.get("key") != "h2h":
                        continue
                    for outcome in mkt.get("outcomes", []):
                        implied = _to_implied(outcome.get("price"))
                        if implied is None:
                            continue
                        by_outcome[outcome.get("name", "")].append((bm_key, implied))

            # Need ≥3 bookmakers to compute a meaningful consensus
            n_books = len({bk for entries in by_outcome.values() for bk, _ in entries})
            if n_books < 3:
                continue

            # Find the most-deviant bookmaker line vs median
            stale_candidate: dict[str, Any] | None = None
            max_dev = 0.0

            for outcome_name, entries in by_outcome.items():
                if len(entries) < 3:
                    continue
                values = [v for _, v in entries]
                med = median(values)
                for bm_key, implied in entries:
                    dev = abs(implied - med)
                    if dev > max_dev:
                        max_dev = dev
                        stale_candidate = {
                            "bookmaker": bm_key,
                            "outcome": outcome_name,
                            "bookmaker_implied": round(implied, 4),
                            "consensus_median": round(med, 4),
                            "deviation": round(implied - med, 4),
                            "abs_deviation": round(dev, 4),
                            "bookmaker_count": n_books,
                        }

            if stale_candidate is None:
                continue

            taken = max_dev >= self.STALE_THRESHOLD
            result = self.emit_signal(
                title=f"{event.get('away_team', '?')} at {event.get('home_team', '?')}",
                summary=(
                    f"{stale_candidate['bookmaker']} is {stale_candidate['deviation']:+.3f} from "
                    f"the median on {stale_candidate['outcome']}. "
                    f"Consensus from {n_books} books. "
                    + ("Possible stale line." if taken else "Below stale threshold.")
                ),
                confidence=min(0.99, max_dev * 3.0),
                signal_taken=taken,
                degraded_reason="" if taken else f"Max deviation {max_dev:.3f} < threshold {self.STALE_THRESHOLD}.",
                data={
                    "event_id": event.get("id"),
                    "sport_key": event.get("sport_key"),
                    "commence_time": event.get("commence_time"),
                    **stale_candidate,
                    "truth_note": "PAPER — PUBLIC DATA ONLY. No order placement.",
                    "quota": quota,
                },
            )
            if not best_result or result["confidence"] > best_result["confidence"]:
                best_result = result

        return best_result or self.disabled_result(
            "No stale bookmaker lines found in the current OddsAPI event sample. "
            "All books are within the consensus deviation threshold."
        )

    def generate_proposal(self, context: dict[str, Any] | None = None) -> Proposal | None:
        result = self.run_one_cycle()
        if not result.get("signal_taken"):
            return None
        data = result.get("data", {}) or {}
        deviation = abs(float(data.get("deviation", 0) or 0))
        edge_bps = deviation * 10000
        edge_post_fee = edge_bps - 50.0
        if edge_post_fee <= 0:
            return None
        ctx = context or {}
        return Proposal(
            proposal_id=f"p_{uuid.uuid4().hex[:12]}",
            bot_id=self.bot_id,
            platform="oddsapi",
            market_id=str(data.get("event_id", "")),
            side="BUY",
            confidence=round(float(result.get("confidence", 0) or 0), 4),
            edge_bps=round(edge_bps, 2),
            edge_post_fee_bps=round(edge_post_fee, 2),
            expected_hold_s=1800,
            max_slippage_bps=80,
            correlation_key=str(data.get("sport_key", "oddsapi")),
            reason_code="stale_line_detected",
            runtime_mode=str(ctx.get("runtime_mode", "paper")).lower(),
            truth_label="PAPER — NO REAL ORDER",
            metadata={
                "bookmaker": data.get("bookmaker", ""),
                "outcome": data.get("outcome", ""),
                "bookmaker_implied": data.get("bookmaker_implied"),
                "consensus_median": data.get("consensus_median"),
                "deviation": data.get("deviation"),
                "sport_key": data.get("sport_key", ""),
            },
        )
