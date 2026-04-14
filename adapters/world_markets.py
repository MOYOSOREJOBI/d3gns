"""
World Markets adapter — global stock indices, commodities, bonds.
Sources: Yahoo Finance public (no auth), stooq.com (no auth).

Covers major indices:
  Americas: S&P 500, Dow, NASDAQ, TSX, Bovespa, IPC Mexico
  Europe:   FTSE 100, DAX, CAC 40, Euro Stoxx 50, Swiss SMI, IBEX 35, AEX
  Asia:     Nikkei 225, Hang Seng, Shanghai Composite, Kospi, Sensex, ASX 200
  Middle East: Tadawul (Saudi), ADX (UAE), TA-35 (Israel)
  Commodities: Gold, Silver, Oil (WTI, Brent), Natural Gas
  Bonds:    US 10Y, German Bund 10Y
"""
from __future__ import annotations

import time
from typing import Any

from adapters.base_adapter import BaseAdapter


# Symbol map: display_name → (stooq_symbol, yahoo_symbol, region, category)
INDEX_MAP: dict[str, dict[str, str]] = {
    # Americas
    "S&P 500":        {"stooq": "^spx",    "yahoo": "^GSPC",  "region": "Americas", "cat": "equity"},
    "Dow Jones":      {"stooq": "^dji",    "yahoo": "^DJI",   "region": "Americas", "cat": "equity"},
    "NASDAQ":         {"stooq": "^ndq",    "yahoo": "^IXIC",  "region": "Americas", "cat": "equity"},
    "TSX (Canada)":   {"stooq": "^tsx",    "yahoo": "^GSPTSE","region": "Americas", "cat": "equity"},
    "Bovespa (Brazil)": {"stooq": "^bvsp", "yahoo": "^BVSP",  "region": "Americas", "cat": "equity"},
    "IPC (Mexico)":   {"stooq": "^ipc",    "yahoo": "^MXX",   "region": "Americas", "cat": "equity"},
    # Europe
    "FTSE 100 (UK)":  {"stooq": "^ftse",   "yahoo": "^FTSE",  "region": "Europe",   "cat": "equity"},
    "DAX (Germany)":  {"stooq": "^dax",    "yahoo": "^GDAXI", "region": "Europe",   "cat": "equity"},
    "CAC 40 (France)": {"stooq": "^cac",   "yahoo": "^FCHI",  "region": "Europe",   "cat": "equity"},
    "Euro Stoxx 50":  {"stooq": "^stoxx50", "yahoo": "^STOXX50E", "region": "Europe", "cat": "equity"},
    "SMI (Switzerland)": {"stooq": "^smi", "yahoo": "^SSMI",  "region": "Europe",   "cat": "equity"},
    "IBEX 35 (Spain)": {"stooq": "^ibex",  "yahoo": "^IBEX",  "region": "Europe",   "cat": "equity"},
    "AEX (Netherlands)": {"stooq": "^aex", "yahoo": "^AEX",   "region": "Europe",   "cat": "equity"},
    # Asia
    "Nikkei 225 (Japan)": {"stooq": "^nkx","yahoo": "^N225",  "region": "Asia",     "cat": "equity"},
    "Hang Seng (HK)": {"stooq": "^hsi",    "yahoo": "^HSI",   "region": "Asia",     "cat": "equity"},
    "Shanghai (China)": {"stooq": "^shc",  "yahoo": "000001.SS","region": "Asia",   "cat": "equity"},
    "KOSPI (Korea)":  {"stooq": "^kospi",  "yahoo": "^KS11",  "region": "Asia",     "cat": "equity"},
    "Sensex (India)": {"stooq": "^bsesn",  "yahoo": "^BSESN", "region": "Asia",     "cat": "equity"},
    "ASX 200 (Australia)": {"stooq": "^asx", "yahoo": "^AXJO","region": "Asia",     "cat": "equity"},
    # Middle East
    "Tadawul (Saudi)": {"stooq": "^tasi",  "yahoo": "^TASI.SR","region": "Middle East", "cat": "equity"},
    "TA-35 (Israel)": {"stooq": "^ta125",  "yahoo": "^TA35.TA","region": "Middle East", "cat": "equity"},
    # Commodities
    "Gold":           {"stooq": "xauusd",  "yahoo": "GC=F",   "region": "Global",   "cat": "commodity"},
    "Silver":         {"stooq": "xagusd",  "yahoo": "SI=F",   "region": "Global",   "cat": "commodity"},
    "WTI Crude Oil":  {"stooq": "crude",   "yahoo": "CL=F",   "region": "Global",   "cat": "commodity"},
    "Brent Crude":    {"stooq": "brent",   "yahoo": "BZ=F",   "region": "Global",   "cat": "commodity"},
    "Natural Gas":    {"stooq": "natgas",  "yahoo": "NG=F",   "region": "Global",   "cat": "commodity"},
    # Bonds
    "US 10Y Treasury": {"stooq": "10usyb.b", "yahoo": "^TNX", "region": "Americas", "cat": "bond"},
    "German Bund 10Y": {"stooq": "10deynb.b", "yahoo": "^DE10YB.B", "region": "Europe", "cat": "bond"},
}


