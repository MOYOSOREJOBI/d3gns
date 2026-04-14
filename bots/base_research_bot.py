from __future__ import annotations

import uuid
from typing import Any

from models.proposal import Proposal


class BaseResearchBot:
    bot_id = "bot_stub"
    display_name = "Stub Bot"
    platform = "unknown"
    mode = "RESEARCH"
    signal_type = "none"
    paper_only = False
    delayed_only = False
    research_only = False
    watchlist_only = False
    demo_only = False
    default_enabled = False
    implemented = False
    degraded_reason = ""
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "medium"
    # Promotion ladder tier: catalog → research → paper → dust_live → capped_live
    promotion_tier = "catalog"
    description = ""
    edge_source = ""
    opp_cadence_per_day = 0.0
    avg_hold_hours = 1.0
    fee_drag_bps = 0
    fill_rate = 1.0
    platforms: list = []

    def metadata(self) -> dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "display_name": self.display_name,
            "platform": self.platform,
            "mode": self.mode,
            "signal_type": self.signal_type,
            "paper_only": self.paper_only,
            "delayed_only": self.delayed_only,
            "research_only": self.research_only,
            "watchlist_only": self.watchlist_only,
            "demo_only": self.demo_only,
            "default_enabled": self.default_enabled,
            "implemented": self.implemented,
            "truth_label": self.truth_label,
            "quality_tier": self.quality_tier,
            "risk_tier": self.risk_tier,
            "promotion_tier": self.promotion_tier,
            "description": self.description,
            "edge_source": self.edge_source,
            "opp_cadence_per_day": self.opp_cadence_per_day,
            "avg_hold_hours": self.avg_hold_hours,
            "fee_drag_bps": self.fee_drag_bps,
            "fill_rate": self.fill_rate,
            "platforms": self.platforms,
        }

    def disabled_result(self, reason: str | None = None) -> dict[str, Any]:
        return self.emit_signal(
            title=f"{self.display_name} is disabled",
            summary=reason or self.degraded_reason or "This bot is registered but intentionally disabled.",
            confidence=0.0,
            signal_taken=False,
            degraded_reason=reason or self.degraded_reason or "Disabled",
            data={},
        )

    def emit_signal(
        self,
        *,
        title: str,
        summary: str,
        confidence: float,
        signal_taken: bool,
        degraded_reason: str = "",
        data: dict[str, Any] | None = None,
        factor_contributions: dict[str, float] | None = None,
        skip_reason: dict[str, Any] | None = None,
        reason_codes: list[str] | None = None,
        platform_truth_label: str | None = None,
    ) -> dict[str, Any]:
        payload = self.metadata()
        payload.update(
            {
                "title": title,
                "summary": summary,
                "confidence": round(float(confidence or 0.0), 4),
                "signal_taken": bool(signal_taken),
                "degraded_reason": degraded_reason,
                "data": data or {},
                "factor_contributions": factor_contributions or {},
                "skip_reason": skip_reason or {},
                "reason_codes": reason_codes or [],
                "platform_truth_label": platform_truth_label or self.mode,
            }
        )
        return payload

    def explain_signal(self, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": result.get("title", ""),
            "summary": result.get("summary", ""),
            "confidence": result.get("confidence", 0.0),
            "degraded_reason": result.get("degraded_reason", ""),
        }

    def generate_proposal(self, context: dict[str, Any] | None = None) -> Proposal | None:
        """
        Default proposal generator. Runs one cycle, converts signal to Proposal.
        Bots should override for custom logic. Returns None if no actionable edge.
        """
        ctx = context or {}
        result = self.run_one_cycle()

        if not result.get("signal_taken"):
            return None

        confidence = float(result.get("confidence", 0) or 0)
        if confidence < 0.1:
            return None

        data = result.get("data", {}) or {}
        edge_bps = float(
            data.get("edge_bps", 0) or data.get("ev_estimate", 0) or 0
        ) * 10000
        if edge_bps == 0 and confidence > 0:
            edge_bps = confidence * 100  # fallback: use confidence as proxy

        fee_drag = float(self.fee_drag_bps or 0)
        edge_post_fee = edge_bps - fee_drag

        if edge_post_fee <= 0:
            return None

        side = str(
            data.get("side", "")
            or data.get("action", "")
            or data.get("signal_side", "")
            or "BUY"
        ).upper()
        if side not in {"BUY", "SELL", "BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO"}:
            side = "BUY"

        market_id = str(
            data.get("market_id", "")
            or data.get("ticker", "")
            or data.get("symbol", "")
            or data.get("event_ticker", "")
            or ""
        )

        runtime_mode = str(ctx.get("runtime_mode", self.mode) or "paper").lower()

        return Proposal(
            proposal_id=f"p_{uuid.uuid4().hex[:12]}",
            bot_id=self.bot_id,
            platform=self.platform,
            market_id=market_id,
            side=side,
            confidence=round(confidence, 4),
            edge_bps=round(edge_bps, 2),
            edge_post_fee_bps=round(edge_post_fee, 2),
            expected_hold_s=float(self.avg_hold_hours or 1) * 3600,
            max_slippage_bps=float(data.get("max_slippage_bps", 100) or 100),
            correlation_key=self.bot_id.split("_")[1] if "_" in self.bot_id else "",
            reason_code=result.get("title", "signal"),
            runtime_mode=runtime_mode,
            truth_label=result.get("platform_truth_label", self.truth_label),
            metadata={
                "signal_title": result.get("title", ""),
                "signal_summary": result.get("summary", ""),
                **{k: v for k, v in data.items() if isinstance(v, (str, int, float, bool))},
            },
        )

    def run_one_cycle(self) -> dict[str, Any]:
        return self.disabled_result()


class DisabledStubBot(BaseResearchBot):
    implemented = False
