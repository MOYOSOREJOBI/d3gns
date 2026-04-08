"""
FastAPI server – 6-bot command center backend.

Features:
  • Password auth (token-based, 24h expiry)
  • SQLite persistence (trades, equity, events, notes, wallet, notifications)
  • SMS alerts via Twilio (milestones, halts, targets)
  • Monte Carlo projections endpoint
  • Full CRUD for notes
  • Wallet tracking with simulated deposits
  • Settings management

Default password: Joseph992127!!!
"""

import os, hashlib, secrets, time, random, logging, threading, collections, json
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Set

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Body, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# ── Stake client: save original real functions before any patching ────────────
import paper_stake, stake_client as _sc

# Stash real functions under private names so we can restore them later
_sc._real_dice_roll   = _sc.dice_roll
_sc._real_limbo_game  = _sc.limbo_game
_sc._real_mines_play  = _sc.mines_play
_sc._real_get_balance = _sc.get_balance

import database as db
db.init_db()   # must run before any logging handler touches the DB
import notifier
from config           import BOT_INITIAL_BANK, TARGET_MULTIPLIER, BET_DELAY_SECONDS
import config as _cfg
from risk_manager     import RiskManager
from stake_strategies import make_strategy
from paper_polymarket    import PaperPolymarketBot
from btc_momentum        import BtcMomentumBot
from intra_arb           import IntraArbBot
from resolution_sniper   import ResolutionSniperBot
from volume_spike        import VolumeSpikeBot

_POLY_STRATEGY_MAP = {
    "edge_scanner"       : PaperPolymarketBot,
    "btc_momentum"       : BtcMomentumBot,
    "intra_arb"          : IntraArbBot,
    "resolution_sniper"  : ResolutionSniperBot,
    "volume_spike"       : VolumeSpikeBot,
}

_cfg.BET_DELAY_SECONDS = max(BET_DELAY_SECONDS / 10.0, 0.05)

# ── Auth ──────────────────────────────────────────────────────────────────────
AUTH_PASSWORD   = os.getenv("AUTH_PASSWORD", "changeme")
_STORED_HASH    = hashlib.sha256(AUTH_PASSWORD.encode()).hexdigest()
_VALID_TOKENS   : dict[str, float] = {}   # token → expiry epoch

def _hash(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()

def _check_token(req: Request):
    token = req.headers.get("Authorization","").replace("Bearer ","").strip()
    if not token or _VALID_TOKENS.get(token, 0) < time.time():
        raise HTTPException(status_code=401, detail="Unauthorized")
    return token

# ── Logging ───────────────────────────────────────────────────────────────────
_log_dir  = os.environ.get("LOG_DIR", ".")
_log_file = os.path.join(_log_dir, "server.log")
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s | %(levelname)-7s | %(message)s",
    handlers= [logging.StreamHandler(), logging.FileHandler(_log_file, encoding="utf-8")],
)
logger = logging.getLogger(__name__)

# ── Stake mode switching ──────────────────────────────────────────────────────
import stake_client as _sc

def _patch_stake_paper():
    _sc.dice_roll   = paper_stake.dice_roll
    _sc.limbo_game  = paper_stake.limbo_game
    _sc.mines_play  = paper_stake.mines_play
    _sc.get_balance = paper_stake.get_balance
    logger.info("Stake: PAPER mode")

def _patch_stake_live():
    _sc.dice_roll   = _sc._real_dice_roll
    _sc.limbo_game  = _sc._real_limbo_game
    _sc.mines_play  = _sc._real_mines_play
    _sc.get_balance = _sc._real_get_balance
    logger.info("Stake: LIVE mode — real API calls active")

_patch_stake_paper()   # default until credentials loaded

# Immediately switch to live if config.py already has a token
import config as _cfg_init
if _cfg_init.STAKE_API_TOKEN:
    import stake_client as _sc_init
    _sc_init.HEADERS["x-access-token"] = _cfg_init.STAKE_API_TOKEN
    _patch_stake_live()
    logger.info("Stake: LIVE from config.py token")

# ── Tor embedded VPN ─────────────────────────────────────────────────────────
import subprocess, shutil, re as _re

_tor_proc    = None
_tor_status  = "off"        # off | not_installed | starting | ready | failed
_tor_ip      = None
_tor_country = None

def _tor_socks():
    return {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}

def _fetch_tor_ip():
    global _tor_ip, _tor_country
    try:
        import requests as _req
        r = _req.get("https://ipapi.co/json/", proxies=_tor_socks(), timeout=20)
        d = r.json()
        _tor_ip      = d.get("ip", "?")
        _tor_country = d.get("country_name", "?")
        logger.info(f"Tor exit: {_tor_ip} ({_tor_country})")
    except Exception as e:
        logger.warning(f"Tor IP check failed: {e}")

def _find_bin(name):
    found = shutil.which(name)
    if found: return found
    for p in [f"/usr/local/bin/{name}", f"/opt/homebrew/bin/{name}"]:
        if os.path.isfile(p): return p
    return None

