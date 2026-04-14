from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STRATS_DIR = ROOT / "docs" / "STRATS"

QUANT_MANUAL_PATH = STRATS_DIR / "degens_quant_system_manual (1).md"
OPS_MANUAL_PATH = STRATS_DIR / "DEGENS_OPERATIONS_MANUAL (3).md"
REALITY_MANUAL_PATH = STRATS_DIR / "compass_artifact_wf-a88f197f-9722-4ea3-8580-572a585b76e7_text_markdown (1).md"


LAB_HOME_STACK = [
    {"bot_id": "bot_kalshi_macro_shock_sniper", "prototype_id": "L01", "prototype_label": "Kalshi Macro Shock Sniper", "tier": "T1", "market": "Kalshi"},
    {"bot_id": "bot_poly_kalshi_crossvenue_spread", "prototype_id": "L02", "prototype_label": "Poly↔Kalshi Spread Compressor", "tier": "T1", "market": "Poly + Kalshi"},
    {"bot_id": "bot_polymarket_microstructure_paper", "prototype_id": "L03", "prototype_label": "Polymarket Microstructure Burst", "tier": "T1", "market": "Polymarket"},
    {"bot_id": "bot_oddsapi_stale_line_scanner", "prototype_id": "L04", "prototype_label": "OddsAPI Stale Line Sniper", "tier": "T1", "market": "OddsAPI"},
    {"bot_id": "bot_prediction_consensus", "prototype_id": "L05", "prototype_label": "Cross-Venue Consensus Scanner", "tier": "T1", "market": "Multi-venue"},
    {"bot_id": "bot_politics_crossvenue", "prototype_id": "L06", "prototype_label": "Politics Cross-Venue Spread", "tier": "T1", "market": "Poly + Kalshi"},
    {"bot_id": "bot_funding_rate_arb_paper", "prototype_id": "L07", "prototype_label": "Funding Rate Carry Engine", "tier": "T2", "market": "Binance"},
    {"bot_id": "bot_gold_funding_basis", "prototype_id": "L08", "prototype_label": "Gold Funding Basis Bot", "tier": "T2", "market": "Gold carry"},
    {"bot_id": "bot_grid_trader_paper", "prototype_id": "L09", "prototype_label": "Grid Trader BTC/ETH", "tier": "T2", "market": "Crypto"},
    {"bot_id": "bot_crypto_momentum", "prototype_id": "L10", "prototype_label": "Crypto Momentum Signal", "tier": "T2", "market": "Crypto lead-lag"},
    {"bot_id": "bot_defi_yield_arb", "prototype_id": "L11", "prototype_label": "DeFi Yield Anomaly Scanner", "tier": "T2", "market": "DeFi"},
    {"bot_id": "bot_poly_adaptive_trend_paper", "prototype_id": "L12", "prototype_label": "Poly Adaptive Trend (15-min)", "tier": "T2", "market": "Polymarket"},
    {"bot_id": "bot_soccer_consensus_latency", "prototype_id": "L13", "prototype_label": "Soccer Consensus Latency", "tier": "T3", "market": "OddsAPI"},
    {"bot_id": "bot_f1_odds_latency", "prototype_id": "L14", "prototype_label": "F1 Odds Latency Bot", "tier": "T3", "market": "OddsAPI + OpenF1"},
    {"bot_id": "bot_weather_forecast_dislocation", "prototype_id": "L15", "prototype_label": "Weather Catalyst Bot", "tier": "T3", "market": "Weather / prediction"},
    {"bot_id": "bot_oil_inventory_shock", "prototype_id": "L16", "prototype_label": "Oil Inventory Shock Bot", "tier": "T3", "market": "EIA / energy"},
    {"bot_id": "bot_earnings_surprise", "prototype_id": "L17", "prototype_label": "Earnings Surprise Scanner", "tier": "T3", "market": "Earnings"},
    {"bot_id": "bot_macro_indicator", "prototype_id": "L18", "prototype_label": "Macro Indicator FRED Bot", "tier": "T3", "market": "FRED / macro"},
    {"bot_id": "bot_currency_vol", "prototype_id": "L19", "prototype_label": "Forex Volatility Scanner", "tier": "T4", "market": "FX"},
    {"bot_id": "bot_sp500_momentum", "prototype_id": "L20", "prototype_label": "S&P 500 Momentum Tracker", "tier": "T4", "market": "Equities"},
    {"bot_id": "bot_gold_price_momentum", "prototype_id": "L21", "prototype_label": "Gold Price Momentum", "tier": "T4", "market": "Commodities"},
    {"bot_id": "bot_volatility_regime", "prototype_id": "L22", "prototype_label": "Volatility Regime Detector", "tier": "T4", "market": "Portfolio context"},
    {"bot_id": "bot_geopolitical_risk", "prototype_id": "L23", "prototype_label": "Geopolitical Risk Monitor", "tier": "T4", "market": "Event context"},
    {"bot_id": "bot_congress_trades", "prototype_id": "L24", "prototype_label": "Congressional Trades Signal", "tier": "T4", "market": "Filings"},
    {"bot_id": "bot_oddsapi_clv_tracker", "prototype_id": "L25", "prototype_label": "CLV Drift Tracker", "tier": "T5", "market": "Sports quality"},
    {"bot_id": "bot_kalshi_orderbook_imbalance_paper", "prototype_id": "L26", "prototype_label": "Kalshi Orderbook Imbalance", "tier": "T5", "market": "Kalshi"},
    {"bot_id": "bot_kalshi_resolution_decay_paper", "prototype_id": "L27", "prototype_label": "Kalshi Resolution Decay", "tier": "T5", "market": "Kalshi"},
    {"bot_id": "bot_kalshi_pair_spread_paper", "prototype_id": "L28", "prototype_label": "Kalshi Pair Spread", "tier": "T5", "market": "Kalshi"},
    {"bot_id": "bot_sports_momentum", "prototype_id": "L29", "prototype_label": "Sports Momentum Tracker", "tier": "T5", "market": "Sports"},
    {"bot_id": "bot_kalshi_demo_execution", "prototype_id": "L30", "prototype_label": "Kalshi Demo Execution Bot", "tier": "T5", "market": "Kalshi demo"},
]

