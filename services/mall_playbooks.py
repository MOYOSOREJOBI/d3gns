from __future__ import annotations

from typing import Any


def _clamp_amount(value: float, low: float, high: float) -> float:
    return float(max(low, min(high, value)))


def _infer_offer(item: dict[str, Any]) -> dict[str, Any]:
    lane = str(item.get("lane") or "").lower()
    title = str(item.get("title") or "").lower()
    payload = dict(item.get("payload") or {})
    merged = " ".join(
        str(part or "").lower()
        for part in (
            lane,
            title,
            payload.get("source"),
            payload.get("next_action"),
            payload.get("website"),
            item.get("bot_id"),
        )
    )

    value_estimate = float(item.get("value_estimate", 0) or 0)

    if any(token in merged for token in ("google", "business profile", "gbp", "map pack", "local listing")):
        recommended = _clamp_amount(value_estimate or 179.0, 149.0, 199.0)
        return {
            "offer_key": "gbp_fix",
            "offer_name": "Google Business Profile Fix",
            "deliverables": [
                "clean category + service setup",
                "profile copy refresh",
                "review / photo / CTA checklist",
            ],
            "timeline": "24-48 hours",
            "recommended_quote": round(recommended, 2),
            "quote_range": [149.0, 199.0],
            "problem_frame": "their local listing likely under-converts because the profile is incomplete, unclear, or poorly positioned",
        }

    if any(token in merged for token in ("booking", "intake", "chatbot", "lead form", "calendar", "funnel")):
        recommended = _clamp_amount(value_estimate or 299.0, 249.0, 349.0)
        return {
            "offer_key": "booking_funnel",
            "offer_name": "Booking Funnel Fix",
            "deliverables": [
                "CTA and intake path cleanup",
                "faster inquiry / booking flow",
                "message + trust section refresh",
            ],
            "timeline": "2-3 days",
            "recommended_quote": round(recommended, 2),
            "quote_range": [249.0, 349.0],
            "problem_frame": "their site or intake flow likely leaks leads before they ever become calls or bookings",
        }

    recommended = _clamp_amount(value_estimate or 449.0, 399.0, 599.0)
    return {
        "offer_key": "site_refresh",
        "offer_name": "Local Site Refresh",
        "deliverables": [
            "homepage refresh with stronger CTA",
            "service section cleanup",
            "mobile trust + conversion polish",
        ],
        "timeline": "3-5 days",
        "recommended_quote": round(recommended, 2),
        "quote_range": [399.0, 599.0],
        "problem_frame": "their website likely looks dated or unclear enough to lose trust and conversions",
    }


def build_mall_item_playbook(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {
            "ok": False,
            "error": "missing_item",
        }

    payload = dict(item.get("payload") or {})
    offer = _infer_offer(item)
    title = str(item.get("title") or item.get("bot_id") or "this business").strip()
    lane = str(item.get("lane") or "service").replace("_", " ")
    website = str(payload.get("website") or "").strip()
    source = str(payload.get("source") or "Mall discovery").strip()
    next_action = str(payload.get("next_action") or "review and send same-day outreach").strip()
    quoted = float(item.get("quoted_amount") or 0) or float(offer["recommended_quote"])

    subject = f"Quick fix idea for {title}"
    opener = f"I took a quick look at {title}"
    if website:
        opener += f" ({website})"
    opener += " and spotted a simple improvement opportunity."

    outreach_body = (
        f"{opener}\n\n"
        f"It looks like {offer['problem_frame']}. I can handle a focused {offer['offer_name'].lower()} for you.\n\n"
        f"What I would change:\n"
        + "\n".join(f"- {line}" for line in offer["deliverables"])
        + f"\n\nTurnaround: {offer['timeline']}\n"
        f"Price: ${offer['recommended_quote']:.2f}\n\n"
        "If you want, I can send the exact scope and get it turned around quickly.\n\n"
        "If this is not a fit, just reply with stop and I will not follow up again."
    )

    quote_body = (
        f"{offer['offer_name']} for {title}\n\n"
        f"Scope:\n"
        + "\n".join(f"- {line}" for line in offer["deliverables"])
        + f"\n\nTimeline: {offer['timeline']}\n"
        f"Quoted amount: ${quoted:.2f}\n\n"
        "This is a focused conversion / trust improvement job, not a full rebuild. Once approved, I can start immediately."
    )

    followup_body = (
        f"Following up on the {offer['offer_name'].lower()} idea for {title}.\n\n"
        f"I can still get the work done in {offer['timeline']} if you want to move quickly. "
        "If timing is bad, just let me know and I will close the loop respectfully."
    )

    return {
        "ok": True,
        "offer": offer,
        "summary": {
            "title": title,
            "lane": lane,
            "source": source,
            "next_action": next_action,
            "website": website,
        },
        "templates": {
            "outreach_subject": subject,
            "outreach_body": outreach_body,
            "quote_body": quote_body,
            "followup_body": followup_body,
        },
        "operator_prompts": [
            "Verify the business is a real fit before sending outreach.",
            "Keep outreach human and specific to one observed issue.",
            "Quote same day when the lead is warm.",
            "Use operator review before any risky or repetitive outbound action.",
        ],
    }
