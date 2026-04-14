from __future__ import annotations

"""
Bot 11 — SportsDataIO Line Movement Research.

Fetches game-level odds data from the SportsDataIO trial API and tracks
opening vs. current line movement where available.

Because the SportsDataIO free trial may return scrambled or synthetic data,
all signals are clearly labeled TRIAL / SCRAMBLED DATA and are never
used to trigger any order placement.

Mode:     RESEARCH — no trade signals, no order placement.
Platform: SportsDataIO trial (free tier, scrambled data possible)
Truth:    TRIAL — SCRAMBLED DATA / RESEARCH ONLY

Hard rules:
  - signal_taken is always False. This is research only.
  - Every payload includes a prominent TRIAL disclaimer.
  - Requires ENABLE_SPORTSDATAIO_TRIAL=true and SPORTSDATAIO_API_KEY.
"""

from typing import Any

from bots.base_research_bot import BaseResearchBot

_TRIAL_NOTE = (
    "TRIAL / SCRAMBLED DATA — SportsDataIO free trial data may be scrambled "
    "or synthetic. Research and study purposes only. "
    "Do not use for live execution or treat as real tradable prices."
)

# Minimum opening→current line movement to surface
_MOVE_THRESHOLD = 0.5  # In decimal odds


def _extract_opening_line(market_entry: dict[str, Any]) -> float | None:
    for key in ("OpeningMoneyLine", "OpeningSpread", "OpeningOverUnder", "OpeningOdds"):
        val = market_entry.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return None


def _extract_current_line(market_entry: dict[str, Any]) -> float | None:
    for key in ("MoneyLine", "PointSpread", "OverUnder", "CurrentOdds"):
        val = market_entry.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return None


def _extract_game_title(game: dict[str, Any]) -> str:
    away = game.get("AwayTeam") or game.get("AwayTeamName") or "?"
    home = game.get("HomeTeam") or game.get("HomeTeamName") or "?"
    return f"{away} @ {home}"


class SportsDataIoLineMovementBot(BaseResearchBot):
    bot_id = "bot_sportsdataio_line_movement_research"
    display_name = "SportsDataIO Line Movement"
    platform = "sportsdataio_trial"
    mode = "RESEARCH"
    signal_type = "line_movement"
    research_only = True
    implemented = True

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        if self.adapter is None:
            return self.disabled_result(
                "SportsDataIO trial adapter is not wired. "
                "Set ENABLE_SPORTSDATAIO_TRIAL=true and provide SPORTSDATAIO_API_KEY."
            )

        health = self.adapter.healthcheck()
        if not health.get("ok"):
            return self.disabled_result(
                health.get("degraded_reason", "SportsDataIO trial adapter unavailable.")
            )

        # Fetch current games
        games_resp = self.adapter.list_markets(sport="nfl", season="2024REG", week="1")
        if not games_resp.get("ok"):
            return self.disabled_result(
                games_resp.get("degraded_reason", "SportsDataIO games unavailable.")
            )

        games = (games_resp.get("data") or {}).get("games", [])
        if not games:
            return self.disabled_result(
                "SportsDataIO returned no games for the requested sport/season/week. "
                "Trial key entitlements may be limited."
            )

        movement_records: list[dict[str, Any]] = []

        for game in games[:15]:
            game_id_raw = game.get("GameID") or game.get("GameKey") or game.get("ScoreID")
            if game_id_raw is None:
                continue
            game_id = str(game_id_raw)
            title = _extract_game_title(game)

            # Try to get line movement detail
            move_resp = self.adapter.get_line_movement(game_id)
            if not move_resp.get("ok"):
                # Fall back to top-level game data
                opening = _extract_opening_line(game)
                current = _extract_current_line(game)
                if opening is not None and current is not None:
                    move = current - opening
                    if abs(move) >= _MOVE_THRESHOLD:
                        movement_records.append({
                            "game_id": game_id,
                            "title": title,
                            "opening": opening,
                            "current": current,
                            "move": round(move, 2),
                            "abs_move": round(abs(move), 2),
                            "source": "game_level_fallback",
                        })
                continue

            betting_markets = (move_resp.get("data") or {}).get("betting_markets", [])
            for bm in betting_markets[:5]:
                if not isinstance(bm, dict):
                    continue
                opening = _extract_opening_line(bm)
                current = _extract_current_line(bm)
                if opening is None or current is None:
                    continue
                move = current - opening
                if abs(move) >= _MOVE_THRESHOLD:
                    movement_records.append({
                        "game_id": game_id,
                        "title": title,
                        "market_type": bm.get("BettingMarketType") or bm.get("MarketType") or "unknown",
                        "opening": opening,
                        "current": current,
                        "move": round(move, 2),
                        "abs_move": round(abs(move), 2),
                        "source": "line_movement_endpoint",
                    })
                    break  # One signal per game is sufficient

        if not movement_records:
            return self.emit_signal(
                title="SportsDataIO Line Movement — No movement detected",
                summary=(
                    f"Scanned {len(games)} games. "
                    "No line movement above threshold. "
                    "Trial data may be scrambled or static."
                ),
                confidence=0.0,
                signal_taken=False,
                degraded_reason=(
                    "No line movement detected. "
                    "This may reflect trial data limitations (scrambled/static lines)."
                ),
                data={
                    "games_scanned": len(games),
                    "truth_note": _TRIAL_NOTE,
                    "scrambled_data_warning": True,
                },
            )

        movement_records.sort(key=lambda r: r["abs_move"], reverse=True)
        top = movement_records[0]

        return self.emit_signal(
            title=f"Line Movement — {top['title']}",
            summary=(
                f"Opening {top['opening']} → current {top['current']} "
                f"(Δ {top['move']:+.2f}). "
                f"{len(movement_records)} games with movement detected. "
                "RESEARCH ONLY — trial data may be scrambled."
            ),
            confidence=0.0,   # RESEARCH ONLY — never produces a confidence for trading
            signal_taken=False,
            degraded_reason="",
            data={
                "top_move": top,
                "all_moves": movement_records[:10],
                "games_scanned": len(games),
                "truth_note": _TRIAL_NOTE,
                "scrambled_data_warning": True,
            },
        )
