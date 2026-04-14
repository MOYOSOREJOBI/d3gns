from __future__ import annotations

"""Bot catalog — single source of truth for all registered bots.

Each bot entry has a bot_class field: 'lab' or 'mall'.
- lab: trading, betting, prediction markets, investing, hedging, crossvenue, research signals
- mall: non-trading, non-gambling utility bots — lead gen, content, services, e-commerce ops
"""

# ── LAB BOTS ─────────────────────────────────────────────────────────────────
from bots.betfair_delayed_mirror import BetfairDelayedMirrorBot
from bots.crossvenue_arb_watchlist import CrossvenueArbWatchlistBot
from bots.funding_rate_arb import FundingRateArbBot
from bots.grid_trader import GridTraderBot
from bots.kalshi_demo_execution import KalshiDemoExecutionBot
from bots.kalshi_orderbook_imbalance import KalshiOrderbookImbalanceBot
from bots.kalshi_pair_spread import KalshiPairSpreadBot
from bots.kalshi_resolution_decay import KalshiResolutionDecayBot
from bots.oddsapi_clv_tracker import OddsApiClvTrackerBot
from bots.oddsapi_consensus_outlier import OddsApiConsensusOutlierBot
from bots.oddsapi_stale_line import OddsApiStaleLineBot
from bots.poly_kalshi_crossvenue import PolyKalshiCrossvenueBot
from bots.polymarket_microstructure import PolymarketMicrostructureBot
from bots.sportsdataio_line_movement import SportsDataIoLineMovementBot
from bots.pmxt_cross_market_scanner import PmxtCrossMarketScannerBot
from bots.poly_adaptive_trend_bot import PolyAdaptiveTrendBot
from bots.poly_copy_trader_bot import PolyCopyTraderBot
from bots.rl_market_bot import RlMarketBot
from bots.weather_forecast_bot import WeatherForecastDislocationBot
from bots.oil_inventory_shock_bot import OilInventoryShockBot
from bots.soccer_consensus_latency_bot import SoccerConsensusLatencyBot
from bots.f1_odds_latency_bot import F1OddsLatencyBot
from bots.politics_crossvenue_bot import PoliticsCrossVenueBot
from bots.gold_funding_basis_bot import GoldFundingBasisBot
from bots.kalshi_macro_shock_sniper_bot import KalshiMacroShockSniperBot
from bots.sp500_momentum_tracker_bot import Sp500MomentumTrackerBot
from bots.gold_price_momentum_bot import GoldPriceMomentumBot
# New LAB bots
from bots.news_sentiment_bot import NewsSentimentBot
from bots.earnings_surprise_bot import EarningsSurpriseBot
from bots.congress_trades_bot import CongressTradesBot
from bots.insider_filing_bot import InsiderFilingBot
from bots.crypto_momentum_bot import CryptoMomentumBot
from bots.defi_yield_arb_bot import DefiYieldArbBot
from bots.social_sentiment_bot import SocialSentimentBot
from bots.macro_indicator_bot import MacroIndicatorBot
from bots.geopolitical_risk_bot import GeopoliticalRiskBot
from bots.currency_vol_bot import CurrencyVolBot
from bots.sports_momentum_bot import SportsMomentumBot
from bots.crypto_funding_rate_bot import CryptoFundingRateBot
from bots.tech_signal_bot import TechSignalBot
from bots.environmental_event_bot import EnvironmentalEventBot
from bots.prediction_consensus_bot import PredictionConsensusBot
from bots.volatility_regime_bot import VolatilityRegimeBot

