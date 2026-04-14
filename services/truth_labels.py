from __future__ import annotations

import os
from typing import Any


EPHEMERAL_PATH_PREFIXES = ("/tmp/", "/var/folders/")
EPHEMERAL_ENV_HINTS = ("RENDER", "RAILWAY_ENVIRONMENT", "DYNO", "K_SERVICE")
RUNTIME_MODE_TRUTH_LABELS = {
    "research": "RESEARCH — SCAN ONLY",
    "replay": "REPLAY — HISTORICAL DATA",
    "paper": "PAPER — SYNTHETIC",
    "shadow": "SHADOW — REAL DATA, NO MONEY",
    "demo": "DEMO — EXCHANGE SANDBOX",
    "live-disabled": "LIVE-CAPABLE — SENDING BLOCKED",
    "live": "LIVE — REAL CAPITAL",
}


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_runtime_mode(value: Any, default: str = "paper") -> str:
    mode = str(value or default).strip().lower()
    if mode in {"live_disabled", "live disabled"}:
        return "live-disabled"
    if mode in RUNTIME_MODE_TRUTH_LABELS:
        return mode
    return default


def runtime_truth_label(value: Any, default: str = "paper") -> str:
    mode = normalize_runtime_mode(value, default=default)
    return RUNTIME_MODE_TRUTH_LABELS.get(mode, RUNTIME_MODE_TRUTH_LABELS[default])


def allows_external_execution(value: Any) -> bool:
    return normalize_runtime_mode(value) in {"demo", "live"}


def storage_mode_from_db_path(db_path: str) -> tuple[str, str]:
    normalized = os.path.abspath(db_path)
    if normalized.startswith(EPHEMERAL_PATH_PREFIXES):
        return "ephemeral", f"DB path {normalized} is on a temporary filesystem."
    if any(os.getenv(key) for key in EPHEMERAL_ENV_HINTS) and not normalized.startswith("/data/"):
        return "ephemeral", f"DB path {normalized} is local to a likely ephemeral deployment filesystem."
    return "durable", f"DB path {normalized} appears durable based on current host heuristics."


def network_routing_truth(proxy_host: str, proxy_port: str, proxy_verified: bool = False) -> tuple[str, str]:
    if not proxy_host or not proxy_port:
        return "direct", "No proxy is configured for adapter HTTP requests."
    if proxy_verified:
        return "proxy_cosmetic", "Proxy is configured and testable, but legacy venue traffic is not globally verified through it."
    return "proxy_cosmetic", "Proxy is configured, but routing is not verified for all venue requests."


def venue_auth_truth(*, credentials_present: bool = False, validated: bool = False, failed: bool = False, public_data: bool = False) -> str:
    if failed:
        return "failed"
    if validated or public_data:
        return "validated"
    if credentials_present:
        return "present"
    return "missing"


def reconciliation_state(enabled_live_execution: bool, externally_reconciled: bool = False) -> str:
    if externally_reconciled:
        return "on"
    if enabled_live_execution:
        return "partial"
    return "off"


# ── Promotion ladder ─────────────────────────────────────────────────────────

PROMOTION_TIERS = ["catalog", "research", "paper", "dust_live", "capped_live"]

PROMOTION_TIER_DESCRIPTIONS = {
    "catalog":    "Registered in the catalog. No evidence of live or paper execution.",
    "research":   "Signal research phase. Runs on public/delayed data only. No execution.",
    "paper":      "Paper trading. Full execution logic active, no real money.",
    "dust_live":  "Dust-live / canary. Tiny real stake, strict audit, breach conditions enforced.",
    "capped_live": "Capped live. Bounded real stake. Operator supervised. Not fully autonomous.",
}

PROMOTION_BLOCKERS: dict[str, list[str]] = {
    "research": [
        "Bot must have at least one successful catalog-level metadata check.",
    ],
    "paper": [
        "Bot must have passed research phase with credible signal evidence.",
        "Brier score or CLV metric required where applicable.",
    ],
    "dust_live": [
        "Bot must have paper execution evidence with no major reconciliation drift.",
        "Operator must explicitly enable dust-live mode.",
        "Max capital at risk must be configured.",
        "Canary breach conditions must be defined.",
    ],
    "capped_live": [
        "Bot must have completed at least one canary session without breach.",
        "Operator review of canary session log required.",
        "go/no-go checklist must be completed and signed off.",
        "'Ready for wider live use' still requires operator supervision — this is NOT fully autonomous.",
    ],
}


