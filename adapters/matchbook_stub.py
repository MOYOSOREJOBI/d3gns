from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class MatchbookStubAdapter(BaseAdapter):
    """
    Matchbook exchange adapter — NOT CONFIGURED stub.

    This stub registers the Matchbook platform in the adapter registry
    so it appears in platform health and the UI. It will never affect
    runtime behavior unless explicitly enabled and implemented.

    Truth labels: NOT CONFIGURED
    Execution:    NEVER — stub only.
    To enable:    Set ENABLE_MATCHBOOK_STUB=true and implement full adapter.
    """

    platform_name = "matchbook"
    mode = "NOT CONFIGURED"
    live_capable = False
    execution_enabled = False
    auth_required = True
    data_truth_label = "NOT CONFIGURED"
    base_url = ""

    def is_configured(self) -> bool:
        return False

    def healthcheck(self) -> dict[str, Any]:
        enabled = self._bool_setting("ENABLE_MATCHBOOK_STUB", False)
        return self._error(
            "not_configured" if enabled else "disabled",
            "Matchbook adapter is not implemented. This is a registered stub.",
            degraded_reason=(
                "Matchbook exchange integration is not yet implemented. "
                "The stub is registered to prevent runtime errors. "
                "Set ENABLE_MATCHBOOK_STUB=true when full implementation is ready."
            ),
            status="not_configured",
            auth_truth="missing",
        )