# ── MALL BOTS ─────────────────────────────────────────────────────────────────
from bots.grant_rfp_scanner_bot import GrantRFPScannerBot
from bots.freelance_lead_scout_bot import FreelanceLeadScoutBot
from bots.scholarship_hackathon_bot import ScholarshipHackathonBot
from bots.bounty_opportunity_bot import BountyOpportunityBot
from bots.deal_content_opportunity_bot import DealContentOpportunityBot
# New MALL bots
from bots.local_biz_website_bot import LocalBizWebsiteBot
from bots.google_biz_profile_bot import GoogleBizProfileBot
from bots.booking_funnel_bot import BookingFunnelBot
from bots.ai_intake_bot import AIIntakeBot
from bots.shopify_ops_bot import ShopifyOpsBot
from bots.etsy_pod_bot import EtsyPODBot
from bots.ebay_flip_bot import EbayFlipBot
from bots.digital_downloads_bot import DigitalDownloadsBot
from bots.content_pipeline_bot import ContentPipelineBot
from bots.lead_enrichment_bot import LeadEnrichmentBot
from bots.property_maps_bot import PropertyMapsBot
from bots.newsletter_bot import NewsletterBot
from bots.affiliate_content_bot import AffiliateContentBot
from bots.youtube_content_bot import YouTubeContentBot
from bots.linkedin_outreach_bot import LinkedInOutreachBot
from bots.seo_audit_bot import SeoAuditBot
from bots.podcast_content_bot import PodcastContentBot
from bots.wordpress_maintenance_bot import WordpressMaintenanceBot
from bots.social_scheduler_bot import SocialSchedulerBot
from bots.job_board_scanner_bot import JobBoardScannerBot
from bots.hackernews_lead_bot import HackerNewsLeadBot
from bots.producthunt_tracker_bot import ProductHuntTrackerBot
from bots.app_store_opt_bot import AppStoreOptBot
from bots.chatbot_builder_bot import ChatbotBuilderBot
from bots.email_automation_bot import EmailAutomationBot
from services.home_content import enrich_catalog_entry


# ── Ordered lists by class ─────────────────────────────────────────────────────

ALL_LAB_BOTS: list[type] = [
    KalshiOrderbookImbalanceBot,
    KalshiResolutionDecayBot,
    KalshiPairSpreadBot,
    KalshiDemoExecutionBot,
    OddsApiConsensusOutlierBot,
    OddsApiStaleLineBot,
    OddsApiClvTrackerBot,
    PolyKalshiCrossvenueBot,
    PolymarketMicrostructureBot,
    BetfairDelayedMirrorBot,
    SportsDataIoLineMovementBot,
    CrossvenueArbWatchlistBot,
    FundingRateArbBot,
    GridTraderBot,
    PmxtCrossMarketScannerBot,
    PolyAdaptiveTrendBot,
    PolyCopyTraderBot,
    RlMarketBot,
    WeatherForecastDislocationBot,
    OilInventoryShockBot,
    SoccerConsensusLatencyBot,
    F1OddsLatencyBot,
    PoliticsCrossVenueBot,
    GoldFundingBasisBot,
    KalshiMacroShockSniperBot,
    Sp500MomentumTrackerBot,
    GoldPriceMomentumBot,
    PredictionConsensusBot,
    MacroIndicatorBot,
    NewsSentimentBot,
    CryptoMomentumBot,
    CryptoFundingRateBot,
    GeopoliticalRiskBot,
    EarningsSurpriseBot,
    CongressTradesBot,
    InsiderFilingBot,
    DefiYieldArbBot,
    SocialSentimentBot,
    CurrencyVolBot,
    SportsMomentumBot,
    TechSignalBot,
    EnvironmentalEventBot,
    VolatilityRegimeBot,
]

ALL_MALL_BOTS: list[type] = [
    GrantRFPScannerBot,
    FreelanceLeadScoutBot,
    ScholarshipHackathonBot,
    BountyOpportunityBot,
    DealContentOpportunityBot,
    LocalBizWebsiteBot,
    GoogleBizProfileBot,
    BookingFunnelBot,
    AIIntakeBot,
    ShopifyOpsBot,
    EtsyPODBot,
    EbayFlipBot,
    DigitalDownloadsBot,
    ContentPipelineBot,
    LeadEnrichmentBot,
    PropertyMapsBot,
    NewsletterBot,
    AffiliateContentBot,
    YouTubeContentBot,
    LinkedInOutreachBot,
    SeoAuditBot,
    PodcastContentBot,
    WordpressMaintenanceBot,
    SocialSchedulerBot,
    JobBoardScannerBot,
    HackerNewsLeadBot,
    ProductHuntTrackerBot,
    AppStoreOptBot,
    ChatbotBuilderBot,
    EmailAutomationBot,
]