MALL_HOME_STACK = [
    {"bot_id": "bot_youtube_content", "prototype_id": "M01", "prototype_label": "Faceless YouTube Factory", "lane": "Content", "prob_100_week": "15%", "prob_500_two_months": "25%", "prob_1000_three_months": "40%", "startup_cost": "$5/mo (ElevenLabs)"},
    {"bot_id": "bot_social_scheduler", "prototype_id": "M02", "prototype_label": "TikTok/IG Reels Mass Poster", "lane": "Content", "prob_100_week": "20%", "prob_500_two_months": "30%", "prob_1000_three_months": "35%", "startup_cost": "$0 (Blotato free tier)"},
    {"bot_id": "bot_affiliate_content", "prototype_id": "M03", "prototype_label": "AI Instagram Avatar Bot", "lane": "Content", "prob_100_week": "5%", "prob_500_two_months": "10%", "prob_1000_three_months": "20%", "startup_cost": "$0"},
    {"bot_id": "bot_podcast_content", "prototype_id": "M04", "prototype_label": "YouTube Kids Content Factory", "lane": "Content", "prob_100_week": "10%", "prob_500_two_months": "20%", "prob_1000_three_months": "45%", "startup_cost": "$5/mo"},
    {"bot_id": "bot_newsletter", "prototype_id": "M05", "prototype_label": "Twitter/X Bot Army", "lane": "Content", "prob_100_week": "5%", "prob_500_two_months": "10%", "prob_1000_three_months": "15%", "startup_cost": "$0"},
    {"bot_id": "bot_ai_intake", "prototype_id": "M06", "prototype_label": "AI Receptionist Bot Sales", "lane": "Service", "prob_100_week": "30%", "prob_500_two_months": "50%", "prob_1000_three_months": "65%", "startup_cost": "$20/mo (Voiceflow)"},
    {"bot_id": "bot_local_biz_website", "prototype_id": "M07", "prototype_label": "Local Biz Website Rescue", "lane": "Service", "prob_100_week": "25%", "prob_500_two_months": "40%", "prob_1000_three_months": "55%", "startup_cost": "$10 (domain)"},
    {"bot_id": "bot_seo_audit", "prototype_id": "M08", "prototype_label": "SEO Audit Lead Generator", "lane": "Service", "prob_100_week": "20%", "prob_500_two_months": "35%", "prob_1000_three_months": "50%", "startup_cost": "$0"},
    {"bot_id": "bot_google_biz_profile", "prototype_id": "M09", "prototype_label": "Google Biz Profile Fixer", "lane": "Service", "prob_100_week": "15%", "prob_500_two_months": "30%", "prob_1000_three_months": "45%", "startup_cost": "$0"},
    {"bot_id": "bot_email_automation", "prototype_id": "M10", "prototype_label": "Email Automation Setup Service", "lane": "Service", "prob_100_week": "15%", "prob_500_two_months": "25%", "prob_1000_three_months": "40%", "startup_cost": "$0"},
    {"bot_id": "bot_digital_downloads", "prototype_id": "M11", "prototype_label": "Etsy Digital Art & Posters", "lane": "E-Commerce", "prob_100_week": "25%", "prob_500_two_months": "40%", "prob_1000_three_months": "55%", "startup_cost": "$4 (listings)"},
    {"bot_id": "bot_etsy_pod", "prototype_id": "M12", "prototype_label": "Print-on-Demand Empire", "lane": "E-Commerce", "prob_100_week": "15%", "prob_500_two_months": "25%", "prob_1000_three_months": "40%", "startup_cost": "$0 (Printify free)"},
    {"bot_id": "bot_ebay_flip", "prototype_id": "M13", "prototype_label": "Amazon/AliExpress Reseller", "lane": "E-Commerce", "prob_100_week": "10%", "prob_500_two_months": "20%", "prob_1000_three_months": "35%", "startup_cost": "$39/mo (Shopify)"},
    {"bot_id": "bot_shopify_ops", "prototype_id": "M14", "prototype_label": "Digital Downloads Store", "lane": "E-Commerce", "prob_100_week": "20%", "prob_500_two_months": "35%", "prob_1000_three_months": "50%", "startup_cost": "$0 (Gumroad)"},
    {"bot_id": "bot_booking_funnel", "prototype_id": "M15", "prototype_label": "Seasonal Products Dropship", "lane": "E-Commerce", "prob_100_week": "10%", "prob_500_two_months": "15%", "prob_1000_three_months": "25%", "startup_cost": "$39/mo"},
    {"bot_id": "bot_freelance_lead_scout", "prototype_id": "M16", "prototype_label": "Fiverr/Upwork Bot Agent", "lane": "Freelancing", "prob_100_week": "35%", "prob_500_two_months": "50%", "prob_1000_three_months": "60%", "startup_cost": "$0"},
    {"bot_id": "bot_property_maps", "prototype_id": "M17", "prototype_label": "Real Estate Lead Sourcer", "lane": "Research", "prob_100_week": "5%", "prob_500_two_months": "10%", "prob_1000_three_months": "20%", "startup_cost": "$0"},
    {"bot_id": "bot_lead_enrichment", "prototype_id": "M18", "prototype_label": "Construction/Service Middleman", "lane": "Growth", "prob_100_week": "10%", "prob_500_two_months": "20%", "prob_1000_three_months": "35%", "startup_cost": "$50 (ads)"},
    {"bot_id": "bot_grant_rfp_scanner", "prototype_id": "M19", "prototype_label": "Grant & Scholarship Scanner", "lane": "Lead Gen", "prob_100_week": "5%", "prob_500_two_months": "5%", "prob_1000_three_months": "10%", "startup_cost": "$0"},
    {"bot_id": "bot_job_board_scanner", "prototype_id": "M20", "prototype_label": "Data Entry & Proofreading Agent", "lane": "Freelancing", "prob_100_week": "25%", "prob_500_two_months": "30%", "prob_1000_three_months": "35%", "startup_cost": "$0"},
    {"bot_id": "bot_content_pipeline", "prototype_id": "M21", "prototype_label": "Content Pipeline Operator", "lane": "Content"},
    {"bot_id": "bot_chatbot_builder", "prototype_id": "M22", "prototype_label": "AI Chatbot Builder", "lane": "Service"},
    {"bot_id": "bot_linkedin_outreach", "prototype_id": "M23", "prototype_label": "LinkedIn Outreach Engine", "lane": "Growth"},
    {"bot_id": "bot_hackernews_lead", "prototype_id": "M24", "prototype_label": "Hacker News Lead Tracker", "lane": "Lead Gen"},
    {"bot_id": "bot_producthunt_tracker", "prototype_id": "M25", "prototype_label": "Product Hunt Tracker", "lane": "Research"},
    {"bot_id": "bot_app_store_opt", "prototype_id": "M26", "prototype_label": "App Store Optimizer", "lane": "Growth"},
    {"bot_id": "bot_wordpress_maintenance", "prototype_id": "M27", "prototype_label": "WordPress Maintenance Bot", "lane": "Service"},
    {"bot_id": "bot_bounty_opportunity", "prototype_id": "M28", "prototype_label": "Bounty Opportunity Hunter", "lane": "Lead Gen"},
    {"bot_id": "bot_deal_content_opportunity", "prototype_id": "M29", "prototype_label": "Deal Content Opportunity Bot", "lane": "Content"},
    {"bot_id": "bot_scholarship_hackathon", "prototype_id": "M30", "prototype_label": "Scholarship Hackathon Scout", "lane": "Lead Gen"},
]

