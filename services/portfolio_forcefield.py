from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4


ACTION_CONTINUE = "CONTINUE"
ACTION_MILESTONE = "MILESTONE"
ACTION_GOAL_HIT = "GOAL_HIT"
ACTION_MANUAL_PAUSE = "MANUAL_PAUSE"

DEFAULT_PORTFOLIO_ID = "main"
FLOOR_PCT = Decimal("0.80")
REPEL_ZONE_PCT = Decimal("0.20")
MICRO_SIZE_PCT = Decimal("0.00025")
HEADROOM_CAP = Decimal("0.20")
SWEEP_TRIGGER = Decimal("1.20")
POST_SWEEP_BUFFER_PCT = Decimal("0.10")
MIN_SWEEP_PCT = Decimal("0.05")
MICRO_MIN = Decimal("0.01")
MILESTONES = (Decimal("3.0"), Decimal("10.0"), Decimal("20.0"))
FINAL_RESERVATION_STATES = {"EXECUTED", "SETTLED", "RELEASED", "EXPIRED", "FAILED"}
OPEN_STATE = "OPEN"  # Position is live — headroom held until settlement


def _decimal(value: Any, default: str = "0") -> Decimal:
    if isinstance(value, Decimal):
        return value
    raw = default if value is None or value == "" else str(value)
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _to_storage(value: Decimal) -> str:
    value = value if isinstance(value, Decimal) else _decimal(value)
    text = format(value, "f")
    return text if "." in text else f"{text}.0"


def _payload_load(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


@contextmanager
def _write_tx(db_module: Any):
    lock = getattr(db_module, "_lock", None)
    cx_factory = getattr(db_module, "_cx", None)
    if lock is None or cx_factory is None:
        raise RuntimeError("db_module does not expose transactional internals")
    with lock:
        c = cx_factory()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def _read_all(db_module: Any, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    c = db_module._cx()
    try:
        return [dict(row) for row in c.execute(query, params).fetchall()]
    finally:
        c.close()


def _read_one(db_module: Any, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    c = db_module._cx()
    try:
        row = c.execute(query, params).fetchone()
    finally:
        c.close()
    return dict(row) if row is not None else None


def _wallet_totals_locked(c: Any) -> dict[str, Decimal]:
    deposits = _decimal(
        c.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM wallet_tx WHERE type IN ('deposit', 'initial')"
        ).fetchone()[0]
    )
    withdrawals = _decimal(
        c.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM wallet_tx WHERE type IN ('withdraw_approved', 'withdraw_sent')"
        ).fetchone()[0]
    )
    vault_total = _decimal(
        c.execute("SELECT COALESCE(SUM(amount), 0) FROM vault").fetchone()[0]
    )
    pnl = _decimal(
        c.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM wallet_tx WHERE type IN ('trade_pnl', 'settlement', 'fee')"
        ).fetchone()[0]
    )
    working_capital = deposits - withdrawals - vault_total + pnl
    if working_capital < Decimal("0"):
        working_capital = Decimal("0")
    return {
        "deposits": deposits,
        "withdrawals": withdrawals,
        "vault_total": vault_total,
        "pnl": pnl,
        "working_capital": working_capital,
    }


def _derive_base_capital(wallet: dict[str, Decimal]) -> Decimal:
    base = wallet["deposits"] - wallet["withdrawals"]
    if base <= Decimal("0"):
        base = wallet["working_capital"] + wallet["vault_total"]
    if base <= Decimal("0"):
        base = Decimal("100")
    return base