PRIMARY_LAB_BOT_IDS: list[str] = [
    "bot_kalshi_macro_shock_sniper",
    "bot_poly_kalshi_crossvenue_spread",
    "bot_polymarket_microstructure_paper",
    "bot_oddsapi_stale_line_scanner",
    "bot_crossvenue_arb_watchlist",
    "bot_kalshi_orderbook_imbalance_paper",
    "bot_kalshi_resolution_decay_paper",
    "bot_kalshi_pair_spread_paper",
    "bot_kalshi_demo_execution",
    "bot_funding_rate_arb_paper",
    "bot_grid_trader_paper",
    "bot_soccer_consensus_latency",
    "bot_volatility_regime",
    "bot_oddsapi_clv_tracker",
    "bot_oddsapi_consensus_outlier_paper",
    "bot_sportsdataio_line_movement_research",
    "bot_politics_crossvenue",
    "bot_weather_forecast_dislocation",
    "bot_oil_inventory_shock",
    "bot_f1_odds_latency",
    "bot_crypto_momentum",
    "bot_crypto_funding_rate",
    "bot_gold_funding_basis",
    "bot_gold_price_momentum",
    "bot_sp500_momentum",
    "bot_prediction_consensus",
    "bot_macro_indicator",
    "bot_news_sentiment",
    "bot_earnings_surprise",
    "bot_pmxt_cross_market_scanner",
]

PRIMARY_MALL_BOT_IDS: list[str] = [
    "bot_local_biz_website",
    "bot_google_biz_profile",
    "bot_chatbot_builder",
    "bot_ai_intake",
    "bot_booking_funnel",
    "bot_seo_audit",
    "bot_freelance_lead_scout",
    "bot_digital_downloads",
    "bot_content_pipeline",
    "bot_linkedin_outreach",
    "bot_grant_rfp_scanner",
    "bot_deal_content_opportunity",
    "bot_lead_enrichment",
    "bot_newsletter",
    "bot_affiliate_content",
    "bot_youtube_content",
    "bot_wordpress_maintenance",
    "bot_email_automation",
    "bot_shopify_ops",
    "bot_property_maps",
]


def _ordered_bot_classes(bot_ids: list[str], classes: list[type]) -> list[type]:
    lookup = {getattr(cls, "bot_id", ""): cls for cls in classes}
    missing = [bot_id for bot_id in bot_ids if bot_id not in lookup]
    if missing:
        raise KeyError(f"Catalog bot ids are missing implementations: {', '.join(missing)}")
    return [lookup[bot_id] for bot_id in bot_ids]


LAB_BOTS: list[type] = _ordered_bot_classes(PRIMARY_LAB_BOT_IDS, ALL_LAB_BOTS)
MALL_BOTS: list[type] = _ordered_bot_classes(PRIMARY_MALL_BOT_IDS, ALL_MALL_BOTS)
CATALOG_BOTS: list[type] = LAB_BOTS + MALL_BOTS


# ── Factory registry for instantiating live / runtime bots ────────────────────