def _start_tor():
    global _tor_proc, _tor_status
    if _tor_status == "ready":
        return
    tor_bin = _find_bin("tor")
    if not tor_bin:
        _tor_status = "not_installed"
        logger.info("Tor not installed — run: brew install tor")
        return
    if _tor_proc and _tor_proc.poll() is None:
        _tor_status = "ready"
        return
    _tor_status = "starting"
    try:
        import socket as _sock
        _tor_proc = subprocess.Popen(
            [tor_bin, "--SocksPort", "9050",
             "--DataDirectory", "/tmp/degens_tor",
             "--Log", "notice stderr"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        for _ in range(60):
            time.sleep(1)
            try:
                s = _sock.socket()
                s.settimeout(1)
                s.connect(("127.0.0.1", 9050))
                s.close()
                _tor_status = "ready"
                db.set_setting("proxy_host", "127.0.0.1")
                db.set_setting("proxy_port", "9050")
                logger.info("Tor SOCKS5 ready — all restricted platforms now routed through Tor")
                threading.Thread(target=_fetch_tor_ip, daemon=True).start()
                return
            except Exception:
                pass
        _tor_status = "failed"
        logger.warning("Tor started but SOCKS5 port never opened")
    except Exception as e:
        _tor_status = "failed"
        logger.error(f"Tor start error: {e}")

def _renew_tor_circuit():
    """Send NEWNYM signal to Tor to get a fresh circuit (new exit IP)."""
    global _tor_ip, _tor_country
    try:
        import socket as _sock
        ctl = _sock.socket()
        ctl.connect(("127.0.0.1", 9051))
        ctl.sendall(b"AUTHENTICATE \"\"\r\nSIGNAL NEWNYM\r\nQUIT\r\n")
        ctl.close()
        _tor_ip = None; _tor_country = None
        threading.Thread(target=_fetch_tor_ip, daemon=True).start()
        return True
    except Exception:
        return False

# ── localtunnel remote access (free, custom subdomain, no account) ────────────
_tunnel_proc   = None
_tunnel_url    = None
_tunnel_status = "off"   # off | starting | ready | failed | not_installed
_TUNNEL_SLUG   = "d3gns"   # → https://d3gns.loca.lt

def _start_tunnel():
    global _tunnel_proc, _tunnel_url, _tunnel_status
    npx = _find_bin("npx")
    if not npx:
        _tunnel_status = "not_installed"
        return
    if _tunnel_proc and _tunnel_proc.poll() is None:
        return
    _tunnel_status = "starting"
    try:
        _tunnel_proc = subprocess.Popen(
            [npx, "localtunnel", "--port", "8000", "--subdomain", _TUNNEL_SLUG],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, bufsize=1
        )
        def _read():
            global _tunnel_url, _tunnel_status
            for line in _tunnel_proc.stdout:
                m = _re.search(r"https://[^\s]+loca\.lt", line)
                if m:
                    _tunnel_url = m.group(0)
                    _tunnel_status = "ready"
                    db.set_setting("tunnel_url", _tunnel_url)
                    logger.info(f"Remote tunnel: {_tunnel_url}")
                    return
            if _tunnel_status == "starting":
                _tunnel_status = "failed"
        threading.Thread(target=_read, daemon=True).start()
    except Exception as e:
        _tunnel_status = "failed"
        logger.error(f"Tunnel error: {e}")

# ── Shared state ──────────────────────────────────────────────────────────────
_stop_event      = threading.Event()
_rms             : dict[str, RiskManager] = {}
_equity_history  : list[dict]             = []
_log_entries     = collections.deque(maxlen=30)
_start_wall      : float | None           = None
_running         = False
_lock            = threading.Lock()
_tick            = 0
_initial_total   = 0.0
_bet_scales      : dict[str, float]       = {}   # per-bot multiplier
_strategy_modes  : dict[str, str]         = {}   # per-bot preset name
_goal_target     : float                  = 6000.0  # portfolio goal $
_recent_trade_nets: dict[str, collections.deque] = {}
_auto_manage_state: dict[str, dict] = {}
_poly_runtime    : dict[str, dict]        = {}

_STRATEGY_SCALES = {"conservative": 0.5, "balanced": 1.0, "aggressive": 2.0, "turbo": 3.5}

# ── WebSocket connection manager ─────────────────────────────────────────────
class _WSManager:
    def __init__(self):
        self._clients: Set[WebSocket] = set()
        self._lock = threading.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        with self._lock:
            self._clients.add(ws)

    def disconnect(self, ws: WebSocket):
        with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, data: dict):
        msg = json.dumps(data)
        dead = set()
        with self._lock:
            clients = set(self._clients)
        for ws in clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        with self._lock:
            self._clients -= dead

_ws_manager = _WSManager()

def _broadcast_sync(data: dict):
    """Fire-and-forget broadcast from a sync thread."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_ws_manager.broadcast(data), loop)
    except Exception:
        pass


def _compute_window_stats(nets, size: int):
    sample = list(nets)[-size:]
    count = len(sample)
    wins = sum(1 for n in sample if n > 0)
    pnl = sum(sample)
    avg_abs = sum(abs(n) for n in sample) / max(count, 1)
    denom = avg_abs * count
    roi_pct = (pnl / denom * 100) if denom > 0 else 0.0
    return {
        "count": count,
        "wins": wins,
        "losses": count - wins,
        "pnl": round(pnl, 4),
        "win_rate": round((wins / max(count, 1)) * 100, 1),
        "roi_pct": round(roi_pct, 2),
    }


def _rolling_windows(bot_id: str):
    nets = _recent_trade_nets.get(bot_id, collections.deque(maxlen=100))
    return {
        "w25": _compute_window_stats(nets, 25),
        "w100": _compute_window_stats(nets, 100),
    }


def _seed_recent_trade_state():
    _recent_trade_nets.clear()
    _auto_manage_state.clear()
    for spec in _active_bot_specs():
        bid = spec["id"]
        dq = collections.deque(maxlen=100)
        rows, _ = db.get_trades(bot_id=bid, limit=100, offset=0)
        for row in reversed(rows):
            dq.append(float(row.get("net", 0) or 0))
        _recent_trade_nets[bid] = dq
        _auto_manage_state[bid] = {
            "last_downscale_count": 0,
            "downscales": 0,
            "last_reason": "",
        }


def _maybe_auto_downscale(bot_id: str):
    rm = _rms.get(bot_id)
    if not rm:
        return
    windows = _rolling_windows(bot_id)
    recent = windows["w25"]
    state = _auto_manage_state.setdefault(bot_id, {
        "last_downscale_count": 0,
        "downscales": 0,
        "last_reason": "",
    })
    if recent["count"] < 25:
        return
    if recent["count"] - state["last_downscale_count"] < 25:
        return

    scale = _bet_scales.get(bot_id, 1.0)
    trigger_reason = None
    if recent["roi_pct"] <= -12:
        trigger_reason = f"rolling 25 ROI {recent['roi_pct']}%"
    elif recent["win_rate"] <= 42:
        trigger_reason = f"rolling 25 win rate {recent['win_rate']}%"
    elif windows["w100"]["count"] >= 60 and windows["w100"]["roi_pct"] <= -8:
        trigger_reason = f"rolling 100 ROI {windows['w100']['roi_pct']}%"

    if not trigger_reason or scale <= 0.35:
        return

    new_scale = max(0.35, round(scale * 0.85, 2))
    if new_scale >= scale:
        return

    _apply_scale(bot_id, new_scale)
    state["last_downscale_count"] = recent["count"]
    state["downscales"] += 1
    state["last_reason"] = trigger_reason
    msg = f"{bot_id} auto-downscaled {scale:.2f}x -> {new_scale:.2f}x ({trigger_reason})"
    logger.info(msg)
    db.save_event("warn", msg, bot_id)
    _broadcast_sync({
        "type": "auto_downscale",
        "bot_id": bot_id,
        "from_scale": round(scale, 2),
        "to_scale": round(new_scale, 2),
        "reason": trigger_reason,
    })


def _capture_poly_runtime(bot_id: str, strategy_type: str, bot=None, error: str = ""):
    base = {
        "strategy": strategy_type,
        "execution_mode": "paper",
        "data_mode": "real_market_data",
        "open_positions": 0,
        "opportunity_count": 0,
        "best_edge": None,
        "best_question": "",
        "last_scan_ts": 0.0,
        "opportunities": [],
        "last_cycle_ts": time.time(),
        "last_error": error or "",
    }
    if bot and hasattr(bot, "snapshot"):
        try:
            base.update(bot.snapshot() or {})
        except Exception as exc:
            base["last_error"] = f"snapshot failed: {exc}"
    elif error:
        base["last_error"] = error
    _poly_runtime[bot_id] = base
# Per-bot user configuration: start_amount, target_amount, floor_amount
_bot_configs: dict[str, dict] = {}

BOT_SPECS = [
    # (bot_id, game/kind, platform_label, strategy_type, description)
    ("bot1_dice",      "dice",  "Stake Dice",   "dice",             "Paroli press on wins"),
    ("bot2_limbo",     "limbo", "Stake Limbo",  "limbo",            "Paroli + 10x big-shot"),
    ("bot3_mines",     "mines", "Stake Mines",  "mines",            "Progressive mine picks"),
    ("bot4_poly",      "poly",  "Polymarket",   "edge_scanner",     "Multi-factor Kelly edge"),
    ("bot5_poly",      "poly",  "Polymarket",   "edge_scanner",     "Execution-truthful EV"),
    ("bot6_poly",      "poly",  "Polymarket",   "edge_scanner",     "Ask-aware order flow"),
    ("bot7_momentum",  "poly",  "Polymarket",   "btc_momentum",     "Binance BTC price lag arb"),
    ("bot8_arb",       "poly",  "Polymarket",   "intra_arb",        "Sum-to-one arb + mid dev"),
    ("bot9_sniper",    "poly",  "Polymarket",   "resolution_sniper","Near-expiry convergence"),
    ("bot10_volume",   "poly",  "Polymarket",   "volume_spike",     "Volume surge momentum"),
]
BOT_COLORS = {
    "bot1_dice"    : "#5eb8ff",
    "bot2_limbo"   : "#b87dff",
    "bot3_mines"   : "#4ade80",
    "bot4_poly"    : "#3b82f6",
    "bot5_poly"    : "#f87171",
    "bot6_poly"    : "#fbbf24",
    "bot7_momentum": "#14b8a6",
    "bot8_arb"     : "#a78bfa",
    "bot9_sniper"  : "#fb7185",
    "bot10_volume" : "#f97316",
}
BOT_INDEX = {spec[0]: i + 1 for i, spec in enumerate(BOT_SPECS)}
BOT_SPEC_MAP = {
    bid: {
        "id"          : bid,
        "kind"        : kind,
        "platform"    : "stake" if kind in ("dice", "limbo", "mines") else "poly",
        "strategy"    : strategy_type,
        "description" : desc,
        "display_name": bid,
        "color"       : BOT_COLORS.get(bid, "#94a3b8"),
        "equity_key"  : f"bot{BOT_INDEX[bid]}",
    }
    for bid, kind, _plat, strategy_type, desc in BOT_SPECS
}


def _load_bot_registry():
    try:
        raw = db.get_setting("bot_registry", "[]")
        data = json.loads(raw) if raw else []
    except Exception:
        data = []

    merged = []
    existing = {item.get("id"): item for item in data if isinstance(item, dict) and item.get("id") in BOT_SPEC_MAP}
    for bid, spec in BOT_SPEC_MAP.items():
        row = existing.get(bid, {})
        merged.append({
            **spec,
            "enabled": bool(row.get("enabled", True)),
            "display_name": str(row.get("display_name") or spec["display_name"]),
        })
    return merged


def _save_bot_registry(registry):
    trimmed = [
        {"id": item["id"], "enabled": bool(item.get("enabled", True)), "display_name": item.get("display_name") or item["id"]}
        for item in registry
        if item.get("id") in BOT_SPEC_MAP
    ]
    db.set_setting("bot_registry", json.dumps(trimmed))


def _active_bot_specs():
    return [item for item in _load_bot_registry() if item["enabled"]]

# ── Log capture ───────────────────────────────────────────────────────────────
class _Capture(logging.Handler):
    def emit(self, record):
        msg   = self.format(record)
        clean = msg.split("|",2)[-1].strip() if "|" in msg else msg
        lm    = clean.lower()
        t     = ("halt" if "hard stop" in lm or "halted" in lm
                 else "fill" if any(x in lm for x in ["locked","profit","filled","withdraw","10x","target"])
                 else "warn" if "recovery" in lm or "warn" in lm
                 else "info")
        _log_entries.appendleft({"time": datetime.now().strftime("%H:%M:%S"), "msg": clean[:120], "type": t})
        db.save_event(t, clean[:200])

logging.getLogger().addHandler(_Capture())

# ── Bot loops ─────────────────────────────────────────────────────────────────

def _handle_result(bot_id: str, game: str, net: float, result: dict):
    """Common post-bet handling: DB writes, WS broadcast, notifications."""
    rm = _rms[bot_id]
    db.save_trade(bot_id, game, rm.phase, abs(net) if net else 0,
                  net > 0, net, rm.current_bankroll)
    _recent_trade_nets.setdefault(bot_id, collections.deque(maxlen=100)).append(float(net or 0))
    _maybe_auto_downscale(bot_id)
    if result.get("withdraw", 0) > 0:
        db.save_wallet_tx("profit_lock", result["withdraw"],
                          platform=game, note=f"{bot_id} profit lock")
        db.save_vault_lock(bot_id, result["withdraw"], "ratchet", rm.current_bankroll)
    if result.get("circuit_breaker"):
        db.save_circuit_breaker(
            bot_id, result.get("cb_reason", "unknown"),
            result.get("cb_duration_s", 0), rm.current_bankroll, rm.phase
        )
    if result.get("phase_changed"):
        db.save_phase_transition(
            bot_id, result.get("prev_phase", "?"), rm.phase,
            rm.current_bankroll, result.get("phase_reason")
        )
    if result["action"] == "TARGET_HIT":
        notifier.notify_target(bot_id, rm.progress)
    _broadcast_sync({
        "type"    : "bet",
        "bot_id"  : bot_id,
        "net"     : round(net, 4),
        "bankroll": round(rm.current_bankroll, 4),
        "phase"   : rm.phase,
        "action"  : result["action"],
    })


def _stake_loop(bot_id: str, game: str):
    rm       = _rms[bot_id]
    strategy = make_strategy(game, rm)
    logger.info(f"[{bot_id}] started game={game}")
    while not _stop_event.is_set():
        # Only run when live Stake credentials are active — never simulate
        if _sc.dice_roll is paper_stake.dice_roll:
            time.sleep(2.0)
            continue
        if rm.is_cooling_down:
            time.sleep(0.5)
            continue
        try:
            net    = strategy.run_one_bet()
            result = rm.record_bet_result(net)
            _handle_result(bot_id, game, net, result)
        except Exception as e:
            logger.error(f"[{bot_id}] {e}")
            time.sleep(0.5)


def _make_clob_client():
    """Create a live Polymarket CLOB client if credentials are set. Returns None otherwise."""
    import config as _c
    if not (_c.POLY_PRIVATE_KEY and "••" not in _c.POLY_PRIVATE_KEY):
        return None
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
        creds = ApiCreds(
            api_key        = _c.POLY_API_KEY,
            api_secret     = _c.POLY_API_SECRET,
            api_passphrase = _c.POLY_API_PASSPHRASE,
        )
        client = ClobClient(
            host     = _c.POLY_HOST,
            chain_id = _c.POLY_CHAIN_ID,
            key      = _c.POLY_PRIVATE_KEY,
            creds    = creds,
        )
        logger.info("Polymarket CLOB client initialised — LIVE orders enabled")
        return client
    except Exception as e:
        logger.error(f"CLOB client init failed: {e}")
        return None


def _poly_loop(bot_id: str, strategy_type: str = "edge_scanner"):
    rm          = _rms[bot_id]
    live_client = _make_clob_client()
    bot_class   = _POLY_STRATEGY_MAP.get(strategy_type, PaperPolymarketBot)
    bot         = bot_class(rm, live_client=live_client)
    scan_delay = max(_cfg.BET_DELAY_SECONDS * 12, 0.4)
    _capture_poly_runtime(bot_id, strategy_type, bot)
    logger.info(f"[{bot_id}] polymarket started strategy={strategy_type}")
    while not _stop_event.is_set():
        if rm.is_cooling_down:
            time.sleep(0.5)
            continue
        try:
            net = bot.run_one_cycle()
            _capture_poly_runtime(bot_id, strategy_type, bot)
            # Only record fills when we have real credentials — never fake fills
            if net != 0.0 and live_client is not None:
                result = rm.record_bet_result(net)
                _handle_result(bot_id, strategy_type, net, result)
        except Exception as e:
            logger.error(f"[{bot_id}] {e}")
            _capture_poly_runtime(bot_id, strategy_type, bot, str(e))
        for _ in range(int(scan_delay / 0.1)):
            if _stop_event.is_set(): break
            time.sleep(0.1)


def _snapshot_loop():
    global _tick
    labels = [f"{h}:{m:02d}" for h in range(24) for m in (0, 30)]
    idx    = 0
    while not _stop_event.is_set():
        time.sleep(max(1.0 / 10.0, 0.2))
        with _lock:
            progress  = {bid: rm.progress for bid, rm in _rms.items()}
            portfolio = sum(progress.values())
            label     = labels[idx % len(labels)]
            snap      = {bid: round(v, 2) for bid, v in progress.items()}
            snap["portfolio"] = round(portfolio, 2)
            snap["label"]     = label
            if len(_equity_history) >= 120:
                _equity_history.pop(0)
            _equity_history.append(snap)
            idx   += 1
            _tick += 1
        # Persist snapshot every 5 ticks
        if _tick % 5 == 0:
            db.save_snapshot(label, progress, portfolio)
        # Check $100 milestone every 10 ticks
        if _tick % 10 == 0:
            notifier.check_milestone(portfolio, _initial_total)
        # Broadcast equity update over WS every tick
        _broadcast_sync({"type": "equity", "snap": snap})


# ── Start / stop ──────────────────────────────────────────────────────────────

def _apply_scale(bid: str, scale: float):
    _bet_scales[bid] = scale
    if _rms.get(bid):
        _rms[bid].bet_scale = scale

def _start_bots():
    global _start_wall, _running, _initial_total
    active_specs = _active_bot_specs()
    _stop_event.clear()
    _rms.clear()
    _equity_history.clear()
    _poly_runtime.clear()
    _seed_recent_trade_state()
    for spec in active_specs:
        bid  = spec["id"]
        cfg  = _bot_configs.get(bid, {})
        start  = float(cfg.get("start_amount",  BOT_INITIAL_BANK))
        target = float(cfg.get("target_amount", start * 5.0))
        floor  = float(cfg.get("floor_amount",  start * 0.40))
        _rms[bid] = RiskManager(bid, start, target, floor)
        scale = _STRATEGY_SCALES.get(_strategy_modes.get(bid, "balanced"), 1.0)
        _rms[bid].bet_scale = scale
        _bet_scales[bid]    = scale
        if bid not in _strategy_modes:
            _strategy_modes[bid] = "balanced"
    _initial_total = len(_rms) * BOT_INITIAL_BANK

    threads = []
    for spec in active_specs:
        bid          = spec["id"]
        kind         = spec["kind"]
        strategy_type = spec.get("strategy", "edge_scanner")
        if kind == "poly":
            fn = (lambda b=bid, s=strategy_type: _poly_loop(b, s))
        else:
            fn = (lambda b=bid, g=kind: _stake_loop(b, g))
        threads.append(threading.Thread(target=fn, name=bid, daemon=True))
    threads.append(threading.Thread(target=_snapshot_loop, name="snapshot", daemon=True))
    for t in threads: t.start()
    _start_wall = time.time()
    _running    = True
    logger.info("Started %s active bots.", len(active_specs))

def _stop_bots():
    global _running
    _stop_event.set()
    _running = False


# ── FastAPI app ───────────────────────────────────────────────────────────────

def _load_credentials_from_db():
    """Inject API credentials stored in DB settings into live config + client modules."""
    import stake_client, config as _c
    s = db.get_all_settings()

    stake_tok = s.get("stake_api_token", "").strip()
    if stake_tok and "••" not in stake_tok:
        _c.STAKE_API_TOKEN = stake_tok
        stake_client.HEADERS["x-access-token"] = stake_tok
        _patch_stake_live()
        logger.info("Stake credentials loaded from DB — LIVE mode active.")
    else:
        _patch_stake_paper()
        logger.info("No Stake token — running PAPER mode.")

    poly_pk  = s.get("poly_private_key",   "").strip()
    poly_ak  = s.get("poly_api_key",        "").strip()
    poly_as  = s.get("poly_api_secret",     "").strip()
    poly_app = s.get("poly_api_passphrase", "").strip()
    if poly_pk and "••" not in poly_pk:
        _c.POLY_PRIVATE_KEY    = poly_pk
        _c.POLY_API_KEY        = poly_ak
        _c.POLY_API_SECRET     = poly_as
        _c.POLY_API_PASSPHRASE = poly_app
        logger.info("Polymarket credentials loaded from DB.")

    twilio_sid   = s.get("twilio_account_sid",  "").strip()
    twilio_token = s.get("twilio_auth_token",    "").strip()
    twilio_from  = s.get("twilio_from_number",   "").strip()
    notify_phone = s.get("notify_phone",         "").strip()
    if twilio_sid and "••" not in twilio_token:
        import notifier
        notifier.TWILIO_SID   = twilio_sid
        notifier.TWILIO_TOKEN = twilio_token
        notifier.TWILIO_FROM  = twilio_from
        notifier.NOTIFY_PHONE = notify_phone
        logger.info("Twilio credentials loaded from DB.")


def _keep_alive():
    """Ping self every 14 min so Render free tier never sleeps."""
    import requests as _req
    while True:
        time.sleep(14 * 60)
        try:
            _req.get("http://localhost:8000/api/ping", timeout=10)
        except Exception:
            pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    _load_credentials_from_db()
    _load_bot_configs()
    _start_bots()
    threading.Thread(target=_start_tor,    daemon=True).start()
    threading.Thread(target=_start_tunnel, daemon=True).start()
    threading.Thread(target=_keep_alive,   daemon=True).start()
    yield
    _stop_bots()
    if _tor_proc:    _tor_proc.terminate()
    if _tunnel_proc: _tunnel_proc.terminate()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def _bypass_tunnel(request, call_next):
    response = await call_next(request)
    response.headers["bypass-tunnel-reminder"] = "true"
    return response

# ── Auth endpoints ─────────────────────────────────────────────────────────────

@app.post("/api/auth")
async def auth(req: Request, body: dict = Body(...)):
    pwd       = body.get("password", "")
    device_id = body.get("device_id", "")
    ua        = req.headers.get("user-agent", "")
    if _hash(pwd) != _STORED_HASH:
        raise HTTPException(status_code=401, detail="Invalid password")
    if device_id:
        device, is_new = db.register_device(device_id, ua)
        # Auto-approve: correct password = trusted. Device log kept for audit only.
        if not device["approved"]:
            db.approve_device(device_id)
    token = secrets.token_hex(32)
    _VALID_TOKENS[token] = time.time() + 86400 * 30  # 30 days
    return {"ok": True, "token": token}

@app.post("/api/auth/change-password")
async def change_password(req: Request, body: dict = Body(...)):
    _check_token(req)
    global _STORED_HASH, AUTH_PASSWORD
    new_pwd = body.get("new_password", "")
    if len(new_pwd) < 6:
        raise HTTPException(400, "Password too short")
    _STORED_HASH = _hash(new_pwd)
    return {"ok": True}

# ── Status endpoints (all require auth) ───────────────────────────────────────

@app.get("/api/ping")
async def ping(): return {"ok": True}

@app.get("/api/status")
async def get_status(req: Request):
    _check_token(req)
    bots = []
    for spec in _active_bot_specs():
        bid = spec["id"]
        rm = _rms.get(bid)
        if not rm:
            continue
        s = rm.status()
        windows = _rolling_windows(bid)
        auto_state = _auto_manage_state.get(bid, {})
        runtime = _poly_runtime.get(bid, {}) if spec["platform"] == "poly" else {}
        status = ("cooling"  if rm.is_cooling_down else
                  "floor"    if rm.phase == "floor" else
                  "pushing"  if rm.phase in ("turbo", "aggressive") else "live")
        bots.append({
            "id": bid,
            "name": spec["display_name"],
            "platform": spec["platform"],
            "strategy": spec["strategy"],
            "equityKey": spec["equity_key"],
            "bankroll":     round(rm.current_bankroll, 2),
            "locked":       round(rm.total_withdrawn, 2),
            "vault":        round(getattr(rm, "vault", 0), 2),
            "progress":     round(rm.progress, 2),
            "phase":        rm.phase,
            "status":       status,
            "winRate":      s["win_rate_pct"],
            "color":        spec["color"],
            "bets":         s["bets"],
            "wins":         s["wins"],
            "losses":       s["losses"],
            "avgBet":       round(rm.current_bankroll * 0.015, 2),
            "maxDD":        round(min(0, (rm.current_bankroll - rm.peak_bankroll) / rm.peak_bankroll * 100), 1),
            "peak":         round(rm.peak_bankroll, 2),
            "streak":       getattr(rm, "streak", 0),
            "sharpe":       round((s["roi_pct"] / 100) / max(0.01, 0.25), 2),
            "roi_pct":      s["roi_pct"],
            "halted":        False,
            "coolingDown":   rm.is_cooling_down,
            "cooldownSec":   round(s.get("cooldown_sec", 0), 1),
            "cbReason":      s.get("cb_reason", ""),
            "drawdownPct":   round(s.get("drawdown_pct", 0) * 100, 2),
            "turboEligible": s.get("turbo_eligible", False),
            "velBoost":      s.get("vel_boost", False),
            "danger":        s.get("danger", False),
            "milestoneHit":  s.get("milestoneHit", False),
            "targetHit":     rm.is_target_hit,
            "elapsed_min":  s["elapsed_min"],
            "bet_scale":    round(_bet_scales.get(bid, 1.0), 2),
            "strategy_mode":_strategy_modes.get(bid, "balanced"),
            # Goal fields
            "start_amount":   round(s.get("start_amount",  100), 2),
            "target_amount":  round(s.get("target_amount", 500), 2),
            "floor_amount":   round(s.get("floor_amount",   40), 2),
            "withdraw_at":    round(s.get("withdraw_at",   115), 2),
            "caution_at":     round(s.get("caution_at",    95),  2),
            "recovery_at":    round(s.get("recovery_at",   90),  2),
            "progress_pct":   round(min(rm.progress / max(s.get("target_amount", 500), 1) * 100, 100), 1),
            "rolling": windows,
            "autoDownscales": auto_state.get("downscales", 0),
            "lastDownscaleReason": auto_state.get("last_reason", ""),
            "autoManaged": auto_state.get("downscales", 0) > 0,
            "execution_mode": runtime.get("execution_mode", "paper" if spec["platform"] == "poly" else "simulated"),
            "data_mode": runtime.get("data_mode", "real_market_data" if spec["platform"] == "poly" else "simulated"),
            "scan_opportunity_count": runtime.get("opportunity_count", 0),
            "scan_best_edge": runtime.get("best_edge"),
            "scan_best_question": runtime.get("best_question", ""),
            "scan_last_ts": runtime.get("last_scan_ts", 0.0),
            "open_positions": runtime.get("open_positions", 0),
            "runtime_error": runtime.get("last_error", ""),
            "opportunities": runtime.get("opportunities", []),
            "btc_price": runtime.get("btc_price"),
            # Extended fields
            "total_withdrawn": round(rm.total_withdrawn, 4),
            "drawdown_pct":    round(s.get("drawdown_pct", 0), 4),
            "win_rate_pct":    s["win_rate_pct"],
            "initial_bankroll": round(rm.initial_bankroll, 2),
            "progress_x":      round(rm.progress_multiplier, 3),
            "cooling_down":    rm.is_cooling_down,
            "cb_reason":       s.get("cb_reason", ""),
            "target_hit":      rm.is_target_hit,
            "execution_mode":  runtime.get("execution_mode", "paper" if spec["platform"] == "poly" else "simulated"),
            # Last 7 bets as [1/0] for streak meter
            "last_7": [1 if n > 0 else 0
                       for n in list(_recent_trade_nets.get(bid, []))[-7:]],
        })
    return JSONResponse({"bots": bots, "tick": _tick, "goal_target": _goal_target, "running": _running})

@app.get("/api/equity")
async def get_equity(req: Request):
    _check_token(req)
    with _lock:
        data = list(_equity_history)
    return JSONResponse({"equity": data, "empty": len(data) == 0})

@app.get("/api/logs")
async def get_logs(req: Request):
    _check_token(req)
    return JSONResponse({"logs": list(_log_entries)})

@app.get("/api/info")
async def get_info(req: Request):
    _check_token(req)
    elapsed_wall = (time.time() - _start_wall) if _start_wall else 0
    elapsed_sim  = elapsed_wall * 10.0
    h, rem = divmod(int(elapsed_sim), 3600)
    m, s   = divmod(rem, 60)
    total_init     = len(_rms) * BOT_INITIAL_BANK
    total_progress = sum(rm.progress for rm in _rms.values()) if _rms else 0
    c = db._cx()
    trade_count = c.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    c.close()
    return JSONResponse({
        "running":       _running,
        "simSpeed":      10.0,
        "simHours":      24.0,
        "uptimeStr":     f"{h:02d}:{m:02d}:{s:02d}",
        "elapsedSim":    round(elapsed_sim),
        "totalProgress": round(total_progress, 2),
        "totalInit":     total_init,
        "goalPct":       round(total_progress / _goal_target * 100, 1),
        "cycle":         _tick,
        "activeBots":    sum(1 for rm in _rms.values() if not rm.is_halted),
        "paperMode":     not bool(_cfg.POLY_PRIVATE_KEY and "••" not in _cfg.POLY_PRIVATE_KEY),
        "executionMode": "live" if (_cfg.POLY_PRIVATE_KEY and "••" not in _cfg.POLY_PRIVATE_KEY) else "paper",
        "marketDataMode": "real_market_data",
        "persistedTrades": trade_count,
    })

# ── History ───────────────────────────────────────────────────────────────────

@app.get("/api/history")
async def get_history(req: Request, bot_id: str = None, won: int = None,
                       game: str = None, limit: int = 100, offset: int = 0):
    _check_token(req)
    won_filter = None if won is None else bool(won)
    rows, total = db.get_trades(bot_id=bot_id, won=won_filter, game=game, limit=limit, offset=offset)
    # Running totals per bot
    return JSONResponse({"trades": rows, "total": total, "limit": limit, "offset": offset})

@app.get("/api/history/summary")
async def history_summary(req: Request):
    _check_token(req)
    c = db._cx()
    summary = {}
    known_ids = {item["id"] for item in _load_bot_registry()}
    trade_ids = {
        row[0] for row in c.execute("SELECT DISTINCT bot_id FROM trades").fetchall()
        if row[0]
    }
    for bid in sorted(known_ids | trade_ids):
        row = c.execute(
            "SELECT COUNT(*) as bets, SUM(won) as wins, SUM(net) as pnl "
            "FROM trades WHERE bot_id=?", (bid,)
        ).fetchone()
        summary[bid] = {
            "bets": row[0] or 0,
            "wins": row[1] or 0,
            "pnl":  round(row[2] or 0, 2),
            "win_rate": round((row[1] or 0) / max(row[0] or 1, 1) * 100, 1),
        }
    c.close()
    return JSONResponse(summary)

# ── Projections (Monte Carlo) ─────────────────────────────────────────────────

@app.get("/api/projections")
async def get_projections(req: Request):
    _check_token(req)
    result = {}
    N_SIMS = 500
    N_BETS = 150

    for spec in _active_bot_specs():
        bid = spec["id"]
        kind = spec["kind"]
        rm = _rms.get(bid)
        if not rm: continue
        win_rate = rm.win_count / max(rm.bet_count, 1)
        if win_rate == 0: win_rate = 0.50
        from config import BET_PCT_BY_PHASE
        bet_pct  = BET_PCT_BY_PHASE.get(rm.phase, 0.015)
        # Payout multiplier approximation
        payout_m = 1.90 if kind != "poly" else 2.50

        sims = []
        for _ in range(N_SIMS):
            bank = rm.current_bankroll
            path = [bank]
            for _ in range(N_BETS):
                bet = max(bank * bet_pct, 0.01)
                if random.random() < win_rate:
                    bank += bet * (payout_m - 1)
                else:
                    bank -= bet
                bank = max(bank, rm.hard_stop_amount)
                path.append(round(bank, 2))
            sims.append(path)

        sims.sort(key=lambda x: x[-1])
        p10_final = sims[int(N_SIMS * 0.10)]
        p50_final = sims[int(N_SIMS * 0.50)]
        p90_final = sims[int(N_SIMS * 0.90)]

        # Subsample paths to 30 points
        def subsample(path, n=30):
            step = max(len(path) // n, 1)
            return [round(path[i], 2) for i in range(0, len(path), step)][:n]

        # Time to 10x estimate (P50 sim)
        time_to_10x = None
        target = rm.initial_bankroll * 10
        for i, v in enumerate(p50_final):
            if v >= target:
                bets_per_hour = 3600 / max(_cfg.BET_DELAY_SECONDS, 0.05) / 10  # sim speed
                time_to_10x  = round(i / bets_per_hour, 1)
                break

        result[bid] = {
            "current":      round(rm.current_bankroll, 2),
            "p10_final":    round(p10_final[-1], 2),
            "p50_final":    round(p50_final[-1], 2),
            "p90_final":    round(p90_final[-1], 2),
            "time_to_10x":  f"{time_to_10x:.1f}h" if time_to_10x else "50h+",
            "win_rate":     round(win_rate * 100, 1),
            "color":        spec["color"],
            "paths": {
                "p10": subsample(p10_final),
                "p50": subsample(p50_final),
                "p90": subsample(p90_final),
            },
        }
    return JSONResponse(result)

# ── Notes CRUD ────────────────────────────────────────────────────────────────

@app.get("/api/notes")
async def list_notes(req: Request, search: str = None, tag: str = None):
    _check_token(req)
    return JSONResponse({"notes": db.get_notes(search=search, tag=tag)})

@app.post("/api/notes")
async def add_note(req: Request, body: dict = Body(...)):
    _check_token(req)
    nid = db.create_note(body.get("title","Untitled"), body.get("content",""),
                         body.get("tags",[]), body.get("pinned", False))
    return JSONResponse({"id": nid, "ok": True})

@app.put("/api/notes/{nid}")
async def edit_note(nid: int, req: Request, body: dict = Body(...)):
    _check_token(req)
    db.update_note(nid, **{k: v for k, v in body.items() if k in ("title","content","tags","pinned")})
    return JSONResponse({"ok": True})

@app.delete("/api/notes/{nid}")
async def remove_note(nid: int, req: Request):
    _check_token(req)
    db.delete_note(nid)
    return JSONResponse({"ok": True})

# ── Wallet ────────────────────────────────────────────────────────────────────

@app.get("/api/wallet")
async def get_wallet(req: Request):
    _check_token(req)
    total_progress = sum(rm.progress for rm in _rms.values()) if _rms else 0
    total_locked   = sum(rm.total_withdrawn for rm in _rms.values()) if _rms else 0
    total_active   = sum(rm.current_bankroll for rm in _rms.values()) if _rms else 0
    txs = db.get_wallet_txs(50)
    total_deposited = sum(t["amount"] for t in txs if t["type"] == "deposit")
    total_deposited += len(_rms) * BOT_INITIAL_BANK  # initial deposits
    return JSONResponse({
        "stake_balance":    round(sum(rm.current_bankroll for bid, rm in _rms.items()
                                      if "dice" in bid or "limbo" in bid or "mines" in bid), 2),
        "poly_balance":     round(sum(rm.current_bankroll for bid, rm in _rms.items()
                                      if "poly" in bid), 2),
        "total_active":     round(total_active, 2),
        "total_locked":     round(total_locked, 2),
        "total_progress":   round(total_progress, 2),
        "total_deposited":  round(total_deposited, 2),
        "unrealized_pnl":   round(total_active - len(_rms) * BOT_INITIAL_BANK, 2),
        "realized_pnl":     round(total_locked, 2),
        "transactions":     txs,
        "stake_connected":  bool(db.get_setting("stake_api_token")),
        "poly_connected":   bool(db.get_setting("poly_private_key")),
    })

@app.post("/api/wallet/deposit")
async def deposit(req: Request, body: dict = Body(...)):
    _check_token(req)
    amount   = float(body.get("amount", 0))
    platform = body.get("platform", "manual")
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    db.save_wallet_tx("deposit", amount, note=f"Manual deposit via {platform}")
    logger.info(f"Deposit recorded: ${amount:.2f} from {platform}")
    return JSONResponse({"ok": True, "amount": amount})

@app.post("/api/wallet/withdraw")
async def request_withdraw(req: Request, body: dict = Body(...)):
    _check_token(req)
    amount = float(body.get("amount", 0))
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    note = body.get("note", "Withdrawal request")
    db.save_wallet_tx("withdraw_request", amount, note=note)
    logger.info(f"Withdrawal request: ${amount:.2f}")
    return JSONResponse({"ok": True, "amount": amount})

# ── Notifications ─────────────────────────────────────────────────────────────

@app.get("/api/notifications")
async def get_notifs(req: Request):
    _check_token(req)
    return JSONResponse({"notifications": db.get_notifications(30)})

# ── Bot registry ──────────────────────────────────────────────────────────────

@app.get("/api/bots/config")
async def get_bot_config(req: Request):
    _check_token(req)
    registry = _load_bot_registry()
    return JSONResponse({
        "bots": registry,
        "active_count": sum(1 for item in registry if item["enabled"]),
    })


@app.get("/api/bots/registry")
async def get_bot_registry(req: Request):
    return await get_bot_config(req)


@app.post("/api/bots/config")
async def save_bot_config(req: Request, body: dict = Body(...)):
    _check_token(req)
    incoming = body.get("bots", [])
    current = {item["id"]: item for item in _load_bot_registry()}
    for item in incoming:
        bid = item.get("id")
        if bid not in current:
            continue
        current[bid]["enabled"] = bool(item.get("enabled", current[bid]["enabled"]))
        display_name = str(item.get("display_name") or current[bid]["display_name"]).strip()
        current[bid]["display_name"] = display_name or bid
    registry = list(current.values())
    _save_bot_registry(registry)
    if _running:
        _stop_bots()
        _start_bots()
    return JSONResponse({
        "ok": True,
        "bots": registry,
        "active_count": sum(1 for item in registry if item["enabled"]),
        "history_preserved": True,
    })


@app.post("/api/bots/registry")
async def save_bot_registry(req: Request, body: dict = Body(...)):
    return await save_bot_config(req, body)

# ── Bot controls ──────────────────────────────────────────────────────────────

@app.post("/api/bots/strategy")
async def set_all_strategy(req: Request, body: dict = Body(...)):
    _check_token(req)
    mode  = body.get("mode", "balanced")
    scale = _STRATEGY_SCALES.get(mode, 1.0)
    for spec in _active_bot_specs():
        bid = spec["id"]
        _strategy_modes[bid] = mode
        _apply_scale(bid, scale)
    logger.info(f"Global strategy → {mode} ({scale}×)")
    return JSONResponse({"ok": True, "mode": mode, "scale": scale})

@app.post("/api/bots/{bot_id}/strategy")
async def set_bot_strategy(bot_id: str, req: Request, body: dict = Body(...)):
    _check_token(req)
    mode  = body.get("mode", "balanced")
    scale = _STRATEGY_SCALES.get(mode, _bet_scales.get(bot_id, 1.0))
    _strategy_modes[bot_id] = mode
    _apply_scale(bot_id, scale)
    return JSONResponse({"ok": True, "mode": mode, "scale": scale})

@app.post("/api/bots/{bot_id}/scale")
async def scale_bot(bot_id: str, req: Request, body: dict = Body(...)):
    _check_token(req)
    scale = max(0.1, min(5.0, float(body.get("scale", 1.0))))
    _apply_scale(bot_id, scale)
    return JSONResponse({"ok": True, "scale": scale})

# ── Per-bot pause / resume / fund ─────────────────────────────────────────────

_bot_pause_flags: dict[str, bool] = {}

@app.post("/api/bots/{bot_id}/pause")
async def pause_bot(bot_id: str, req: Request):
    _check_token(req)
    _bot_pause_flags[bot_id] = True
    rm = _rms.get(bot_id)
    if rm:
        rm.is_halted = True
    logger.info(f"Bot {bot_id} paused via API")
    _broadcast_sync({"type": "bot_paused", "bot_id": bot_id})
    return JSONResponse({"ok": True, "bot_id": bot_id, "paused": True})

@app.post("/api/bots/{bot_id}/resume")
async def resume_bot(bot_id: str, req: Request):
    _check_token(req)
    _bot_pause_flags.pop(bot_id, None)
    rm = _rms.get(bot_id)
    if rm:
        rm.is_halted = False
    logger.info(f"Bot {bot_id} resumed via API")
    _broadcast_sync({"type": "bot_resumed", "bot_id": bot_id})
    return JSONResponse({"ok": True, "bot_id": bot_id, "paused": False})

@app.post("/api/bots/{bot_id}/fund")
async def fund_bot(bot_id: str, req: Request, body: dict = Body(...)):
    """Add funds to a bot's bankroll (simulation — for paper mode)."""
    _check_token(req)
    amount = float(body.get("amount", 0))
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    rm = _rms.get(bot_id)
    if not rm:
        raise HTTPException(404, "Bot not found")
    rm.bankroll        += amount
    rm.current_bankroll = rm.bankroll
    rm.start_amount    += amount   # adjust start so floor scales too
    rm.initial_bankroll = rm.start_amount
    rm.floor            = rm.start_amount * 0.40
    db.save_wallet_tx("fund", amount, note=f"Manual fund: {bot_id}")
    logger.info(f"[{bot_id}] funded +${amount:.2f} via API")
    _broadcast_sync({"type": "bot_funded", "bot_id": bot_id, "amount": amount, "bankroll": round(rm.bankroll, 4)})
    return JSONResponse({"ok": True, "bot_id": bot_id, "amount": amount, "new_bankroll": round(rm.bankroll, 4)})

# ── Phases endpoint ───────────────────────────────────────────────────────────

@app.get("/api/phases")
async def get_phases(req: Request):
    _check_token(req)
    phases = {}
    for bid, rm in _rms.items():
        phases[bid] = {
            "phase":       rm.phase,
            "drawdown_pct": round(rm.drawdown_pct * 100, 2),
            "bankroll":    round(rm.current_bankroll, 4),
            "streak":      getattr(rm, "streak", 0),
        }
    transitions = db.get_phase_transitions(limit=20)
    return JSONResponse({"phases": phases, "recent_transitions": transitions})

# ── Goals ─────────────────────────────────────────────────────────────────────

@app.get("/api/goals")
async def get_goals(req: Request):
    _check_token(req)
    total_progress = sum(rm.progress for rm in _rms.values()) if _rms else 0
    return JSONResponse({
        "target":   _goal_target,
        "progress": round(total_progress, 2),
        "pct":      round(min(total_progress / _goal_target * 100, 100), 1),
    })

@app.post("/api/goals")
async def set_goals(req: Request, body: dict = Body(...)):
    global _goal_target
    _check_token(req)
    _goal_target = max(float(body.get("target", 6000.0)), 100.0)
    return JSONResponse({"ok": True, "target": _goal_target})

# ── Timeline ──────────────────────────────────────────────────────────────────

@app.get("/api/timeline")
async def get_timeline(req: Request):
    _check_token(req)
    events = db.get_events(limit=60)
    return JSONResponse({"events": events})

@app.post("/api/notifications/test-sms")
async def test_sms(req: Request):
    _check_token(req)
    sent = notifier.send_sms("6-Bot test SMS — your notifications are working!")
    return JSONResponse({"sent": sent})

# ── Settings ──────────────────────────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings(req: Request):
    _check_token(req)
    s = db.get_all_settings()
    # Mask sensitive fields
    for k in ("twilio_auth_token","poly_private_key","poly_api_secret","stake_api_token"):
        if s.get(k): s[k] = "••••••••"
    return JSONResponse(s)

@app.post("/api/settings")
async def save_settings(req: Request, body: dict = Body(...)):
    _check_token(req)
    for k, v in body.items():
        if "••" not in str(v):  # don't overwrite with masked values
            db.set_setting(k, v)
    return JSONResponse({"ok": True})

# ── VPN / Tor endpoints ───────────────────────────────────────────────────────

@app.get("/api/vpn/status")
async def vpn_status(req: Request):
    _check_token(req)
    return JSONResponse({
        "status":  _tor_status,
        "ip":      _tor_ip,
        "country": _tor_country,
        "installed": _find_bin("tor") is not None,
    })

@app.post("/api/vpn/start")
async def vpn_start(req: Request):
    _check_token(req)
    threading.Thread(target=_start_tor, daemon=True).start()
    return JSONResponse({"ok": True})

@app.post("/api/vpn/renew")
async def vpn_renew(req: Request):
    _check_token(req)
    ok = _renew_tor_circuit()
    return JSONResponse({"ok": ok})

# ── Remote access tunnel endpoints ────────────────────────────────────────────

@app.get("/api/tunnel/status")
async def tunnel_status(req: Request):
    _check_token(req)
    return JSONResponse({
        "status":    _tunnel_status,
        "url":       _tunnel_url,
        "installed": _find_bin("cloudflared") is not None,
    })

@app.post("/api/tunnel/start")
async def tunnel_start(req: Request):
    _check_token(req)
    threading.Thread(target=_start_tunnel, daemon=True).start()
    return JSONResponse({"ok": True})

@app.post("/api/tunnel/stop")
async def tunnel_stop(req: Request):
    global _tunnel_proc, _tunnel_url, _tunnel_status
    _check_token(req)
    if _tunnel_proc:
        _tunnel_proc.terminate()
        _tunnel_proc = None
    _tunnel_url = None
    _tunnel_status = "off"
    return JSONResponse({"ok": True})

# ── Device management endpoints ───────────────────────────────────────────────

@app.get("/api/devices")
async def list_devices(req: Request):
    _check_token(req)
    return JSONResponse({"devices": db.get_devices()})

@app.post("/api/devices/approve")
async def approve_device(req: Request, body: dict = Body(...)):
    _check_token(req)
    db.approve_device(body["device_id"], body.get("name", ""))
    return JSONResponse({"ok": True, "devices": db.get_devices()})

@app.post("/api/devices/revoke")
async def revoke_device(req: Request, body: dict = Body(...)):
    _check_token(req)
    db.revoke_device(body["device_id"])
    return JSONResponse({"ok": True, "devices": db.get_devices()})

@app.post("/api/devices/rename")
async def rename_device(req: Request, body: dict = Body(...)):
    _check_token(req)
    db.rename_device(body["device_id"], body["name"])
    return JSONResponse({"ok": True, "devices": db.get_devices()})

# ── Proxy / VPN helpers ───────────────────────────────────────────────────────

def _get_proxies(force: bool = False):
    """Return requests-compatible proxy dict if proxy is configured."""
    host = db.get_setting("proxy_host") or ""
    port = db.get_setting("proxy_port") or ""
    if host and port:
        url = f"socks5h://{host}:{port}"
        return {"http": url, "https": url}
    return None

@app.get("/api/proxy/test")
async def test_proxy(req: Request):
    _check_token(req)
    proxies = _get_proxies()
    if not proxies:
        return JSONResponse({"ok": False, "error": "No proxy configured"})
    try:
        import requests as _req
        r = _req.get("https://ipapi.co/json/", proxies=proxies, timeout=10)
        d = r.json()
        return JSONResponse({"ok": True, "ip": d.get("ip"), "country": d.get("country_name")})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

# ── Polymarket auto-wallet ────────────────────────────────────────────────────

@app.get("/api/wallet/poly")
async def get_poly_wallet(req: Request):
    _check_token(req)
    pk = db.get_setting("poly_auto_private_key")
    if pk:
        from eth_account import Account
        acct = Account.from_key(pk)
        return JSONResponse({"address": acct.address, "exists": True})
    return JSONResponse({"address": None, "exists": False})

@app.post("/api/wallet/poly/generate")
async def generate_poly_wallet(req: Request):
    _check_token(req)
    existing = db.get_setting("poly_auto_private_key")
    if existing:
        from eth_account import Account
        acct = Account.from_key(existing)
        return JSONResponse({"address": acct.address, "exists": True})
    from eth_account import Account
    acct = Account.create()
    db.set_setting("poly_auto_private_key", acct.key.hex())
    logger.info(f"Polymarket auto-wallet generated: {acct.address}")
    return JSONResponse({"address": acct.address, "exists": True})

# ── Control ───────────────────────────────────────────────────────────────────

@app.post("/api/start")
async def start_bots(req: Request):
    _check_token(req)
    if not _running: _start_bots()
    return JSONResponse({"ok": True})

@app.post("/api/stop")
async def stop_bots(req: Request):
    _check_token(req)
    _stop_bots()
    return JSONResponse({"ok": True})

# ── Per-bot configuration ─────────────────────────────────────────────────────

@app.post("/api/bots/{bot_id}/configure")
async def configure_bot(bot_id: str, req: Request, body: dict = Body(...)):
    """Set start_amount, target_amount, floor_amount for a bot and restart it."""
    _check_token(req)
    start  = float(body.get("start_amount",  100.0))
    target = float(body.get("target_amount", start * 5.0))
    floor  = float(body.get("floor_amount",  start * 0.40))
    if start <= 0 or target <= start or floor >= start:
        raise HTTPException(400, "Invalid config: start > floor, target > start")
    _bot_configs[bot_id] = {"start_amount": start, "target_amount": target, "floor_amount": floor}
    # Live reconfigure if bot is running
    if bot_id in _rms:
        _rms[bot_id].reconfigure(start, target, floor)
    # Persist
    all_cfgs = json.loads(db.get_setting("bot_configs", "{}") or "{}")
    all_cfgs[bot_id] = _bot_configs[bot_id]
    db.set_setting("bot_configs", json.dumps(all_cfgs))
    return JSONResponse({"ok": True, "bot_id": bot_id, "config": _bot_configs[bot_id]})


@app.post("/api/bots/{bot_id}/milestone/continue")
async def milestone_continue(bot_id: str, req: Request, body: dict = Body(...)):
    """User acknowledges 3x milestone and chooses new target (10x or 20x)."""
    _check_token(req)
    multiplier = float(body.get("multiplier", 10.0))
    if multiplier < 3:
        raise HTTPException(400, "multiplier must be ≥ 3")
    rm = _rms.get(bot_id)
    if not rm:
        raise HTTPException(404, "Bot not found")
    rm.continue_after_milestone(multiplier)
    db.save_event("info", f"{bot_id} continuing to {multiplier}x after milestone", bot_id)
    return JSONResponse({"ok": True, "new_target": round(rm.target, 2)})


# ── Vault ─────────────────────────────────────────────────────────────────────

@app.get("/api/vault")
async def get_vault(req: Request):
    _check_token(req)
    total_vault = sum(getattr(rm, "vault", 0) for rm in _rms.values())
    total_locked = sum(rm.total_withdrawn for rm in _rms.values())
    per_bot = {
        bid: {"vault": round(getattr(rm, "vault", 0), 4), "locked": round(rm.total_withdrawn, 4)}
        for bid, rm in _rms.items()
    }
    return JSONResponse({"total_vault": round(total_vault, 4), "total_locked": round(total_locked, 4), "per_bot": per_bot})


@app.post("/api/vault/lock")
async def lock_to_vault(req: Request, body: dict = Body(...)):
    """Move withdrawn profits into the permanent vault for a bot."""
    _check_token(req)
    bot_id = body.get("bot_id", "")
    amount = float(body.get("amount", 0))
    rm = _rms.get(bot_id)
    if not rm:
        raise HTTPException(404, "Bot not found")
    locked = rm.send_to_vault(amount)
    db.save_wallet_tx("vault_lock", locked, note=f"{bot_id} → vault")
    return JSONResponse({"ok": True, "locked": round(locked, 4), "vault_total": round(rm.vault, 4)})


# ── Load saved bot configs on startup ─────────────────────────────────────────
def _load_bot_configs():
    try:
        stored = json.loads(db.get_setting("bot_configs", "{}") or "{}")
        _bot_configs.update(stored)
    except Exception:
        pass


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket):
    # Token check via query param: /api/ws?token=<token>
    token = ws.query_params.get("token", "")
    if not token or _VALID_TOKENS.get(token, 0) < time.time():
        await ws.close(code=4401)
        return
    await _ws_manager.connect(ws)
    try:
        while True:
            # Keep alive; client can send ping, we just echo
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        _ws_manager.disconnect(ws)


