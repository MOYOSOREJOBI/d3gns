"""
Forex & Multi-Currency Rate Adapter — NO authentication required.

Sources:
  1. Frankfurter (ECB rates, free, no auth) — https://api.frankfurter.app
  2. Exchange Rate API public endpoint (free, no auth) — https://open.er-api.com/v6/latest
  3. Fallback: CoinGecko VS currencies (for crypto)

Covers all major fiat currencies including:
  USD, CAD, GBP, NGN (Naira), BRL, JMD, ZAR, GHS, CNY, EUR,
  SAR, AED, ILS, JPY, RUB, INR, KRW, MXN, HKD, SGD, CHF, AUD,
  and all major crypto vs USD.
"""
from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


# All currencies we care about
ALL_FIAT_CURRENCIES = [
    "USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "HKD", "SGD",
    "NOK", "SEK", "DKK", "NZD", "MXN", "BRL", "ZAR", "INR", "KRW", "TRY",
    "NGN", "GHS", "JMD", "ZAR", "SAR", "AED", "ILS", "RUB", "IRR", "PKR",
    "PHP", "THB", "IDR", "MYR", "CZK", "PLN", "HUF",
]

# Currency display metadata
CURRENCY_META: dict[str, dict[str, str]] = {
    "USD": {"name": "US Dollar",          "flag": "🇺🇸", "region": "Americas"},
    "EUR": {"name": "Euro",               "flag": "🇪🇺", "region": "Europe"},
    "GBP": {"name": "British Pound",      "flag": "🇬🇧", "region": "Europe"},
    "JPY": {"name": "Japanese Yen",       "flag": "🇯🇵", "region": "Asia"},
    "CAD": {"name": "Canadian Dollar",    "flag": "🇨🇦", "region": "Americas"},
    "AUD": {"name": "Australian Dollar",  "flag": "🇦🇺", "region": "Oceania"},
    "CHF": {"name": "Swiss Franc",        "flag": "🇨🇭", "region": "Europe"},
    "CNY": {"name": "Chinese Yuan",       "flag": "🇨🇳", "region": "Asia"},
    "HKD": {"name": "Hong Kong Dollar",   "flag": "🇭🇰", "region": "Asia"},
    "SGD": {"name": "Singapore Dollar",   "flag": "🇸🇬", "region": "Asia"},
    "SAR": {"name": "Saudi Riyal",        "flag": "🇸🇦", "region": "Middle East"},
    "AED": {"name": "UAE Dirham",         "flag": "🇦🇪", "region": "Middle East"},
    "ILS": {"name": "Israeli Shekel",     "flag": "🇮🇱", "region": "Middle East"},
    "NGN": {"name": "Nigerian Naira",     "flag": "🇳🇬", "region": "Africa"},
    "GHS": {"name": "Ghanaian Cedi",      "flag": "🇬🇭", "region": "Africa"},
    "ZAR": {"name": "South African Rand", "flag": "🇿🇦", "region": "Africa"},
    "BRL": {"name": "Brazilian Real",     "flag": "🇧🇷", "region": "Americas"},
    "MXN": {"name": "Mexican Peso",       "flag": "🇲🇽", "region": "Americas"},
    "JMD": {"name": "Jamaican Dollar",    "flag": "🇯🇲", "region": "Americas"},
    "RUB": {"name": "Russian Ruble",      "flag": "🇷🇺", "region": "Europe/Asia"},
    "IRR": {"name": "Iranian Rial",       "flag": "🇮🇷", "region": "Middle East"},
    "INR": {"name": "Indian Rupee",       "flag": "🇮🇳", "region": "Asia"},
    "KRW": {"name": "South Korean Won",   "flag": "🇰🇷", "region": "Asia"},
    "PKR": {"name": "Pakistani Rupee",    "flag": "🇵🇰", "region": "Asia"},
    "IDR": {"name": "Indonesian Rupiah",  "flag": "🇮🇩", "region": "Asia"},
    "TRY": {"name": "Turkish Lira",       "flag": "🇹🇷", "region": "Europe/Asia"},
    "NZD": {"name": "New Zealand Dollar", "flag": "🇳🇿", "region": "Oceania"},
}