IMPLEMENTED_BOT_FACTORIES: dict[str, object] = {
    # Original 18 with adapters
    "bot_kalshi_orderbook_imbalance_paper": (lambda r: KalshiOrderbookImbalanceBot(r.get("kalshi_public"))),
    "bot_kalshi_resolution_decay_paper": (lambda r: KalshiResolutionDecayBot(r.get("kalshi_public"))),
    "bot_kalshi_pair_spread_paper": (lambda r: KalshiPairSpreadBot(r.get("kalshi_public"))),
    "bot_oddsapi_consensus_outlier_paper": (lambda r: OddsApiConsensusOutlierBot(r.get("oddsapi"))),
    "bot_kalshi_demo_execution": (lambda r: KalshiDemoExecutionBot(r.get("kalshi_demo"))),
    "bot_oddsapi_stale_line_scanner": (lambda r: OddsApiStaleLineBot(r.get("oddsapi"))),
    "bot_oddsapi_clv_tracker": (lambda r: OddsApiClvTrackerBot(r.get("oddsapi"))),
    "bot_poly_kalshi_crossvenue_spread": (lambda r: PolyKalshiCrossvenueBot(poly_adapter=r.get("polymarket_public"), kalshi_adapter=r.get("kalshi_public"))),
    "bot_polymarket_microstructure_paper": (lambda r: PolymarketMicrostructureBot(r.get("polymarket_public"))),
    "bot_betfair_delayed_mirror": (lambda r: BetfairDelayedMirrorBot(r.get("betfair_delayed"))),
    "bot_sportsdataio_line_movement_research": (lambda r: SportsDataIoLineMovementBot(r.get("sportsdataio_trial"))),
    "bot_crossvenue_arb_watchlist": (lambda r: CrossvenueArbWatchlistBot(kalshi_adapter=r.get("kalshi_public"), poly_adapter=r.get("polymarket_public"), oddsapi_adapter=r.get("oddsapi"))),
    "bot_funding_rate_arb_paper": (lambda r: FundingRateArbBot()),
    "bot_grid_trader_paper": (lambda r: GridTraderBot()),
    "bot_pmxt_cross_market_scanner": (lambda r: PmxtCrossMarketScannerBot()),
    "bot_poly_adaptive_trend_paper": (lambda r: PolyAdaptiveTrendBot()),
    "bot_poly_copy_trader_paper": (lambda r: PolyCopyTraderBot()),
    "bot_rl_market_paper": (lambda r: RlMarketBot()),
    # Market context / data bots
    "bot_weather_forecast_dislocation": (lambda r: WeatherForecastDislocationBot()),
    "bot_oil_inventory_shock": (lambda r: OilInventoryShockBot()),
    "bot_soccer_consensus_latency": (lambda r: SoccerConsensusLatencyBot(r.get("oddsapi"))),
    "bot_f1_odds_latency": (lambda r: F1OddsLatencyBot(r.get("oddsapi"))),
    "bot_politics_crossvenue": (lambda r: PoliticsCrossVenueBot(poly_adapter=r.get("polymarket_public"), kalshi_adapter=r.get("kalshi_public"))),
    "bot_gold_funding_basis": (lambda r: GoldFundingBasisBot()),
    "bot_kalshi_macro_shock_sniper": (lambda r: KalshiMacroShockSniperBot()),
    "bot_sp500_momentum": (lambda r: Sp500MomentumTrackerBot()),
    "bot_gold_price_momentum": (lambda r: GoldPriceMomentumBot()),
    # New LAB research bots
    "bot_news_sentiment": (lambda r: NewsSentimentBot()),
    "bot_earnings_surprise": (lambda r: EarningsSurpriseBot()),
    "bot_congress_trades": (lambda r: CongressTradesBot()),
    "bot_insider_filing": (lambda r: InsiderFilingBot()),
    "bot_crypto_momentum": (lambda r: CryptoMomentumBot()),
    "bot_defi_yield_arb": (lambda r: DefiYieldArbBot()),
    "bot_social_sentiment": (lambda r: SocialSentimentBot()),
    "bot_macro_indicator": (lambda r: MacroIndicatorBot()),
    "bot_geopolitical_risk": (lambda r: GeopoliticalRiskBot()),
    "bot_currency_vol": (lambda r: CurrencyVolBot()),
    "bot_sports_momentum": (lambda r: SportsMomentumBot()),
    "bot_crypto_funding_rate": (lambda r: CryptoFundingRateBot()),
    "bot_tech_signal": (lambda r: TechSignalBot()),
    "bot_environmental_event": (lambda r: EnvironmentalEventBot()),
    "bot_prediction_consensus": (lambda r: PredictionConsensusBot()),
    "bot_volatility_regime": (lambda r: VolatilityRegimeBot()),
    # MALL bots
    "bot_grant_rfp_scanner": (lambda r: GrantRFPScannerBot()),
    "bot_freelance_lead_scout": (lambda r: FreelanceLeadScoutBot()),
    "bot_scholarship_hackathon": (lambda r: ScholarshipHackathonBot()),
    "bot_bounty_opportunity": (lambda r: BountyOpportunityBot()),
    "bot_deal_content_opportunity": (lambda r: DealContentOpportunityBot()),
    "bot_local_biz_website": (lambda r: LocalBizWebsiteBot()),
    "bot_google_biz_profile": (lambda r: GoogleBizProfileBot()),
    "bot_booking_funnel": (lambda r: BookingFunnelBot()),
    "bot_ai_intake": (lambda r: AIIntakeBot()),
    "bot_shopify_ops": (lambda r: ShopifyOpsBot()),
    "bot_etsy_pod": (lambda r: EtsyPODBot()),
    "bot_ebay_flip": (lambda r: EbayFlipBot()),
    "bot_digital_downloads": (lambda r: DigitalDownloadsBot()),
    "bot_content_pipeline": (lambda r: ContentPipelineBot()),
    "bot_lead_enrichment": (lambda r: LeadEnrichmentBot()),
    "bot_property_maps": (lambda r: PropertyMapsBot()),
    "bot_newsletter": (lambda r: NewsletterBot()),
    "bot_affiliate_content": (lambda r: AffiliateContentBot()),
    "bot_youtube_content": (lambda r: YouTubeContentBot()),
    "bot_linkedin_outreach": (lambda r: LinkedInOutreachBot()),
    "bot_seo_audit": (lambda r: SeoAuditBot()),
    "bot_podcast_content": (lambda r: PodcastContentBot()),
    "bot_wordpress_maintenance": (lambda r: WordpressMaintenanceBot()),
    "bot_social_scheduler": (lambda r: SocialSchedulerBot()),
    "bot_job_board_scanner": (lambda r: JobBoardScannerBot()),
    "bot_hackernews_lead": (lambda r: HackerNewsLeadBot()),
    "bot_producthunt_tracker": (lambda r: ProductHuntTrackerBot()),
    "bot_app_store_opt": (lambda r: AppStoreOptBot()),
    "bot_chatbot_builder": (lambda r: ChatbotBuilderBot()),
    "bot_email_automation": (lambda r: EmailAutomationBot()),
}