@dataclass
class PortfolioSnapshot:
    portfolio_id: str
    base_capital: Decimal
    hard_floor: Decimal
    equity: Decimal
    free_cash: Decimal
    reserved_loss_budget: Decimal
    reserved_fees: Decimal
    vault_balance: Decimal
    milestone_index: int
    milestone_pending: bool
    manual_paused: bool
    target_hit: bool
    updated_at: str = ""

    @property
    def total_value(self) -> Decimal:
        return self.equity + self.vault_balance

    @property
    def current_multiple(self) -> Decimal:
        if self.base_capital <= Decimal("0"):
            return Decimal("0")
        return self.total_value / self.base_capital

    @property
    def next_milestone(self) -> Decimal:
        if self.target_hit:
            return MILESTONES[-1]
        index = min(max(self.milestone_index, 0), len(MILESTONES) - 1)
        return MILESTONES[index]

    @property
    def true_free_headroom(self) -> Decimal:
        return max(
            Decimal("0"),
            self.equity - self.hard_floor - self.reserved_loss_budget - self.reserved_fees,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "portfolio_id": self.portfolio_id,
            "base_capital": float(self.base_capital),
            "hard_floor": float(self.hard_floor),
            "equity": float(self.equity),
            "free_cash": float(self.free_cash),
            "reserved_loss_budget": float(self.reserved_loss_budget),
            "reserved_fees": float(self.reserved_fees),
            "vault_balance": float(self.vault_balance),
            "total_value": float(self.total_value),
            "current_multiple": float(self.current_multiple),
            "true_free_headroom": float(self.true_free_headroom),
            "milestone_index": self.milestone_index,
            "milestone_pending": self.milestone_pending,
            "manual_paused": self.manual_paused,
            "target_hit": self.target_hit,
            "next_milestone": float(self.next_milestone),
            "updated_at": self.updated_at,
        }


def _snapshot_from_row(row: dict[str, Any]) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        portfolio_id=str(row.get("portfolio_id", DEFAULT_PORTFOLIO_ID) or DEFAULT_PORTFOLIO_ID),
        base_capital=_decimal(row.get("base_capital")),
        hard_floor=_decimal(row.get("hard_floor")),
        equity=_decimal(row.get("equity")),
        free_cash=_decimal(row.get("free_cash")),
        reserved_loss_budget=_decimal(row.get("reserved_loss_budget")),
        reserved_fees=_decimal(row.get("reserved_fees")),
        vault_balance=_decimal(row.get("vault_balance")),
        milestone_index=int(row.get("milestone_index", 0) or 0),
        milestone_pending=bool(int(row.get("milestone_pending", 0) or 0)),
        manual_paused=bool(int(row.get("manual_paused", 0) or 0)),
        target_hit=bool(int(row.get("target_hit", 0) or 0)),
        updated_at=str(row.get("updated_at", "") or ""),
    )


