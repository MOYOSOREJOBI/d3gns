from __future__ import annotations

from collections import defaultdict

from bots.base_research_bot import BaseResearchBot


def _to_implied_probability(price) -> float | None:
    try:
        decimal_price = float(price)
        if decimal_price <= 1.0:
            return None
        return 1.0 / decimal_price
    except Exception:
        return None


class OddsApiConsensusOutlierBot(BaseResearchBot):
    bot_id = "bot_oddsapi_consensus_outlier_paper"
    display_name = "OddsAPI Consensus Outlier"
    platform = "oddsapi"
    mode = "PAPER"
    signal_type = "consensus_outlier"
    paper_only = True
    implemented = True

    def __init__(self, adapter):
        self.adapter = adapter

    def run_one_cycle(self) -> dict:
        health = self.adapter.healthcheck()
        if not health.get("ok"):
            return self.disabled_result(health.get("degraded_reason"))

        events_resp = self.adapter.list_markets(sport="upcoming", regions="us", markets="h2h", oddsFormat="decimal")
        events = (events_resp.get("data") or {}).get("events", [])
        best_result = None

        for event in events[:12]:
            consensus = defaultdict(list)
            details = []
            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    for outcome in market.get("outcomes", []):
                        implied = _to_implied_probability(outcome.get("price"))
                        if implied is None:
                            continue
                        consensus[outcome.get("name", "")].append(implied)
                        details.append(
                            {
                                "bookmaker": bookmaker.get("key"),
                                "bookmaker_title": bookmaker.get("title"),
                                "outcome": outcome.get("name"),
                                "implied": implied,
                            }
                        )
            if not consensus:
                continue

            averages = {
                outcome: sum(values) / len(values)
                for outcome, values in consensus.items()
                if values
            }
            for row in details:
                avg = averages.get(row["outcome"])
                if avg is None:
                    continue
                deviation = row["implied"] - avg
                confidence = abs(deviation) * max(1, len(consensus[row["outcome"]]))
                result = self.emit_signal(
                    title=f"{event.get('away_team')} at {event.get('home_team')}",
                    summary=(
                        f"{row['bookmaker_title'] or row['bookmaker']} is {deviation:+.3f} from consensus "
                        f"on {row['outcome']}."
                    ),
                    confidence=min(0.99, confidence),
                    signal_taken=abs(deviation) >= 0.05,
                    degraded_reason="" if abs(deviation) >= 0.05 else "No bookmaker drifted far enough from consensus.",
                    data={
                        "event_id": event.get("id"),
                        "bookmaker": row["bookmaker"],
                        "outcome": row["outcome"],
                        "consensus_implied": round(avg, 4),
                        "bookmaker_implied": round(row["implied"], 4),
                        "consensus_deviation": round(deviation, 4),
                        "quota": (events_resp.get("data") or {}).get("quota", {}),
                    },
                )
                if not best_result or result["confidence"] > best_result["confidence"]:
                    best_result = result

        return best_result or self.disabled_result("The Odds API sample did not contain a clean consensus outlier.")

