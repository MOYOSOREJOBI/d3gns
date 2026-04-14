from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class MetaculusAdapter(BaseAdapter):
    """
    Metaculus API — free, no auth for public data.
    Research-grade prediction market with 30,000+ questions, calibrated forecasters.
    Best source for long-horizon political, scientific, and economic forecasts.
    """

    platform_name = "metaculus"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://www.metaculus.com/api2"

    # Key tournament/category slugs
    CATEGORIES = {
        "ai":        "ai-progress",
        "us_politics": "us-elections",
        "economy":   "economics",
        "geopolitics": "geopolitics",
        "science":   "science-and-technology",
        "crypto":    "cryptocurrency",
        "health":    "health-and-pandemics",
        "climate":   "climate-and-environment",
    }

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        res = self.get_questions(limit=1)
        if res.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        return self._error("health_failed", res.get("error", "unknown"), auth_truth="no_auth_required")

    def get_questions(
        self,
        status: str = "open",
        order_by: str = "-activity",
        limit: int = 20,
        search: str | None = None,
        project_slug: str | None = None,
    ) -> dict[str, Any]:
        """Fetch Metaculus questions (prediction market forecasts)."""
        params: dict[str, Any] = {
            "status": status,
            "order_by": order_by,
            "limit": limit,
            "format": "json",
        }
        if search:
            params["search"] = search
        if project_slug:
            params["project__slug"] = project_slug
        try:
            r = self._request("GET", "/questions/", params=params, timeout=12.0)
            raw = r.json()
            questions = [
                {
                    "id": q.get("id"),
                    "title": q.get("title"),
                    "url": f"https://www.metaculus.com{q.get('page_url', '')}",
                    "resolution_criteria": (q.get("resolution_criteria") or "")[:300],
                    "close_time": q.get("close_time"),
                    "resolve_time": q.get("resolve_time"),
                    "community_prediction": q.get("community_prediction", {}).get("full", {}).get("q2"),
                    "prediction_count": q.get("prediction_count"),
                    "comment_count": q.get("comment_count"),
                    "activity": q.get("activity"),
                    "category": q.get("cat"),
                    "resolution": q.get("resolution"),
                    "status": q.get("active_state"),
                }
                for q in raw.get("results", [])
            ]
            return self._ok(
                data={"questions": questions, "count": len(questions), "total": raw.get("count")},
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("questions_failed", str(exc), auth_truth="no_auth_required")

    def search_questions(self, query: str, limit: int = 15) -> dict[str, Any]:
        """Search for questions matching a query string."""
        return self.get_questions(search=query, limit=limit)

    def get_question_detail(self, question_id: int) -> dict[str, Any]:
        """Fetch detailed data for a single question including full forecast history."""
        try:
            r = self._request("GET", f"/questions/{question_id}/", timeout=10.0)
            raw = r.json()
            return self._ok(
                data={
                    "id": raw.get("id"),
                    "title": raw.get("title"),
                    "community_prediction": raw.get("community_prediction", {}).get("full", {}).get("q2"),
                    "prediction_count": raw.get("prediction_count"),
                    "close_time": raw.get("close_time"),
                    "resolve_time": raw.get("resolve_time"),
                    "resolution": raw.get("resolution"),
                    "resolution_criteria": (raw.get("resolution_criteria") or "")[:500],
                    "background_info": (raw.get("background_info") or "")[:500],
                    "status": raw.get("active_state"),
                    "forecasters_count": raw.get("prediction_count"),
                    "url": f"https://www.metaculus.com{raw.get('page_url', '')}",
                },
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("detail_failed", str(exc), auth_truth="no_auth_required")

    def get_tournaments(self, limit: int = 10) -> dict[str, Any]:
        """Fetch active Metaculus tournaments (structured prediction competitions)."""
        try:
            r = self._request("GET", "/projects/", params={"type": "tournament", "limit": limit}, timeout=10.0)
            raw = r.json()
            tournaments = [
                {
                    "id": t.get("id"),
                    "name": t.get("name"),
                    "slug": t.get("slug"),
                    "description": (t.get("description") or "")[:200],
                    "close_date": t.get("close_date"),
                    "prize_pool": t.get("prize_pool"),
                    "question_count": t.get("question_count"),
                    "forecaster_count": t.get("forecaster_count"),
                }
                for t in raw.get("results", [])
            ]
            return self._ok(
                data={"tournaments": tournaments, "count": len(tournaments)},
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("tournaments_failed", str(exc), auth_truth="no_auth_required")

    def get_signal_for_topics(self, topics: list[str] | None = None) -> dict[str, Any]:
        """
        Search Metaculus for open questions on prediction-market-relevant topics.
        Returns community probability estimates as research signals.
        """
        targets = topics or [
            "bitcoin price", "US election", "Federal Reserve rate",
            "AI regulation", "recession", "Ukraine", "China Taiwan",
        ]
        signals = []
        for topic in targets[:6]:
            res = self.search_questions(query=topic, limit=5)
            if not res.get("ok"):
                continue
            questions = res["data"].get("questions", [])
            if not questions:
                continue
            # Most active question for this topic
            best = max(questions, key=lambda q: q.get("prediction_count") or 0)
            prob = best.get("community_prediction")
            if prob is not None:
                signals.append({
                    "topic": topic,
                    "question": best.get("title"),
                    "metaculus_probability": round(float(prob), 3),
                    "prediction_count": best.get("prediction_count"),
                    "close_time": best.get("close_time"),
                    "url": best.get("url"),
                    "signal_note": f"Research-grade forecast: {round(float(prob)*100,1)}% probability",
                })
        return self._ok(
            data={"signals": signals, "topics_searched": len(targets)},
            status="ok", auth_truth="no_auth_required",
        )

    def get_calibration_summary(self) -> dict[str, Any]:
        """
        Fetch recently resolved questions to assess Metaculus calibration.
        Compares community predictions vs actual resolutions.
        """
        try:
            r = self._request(
                "GET", "/questions/",
                params={"status": "resolved", "order_by": "-resolve_time", "limit": 20},
                timeout=12.0,
            )
            raw = r.json()
            resolved = raw.get("results", [])
            correct = 0
            total = 0
            calibration_data = []
            for q in resolved:
                prob = q.get("community_prediction", {}).get("full", {}).get("q2")
                resolution = q.get("resolution")
                if prob is None or resolution not in (1.0, 0.0):
                    continue
                total += 1
                predicted_yes = float(prob) > 0.5
                resolved_yes = float(resolution) == 1.0
                correct += 1 if predicted_yes == resolved_yes else 0
                calibration_data.append({
                    "title": q.get("title"),
                    "probability": round(float(prob), 3),
                    "resolution": resolution,
                    "correct": predicted_yes == resolved_yes,
                })
            accuracy = round(correct / total, 3) if total > 0 else None
            return self._ok(
                data={
                    "accuracy": accuracy,
                    "correct": correct,
                    "total": total,
                    "calibration_data": calibration_data[:10],
                    "note": "Metaculus community is historically well-calibrated (~70-75% accuracy at median threshold)",
                },
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("calibration_failed", str(exc), auth_truth="no_auth_required")
