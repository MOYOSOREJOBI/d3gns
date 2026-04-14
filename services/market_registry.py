from __future__ import annotations

from adapters.base_adapter import BaseAdapter, SettingsGetter, ProxyGetter
from adapters.betdaq_stub import BetdaqStubAdapter
from adapters.betfair_delayed import BetfairDelayedAdapter
from adapters.dexscreener import DexScreenerAdapter
from adapters.kalshi_demo import KalshiDemoAdapter
from adapters.kalshi_live import KalshiLiveAdapter
from adapters.kalshi_public import KalshiPublicAdapter
from adapters.matchbook_stub import MatchbookStubAdapter
from adapters.oddsapi import OddsApiAdapter
from adapters.predictit import PredictItAdapter
from adapters.polymarket_public import PolymarketPublicAdapter
from adapters.smarkets_stub import SmarketsStubAdapter
from adapters.sportsdataio_trial import SportsDataIoTrialAdapter
from services import quota_budgeter


class MarketRegistry:
    def __init__(self):
        self._factories: dict[str, object] = {}

    def register(self, platform: str, factory) -> None:
        self._factories[platform] = factory

    def get(self, platform: str) -> BaseAdapter:
        if platform not in self._factories:
            raise KeyError(platform)
        return self._factories[platform]()  # type: ignore[return-value]

    def list_platforms(self) -> list[str]:
        return sorted(self._factories.keys())

    def metadata_summary(self) -> list[dict]:
        rows = []
        for platform in self.list_platforms():
            adapter = self.get(platform)
            health = adapter.healthcheck()
            rows.append({
                "platform": platform,
                "mode": adapter.get_mode(),
                "configured": adapter.is_configured(),
                "truth_labels": health.get("truth_labels", {}),
                "status": health.get("status", "unknown"),
                "degraded_reason": health.get("degraded_reason", ""),
            })
        return rows

    def platform_truth_summary(self) -> dict[str, dict]:
        return {
            row["platform"]: {
                "mode": row["mode"],
                "status": row["status"],
                "truth_labels": row["truth_labels"],
                "degraded_reason": row["degraded_reason"],
            }
            for row in self.metadata_summary()
        }

    def platform_quota_summary(self) -> dict[str, dict]:
        budget = quota_budgeter.get_budget()
        return {
            platform: budget.status(platform)
            for platform in self.list_platforms()
        }

    def quota_summary(self) -> dict[str, dict]:
        return self.platform_quota_summary()

    def scheduling_metadata(self) -> dict[str, dict]:
        quota_summary = self.platform_quota_summary()
        return {
            platform: {
                "mode": self.get(platform).get_mode(),
                "quota": quota_summary.get(platform, {}),
            }
            for platform in self.list_platforms()
        }


def build_default_market_registry(
    settings_getter: SettingsGetter = None,
    proxy_getter: ProxyGetter = None,
) -> MarketRegistry:
    """
    Build and return the default MarketRegistry with all known adapters.

    Rules:
    - Every adapter must degrade safely when unconfigured.
    - Stubs register but never affect runtime behavior.
    - New adapters added here automatically appear in /api/platforms health.
    - Application startup must not fail if any of these adapters are unconfigured.
    """
    registry = MarketRegistry()

    def _make(cls):
        return lambda: cls(settings_getter=settings_getter, proxy_getter=proxy_getter)

    # ── Implemented adapters ─────────────────────────────────────────────────
    registry.register("kalshi_public",      _make(KalshiPublicAdapter))
    registry.register("kalshi_demo",        _make(KalshiDemoAdapter))
    registry.register("kalshi_live",        _make(KalshiLiveAdapter))
    registry.register("oddsapi",            _make(OddsApiAdapter))
    registry.register("betfair_delayed",    _make(BetfairDelayedAdapter))
    registry.register("sportsdataio_trial", _make(SportsDataIoTrialAdapter))
    registry.register("polymarket_public",  _make(PolymarketPublicAdapter))
    registry.register("dexscreener",        _make(DexScreenerAdapter))
    registry.register("predictit",          _make(PredictItAdapter))

    # ── Stub adapters (NOT CONFIGURED — registered for UI visibility) ────────
    registry.register("matchbook", _make(MatchbookStubAdapter))
    registry.register("betdaq",    _make(BetdaqStubAdapter))
    registry.register("smarkets",  _make(SmarketsStubAdapter))

    return registry
