from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class BetdaqStubAdapter(BaseAdapter):
    """
    BETDAQ exchange adapter — NOT CONFIGURED stub.

    Registers BETDAQ in the platform registry. No runtime effect unless
    explicitly enabled and a full implementation is added.

    Truth labels: NOT CONFIGURED
    Execution:    NEVER — stub only.
    """

    platform_name = "betdaq"
    mode = "NOT CONFIGURED"
    live_capable = False
    execution_enabled = False
    auth_required = True
    data_truth_label = "NOT CONFIGURED"
    base_url = ""

    def is_configured(self) -> bool:
        return False

    def healthcheck(self) -> dict[str, Any]:
        enabled = self._bool_setting("ENABLE_BETDAQ_STUB", False)
        return self._error(
            "not_configured" if enabled else "disabled",
            "BETDAQ adapter is not implemented. This is a registered stub.",
            degraded_reason=(
                "BETDAQ exchange integration is not yet implemented. "
                "The stub is registered to prevent runtime errors. "
                "Set ENABLE_BETDAQ_STUB=true when full implementation is ready."
            ),
            status="not_configured",
            auth_truth="missing",
        )