class WorldMarketsAdapter(BaseAdapter):
    platform_name = "world_markets"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://stooq.com/q/l"

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        res = self.get_quote_stooq("^spx")
        if res.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        return self._error("health_failed", "stooq unavailable", auth_truth="no_auth_required")

    def get_quote_stooq(self, symbol: str) -> dict[str, Any]:
        """
        Get latest quote from stooq.com (free, no auth, CSV response).
        Returns open, high, low, close, volume.
        """
        try:
            r = self._request("GET", "/",
                              params={"s": symbol, "f": "sd2t2ohlcv", "h": "&e=csv"},
                              timeout=8.0)
            text = r.text.strip()
            lines = [l for l in text.split("\n") if l.strip() and "Symbol" not in l]
            if not lines:
                return self._error("no_data", f"No data for {symbol}", auth_truth="no_auth_required")
            parts = lines[0].split(",")
            if len(parts) < 7:
                return self._error("parse_error", f"Bad CSV: {lines[0]}", auth_truth="no_auth_required")
            close_val = float(parts[6]) if parts[6] not in ("N/D", "") else None
            open_val  = float(parts[4]) if parts[4] not in ("N/D", "") else None
            high_val  = float(parts[5]) if parts[5] not in ("N/D", "") else None
            low_val   = float(parts[3]) if parts[3] not in ("N/D", "") else None
            vol_val   = float(parts[7]) if len(parts) > 7 and parts[7] not in ("N/D", "") else None
            change_pct = round((close_val - open_val) / open_val * 100, 3) if close_val and open_val and open_val != 0 else None
            return self._ok(data={
                "symbol": symbol,
                "close":  close_val,
                "open":   open_val,
                "high":   high_val,
                "low":    low_val,
                "volume": vol_val,
                "change_pct": change_pct,
                "date":   parts[1] if len(parts) > 1 else None,
                "source": "stooq.com",
            }, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("stooq_failed", str(exc), auth_truth="no_auth_required")

    def get_index(self, name: str) -> dict[str, Any]:
        """Get quote for a named index from INDEX_MAP."""
        meta = INDEX_MAP.get(name)
        if not meta:
            return self._error("unknown_index", f"{name} not in registry", auth_truth="no_auth_required")
        stooq_sym = meta.get("stooq", "")
        res = self.get_quote_stooq(stooq_sym)
        if res.get("ok"):
            res["data"]["name"]     = name
            res["data"]["region"]   = meta.get("region")
            res["data"]["category"] = meta.get("cat")
        return res

    def get_region(self, region: str) -> dict[str, Any]:
        """Get all indices for a specific region."""
        results = []
        errors  = []
        for name, meta in INDEX_MAP.items():
            if meta.get("region", "").lower() == region.lower():
                res = self.get_index(name)
                time.sleep(0.1)  # be gentle with stooq
                if res.get("ok"):
                    results.append(res["data"])
                else:
                    errors.append({"name": name, "error": res.get("error")})
        return self._ok(data={
            "region":  region,
            "indices": results,
            "count":   len(results),
            "errors":  errors,
        }, auth_truth="no_auth_required")

    def get_global_snapshot(self, max_per_region: int = 3) -> dict[str, Any]:
        """
        Fast multi-region snapshot — top indices per region.
        Returns a compact view suitable for a dashboard widget.
        """
        # Priority index per region for quick snapshot
        PRIORITY_INDICES = [
            "S&P 500", "Dow Jones", "NASDAQ",
            "FTSE 100 (UK)", "DAX (Germany)", "CAC 40 (France)",
            "Nikkei 225 (Japan)", "Hang Seng (HK)", "Sensex (India)",
            "Gold", "WTI Crude Oil",
            "Tadawul (Saudi)", "TA-35 (Israel)",
        ]
        results = []
        errors  = []
        for name in PRIORITY_INDICES[:12]:  # cap at 12 to avoid rate limiting
            res = self.get_index(name)
            time.sleep(0.08)
            if res.get("ok"):
                results.append(res["data"])
            else:
                errors.append({"name": name, "error": res.get("error")})

        # Group by region
        by_region: dict[str, list] = {}
        for r in results:
            reg = r.get("region", "Other")
            by_region.setdefault(reg, []).append(r)

        # Market mood: count up vs down
        up_count   = sum(1 for r in results if (r.get("change_pct") or 0) > 0)
        down_count = sum(1 for r in results if (r.get("change_pct") or 0) < 0)
        mood = "risk_on" if up_count > down_count * 1.5 else "risk_off" if down_count > up_count * 1.5 else "mixed"

        return self._ok(data={
            "snapshot":  results,
            "by_region": by_region,
            "count":     len(results),
            "up_count":  up_count,
            "down_count": down_count,
            "market_mood": mood,
            "errors":    errors,
            "source":    "stooq.com (free, no auth)",
        }, auth_truth="no_auth_required")

    def get_commodity_prices(self) -> dict[str, Any]:
        """Quick commodity snapshot: Gold, Silver, Oil, Gas."""
        commodities = ["Gold", "Silver", "WTI Crude Oil", "Brent Crude", "Natural Gas"]
        results = []
        for name in commodities:
            res = self.get_index(name)
            time.sleep(0.05)
            if res.get("ok"):
                results.append(res["data"])
        return self._ok(data={"commodities": results}, auth_truth="no_auth_required")
