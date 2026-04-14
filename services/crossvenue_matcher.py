from __future__ import annotations

"""
Crossvenue Matcher — Phase 4.

Compares event/market lists from different prediction market venues and
produces pairs with confidence scores.

Design principles:
- Conservative: weak matches become watchlist-only, never active comparisons.
- Never assume Kalshi and Polymarket (or any two venues) are equivalent automatically.
- All outputs include explicit truth labels and warnings.
- No live order signals are produced here. Matching is research/watchlist only.
- Cap iterations to prevent O(n²) abuse on large market lists.

Confidence thresholds (calibrated conservatively):
  >= 0.70  → active_pair      (comparable for research; still needs human review)
  >= 0.40  → watchlist_only   (worth monitoring; do NOT trade on this match)
  <  0.40  → unmatched        (logged but not surfaced to UI)
"""

from typing import Any

from services.event_normalizer import composite_score


# ── Thresholds ────────────────────────────────────────────────────────────────
HIGH_CONFIDENCE_THRESHOLD: float = 0.70
WATCHLIST_THRESHOLD: float = 0.40

# Maximum number of markets to compare from each side to keep latency bounded.
_MAX_COMPARE_PER_SIDE: int = 50


# ── Match pair computation ────────────────────────────────────────────────────

def compute_match(
    title_a: str,
    title_b: str,
    start_a: float | None = None,
    end_a: float | None = None,
    start_b: float | None = None,
    end_b: float | None = None,
) -> dict[str, Any]:
    """
    Compute a match record between two event titles with optional time windows.
    Returns a dict including component scores, composite confidence, verdict,
    and truth/warning annotations.
    """
    scores = composite_score(title_a, title_b, start_a, end_a, start_b, end_b)
    conf = scores["composite_confidence"]

    if conf >= HIGH_CONFIDENCE_THRESHOLD:
        verdict = "active_pair"
        verdict_reason = (
            f"Composite confidence {conf:.2f} ≥ {HIGH_CONFIDENCE_THRESHOLD} → "
            "active comparable pair. Still requires human verification before use."
        )
        truth_label = "COMPARABLE PAIR — VERIFY BEFORE USE"
        warning = ""
    elif conf >= WATCHLIST_THRESHOLD:
        verdict = "watchlist_only"
        verdict_reason = (
            f"Composite confidence {conf:.2f} ≥ {WATCHLIST_THRESHOLD} but "
            f"< {HIGH_CONFIDENCE_THRESHOLD} → watchlist only. "
            "Do not treat as confirmed equivalent markets."
        )
        truth_label = "WATCHLIST ONLY"
        warning = (
            "Match confidence is below the active threshold. "
            "Do not compare implied probabilities or use for trading without manual confirmation."
        )
    else:
        verdict = "unmatched"
        verdict_reason = (
            f"Composite confidence {conf:.2f} < {WATCHLIST_THRESHOLD} → unmatched. "
            "These events are not comparable based on available data."
        )
        truth_label = "UNMATCHED"
        warning = "Low confidence. Not surfaced as a match."

    return {
        **scores,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "truth_label": truth_label,
        "warning": warning,
        "side_map_note": (
            "Outcome sides are auto-mapped YES→YES / NO→NO. "
            "Verify before comparing any implied probability spreads."
        ),
    }


def match_market_lists(
    markets_a: list[dict[str, Any]],
    platform_a: str,
    markets_b: list[dict[str, Any]],
    platform_b: str,
    *,
    title_key_a: str = "question",
    title_key_b: str = "question",
    id_key_a: str = "id",
    id_key_b: str = "id",
    start_key: str = "endDateIso",
    include_unmatched: bool = False,
) -> list[dict[str, Any]]:
    """
    Compare two market lists and return pairs with confidence scores.

    For each market in markets_a, find the best-matching market in markets_b.
    Only returns pairs that meet at least the watchlist threshold.
    Optionally includes unmatched entries for logging.

    Args:
        markets_a, markets_b:  Lists of market dicts from respective platforms.
        platform_a, platform_b: Platform name strings for labeling.
        title_key_a/b:         Key to use for market title in each list.
        id_key_a/b:            Key to use for market ID in each list.
        start_key:             Key for resolution/end time (ISO string or epoch).
        include_unmatched:     If True, include pairs below watchlist threshold too.

    Returns sorted list of match dicts (by composite_confidence desc).
    """
    a_sample = markets_a[:_MAX_COMPARE_PER_SIDE]
    b_sample = markets_b[:_MAX_COMPARE_PER_SIDE]

    results: list[dict[str, Any]] = []

    for mkt_a in a_sample:
        title_a = (
            mkt_a.get(title_key_a)
            or mkt_a.get("title")
            or mkt_a.get("question")
            or mkt_a.get("subtitle")
            or ""
        )
        if not title_a:
            continue

        end_a = _parse_epoch(mkt_a.get(start_key))

        best_conf = -1.0
        best_pair: dict[str, Any] | None = None

        for mkt_b in b_sample:
            title_b = (
                mkt_b.get(title_key_b)
                or mkt_b.get("title")
                or mkt_b.get("question")
                or mkt_b.get("subtitle")
                or ""
            )
            if not title_b:
                continue

            end_b = _parse_epoch(mkt_b.get(start_key))
            match = compute_match(title_a, title_b, end_a=end_a, end_b=end_b)

            if match["composite_confidence"] > best_conf:
                best_conf = match["composite_confidence"]
                best_pair = {
                    "platform_a": platform_a,
                    "platform_b": platform_b,
                    "id_a": mkt_a.get(id_key_a) or mkt_a.get("ticker") or mkt_a.get("conditionId") or "",
                    "id_b": mkt_b.get(id_key_b) or mkt_b.get("ticker") or mkt_b.get("conditionId") or "",
                    "title_a": title_a,
                    "title_b": title_b,
                    **match,
                }

        if best_pair is None:
            continue

        if best_conf >= WATCHLIST_THRESHOLD or include_unmatched:
            results.append(best_pair)

    results.sort(key=lambda r: r["composite_confidence"], reverse=True)
    return results


def summarize_matches(matches: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Produce a summary dict suitable for the /api/crossvenue/watchlist endpoint.
    """
    active = [m for m in matches if m.get("verdict") == "active_pair"]
    watchlist = [m for m in matches if m.get("verdict") == "watchlist_only"]
    return {
        "total_compared": len(matches),
        "active_pairs": len(active),
        "watchlist_pairs": len(watchlist),
        "pairs": matches,
        "truth_label": "WATCHLIST ONLY — no realized PnL implied",
        "warning": (
            "All crossvenue pairs are for research and monitoring only. "
            "No profit/loss figures are associated with this watchlist. "
            "Active pairs still require manual confirmation before any comparison."
        ),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_epoch(value: Any) -> float | None:
    """Best-effort parse of a date/epoch value to float epoch seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # Try ISO 8601
        import datetime
        for fmt in (
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.datetime.strptime(value, fmt)
                return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
            except ValueError:
                continue
    return None
