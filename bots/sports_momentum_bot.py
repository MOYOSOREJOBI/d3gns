from __future__ import annotations

from typing import Any

from bots.base_research_bot import BaseResearchBot


class SportsMomentumBot(BaseResearchBot):
    bot_id = "bot_sports_momentum"
    display_name = "Sports Momentum Tracker"
    platform = "oddsapi"
    mode = "RESEARCH"
    signal_type = "momentum"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "medium"
    description = (
        "Monitors real sports data via Balldontlie NBA API (no auth), "
        "TheSportsDB multi-sport API (no auth, free key), and Ergast F1 (no auth). "
        "Computes team win-rate momentum and championship standings across NBA, NFL, "
        "Premier League, La Liga, F1, and more. Flags when strong team momentum "
        "may not be reflected in current betting lines."
    )
    edge_source = "Multi-sport team momentum and standings not yet reflected in current odds lines"
    opp_cadence_per_day = 3.0
    avg_hold_hours = 2.0
    fee_drag_bps = 130
    fill_rate = 0.50
    platforms = ["oddsapi", "balldontlie", "thesportsdb", "ergast_f1"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        from adapters.balldontlie import BallDontLieAdapter
        from adapters.ergast_f1 import ErgastF1Adapter
        from adapters.thesportsdb import TheSportsDBAdapter

        bdl   = BallDontLieAdapter()
        f1    = ErgastF1Adapter()
        tsdb  = TheSportsDBAdapter()

        errors: list[str] = []
        source_status: dict[str, str] = {}
        momentum_signals: list[dict[str, Any]] = []

        # ── NBA via BallDontLie ───────────────────────────────────────────────
        nba_standings = None
        nba_top_team  = None
        nba_res = bdl.get_standings_signal(season=2024)
        if nba_res.get("ok"):
            nd = nba_res["data"]
            nba_standings = nd.get("standings", [])[:10]
            nba_top_team  = nd.get("top_team")
            source_status["balldontlie_nba"] = "live"
            if nba_standings and nba_standings[0].get("win_pct", 0) > 0.70:
                momentum_signals.append({
                    "sport": "NBA", "source": "balldontlie",
                    "signal": f"strong_favorite:{nba_standings[0]['team']}",
                    "win_pct": nba_standings[0]["win_pct"],
                })
        else:
            errors.append(f"balldontlie: {nba_res.get('error', 'failed')}")
            source_status["balldontlie_nba"] = "error"

        # ── TheSportsDB — multi-sport snapshot ───────────────────────────────
        multi_sport_data = {}
        sports_snapshot_res = tsdb.get_multi_sport_snapshot()
        if sports_snapshot_res.get("ok"):
            multi_sport_data = sports_snapshot_res["data"]
            source_status["thesportsdb_snapshot"] = "live"
        else:
            errors.append(f"thesportsdb_snapshot: {sports_snapshot_res.get('error')}")
            source_status["thesportsdb_snapshot"] = "error"

        # Premier League table
        pl_table = []
        pl_res = tsdb.get_league_table(4328)  # Premier League
        if pl_res.get("ok"):
            pl_table = pl_res["data"].get("table", [])[:5]
            source_status["thesportsdb_pl"] = "live"
            if pl_table:
                leader = pl_table[0]
                pts_gap = (leader.get("intPoints", 0) or 0) - (pl_table[1].get("intPoints", 0) if len(pl_table) > 1 else 0)
                if pts_gap >= 10:
                    momentum_signals.append({
                        "sport": "Premier League", "source": "thesportsdb",
                        "signal": f"runaway_leader:{leader.get('strTeam')}",
                        "points_gap": pts_gap,
                    })
        else:
            errors.append(f"thesportsdb_pl: {pl_res.get('error')}")
            source_status["thesportsdb_pl"] = "error"

        # NBA table from TheSportsDB (cross-check with BallDontLie)
        nba_tsdb_table = []
        nba_tsdb_res = tsdb.get_league_table(4387)  # NBA
        if nba_tsdb_res.get("ok"):
            nba_tsdb_table = nba_tsdb_res["data"].get("table", [])[:5]
            source_status["thesportsdb_nba"] = "live"
        else:
            source_status["thesportsdb_nba"] = "error"

        # Upcoming major events (next 48h)
        upcoming_events: list[dict] = []
        for league_id, sport_name in [(4391, "NFL"), (4387, "NBA"), (4328, "Premier League")]:
            ev_res = tsdb.get_next_events(league_id)
            if ev_res.get("ok"):
                evts = ev_res["data"].get("events", [])[:2]
                for e in evts:
                    e["sport_name"] = sport_name
                upcoming_events.extend(evts)

        # ── F1 Driver Standings via Ergast ────────────────────────────────────
        f1_leader = None
        f1_driver_standings = None
        f1_next_race = None

        f1_drivers_res = f1.get_driver_standings()
        if f1_drivers_res.get("ok"):
            f1_driver_standings = f1_drivers_res["data"].get("standings", [])[:5]
            if f1_driver_standings:
                f1_leader = f1_driver_standings[0]
            source_status["ergast_f1_drivers"] = "live"
        else:
            errors.append(f"ergast_f1: {f1_drivers_res.get('error')}")
            source_status["ergast_f1_drivers"] = "error"

        f1_next_res = f1.get_next_race()
        if f1_next_res.get("ok"):
            f1_next_race = f1_next_res["data"].get("next_race")
            source_status["ergast_f1_schedule"] = "live"
        else:
            source_status["ergast_f1_schedule"] = "error"

        if f1_driver_standings and len(f1_driver_standings) >= 2:
            gap = (f1_driver_standings[0].get("points", 0) or 0) - (f1_driver_standings[1].get("points", 0) or 0)
            if gap > 50:
                momentum_signals.append({
                    "sport": "F1", "source": "ergast",
                    "signal": f"championship_leader:{f1_leader.get('driver')}",
                    "points_gap": gap,
                })

        # ── Compute confidence ────────────────────────────────────────────────
        live_count = sum(1 for v in source_status.values() if v == "live")
        confidence = 0.0
        signal_taken = "neutral"
        degraded_reason = ""

        if live_count == 0:
            degraded_reason = "All sports data sources failed."
        else:
            for _ in momentum_signals:
                confidence += 0.12
            confidence = min(confidence, 0.55)

            if momentum_signals:
                signal_taken = "bullish"  # strong momentum signals = actionable markets
            else:
                degraded_reason = "No strong multi-sport momentum signal. Standings are competitive."

        # Build summary
        nba_str = f"NBA: {nba_top_team}" if nba_top_team else "NBA: no data"
        f1_str  = f"F1 leader: {f1_leader.get('driver')} ({f1_leader.get('points')} pts)" if f1_leader else "F1: no data"
        pl_str  = f"PL leader: {pl_table[0].get('strTeam')} ({pl_table[0].get('intPoints')} pts)" if pl_table else ""
        next_race_str = f"Next F1 race: {f1_next_race.get('race_name')} {f1_next_race.get('date')}" if f1_next_race else ""
        upcoming_str = f"Upcoming events: {len(upcoming_events)}"

        summary = (
            f"Multi-sport momentum scan. {nba_str}. {f1_str}. {pl_str}. "
            f"{next_race_str}. {upcoming_str}. "
            f"Momentum signals: {len(momentum_signals)}. Live sources: {live_count}. "
            f"Signal: {signal_taken}."
        )

        return self.emit_signal(
            title="Sports Momentum Tracker",
            summary=summary,
            confidence=confidence,
            signal_taken=signal_taken,
            degraded_reason=degraded_reason,
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={
                "nba_standings": nba_standings,
                "nba_top_team": nba_top_team,
                "nba_tsdb_table": nba_tsdb_table,
                "pl_table": pl_table,
                "f1_driver_standings": f1_driver_standings,
                "f1_leader": f1_leader,
                "f1_next_race": f1_next_race,
                "upcoming_events": upcoming_events[:6],
                "multi_sport_snapshot": multi_sport_data,
                "momentum_signals": momentum_signals,
                "source_status": source_status,
                "errors": errors,
                "sources": [
                    "api.balldontlie.io/v1 (NBA, no auth)",
                    "www.thesportsdb.com/api/v1 (multi-sport, free key)",
                    "ergast.com/api/f1 (Formula 1, no auth)",
                ],
            },
        )
