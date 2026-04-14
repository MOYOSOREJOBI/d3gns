from __future__ import annotations

from typing import Any

from models.proposal import Proposal

MAX_DRAWDOWN_PCT = 0.199  # 19.9% max loss — NEVER reaches 20%
REPEL_START_PCT = 0.05    # Start repelling at 5% drawdown


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def compute_floor_multiplier(working_capital: float, floor: float, repel_zone: float) -> tuple[float, float]:
    safe_repel_zone = max(float(repel_zone or 0.0), 1e-9)
    headroom = float(working_capital or 0.0) - float(floor or 0.0)
    proximity = clamp(headroom / safe_repel_zone, 0.0, 1.0)
    floor_multiplier = max(0.05, proximity**2)
    return headroom, floor_multiplier


def compute_kelly_fraction(probability: float, odds_multiple: float) -> float:
    b = max(float(odds_multiple or 0.0), 1e-9)
    p = clamp(float(probability or 0.0), 0.0, 1.0)
    q = 1.0 - p
    return max(0.0, (b * p - q) / b)


def compute_forcefield_floor(starting_capital: float, max_drawdown: float = MAX_DRAWDOWN_PCT) -> float:
    """Compute the absolute floor below which capital must never drop."""
    return starting_capital * (1.0 - max_drawdown)


def compute_dynamic_repel(
    working_capital: float,
    starting_capital: float,
    max_drawdown: float = MAX_DRAWDOWN_PCT,
    repel_start: float = REPEL_START_PCT,
) -> dict[str, float]:
    """
    Progressive repeller: as drawdown grows from repel_start toward max_drawdown,
    sizing shrinks quadratically toward zero. NEVER allows crossing the floor.
    """
    floor = starting_capital * (1.0 - max_drawdown)
    repel_zone_start = starting_capital * (1.0 - repel_start)

    if working_capital <= floor:
        return {"multiplier": 0.0, "headroom": 0.0, "floor": floor, "zone": "FLOOR"}

    if working_capital >= repel_zone_start:
        return {
            "multiplier": 1.0,
            "headroom": working_capital - floor,
            "floor": floor,
            "zone": "NORMAL",
        }

    # Inside repel zone: quadratic decay
    total_zone = repel_zone_start - floor
    distance_from_floor = working_capital - floor
    proximity = clamp(distance_from_floor / max(total_zone, 1e-9), 0.0, 1.0)
    multiplier = max(0.001, proximity ** 2)

    return {
        "multiplier": round(multiplier, 6),
        "headroom": round(distance_from_floor, 2),
        "floor": round(floor, 2),
        "zone": "REPEL",
    }


def compute_vault_lock_amount(working_capital: float, start_working_capital: float) -> float:
    current = float(working_capital or 0.0)
    start = float(start_working_capital or 0.0)
    if start <= 0:
        return 0.0
    if current >= 1.20 * start:
        return max(0.0, current - start)
    return 0.0


