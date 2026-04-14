"""
Telegram command handler — lets the operator control DeG£N$ via Telegram.
Commands: /status /pause /resume /balance /bots /vault /help /pnl /kill
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)


class TelegramCommandHandler:
    def __init__(self, bot_token: str, allowed_chat_ids: list[str]):
        self.bot_token       = bot_token
        self.allowed_chat_ids = set(str(cid) for cid in allowed_chat_ids)
        self._offset         = 0
        self._running        = False
        self._thread: threading.Thread | None = None
        self._commands: dict[str, Callable] = {}
        self._status_fn: Callable | None    = None

    def register_command(self, cmd: str, handler: Callable) -> None:
        self._commands[cmd.lstrip("/")] = handler

    def set_status_provider(self, fn: Callable) -> None:
        self._status_fn = fn

    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(
            target=self._poll_loop, daemon=True, name="tg_cmd_handler"
        )
        self._thread.start()
        logger.info("Telegram command handler started")

    def stop(self) -> None:
        self._running = False

    # ── Internal ──────────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while self._running:
            try:
                self._process_updates()
            except Exception as exc:
                logger.error(f"Telegram poll error: {exc}")
            time.sleep(2)

    def _process_updates(self) -> None:
        url    = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        params = {"offset": self._offset, "timeout": 10}
        try:
            resp = requests.get(url, params=params, timeout=15)
            data = resp.json()
            if not data.get("ok"):
                return
            for update in data.get("result", []):
                self._offset = update["update_id"] + 1
                msg     = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text    = str(msg.get("text", "")).strip()
                if chat_id not in self.allowed_chat_ids:
                    continue
                if text.startswith("/"):
                    self._handle_command(chat_id, text)
        except Exception as exc:
            logger.error(f"Telegram update fetch error: {exc}")

    def _handle_command(self, chat_id: str, text: str) -> None:
        parts = text.split()
        cmd   = parts[0].lstrip("/").lower()
        args  = parts[1:]

        if cmd == "help":
            self._reply(
                chat_id,
                "🤖 <b>DeG£N$ Commands</b>\n"
                "/status — System overview\n"
                "/bots — All bot statuses\n"
                "/balance — Current balances\n"
                "/vault — Vault locked profits\n"
                "/pnl — Today's P&amp;L\n"
                "/help — This menu",
            )
            return

        if cmd in ("status", "bots", "balance") and self._status_fn:
            try:
                result = self._status_fn(args)
                self._reply(chat_id, str(result))
            except Exception as exc:
                self._reply(chat_id, f"❌ Status error: {exc}")
            return

        handler = self._commands.get(cmd)
        if handler:
            try:
                result = handler(args)
                self._reply(chat_id, str(result))
            except Exception as exc:
                self._reply(chat_id, f"❌ Error: {exc}")
        else:
            self._reply(chat_id, f"Unknown command: /{cmd}\nType /help for available commands")

    def _reply(self, chat_id: str, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            requests.post(
                url,
                json={"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML"},
                timeout=10,
            )
        except Exception as exc:
            logger.error(f"Telegram reply error: {exc}")
