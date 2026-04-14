from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class DeFiLlamaAdapter(BaseAdapter):
    """
    DeFiLlama API — completely free, no auth, no rate limits stated.
    Tracks TVL, yields, DEX volumes, stablecoin data across 2,400+ protocols.
    Best free DeFi intelligence source available.
    """

    platform_name = "defillama"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://api.llama.fi"

    _YIELDS_BASE = "https://yields.llama.fi"
    _COINS_BASE  = "https://coins.llama.fi"
    _STABLES_BASE = "https://stablecoins.llama.fi"

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        try:
            r = self._request("GET", "/protocols", timeout=10.0)
            data = r.json()
            return self._ok(
                data={"status": "ok", "protocol_count": len(data) if isinstance(data, list) else 0},
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("health_failed", str(exc), auth_truth="no_auth_required")

    # ── TVL ──────────────────────────────────────────────────────────────────

    def get_global_tvl(self) -> dict[str, Any]:
        """Total DeFi TVL in USD across all chains."""
        try:
            r = self._request("GET", "/v2/historicalChainTvl")
            raw = r.json()
            if isinstance(raw, list) and raw:
                latest = raw[-1]
                prior  = raw[-2] if len(raw) > 1 else None
                delta_pct = None
                if prior and prior.get("tvl") and prior["tvl"] != 0:
                    delta_pct = round((latest["tvl"] - prior["tvl"]) / prior["tvl"] * 100, 3)
                return self._ok(
                    data={
                        "total_tvl_usd": latest.get("tvl"),
                        "date": latest.get("date"),
                        "delta_24h_pct": delta_pct,
                        "history_30d": raw[-30:],
                    },
                    status="ok", auth_truth="no_auth_required",
                )
            return self._error("no_data", "Empty TVL response", auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("tvl_failed", str(exc), auth_truth="no_auth_required")

    def get_top_protocols(self, limit: int = 20) -> dict[str, Any]:
        """Top DeFi protocols by TVL."""
        try:
            r = self._request("GET", "/protocols")
            raw = r.json()
            if not isinstance(raw, list):
                return self._error("bad_response", "Unexpected format", auth_truth="no_auth_required")
            protocols = sorted(raw, key=lambda x: float(x.get("tvl") or 0), reverse=True)[:limit]
            result = [
                {
                    "name": p.get("name"),
                    "slug": p.get("slug"),
                    "category": p.get("category"),
                    "chain": p.get("chain"),
                    "tvl_usd": p.get("tvl"),
                    "change_1h_pct": p.get("change_1h"),
                    "change_1d_pct": p.get("change_1d"),
                    "change_7d_pct": p.get("change_7d"),
                    "mcap_tvl_ratio": p.get("mcap") / p.get("tvl") if p.get("mcap") and p.get("tvl") else None,
                    "token": p.get("symbol"),
                }
                for p in protocols
            ]
            return self._ok(data={"protocols": result, "count": len(result)}, status="ok", auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("protocols_failed", str(exc), auth_truth="no_auth_required")

    def get_chain_tvl(self, chain: str = "Ethereum") -> dict[str, Any]:
        """Historical TVL for a specific blockchain."""
        try:
            r = self._request("GET", f"/v2/historicalChainTvl/{chain}")
            raw = r.json()
            if isinstance(raw, list) and raw:
                latest = raw[-1]
                prior  = raw[-2] if len(raw) > 1 else None
                delta = None
                if prior and prior.get("tvl") and prior["tvl"] != 0:
                    delta = round((latest["tvl"] - prior["tvl"]) / prior["tvl"] * 100, 3)
                return self._ok(
                    data={
                        "chain": chain,
                        "tvl_usd": latest.get("tvl"),
                        "date": latest.get("date"),
                        "delta_24h_pct": delta,
                        "history_7d": raw[-7:],
                    },
                    status="ok", auth_truth="no_auth_required",
                )
            return self._error("no_data", "Empty chain TVL", auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("chain_tvl_failed", str(exc), auth_truth="no_auth_required")

    # ── YIELDS ───────────────────────────────────────────────────────────────

    def get_top_yields(self, min_tvl_usd: float = 1_000_000, limit: int = 20) -> dict[str, Any]:
        """Top yield-bearing pools across all DeFi protocols."""
        try:
            r = self._request("GET", "/pools", base_url=self._YIELDS_BASE)
            raw = r.json().get("data", [])
            # Filter: tvlUsd > min_tvl, stablecoins preferred for safety
            pools = [
                p for p in raw
                if float(p.get("tvlUsd") or 0) >= min_tvl_usd
                and float(p.get("apy") or 0) > 0
            ]
            pools.sort(key=lambda x: float(x.get("apy") or 0), reverse=True)
            top = pools[:limit]
            result = [
                {
                    "pool": p.get("pool"),
                    "project": p.get("project"),
                    "symbol": p.get("symbol"),
                    "chain": p.get("chain"),
                    "apy_pct": round(float(p.get("apy") or 0), 3),
                    "apy_base_pct": round(float(p.get("apyBase") or 0), 3),
                    "apy_reward_pct": round(float(p.get("apyReward") or 0), 3),
                    "tvl_usd": p.get("tvlUsd"),
                    "stablecoin": p.get("stablecoin", False),
                    "il_risk": p.get("ilRisk"),
                    "exposure": p.get("exposure"),
                    "predictions": p.get("predictions"),
                }
                for p in top
            ]
            # Stablecoin yields separately (safer)
            stable_yields = [x for x in result if x.get("stablecoin")]
            return self._ok(
                data={
                    "top_yields": result,
                    "stablecoin_yields": stable_yields[:5],
                    "count": len(result),
                    "max_apy": result[0]["apy_pct"] if result else 0,
                    "min_tvl_filter_usd": min_tvl_usd,
                },
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("yields_failed", str(exc), auth_truth="no_auth_required")

    def get_protocol_yields(self, protocol: str = "aave-v3") -> dict[str, Any]:
        """All yield pools for a specific protocol."""
        try:
            r = self._request("GET", "/pools", base_url=self._YIELDS_BASE)
            raw = r.json().get("data", [])
            pools = [p for p in raw if p.get("project", "").lower() == protocol.lower()]
            return self._ok(
                data={"protocol": protocol, "pools": pools[:20], "count": len(pools)},
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("protocol_yields_failed", str(exc), auth_truth="no_auth_required")

    # ── STABLECOINS ──────────────────────────────────────────────────────────

    def get_stablecoins(self) -> dict[str, Any]:
        """Stablecoin market caps and peg data."""
        try:
            r = self._request("GET", "/stablecoins", base_url=self._STABLES_BASE, params={"includePrices": "true"})
            raw = r.json().get("peggedAssets", [])
            stables = sorted(raw, key=lambda x: float(x.get("circulating", {}).get("peggedUSD", 0) or 0), reverse=True)
            result = [
                {
                    "name": s.get("name"),
                    "symbol": s.get("symbol"),
                    "peg_type": s.get("pegType"),
                    "peg_mechanism": s.get("pegMechanism"),
                    "circulating_usd": s.get("circulating", {}).get("peggedUSD"),
                    "price": s.get("price"),
                    "peg_deviation_pct": round(abs((s.get("price") or 1) - 1) * 100, 4) if s.get("price") else None,
                    "chains": list(s.get("chainCirculating", {}).keys()),
                }
                for s in stables[:15]
            ]
            # Flag depegged stablecoins
            depegged = [s for s in result if s.get("peg_deviation_pct") is not None and s["peg_deviation_pct"] > 0.5]
            return self._ok(
                data={
                    "stablecoins": result,
                    "depegged": depegged,
                    "depeg_alert": len(depegged) > 0,
                    "count": len(result),
                },
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("stablecoins_failed", str(exc), auth_truth="no_auth_required")

    # ── COINS / PRICES ────────────────────────────────────────────────────────

    def get_current_prices(self, coins: list[str] | None = None) -> dict[str, Any]:
        """
        Get current prices using DeFiLlama's coin ID format.
        coins: e.g. ['coingecko:bitcoin', 'coingecko:ethereum', 'ethereum:0x...']
        """
        default_coins = [
            "coingecko:bitcoin", "coingecko:ethereum", "coingecko:solana",
            "coingecko:chainlink", "coingecko:uniswap",
        ]
        targets = coins or default_coins
        try:
            r = self._request("GET", f"/prices/current/{','.join(targets)}", base_url=self._COINS_BASE)
            raw = r.json().get("coins", {})
            prices = {
                coin_id: {
                    "price": data.get("price"),
                    "symbol": data.get("symbol"),
                    "timestamp": data.get("timestamp"),
                    "confidence": data.get("confidence"),
                }
                for coin_id, data in raw.items()
            }
            return self._ok(data={"prices": prices, "count": len(prices)}, status="ok", auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("prices_failed", str(exc), auth_truth="no_auth_required")

    # ── DEX ───────────────────────────────────────────────────────────────────

    def get_dex_overview(self) -> dict[str, Any]:
        """DEX trading volumes and stats."""
        try:
            r = self._request("GET", "/overview/dexs", params={"excludeTotalDataChartBreakdown": "true"})
            raw = r.json()
            protocols = raw.get("protocols", [])[:15]
            total_24h = raw.get("total24h")
            total_7d = raw.get("total7d")
            result = [
                {
                    "name": p.get("name"),
                    "category": p.get("category"),
                    "chains": p.get("chains", []),
                    "volume_24h": p.get("total24h"),
                    "volume_7d": p.get("total7d"),
                    "change_24h_pct": p.get("change_1d"),
                }
                for p in protocols
            ]
            return self._ok(
                data={"protocols": result, "total_24h_volume": total_24h, "total_7d_volume": total_7d},
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("dex_failed", str(exc), auth_truth="no_auth_required")

    # ── SIGNAL ────────────────────────────────────────────────────────────────

    def get_market_signal(self) -> dict[str, Any]:
        """
        Composite DeFi health signal for prediction market inference.
        Combines TVL trends, stablecoin health, yield levels, and DEX activity.
        """
        signals: dict[str, Any] = {}
        errors: list[str] = []

        tvl_res = self.get_global_tvl()
        if tvl_res.get("ok"):
            tvl_delta = tvl_res["data"].get("delta_24h_pct") or 0
            signals["tvl_delta_24h"] = tvl_delta
            signals["tvl_trend"] = "expanding" if tvl_delta > 1 else "contracting" if tvl_delta < -1 else "stable"
        else:
            errors.append(f"tvl: {tvl_res.get('error')}")

        stables_res = self.get_stablecoins()
        if stables_res.get("ok"):
            signals["depeg_alert"] = stables_res["data"].get("depeg_alert", False)
            signals["depegged_count"] = len(stables_res["data"].get("depegged", []))
        else:
            errors.append(f"stables: {stables_res.get('error')}")

        yields_res = self.get_top_yields(min_tvl_usd=10_000_000, limit=5)
        if yields_res.get("ok"):
            signals["max_stable_apy"] = max(
                (p["apy_pct"] for p in yields_res["data"].get("stablecoin_yields", [])), default=0
            )
            signals["risk_free_rate_proxy"] = signals["max_stable_apy"]
        else:
            errors.append(f"yields: {yields_res.get('error')}")

        # Composite risk score
        risk_score = 0.0
        if signals.get("depeg_alert"):
            risk_score += 0.4
        if signals.get("tvl_trend") == "contracting":
            risk_score += 0.2
        if (signals.get("max_stable_apy") or 0) > 15:
            risk_score += 0.2  # unusually high yields = elevated risk

        signals["composite_risk_score"] = round(min(risk_score, 1.0), 3)
        signals["market_regime"] = (
            "high_risk" if risk_score > 0.5
            else "elevated_risk" if risk_score > 0.25
            else "normal"
        )
        signals["errors"] = errors

        return self._ok(data=signals, status="ok", auth_truth="no_auth_required")