def evaluate_proposal(
    proposal: Proposal,
    *,
    working_capital: float,
    floor: float,
    repel_zone: float,
    phase: str = "NORMAL",
    regime: str = "normal",
    hard_risk_cap: float = 0.02,
    liquidity: float = 1.0,
    regime_multiplier: float = 1.0,
    correlation_multiplier: float = 1.0,
    execution_quality: float = 1.0,
    odds_multiple: float = 1.0,
    max_open_proposals_per_correlation_group: int = 3,
    open_proposals_in_correlation_group: int = 0,
    max_venue_concentration: float = 0.35,
    venue_concentration: float = 0.0,
    max_total_notional: float = 1000.0,
    current_total_notional: float = 0.0,
    max_simultaneous_event_exposure: int = 3,
    simultaneous_event_exposure: int = 0,
    max_slippage_allowance_bps: float = 150.0,
    runtime_mode: str | None = None,
) -> dict[str, Any]:
    mode = str(runtime_mode or proposal.runtime_mode or "paper").strip().lower()
    reasons: list[str] = []

    if proposal.edge_post_fee_bps <= 0:
        return {
            "decision": "reject",
            "size_usd": 0.0,
            "size_contracts": None,
            "phase": phase,
            "floor_multiplier": 0.05,
            "regime": regime,
            "reasons": ["non_positive_post_fee_edge"],
        }

    if open_proposals_in_correlation_group >= max_open_proposals_per_correlation_group:
        return {
            "decision": "reject",
            "size_usd": 0.0,
            "size_contracts": None,
            "phase": phase,
            "floor_multiplier": 0.05,
            "regime": regime,
            "reasons": ["correlation_cap"],
        }

    if venue_concentration >= max_venue_concentration:
        return {
            "decision": "reject",
            "size_usd": 0.0,
            "size_contracts": None,
            "phase": phase,
            "floor_multiplier": 0.05,
            "regime": regime,
            "reasons": ["venue_concentration_cap"],
        }

    if current_total_notional >= max_total_notional:
        return {
            "decision": "reject",
            "size_usd": 0.0,
            "size_contracts": None,
            "phase": phase,
            "floor_multiplier": 0.05,
            "regime": regime,
            "reasons": ["total_notional_cap"],
        }

    if simultaneous_event_exposure >= max_simultaneous_event_exposure:
        return {
            "decision": "reject",
            "size_usd": 0.0,
            "size_contracts": None,
            "phase": phase,
            "floor_multiplier": 0.05,
            "regime": regime,
            "reasons": ["event_exposure_cap"],
        }

    if float(proposal.max_slippage_bps or 0.0) > float(max_slippage_allowance_bps or 0.0):
        return {
            "decision": "reject",
            "size_usd": 0.0,
            "size_contracts": None,
            "phase": phase,
            "floor_multiplier": 0.05,
            "regime": regime,
            "reasons": ["slippage_cap"],
        }

    starting_capital = max(float(working_capital or 0.0), float(floor or 0.0) + float(repel_zone or 0.0))
    repel = compute_dynamic_repel(
        float(working_capital or 0.0),
        starting_capital,
        max_drawdown=MAX_DRAWDOWN_PCT,
        repel_start=REPEL_START_PCT,
    )
    headroom = float(repel.get("headroom", 0.0) or 0.0)
    floor_multiplier = float(repel.get("multiplier", 0.0) or 0.0)
    if headroom <= 0 or repel.get("zone") == "FLOOR":
        reasons.append("forcefield_floor")

    kelly_fraction = compute_kelly_fraction(proposal.confidence, odds_multiple)
    size_base = min(kelly_fraction * 0.25, max(float(hard_risk_cap or 0.0), 0.0))
    size_final = (
        size_base
        * clamp(float(proposal.confidence or 0.0), 0.0, 1.0)
        * clamp(float(liquidity or 0.0), 0.0, 1.0)
        * clamp(float(regime_multiplier or 0.0), 0.0, 1.5)
        * clamp(float(correlation_multiplier or 0.0), 0.0, 1.0)
        * clamp(float(execution_quality or 0.0), 0.0, 1.0)
        * floor_multiplier
    )
    size_usd = round(max(0.0, float(working_capital or 0.0) * size_final), 2)

    # Prevent fee churning on tiny bankrolls
    min_trade_usd = max(1.0, float(working_capital or 0.0) * 0.005)
    if 0 < size_usd < min_trade_usd:
        size_usd = 0.0
        reasons.append("below_minimum_trade_size")

    decision = "approved"
    if size_usd <= 0:
        decision = "reject"
        reasons.append("zero_size")
    elif size_usd < max(float(working_capital or 0.0) * 0.0025, 1.0):
        decision = "scout"
        reasons.append("small_probe")

    if decision != "reject" and current_total_notional + size_usd > max_total_notional:
        size_usd = round(max(0.0, max_total_notional - current_total_notional), 2)
        decision = "reduced" if size_usd > 0 else "reject"
        reasons.append("total_notional_cap")

    if decision != "reject" and mode == "shadow":
        decision = "shadow_only"
        reasons.append("shadow_mode")
    elif decision != "reject" and mode == "demo":
        decision = "demo_only"
        reasons.append("demo_mode")
    elif decision != "reject" and mode in {"paper", "research", "replay", "live-disabled"}:
        reasons.append(f"{mode}_mode")

    return {
        "decision": decision,
        "size_usd": size_usd,
        "size_contracts": None,
        "phase": phase,
        "floor_multiplier": round(floor_multiplier, 4),
        "regime": regime,
        "repel_zone": repel.get("zone", "NORMAL"),
        "headroom": round(headroom, 2),
        "reasons": reasons,
    }