class ForexRatesAdapter(BaseAdapter):
    platform_name = "forex_rates"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://open.er-api.com/v6"   # primary (ExchangeRate-API free)
    _FRANKFURTER = "https://api.frankfurter.app"
    _FAWAZ_AHMED = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1"

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        res = self.get_rates("USD")
        if res.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        return self._error("health_failed", res.get("error", "unknown"), auth_truth="no_auth_required")

    def get_rates(self, base: str = "USD") -> dict[str, Any]:
        """
        Get all exchange rates from a base currency.
        Uses ExchangeRate-API free endpoint (primary) then Frankfurter (fallback).
        """
        # Primary: open.er-api.com
        try:
            r = self._request("GET", f"/latest/{base.upper()}", timeout=8.0)
            d = r.json()
            if d.get("result") == "success":
                rates = d.get("rates", {})
                return self._ok(data={
                    "base":        base.upper(),
                    "rates":       rates,
                    "source":      "open.er-api.com",
                    "last_update": d.get("time_last_update_utc"),
                }, auth_truth="no_auth_required")
        except Exception:
            pass  # fall through to fallback

        # Fallback: frankfurter.app (ECB rates — may not have exotic pairs)
        try:
            r = self._request("GET", "/latest",
                              base_url=self._FRANKFURTER,
                              params={"from": base.upper()},
                              timeout=8.0)
            d = r.json()
            rates = d.get("rates", {})
            return self._ok(data={
                "base":        base.upper(),
                "rates":       rates,
                "source":      "frankfurter.app (ECB)",
                "last_update": d.get("date"),
                "note":        "ECB rates — exotic pairs (NGN, JMD, IRR) may be missing",
            }, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("rates_failed", str(exc), auth_truth="no_auth_required")

    def convert(self, amount: float, from_ccy: str, to_ccy: str) -> dict[str, Any]:
        """Convert amount from one currency to another."""
        res = self.get_rates(from_ccy)
        if not res.get("ok"):
            return res
        rates = res["data"].get("rates", {})
        rate = rates.get(to_ccy.upper())
        if rate is None:
            return self._error("pair_not_found",
                               f"{from_ccy}/{to_ccy} pair not available in this source",
                               auth_truth="no_auth_required")
        converted = round(amount * rate, 6)
        return self._ok(data={
            "from":        from_ccy.upper(),
            "to":          to_ccy.upper(),
            "amount":      amount,
            "rate":        rate,
            "converted":   converted,
            "source":      res["data"].get("source"),
        }, auth_truth="no_auth_required")

    def get_key_pairs(self) -> dict[str, Any]:
        """
        Get rates for the full set of currencies the user cares about.
        Returns a structured dict with region groupings.
        """
        res = self.get_rates("USD")
        if not res.get("ok"):
            return res

        rates = res["data"].get("rates", {})
        source = res["data"].get("source")

        # Build enriched currency list
        enriched: list[dict[str, Any]] = []
        for ccy in ALL_FIAT_CURRENCIES:
            if ccy == "USD":
                rate = 1.0
            else:
                rate = rates.get(ccy)
            meta = CURRENCY_META.get(ccy, {})
            enriched.append({
                "code":       ccy,
                "name":       meta.get("name", ccy),
                "flag":       meta.get("flag", ""),
                "region":     meta.get("region", "Other"),
                "rate_vs_usd": rate,  # how many units of this ccy = $1 USD
                "usd_per_unit": round(1.0 / rate, 8) if rate else None,
                "available":  rate is not None,
            })

        # Group by region
        by_region: dict[str, list] = {}
        for c in enriched:
            r = c["region"]
            by_region.setdefault(r, []).append(c)

        available_count = sum(1 for c in enriched if c["available"])

        return self._ok(data={
            "base":            "USD",
            "currencies":      enriched,
            "by_region":       by_region,
            "available_count": available_count,
            "total_count":     len(enriched),
            "source":          source,
            "last_update":     res["data"].get("last_update"),
        }, auth_truth="no_auth_required")

    def get_crypto_vs_fiat(self, crypto_symbol: str = "bitcoin") -> dict[str, Any]:
        """
        Get a cryptocurrency price in multiple fiat currencies via CoinGecko.
        crypto_symbol: 'bitcoin', 'ethereum', etc.
        """
        fiat_list = "usd,eur,gbp,jpy,cad,aud,cny,inr,brl,zar,ngn,krw,mxn,chf,sgd,hkd,nzd,aed,sar,ils,try"
        try:
            r = self._request("GET", "/simple/price",
                              base_url="https://api.coingecko.com/api/v3",
                              params={"ids": crypto_symbol, "vs_currencies": fiat_list},
                              timeout=10.0)
            d = r.json()
            prices = d.get(crypto_symbol, {})
            enriched = {}
            for ccy, price in prices.items():
                meta = CURRENCY_META.get(ccy.upper(), {})
                enriched[ccy.upper()] = {
                    "price":  price,
                    "name":   meta.get("name", ccy.upper()),
                    "flag":   meta.get("flag", ""),
                    "region": meta.get("region", ""),
                }
            return self._ok(data={
                "crypto":    crypto_symbol,
                "prices":    enriched,
                "count":     len(enriched),
                "source":    "coingecko.com",
            }, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("crypto_fiat_failed", str(exc), auth_truth="no_auth_required")

    def get_full_currency_board(self, include_crypto: bool = True) -> dict[str, Any]:
        """
        Complete currency board: all fiat rates + BTC/ETH/SOL in all fiats.
        This is the 'currency checker' view for the dashboard.
        """
        fiat_res = self.get_key_pairs()
        board: dict[str, Any] = {
            "fiat": fiat_res.get("data", {}),
            "crypto": {},
            "errors": [],
        }

        if include_crypto:
            for coin in ["bitcoin", "ethereum", "solana"]:
                c_res = self.get_crypto_vs_fiat(coin)
                if c_res.get("ok"):
                    board["crypto"][coin] = c_res["data"]
                else:
                    board["errors"].append(f"{coin}: {c_res.get('error')}")

        return self._ok(data=board, auth_truth="no_auth_required")