def compute_promotion_gate(
    current_tier: str,
    target_tier: str,
    *,
    has_signal_evidence: bool = False,
    brier_score: float | None = None,
    clv_measured: bool = False,
    paper_execution_count: int = 0,
    reconciliation_drift_pct: float | None = None,
    canary_sessions_completed: int = 0,
    canary_breach_count: int = 0,
    operator_confirmed: bool = False,
) -> dict[str, Any]:
    """
    Evaluate whether a bot can advance from current_tier to target_tier.

    Returns:
        {
          "eligible": bool,
          "target_tier": str,
          "blockers": [str],    # non-empty when eligible=False
          "notes": [str],       # informational warnings even when eligible
        }
    """
    blockers: list[str] = []
    notes: list[str] = []

    if target_tier not in PROMOTION_TIERS:
        return {"eligible": False, "target_tier": target_tier, "blockers": [f"Unknown tier: {target_tier}"], "notes": []}

    current_idx = PROMOTION_TIERS.index(current_tier) if current_tier in PROMOTION_TIERS else -1
    target_idx  = PROMOTION_TIERS.index(target_tier)

    if target_idx <= current_idx:
        return {"eligible": True, "target_tier": target_tier, "blockers": [], "notes": ["Already at or above target tier."], "tier_description": PROMOTION_TIER_DESCRIPTIONS.get(target_tier, "")}

    if target_idx - current_idx > 1:
        skipped = PROMOTION_TIERS[current_idx + 1] if current_idx >= 0 else PROMOTION_TIERS[0]
        blockers.append(f"Cannot skip tiers. Must pass through '{skipped}' first.")

    if target_tier == "paper":
        if not has_signal_evidence:
            blockers.append("No credible signal evidence recorded for this bot.")
        if brier_score is not None and brier_score > 0.25:
            blockers.append(f"Brier score {brier_score:.3f} exceeds paper-ready threshold (0.25).")

    if target_tier == "dust_live":
        if paper_execution_count < 10:
            blockers.append(f"Only {paper_execution_count} paper execution cycles recorded; need at least 10.")
        if reconciliation_drift_pct is not None and reconciliation_drift_pct > 2.0:
            blockers.append(f"Reconciliation drift {reconciliation_drift_pct:.1f}% exceeds 2% threshold for dust-live.")
        if not operator_confirmed:
            blockers.append("Operator must explicitly enable dust-live mode in settings.")

    if target_tier == "capped_live":
        if canary_sessions_completed < 1:
            blockers.append("No completed canary sessions. Complete at least one before capped-live.")
        if canary_breach_count > 0 and canary_sessions_completed <= canary_breach_count:
            blockers.append("All canary sessions ended in breach. Review canary log before advancing.")
        if not operator_confirmed:
            blockers.append("go/no-go checklist must be completed and operator-confirmed.")
        notes.append("capped_live is operator-supervised. It is NOT fully autonomous production deployment.")

    return {
        "eligible": len(blockers) == 0,
        "target_tier": target_tier,
        "blockers": blockers,
        "notes": notes,
        "tier_description": PROMOTION_TIER_DESCRIPTIONS.get(target_tier, ""),
    }


def build_system_truth(
    *,
    execution_mode: str,
    reconciliation_state_value: str,
    storage_mode: str,
    storage_reason: str,
    wallet_truth: str,
    network_routing_truth_value: str,
    network_routing_reason: str,
    venue_auth_truth_map: dict[str, str],
) -> dict[str, Any]:
    return {
        "execution_mode": execution_mode,
        "reconciliation_state": reconciliation_state_value,
        "storage_mode": storage_mode,
        "storage_reason": storage_reason,
        "wallet_truth": wallet_truth,
        "network_routing_truth": network_routing_truth_value,
        "network_routing_reason": network_routing_reason,
        "venue_auth_truth": venue_auth_truth_map,
    }