# ── Vault history ─────────────────────────────────────────────────────────────

@app.get("/api/vault/history")
async def vault_history(req: Request, bot_id: str = None, limit: int = 100):
    _check_token(req)
    rows = db.get_vault_history(bot_id=bot_id, limit=limit)
    total = sum(r["amount"] for r in rows)
    return JSONResponse({"history": rows, "total": round(total, 4)})


# ── Circuit-breaker log ───────────────────────────────────────────────────────

@app.get("/api/circuit-breakers")
async def get_circuit_breakers(req: Request, bot_id: str = None):
    _check_token(req)
    rows = db.get_circuit_breakers(bot_id=bot_id, limit=100)
    return JSONResponse({"circuit_breakers": rows})


# ── Phase transition log ──────────────────────────────────────────────────────

@app.get("/api/phase-transitions")
async def get_phase_transitions(req: Request, bot_id: str = None):
    _check_token(req)
    rows = db.get_phase_transitions(bot_id=bot_id, limit=200)
    return JSONResponse({"transitions": rows})


# ── Volume spikes (Polymarket) ────────────────────────────────────────────────

@app.get("/api/volume-spikes")
async def get_volume_spikes(req: Request, min_ratio: float = 3.0):
    _check_token(req)
    rows = db.get_volume_spikes(min_ratio=min_ratio, limit=20)
    return JSONResponse({"spikes": rows})