FORCEFIELD_SUMMARY = {
    "floor_pct": 0.80,
    "withdraw_trigger_pct": 1.20,
    "withdrawals_per_cycle": 5,
    "phases": [
        {"name": "FLOOR", "drawdown": "≥10%", "bet_pct": "0.1%"},
        {"name": "ULTRA_SAFE", "drawdown": "7-10%", "bet_pct": "0.2%"},
        {"name": "SAFE", "drawdown": "5-7%", "bet_pct": "0.5%"},
        {"name": "CAREFUL", "drawdown": "3-5%", "bet_pct": "0.8%"},
        {"name": "NORMAL", "drawdown": "<3%", "bet_pct": "1.5%"},
        {"name": "AGGRESSIVE", "drawdown": "0% + streak≥2", "bet_pct": "3%"},
        {"name": "TURBO", "drawdown": "0% + streak≥3", "bet_pct": "4%"},
    ],
}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _clip_words(text: str, max_words: int = 64) -> str:
    words = re.split(r"\s+", str(text or "").strip())
    words = [word for word in words if word]
    if len(words) <= max_words:
        return " ".join(words)
    return f"{' '.join(words[:max_words])}…"


def _clean_markdown(text: str) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"`+", "", cleaned)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
    cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _first_paragraph(text: str) -> str:
    for paragraph in re.split(r"\n\s*\n", str(text or "")):
        line = _clean_markdown(paragraph)
        if line:
            return line
    return ""


