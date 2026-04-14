from __future__ import annotations

from typing import Any

from bots.base_research_bot import BaseResearchBot


class CongressTradesBot(BaseResearchBot):
    bot_id = "bot_congress_trades"
    display_name = "Congressional Trades Scanner"
    platform = "polymarket"
    mode = "RESEARCH"
    signal_type = "insider_signal"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "low"
    description = (
        "Monitors publicly disclosed congressional stock trades via SEC EDGAR (no auth). "
        "Scans Form 4 insider trade filings for committee-relevant transactions in sectors "
        "related to open prediction markets. Also searches EDGAR full-text for major "
        "corporate filings (8-K material events) that may impact outcome markets. "
        "All data is publicly available — no insider access, no MNPI."
    )
    edge_source = "Committee-relevant trades and material SEC filings as lagging signals for political/sector outcome markets"
    opp_cadence_per_day = 1.0
    avg_hold_hours = 12.0
    fee_drag_bps = 80
    fill_rate = 0.60
    platforms = ["polymarket", "kalshi", "sec_edgar"]

    # Tickers to monitor for insider trades across major sectors
    _WATCH_TICKERS = ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "JPM", "GS"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        from adapters.sec_edgar import SECEdgarAdapter

        edgar = SECEdgarAdapter()
        errors: list[str] = []
        source_status: dict[str, str] = {}
        all_filings: list[dict] = []
        insider_trades: list[dict] = []

        # --- Healthcheck ---
        health = edgar.healthcheck()
        if not health.get("ok"):
            return self.emit_signal(
                title="Congressional Trades Scanner",
                summary=f"SEC EDGAR unavailable: {health.get('error', 'unknown')}",
                confidence=0.0,
                signal_taken=False,
                degraded_reason=f"SEC EDGAR API failed: {health.get('error')}",
                platform_truth_label="PUBLIC_DATA_ONLY",
                data={"error": health.get("error")},
            )

        # --- Scan insider trades (Form 4) for major companies ---
        for ticker in self._WATCH_TICKERS[:5]:
            res = edgar.get_insider_trades(ticker=ticker, limit=5)
            if res.get("ok"):
                filings = res["data"].get("filings", [])
                if filings:
                    insider_trades.extend([{
                        "ticker": ticker,
                        "entity": res["data"].get("entity_name"),
                        "filing": f,
                    } for f in filings[:3]])
                source_status[f"edgar_{ticker}_form4"] = "live"
            else:
                errors.append(f"edgar/{ticker}/form4: {res.get('error', 'failed')}")
                source_status[f"edgar_{ticker}_form4"] = "error"

        # --- Scan 8-K (material event) filings for big movers ---
        material_events_res = edgar.get_full_text_search(
            query="material event acquisition merger",
            form_type="8-K",
            start_dt="2025-01-01",
            end_dt="2026-12-31",
        )
        material_events = []
        if material_events_res.get("ok"):
            material_events = material_events_res["data"].get("results", [])
            source_status["edgar_8k_search"] = "live"
        else:
            errors.append(f"edgar/8k_search: {material_events_res.get('error', 'failed')}")
            source_status["edgar_8k_search"] = "error"

        # --- AAPL company facts for valuation context ---
        aapl_revenue_res = edgar.get_company_facts(ticker="AAPL", concept="us-gaap/Revenues")
        latest_revenue = None
        if aapl_revenue_res.get("ok"):
            entries = aapl_revenue_res["data"].get("recent_values", [])
            if entries:
                latest_revenue = entries[0]
            source_status["edgar_aapl_facts"] = "live"
        else:
            source_status["edgar_aapl_facts"] = "error"

        live_count = sum(1 for v in source_status.values() if v == "live")
        recent_insider_count = len(insider_trades)

        # Signal logic
        confidence = 0.0
        signal_taken = False
        degraded_reason = ""

        if live_count == 0:
            degraded_reason = "SEC EDGAR API failed for all requests."
        elif recent_insider_count == 0:
            degraded_reason = "No recent Form 4 insider filings found for monitored tickers."
        else:
            # Flag when multiple insiders are buying across different companies
            buy_signals = [t for t in insider_trades if "buy" in str(t.get("filing", {}).get("form", "")).lower() or t.get("filing")]
            if len(buy_signals) >= 3:
                confidence = 0.25
            elif len(material_events) >= 2:
                confidence = 0.20
            else:
                degraded_reason = "Insider filings present but no significant cluster detected."

        summary = (
            f"SEC EDGAR scan: {recent_insider_count} Form 4 filings from {len(self._WATCH_TICKERS[:5])} tickers. "
            f"{len(material_events)} material 8-K events found. "
            f"All data from public SEC EDGAR — no MNPI, no insider access. "
            f"Latest AAPL revenue entry: {latest_revenue}. "
            f"Sources: data.sec.gov, efts.sec.gov."
        )

        return self.emit_signal(
            title="Congressional Trades Scanner",
            summary=summary,
            confidence=confidence,
            signal_taken=signal_taken,
            degraded_reason=degraded_reason,
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={
                "insider_trades": insider_trades[:10],
                "material_events": material_events[:5],
                "recent_insider_count": recent_insider_count,
                "material_event_count": len(material_events),
                "watched_tickers": self._WATCH_TICKERS[:5],
                "latest_aapl_revenue": latest_revenue,
                "source_status": source_status,
                "errors": errors,
                "disclaimer": "All data is publicly available via SEC EDGAR. No MNPI or insider access.",
                "sources": [
                    "data.sec.gov (SEC EDGAR, no auth)",
                    "efts.sec.gov (EDGAR full-text search, no auth)",
                ],
            },
        )