def _fetch_portfolio_locked(c: Any, portfolio_id: str) -> dict[str, Any] | None:
    row = c.execute(
        "SELECT * FROM portfolio_state WHERE portfolio_id=?",
        (portfolio_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _action_from_snapshot(snapshot: PortfolioSnapshot) -> str:
    if snapshot.manual_paused:
        return ACTION_MANUAL_PAUSE
    if snapshot.target_hit:
        return ACTION_GOAL_HIT
    if snapshot.milestone_pending:
        return ACTION_MILESTONE
    return ACTION_CONTINUE


def _refresh_milestone_locked(c: Any, snapshot: PortfolioSnapshot) -> str:
    action = _action_from_snapshot(snapshot)
    if action != ACTION_CONTINUE:
        return action
    if snapshot.milestone_index >= len(MILESTONES):
        c.execute(
            """
            UPDATE portfolio_state
            SET target_hit=1,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE portfolio_id=?
            """,
            (snapshot.portfolio_id,),
        )
        return ACTION_GOAL_HIT
    target_multiple = MILESTONES[snapshot.milestone_index]
    if snapshot.current_multiple < target_multiple:
        return ACTION_CONTINUE
    if snapshot.milestone_index >= len(MILESTONES) - 1:
        c.execute(
            """
            UPDATE portfolio_state
            SET target_hit=1,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE portfolio_id=?
            """,
            (snapshot.portfolio_id,),
        )
        return ACTION_GOAL_HIT
    c.execute(
        """
        UPDATE portfolio_state
        SET milestone_pending=1,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
        WHERE portfolio_id=?
        """,
        (snapshot.portfolio_id,),
    )
    return ACTION_MILESTONE


def _ensure_portfolio_locked(c: Any, portfolio_id: str = DEFAULT_PORTFOLIO_ID) -> PortfolioSnapshot:
    wallet = _wallet_totals_locked(c)
    row = _fetch_portfolio_locked(c, portfolio_id)
    if row is None:
        base_capital = _derive_base_capital(wallet)
        hard_floor = base_capital * FLOOR_PCT
        equity = wallet["working_capital"]
        c.execute(
            """
            INSERT INTO portfolio_state(
                portfolio_id,base_capital,hard_floor,equity,free_cash,
                reserved_loss_budget,reserved_fees,vault_balance,payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                portfolio_id,
                _to_storage(base_capital),
                _to_storage(hard_floor),
                _to_storage(equity),
                _to_storage(equity),
                _to_storage(Decimal("0")),
                _to_storage(Decimal("0")),
                _to_storage(wallet["vault_total"]),
                json.dumps({"source": "portfolio_forcefield"}),
            ),
        )
        row = _fetch_portfolio_locked(c, portfolio_id)
    base_capital = _decimal(row.get("base_capital"))
    if base_capital <= Decimal("0"):
        base_capital = _derive_base_capital(wallet)
    reserved_loss = _decimal(row.get("reserved_loss_budget"))
    reserved_fees = _decimal(row.get("reserved_fees"))
    hard_floor = base_capital * FLOOR_PCT
    equity = wallet["working_capital"]
    free_cash = max(Decimal("0"), equity - reserved_loss - reserved_fees)
    c.execute(
        """
        UPDATE portfolio_state
        SET base_capital=?,
            hard_floor=?,
            equity=?,
            free_cash=?,
            vault_balance=?,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
        WHERE portfolio_id=?
        """,
        (
            _to_storage(base_capital),
            _to_storage(hard_floor),
            _to_storage(equity),
            _to_storage(free_cash),
            _to_storage(wallet["vault_total"]),
            portfolio_id,
        ),
    )
    snapshot = _snapshot_from_row(_fetch_portfolio_locked(c, portfolio_id) or {})
    _refresh_milestone_locked(c, snapshot)
    return _snapshot_from_row(_fetch_portfolio_locked(c, portfolio_id) or {})


def _repel_multiplier(base_capital: Decimal, true_free_headroom: Decimal) -> Decimal:
    repel_zone = max(base_capital * REPEL_ZONE_PCT, MICRO_MIN)
    proximity = min(Decimal("1"), max(Decimal("0"), true_free_headroom / repel_zone))
    return max(Decimal("0.02"), proximity * proximity * proximity)


def compute_sweepable_cash(snapshot: PortfolioSnapshot) -> Decimal:
    if snapshot.current_multiple < SWEEP_TRIGGER:
        return Decimal("0")
    post_sweep_buffer = max(snapshot.base_capital * POST_SWEEP_BUFFER_PCT, MICRO_MIN)
    sweepable = snapshot.true_free_headroom - post_sweep_buffer
    return max(Decimal("0"), sweepable)


def sync_portfolio_state(db_module: Any, portfolio_id: str = DEFAULT_PORTFOLIO_ID) -> dict[str, Any]:
    with _write_tx(db_module) as c:
        snapshot = _ensure_portfolio_locked(c, portfolio_id)
        return {
            "ok": True,
            "action": _action_from_snapshot(snapshot),
            "portfolio": snapshot.as_dict(),
        }


def approve_reservation(
    db_module: Any,
    request: dict[str, Any],
    *,
    portfolio_id: str = DEFAULT_PORTFOLIO_ID,
) -> dict[str, Any]:
    with _write_tx(db_module) as c:
        snapshot = _ensure_portfolio_locked(c, portfolio_id)
        action = _action_from_snapshot(snapshot)
        if action != ACTION_CONTINUE:
            return {
                "approved": False,
                "action": action,
                "portfolio": snapshot.as_dict(),
            }

        idem_key = str(
            request.get("idempotency_key")
            or request.get("client_order_id")
            or request.get("proposal_id")
            or uuid4()
        )
        existing = c.execute(
            "SELECT * FROM risk_reservations WHERE idempotency_key=?",
            (idem_key,),
        ).fetchone()
        if existing is not None:
            row = dict(existing)
            return {
                "approved": row.get("reservation_state") not in {"FAILED", "EXPIRED", "RELEASED"},
                "action": ACTION_CONTINUE,
                "reservation_id": row["reservation_id"],
                "approved_notional": float(_decimal(row.get("approved_notional"))),
                "reserved_worst_loss": float(_decimal(row.get("reserved_worst_loss"))),
                "portfolio": snapshot.as_dict(),
                "idempotent": True,
            }

        desired_notional = max(_decimal(request.get("desired_notional")), Decimal("0"))
        worst_case_loss = max(_decimal(request.get("worst_case_loss"), str(desired_notional)), Decimal("0"))
        fee_budget = max(_decimal(request.get("fee_budget")), Decimal("0"))
        slippage_budget = max(_decimal(request.get("slippage_budget")), Decimal("0"))
        if desired_notional <= Decimal("0") or worst_case_loss <= Decimal("0"):
            return {
                "approved": False,
                "action": ACTION_CONTINUE,
                "reason": "zero_notional",
                "portfolio": snapshot.as_dict(),
            }

        repel_mult = _repel_multiplier(snapshot.base_capital, snapshot.true_free_headroom)
        micro_size = max(snapshot.base_capital * MICRO_SIZE_PCT, MICRO_MIN)
        max_total_reserve = max(micro_size, snapshot.true_free_headroom)
        max_loss_budget = max(micro_size, snapshot.true_free_headroom * HEADROOM_CAP)
        approved_loss = min(worst_case_loss * repel_mult, max_loss_budget, snapshot.free_cash)
        approved_loss = min(approved_loss, max(Decimal("0"), max_total_reserve - fee_budget - slippage_budget))
        if approved_loss <= Decimal("0"):
            approved_loss = min(micro_size, snapshot.free_cash)
        approved_notional = min(desired_notional, approved_loss)
        if approved_notional <= Decimal("0"):
            return {
                "approved": False,
                "action": ACTION_CONTINUE,
                "reason": "insufficient_headroom",
                "portfolio": snapshot.as_dict(),
            }

        reservation_id = str(uuid4())
        c.execute(
            """
            INSERT INTO risk_reservations(
                reservation_id,portfolio_id,strategy_id,platform,venue_symbol,order_side,
                requested_notional,approved_notional,reserved_worst_loss,reserved_fee_budget,
                reserved_slippage,reservation_state,execution_mode,order_ref,release_reason,
                expires_at,idempotency_key,payload_json
            ) VALUES(
                ?,?,?,?,?,?,
                ?,?,?,?,?,?,
                ?,?,?,
                strftime('%Y-%m-%dT%H:%M:%fZ','now','+5 seconds'),?,?
            )
            """,
            (
                reservation_id,
                portfolio_id,
                str(request.get("strategy_id", "") or ""),
                str(request.get("platform", "") or ""),
                str(request.get("venue_symbol", "") or ""),
                str(request.get("side", "") or ""),
                _to_storage(desired_notional),
                _to_storage(approved_notional),
                _to_storage(approved_loss),
                _to_storage(fee_budget),
                _to_storage(slippage_budget),
                "RESERVED",
                str(request.get("execution_mode", request.get("runtime_mode", "")) or ""),
                "",
                "",
                idem_key,
                json.dumps(
                    {
                        "proposal_id": request.get("proposal_id", ""),
                        "reason_code": request.get("reason_code", ""),
                        "runtime_mode": request.get("runtime_mode", ""),
                    }
                ),
            ),
        )
        next_reserved_loss = snapshot.reserved_loss_budget + approved_loss
        next_reserved_fees = snapshot.reserved_fees + fee_budget + slippage_budget
        next_free_cash = max(Decimal("0"), snapshot.equity - next_reserved_loss - next_reserved_fees)
        c.execute(
            """
            UPDATE portfolio_state
            SET reserved_loss_budget=?,
                reserved_fees=?,
                free_cash=?,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE portfolio_id=?
            """,
            (
                _to_storage(next_reserved_loss),
                _to_storage(next_reserved_fees),
                _to_storage(next_free_cash),
                portfolio_id,
            ),
        )
        portfolio = _ensure_portfolio_locked(c, portfolio_id)
        return {
            "approved": True,
            "action": ACTION_CONTINUE,
            "reservation_id": reservation_id,
            "approved_notional": float(approved_notional),
            "reserved_worst_loss": float(approved_loss),
            "reserved_fee_budget": float(fee_budget),
            "reserved_slippage": float(slippage_budget),
            "repel_multiplier": float(repel_mult),
            "portfolio": portfolio.as_dict(),
        }


def _release_locked(
    c: Any,
    reservation_row: dict[str, Any],
    *,
    new_state: str,
    reason: str = "",
    order_ref: str = "",
    execution_mode: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if reservation_row.get("reservation_state") in FINAL_RESERVATION_STATES:
        return reservation_row
    portfolio = _fetch_portfolio_locked(c, str(reservation_row.get("portfolio_id", DEFAULT_PORTFOLIO_ID)) or DEFAULT_PORTFOLIO_ID)
    if portfolio is None:
        raise RuntimeError("portfolio_state row missing while releasing reservation")
    next_reserved_loss = max(
        Decimal("0"),
        _decimal(portfolio.get("reserved_loss_budget")) - _decimal(reservation_row.get("reserved_worst_loss")),
    )
    next_reserved_fees = max(
        Decimal("0"),
        _decimal(portfolio.get("reserved_fees"))
        - _decimal(reservation_row.get("reserved_fee_budget"))
        - _decimal(reservation_row.get("reserved_slippage")),
    )
    equity = _decimal(portfolio.get("equity"))
    next_free_cash = max(Decimal("0"), equity - next_reserved_loss - next_reserved_fees)
    c.execute(
        """
        UPDATE portfolio_state
        SET reserved_loss_budget=?,
            reserved_fees=?,
            free_cash=?,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
        WHERE portfolio_id=?
        """,
        (
            _to_storage(next_reserved_loss),
            _to_storage(next_reserved_fees),
            _to_storage(next_free_cash),
            portfolio["portfolio_id"],
        ),
    )
    merged_payload = _payload_load(reservation_row.get("payload_json"))
    if payload:
        merged_payload.update(payload)
    c.execute(
        """
        UPDATE risk_reservations
        SET reservation_state=?,
            execution_mode=?,
            order_ref=?,
            release_reason=?,
            payload_json=?,
            released_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
        WHERE reservation_id=?
        """,
        (
            new_state,
            execution_mode,
            order_ref,
            reason,
            json.dumps(merged_payload),
            reservation_row["reservation_id"],
        ),
    )
    row = c.execute(
        "SELECT * FROM risk_reservations WHERE reservation_id=?",
        (reservation_row["reservation_id"],),
    ).fetchone()
    return dict(row) if row is not None else reservation_row


def _mark_open_locked(
    c: Any,
    reservation_row: dict[str, Any],
    *,
    order_ref: str = "",
    execution_mode: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Transition reservation to OPEN state WITHOUT releasing portfolio headroom.
    Called when an order is placed/filled but the position is still live.
    """
    if reservation_row.get("reservation_state") in FINAL_RESERVATION_STATES:
        return reservation_row
    merged_payload = _payload_load(reservation_row.get("payload_json"))
    if payload:
        merged_payload.update(payload)
    c.execute(
        """
        UPDATE risk_reservations
        SET reservation_state='OPEN',
            execution_mode=?,
            order_ref=?,
            payload_json=?,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
        WHERE reservation_id=?
        """,
        (
            execution_mode,
            order_ref,
            json.dumps(merged_payload),
            reservation_row["reservation_id"],
        ),
    )
    row = c.execute(
        "SELECT * FROM risk_reservations WHERE reservation_id=?",
        (reservation_row["reservation_id"],),
    ).fetchone()
    return dict(row) if row is not None else reservation_row


def mark_reservation_executed(
    db_module: Any,
    reservation_id: str,
    *,
    order_ref: str = "",
    execution_mode: str = "",
    payload: dict[str, Any] | None = None,
    hold_open: bool = True,
) -> dict[str, Any]:
    """
    Mark a reservation as executed.

    hold_open=True  (default): transition to OPEN state, headroom retained until settlement.
    hold_open=False (legacy):  transition to EXECUTED state, headroom released immediately.
    """
    with _write_tx(db_module) as c:
        row = c.execute(
            "SELECT * FROM risk_reservations WHERE reservation_id=?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "reservation_not_found"}
        if hold_open:
            updated = _mark_open_locked(
                c, dict(row),
                order_ref=order_ref,
                execution_mode=execution_mode,
                payload=payload,
            )
            snapshot = _ensure_portfolio_locked(
                c, str(updated.get("portfolio_id", DEFAULT_PORTFOLIO_ID) or DEFAULT_PORTFOLIO_ID)
            )
            return {
                "ok": True,
                "reservation_id": reservation_id,
                "state": OPEN_STATE,
                "portfolio": snapshot.as_dict(),
            }
        else:
            updated = _release_locked(
                c,
                dict(row),
                new_state="EXECUTED",
                reason="executed",
                order_ref=order_ref,
                execution_mode=execution_mode,
                payload=payload,
            )
            snapshot = _ensure_portfolio_locked(
                c, str(updated.get("portfolio_id", DEFAULT_PORTFOLIO_ID) or DEFAULT_PORTFOLIO_ID)
            )
            return {
                "ok": True,
                "reservation_id": reservation_id,
                "state": "EXECUTED",
                "portfolio": snapshot.as_dict(),
            }


def release_reservation(
    db_module: Any,
    reservation_id: str,
    *,
    reason: str = "",
    order_ref: str = "",
    execution_mode: str = "",
    failed: bool = False,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with _write_tx(db_module) as c:
        row = c.execute(
            "SELECT * FROM risk_reservations WHERE reservation_id=?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "reservation_not_found"}
        updated = _release_locked(
            c,
            dict(row),
            new_state="FAILED" if failed else "RELEASED",
            reason=reason,
            order_ref=order_ref,
            execution_mode=execution_mode,
            payload=payload,
        )
        snapshot = _ensure_portfolio_locked(c, str(updated.get("portfolio_id", DEFAULT_PORTFOLIO_ID) or DEFAULT_PORTFOLIO_ID))
        return {
            "ok": True,
            "reservation_id": reservation_id,
            "state": updated["reservation_state"],
            "portfolio": snapshot.as_dict(),
        }


def settle_reservation(
    db_module: Any,
    reservation_id: str,
    *,
    pnl: float | None = None,
    settlement_price: float | None = None,
) -> dict[str, Any]:
    """
    Release headroom held for an OPEN position when it closes/settles.
    Transitions: OPEN → SETTLED (headroom returned to free_cash).
    """
    with _write_tx(db_module) as c:
        row = c.execute(
            "SELECT * FROM risk_reservations WHERE reservation_id=?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "reservation_not_found"}
        row = dict(row)
        if row.get("reservation_state") in FINAL_RESERVATION_STATES:
            return {"ok": True, "reservation_id": reservation_id, "state": row["reservation_state"], "already_final": True}
        settle_payload: dict[str, Any] = {}
        if pnl is not None:
            settle_payload["pnl"] = pnl
        if settlement_price is not None:
            settle_payload["settlement_price"] = settlement_price
        updated = _release_locked(
            c, row, new_state="SETTLED", reason="position_settled", payload=settle_payload
        )
        snapshot = _ensure_portfolio_locked(
            c, str(updated.get("portfolio_id", DEFAULT_PORTFOLIO_ID) or DEFAULT_PORTFOLIO_ID)
        )
        return {
            "ok": True,
            "reservation_id": reservation_id,
            "state": "SETTLED",
            "portfolio": snapshot.as_dict(),
        }


def settle_reservation_by_order_ref(
    db_module: Any,
    order_ref: str,
    *,
    pnl: float | None = None,
    settlement_price: float | None = None,
) -> dict[str, Any]:
    """Find the OPEN reservation linked to an order_ref and settle it."""
    row = _read_one(
        db_module,
        "SELECT * FROM risk_reservations WHERE order_ref=? AND reservation_state='OPEN' ORDER BY created_at DESC LIMIT 1",
        (order_ref,),
    )
    if row is None:
        return {"ok": False, "error": "no_open_reservation_for_order", "order_ref": order_ref}
    return settle_reservation(db_module, row["reservation_id"], pnl=pnl, settlement_price=settlement_price)


def reconcile_open_positions(
    db_module: Any,
    portfolio_id: str = DEFAULT_PORTFOLIO_ID,
) -> dict[str, Any]:
    """
    Find OPEN reservations where the linked order_lifecycle row has been settled
    (settled_at IS NOT NULL) and auto-release their headroom.
    Called from the periodic reconciliation cycle.
    """
    open_reservations = _read_all(
        db_module,
        "SELECT * FROM risk_reservations WHERE portfolio_id=? AND reservation_state='OPEN'",
        (portfolio_id,),
    )
    settled_ids: list[str] = []
    errors: list[str] = []
    for res in open_reservations:
        order_ref = str(res.get("order_ref", "") or "")
        if not order_ref:
            continue
        order_row = _read_one(
            db_module,
            "SELECT * FROM order_lifecycle WHERE order_id=? AND settled_at IS NOT NULL LIMIT 1",
            (order_ref,),
        )
        if order_row is not None:
            try:
                fill_amt = float(order_row.get("fill_amount", 0) or 0)
                notional = float(res.get("approved_notional", 0) or 0)
                pnl = fill_amt - notional if fill_amt > 0 else None
                result = settle_reservation(db_module, res["reservation_id"], pnl=pnl)
                if result.get("ok") and not result.get("already_final"):
                    settled_ids.append(str(res["reservation_id"]))
            except Exception as exc:
                errors.append(str(exc))
    return {
        "ok": True,
        "open_checked": len(open_reservations),
        "reconciled": len(settled_ids),
        "settled_ids": settled_ids,
        "errors": errors,
    }


def expire_stale_reservations(db_module: Any, portfolio_id: str = DEFAULT_PORTFOLIO_ID) -> dict[str, Any]:
    expired_ids: list[str] = []
    with _write_tx(db_module) as c:
        rows = [
            dict(row)
            for row in c.execute(
                """
                SELECT * FROM risk_reservations
                WHERE portfolio_id=?
                  AND reservation_state='RESERVED'
                  AND expires_at < strftime('%Y-%m-%dT%H:%M:%fZ','now')
                ORDER BY created_at ASC
                """,
                (portfolio_id,),
            ).fetchall()
        ]
        for row in rows:
            _release_locked(c, row, new_state="EXPIRED", reason="reservation_timeout")
            expired_ids.append(str(row["reservation_id"]))
        snapshot = _ensure_portfolio_locked(c, portfolio_id)
        return {
            "ok": True,
            "expired_count": len(expired_ids),
            "expired_ids": expired_ids,
            "portfolio": snapshot.as_dict(),
        }


def continue_after_milestone(db_module: Any, portfolio_id: str = DEFAULT_PORTFOLIO_ID) -> dict[str, Any]:
    with _write_tx(db_module) as c:
        snapshot = _ensure_portfolio_locked(c, portfolio_id)
        if not snapshot.milestone_pending:
            return {"ok": True, "action": ACTION_CONTINUE, "portfolio": snapshot.as_dict()}
        c.execute(
            """
            UPDATE portfolio_state
            SET milestone_index=milestone_index + 1,
                milestone_pending=0,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE portfolio_id=?
            """,
            (portfolio_id,),
        )
        snapshot = _ensure_portfolio_locked(c, portfolio_id)
        return {"ok": True, "action": ACTION_CONTINUE, "portfolio": snapshot.as_dict()}


def set_manual_pause(
    db_module: Any,
    paused: bool,
    *,
    portfolio_id: str = DEFAULT_PORTFOLIO_ID,
) -> dict[str, Any]:
    with _write_tx(db_module) as c:
        _ensure_portfolio_locked(c, portfolio_id)
        c.execute(
            """
            UPDATE portfolio_state
            SET manual_paused=?,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE portfolio_id=?
            """,
            (1 if paused else 0, portfolio_id),
        )
        snapshot = _ensure_portfolio_locked(c, portfolio_id)
        return {
            "ok": True,
            "action": ACTION_MANUAL_PAUSE if paused else ACTION_CONTINUE,
            "portfolio": snapshot.as_dict(),
        }


def maybe_auto_sweep(
    db_module: Any,
    vault: Any,
    *,
    portfolio_id: str = DEFAULT_PORTFOLIO_ID,
    platform: str = "portfolio",
) -> dict[str, Any]:
    with _write_tx(db_module) as c:
        snapshot = _ensure_portfolio_locked(c, portfolio_id)
        sweepable = compute_sweepable_cash(snapshot)
        min_sweep = max(snapshot.base_capital * MIN_SWEEP_PCT, Decimal("1"))
        if sweepable < min_sweep:
            return {
                "ok": True,
                "action": "not_enough_sweepable_cash",
                "amount": float(sweepable),
                "portfolio": snapshot.as_dict(),
            }
        movement_id = str(uuid4())
        idem = f"forcefield-sweep:{platform}:{movement_id}"
        c.execute(
            """
            INSERT INTO cash_movements(
                movement_id,portfolio_id,platform,movement_type,amount,
                idempotency_key,movement_state,payload_json
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                movement_id,
                portfolio_id,
                platform,
                "VAULT_SWEEP",
                _to_storage(sweepable),
                idem,
                "REQUESTED",
                json.dumps({"trigger_multiple": float(snapshot.current_multiple)}),
            ),
        )
    locked_amount = Decimal("0")
    try:
        locked_amount = _decimal(vault.lock("forcefield", float(sweepable), reason="forcefield_sweep"))
    except Exception as exc:
        with _write_tx(db_module) as c:
            failed_payload = json.dumps({"error": str(exc)})
            c.execute(
                """
                UPDATE cash_movements
                SET movement_state='FAILED',
                    payload_json=?
                WHERE movement_id=?
                """,
                (failed_payload, movement_id),
            )
        return {"ok": False, "action": "sweep_failed", "error": str(exc)}
    with _write_tx(db_module) as c:
        c.execute(
            """
            UPDATE cash_movements
            SET movement_state='CONFIRMED',
                confirmed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE movement_id=?
            """,
            (movement_id,),
        )
        snapshot = _ensure_portfolio_locked(c, portfolio_id)
        return {
            "ok": True,
            "action": "swept",
            "movement_id": movement_id,
            "amount": float(locked_amount),
            "portfolio": snapshot.as_dict(),
        }


def get_status(
    db_module: Any,
    *,
    portfolio_id: str = DEFAULT_PORTFOLIO_ID,
    recent_limit: int = 20,
) -> dict[str, Any]:
    sync_portfolio_state(db_module, portfolio_id=portfolio_id)
    portfolio_row = _read_one(
        db_module,
        "SELECT * FROM portfolio_state WHERE portfolio_id=?",
        (portfolio_id,),
    )
    snapshot = _snapshot_from_row(portfolio_row or {"portfolio_id": portfolio_id})
    reservation_rows = _read_all(
        db_module,
        """
        SELECT * FROM risk_reservations
        WHERE portfolio_id=?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (portfolio_id, recent_limit),
    )
    for row in reservation_rows:
        row["payload"] = _payload_load(row.get("payload_json"))
    movement_rows = _read_all(
        db_module,
        """
        SELECT * FROM cash_movements
        WHERE portfolio_id=?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (portfolio_id, recent_limit),
    )
    for row in movement_rows:
        row["payload"] = _payload_load(row.get("payload_json"))
    summary = {
        "total": len(reservation_rows),
        "active": sum(1 for row in reservation_rows if row.get("reservation_state") == "RESERVED"),
        "open": sum(1 for row in reservation_rows if row.get("reservation_state") == "OPEN"),
        "executed": sum(1 for row in reservation_rows if row.get("reservation_state") == "EXECUTED"),
        "settled": sum(1 for row in reservation_rows if row.get("reservation_state") == "SETTLED"),
        "failed": sum(1 for row in reservation_rows if row.get("reservation_state") == "FAILED"),
        "expired": sum(1 for row in reservation_rows if row.get("reservation_state") == "EXPIRED"),
        "released": sum(1 for row in reservation_rows if row.get("reservation_state") == "RELEASED"),
    }
    sweepable = compute_sweepable_cash(snapshot)
    open_position_count = 0
    try:
        if hasattr(db_module, "get_open_position_count"):
            open_position_count = db_module.get_open_position_count()
    except Exception:
        pass
    return {
        "ok": True,
        "action": _action_from_snapshot(snapshot),
        "portfolio": snapshot.as_dict(),
        "milestones": {
            "targets": [float(value) for value in MILESTONES],
            "current_index": snapshot.milestone_index,
            "next_target_multiple": float(snapshot.next_milestone),
            "milestone_pending": snapshot.milestone_pending,
            "target_hit": snapshot.target_hit,
        },
        "reservations": summary,
        "open_position_count": open_position_count,
        "recent_reservations": reservation_rows,
        "recent_cash_movements": movement_rows,
        "sweepable_cash": float(sweepable),
    }
