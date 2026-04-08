"""
SQLite persistence layer for the DeG£N$ command center.

Tables:
  trades             – every bet placed by any bot
  equity_snapshots   – periodic portfolio value snapshots (flexible JSON)
  events             – system events (phase changes, CBs, milestones)
  notes              – user notes
  wallet_tx          – deposit/withdraw/lock transactions
  notifications      – SMS/push queue
  settings           – key-value config store
  vault              – immutable profit-lock ledger
  circuit_breakers   – CB trigger history
  phase_transitions  – phase change log per bot
  volume_cache       – Polymarket volume spike cache
"""

import os, sqlite3, json, threading
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "bots.db")
_lock   = threading.Lock()

def _cx():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def init_db():
    with _lock:
        c = _cx()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            bot_id   TEXT NOT NULL,
            game     TEXT NOT NULL,
            phase    TEXT NOT NULL,
            amount   REAL NOT NULL,
            won      INTEGER NOT NULL,
            net      REAL NOT NULL,
            bankroll REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            label     TEXT,
            data_json TEXT NOT NULL DEFAULT '{}',
            portfolio REAL DEFAULT 0,
            -- Legacy columns kept for backwards compat with old queries
            bot1 REAL DEFAULT 0, bot2 REAL DEFAULT 0, bot3 REAL DEFAULT 0,
            bot4 REAL DEFAULT 0, bot5 REAL DEFAULT 0, bot6 REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS events (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            ts     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            type   TEXT NOT NULL,
            bot_id TEXT,
            msg    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            title      TEXT NOT NULL,
            content    TEXT NOT NULL DEFAULT '',
            tags       TEXT NOT NULL DEFAULT '[]',
            pinned     INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS wallet_tx (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            type     TEXT NOT NULL,
            amount   REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            platform TEXT,
            note     TEXT
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            ts   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            type TEXT NOT NULL DEFAULT 'info',
            msg  TEXT NOT NULL,
            sent INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        -- Immutable profit-lock ledger: money moved to vault cannot be reversed
        CREATE TABLE IF NOT EXISTS vault (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            bot_id    TEXT NOT NULL,
            amount    REAL NOT NULL,
            trigger   TEXT NOT NULL,   -- 'ratchet', 'auto_withdraw', 'manual'
            bankroll  REAL NOT NULL    -- bankroll at time of lock
        );

        -- Circuit-breaker trigger history
        CREATE TABLE IF NOT EXISTS circuit_breakers (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            bot_id     TEXT NOT NULL,
            reason     TEXT NOT NULL,   -- 'consec_3', 'consec_5', 'vel_3pct', 'vel_5pct'
            duration_s REAL NOT NULL,
            bankroll   REAL NOT NULL,
            phase      TEXT NOT NULL
        );

        -- Phase transition log
        CREATE TABLE IF NOT EXISTS phase_transitions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            bot_id     TEXT NOT NULL,
            from_phase TEXT NOT NULL,
            to_phase   TEXT NOT NULL,
            bankroll   REAL NOT NULL,
            reason     TEXT
        );

        -- Polymarket volume spike cache (reused across bots to avoid duplicate fetches)
        CREATE TABLE IF NOT EXISTS volume_cache (
            market_id   TEXT PRIMARY KEY,
            ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            vol_1h      REAL DEFAULT 0,
            vol_prev_1h REAL DEFAULT 0,
            spike_ratio REAL DEFAULT 1.0,
            question    TEXT,
            yes_price   REAL DEFAULT 0.5
        );

        -- Approved devices (iPhone, iPad, MacBook etc)
        CREATE TABLE IF NOT EXISTS devices (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL DEFAULT 'Unknown Device',
            user_agent TEXT,
            approved   INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            last_seen  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        """)

        # Default settings
        defaults = {
            "sim_speed"            : "10",
            "notify_phone"         : "",
            "milestone_increment"  : "100",
            "twilio_account_sid"   : "",
            "twilio_auth_token"    : "",
            "twilio_from_number"   : "",
            "stake_api_token"      : "",
            "poly_private_key"     : "",
            "poly_api_key"         : "",
            "poly_api_secret"      : "",
            "poly_api_passphrase"  : "",
            "bot_registry"         : json.dumps([
                {"id": "bot1_dice",     "enabled": True, "display_name": "Dice Runner",    "platform": "stake", "strategy": "dice"},
                {"id": "bot2_limbo",    "enabled": True, "display_name": "Limbo Edge",     "platform": "stake", "strategy": "limbo"},
                {"id": "bot3_mines",    "enabled": True, "display_name": "Mines Runner",   "platform": "stake", "strategy": "mines"},
                {"id": "bot4_poly",     "enabled": True, "display_name": "Poly Scout",     "platform": "poly",  "strategy": "edge_scanner"},
                {"id": "bot5_poly",     "enabled": True, "display_name": "Poly Hunter",    "platform": "poly",  "strategy": "edge_scanner"},
                {"id": "bot6_poly",     "enabled": True, "display_name": "Poly Edge",      "platform": "poly",  "strategy": "edge_scanner"},
                {"id": "bot7_momentum", "enabled": True, "display_name": "Momentum BTC",   "platform": "poly",  "strategy": "btc_momentum"},
                {"id": "bot8_arb",      "enabled": True, "display_name": "Arb Hunter",     "platform": "poly",  "strategy": "intra_arb"},
                {"id": "bot9_sniper",   "enabled": True, "display_name": "Poly Sniper",    "platform": "poly",  "strategy": "resolution_sniper"},
                {"id": "bot10_volume",  "enabled": True, "display_name": "Vol Spike",      "platform": "poly",  "strategy": "volume_spike"},
            ]),
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v))
        c.commit()
        c.close()

# ── Write ─────────────────────────────────────────────────────────────────────

def save_trade(bot_id, game, phase, amount, won, net, bankroll):
    with _lock:
        c = _cx()
        c.execute(
            "INSERT INTO trades(bot_id,game,phase,amount,won,net,bankroll) VALUES(?,?,?,?,?,?,?)",
            (bot_id, game, phase, amount, 1 if won else 0, net, bankroll)
        )
        c.commit(); c.close()


def save_snapshot(label: str, bots_progress: dict, portfolio: float):
    """
    bots_progress: {bot_id: bankroll_value, ...}  — any number of bots.
    Writes JSON column (flexible) + legacy bot1-bot6 columns for old queries.
    """
    with _lock:
        c = _cx()
        data_json = json.dumps(bots_progress)
        c.execute(
            """INSERT INTO equity_snapshots
               (label, data_json, portfolio, bot1, bot2, bot3, bot4, bot5, bot6)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                label, data_json, portfolio,
                bots_progress.get("bot1_dice",  0),
                bots_progress.get("bot2_limbo", 0),
                bots_progress.get("bot3_mines", 0),
                bots_progress.get("bot4_poly",  0),
                bots_progress.get("bot5_poly",  0),
                bots_progress.get("bot6_poly",  0),
            )
        )
        c.commit(); c.close()


