from __future__ import annotations

"""
Bot 7 — OddsAPI Closing Line Value (CLV) Tracker.

Research-only. Tracks how odds shift from the first time an event is seen
(the "entry snapshot") to the most recent poll ("current snapshot").

Because The Odds API free tier does not expose true historical closing odds,
CLV is approximated as: drift from first-seen poll to latest poll.
This is explicitly labeled as in-memory approximation, not true historical CLV.

Mode:     RESEARCH — no trade signals, no order placement.
Platform: The Odds API (public, quota-aware)
Truth:    PUBLIC DATA ONLY / RESEARCH ONLY / IN-MEMORY APPROXIMATION
"""

from collections import defaultdict
from statistics import median
from typing import Any

from bots.base_research_bot import BaseResearchBot
from services import quota_budgeter


def _to_implied(price: Any) -> float | None:
    try:
        d = float(price)
        return round(1.0 / d, 6) if d > 1.0 else None
    except Exception:
        return None


class OddsApiClvTrackerBot(BaseResearchBot):
    bot_id = "bot_oddsapi_clv_tracker"
    display_name = "OddsAPI CLV Tracker"
    platform = "oddsapi"
    mode = "RESEARCH"
    signal_type = "closing_line_value"
    research_only = True
    implemented = True

    CLV_NOTE = (
        "RESEARCH ONLY — CLV is approximated from in-memory snapshots. "
        "The Odds API free tier does not provide true historical closing odds. "
        "This is a drift study, not a true closing-line comparison. "
        "Do not use for order placement."
    )

    def __init__(self, adapter=None):
        self.adapter = adapter
        # event_id → {outcome_name → first_seen_implied}
        self._entry_snapshots: dict[str, dict[str, float]] = {}
        self._cycle_count = 0

    def run_one_cycle(self) -> dict[str, Any]:
        if self.adapter is None:
            return self.disabled_result("OddsAPI adapter is not wired.")

        reason = quota_budgeter.get_budget().degraded_reason("oddsapi")
        if reason:
            return self.disabled_result(f"QUOTA LIMITED — {reason}")

        health = self.adapter.healthcheck()
        if not health.get("ok"):
            return self.disabled_result(health.get("degraded_reason", "OddsAPI adapter unhealthy."))

        resp = self.adapter.list_markets(
            sport="upcoming", regions="us", markets="h2h", oddsFormat="decimal"
        )
        if not resp.get("ok"):
            return self.disabled_result(resp.get("degraded_reason", "OddsAPI markets unavailable."))

        quota = (resp.get("data") or {}).get("quota", {})
        remaining = quota.get("remaining")
        used = quota.get("used")
        quota_budgeter.record(
            "oddsapi",
            remote_remaining=int(remaining) if remaining is not None else None,
            remote_used=int(used) if used is not None else None,
        )

        self._cycle_count += 1
        events = (resp.get("data") or {}).get("events", [])
        drift_records: list[dict[str, Any]] = []

        for event in events[:20]:
            event_id = event.get("id", "")
            if not event_id:
                continue

            # Build consensus implied for this poll
            by_outcome: dict[str, list[float]] = defaultdict(list)
            for bm in event.get("bookmakers", []):
                for mkt in bm.get("markets", []):
                    if mkt.get("key") != "h2h":
                        continue
                    for outcome in mkt.get("outcomes", []):
                        implied = _to_implied(outcome.get("price"))
                        if implied is not None:
                            by_outcome[outcome.get("name", "")].append(implied)

            if not by_outcome:
                continue

            current_consensus = {
                name: round(median(vals), 5)
                for name, vals in by_outcome.items()
                if vals
            }

            if event_id not in self._entry_snapshots:
                # First time we see this event — store as entry
                self._entry_snapshots[event_id] = dict(current_consensus)
                continue

            entry = self._entry_snapshots[event_id]
            for outcome_name, current_implied in current_consensus.items():
                entry_implied = entry.get(outcome_name)
                if entry_implied is None:
                    continue
                drift = current_implied - entry_implied
                if abs(drift) > 0.001:  # only surface non-trivial drifts
                    drift_records.append({
                        "event_id": event_id,
                        "event_title": f"{event.get('away_team', '?')} @ {event.get('home_team', '?')}",
                        "sport_key": event.get("sport_key"),
                        "outcome": outcome_name,
                        "entry_implied": entry_implied,
                        "current_implied": current_implied,
                        "drift": round(drift, 5),
                        "abs_drift": round(abs(drift), 5),
                        "cycle": self._cycle_count,
                    })

        if not drift_records:
            return self.emit_signal(
                title="CLV Tracker — No drift detected",
                summary=(
                    f"Cycle {self._cycle_count}. "
                    + (f"Tracking {len(self._entry_snapshots)} events. "
                       if self._entry_snapshots else "Building initial entry snapshots. ")
                    + "No significant line movement detected yet."
                ),
                confidence=0.0,
                signal_taken=False,
                degraded_reason="Insufficient drift data. Run more cycles or wait for line movement.",
                data={
                    "tracked_events": len(self._entry_snapshots),
                    "cycle": self._cycle_count,
                    "truth_note": self.CLV_NOTE,
                    "quota": quota,
                },
            )

        # Sort by abs_drift descending
        drift_records.sort(key=lambda r: r["abs_drift"], reverse=True)
        top = drift_records[0]

        return self.emit_signal(
            title=f"CLV Drift — {top['event_title']}",
            summary=(
                f"{top['outcome']}: entry {top['entry_implied']:.4f} → "
                f"current {top['current_implied']:.4f} "
                f"(drift {top['drift']:+.4f}). "
                f"Cycle {self._cycle_count}. {len(drift_records)} total drifts."
            ),
            confidence=min(0.99, top["abs_drift"] * 2.0),
            signal_taken=False,  # RESEARCH ONLY — never signals a trade
            degraded_reason="",
            data={
                "top_drift": top,
                "all_drifts": drift_records[:10],
                "tracked_events": len(self._entry_snapshots),
                "cycle": self._cycle_count,
                "truth_note": self.CLV_NOTE,
                "quota": quota,
            },
        )