@app.get("/api/btc-price")
async def get_btc_price(req: Request):
    _check_token(req)
    runtime = _poly_runtime.get("bot7_momentum", {})
    return JSONResponse({
        "price": runtime.get("btc_price"),
        "ts": runtime.get("last_scan_ts"),
        "execution_mode": "paper",
        "data_mode": "real_market_data",
    })


@app.get("/api/arbitrage")
async def get_arbitrage(req: Request):
    _check_token(req)
    runtime = _poly_runtime.get("bot8_arb", {})
    return JSONResponse({
        "opportunities": runtime.get("opportunities", []),
        "count": runtime.get("opportunity_count", 0),
        "best_edge": runtime.get("best_edge"),
        "execution_mode": "paper",
        "data_mode": "real_market_data",
    })


@app.get("/api/strategy-runtime")
async def get_strategy_runtime(req: Request):
    _check_token(req)
    return JSONResponse({
        "execution_mode": "paper",
        "paper_execution_only": True,
        "bots": _poly_runtime,
    })


@app.get("/api/data-sources")
async def get_data_sources(req: Request):
    _check_token(req)
    gamma_ts = max((row.get("last_scan_ts", 0.0) for row in _poly_runtime.values()), default=0.0)
    btc_runtime = _poly_runtime.get("bot7_momentum", {})
    return JSONResponse({
        "execution_mode": "paper",
        "paper_execution_only": True,
        "sources": {
            "gamma": {
                "mode": "public_api",
                "active": gamma_ts > 0,
                "last_scan_ts": gamma_ts,
            },
            "binance": {
                "mode": "public_api",
                "active": btc_runtime.get("btc_price") is not None,
                "last_scan_ts": btc_runtime.get("last_scan_ts", 0.0),
                "price": btc_runtime.get("btc_price"),
            },
            "stake_credentials_present": bool(db.get_setting("stake_api_token")),
            "polymarket_credentials_present": bool(db.get_setting("poly_private_key")),
        },
    })


# ── Public health check (no auth) ─────────────────────────────────────────────
@app.get("/api/ping")
async def ping():
    return {"ok": True, "ts": time.time()}

# ── Serve React frontend (must be last — catches all non-API routes) ──────────
_UI_DIST = os.path.join(os.path.dirname(__file__), "ui", "dist")
if os.path.isdir(_UI_DIST):
    # Assets are content-hashed (index-abc123.js) → safe to cache forever
    app.mount("/assets", StaticFiles(directory=os.path.join(_UI_DIST, "assets")), name="assets")

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon():
        f = os.path.join(_UI_DIST, "favicon.svg")
        return FileResponse(f) if os.path.exists(f) else JSONResponse({}, status_code=404)

    # HTML is NEVER cached — browser always gets the freshest index.html
    # which points to the freshest content-hashed JS bundle
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        from fastapi.responses import Response
        with open(os.path.join(_UI_DIST, "index.html"), "rb") as f:
            content = f.read()
        return Response(content=content, media_type="text/html", headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma":        "no-cache",
            "Expires":       "0",
        })


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