def save_event(etype: str, msg: str, bot_id=None):
    with _lock:
        c = _cx()
        c.execute("INSERT INTO events(type,bot_id,msg) VALUES(?,?,?)", (etype, bot_id, msg))
        c.commit(); c.close()


def save_notification(msg: str, ntype="info", sent=False):
    with _lock:
        c = _cx()
        c.execute(
            "INSERT INTO notifications(type,msg,sent) VALUES(?,?,?)",
            (ntype, msg, 1 if sent else 0)
        )
        c.commit(); c.close()


def save_wallet_tx(tx_type, amount, currency="USD", platform=None, note=None):
    with _lock:
        c = _cx()
        c.execute(
            "INSERT INTO wallet_tx(type,amount,currency,platform,note) VALUES(?,?,?,?,?)",
            (tx_type, amount, currency, platform, note)
        )
        c.commit(); c.close()


def save_vault_lock(bot_id: str, amount: float, trigger: str, bankroll: float):
    with _lock:
        c = _cx()
        c.execute(
            "INSERT INTO vault(bot_id,amount,trigger,bankroll) VALUES(?,?,?,?)",
            (bot_id, amount, trigger, bankroll)
        )
        c.commit(); c.close()


def save_circuit_breaker(bot_id: str, reason: str, duration_s: float,
                         bankroll: float, phase: str):
    with _lock:
        c = _cx()
        c.execute(
            "INSERT INTO circuit_breakers(bot_id,reason,duration_s,bankroll,phase) VALUES(?,?,?,?,?)",
            (bot_id, reason, duration_s, bankroll, phase)
        )
        c.commit(); c.close()