def _instantiate_for_metadata(bot_cls: type):
    try:
        return bot_cls(None)
    except TypeError:
        return bot_cls()


_BOT_PALETTE = [
    "#5ea1ff",  # blue
    "#59d47a",  # green
    "#ef5f57",  # red
    "#d6af41",  # gold
    "#a78bfa",  # violet
    "#f97316",  # orange
    "#22d3ee",  # cyan
    "#f472b6",  # pink
    "#34d399",  # emerald
    "#fb923c",  # amber
    "#818cf8",  # indigo
    "#4ade80",  # lime green
    "#e879f9",  # fuchsia
    "#38bdf8",  # sky
    "#facc15",  # yellow
    "#f43f5e",  # rose
]


def _truth_label_for_runtime_mode(runtime_mode: str) -> str:
    normalized = str(runtime_mode or "research").strip().lower()
    return {
        "research": "PUBLIC-DATA-ONLY",
        "replay": "REPLAY - HISTORICAL DATA",
        "paper": "PAPER - SYNTHETIC",
        "shadow": "SHADOW - REAL DATA, NO MONEY",
        "demo": "DEMO - EXCHANGE SANDBOX",
        "live-disabled": "LIVE-DISABLED - SENDING BLOCKED",
        "live": "LIVE - REAL CAPITAL",
    }.get(normalized, "PUBLIC-DATA-ONLY")


def _lab_spec(
    tier: str,
    runtime_mode: str,
    risk_family: str,
    correlation_group: str,
    ui_group: str,
    *,
    required_env: tuple[str, ...] = (),
    status: str = "enabled",
    capital_eligible: bool = True,
    supports_replay: bool = True,
    supports_shadow: bool = True,
    supports_demo: bool = False,
    supports_live: bool = False,
) -> dict:
    return {
        "tier": tier,
        "runtime_mode": runtime_mode,
        "status": status,
        "risk_family": risk_family,
        "correlation_group": correlation_group,
        "ui_group": ui_group,
        "capital_eligible": capital_eligible,
        "required_env": list(required_env),
        "supports_replay": supports_replay,
        "supports_shadow": supports_shadow,
        "supports_demo": supports_demo,
        "supports_live": supports_live,
        "truth_label": _truth_label_for_runtime_mode(runtime_mode),
    }