def _extract_section(markdown: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(markdown)
    return match.group("body").strip() if match else ""


def _extract_heading_block(markdown: str, heading: str) -> str:
    pattern = re.compile(
        rf"^#{{1,6}}\s+{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^#{{1,6}}\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(markdown)
    return match.group("body").strip() if match else ""


def _extract_first_table(markdown: str, heading: str) -> list[str]:
    block = _extract_heading_block(markdown, heading)
    if not block:
        return []
    lines = [line.rstrip() for line in block.splitlines()]
    table_lines: list[str] = []
    in_table = False
    for line in lines:
        if line.lstrip().startswith("|"):
            table_lines.append(line)
            in_table = True
            continue
        if in_table:
            break
    return table_lines


def _parse_markdown_table(lines: list[str]) -> list[dict[str, str]]:
    if len(lines) < 2:
        return []
    headers = [_clean_markdown(cell).strip() for cell in lines[0].strip().strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[2:]:
        if not line.lstrip().startswith("|"):
            break
        cells = [_clean_markdown(cell).strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < len(headers):
            continue
        rows.append({headers[index]: cells[index] for index in range(len(headers))})
    return rows


@lru_cache(maxsize=1)
def _quant_text() -> str:
    return _read_text(QUANT_MANUAL_PATH)


@lru_cache(maxsize=1)
def _ops_text() -> str:
    return _read_text(OPS_MANUAL_PATH)


@lru_cache(maxsize=1)
def _reality_text() -> str:
    return _read_text(REALITY_MANUAL_PATH)


@lru_cache(maxsize=1)
def build_manual_library() -> list[dict[str, Any]]:
    quant_intro = _first_paragraph(_extract_section(_quant_text(), "What this manual is"))
    ops_summary = _first_paragraph(_extract_section(_ops_text(), "1. EXECUTIVE SUMMARY")) or _first_paragraph(_ops_text())
    reality_summary = _first_paragraph(_reality_text())
    return [
        {
            "id": "quant_manual",
            "title": "Quant System Manual",
            "subtitle": "LAB architecture, ForceField, and per-bot logic",
            "path": str(QUANT_MANUAL_PATH.relative_to(ROOT)),
            "excerpt": _clip_words(quant_intro, 58),
        },
        {
            "id": "operations_manual",
            "title": "Operations Manual",
            "subtitle": "45-day sprint, capital split, and ForceField operating rules",
            "path": str(OPS_MANUAL_PATH.relative_to(ROOT)),
            "excerpt": _clip_words(ops_summary, 58),
        },
        {
            "id": "reality_check",
            "title": "Reality Check",
            "subtitle": "Probability, capital constraints, and viable sequencing",
            "path": str(REALITY_MANUAL_PATH.relative_to(ROOT)),
            "excerpt": _clip_words(reality_summary, 58),
        },
    ]


@lru_cache(maxsize=1)
def build_lab_highlights() -> list[dict[str, Any]]:
    quant = _quant_text()
    highlights = []
    for item in (LAB_HOME_STACK[0], LAB_HOME_STACK[1], LAB_HOME_STACK[6], LAB_HOME_STACK[21], LAB_HOME_STACK[29]):
        section = _extract_section(quant, f"{item['prototype_id']} — {item['prototype_label']}")
        excerpt = _clip_words(_first_paragraph(section), 54)
        highlights.append(
            {
                "bot_id": item["bot_id"],
                "prototype_id": item["prototype_id"],
                "title": item["prototype_label"],
                "tier": item["tier"],
                "excerpt": excerpt,
            }
        )
    return highlights


@lru_cache(maxsize=1)
def build_lab_probability_map() -> dict[str, dict[str, str]]:
    rows = _parse_markdown_table(_extract_first_table(_ops_text(), "Probability of Each Bot Achieving Return Targets"))
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        bot_id = row.get("Bot ID", "").strip()
        if not bot_id:
            continue
        result[bot_id] = {
            "prob_20_week": row.get("20% in 1 week", ""),
            "prob_20_month": row.get("20% in 1 month", ""),
            "prob_300_month": row.get("300% in 1 month", ""),
            "prob_1000_month": row.get("1000% in 1 month", ""),
        }
    return result


@lru_cache(maxsize=1)
def build_mall_probability_map() -> dict[str, dict[str, str]]:
    rows = _parse_markdown_table(_extract_first_table(_ops_text(), "Probability of Each Mall Bot Reaching Revenue Targets"))
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        label = row.get("Bot", "").strip()
        prototype_id = label.split(" ", 1)[0].strip()
        if not prototype_id:
            continue
        result[prototype_id] = {
            "prob_100_week": row.get("$100/week in 1 month", ""),
            "prob_500_two_months": row.get("$500/week in 2 months", ""),
            "prob_1000_three_months": row.get("$1000/week in 3 months", ""),
            "startup_cost": row.get("Startup Cost", ""),
        }
    return result


@lru_cache(maxsize=1)
def get_catalog_home_overrides() -> dict[str, dict[str, Any]]:
    quant = _quant_text()
    lab_probabilities = build_lab_probability_map()
    mall_probabilities = build_mall_probability_map()
    overrides: dict[str, dict[str, Any]] = {}
    for rank, item in enumerate(LAB_HOME_STACK, start=1):
        section = _extract_section(quant, f"{item['prototype_id']} — {item['prototype_label']}")
        probabilities = lab_probabilities.get(item["prototype_id"], {})
        overrides[item["bot_id"]] = {
            "home_enabled": True,
            "home_section": "lab",
            "home_rank": rank,
            "prototype_id": item["prototype_id"],
            "prototype_label": item["prototype_label"],
            "home_tier": item["tier"],
            "home_market": item["market"],
            "manual_excerpt": _clip_words(_first_paragraph(section), 64),
            **{k: v for k, v in probabilities.items() if v},
        }
    for rank, item in enumerate(MALL_HOME_STACK, start=1):
        probabilities = mall_probabilities.get(item.get("prototype_id", ""), {})
        overrides[item["bot_id"]] = {
            "home_enabled": True,
            "home_section": "mall",
            "home_rank": rank,
            "prototype_id": item.get("prototype_id", ""),
            "prototype_label": item.get("prototype_label", ""),
            "home_lane": item.get("lane", ""),
            "prob_100_week": probabilities.get("prob_100_week") or item.get("prob_100_week", ""),
            "prob_500_two_months": probabilities.get("prob_500_two_months") or item.get("prob_500_two_months", ""),
            "prob_1000_three_months": probabilities.get("prob_1000_three_months") or item.get("prob_1000_three_months", ""),
            "startup_cost": probabilities.get("startup_cost") or item.get("startup_cost", ""),
        }
    return overrides


def enrich_catalog_entry(entry: dict[str, Any]) -> dict[str, Any]:
    overrides = get_catalog_home_overrides().get(entry.get("bot_id"), {})
    if not overrides:
        return entry
    merged = dict(entry)
    merged.update({k: v for k, v in overrides.items() if v not in (None, "")})
    return merged


def build_home_payload() -> dict[str, Any]:
    return {
        "manuals": build_manual_library(),
        "lab_highlights": build_lab_highlights(),
        "forcefield": FORCEFIELD_SUMMARY,
        "featured_lab_stack": LAB_HOME_STACK,
        "featured_mall_stack": MALL_HOME_STACK,
        "featured_lab_ids": [item["bot_id"] for item in LAB_HOME_STACK],
        "featured_mall_ids": [item["bot_id"] for item in MALL_HOME_STACK],
        "featured_lab_count": len(LAB_HOME_STACK),
        "featured_mall_count": len(MALL_HOME_STACK),
    }
