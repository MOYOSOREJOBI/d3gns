from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class SECEdgarAdapter(BaseAdapter):
    """SEC EDGAR API — no auth, US public company filings, insider trades, 13F holdings."""

    platform_name = "sec_edgar"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://data.sec.gov"

    _HEADERS = {
        "User-Agent": "DeGens Research Bot research@degens.local",
        "Accept": "application/json",
    }

    # Well-known CIKs for major companies
    _KNOWN_CIKS = {
        "AAPL": "0000320193",
        "MSFT": "0000789019",
        "AMZN": "0001018724",
        "GOOGL": "0001652044",
        "META": "0001326801",
        "TSLA": "0001318605",
        "NVDA": "0001045810",
        "JPM": "0000019617",
        "GS": "0000886982",
        "BRK": "0001067983",
        "SPY": "0000884394",
    }

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        try:
            r = self._request("GET", "/submissions/CIK0000320193.json", headers=self._HEADERS)
            data = r.json()
            return self._ok(data={"status": "ok", "test_entity": data.get("name")}, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("health_failed", str(exc), auth_truth="no_auth_required")

    def get_company_filings(self, ticker: str = "AAPL", form_types: list[str] | None = None, limit: int = 20) -> dict[str, Any]:
        """Fetch recent SEC filings for a company by ticker."""
        cik = self._KNOWN_CIKS.get(ticker.upper())
        if not cik:
            # Try to pad to 10-digit CIK format if user provides a number
            return self._error("unknown_ticker", f"CIK for {ticker} not in presets. Add to _KNOWN_CIKS.", auth_truth="no_auth_required")
        cik_padded = cik.lstrip("0").zfill(10)
        try:
            r = self._request("GET", f"/submissions/CIK{cik_padded}.json", headers=self._HEADERS)
            raw = r.json()
            recent = raw.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            accessions = recent.get("accessionNumber", [])
            descriptions = recent.get("primaryDocument", [])
            filings = [
                {
                    "form": forms[i] if i < len(forms) else None,
                    "date": dates[i] if i < len(dates) else None,
                    "accession": accessions[i] if i < len(accessions) else None,
                    "document": descriptions[i] if i < len(descriptions) else None,
                }
                for i in range(min(len(forms), limit))
                if not form_types or forms[i] in form_types
            ]
            return self._ok(
                data={
                    "ticker": ticker.upper(),
                    "cik": cik,
                    "entity_name": raw.get("name"),
                    "sic": raw.get("sic"),
                    "category": raw.get("category"),
                    "filings": filings,
                    "count": len(filings),
                },
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("filings_failed", str(exc), auth_truth="no_auth_required")

    def get_insider_trades(self, ticker: str = "AAPL", limit: int = 20) -> dict[str, Any]:
        """Fetch Form 4 (insider transactions) for a company."""
        return self.get_company_filings(ticker=ticker, form_types=["4", "4/A"], limit=limit)

    def get_13f_holdings(self, ticker: str = "BRK", limit: int = 5) -> dict[str, Any]:
        """Fetch 13-F quarterly holdings reports (large institutional investors)."""
        return self.get_company_filings(ticker=ticker, form_types=["13F-HR", "13F-HR/A"], limit=limit)

    def get_company_facts(self, ticker: str = "AAPL", concept: str = "us-gaap/Revenues") -> dict[str, Any]:
        """Fetch a specific XBRL fact (e.g. revenues, assets) for fundamental analysis."""
        cik = self._KNOWN_CIKS.get(ticker.upper())
        if not cik:
            return self._error("unknown_ticker", f"CIK for {ticker} not in presets.", auth_truth="no_auth_required")
        cik_padded = cik.lstrip("0").zfill(10)
        taxonomy, tag = concept.split("/", 1) if "/" in concept else ("us-gaap", concept)
        try:
            r = self._request(
                "GET", f"/api/xbrl/companyfacts/CIK{cik_padded}.json",
                headers=self._HEADERS,
            )
            raw = r.json()
            facts = (
                raw.get("facts", {})
                .get(taxonomy, {})
                .get(tag, {})
                .get("units", {})
            )
            # Get USD entries if available
            entries = facts.get("USD", facts.get("shares", []))
            recent_entries = sorted(entries, key=lambda x: x.get("end", ""), reverse=True)[:10] if entries else []
            return self._ok(
                data={
                    "ticker": ticker.upper(),
                    "concept": concept,
                    "recent_values": recent_entries,
                },
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("facts_failed", str(exc), auth_truth="no_auth_required")

    def get_full_text_search(self, query: str, form_type: str = "8-K", date_range: str = "custom", start_dt: str = "2024-01-01", end_dt: str = "2025-12-31") -> dict[str, Any]:
        """Search EDGAR full text for keywords in filings (EFTS endpoint)."""
        try:
            r = self._request(
                "GET", "/efts/",
                base_url="https://efts.sec.gov",
                params={
                    "q": f'"{query}"',
                    "dateRange": date_range,
                    "startdt": start_dt,
                    "enddt": end_dt,
                    "forms": form_type,
                },
                headers=self._HEADERS,
            )
            raw = r.json()
            hits = raw.get("hits", {}).get("hits", [])
            results = [
                {
                    "form": h.get("_source", {}).get("form_type"),
                    "company": h.get("_source", {}).get("display_names"),
                    "filed": h.get("_source", {}).get("file_date"),
                    "accession": h.get("_id"),
                }
                for h in hits[:10]
            ]
            return self._ok(
                data={"query": query, "form_type": form_type, "results": results, "total": raw.get("hits", {}).get("total", {}).get("value")},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("fulltext_failed", str(exc), auth_truth="no_auth_required")