def save_phase_transition(bot_id: str, from_phase: str, to_phase: str,
                          bankroll: float, reason: str = None):
    with _lock:
        c = _cx()
        c.execute(
            "INSERT INTO phase_transitions(bot_id,from_phase,to_phase,bankroll,reason) VALUES(?,?,?,?,?)",
            (bot_id, from_phase, to_phase, bankroll, reason)
        )
        c.commit(); c.close()


def upsert_volume_cache(market_id: str, vol_1h: float, vol_prev_1h: float,
                        spike_ratio: float, question: str, yes_price: float):
    with _lock:
        c = _cx()
        c.execute(
            """INSERT INTO volume_cache(market_id,vol_1h,vol_prev_1h,spike_ratio,question,yes_price)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(market_id) DO UPDATE SET
                 ts=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                 vol_1h=excluded.vol_1h,
                 vol_prev_1h=excluded.vol_prev_1h,
                 spike_ratio=excluded.spike_ratio,
                 yes_price=excluded.yes_price""",
            (market_id, vol_1h, vol_prev_1h, spike_ratio, question, yes_price)
        )
        c.commit(); c.close()


# ── Read ──────────────────────────────────────────────────────────────────────

def get_trades(bot_id=None, won=None, game=None, limit=100, offset=0):
    c = _cx()
    q, p = "SELECT * FROM trades WHERE 1=1", []
    if bot_id: q += " AND bot_id=?"; p.append(bot_id)
    if won is not None: q += " AND won=?"; p.append(1 if won else 0)
    if game: q += " AND game=?"; p.append(game)
    total = c.execute(q.replace("SELECT *", "SELECT COUNT(*)"), p).fetchone()[0]
    q += " ORDER BY id DESC LIMIT ? OFFSET ?"; p += [limit, offset]
    rows = [dict(r) for r in c.execute(q, p).fetchall()]
    c.close()
    return rows, total


def get_equity_snapshots(limit=500):
    c = _cx()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM equity_snapshots ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()]
    c.close()
    # Parse data_json for callers that want it
    for r in rows:
        if r.get("data_json"):
            try:
                r["data"] = json.loads(r["data_json"])
            except Exception:
                r["data"] = {}
    return rows[::-1]


def get_events(limit=50):
    c = _cx()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()]
    c.close()
    return rows


def get_notes(search=None, tag=None):
    c = _cx()
    q, p = "SELECT * FROM notes WHERE 1=1", []
    if search:
        q += " AND (title LIKE ? OR content LIKE ?)"; p += [f"%{search}%", f"%{search}%"]
    q += " ORDER BY pinned DESC, updated_at DESC"
    rows = [dict(r) for r in c.execute(q, p).fetchall()]
    c.close()
    return rows


def create_note(title, content="", tags=None, pinned=False):
    with _lock:
        c = _cx()
        cur = c.execute(
            "INSERT INTO notes(title,content,tags,pinned) VALUES(?,?,?,?)",
            (title, content, json.dumps(tags or []), 1 if pinned else 0)
        )
        nid = cur.lastrowid; c.commit(); c.close()
        return nid


def update_note(nid, **kwargs):
    with _lock:
        c = _cx()
        parts, vals = [], []
        if "title"   in kwargs: parts.append("title=?");   vals.append(kwargs["title"])
        if "content" in kwargs: parts.append("content=?"); vals.append(kwargs["content"])
        if "tags"    in kwargs: parts.append("tags=?");    vals.append(json.dumps(kwargs["tags"]))
        if "pinned"  in kwargs: parts.append("pinned=?");  vals.append(1 if kwargs["pinned"] else 0)
        parts.append("updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')")
        c.execute(f"UPDATE notes SET {','.join(parts)} WHERE id=?", vals + [nid])
        c.commit(); c.close()


def delete_note(nid):
    with _lock:
        c = _cx()
        c.execute("DELETE FROM notes WHERE id=?", (nid,))
        c.commit(); c.close()


def get_wallet_txs(limit=50):
    c = _cx()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM wallet_tx ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()]
    c.close()
    return rows


