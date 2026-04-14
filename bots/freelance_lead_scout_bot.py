from __future__ import annotations

import hashlib
import re
import time
import logging
from typing import Any

from bots.base_research_bot import BaseResearchBot

logger = logging.getLogger(__name__)


class FreelanceLeadScoutBot(BaseResearchBot):
    bot_id = "bot_freelance_lead_scout"
    display_name = "Freelance Lead Scout"
    platform = "public_web"
    mode = "RESEARCH"
    signal_type = "opportunity_discovery"
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "A"
    risk_tier = "low"
    description = (
        "Scans public RSS freelance boards for automation/Python/AI contracts. "
        "Ranks by keyword match and rate signals."
    )
    edge_source = "Early discovery of high-value freelance contracts"
    opp_cadence_per_day = 10.0
    platforms = ["remoteok", "weworkremotely"]

    MATCH_KEYWORDS = [
        "python", "automation", "trading", "bot", "api", "fastapi", "react",
        "data", "scraping", "ai", "machine learning", "crypto", "web3",
        "dashboard", "full-stack", "fullstack", "backend",
    ]
    RATE_SIGNALS = ["$100", "$150", "$200", "/hr", "hourly", "contract"]

    def __init__(self, adapter=None):
        self.adapter = adapter
        self._seen: set[str] = set()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fetch_rss(self, url: str) -> list[dict]:
        import requests
        try:
            resp = requests.get(
                url, timeout=15,
                headers={"User-Agent": "DeGENS-Scout/1.0"}
            )
            if resp.status_code != 200:
                return []
            items = []
            for m in re.finditer(r"<item>(.*?)</item>", resp.text, re.DOTALL):
                xml  = m.group(1)
                t    = re.search(r"<title>(.*?)</title>", xml, re.DOTALL)
                link = re.search(r"<link>(.*?)</link>", xml, re.DOTALL)
                desc = re.search(r"<description>(.*?)</description>", xml, re.DOTALL)
                if t:
                    items.append({
                        "title": re.sub(r"<[^>]+>", "", t.group(1)).strip(),
                        "link" : link.group(1).strip() if link else "",
                        "desc" : re.sub(r"<[^>]+>", "", desc.group(1)).strip()[:400]
                        if desc else "",
                    })
            return items[:25]
        except Exception as exc:
            logger.debug(f"RSS fetch error {url}: {exc}")
            return []

    def _score(self, title: str, desc: str) -> float:
        text    = f"{title} {desc}".lower()
        kw_hits = sum(1 for kw in self.MATCH_KEYWORDS if kw in text)
        rate_ok = any(r.lower() in text for r in self.RATE_SIGNALS)
        score   = min(1.0, kw_hits / 5.0)
        if rate_ok:
            score = min(1.0, score + 0.2)
        return round(score, 2)

    # ── Main cycle ────────────────────────────────────────────────────────────

    def run_one_cycle(self) -> dict[str, Any]:
        feeds = [
            ("https://remoteok.com/remote-python-jobs.rss", "remoteok"),
            ("https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss", "wwr"),
        ]

        leads = []
        for url, source in feeds:
            for item in self._fetch_rss(url):
                uid = hashlib.md5(item["title"].encode()).hexdigest()[:10]
                if uid in self._seen:
                    continue
                self._seen.add(uid)
                score = self._score(item["title"], item["desc"])
                if score >= 0.40:
                    leads.append({
                        "title" : item["title"],
                        "link"  : item["link"],
                        "source": source,
                        "score" : score,
                        "desc"  : item["desc"][:180],
                    })

        leads.sort(key=lambda x: x["score"], reverse=True)
        top = leads[:5]

        if top:
            return self.emit_signal(
                title=f"Found {len(top)} Freelance Leads",
                summary=f"Top: {top[0]['title'][:80]}",
                confidence=top[0]["score"],
                signal_taken=True,
                data={
                    "leads"            : top,
                    "requires_capital" : False,
                    "opportunity_value": round(sum(lead["score"] * 500 for lead in top), 2),
                },
            )

        return self.emit_signal(
            title="Freelance Lead Scout — No New Leads",
            summary="Monitoring RSS. No new high-fit leads this cycle.",
            confidence=0.3,
            signal_taken=False,
            data={"leads": [], "requires_capital": False},
        )
