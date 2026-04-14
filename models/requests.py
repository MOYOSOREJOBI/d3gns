from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FlexibleRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    def to_body(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True, exclude_unset=True)


class AuthRequest(FlexibleRequest):
    password: str
    device_id: str = ""


class ChangePasswordRequest(FlexibleRequest):
    current_password: str | None = None
    new_password: str


class NoteRequest(FlexibleRequest):
    title: str = ""
    text: str = ""


class AmountRequest(FlexibleRequest):
    amount: float
    platform: str | None = None
    note: str | None = None


class BotRegistryItem(FlexibleRequest):
    id: str | None = None
    enabled: bool | None = None
    display_name: str | None = None


class BotConfigSaveRequest(FlexibleRequest):
    bots: list[BotRegistryItem] = Field(default_factory=list)


class StrategyModeRequest(FlexibleRequest):
    mode: str = "balanced"


class ScaleRequest(FlexibleRequest):
    scale: float = 1.0


class FundRequest(FlexibleRequest):
    amount: float


class GoalsRequest(FlexibleRequest):
    target: float


class TelegramTestRequest(FlexibleRequest):
    bot_token: str | None = None
    chat_id: str | None = None


class HumanRelayOpenRequest(FlexibleRequest):
    bot_id: str = ""
    platform: str = ""
    prompt: str
    description: str | None = None
    screenshot_path: str | None = None
    challenge_type: str | None = None
    timeout_s: int | None = None
    chat_id: str | None = None
    bot_token: str | None = None
    payload: dict[str, Any] | None = None


class HumanRelayRespondRequest(FlexibleRequest):
    decision: str
    source: str | None = None
    payload: dict[str, Any] | None = None


class SettingsUpdateRequest(FlexibleRequest):
    key: str | None = None
    value: Any | None = None
    reauth_password: str | None = None


class LiveControlRequest(FlexibleRequest):
    live_execution_enabled: bool | None = None
    stake_live_enabled: bool | None = None
    polymarket_live_enabled: bool | None = None
    confirm_live: bool | None = None
    reauth_password: str | None = None


class DeviceApproveRequest(FlexibleRequest):
    device_id: str
    name: str | None = None
    reauth_password: str | None = None


class DeviceRevokeRequest(FlexibleRequest):
    device_id: str
    reauth_password: str | None = None


class DeviceRenameRequest(FlexibleRequest):
    device_id: str
    name: str


class ResetToZeroRequest(FlexibleRequest):
    reauth_password: str | None = None


class ExecutorTakeoverRequest(FlexibleRequest):
    reauth_password: str | None = None


class BotConfigureRequest(FlexibleRequest):
    start_amount: float = 100.0
    target_amount: float | None = None
    floor_amount: float | None = None


class MilestoneContinueRequest(FlexibleRequest):
    multiplier: float = 10.0


class VaultLockRequest(FlexibleRequest):
    bot_id: str
    amount: float


class SchedulerIntervalRequest(FlexibleRequest):
    interval_seconds: int


class KalshiLiveOrderRequest(FlexibleRequest):
    market_id: str = ""
    side: str = ""
    size: float | None = None
    price: float | None = None
    order_type: str | None = None
    live_enabled: bool | None = None


class SimulatorRunRequest(FlexibleRequest):
    mode: str | None = None
    bot_id: str | None = None
    strategy_id: str | None = None
    strategy: str | None = None
    run_count: int | None = None
    n_rounds: int | None = None
    bankroll: float | None = None
    params_override: dict[str, Any] | None = None
    initial_state: dict[str, Any] | None = None
    signal_sequence: list[dict[str, Any]] | None = None


class SimulatorCompareRequest(FlexibleRequest):
    runs: list[dict[str, Any]] = Field(default_factory=list)
    label: str = ""


class ProposalPreviewRequest(FlexibleRequest):
    bot_id: str
    force_refresh: bool = False
    runtime_mode: str = "shadow"


class ShadowOrderRequest(FlexibleRequest):
    proposal_id: str
    execution_mode: str = "shadow"
    price_limit: float | None = None


class WithdrawalCreateRequest(FlexibleRequest):
    bot_id: str = ""
    venue: str = ""
    amount: float
    currency: str = "USD"
    destination_type: str = "internal_vault"
    destination_ref_masked: str = ""
    note: str | None = None
    operator_note: str | None = None
    payload: dict[str, Any] | None = None
    reauth_password: str | None = None


class WithdrawalApprovalRequest(FlexibleRequest):
    approved: bool = True
    operator_note: str | None = None
    external_ref: str | None = None
    payload: dict[str, Any] | None = None
    reauth_password: str | None = None


class SimulatorReplayRequest(FlexibleRequest):
    bot_id: str
    market_id: str = ""
    start_ts: str
    end_ts: str
    bankroll: float = 100.0
    fee_profile: str = "realistic"
    slippage_profile: str = "moderate"
    mode: str = "replay"