def _mall_spec(
    tier: str,
    lane: str,
    ui_group: str,
    *,
    runtime_mode: str = "research",
    status: str = "enabled",
    required_env: tuple[str, ...] = (),
) -> dict:
    return {
        "tier": tier,
        "runtime_mode": runtime_mode,
        "status": status,
        "risk_family": lane,
        "correlation_group": lane,
        "ui_group": ui_group,
        "lane": lane,
        "capital_eligible": False,
        "required_env": list(required_env),
        "supports_replay": False,
        "supports_shadow": False,
        "supports_demo": False,
        "supports_live": False,
        "truth_label": _truth_label_for_runtime_mode(runtime_mode),
    }


_LAB_REGISTRY_ROWS = [
    ("bot_kalshi_macro_shock_sniper", "T1", "paper", "macro_event", "macro_us_release", "lab_t1", ("KALSHI_API_KEY",), True, True, True),
    ("bot_poly_kalshi_crossvenue_spread", "T1", "paper", "crossvenue_arb", "prediction_crossvenue", "lab_t1", ("KALSHI_API_KEY", "POLYMARKET_*"), True, False, True),
    ("bot_polymarket_microstructure_paper", "T1", "paper", "microstructure", "polymarket_microstructure", "lab_t1", ("POLYMARKET_*",), True, False, True),
    ("bot_oddsapi_stale_line_scanner", "T1", "paper", "stale_line", "sports_consensus", "lab_t1", ("ODDS_API_KEY",), False, False, False),
    ("bot_crossvenue_arb_watchlist", "T1", "research", "crossvenue_watchlist", "prediction_crossvenue", "lab_t1", ("KALSHI_API_KEY", "POLYMARKET_*"), False, False, False),
    ("bot_kalshi_orderbook_imbalance_paper", "T1", "paper", "orderbook_imbalance", "kalshi_microstructure", "lab_t1", ("KALSHI_API_KEY",), True, True, True),
    ("bot_kalshi_resolution_decay_paper", "T1", "paper", "theta_decay", "kalshi_event_decay", "lab_t1", ("KALSHI_API_KEY",), True, True, True),
    ("bot_kalshi_pair_spread_paper", "T1", "paper", "pair_spread", "kalshi_pair_relationship", "lab_t1", ("KALSHI_API_KEY",), True, True, True),
    ("bot_kalshi_demo_execution", "T1", "demo", "execution_certification", "kalshi_execution", "lab_t1", ("KALSHI_API_KEY",), False, True, False),
    ("bot_funding_rate_arb_paper", "T2", "paper", "funding_carry", "crypto_funding", "lab_t2", ("BINANCE_API_KEY",), True, False, True),
    ("bot_grid_trader_paper", "T2", "paper", "grid_regime", "crypto_range", "lab_t2", ("BINANCE_API_KEY",), True, False, True),
    ("bot_soccer_consensus_latency", "T2", "research", "sports_latency", "soccer_consensus", "lab_t2", ("ODDS_API_KEY",), False, False, False),
    ("bot_volatility_regime", "T2", "research", "regime_control", "global_regime", "lab_t2", (), False, False, False),
    ("bot_oddsapi_clv_tracker", "T2", "research", "clv_tracking", "sports_consensus", "lab_t2", ("ODDS_API_KEY",), False, False, False),
    ("bot_oddsapi_consensus_outlier_paper", "T2", "paper", "consensus_outlier", "sports_consensus", "lab_t2", ("ODDS_API_KEY",), False, False, False),
    ("bot_sportsdataio_line_movement_research", "T2", "research", "line_movement", "sports_consensus", "lab_t2", ("SPORTSDATAIO_API_KEY",), False, False, False),
    ("bot_politics_crossvenue", "T3", "research", "crossvenue_politics", "politics_crossvenue", "lab_t3", ("KALSHI_API_KEY", "POLYMARKET_*"), False, False, False),
    ("bot_weather_forecast_dislocation", "T3", "research", "weather_dislocation", "weather_event", "lab_t3", (), False, False, False),
    ("bot_oil_inventory_shock", "T3", "research", "commodity_event", "macro_energy", "lab_t3", (), False, False, False),
    ("bot_f1_odds_latency", "T3", "research", "sports_latency", "f1_incident", "lab_t3", ("ODDS_API_KEY",), False, False, False),
    ("bot_crypto_momentum", "T3", "paper", "momentum", "crypto_directional", "lab_t3", ("BINANCE_API_KEY",), True, False, False),
    ("bot_crypto_funding_rate", "T3", "research", "funding_signal", "crypto_funding", "lab_t3", (), False, False, False),
    ("bot_gold_funding_basis", "T4", "paper", "basis_carry", "macro_metals", "lab_t4", ("BINANCE_API_KEY",), True, False, True),
    ("bot_gold_price_momentum", "T4", "research", "macro_momentum", "macro_metals", "lab_t4", (), False, False, False),
    ("bot_sp500_momentum", "T4", "research", "macro_momentum", "macro_equities", "lab_t4", (), False, False, False),
    ("bot_prediction_consensus", "T4", "research", "consensus_scanner", "prediction_crossvenue", "lab_t4", (), False, False, False),
    ("bot_macro_indicator", "T4", "research", "macro_indicator", "macro_us_release", "lab_t4", (), False, False, False),
    ("bot_news_sentiment", "T5", "research", "news_sentiment", "macro_headlines", "lab_t5", (), False, False, False),
    ("bot_earnings_surprise", "T5", "research", "earnings_signal", "equity_earnings", "lab_t5", (), False, False, False),
    ("bot_pmxt_cross_market_scanner", "T5", "research", "cross_market_scanner", "prediction_crossvenue", "lab_t5", (), False, False, False),
]