def get_vault_history(bot_id=None, limit=100):
    c = _cx()
    q, p = "SELECT * FROM vault WHERE 1=1", []
    if bot_id: q += " AND bot_id=?"; p.append(bot_id)
    q += " ORDER BY id DESC LIMIT ?"; p.append(limit)
    rows = [dict(r) for r in c.execute(q, p).fetchall()]
    c.close()
    return rows


def get_circuit_breakers(bot_id=None, limit=50):
    c = _cx()
    q, p = "SELECT * FROM circuit_breakers WHERE 1=1", []
    if bot_id: q += " AND bot_id=?"; p.append(bot_id)
    q += " ORDER BY id DESC LIMIT ?"; p.append(limit)
    rows = [dict(r) for r in c.execute(q, p).fetchall()]
    c.close()
    return rows


def get_phase_transitions(bot_id=None, limit=100):
    c = _cx()
    q, p = "SELECT * FROM phase_transitions WHERE 1=1", []
    if bot_id: q += " AND bot_id=?"; p.append(bot_id)
    q += " ORDER BY id DESC LIMIT ?"; p.append(limit)
    rows = [dict(r) for r in c.execute(q, p).fetchall()]
    c.close()
    return rows


def get_volume_spikes(min_ratio=3.0, limit=20):
    c = _cx()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM volume_cache WHERE spike_ratio >= ? ORDER BY spike_ratio DESC LIMIT ?",
        (min_ratio, limit)
    ).fetchall()]
    c.close()
    return rows


def get_notifications(limit=30):
    c = _cx()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()]
    c.close()
    return rows


def get_setting(key, default=""):
    c = _cx()
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    c.close()
    return row[0] if row else default


def set_setting(key, value):
    with _lock:
        c = _cx()
        c.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
            (key, str(value))
        )
        c.commit(); c.close()


def get_all_settings():
    c = _cx()
    rows = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings").fetchall()}
    c.close()
    return rows


# ── Device management ─────────────────────────────────────────────────────────

def register_device(device_id: str, user_agent: str = "", name: str = ""):
    """Register device if new; update last_seen if existing. Returns (row, is_new)."""
    with _lock:
        c = _cx()
        row = c.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        if row is None:
            # First device ever → auto-approve
            total = c.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
            approved = 1 if total == 0 else 0
            display = name or _guess_device_name(user_agent)
            c.execute(
                "INSERT INTO devices(id,name,user_agent,approved) VALUES(?,?,?,?)",
                (device_id, display, user_agent, approved)
            )
            c.commit()
            row = dict(c.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone())
            c.close()
            return row, True
        else:
            c.execute(
                "UPDATE devices SET last_seen=strftime('%Y-%m-%dT%H:%M:%fZ','now'), user_agent=? WHERE id=?",
                (user_agent, device_id)
            )
            c.commit()
            row = dict(c.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone())
            c.close()
            return row, False


def _guess_device_name(ua: str) -> str:
    ua = ua.lower()
    if "ipad" in ua:       return "iPad"
    if "iphone" in ua:     return "iPhone"
    if "macintosh" in ua:  return "MacBook"
    if "android" in ua:    return "Android"
    if "windows" in ua:    return "Windows PC"
    return "Unknown Device"


def approve_device(device_id: str, name: str = ""):
    with _lock:
        c = _cx()
        if name:
            c.execute("UPDATE devices SET approved=1, name=? WHERE id=?", (name, device_id))
        else:
            c.execute("UPDATE devices SET approved=1 WHERE id=?", (device_id,))
        c.commit(); c.close()


def revoke_device(device_id: str):
    with _lock:
        c = _cx()
        c.execute("UPDATE devices SET approved=0 WHERE id=?", (device_id,))
        c.commit(); c.close()


def rename_device(device_id: str, name: str):
    with _lock:
        c = _cx()
        c.execute("UPDATE devices SET name=? WHERE id=?", (name, device_id))
        c.commit(); c.close()


def get_devices():
    c = _cx()
    rows = [dict(r) for r in c.execute("SELECT * FROM devices ORDER BY first_seen ASC").fetchall()]
    c.close()
    return rows


def is_device_approved(device_id: str) -> bool:
    c = _cx()
    row = c.execute("SELECT approved FROM devices WHERE id=?", (device_id,)).fetchone()
    c.close()
    return bool(row and row[0])