_MALL_REGISTRY_ROWS = [
    ("bot_local_biz_website", "M1", "service", "mall_service"),
    ("bot_google_biz_profile", "M1", "service", "mall_service"),
    ("bot_chatbot_builder", "M1", "service", "mall_service"),
    ("bot_ai_intake", "M1", "lead_gen", "mall_leads"),
    ("bot_booking_funnel", "M1", "service", "mall_service"),
    ("bot_seo_audit", "M1", "service", "mall_service"),
    ("bot_freelance_lead_scout", "M1", "freelancing", "mall_freelance"),
    ("bot_digital_downloads", "M2", "ecommerce", "mall_ecommerce"),
    ("bot_content_pipeline", "M1", "content", "mall_content"),
    ("bot_linkedin_outreach", "M1", "lead_gen", "mall_leads"),
    ("bot_grant_rfp_scanner", "M2", "lead_gen", "mall_leads"),
    ("bot_deal_content_opportunity", "M2", "content", "mall_content"),
    ("bot_lead_enrichment", "M2", "lead_gen", "mall_leads"),
    ("bot_newsletter", "M2", "content", "mall_content"),
    ("bot_affiliate_content", "M2", "content", "mall_content"),
    ("bot_youtube_content", "M2", "content", "mall_content"),
    ("bot_wordpress_maintenance", "M2", "service", "mall_service"),
    ("bot_email_automation", "M2", "lead_gen", "mall_leads"),
    ("bot_shopify_ops", "M2", "ecommerce", "mall_ecommerce"),
    ("bot_property_maps", "M2", "lead_gen", "mall_leads"),
]

BOT_REGISTRY_OVERRIDES = {
    **{
        bot_id: _lab_spec(
            tier,
            runtime_mode,
            risk_family,
            correlation_group,
            ui_group,
            required_env=required_env,
            capital_eligible=capital_eligible,
            supports_demo=supports_demo,
            supports_live=supports_live,
        )
        for bot_id, tier, runtime_mode, risk_family, correlation_group, ui_group, required_env, capital_eligible, supports_demo, supports_live in _LAB_REGISTRY_ROWS
    },
    **{
        bot_id: _mall_spec(tier, lane, ui_group)
        for bot_id, tier, lane, ui_group in _MALL_REGISTRY_ROWS
    },
}


def build_catalog() -> list[dict]:
    """Returns the canonical 30 LAB + 20 MALL registry used by the backend."""
    result = []
    lab_ids = set(PRIMARY_LAB_BOT_IDS)
    lab_rank = 0
    mall_rank = 0
    for i, cls in enumerate(CATALOG_BOTS):
        meta = _instantiate_for_metadata(cls).metadata()
        bot_id = meta["bot_id"]
        is_lab = bot_id in lab_ids
        override = BOT_REGISTRY_OVERRIDES.get(bot_id, {})
        runtime_mode = str(override.get("runtime_mode", str(meta.get("mode", "research")).lower())).strip().lower()
        meta["bot_class"] = "lab" if is_lab else "mall"
        meta["mode"] = runtime_mode.upper().replace("-", "_")
        meta["runtime_mode"] = runtime_mode
        meta["truth_label"] = override.get("truth_label", _truth_label_for_runtime_mode(runtime_mode))
        meta["tier"] = override.get("tier", "T5" if is_lab else "M2")
        meta["status"] = override.get("status", "enabled")
        meta["risk_family"] = override.get("risk_family", meta.get("signal_type", "unknown"))
        meta["correlation_group"] = override.get("correlation_group", meta.get("platform", "unknown"))
        meta["ui_group"] = override.get("ui_group", "lab_t5" if is_lab else "mall_service")
        meta["capital_eligible"] = bool(override.get("capital_eligible", is_lab))
        meta["required_env"] = list(override.get("required_env", []))
        meta["supports_replay"] = bool(override.get("supports_replay", False))
        meta["supports_shadow"] = bool(override.get("supports_shadow", False))
        meta["supports_demo"] = bool(override.get("supports_demo", False))
        meta["supports_live"] = bool(override.get("supports_live", False))
        if override.get("lane"):
            meta["lane"] = override["lane"]
        meta["registry_version"] = "canonical_30_lab_20_mall_v1"
        meta["catalog_order"] = i + 1
        if not meta.get("color"):
            meta["color"] = _BOT_PALETTE[i % len(_BOT_PALETTE)]
        meta["home_enabled"] = True
        meta["home_section"] = "lab" if is_lab else "mall"
        if is_lab:
            lab_rank += 1
            meta["home_rank"] = lab_rank
        else:
            mall_rank += 1
            meta["home_rank"] = mall_rank
        result.append(enrich_catalog_entry(meta))
    return result


def load_catalog_registry(entries: list[dict] | None = None) -> dict[str, object]:
    ordered = list(entries or build_catalog())
    lab_bots = [entry for entry in ordered if entry.get("bot_class") == "lab"]
    mall_bots = [entry for entry in ordered if entry.get("bot_class") == "mall"]
    return {
        "registry_version": "canonical_30_lab_20_mall_v1",
        "ordered_bots": ordered,
        "lab_bots": lab_bots,
        "mall_bots": mall_bots,
        "counts": {"lab": len(lab_bots), "mall": len(mall_bots), "total": len(ordered)},
        "tiers": {
            "lab": sorted({entry.get("tier", "") for entry in lab_bots if entry.get("tier")}),
            "mall": sorted({entry.get("tier", "") for entry in mall_bots if entry.get("tier")}),
        },
        "runtime_modes": sorted({str(entry.get("runtime_mode", "")).lower() for entry in ordered if entry.get("runtime_mode")}),
    }


def instantiate_bot(bot_id: str, registry):
    factory = IMPLEMENTED_BOT_FACTORIES.get(bot_id)
    if factory:
        return factory(registry)

    for bot_cls in CATALOG_BOTS:
        probe = _instantiate_for_metadata(bot_cls)
        if probe.bot_id == bot_id:
            return probe

    raise KeyError(bot_id)
