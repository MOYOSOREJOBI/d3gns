import { useState, useEffect, useCallback, useMemo, useRef, Component } from "react";
import {
  AreaChart, Area, LineChart, Line, ComposedChart,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts";

// ─────────────────────────────────────────────────────────────────────────────
//  CONFIG
// ─────────────────────────────────────────────────────────────────────────────
const API      = "/api";
const POLL_MS  = 2000;
const DEMO_TOKEN = "__DEMO__";
const ADMIN_VAULT_KEY = "degens_admin_vault_v1";

// ─── Demo / offline mock data ────────────────────────────────────────────────
const DEMO_BOTS = [
  { id:"bot1_dice",    name:"Dice Runner",   platform:"stake",  bankroll:187.42, start_amount:100, target_amount:500, floor_amount:40,  roi_pct:87.4,  winRate:64, bets:2140, wins:1370, losses:770,  phase:"normal",      streak:4,  vault:22,  total_withdrawn:18,  halted:false, danger:false, execution_mode:"live",      strategy_mode:"balanced",  progress_pct:24, milestoneHit:false },
  { id:"bot2_limbo",   name:"Limbo Edge",    platform:"stake",  bankroll:134.80, start_amount:100, target_amount:500, floor_amount:40,  roi_pct:34.8,  winRate:58, bets:1820, wins:1056, losses:764,  phase:"careful",     streak:-2, vault:8,   total_withdrawn:0,   halted:false, danger:false, execution_mode:"live",      strategy_mode:"conservative", progress_pct:9, milestoneHit:false },
  { id:"bot3_mines",   name:"Mines Runner",  platform:"stake",  bankroll:76.50,  start_amount:100, target_amount:500, floor_amount:40,  roi_pct:-23.5, winRate:44, bets:990,  wins:436,  losses:554,  phase:"floor",       streak:-8, vault:0,   total_withdrawn:0,   halted:false, danger:true,  execution_mode:"live",      strategy_mode:"aggressive", progress_pct:0, milestoneHit:false },
  { id:"bot4_poly",    name:"Poly Scout",    platform:"poly",   bankroll:320.10, start_amount:200, target_amount:1000,floor_amount:80,  roi_pct:60.1,  winRate:71, bets:430,  wins:305,  losses:125,  phase:"turbo",       streak:11, vault:60,  total_withdrawn:40,  halted:false, danger:false, execution_mode:"paper",     strategy_mode:"aggressive", progress_pct:31, milestoneHit:true  },
  { id:"bot5_poly",    name:"Poly Hunter",   platform:"poly",   bankroll:210.00, start_amount:200, target_amount:1000,floor_amount:80,  roi_pct:5.0,   winRate:55, bets:210,  wins:116,  losses:94,   phase:"normal",      streak:1,  vault:10,  total_withdrawn:0,   halted:false, danger:false, execution_mode:"paper",     strategy_mode:"balanced",  progress_pct:4, milestoneHit:false },
  { id:"bot7_momentum",name:"Momentum BTC",  platform:"stake",  bankroll:450.00, start_amount:400, target_amount:2000,floor_amount:160, roi_pct:12.5,  winRate:61, bets:680,  wins:415,  losses:265,  phase:"safe",        streak:3,  vault:35,  total_withdrawn:10,  halted:false, danger:false, execution_mode:"simulated", strategy_mode:"balanced",  progress_pct:6, milestoneHit:false },
];

const _eq = (n, base, vol) => Array.from({length:30}, (_,i) => ({
  t: `${i+1}`, equity: +(base + Math.sin(i*0.4)*vol + i*(vol/20) + (Math.random()-0.45)*vol*0.5).toFixed(2)
}));

const DEMO_EQUITY = [
  ..._eq(0, 100,  25).map((p,i)=>({t:p.t, bot1:p.equity, bot2:_eq(1,100,15)[i].equity, bot3:_eq(2,100,35)[i].equity,
     bot4:_eq(3,200,50)[i].equity, bot5:_eq(4,200,20)[i].equity, bot7:_eq(5,400,40)[i].equity, total:0}))
].map(p=>({...p, total:+(p.bot1+p.bot2+p.bot3+p.bot4+p.bot5+p.bot7).toFixed(2)}));

const DEMO_STATUS = {
  running: true,
  bots: DEMO_BOTS,
};
const DEMO_EQUITY_RESP  = { equity: DEMO_EQUITY };
const DEMO_INFO         = { uptime: "14h 22m", version: "2.4.1", env: "demo" };
const DEMO_HIST         = { total_bets:6270, total_wins:3698, overall_roi:28.4, best_bot:"bot4_poly", best_roi:60.1 };
const DEMO_VAULT        = { total_vault: 135, breakdown: DEMO_BOTS.map(b=>({id:b.id,vault:b.vault})) };
const DEMO_HISTORY      = { trades: Array.from({length:20},(_,i)=>({ id:i+1, bot_id: DEMO_BOTS[i%6].id, outcome: i%3===2?"loss":"win", amount: +(1+Math.random()*4).toFixed(2), profit: i%3===2 ? -(0.5+Math.random()*2).toFixed(2) : +(0.3+Math.random()*3).toFixed(2), ts: Date.now()-i*180000 })) };

function demoFetch(path) {
  if (path.includes("/status"))         return Promise.resolve(DEMO_STATUS);
  if (path.includes("/equity"))         return Promise.resolve(DEMO_EQUITY_RESP);
  if (path.includes("/info"))           return Promise.resolve(DEMO_INFO);
  if (path.includes("/history/summary"))return Promise.resolve(DEMO_HIST);
  if (path.includes("/vault"))          return Promise.resolve(DEMO_VAULT);
  if (path.includes("/history"))        return Promise.resolve(DEMO_HISTORY);
  if (path.includes("/volume-spikes"))  return Promise.resolve({ spikes: [] });
  if (path.includes("/arbitrage"))      return Promise.resolve({ opportunities: [] });
  return Promise.resolve({});
}
const THEME_KEY = "degens_theme_v1";
const MILESTONE_DISMISS_KEY = "degens_milestone_dismiss_v1";
const TZ_KEY   = "degens_timezone_v1";
const getToken    = () => localStorage.getItem("bot6_token") || "";
const storeToken  = t  => localStorage.setItem("bot6_token", t);
const clearToken  = ()  => localStorage.removeItem("bot6_token");
const getTheme    = () => localStorage.getItem(THEME_KEY) || "dark";
const storeTheme  = t => localStorage.setItem(THEME_KEY, t);

// ─── DEVICE ID ───────────────────────────────────────────────────────────────
const DEVICE_KEY = "degens_device_id_v1";
const getDeviceId = () => {
  let id = localStorage.getItem(DEVICE_KEY);
  if (!id) {
    id = crypto.randomUUID ? crypto.randomUUID() : ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
      (c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16));
    localStorage.setItem(DEVICE_KEY, id);
  }
  return id;
};
const getTz       = () => localStorage.getItem(TZ_KEY) || "America/Edmonton";
const storeTz     = tz => localStorage.setItem(TZ_KEY, tz);

// ─── TIMEZONE DEFINITIONS ────────────────────────────────────────────────────
const TIMEZONES = [
  { label: "Calgary",   tz: "America/Edmonton"     },
  { label: "Vancouver", tz: "America/Vancouver"    },
  { label: "Winnipeg",  tz: "America/Winnipeg"     },
  { label: "Toronto",   tz: "America/Toronto"      },
  { label: "Los Angeles", tz: "America/Los_Angeles" },
  { label: "Sao Paulo", tz: "America/Sao_Paulo"    },
  { label: "London",    tz: "Europe/London"        },
  { label: "Zurich",    tz: "Europe/Zurich"        },
  { label: "Istanbul",  tz: "Europe/Istanbul"      },
  { label: "Lagos",     tz: "Africa/Lagos"         },
  { label: "Cape Town", tz: "Africa/Johannesburg"  },
  { label: "Tokyo",     tz: "Asia/Tokyo"           },
  { label: "Shanghai",  tz: "Asia/Shanghai"        },
];

// ─── CLOCK HOOK ──────────────────────────────────────────────────────────────
function useClock(tz) {
  const [display, setDisplay] = useState({ time: "--:--:--", date: "", city: "" });
  useEffect(() => {
    const tick = () => {
      const now  = new Date();
      const city = TIMEZONES.find(t => t.tz === tz)?.label || "Calgary";
      const time = now.toLocaleTimeString("en-CA", { timeZone: tz, hour12: false,
        hour: "2-digit", minute: "2-digit", second: "2-digit" });
      const date = now.toLocaleDateString("en-CA", { timeZone: tz,
        weekday: "short", month: "short", day: "numeric" });
      setDisplay({ time, date, city });
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [tz]);
  return display;
}

function getDismissedMilestones() {
  try {
    const raw = JSON.parse(sessionStorage.getItem(MILESTONE_DISMISS_KEY) || "[]");
    return Array.isArray(raw) ? raw : [];
  } catch {
    return [];
  }
}

function setDismissedMilestone(botId) {
  const next = Array.from(new Set([...getDismissedMilestones(), botId]));
  sessionStorage.setItem(MILESTONE_DISMISS_KEY, JSON.stringify(next));
}

function clearDismissedMilestone(botId) {
  const next = getDismissedMilestones().filter(id => id !== botId);
  sessionStorage.setItem(MILESTONE_DISMISS_KEY, JSON.stringify(next));
}

const BC = {
  bot1_dice    : "#5ea1ff",
  bot2_limbo   : "#c089ff",
  bot3_mines   : "#59d47a",
  bot4_poly    : "#60a5fa",
  bot5_poly    : "#ef5f57",
  bot6_poly    : "#d6af41",
  bot7_momentum: "#14b8a6",
  bot8_arb     : "#a78bfa",
  bot9_sniper  : "#fb7185",
  bot10_volume : "#f97316",
};

function getBotColor(botId) {
  return BC[botId] || "#5ea1ff";
}

// Phase color palette — matches 7-phase risk engine
const PHASE_COLORS = {
  floor      : "#ef5f57",   // red — emergency grind
  ultra_safe : "#ff8f5a",   // orange — danger
  safe       : "#d6af41",   // yellow — caution
  careful    : "#c089ff",   // purple — watchful
  normal     : "#5ea1ff",   // blue — standard
  aggressive : "#59d47a",   // green — go
  turbo      : "#00ff88",   // bright green — max power
  milestone  : "#f0c060",   // gold — milestone hit
};

// Strategy display labels
const STRATEGY_LABELS = {
  dice              : "Dice",
  limbo             : "Limbo",
  mines             : "Mines",
  edge_scanner      : "Edge Scan",
  btc_momentum      : "BTC Mom.",
  intra_arb         : "Intra Arb",
  resolution_sniper : "Sniper",
  volume_spike      : "Vol Spike",
};

const STRATEGY_DEFS = [
  { id:"conservative", label:"Conservative", scale:"0.5x" },
  { id:"balanced",     label:"Balanced",     scale:"1x"   },
  { id:"aggressive",   label:"Aggressive",   scale:"2x"   },
  { id:"turbo",        label:"Turbo",        scale:"3.5x" },
];

// ─────────────────────────────────────────────────────────────────────────────
//  DESIGN TOKENS
// ─────────────────────────────────────────────────────────────────────────────
const THEMES = {
  dark: {
    bg:     "#000000",
    fg:     "#f5f5f5",
    muted:  "#6e6e6e",
    line:   "rgba(255,255,255,0.08)",
    blue:   "#5ea1ff",
    purple: "#c089ff",
    green:  "#59d47a",
    red:    "#ef5f57",
    yellow: "#d6af41",
    orange: "#ff8f5a",
    card:   "rgba(255,255,255,0.02)",
    tooltip:"#0a0a0a",
  },
  light: {
    bg:     "#ffffff",
    fg:     "#000000",
    muted:  "#5f5f5f",
    line:   "rgba(0,0,0,0.10)",
    blue:   "#2563eb",
    purple: "#7c3aed",
    green:  "#15803d",
    red:    "#dc2626",
    yellow: "#b45309",
    orange: "#ea580c",
    card:   "rgba(0,0,0,0.02)",
    tooltip:"#f3f3f3",
  },
};

const T = {
  ...THEMES.dark,
  font: `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace`,
};

// ─── SPACING TOKENS ──────────────────────────────────────────────────────────
const SP = { xs: 4, sm: 8, md: 14, lg: 20, xl: 28, xxl: 40 };

function applyTheme(theme) {
  Object.assign(T, THEMES[theme] || THEMES.dark, { font: T.font });
}

function getGlobalCss() {
  return `
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body, #root { height: 100%; background: ${T.bg}; color: ${T.fg};
      font-family: ${T.font}; font-size: 12px; -webkit-font-smoothing: antialiased; }
    body { transition: background .2s ease, color .2s ease; }
    ::-webkit-scrollbar { width: 3px; height: 3px; }
    ::-webkit-scrollbar-thumb { background: ${T.muted}; }
    ::-webkit-scrollbar-track { background: transparent; }
    * { scrollbar-width: thin; scrollbar-color: ${T.muted} transparent; }
    input, select, textarea { background: transparent; border: 1px solid ${T.line};
      color: ${T.fg}; font-family: ${T.font}; outline: none; }
    input::placeholder, textarea::placeholder { color: ${T.muted}; }
    input:focus, select:focus, textarea:focus { border-color: rgba(94,161,255,0.4); }
    button { cursor: pointer; font-family: ${T.font}; }
    input[type=range] { -webkit-appearance: none; height: 3px; background: ${T.line}; border: none; border-radius: 999px; }
    input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; width: 12px; height: 12px; border-radius: 50%; background: ${T.blue}; cursor: pointer; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.35} }
    @keyframes fadein { from{opacity:0;transform:translateY(4px)} to{opacity:1;transform:none} }
    @keyframes spin { to { transform: rotate(360deg); } }
    @keyframes milestoneIn { from{opacity:0;transform:scale(0.92)} to{opacity:1;transform:scale(1)} }
    @keyframes glowPulse { 0%,100%{box-shadow:0 0 0 rgba(89,212,122,0)} 50%{box-shadow:0 0 18px rgba(89,212,122,0.35)} }
    @keyframes turbo-pulse { 0%,100%{opacity:1;box-shadow:0 0 0 0 #00ff8844;} 50%{opacity:0.9;box-shadow:0 0 0 6px #00ff8800;} }
    .dg-chip { transition: background .15s ease, border-color .15s ease, color .15s ease; }
    .dg-chip:hover { background: rgba(255,255,255,0.06) !important; }
    .dg-btn { transition: opacity .15s ease, background .15s ease; }
    .dg-btn:hover:not(:disabled) { opacity: 0.82; }
    .dg-btn:active:not(:disabled) { opacity: 0.65; transform: scale(0.98); }
    .dg-nav-btn { transition: color .15s ease, border-color .15s ease; }
    .dg-nav-btn:hover { color: ${T.fg} !important; }
    .dg-row-hover:hover { background: rgba(255,255,255,0.025) !important; }
    select { cursor: pointer; }
    select:hover { border-color: rgba(94,161,255,0.3) !important; }
    a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible {
      outline: 1.5px solid rgba(94,161,255,0.55);
      outline-offset: 2px;
    }
  `;
}

// ─────────────────────────────────────────────────────────────────────────────
//  ATOMS
// ─────────────────────────────────────────────────────────────────────────────
const lbl = {
  fontSize: 9, color: T.muted, letterSpacing: "0.16em",
  textTransform: "uppercase", fontWeight: 700, userSelect: "none",
};

function Label({ children, style }) {
  return <div style={{ ...lbl, ...style }}>{children}</div>;
}

function Chip({ children, active, onClick, style }) {
  return (
    <span onClick={onClick} className="dg-chip" style={{
      fontSize: 10, border: `1px solid ${active ? "rgba(94,161,255,0.45)" : T.line}`,
      padding: "6px 11px", borderRadius: 6,
      background: active ? "rgba(94,161,255,0.12)" : "rgba(255,255,255,0.02)",
      color: active ? T.blue : T.muted,
      fontWeight: active ? 700 : 500,
      cursor: onClick ? "pointer" : "default",
      whiteSpace: "nowrap", letterSpacing: "0.04em",
      display: "inline-flex", alignItems: "center",
      ...style,
    }}>{children}</span>
  );
}

function ActionBtn({ children, onClick, color = T.green, style, disabled }) {
  const c = color === T.green ? "rgba(89,212,122,0.14)" : `${color}22`;
  const bg = color === T.green ? "rgba(89,212,122,0.08)" : `${color}10`;
  return (
    <span onClick={disabled ? undefined : onClick} className="dg-btn" style={{
      fontSize: 10, border: `1px solid ${disabled ? T.line : c}`,
      padding: "6px 12px", borderRadius: 6,
      background: disabled ? "transparent" : bg,
      color: disabled ? T.muted : color, fontWeight: 700, cursor: disabled ? "not-allowed" : "pointer",
      whiteSpace: "nowrap", opacity: disabled ? 0.45 : 1, letterSpacing: "0.06em",
      display: "inline-flex", alignItems: "center", gap: 5,
      ...style,
    }}>{children}</span>
  );
}

function Dot({ ok = true, size = 6 }) {
  return (
    <span style={{
      width: size, height: size, borderRadius: "50%", display: "inline-block",
      background: ok ? T.green : T.red, flexShrink: 0,
      boxShadow: ok ? "0 0 10px rgba(89,212,122,0.6)" : "none",
      animation: ok ? "pulse 2s infinite" : "none",
    }} />
  );
}

function Spinner({ size = 14 }) {
  return (
    <span style={{
      width: size, height: size, border: `1.5px solid #222`,
      borderTopColor: T.blue, borderRadius: "50%",
      display: "inline-block", animation: "spin .7s linear infinite",
    }} />
  );
}

function Tip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: T.tooltip, border: `1px solid ${T.line}`, borderRadius: 6, padding: "8px 12px" }}>
      <div style={{ fontSize: 9, color: T.muted, marginBottom: 5 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ fontSize: 10, color: p.color, display: "flex", justifyContent: "space-between", gap: 14 }}>
          <span>{p.name}</span>
          <span style={{ color: T.fg }}>${typeof p.value === "number" ? p.value.toFixed(2) : p.value}</span>
        </div>
      ))}
    </div>
  );
}

function MiniSpark({ data, dataKey = "v", color = T.blue, height = 28 }) {
  const gradId = `sg${color.replace(/[^a-zA-Z0-9]/g, "")}`;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.22} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area type="monotone" dataKey={dataKey} stroke={color} strokeWidth={1.5}
              fill={`url(#${gradId})`} dot={false} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function GlowArea({ data, dataKey = "v", color = T.blue, height = 80, refLines = [] }) {
  const filterId = `glow${color.replace(/[^a-zA-Z0-9]/g, "")}`;
  const gradId   = `gg${color.replace(/[^a-zA-Z0-9]/g, "")}`;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 2, bottom: 0, left: -24 }}>
        <defs>
          <filter id={filterId} x="-20%" y="-50%" width="140%" height="200%">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.28} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={T.line} strokeDasharray="0" />
        <XAxis dataKey="label" tick={{ fontSize: 8, fill: T.muted }} tickLine={false} axisLine={false}
               interval={Math.max(1, Math.floor((data.length || 1) / 5))} />
        <YAxis tick={{ fontSize: 8, fill: T.muted }} tickLine={false} axisLine={false} />
        <Tooltip content={<Tip />} />
        {refLines.map((r, i) => (
          <ReferenceLine key={i} y={r.y} stroke={r.color} strokeDasharray="4 4"
            label={{ value: r.label, position: "right", fontSize: 8, fill: r.color }} />
        ))}
        <Area type="monotone" dataKey={dataKey} stroke={color} strokeWidth={1.8}
              fill={`url(#${gradId})`} dot={false} isAnimationActive={false} filter={`url(#${filterId})`} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function DonutViz({ value = 0, color = T.green, label = "WIN" }) {
  const pct = Math.max(0, Math.min(100, value));
  const r = 44; const c = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 120 120" width="110" height="110" aria-hidden="true">
      <circle cx="60" cy="60" r={r} fill="none" stroke={T.line} strokeWidth="10" />
      <circle cx="60" cy="60" r={r} fill="none" stroke={color} strokeWidth="10"
        strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c - (pct / 100) * c} transform="rotate(-90 60 60)" />
      <text x="60" y="56" textAnchor="middle" fill={T.fg} style={{ fontSize: 14, fontWeight: 800 }}>{pct.toFixed(1)}%</text>
      <text x="60" y="72" textAnchor="middle" fill={T.muted} style={{ fontSize: 9, fontWeight: 700, letterSpacing: "0.18em" }}>{label}</text>
    </svg>
  );
}

function MetricBars({ rows }) {
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {rows.map((row) => (
        <div key={row.label} style={{ display: "grid", gridTemplateColumns: "74px 1fr 48px", gap: 10, alignItems: "center" }}>
          <span style={{ fontSize: 10, fontWeight: 700, color: T.muted }}>{row.label}</span>
          <div style={{ height: 4, background: T.line, borderRadius: 999, overflow: "hidden" }}>
            <div style={{ width: `${Math.max(0, Math.min(100, row.value))}%`, height: "100%", background: row.color,
              borderRadius: 999 }} />
          </div>
          <span style={{ fontSize: 10, fontWeight: 800 }}>{row.value}%</span>
        </div>
      ))}
    </div>
  );
}

function useViewport() {
  const getWidth = () => (typeof window === "undefined" ? 1280 : window.innerWidth);
  const [width, setWidth] = useState(getWidth);
  useEffect(() => {
    const onResize = () => setWidth(getWidth());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return { width, isMobile: width <= 768, isTablet: width <= 1100 };
}

const WORKSHOP_KEY = "degens_workshop_variants_v1";
const WORKSHOP_PRESETS = [
  {
    id: "low_variance",
    label: "Low Variance",
    description: "Small size, tighter floor, steadier paper testing.",
    mode: "conservative",
    scale: 0.5,
    target_multiplier: 3,
    floor_multiplier: 0.55,
  },
  {
    id: "trend_probe",
    label: "Trend Probe",
    description: "Balanced size for testing directional reads without overcommitting.",
    mode: "balanced",
    scale: 1.25,
    target_multiplier: 5,
    floor_multiplier: 0.45,
  },
  {
    id: "recovery_drill",
    label: "Recovery Drill",
    description: "Smaller sizing with more breathing room for drawdown recovery tests.",
    mode: "conservative",
    scale: 0.7,
    target_multiplier: 4,
    floor_multiplier: 0.65,
  },
  {
    id: "turbo_probe",
    label: "Turbo Probe",
    description: "Higher scale preset for short paper stress tests.",
    mode: "turbo",
    scale: 2,
    target_multiplier: 6,
    floor_multiplier: 0.4,
  },
];
const WORKSHOP_TAG_SUGGESTIONS = ["benchmark", "recovery", "experimental", "quarantine", "scaled", "paper"];

function parseVariantTags(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  return String(value || "")
    .split(",")
    .map(tag => tag.trim().toLowerCase())
    .filter(Boolean)
    .filter((tag, index, arr) => arr.indexOf(tag) === index);
}

function deriveBotTags(bot) {
  const tags = [];
  if ((bot.roi_pct || 0) > 0 && (bot.rolling?.w25?.roi_pct || 0) > 0) tags.push("benchmark");
  if ((bot.autoDownscales || 0) > 0) tags.push("recovery");
  if (bot.danger || (bot.rolling?.w25?.roi_pct || 0) <= -10) tags.push("quarantine");
  if ((bot.bet_scale || 1) > 1.1) tags.push("scaled");
  if (!tags.length) tags.push("experimental");
  return tags;
}

function getBotCurve(equity, bot) {
  const idx = bot?.id?.match(/\d+/)?.[0];
  return (equity || []).map((e, i) => ({
    label: e.label || `${i}`,
    value: e[`bot${idx}`] ?? bot?.start_amount ?? 0,
  }));
}

function buildOverviewSeries(bots, equity) {
  const starts = bots.reduce((s, b) => s + (b.start_amount || 100), 0);
  return (equity || []).map((row, i) => {
    const portfolio = row.portfolio ?? 0;
    const progress = Math.max(0, ((portfolio / Math.max(starts, 1)) * 100));
    return {
      label: row.label || `${i}`,
      portfolio,
      pnl: portfolio - starts,
      goal: starts,
      progress,
    };
  });
}

function buildAnalyticsInsights(bots, proj) {
  const sortedByRoi = [...bots].sort((a, b) => (b.roi_pct || 0) - (a.roi_pct || 0));
  const strongest = sortedByRoi[0];
  const weakest = sortedByRoi[sortedByRoi.length - 1];
  const safeBots = bots.filter(b => (b.winRate || 0) >= 70 && (b.roi_pct || 0) >= -5);
  const stressed = bots.filter(b => (b.roi_pct || 0) < -10 || b.danger);
  const notes = [];

  if (strongest) {
    notes.push({
      title: "Best Live Read",
      tone: T.green,
      body: `${strongest.name || strongest.id} is leading on ROI and is the cleanest candidate to study for scaling.`,
      reason: `High ROI with ${strongest.bets || 0} bets usually means its current mode is matching the tape better than the rest.`,
      change: `Keep size disciplined, then duplicate its settings in Workshop and test a slightly higher scale or target.`,
    });
  }

  if (weakest) {
    notes.push({
      title: "Main Drag",
      tone: T.red,
      body: `${weakest.name || weakest.id} is pulling the portfolio down the hardest right now.`,
      reason: `Negative ROI with sustained bet count often points to poor thresholds, too much size, or a stale strategy mode.`,
      change: `Reduce scale, raise floor protection, or switch that bot into a safer mode before copying it into a new variant.`,
    });
  }

  notes.push({
    title: "What Seems To Work",
    tone: T.blue,
    body: safeBots.length ? `${safeBots.length} bot${safeBots.length > 1 ? "s are" : " is"} holding up with decent win rate and controlled drawdown.` : "No bot is cleanly separating from the pack yet.",
    reason: safeBots.length ? "Higher win rate with limited damage usually means the current bet size and threshold mix are closer to the market tempo." : "The system still needs sharper sizing or better trigger quality.",
    change: safeBots.length ? "Use those bots as templates for variants with tiny adjustments instead of reinventing everything." : "Test smaller scale, lower aggression, and tighter goal/floor spacing in Workshop.",
  });

  notes.push({
    title: "What Should Change",
    tone: T.yellow,
    body: stressed.length ? `${stressed.length} bot${stressed.length > 1 ? "s are" : " is"} stressed enough to justify intervention.` : "Portfolio stress is manageable, so optimize for cleaner consistency instead of emergency cuts.",
    reason: stressed.length ? "When multiple bots are deep red at once, the issue is often system-wide sizing or weak strategy fit, not just one bad run." : "This is the moment to refine rather than overhaul.",
    change: stressed.length ? "Lower scale on losers, preserve winners, and clone one controlled variant for each weak bot." : "Test one variable at a time: scale, mode, goal, or floor.",
  });

  if (proj && Object.keys(proj).length) {
    const bestProjection = Object.entries(proj).sort((a, b) => (b[1]?.p50_final || 0) - (a[1]?.p50_final || 0))[0];
    if (bestProjection) {
      notes.push({
        title: "Forward Expectation",
        tone: T.purple,
        body: `${bestProjection[0]} has the strongest median path in projections right now.`,
        reason: `Its projected p50 ending value is $${(bestProjection[1]?.p50_final || 0).toFixed(2)}, which is the best middle-case outcome in the current set.`,
        change: `Give this bot the first workshop clone and test better thresholds before expanding others.`,
      });
    }
  }

  return notes;
}

// ─────────────────────────────────────────────────────────────────────────────
//  GOAL BAR
// ─────────────────────────────────────────────────────────────────────────────
function GoalBar({ bankroll, start, target, floor, withdrawAt, cautionAt, recoveryAt }) {
  const min = 0;
  const max = target || start * 5;
  const range = max - min;
  const toX = v => `${Math.max(0, Math.min(100, ((v - min) / range) * 100))}%`;
  const fillPct = Math.max(0, Math.min(100, ((bankroll - min) / range) * 100));
  const danger  = bankroll <= (floor || start * 0.4);
  const fillColor = danger ? T.red
    : bankroll <= (recoveryAt || start * 0.9) ? T.orange
    : bankroll <= (cautionAt  || start * 0.95) ? T.yellow
    : T.green;

  const markers = [
    { v: floor      || start * 0.4,  label: "FLOOR",   color: T.red    },
    { v: recoveryAt || start * 0.9,  label: "-10%",    color: T.orange },
    { v: cautionAt  || start * 0.95, label: "-5%",     color: T.yellow },
    { v: withdrawAt || start * 1.2,  label: "WITHDRAW", color: T.blue  },
    { v: target     || start * 5,    label: "GOAL",    color: T.green  },
  ].filter(m => m.v > 0 && m.v <= max);

  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ position: "relative", height: 4, background: T.line, borderRadius: 999, overflow: "visible" }}>
        <div style={{
          position: "absolute", left: 0, top: 0, height: "100%",
          width: `${fillPct}%`, background: fillColor,
          borderRadius: 999, transition: "width 0.4s ease",
        }} />
        {markers.map(m => (
          <div key={m.label} style={{
            position: "absolute", top: -3, bottom: -3, left: toX(m.v),
            width: 1, background: m.color, opacity: 0.65,
          }} />
        ))}
      </div>
      <div style={{ position: "relative", height: 12, marginTop: 3 }}>
        {markers.map(m => (
          <div key={m.label} style={{
            position: "absolute", left: toX(m.v), transform: "translateX(-50%)",
            fontSize: 7, color: m.color, fontWeight: 700, letterSpacing: "0.06em", whiteSpace: "nowrap",
          }}>{m.label}</div>
        ))}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 8, color: T.muted, marginTop: 2 }}>
        <span style={{ color: fillColor, fontWeight: 700 }}>${bankroll.toFixed(2)}</span>
        <span>${(target || start * 5).toFixed(0)}</span>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  BOT SETUP CARD
// ─────────────────────────────────────────────────────────────────────────────
function BotSetupCard({ bot, token, onSaved }) {
  const [start,  setStart]  = useState(String(bot.start_amount  || 20));
  const [target, setTarget] = useState(String(bot.target_amount || (bot.start_amount || 20) * 5));
  const [floor,  setFloor]  = useState(String(bot.floor_amount  || (bot.start_amount || 20) * 0.4));
  const [saving, setSaving] = useState(false);
  const [msg,    setMsg]    = useState("");

  const withdrawAt = (parseFloat(start) || 20) * 1.2;

  const save = async () => {
    const s = parseFloat(start); const t = parseFloat(target); const f = parseFloat(floor);
    if (!s || !t || t <= s) { setMsg("Target must be > start."); return; }
    setSaving(true);
    try {
      const r = await fetch(`${API}/bots/${bot.id}/configure`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ start_amount: s, target_amount: t, floor_amount: f }),
      });
      const d = await r.json();
      setMsg(d.ok ? "Saved." : d.error || "Error");
      if (d.ok) onSaved?.();
    } catch { setMsg("Network error"); }
    setSaving(false);
    setTimeout(() => setMsg(""), 2500);
  };

  return (
    <div style={{ marginTop: 12, padding: "12px 0", borderTop: `1px solid ${T.line}` }}>
      <Label style={{ marginBottom: 10 }}>Configure</Label>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginBottom: 8 }}>
        {[["Start $", start, setStart], ["Target $", target, setTarget], ["Floor $", floor, setFloor]].map(([lbl2, val, set]) => (
          <div key={lbl2}>
            <div style={{ fontSize: 9, color: T.muted, marginBottom: 4 }}>{lbl2}</div>
            <input value={val} onChange={e => set(e.target.value)}
              style={{ width: "100%", padding: "6px 8px", borderRadius: 6, fontSize: 11 }} />
          </div>
        ))}
      </div>
      <div style={{ fontSize: 9, color: T.muted, marginBottom: 8 }}>
        Auto-withdraw at ${withdrawAt.toFixed(2)} back to ${parseFloat(start).toFixed(2)}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <ActionBtn onClick={save} disabled={saving}>{saving ? "SAVING..." : "APPLY"}</ActionBtn>
        {msg && <span style={{ fontSize: 10, color: msg === "Saved." ? T.green : T.red }}>{msg}</span>}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────
//  NOTIFICATION BELL
// ─────────────────────────────────────────────────────────────────────────────
function NotificationBell({ notifications, onClear }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const unread = notifications.length;

  useEffect(() => {
    if (!open) return;
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button onClick={() => setOpen(p => !p)} style={{
        background: "none", border: "none", cursor: "pointer", padding: "4px 6px",
        color: unread > 0 ? T.yellow : T.muted, position: "relative",
      }}>
        {/* Bell icon */}
        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6V11c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5S10.5 3.17 10.5 4v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/>
        </svg>
        {unread > 0 && (
          <span style={{
            position: "absolute", top: 0, right: 0,
            width: 14, height: 14, borderRadius: "50%",
            background: T.yellow, color: "#000",
            fontSize: 8, fontWeight: 900,
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>{unread > 9 ? "9+" : unread}</span>
        )}
      </button>

      {open && (
        <div style={{
          position: "absolute", right: 0, top: "calc(100% + 6px)", zIndex: 300,
          width: 280, background: T.bg === "#000000" ? "#0d0d0d" : "#f0f0f0",
          border: `1px solid ${T.line}`, borderRadius: 10,
          boxShadow: "0 8px 32px rgba(0,0,0,0.5)", overflow: "hidden",
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
            padding: "10px 14px", borderBottom: `1px solid ${T.line}` }}>
            <span style={{ fontSize: 10, fontWeight: 800, letterSpacing: "0.1em", color: T.fg }}>
              ALERTS {unread > 0 ? `(${unread})` : ""}
            </span>
            {unread > 0 && (
              <button onClick={() => { onClear(); setOpen(false); }}
                style={{ fontSize: 9, color: T.muted, background: "none", border: "none",
                  cursor: "pointer", fontWeight: 700, letterSpacing: "0.08em" }}>
                CLEAR ALL
              </button>
            )}
          </div>
          {notifications.length === 0 ? (
            <div style={{ padding: "20px 14px", fontSize: 11, color: T.muted, textAlign: "center" }}>
              No alerts
            </div>
          ) : (
            <div style={{ maxHeight: 320, overflowY: "auto" }}>
              {notifications.map((n, i) => (
                <div key={i} style={{ padding: "10px 14px", borderBottom: `1px solid ${T.line}44`,
                  display: "flex", gap: 10, alignItems: "flex-start" }}>
                  <div style={{ width: 6, height: 6, borderRadius: "50%", marginTop: 4, flexShrink: 0,
                    background: n.type === "milestone" ? T.yellow : n.type === "circuit_breaker" ? T.orange : T.green }} />
                  <div>
                    <div style={{ fontSize: 11, color: T.fg, fontWeight: 700 }}>{n.title}</div>
                    <div style={{ fontSize: 10, color: T.muted, marginTop: 2 }}>{n.body}</div>
                    <div style={{ fontSize: 9, color: T.muted, marginTop: 3, opacity: 0.6 }}>{n.time}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── SHARED PRIMITIVES ────────────────────────────────────────────────────────
function SectionHeader({ children, right, style }) {
  return (
    <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center",
      marginBottom: SP.sm, paddingBottom: SP.xs,
      borderBottom: `1px solid ${T.line}`,
      ...style }}>
      <span style={{ fontSize:9, fontWeight:800, letterSpacing:"0.16em", color:T.muted,
        textTransform:"uppercase", userSelect:"none" }}>{children}</span>
      {right && <span style={{ fontSize:9, color:T.muted, letterSpacing:"0.06em" }}>{right}</span>}
    </div>
  );
}

// StatBlock: T1 (hero) by default; pass tier="t2" for card-level values
function StatBlock({ label, value, sub, color, style, tier = "t1" }) {
  const sz = tier === "t1" ? 30 : tier === "t2" ? 22 : 18;
  return (
    <div style={{ ...style }}>
      <div style={{ fontSize:9, fontWeight:700, color:T.muted, letterSpacing:"0.14em",
        marginBottom: SP.xs, textTransform:"uppercase", userSelect:"none" }}>{label}</div>
      <div style={{ fontSize: sz, fontWeight:800, color:color||T.fg, letterSpacing:"-0.03em",
        lineHeight:1 }}>{value}</div>
      {sub && <div style={{ fontSize:10, color:T.muted, marginTop: SP.xs, lineHeight:1.5,
        letterSpacing:"0.02em" }}>{sub}</div>}
    </div>
  );
}

// Full status vocabulary — used consistently across all views
function StatusChip({ status }) {
  const map = {
    live:       { label:"LIVE",    color:"#59d47a" },
    healthy:    { label:"HEALTHY", color:"#59d47a" },
    paper:      { label:"PAPER",   color:"#888"    },
    paused:     { label:"PAUSED",  color:"#d6af41" },
    warning:    { label:"WARN",    color:"#ff8f5a" },
    warn:       { label:"WARN",    color:"#ff8f5a" },
    error:      { label:"ERROR",   color:"#ef5f57" },
    idle:       { label:"IDLE",    color:"#5f5f5f" },
    stale:      { label:"STALE",   color:"#5f5f5f" },
    floor:      { label:"FLOOR",   color:"#ef5f57" },
    turbo:      { label:"TURBO",   color:"#00ff88" },
    aggressive: { label:"AGGR",    color:"#59d47a" },
    normal:     { label:"NORMAL",  color:"#5ea1ff" },
    safe:       { label:"SAFE",    color:"#d6af41" },
    ultra_safe: { label:"CAUTIOUS",color:"#ff8f5a" },
    careful:    { label:"CAREFUL", color:"#c089ff" },
  };
  const s = map[status?.toLowerCase()] || { label:(status||"?").toUpperCase().slice(0,8), color:"#666" };
  return (
    <span style={{ fontSize:8, fontWeight:800, letterSpacing:"0.1em",
      padding:"2px 6px", borderRadius:3,
      background:`${s.color}15`, color:s.color, border:`1px solid ${s.color}28`,
      whiteSpace:"nowrap", userSelect:"none", display:"inline-block" }}>
      {s.label}
    </span>
  );
}

function EmptyState({ message, style }) {
  return (
    <div style={{ padding:"44px 0", textAlign:"center", color:T.muted,
      fontSize:11, letterSpacing:"0.08em", lineHeight:1.8, ...style }}>
      <div style={{ fontSize:18, marginBottom: SP.sm, opacity:0.3 }}>—</div>
      {message || "No data yet"}
    </div>
  );
}

function InlineNotice({ type="info", children, style }) {
  const col = type==="warn"?"#ff8f5a":type==="error"?"#ef5f57":type==="ok"?"#59d47a":"#5ea1ff";
  return (
    <div style={{ fontSize:10, color:col, padding:"9px 13px",
      background:`${col}0e`, borderRadius:6, border:`1px solid ${col}22`,
      lineHeight:1.7, ...style }}>
      {children}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  LOGIN
// ─────────────────────────────────────────────────────────────────────────────
function Login({ onLogin, theme, onToggleTheme }) {
  const [pwd, setPwd]       = useState("");
  const [show, setShow]     = useState(false);
  const [err, setErr]       = useState("");
  const [busy, setBusy]     = useState(false);
  const [serverDown, setServerDown] = useState(false);

  const submit = async () => {
    if (!pwd) { setErr("Enter password"); return; }
    setBusy(true); setErr("");
    try {
      const r = await fetch(`${API}/auth`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pwd, device_id: getDeviceId() }),
      });
      const d = await r.json();
      if (d.ok) { storeToken(d.token); onLogin(d.token); }
      else if (d.pending) setErr(d.msg);
      else setErr("Wrong password.");
    } catch { setErr("Cannot reach server."); setServerDown(true); }
    setBusy(false);
  };

  return (
    <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <style>{getGlobalCss()}</style>
      <div style={{ width: 320, padding: "0 20px" }}>
        <button onClick={onToggleTheme}
          style={{ background: "none", border: "none", padding: 0, color: T.fg,
            fontSize: 36, fontWeight: 800, letterSpacing: "-0.08em", marginBottom: 32 }}>
          DeG£N$
          <span style={{ fontSize: 10, color: T.muted, verticalAlign: "super", marginLeft: 8, letterSpacing: "0.18em" }}>
            {theme === "dark" ? "L" : "D"}
          </span>
        </button>
        <Label style={{ marginBottom: 8 }}>Password</Label>
        <div style={{ position: "relative" }}>
          <input
            type={show ? "text" : "password"} value={pwd}
            onChange={e => setPwd(e.target.value)} onKeyDown={e => e.key === "Enter" && submit()}
            autoFocus placeholder="--"
            style={{ width: "100%", padding: "11px 36px 11px 0", borderTop: "none", borderLeft: "none", borderRight: "none",
              borderBottom: `1px solid ${err ? "rgba(239,95,87,0.5)" : T.line}`, borderRadius: 0, fontSize: 14 }}
          />
          <button onClick={() => setShow(p => !p)}
            style={{ position: "absolute", right: 4, top: "50%", transform: "translateY(-50%)",
              background: "none", border: "none", color: T.muted, fontSize: 10 }}>
            {show ? "HIDE" : "SHOW"}
          </button>
        </div>
        {err && <div style={{ fontSize: 10, color: T.red, marginTop: 8 }}>{err}</div>}
        <button onClick={submit} disabled={busy}
          style={{ marginTop: 24, background: "none", border: "none", color: busy ? T.muted : T.fg,
            fontSize: 11, fontWeight: 700, letterSpacing: "0.13em", padding: 0,
            display: "flex", alignItems: "center", gap: 8 }}>
          {busy ? <Spinner size={12} /> : null}
          {busy ? "AUTHENTICATING..." : "UNLOCK DASHBOARD"}
        </button>
        {serverDown && (
          <div style={{ marginTop: 28, borderTop: `1px solid ${T.line}`, paddingTop: 20 }}>
            <div style={{ fontSize: 10, color: T.muted, marginBottom: 12, letterSpacing: "0.06em" }}>
              Server offline — browse with mock data
            </div>
            <button onClick={() => onLogin(DEMO_TOKEN)}
              style={{ background: "none", border: `1px solid ${T.line}`, color: T.muted,
                fontSize: 10, fontWeight: 700, letterSpacing: "0.13em", padding: "8px 14px",
                borderRadius: 6, cursor: "pointer" }}>
              DEMO MODE
            </button>
          </div>
        )}

      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  TOOLBAR
// ─────────────────────────────────────────────────────────────────────────────
function Toolbar({ token, strategy, onStrategy, onDeposit }) {
  const { isMobile, isTablet } = useViewport();
  const [goalInput, setGoalInput] = useState("");
  const [saved, setSaved]         = useState(false);

  const api = (path, opts = {}) =>
    fetch(path, { ...opts, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` } });

  const setStrat = async mode => {
    await api(`${API}/bots/strategy`, { method: "POST", body: JSON.stringify({ mode }) });
    onStrategy(mode);
  };

  const saveGoal = async () => {
    const t = parseFloat(goalInput);
    if (!t || t < 1) return;
    await api(`${API}/goals`, { method: "POST", body: JSON.stringify({ target: t }) });
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: SP.lg, flexWrap: "wrap",
      borderBottom: `1px solid ${T.line}`,
      padding: "9px 18px", flexShrink: 0,
      background: "rgba(255,255,255,0.012)",
    }}>
      {/* Mode group */}
      <div style={{ display: "flex", gap: SP.xs, alignItems: "center" }}>
        <Label style={{ marginRight: 4, whiteSpace: "nowrap" }}>Mode</Label>
        {STRATEGY_DEFS.map(s => (
          <Chip key={s.id} active={strategy === s.id} onClick={() => setStrat(s.id)}
            style={{ fontSize: 9, padding: "4px 9px" }}>
            {s.label}
            <span style={{ marginLeft: 4, opacity: 0.55, fontWeight: 400 }}>{s.scale}</span>
          </Chip>
        ))}
      </div>

      {/* Divider */}
      {!isMobile && <div style={{ width: 1, height: 18, background: T.line }} />}

      {/* Goal group */}
      <div style={{ display: "flex", gap: SP.xs, alignItems: "center" }}>
        <Label style={{ marginRight: 4, whiteSpace: "nowrap" }}>Portfolio Goal</Label>
        <input value={goalInput} onChange={e => setGoalInput(e.target.value)}
          onKeyDown={e => e.key === "Enter" && saveGoal()}
          placeholder="$6000"
          style={{ width: 78, padding: "5px 9px", borderRadius: 5, fontSize: 10, color: T.muted }} />
        <Chip onClick={saveGoal} style={{ fontSize: 9, padding: "4px 9px",
          color: saved ? T.green : undefined,
          borderColor: saved ? `${T.green}44` : undefined }}>
          {saved ? "✓ SAVED" : "SET"}
        </Chip>
      </div>

      {/* Divider */}
      {!isMobile && <div style={{ width: 1, height: 18, background: T.line }} />}

      {/* Fund group */}
      <div style={{ display: "flex", gap: SP.xs, alignItems: "center" }}>
        <Label style={{ marginRight: 4, whiteSpace: "nowrap" }}>Quick Fund</Label>
        {[100, 500, 1000].map(amt => (
          <ActionBtn key={amt} onClick={() => onDeposit(amt)}
            style={{ fontSize: 9, padding: "4px 9px" }}>
            +${amt}
          </ActionBtn>
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  VOLUME SPIKE FEED WIDGET
// ─────────────────────────────────────────────────────────────────────────────
function VolumeSpikeFeed({ token }) {
  const [spikes, setSpikes] = useState([]);
  useEffect(() => {
    const load = () =>
      fetch(`${API}/volume-spikes`, { headers: { Authorization: `Bearer ${token}` } })
        .then(r => r.json())
        .then(d => setSpikes(d.spikes || []))
        .catch(() => {});
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [token]);

  if (!spikes.length) return (
    <div style={{ fontSize: 10, color: T.muted }}>No volume spikes detected.</div>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {spikes.slice(0, 5).map((s, i) => (
        <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "6px 0", borderBottom: `1px solid ${T.line}44`, fontSize: 10 }}>
          <span style={{ color: T.fg, flex: 1, marginRight: 8 }}>{(s.question || "").slice(0, 50)}</span>
          <span style={{ color: T.orange, fontWeight: 700, marginRight: 8 }}>{(s.spike_ratio || 0).toFixed(1)}x vol</span>
          <span style={{ color: T.muted }}>${(s.yes_price || 0).toFixed(2)}</span>
        </div>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  ARBITRAGE PANEL
// ─────────────────────────────────────────────────────────────────────────────
function ArbitragePanel({ token }) {
  const [opps, setOpps] = useState([]);
  useEffect(() => {
    const load = () =>
      fetch(`${API}/arbitrage`, { headers: { Authorization: `Bearer ${token}` } })
        .then(r => r.json())
        .then(d => setOpps(d.opportunities || []))
        .catch(() => {});
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, [token]);

  if (!opps.length) return (
    <div style={{ fontSize: 10, color: T.muted }}>No arbitrage opportunities detected.</div>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {opps.slice(0, 5).map((o, i) => (
        <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "6px 0", borderBottom: `1px solid ${T.line}44`, fontSize: 10 }}>
          <span style={{ color: T.fg, flex: 1, marginRight: 8 }}>{(o.question || "").slice(0, 50)}</span>
          <span style={{ color: o.direction === "yes" ? T.green : T.orange, fontWeight: 700, marginRight: 8 }}>
            {(o.direction || "").toUpperCase()}
          </span>
          <span style={{ color: T.green, fontWeight: 700 }}>+{((o.edge || 0) * 100).toFixed(1)}%</span>
        </div>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  OVERVIEW TAB
// ─────────────────────────────────────────────────────────────────────────────
function OverviewTab({ bots, equity, info, historySummary, token }) {
  const { isMobile, isTablet } = useViewport();
  const overviewSeries = useMemo(() => buildOverviewSeries(bots, equity), [bots, equity]);
  const t = useMemo(() => {
    const bank     = bots.reduce((s, b) => s + b.bankroll, 0);
    const locked   = bots.reduce((s, b) => s + (b.total_withdrawn || 0), 0);
    const vault    = bots.reduce((s, b) => s + (b.vault || 0), 0);
    const starts   = bots.reduce((s, b) => s + (b.start_amount || 100), 0);
    const histRows = Object.values(historySummary || {});
    const hBets    = histRows.reduce((s, r) => s + (r.bets || 0), 0);
    const hWins    = histRows.reduce((s, r) => s + (r.wins || 0), 0);
    const hPnL     = histRows.reduce((s, r) => s + (r.pnl  || 0), 0);
    const progress = bank + locked + vault;
    return {
      bank, locked, vault, starts, progress,
      pnl:     progress - starts,
      pnlPct:  starts ? ((progress / starts) - 1) * 100 : 0,
      active:  bots.filter(b => !b.halted).length,
      danger:  bots.filter(b => b.danger).length,
      winRate: bots.length ? (bots.reduce((s, b) => s + (b.winRate || 0), 0) / bots.length).toFixed(1) : "0.0",
      bets:    bots.reduce((s, b) => s + (b.bets || 0), 0),
      goalPct: info?.goalPct || 0,
      hBets, hPnL,
      hWinRate: hBets ? ((hWins / hBets) * 100).toFixed(1) : "0.0",
    };
  }, [bots, info, historySummary]);
  // Derive status alerts from existing frontend state
  const coolingBots = bots.filter(b => b.coolingDown);
  const haltedBots  = bots.filter(b => b.halted);
  const staleBots   = bots.filter(b => b.bets === 0 && !b.halted);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: SP.xl, animation: "fadein .25s ease" }}>

      {/* ── Status Alert Strip ── only shown when there's something to flag ── */}
      {(t.danger > 0 || coolingBots.length > 0 || haltedBots.length > 0) && (
        <div style={{ display: "flex", flexDirection: "column", gap: SP.xs }}>
          {t.danger > 0 && (
            <InlineNotice type="error">
              <strong>{t.danger} bot{t.danger > 1 ? "s" : ""} at floor</strong> — emergency micro-bets active. Review in Bots tab.
            </InlineNotice>
          )}
          {coolingBots.length > 0 && (
            <InlineNotice type="warn">
              <strong>{coolingBots.length} bot{coolingBots.length > 1 ? "s" : ""} on circuit breaker</strong>
              {" — "}{coolingBots.map(b => `${b.id} (${Math.ceil(b.cooldownSec || 0)}s)`).join(", ")}
            </InlineNotice>
          )}
          {haltedBots.length > 0 && (
            <InlineNotice type="warn">
              <strong>{haltedBots.length} bot{haltedBots.length > 1 ? "s" : ""} halted</strong>
              {" — "}{haltedBots.map(b => b.id).join(", ")}
            </InlineNotice>
          )}
        </div>
      )}

      {/* ── Compact Bot Strip — snapshot first, always visible above charts ── */}
      <div>
        <SectionHeader right={`${bots.length} bots · full workspace → Bots tab`}>
          Bot Snapshot
        </SectionHeader>
        {bots.length === 0 ? (
          <EmptyState message="No bots running. Start the server first." />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
            {bots.map(b => {
              const phCol = PHASE_COLORS[b.phase] || T.muted;
              const d = equity.map(e => ({ v: e[b.id] || b.start_amount || 100 }));
              return (
                <div key={b.id} className="dg-row-hover" style={{
                  display: "grid",
                  gridTemplateColumns: isMobile
                    ? "1fr auto auto"
                    : "180px 1fr 64px 64px 64px 80px",
                  gap: SP.sm, alignItems: "center",
                  padding: `${SP.sm}px 0`,
                  borderBottom: `1px solid ${T.line}22`,
                }}>
                  {/* Name + platform */}
                  <div style={{ display: "flex", alignItems: "center", gap: SP.sm, minWidth: 0 }}>
                    <div style={{ width: 3, height: 28, borderRadius: 2, background: b.color, flexShrink: 0 }} />
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 11, fontWeight: 700, color: b.color,
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {b.name || b.id}
                      </div>
                      <div style={{ fontSize: 8, color: T.muted, letterSpacing: "0.08em" }}>
                        {b.platform === "stake" ? "STAKE" : "POLY"}
                      </div>
                    </div>
                  </div>
                  {/* Spark + goal bar */}
                  {!isMobile && (
                    <div style={{ minWidth: 0 }}>
                      <GoalBar bankroll={b.bankroll} start={b.start_amount || 100}
                        target={b.target_amount} floor={b.floor_amount}
                        withdrawAt={b.withdraw_at} cautionAt={b.caution_at} recoveryAt={b.recovery_at} />
                    </div>
                  )}
                  {/* Bankroll */}
                  <div style={{ textAlign: "right" }}>
                    <div style={{ fontSize: 13, fontWeight: 800, letterSpacing: "-0.02em" }}>
                      ${b.bankroll.toFixed(2)}
                    </div>
                  </div>
                  {/* ROI */}
                  <div style={{ textAlign: "right" }}>
                    <div style={{ fontSize: 11, fontWeight: 700,
                      color: (b.roi_pct || 0) >= 0 ? T.green : T.red }}>
                      {(b.roi_pct || 0) >= 0 ? "+" : ""}{(b.roi_pct || 0).toFixed(1)}%
                    </div>
                    <div style={{ fontSize: 8, color: T.muted }}>ROI</div>
                  </div>
                  {/* Win rate */}
                  {!isMobile && (
                    <div style={{ textAlign: "right" }}>
                      <div style={{ fontSize: 11, fontWeight: 700 }}>{(b.winRate || 0).toFixed(0)}%</div>
                      <div style={{ fontSize: 8, color: T.muted }}>WIN</div>
                    </div>
                  )}
                  {/* Phase chip + status dot */}
                  <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: SP.xs }}>
                    <span style={{
                      fontSize: 7, fontWeight: 800, padding: "2px 5px", borderRadius: 3,
                      background: `${phCol}18`, color: phCol, letterSpacing: "0.06em",
                      animation: b.phase === "turbo" ? "turbo-pulse 1.2s infinite" : "none",
                    }}>
                      {(b.phase || "").toUpperCase()}
                    </span>
                    <Dot ok={!b.halted} size={5} />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── T1 Metric Row ── */}
      <div style={{ display: "grid",
        gridTemplateColumns: isMobile ? "1fr 1fr" : isTablet ? "repeat(3,minmax(0,1fr))" : "repeat(6,minmax(0,1fr))",
        gap: SP.md }}>
        {[
          { k: "Portfolio",   v: `$${t.progress.toFixed(2)}`, s: `$${t.bank.toFixed(0)} active · $${t.locked.toFixed(0)} out` },
          { k: "P&L",         v: `${t.pnl >= 0 ? "+" : ""}$${t.pnl.toFixed(2)}`, s: `${t.pnlPct >= 0 ? "+" : ""}${t.pnlPct.toFixed(1)}% vs start`, c: t.pnl >= 0 ? T.green : T.red },
          { k: "Vault",       v: `$${t.vault.toFixed(2)}`,    s: "locked · never re-risked", c: T.yellow },
          { k: "Withdrawn",   v: `$${t.locked.toFixed(2)}`,   s: "profit out of play", c: T.blue },
          { k: "Win Rate",    v: `${t.winRate}%`,              s: `${t.active}/${bots.length} bots active`, c: T.blue },
          { k: "Session Bets",v: t.bets.toLocaleString(),      s: `${t.hBets.toLocaleString()} all-time` },
        ].map((m, i) => (
          <div key={m.k} style={{
            borderTop: `1px solid ${i === 0 ? T.blue + "55" : T.line}`,
            paddingTop: SP.sm,
          }}>
            <Label style={{ marginBottom: SP.sm }}>{m.k}</Label>
            <div style={{ fontSize: i === 0 ? 28 : 22, fontWeight: 800,
              letterSpacing: "-0.04em", color: m.c || T.fg, lineHeight: 1 }}>
              {m.v}
            </div>
            <div style={{ color: T.muted, fontSize: 10, marginTop: SP.xs, lineHeight: 1.5 }}>{m.s}</div>
          </div>
        ))}
      </div>

      {/* ── Portfolio Equity Chart ── */}
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline",
          marginBottom: SP.sm }}>
          <SectionHeader style={{ marginBottom: 0, borderBottom: "none", flex: 1 }}>
            Portfolio Equity
          </SectionHeader>
          <div style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.04em" }}>
            ${t.progress.toFixed(2)}
          </div>
        </div>
        <div style={{ borderTop: `1px solid ${T.line}`, height: 200 }}>
          <GlowArea data={equity} dataKey="portfolio" color={T.blue} height={210}
            refLines={[{ y: t.starts, color: "rgba(255,255,255,0.12)", label: "start" }]} />
        </div>
        {/* Bot legend */}
        <div style={{ marginTop: SP.sm, display: "flex", flexWrap: "wrap", gap: `${SP.xs}px ${SP.md}px` }}>
          {bots.map(b => (
            <span key={b.id} style={{ fontSize: 9, color: b.color, display: "flex", alignItems: "center", gap: 4 }}>
              <span style={{ width: 5, height: 5, borderRadius: "50%", background: "currentColor", display: "inline-block" }} />
              {b.name || b.id}
            </span>
          ))}
        </div>
      </div>

      {/* ── Money Flow Chart + Side Stats ── */}
      <div>
        <SectionHeader>Money Flow</SectionHeader>
        <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1.3fr 0.7fr", gap: SP.md }}>
          <div style={{ height: 260 }}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={overviewSeries} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
                <defs>
                  <linearGradient id="moneyFlow" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={T.blue} stopOpacity={0.2} />
                    <stop offset="100%" stopColor={T.blue} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke={T.line} />
                <XAxis dataKey="label" tick={{ fontSize: 8, fill: T.muted }} tickLine={false} axisLine={false}
                  interval={Math.max(1, Math.floor((overviewSeries.length || 1) / 7))} />
                <YAxis yAxisId="money" tick={{ fontSize: 8, fill: T.muted }} tickLine={false} axisLine={false} />
                <YAxis yAxisId="pct" orientation="right" tick={{ fontSize: 8, fill: T.muted }} tickLine={false} axisLine={false} />
                <Tooltip content={<Tip />} />
                <ReferenceLine yAxisId="money" y={t.starts} stroke={T.line} strokeDasharray="4 4" />
                <Area yAxisId="money" type="monotone" dataKey="portfolio" stroke={T.blue} strokeWidth={2}
                  fill="url(#moneyFlow)" name="Portfolio" dot={false} isAnimationActive={false} />
                <Line yAxisId="money" type="monotone" dataKey="pnl" stroke={t.pnl >= 0 ? T.green : T.red}
                  strokeWidth={1.4} name="P&L" dot={false} isAnimationActive={false} />
                <Line yAxisId="pct" type="monotone" dataKey="progress" stroke={T.yellow}
                  strokeWidth={1.2} name="Progress %" dot={false} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          {/* Side stats */}
          <div style={{ display: "grid", gap: SP.sm, alignContent: "start" }}>
            {[
              { k: "P&L", v: `${t.pnl >= 0 ? "+" : ""}$${t.pnl.toFixed(2)}`, c: t.pnl >= 0 ? T.green : T.red },
              { k: "vs Start", v: `${((t.progress / Math.max(t.starts, 1)) * 100).toFixed(1)}%` },
              { k: "Secured", v: `$${(t.locked + t.vault).toFixed(2)}` },
              { k: "Goal", v: `${(+(info?.goalPct ?? 0)).toFixed(1)}%`, c: T.green },
            ].map(m => (
              <div key={m.k} style={{ borderTop: `1px solid ${T.line}`, paddingTop: SP.sm }}>
                <Label style={{ marginBottom: SP.xs }}>{m.k}</Label>
                <div style={{ fontSize: 18, fontWeight: 800, letterSpacing: "-0.04em",
                  color: m.c || T.fg }}>{m.v}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ── All-Time Record ── */}
      <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: SP.md }}>
        <SectionHeader>All-Time Record</SectionHeader>
        <div style={{ display: "flex", gap: SP.xl, flexWrap: "wrap" }}>
          {[
            { k: "Bets",     v: t.hBets.toLocaleString() },
            { k: "Win Rate", v: `${t.hWinRate}%` },
            { k: "P&L",      v: `${t.hPnL >= 0 ? "+" : ""}$${t.hPnL.toFixed(2)}`, c: t.hPnL >= 0 ? T.green : T.red },
            { k: "Vault",    v: `$${t.vault.toFixed(2)}`, c: T.yellow },
          ].map(m => (
            <div key={m.k}>
              <Label style={{ marginBottom: SP.xs }}>{m.k}</Label>
              <div style={{ fontSize: 18, fontWeight: 800, letterSpacing: "-0.04em",
                color: m.c || T.fg }}>{m.v}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Live Market Signals ── */}
      <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr", gap: SP.md,
        borderTop: `1px solid ${T.line}`, paddingTop: SP.md }}>
        <div>
          <SectionHeader>Volume Spikes</SectionHeader>
          <VolumeSpikeFeed token={token} />
        </div>
        <div>
          <SectionHeader>Arbitrage Opportunities</SectionHeader>
          <ArbitragePanel token={token} />
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  BOTS TAB
// ─────────────────────────────────────────────────────────────────────────────
function BotsTab({ token, bots, equity, botFilter, setBotFilter, expandedBots, setExpandedBots }) {
  const [scales,   setScales]   = useState({});
  const [modes,    setModes]    = useState({});
  const [feedback, setFeedback] = useState({});
  const [expanded, setExpanded] = useState({});

  useEffect(() => {
    const s = {}; const m = {};
    bots.forEach(b => { s[b.id] = b.bet_scale ?? 1.0; m[b.id] = b.strategy_mode ?? "balanced"; });
    setScales(s); setModes(m);
  }, [bots]);

  const api = (path, opts = {}) =>
    fetch(path, { ...opts, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` } });

  const setMode = async (bid, mode) => {
    setModes(p => ({ ...p, [bid]: mode }));
    await api(`${API}/bots/${bid}/strategy`, { method: "POST", body: JSON.stringify({ mode }) });
    flash(bid);
  };

  const applyScale = async (bid, scale) => {
    setScales(p => ({ ...p, [bid]: scale }));
    await api(`${API}/bots/${bid}/scale`, { method: "POST", body: JSON.stringify({ scale }) });
    flash(bid);
  };

  const flash = bid => {
    setFeedback(p => ({ ...p, [bid]: true }));
    setTimeout(() => setFeedback(p => ({ ...p, [bid]: false })), 900);
  };

  const filteredBots = bots.filter(b => {
    if (botFilter === "all") return true;
    if (botFilter === "stake") {
      const g = (b.game || b.strategy || "").toLowerCase();
      return g.includes("dice") || g.includes("limbo") || g.includes("mines") || b.platform === "stake";
    }
    if (botFilter === "poly") {
      const g = (b.game || b.strategy || "").toLowerCase();
      return g.includes("poly") || b.platform === "poly" || b.platform === "polymarket";
    }
    if (botFilter === "paused") {
      return (b.cooldownSec || 0) > 0 || b.phase === "floor";
    }
    return true;
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: SP.md, animation: "fadein .25s ease" }}>
      {/* ── Filter bar ── */}
      <div style={{ display: "flex", gap: SP.xs, alignItems: "center", flexWrap: "wrap",
        borderBottom: `1px solid ${T.line}`, paddingBottom: SP.sm }}>
        {[["all","ALL"],["stake","STAKE"],["poly","POLY"],["paused","PAUSED"]].map(([id, label]) => (
          <button key={id} onClick={() => setBotFilter(id)} className="dg-nav-btn"
            style={{
              padding: "4px 10px", borderRadius: 5, fontSize: 9, fontWeight: 800,
              letterSpacing: "0.1em", cursor: "pointer",
              background: botFilter === id ? `${T.blue}18` : "transparent",
              border: `1px solid ${botFilter === id ? T.blue + "66" : T.line}`,
              color: botFilter === id ? T.blue : T.muted,
              transition: "all .15s",
            }}>
            {label}
            {id !== "all" && <span style={{ marginLeft: 5, fontSize: 8, opacity: 0.65 }}>
              {id === "stake" ? bots.filter(b => { const g=(b.game||b.strategy||"").toLowerCase(); return g.includes("dice")||g.includes("limbo")||g.includes("mines")||b.platform==="stake"; }).length
               : id === "poly" ? bots.filter(b => { const g=(b.game||b.strategy||"").toLowerCase(); return g.includes("poly")||b.platform==="poly"||b.platform==="polymarket"; }).length
               : bots.filter(b => (b.cooldownSec||0)>0||b.phase==="floor").length}
            </span>}
          </button>
        ))}
        <span style={{ fontSize: 10, color: T.muted, marginLeft: 4 }}>{filteredBots.length} bot{filteredBots.length !== 1 ? "s" : ""}</span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 14 }}>
        {filteredBots.map(b => {
          const d      = equity.map(e => ({ v: e[b.id] || b.start_amount || 100 }));
          const sc     = scales[b.id] ?? 1.0;
          const md     = modes[b.id]  ?? "balanced";
          const exp    = expanded[b.id];
          const phCol  = PHASE_COLORS[b.phase] || T.muted;
          const isTurbo = b.phase === "turbo";
          const stratLbl = STRATEGY_LABELS[b.strategy] || (b.strategy || "").replace(/_/g," ");

          const cardExpanded = expandedBots[b.id] || false;

          return (
            <div key={b.id} style={{
              borderTop: `2px solid ${b.color}`, padding: "10px 0 0",
              opacity: b.halted ? 0.55 : 1, transition: "opacity .2s",
              animation: feedback[b.id] ? "glowPulse 0.9s ease" : "none",
            }}>
              {/* ── Compact header (always visible) ── */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: b.color }}>{b.name || b.id}</span>
                <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  {b.danger && <span style={{ color: T.red, animation: "pulse 1.5s infinite" }}><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg></span>}
                  {b.milestoneHit && <span style={{ color: T.yellow, display: "inline-flex", alignItems: "center" }}><svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg><span style={{ fontSize: 7, fontWeight: 700 }}>3x</span></span>}
                  <span style={{
                    fontSize: 7, fontWeight: 700, padding: "1px 4px", borderRadius: 3,
                    background: `${phCol}22`, color: phCol, letterSpacing: "0.05em",
                    animation: isTurbo ? "turbo-pulse 1.2s infinite" : "none",
                    display: "inline-flex", alignItems: "center", gap: 3,
                  }}>
                    {isTurbo && <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M7 2v11h3v9l7-12h-4l4-8z"/></svg>}
                    {(b.phase || "").toUpperCase()}
                  </span>
                  <Dot ok={!b.halted} size={5} />
                  {/* Chevron toggle */}
                  <button onClick={() => setExpandedBots(p => ({ ...p, [b.id]: !p[b.id] }))}
                    style={{ background: "none", border: "none", color: T.muted, cursor: "pointer",
                      padding: "0 2px", fontSize: 9, lineHeight: 1, marginLeft: 2 }}
                    title={cardExpanded ? "Collapse" : "Expand"}>
                    {cardExpanded ? "▲" : "▼"}
                  </button>
                </div>
              </div>

              {/* Compact stats row — always visible */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: SP.xs,
                marginBottom: SP.sm }}>
                <div>
                  <div style={{ fontSize: 8, color: T.muted, fontWeight: 700, letterSpacing: "0.1em",
                    textTransform: "uppercase", marginBottom: 2 }}>Bank</div>
                  <div style={{ fontSize: 15, fontWeight: 800, letterSpacing: "-0.02em" }}>
                    ${b.bankroll.toFixed(2)}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 8, color: T.muted, fontWeight: 700, letterSpacing: "0.1em",
                    textTransform: "uppercase", marginBottom: 2 }}>ROI</div>
                  <div style={{ fontSize: 15, fontWeight: 800, letterSpacing: "-0.02em",
                    color: (b.roi_pct||0) >= 0 ? T.green : T.red }}>
                    {(b.roi_pct||0) >= 0 ? "+" : ""}{(b.roi_pct||0).toFixed(1)}%
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 8, color: T.muted, fontWeight: 700, letterSpacing: "0.1em",
                    textTransform: "uppercase", marginBottom: 2 }}>Win%</div>
                  <div style={{ fontSize: 15, fontWeight: 800, letterSpacing: "-0.02em" }}>
                    {(b.winRate||0).toFixed(0)}%
                  </div>
                </div>
              </div>
              <div style={{ fontSize: 9, color: T.muted, letterSpacing: "0.04em",
                marginBottom: cardExpanded ? SP.sm : 0 }}>
                {b.bets || 0} bets · {b.platform === "stake" ? "STAKE" : "POLY"}
                {b.bets === 0 && <span style={{ color: T.muted, marginLeft: 6, opacity: 0.6 }}>— no activity yet</span>}
              </div>

              {/* ── Expanded body ── */}
              {cardExpanded && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ fontSize: 10, color: T.muted, marginBottom: 5, letterSpacing: "0.07em" }}>
                    {stratLbl.toUpperCase()}
                    {b.coolingDown && <span style={{ color: T.orange, marginLeft: 6 }}>CB {Math.ceil(b.cooldownSec)}s</span>}
                  </div>
                  {b.platform === "poly" && (
                    <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginBottom: 8 }}>
                      <Chip style={{ padding: "3px 6px", fontSize: 8, color: T.blue }}>REAL DATA</Chip>
                      <Chip style={{ padding: "3px 6px", fontSize: 8, color: T.yellow }}>PAPER EXEC</Chip>
                      <Chip style={{ padding: "3px 6px", fontSize: 8 }}>{b.scan_opportunity_count || 0} OPPS</Chip>
                      <Chip style={{ padding: "3px 6px", fontSize: 8 }}>{b.open_positions || 0} OPEN</Chip>
                    </div>
                  )}

                  <div style={{ fontSize: 10, color: (b.roi_pct || 0) >= 0 ? T.green : T.red, marginBottom: 7 }}>
                    streak {(b.streak || 0) >= 0 ? "+" : ""}{b.streak || 0}
                  </div>

                  <GoalBar bankroll={b.bankroll} start={b.start_amount || 100} target={b.target_amount}
                    floor={b.floor_amount} withdrawAt={b.withdraw_at} cautionAt={b.caution_at} recoveryAt={b.recovery_at} />

                  <div style={{ height: 22, borderTop: `1px solid ${b.color}22`,
                    background: `linear-gradient(180deg, ${b.color}08, transparent)`, marginBottom: 10 }}>
                    <MiniSpark data={d} dataKey="v" color={b.color} height={22} />
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 6, marginBottom: 10 }}>
                    {[
                      ["BANK",  `$${b.bankroll.toFixed(0)}`],
                      ["OUT",   `$${(b.total_withdrawn || 0).toFixed(0)}`],
                      ["VAULT", `$${(b.vault || 0).toFixed(0)}`],
                      ["W%",    `${(b.winRate || 0).toFixed(0)}%`],
                    ].map(([k, v]) => (
                      <div key={k}>
                        <Label style={{ marginBottom: 3 }}>{k}</Label>
                        <div style={{ fontSize: 11, fontWeight: 800 }}>{v}</div>
                      </div>
                    ))}
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 6, marginBottom: 10 }}>
                    {[
                      ["25B ROI", `${(b.rolling?.w25?.roi_pct || 0) >= 0 ? "+" : ""}${(b.rolling?.w25?.roi_pct || 0).toFixed(1)}%`],
                      ["100B ROI", `${(b.rolling?.w100?.roi_pct || 0) >= 0 ? "+" : ""}${(b.rolling?.w100?.roi_pct || 0).toFixed(1)}%`],
                      ["AUTO", `${b.autoDownscales || 0}x`],
                    ].map(([k, v]) => (
                      <div key={k}>
                        <Label style={{ marginBottom: 3 }}>{k}</Label>
                        <div style={{ fontSize: 11, fontWeight: 800 }}>{v}</div>
                      </div>
                    ))}
                  </div>
                  {b.lastDownscaleReason && (
                    <div style={{ fontSize: 10, color: T.yellow, lineHeight: 1.6, marginBottom: 10 }}>
                      Auto-managed: {b.lastDownscaleReason}
                    </div>
                  )}
                  {b.platform === "poly" && (b.scan_best_question || b.scan_best_edge != null) && (
                    <div style={{ fontSize: 10, color: T.muted, lineHeight: 1.6, marginBottom: 10 }}>
                      {b.scan_best_edge != null ? `Best edge ${b.scan_best_edge >= 0 ? "+" : ""}${Number(b.scan_best_edge).toFixed(3)}.` : "No live edge yet."}
                      {b.scan_best_question ? ` ${b.scan_best_question.slice(0, 70)}` : ""}
                    </div>
                  )}
                  {b.runtime_error && (
                    <div style={{ fontSize: 10, color: T.red, lineHeight: 1.6, marginBottom: 10 }}>
                      Feed issue: {b.runtime_error}
                    </div>
                  )}

                  <Label style={{ marginBottom: 6 }}>Strategy</Label>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 4, marginBottom: 10 }}>
                    {STRATEGY_DEFS.map(s => (
                      <button key={s.id} onClick={() => !b.halted && setMode(b.id, s.id)} disabled={b.halted}
                        style={{
                          padding: "5px 4px", borderRadius: 6, fontSize: 9, fontWeight: 700,
                          border: `1px solid ${md === s.id ? "rgba(94,161,255,0.4)" : T.line}`,
                          background: md === s.id ? "rgba(94,161,255,0.12)" : "transparent",
                          color: md === s.id ? T.blue : T.muted, letterSpacing: "0.04em",
                          cursor: b.halted ? "not-allowed" : "pointer",
                        }}>
                        {s.label} {s.scale}
                      </button>
                    ))}
                  </div>

                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
                    <Label>Bet Scale</Label>
                    <span style={{ fontSize: 9, color: T.blue, fontWeight: 700 }}>{sc.toFixed(1)}x</span>
                  </div>
                  <input type="range" min={0.1} max={5} step={0.1} value={sc}
                    disabled={b.halted}
                    onChange={e => setScales(p => ({ ...p, [b.id]: parseFloat(e.target.value) }))}
                    onMouseUp={e  => applyScale(b.id, parseFloat(e.target.value))}
                    onTouchEnd={e => applyScale(b.id, parseFloat(e.target.value))}
                    style={{ width: "100%", marginBottom: 3 }} />
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 8, color: T.muted }}>
                    <span>0.1x</span><span>2.5x</span><span>5x</span>
                  </div>

                  <button onClick={() => setExpanded(p => ({ ...p, [b.id]: !p[b.id] }))}
                    style={{ marginTop: 10, background: "none", border: "none", color: T.muted,
                      fontSize: 9, cursor: "pointer", letterSpacing: "0.1em", padding: 0 }}>
                    {exp ? "HIDE CONFIG" : "CONFIGURE"}
                  </button>

                  {exp && <BotSetupCard bot={b} token={token} onSaved={() => setExpanded(p => ({ ...p, [b.id]: false }))} />}

                  {/* Streak meter — last 7 bets as dots */}
                  {(b.last_7 || []).length > 0 && (
                    <div style={{ display: "flex", gap: 3, marginTop: 8 }}>
                      {(b.last_7 || []).map((w, i) => (
                        <div key={i} style={{
                          width: 8, height: 8, borderRadius: "50%",
                          background: w ? T.green : T.red, opacity: 0.8,
                        }} />
                      ))}
                    </div>
                  )}

                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: T.muted,
                    marginTop: 10, paddingTop: 10, borderTop: `1px solid ${T.line}` }}>
                    <span>{b.bets || 0} bets - {b.wins || 0}W/{b.losses || 0}L</span>
                    <span style={{ color: b.danger ? T.red : T.muted }}>{b.platform || "stake"}</span>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  ANALYTICS TAB
// ─────────────────────────────────────────────────────────────────────────────
function AnalyticsTab({ token, bots, equity }) {
  const { isMobile, isTablet } = useViewport();
  const [sub,     setSub]     = useState("equity");
  const [trades,  setTrades]  = useState([]);
  const [total,   setTotal]   = useState(0);
  const [proj,    setProj]    = useState(null);
  const [filters, setFilters] = useState({ bot_id: "", won: "", limit: 50, offset: 0 });
  const [loading, setLoading] = useState(false);
  const [variants, setVariants] = useState(() => {
    try { return JSON.parse(localStorage.getItem(WORKSHOP_KEY) || "[]"); } catch { return []; }
  });
  const [variantDraft, setVariantDraft] = useState({
    sourceBot: "",
    label: "",
    mode: "balanced",
    scale: 1,
    start_amount: 20,
    target_amount: 100,
    floor_amount: 8,
    tagsText: "experimental,paper",
  });

  const api = path => fetch(path, { headers: { Authorization: `Bearer ${token}` } });
  const authedApi = (path, opts = {}) => fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
  });

  useEffect(() => {
    if (sub !== "history") return;
    setLoading(true);
    const p = new URLSearchParams();
    if (filters.bot_id) p.append("bot_id", filters.bot_id);
    if (filters.won !== "") p.append("won", filters.won);
    p.append("limit", filters.limit); p.append("offset", filters.offset);
    api(`${API}/history?${p}`).then(r => r.json())
      .then(d => { setTrades(d.trades || []); setTotal(d.total || 0); setLoading(false); })
      .catch(() => setLoading(false));
  }, [sub, filters]);

  useEffect(() => {
    if (sub !== "projections" || proj) return;
    api(`${API}/projections`).then(r => r.json()).then(setProj).catch(() => {});
  }, [sub]);

  useEffect(() => {
    localStorage.setItem(WORKSHOP_KEY, JSON.stringify(variants));
  }, [variants]);

  useEffect(() => {
    if (!bots.length || variantDraft.sourceBot) return;
    const seed = bots[0];
    setVariantDraft({
      sourceBot: seed.id,
      label: `${seed.id}_variant`,
      mode: seed.strategy_mode || "balanced",
      scale: seed.bet_scale || 1,
      start_amount: seed.start_amount || 20,
      target_amount: seed.target_amount || 100,
      floor_amount: seed.floor_amount || 8,
      tagsText: deriveBotTags(seed).join(", "),
    });
  }, [bots, variantDraft.sourceBot]);

  const insightCards = useMemo(() => buildAnalyticsInsights(bots, proj), [bots, proj]);
  const activeVariants = useMemo(() => variants.filter(v => !v.archived), [variants]);
  const archivedVariants = useMemo(() => variants.filter(v => v.archived), [variants]);
  const strategyRows = useMemo(() => bots.map(b => ({
    id: b.id,
    winRate: b.winRate || 0,
    roi: b.roi_pct || 0,
    streak: b.streak || 0,
    bankroll: b.bankroll || 0,
    rolling25: b.rolling?.w25?.roi_pct || 0,
    rolling100: b.rolling?.w100?.roi_pct || 0,
    autoDownscales: b.autoDownscales || 0,
    lastDownscaleReason: b.lastDownscaleReason || "",
    tags: deriveBotTags(b),
    stress: b.danger ? "high" : (b.roi_pct || 0) < -8 ? "medium" : "low",
  })), [bots]);

  const createVariant = () => {
    if (!variantDraft.sourceBot || !variantDraft.label.trim()) return;
    setVariants(prev => [
      {
        id: `${Date.now()}`,
        archived: false,
        createdAt: new Date().toISOString(),
        tags: parseVariantTags(variantDraft.tagsText),
        ...variantDraft,
      },
      ...prev,
    ]);
  };

  const loadFromBot = botId => {
    const b = bots.find(x => x.id === botId);
    if (!b) return;
    setVariantDraft({
      sourceBot: b.id,
      label: `${b.id}_variant`,
      mode: b.strategy_mode || "balanced",
      scale: b.bet_scale || 1,
      start_amount: b.start_amount || 20,
      target_amount: b.target_amount || 100,
      floor_amount: b.floor_amount || 8,
      tagsText: deriveBotTags(b).join(", "),
    });
  };

  const applyVariant = async variant => {
    await authedApi(`${API}/bots/${variant.sourceBot}/strategy`, { method: "POST", body: JSON.stringify({ mode: variant.mode }) });
    await authedApi(`${API}/bots/${variant.sourceBot}/scale`, { method: "POST", body: JSON.stringify({ scale: Number(variant.scale) }) });
    await authedApi(`${API}/bots/${variant.sourceBot}/configure`, {
      method: "POST",
      body: JSON.stringify({
        start_amount: Number(variant.start_amount),
        target_amount: Number(variant.target_amount),
        floor_amount: Number(variant.floor_amount),
      }),
    });
  };

  const archiveVariant = id => {
    setVariants(prev => prev.map(v => v.id === id ? { ...v, archived: true, archivedAt: new Date().toISOString() } : v));
  };

  const restoreVariant = id => {
    setVariants(prev => prev.map(v => v.id === id ? { ...v, archived: false, archivedAt: null } : v));
  };

  const loadPreset = preset => {
    const baseBot = bots.find(b => b.id === variantDraft.sourceBot) || bots[0];
    if (!baseBot) return;
    const startAmount = Number(baseBot.start_amount || 20);
    setVariantDraft({
      sourceBot: baseBot.id,
      label: `${baseBot.id}_${preset.id}`,
      mode: preset.mode,
      scale: preset.scale,
      start_amount: startAmount,
      target_amount: Math.round(startAmount * preset.target_multiplier),
      floor_amount: Math.max(1, +(startAmount * preset.floor_multiplier).toFixed(2)),
      tagsText: `experimental,paper,${preset.id}`,
    });
  };

  const subTabs = [
    ["equity","Equity","Portfolio curves + strength"],
    ["history","History","Bet-level trade log"],
    ["projections","Projections","Monte Carlo forward paths"],
    ["workshop","Workshop","Strategy variant builder"],
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: SP.md, animation: "fadein .25s ease" }}>
      {/* ── Sub-tab navigation ── */}
      <div style={{ display: "flex", gap: 0, borderBottom: `1px solid ${T.line}` }}>
        {subTabs.map(([id, l, desc]) => (
          <button key={id} onClick={() => setSub(id)} className="dg-nav-btn"
            style={{ padding: "8px 16px", background: "none", border: "none",
              borderBottom: sub === id ? `2px solid ${T.fg}` : "2px solid transparent",
              marginBottom: -1,
              fontSize: 10, fontWeight: sub === id ? 800 : 500,
              letterSpacing: "0.08em", textTransform: "uppercase",
              color: sub === id ? T.fg : T.muted,
              cursor: "pointer", transition: "color .15s",
              display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
            <span>{l}</span>
            {sub === id && (
              <span style={{ fontSize: 8, fontWeight: 400, color: T.muted, letterSpacing: "0.04em",
                textTransform: "none" }}>{desc}</span>
            )}
          </button>
        ))}
      </div>

      {sub === "equity" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ border: `1px solid ${T.line}`, height: 260, padding: "8px 4px 4px" }}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={equity} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
                <defs>
                  <linearGradient id="gFull" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={T.blue} stopOpacity={0.18} />
                    <stop offset="100%" stopColor={T.blue} stopOpacity={0} />
                  </linearGradient>
                  <filter id="glowLine" x="-5%" y="-50%" width="110%" height="200%">
                    <feGaussianBlur stdDeviation="2.5" result="blur" />
                    <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
                  </filter>
                </defs>
                <CartesianGrid stroke={T.line} />
                <XAxis dataKey="label" tick={{ fontSize: 8, fill: T.muted }} tickLine={false} axisLine={false} interval={9} />
                <YAxis tick={{ fontSize: 8, fill: T.muted }} tickLine={false} axisLine={false} />
                <Tooltip content={<Tip />} />
                {bots.map(b => (
                  <Line key={b.id} type="monotone" dataKey={b.id} stroke={b.color}
                        strokeWidth={1} dot={false} name={b.name || b.id} strokeOpacity={0.7} isAnimationActive={false} />
                ))}
                <Area type="monotone" dataKey="portfolio" stroke={T.fg} strokeWidth={2}
                      fill="url(#gFull)" dot={false} name="Total" isAnimationActive={false} filter="url(#glowLine)" />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1.2fr .8fr", gap: 14 }}>
            <div style={{ border: `1px solid ${T.line}`, padding: 14 }}>
              <Label style={{ marginBottom: 12 }}>Bot strength</Label>
              <MetricBars rows={bots.map(b => ({
                label: b.id.replace("bot", "b"),
                value: Math.max(0, Math.min(100, Math.round(b.winRate || 0))),
                color: b.color,
              }))} />
            </div>
            <div style={{ border: `1px solid ${T.line}`, padding: 14 }}>
              <Label style={{ marginBottom: 12 }}>Goal progress</Label>
              {bots.map(b => (
                <div key={b.id} style={{ marginBottom: 10 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span style={{ fontSize: 9, color: b.color, fontWeight: 700 }}>{b.id}</span>
                    <span style={{ fontSize: 9, color: T.muted }}>{(b.progress_pct || 0).toFixed(0)}%</span>
                  </div>
                  <div style={{ height: 4, background: T.line, borderRadius: 999 }}>
                    <div style={{ width: `${Math.min(100, b.progress_pct || 0)}%`, height: "100%",
                      background: b.color, borderRadius: 999, boxShadow: `0 0 6px ${b.color}55` }} />
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "repeat(2,minmax(0,1fr))", gap: SP.md }}>
            {insightCards.map(card => (
              <div key={card.title} style={{ borderLeft: `2px solid ${card.tone}`, paddingLeft: SP.md }}>
                <div style={{ fontSize: 8, fontWeight: 800, letterSpacing: "0.16em",
                  color: card.tone, textTransform: "uppercase", marginBottom: SP.xs }}>
                  {card.title}
                </div>
                <div style={{ fontSize: 13, fontWeight: 800, lineHeight: 1.3, marginBottom: SP.xs }}>
                  {card.body}
                </div>
                <div style={{ fontSize: 10, color: T.muted, lineHeight: 1.7, marginBottom: SP.xs }}>
                  {card.reason}
                </div>
                <div style={{ fontSize: 10, color: T.fg, lineHeight: 1.7, opacity: 0.8 }}>
                  → {card.change}
                </div>
              </div>
            ))}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : isTablet ? "repeat(3,1fr)" : "repeat(6,1fr)", gap: 8 }}>
            {bots.map(b => {
              const idx = b.id.match(/\d/)?.[0];
              const d   = equity.map(e => ({ v: e[`bot${idx}`] || b.start_amount || 100 }));
              return (
                <div key={b.id} style={{ borderTop: `1px solid ${b.color}44`, paddingTop: 10 }}>
                  <div style={{ fontSize: 9, fontWeight: 700, color: b.color, marginBottom: 4 }}>{b.id}</div>
                  <div style={{ fontSize: 15, fontWeight: 800 }}>${b.bankroll.toFixed(0)}</div>
                  <div style={{ fontSize: 9, color: (b.roi_pct || 0) >= 0 ? T.green : T.red, marginBottom: 6 }}>
                    {(b.roi_pct || 0) >= 0 ? "+" : ""}{(b.roi_pct || 0).toFixed(1)}%
                  </div>
                  <MiniSpark data={d} color={b.color} height={36} />
                </div>
              );
            })}
          </div>
        </div>
      )}

      {sub === "history" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select value={filters.bot_id} onChange={e => setFilters(p => ({ ...p, bot_id: e.target.value, offset: 0 }))}
              style={{ padding: "5px 10px", borderRadius: 6, fontSize: 10 }}>
              <option value="">All Bots</option>
              {bots.map(b => <option key={b.id} value={b.id}>{b.id}</option>)}
            </select>
            <select value={filters.won} onChange={e => setFilters(p => ({ ...p, won: e.target.value, offset: 0 }))}
              style={{ padding: "5px 10px", borderRadius: 6, fontSize: 10 }}>
              <option value="">All</option>
              <option value="1">Wins</option>
              <option value="0">Losses</option>
            </select>
            <span style={{ fontSize: 10, color: T.muted }}>{total} trades</span>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${T.line}` }}>
                  {["Time","Bot","Bet","Profit","Won","Phase"].map(h => (
                    <th key={h} style={{ ...lbl, padding: "8px 10px", textAlign: "left" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={6} style={{ padding: 20, textAlign: "center", color: T.muted }}><Spinner /></td></tr>
                ) : trades.map((r, i) => (
                  <tr key={i} style={{ borderBottom: `1px solid ${T.line}44` }}>
                    <td style={{ padding: "7px 10px", fontSize: 9, color: T.muted }}>{r.ts?.slice(11,19)}</td>
                    <td style={{ padding: "7px 10px", fontSize: 10, color: BC[r.bot_id] || T.fg }}>{r.bot_id}</td>
                    <td style={{ padding: "7px 10px", fontSize: 10 }}>${r.bet?.toFixed(4)}</td>
                    <td style={{ padding: "7px 10px", fontSize: 10, color: r.profit >= 0 ? T.green : T.red }}>
                      {r.profit >= 0 ? "+" : ""}${r.profit?.toFixed(4)}
                    </td>
                    <td style={{ padding: "7px 10px", fontSize: 10, color: r.won ? T.green : T.red }}>{r.won ? "W" : "L"}</td>
                    <td style={{ padding: "7px 10px", fontSize: 9, color: T.muted }}>{r.phase}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <Chip onClick={() => setFilters(p => ({ ...p, offset: Math.max(0, p.offset - p.limit) }))}>PREV</Chip>
            <Chip onClick={() => setFilters(p => ({ ...p, offset: p.offset + p.limit }))}>NEXT</Chip>
          </div>
        </div>
      )}

      {sub === "projections" && (
        <div>
          {!proj ? (
            <div style={{ padding: 40, textAlign: "center", color: T.muted }}><Spinner /></div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "repeat(2,minmax(0,1fr))", gap: 16 }}>
              {Object.entries(proj).map(([botId, row]) => (
                <div key={botId} style={{ borderTop: `1px solid ${(bots.find(b => b.id === botId)?.color) || T.line}`, paddingTop: 12 }}>
                  <Label style={{ marginBottom: 8 }}>{botId}</Label>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(2,minmax(0,1fr))", gap: 8, marginBottom: 12 }}>
                    {[
                      ["Current", `$${(row.current || 0).toFixed(2)}`],
                      ["Median", `$${(row.p50_final || 0).toFixed(2)}`],
                      ["Range Low", `$${(row.p10_final || 0).toFixed(2)}`],
                      ["Range High", `$${(row.p90_final || 0).toFixed(2)}`],
                      ["10x Read", row.time_to_10x || "--"],
                      ["Win Rate", `${(row.win_rate || 0).toFixed(1)}%`],
                    ].map(([k, v]) => (
                      <div key={k}>
                        <Label style={{ marginBottom: 4 }}>{k}</Label>
                        <div style={{ fontSize: 12, fontWeight: 800 }}>{v}</div>
                      </div>
                    ))}
                  </div>
                  <div style={{ fontSize: 10, color: T.muted, lineHeight: 1.7, marginBottom: 8 }}>
                    {(row.p50_final || 0) >= (row.current || 0)
                      ? "Median path leans upward. Current settings have enough resilience to justify refinement instead of a full reset."
                      : "Median path leans weak. This bot likely needs smaller sizing, stricter floors, or a more conservative mode before scaling."}
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 6 }}>
                    {["p10", "p50", "p90"].map(key => (
                      <MiniSpark
                        key={key}
                        data={(row.paths?.[key] || []).map((v, i) => ({ v, i }))}
                        color={key === "p50" ? ((bots.find(b => b.id === botId)?.color) || T.blue) : T.muted}
                        height={26}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {sub === "workshop" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1.05fr .95fr", gap: 16 }}>
            <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: 12 }}>
              <Label style={{ marginBottom: 10 }}>Strategy Workshop</Label>
              <div style={{ fontSize: 10, color: T.muted, lineHeight: 1.7, marginBottom: 14 }}>
                Clone a live bot into a workshop variant, tweak its mode, scale, and bankroll thresholds, then push that version back onto the real bot when you like the setup.
              </div>
              <div style={{ marginBottom: 14 }}>
                <Label style={{ marginBottom: 8 }}>Preset Templates</Label>
                <div style={{ display: "grid", gap: 8 }}>
                  {WORKSHOP_PRESETS.map(preset => (
                    <div key={preset.id} style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1.1fr auto", gap: 8, alignItems: "start", borderTop: `1px solid ${T.line}66`, paddingTop: 8 }}>
                      <div>
                        <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 4 }}>{preset.label}</div>
                        <div style={{ fontSize: 10, color: T.muted, lineHeight: 1.6 }}>{preset.description}</div>
                      </div>
                      <Chip onClick={() => loadPreset(preset)}>LOAD PRESET</Chip>
                    </div>
                  ))}
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "repeat(2,minmax(0,1fr))", gap: 10 }}>
                <div>
                  <Label style={{ marginBottom: 6 }}>Source Bot</Label>
                  <select value={variantDraft.sourceBot} onChange={e => loadFromBot(e.target.value)} style={{ width: "100%", padding: "7px 10px", borderRadius: 8, fontSize: 11 }}>
                    {bots.map(b => <option key={b.id} value={b.id}>{b.id}</option>)}
                  </select>
                </div>
                <div>
                  <Label style={{ marginBottom: 6 }}>Variant Name</Label>
                  <input value={variantDraft.label} onChange={e => setVariantDraft(p => ({ ...p, label: e.target.value }))} style={{ width: "100%", padding: "7px 10px", borderRadius: 8, fontSize: 11 }} />
                </div>
                <div>
                  <Label style={{ marginBottom: 6 }}>Mode</Label>
                  <select value={variantDraft.mode} onChange={e => setVariantDraft(p => ({ ...p, mode: e.target.value }))} style={{ width: "100%", padding: "7px 10px", borderRadius: 8, fontSize: 11 }}>
                    {STRATEGY_DEFS.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
                  </select>
                </div>
                <div>
                  <Label style={{ marginBottom: 6 }}>Bet Scale</Label>
                  <input value={variantDraft.scale} onChange={e => setVariantDraft(p => ({ ...p, scale: e.target.value }))} style={{ width: "100%", padding: "7px 10px", borderRadius: 8, fontSize: 11 }} />
                </div>
                <div>
                  <Label style={{ marginBottom: 6 }}>Start Amount</Label>
                  <input value={variantDraft.start_amount} onChange={e => setVariantDraft(p => ({ ...p, start_amount: e.target.value }))} style={{ width: "100%", padding: "7px 10px", borderRadius: 8, fontSize: 11 }} />
                </div>
                <div>
                  <Label style={{ marginBottom: 6 }}>Goal</Label>
                  <input value={variantDraft.target_amount} onChange={e => setVariantDraft(p => ({ ...p, target_amount: e.target.value }))} style={{ width: "100%", padding: "7px 10px", borderRadius: 8, fontSize: 11 }} />
                </div>
                <div>
                  <Label style={{ marginBottom: 6 }}>Floor</Label>
                  <input value={variantDraft.floor_amount} onChange={e => setVariantDraft(p => ({ ...p, floor_amount: e.target.value }))} style={{ width: "100%", padding: "7px 10px", borderRadius: 8, fontSize: 11 }} />
                </div>
                <div style={{ gridColumn: isMobile ? "auto" : "1 / -1" }}>
                  <Label style={{ marginBottom: 6 }}>Tags</Label>
                  <input
                    value={variantDraft.tagsText}
                    onChange={e => setVariantDraft(p => ({ ...p, tagsText: e.target.value }))}
                    placeholder="benchmark, recovery, paper"
                    style={{ width: "100%", padding: "7px 10px", borderRadius: 8, fontSize: 11 }}
                  />
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
                    {WORKSHOP_TAG_SUGGESTIONS.map(tag => (
                      <Chip
                        key={tag}
                        active={parseVariantTags(variantDraft.tagsText).includes(tag)}
                        onClick={() => {
                          const tags = parseVariantTags(variantDraft.tagsText);
                          const next = tags.includes(tag) ? tags.filter(t => t !== tag) : [...tags, tag];
                          setVariantDraft(p => ({ ...p, tagsText: next.join(", ") }));
                        }}
                      >
                        {tag}
                      </Chip>
                    ))}
                  </div>
                </div>
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
                <ActionBtn onClick={createVariant}>CREATE VARIANT</ActionBtn>
                <Chip onClick={() => loadFromBot(variantDraft.sourceBot)}>RESET FROM LIVE BOT</Chip>
              </div>
            </div>

            <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: 12 }}>
              <Label style={{ marginBottom: 10 }}>Live Recommendations</Label>
              <div style={{ display: "grid", gap: 10 }}>
                {strategyRows.map(row => (
                  <div key={row.id} style={{ borderTop: `1px solid ${T.line}`, paddingTop: 10 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                      <span style={{ fontSize: 11, fontWeight: 700, color: (bots.find(b => b.id === row.id)?.color) || T.fg }}>{row.id}</span>
                      <span style={{ fontSize: 9, color: row.stress === "high" ? T.red : row.stress === "medium" ? T.yellow : T.green }}>{row.stress.toUpperCase()}</span>
                    </div>
                    <div style={{ fontSize: 10, color: T.muted, lineHeight: 1.7 }}>
                      {row.autoDownscales > 0
                        ? `Already auto-managed ${row.autoDownscales} time(s). Keep this in recovery until the rolling window improves.`
                        : row.roi < -10 || row.rolling25 < -8
                        ? "Cut size first. The recent window is weak enough that this bot belongs in a recovery or quarantine bucket."
                        : row.winRate > 70 && row.rolling25 > 0
                        ? "Preserve the shape. This is the cleanest candidate for a benchmark-tagged variant."
                        : "Improve trigger quality before adding more size. The recent read is not clean enough yet."}
                    </div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
                      {row.tags.map(tag => <Chip key={tag}>{tag}</Chip>)}
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: 8, marginTop: 8 }}>
                      {[
                        ["25B ROI", `${row.rolling25 >= 0 ? "+" : ""}${row.rolling25.toFixed(1)}%`],
                        ["100B ROI", `${row.rolling100 >= 0 ? "+" : ""}${row.rolling100.toFixed(1)}%`],
                        ["Downscales", `${row.autoDownscales}`],
                      ].map(([k, v]) => (
                        <div key={k}>
                          <Label style={{ marginBottom: 4 }}>{k}</Label>
                          <div style={{ fontSize: 11, fontWeight: 800 }}>{v}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div style={{ display: "grid", gap: 12 }}>
            {activeVariants.length === 0 ? (
              <div style={{ fontSize: 10, color: T.muted }}>No workshop variants yet.</div>
            ) : activeVariants.map(variant => (
              <div key={variant.id} style={{ borderTop: `1px solid ${T.line}`, paddingTop: 12, display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1.15fr .85fr", gap: 12 }}>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 800, marginBottom: 6 }}>{variant.label}</div>
                  <div style={{ fontSize: 10, color: T.muted, lineHeight: 1.7 }}>
                    Source: {variant.sourceBot} · Mode: {variant.mode} · Scale: {variant.scale}x · Start ${variant.start_amount} · Goal ${variant.target_amount} · Floor ${variant.floor_amount}
                  </div>
                  {!!variant.tags?.length && (
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
                      {variant.tags.map(tag => <Chip key={tag}>{tag}</Chip>)}
                    </div>
                  )}
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: isMobile ? "flex-start" : "flex-end" }}>
                  <Chip onClick={() => setVariantDraft({ ...variant, tagsText: (variant.tags || []).join(", ") })}>EDIT</Chip>
                  <ActionBtn onClick={() => applyVariant(variant)}>APPLY TO LIVE BOT</ActionBtn>
                  <Chip onClick={() => archiveVariant(variant.id)}>ARCHIVE</Chip>
                  <Chip onClick={() => setVariants(prev => prev.filter(v => v.id !== variant.id))}>DELETE</Chip>
                </div>
              </div>
            ))}
          </div>

          <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: 14 }}>
            <Label style={{ marginBottom: 10 }}>Archived Variants</Label>
            {archivedVariants.length === 0 ? (
              <div style={{ fontSize: 10, color: T.muted }}>No archived variants.</div>
            ) : (
              <div style={{ display: "grid", gap: 12 }}>
                {archivedVariants.map(variant => (
                  <div key={variant.id} style={{ borderTop: `1px solid ${T.line}66`, paddingTop: 12, display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1.15fr .85fr", gap: 12, opacity: 0.82 }}>
                    <div>
                      <div style={{ fontSize: 12, fontWeight: 800, marginBottom: 6 }}>{variant.label}</div>
                      <div style={{ fontSize: 10, color: T.muted, lineHeight: 1.7 }}>
                        Source: {variant.sourceBot} · Mode: {variant.mode} · Scale: {variant.scale}x · Start ${variant.start_amount} · Goal ${variant.target_amount} · Floor ${variant.floor_amount}
                      </div>
                      {!!variant.tags?.length && (
                        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
                          {variant.tags.map(tag => <Chip key={tag}>{tag}</Chip>)}
                        </div>
                      )}
                    </div>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: isMobile ? "flex-start" : "flex-end" }}>
                      <Chip onClick={() => setVariantDraft({ ...variant, archived: false, tagsText: (variant.tags || []).join(", ") })}>LOAD INTO EDITOR</Chip>
                      <Chip onClick={() => restoreVariant(variant.id)}>RESTORE</Chip>
                      <Chip onClick={() => setVariants(prev => prev.filter(v => v.id !== variant.id))}>DELETE</Chip>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  WALLET TAB
// ─────────────────────────────────────────────────────────────────────────────
function WalletTab({ token, bots }) {
  const { isMobile } = useViewport();

  const [ethAccount, setEthAccount] = useState("");
  const [ethBalance, setEthBalance] = useState(null);
  const [mmBusy,     setMmBusy]     = useState(false);
  const [mmMsg,      setMmMsg]      = useState("");
  const [sendTo,     setSendTo]     = useState("");
  const [sendAmt,    setSendAmt]    = useState("");
  const [sendBusy,   setSendBusy]   = useState(false);

  const [vaultData,  setVaultData]  = useState(null);
  const [lockAmt,    setLockAmt]    = useState("");
  const [lockBot,    setLockBot]    = useState(bots[0]?.id || "");
  const [lockMsg,    setLockMsg]    = useState("");

  const [wdAmount,   setWdAmount]   = useState("");
  const [wdBotId,    setWdBotId]    = useState(bots[0]?.id || "");
  const [wdMsg,      setWdMsg]      = useState("");

  const [xferMethod, setXferMethod] = useState("interac");
  const [xferTo,     setXferTo]     = useState("");
  const [xferAmt,    setXferAmt]    = useState("");
  const [xferNote,   setXferNote]   = useState("");
  const [xferMsg,    setXferMsg]    = useState("");

  const [txs, setTxs] = useState([]);

  const api = (path, opts = {}) =>
    fetch(path, { ...opts, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` } });

  useEffect(() => {
    api(`${API}/wallet/transactions`).then(r => r.json()).then(d => setTxs(d.transactions || [])).catch(() => {});
    api(`${API}/vault`).then(r => r.json()).then(setVaultData).catch(() => {});
  }, []);

  const connectMM = async () => {
    if (!window.ethereum) { setMmMsg("MetaMask not found. Install the extension."); return; }
    setMmBusy(true); setMmMsg("");
    try {
      const accounts = await window.ethereum.request({ method: "eth_requestAccounts" });
      const acc = accounts[0];
      setEthAccount(acc);
      const hexBal = await window.ethereum.request({ method: "eth_getBalance", params: [acc, "latest"] });
      setEthBalance((parseInt(hexBal, 16) / 1e18).toFixed(6));
      setMmMsg("Connected.");
    } catch (e) { setMmMsg(e.message || "Connection refused."); }
    setMmBusy(false);
  };

  const sendEth = async () => {
    if (!ethAccount || !sendTo || !sendAmt) { setMmMsg("Fill in address and amount."); return; }
    setSendBusy(true); setMmMsg("");
    try {
      const wei = (parseFloat(sendAmt) * 1e18).toString(16);
      const txHash = await window.ethereum.request({
        method: "eth_sendTransaction",
        params: [{ from: ethAccount, to: sendTo, value: "0x" + wei }],
      });
      setMmMsg(`TX: ${txHash.slice(0, 18)}...`);
      setSendTo(""); setSendAmt("");
    } catch (e) { setMmMsg(e.message || "Transaction failed."); }
    setSendBusy(false);
  };

  const lockToVault = async () => {
    const amt = parseFloat(lockAmt);
    if (!amt || !lockBot) { setLockMsg("Enter amount."); return; }
    const r = await api(`${API}/vault/lock`, { method: "POST", body: JSON.stringify({ bot_id: lockBot, amount: amt }) });
    const d = await r.json();
    setLockMsg(d.ok ? `$${(d.locked || amt).toFixed(2)} moved to vault.` : d.error || "Error");
    if (d.ok) { setLockAmt(""); api(`${API}/vault`).then(r2 => r2.json()).then(setVaultData).catch(() => {}); }
    setTimeout(() => setLockMsg(""), 3000);
  };

  const withdraw = async () => {
    const amt = parseFloat(wdAmount);
    if (!amt || !wdBotId) { setWdMsg("Enter amount and select a bot."); return; }
    const r = await api(`${API}/wallet/withdraw`, { method: "POST", body: JSON.stringify({ bot_id: wdBotId, amount: amt }) });
    const d = await r.json();
    setWdMsg(d.ok ? `Withdrawal of $${amt} requested.` : `Error: ${d.error}`);
    setTimeout(() => setWdMsg(""), 3000);
  };

  const sendTransfer = () => {
    if (!xferTo || !xferAmt) { setXferMsg("Fill in recipient and amount."); return; }
    setXferMsg("Transfer requires API keys in Settings. Placeholder only.");
    setTimeout(() => setXferMsg(""), 4000);
  };

  const totalWithdrawn = bots.reduce((s, b) => s + (b.total_withdrawn || 0), 0);
  const totalVault     = vaultData?.total_vault ?? bots.reduce((s, b) => s + (b.vault || 0), 0);
  const totalBankroll  = bots.reduce((s, b) => s + b.bankroll, 0);
  const starts         = bots.reduce((s, b) => s + (b.start_amount || 100), 0);
  const totalProgress  = totalBankroll + totalWithdrawn + totalVault;
  const pnl            = totalProgress - starts;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: SP.xl, animation: "fadein .25s ease",
      maxWidth: 860 }}>

      {/* ── SECTION A: Balance Summary ── */}
      <div>
        <SectionHeader>Balances</SectionHeader>
        <div style={{ display: "grid",
          gridTemplateColumns: isMobile ? "1fr 1fr" : "repeat(4,minmax(0,1fr))", gap: SP.md }}>
          {[
            { k: "Active Bankroll", v: `$${totalBankroll.toFixed(2)}`,  c: T.fg    },
            { k: "P&L",             v: `${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}`, c: pnl >= 0 ? T.green : T.red },
            { k: "Vault",           v: `$${totalVault.toFixed(2)}`,     c: T.yellow },
            { k: "Total Out",       v: `$${totalWithdrawn.toFixed(2)}`, c: T.blue  },
          ].map(m => (
            <div key={m.k} style={{ borderTop: `1px solid ${T.line}`, paddingTop: SP.sm }}>
              <Label style={{ marginBottom: SP.xs }}>{m.k}</Label>
              <div style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.04em", color: m.c }}>
                {m.v}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── SECTION B: Internal Money Movement (safe actions) ── */}
      <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: SP.lg }}>
        <SectionHeader>Internal — Move Money Safely</SectionHeader>
        <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr", gap: SP.lg }}>

          {/* Vault Lock */}
          <div>
            <Label style={{ marginBottom: SP.xs }}>Lock to Vault</Label>
            <div style={{ fontSize: 10, color: T.muted, marginBottom: SP.sm, lineHeight: 1.6 }}>
              Vault funds are never re-risked. Move profits here to protect them permanently.
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: SP.xs }}>
              <select value={lockBot} onChange={e => setLockBot(e.target.value)}
                style={{ padding: "7px 10px", borderRadius: 6, fontSize: 10 }}>
                {bots.map(b => (
                  <option key={b.id} value={b.id}>
                    {b.id} — ${(b.total_withdrawn || 0).toFixed(2)} available
                  </option>
                ))}
              </select>
              <div style={{ display: "flex", gap: SP.xs, alignItems: "center" }}>
                <input value={lockAmt} onChange={e => setLockAmt(e.target.value)}
                  placeholder="Amount"
                  style={{ flex: 1, padding: "7px 10px", borderRadius: 6, fontSize: 10 }} />
                <ActionBtn onClick={lockToVault} color={T.yellow}>LOCK</ActionBtn>
              </div>
              {lockMsg && (
                <InlineNotice type={lockMsg.includes("Error") ? "error" : "ok"}>
                  {lockMsg}
                </InlineNotice>
              )}
            </div>
            {/* Per-bot vault breakdown */}
            {bots.some(b => (b.vault || 0) > 0) && (
              <div style={{ marginTop: SP.sm, display: "flex", flexDirection: "column" }}>
                {bots.filter(b => (b.vault || 0) > 0).map(b => (
                  <div key={b.id} className="dg-row-hover" style={{
                    display: "flex", justifyContent: "space-between", padding: "6px 0",
                    borderBottom: `1px solid ${T.line}33`, fontSize: 10 }}>
                    <span style={{ color: b.color, fontWeight: 700 }}>{b.id}</span>
                    <span style={{ color: T.yellow, fontWeight: 800 }}>
                      ${(b.vault || 0).toFixed(2)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Withdraw Profits */}
          <div>
            <Label style={{ marginBottom: SP.xs }}>Withdraw Profits</Label>
            <div style={{ fontSize: 10, color: T.muted, marginBottom: SP.sm, lineHeight: 1.6 }}>
              Request a withdrawal from a bot's bankroll back to your account.
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: SP.xs }}>
              <select value={wdBotId} onChange={e => setWdBotId(e.target.value)}
                style={{ padding: "7px 10px", borderRadius: 6, fontSize: 10 }}>
                {bots.map(b => (
                  <option key={b.id} value={b.id}>
                    {b.id} — ${(b.total_withdrawn || 0).toFixed(2)} out
                  </option>
                ))}
              </select>
              <div style={{ display: "flex", gap: SP.xs, alignItems: "center" }}>
                <input value={wdAmount} onChange={e => setWdAmount(e.target.value)}
                  placeholder="Amount"
                  style={{ flex: 1, padding: "7px 10px", borderRadius: 6, fontSize: 10 }} />
                <ActionBtn onClick={withdraw} color={T.blue}>WITHDRAW</ActionBtn>
              </div>
              {wdMsg && (
                <InlineNotice type={wdMsg.startsWith("Error") ? "error" : "ok"}>
                  {wdMsg}
                </InlineNotice>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── SECTION C: External Wallet (MetaMask) — elevated risk visual ── */}
      <div style={{ borderTop: `1px solid rgba(255,143,90,0.3)`, paddingTop: SP.lg }}>
        <SectionHeader style={{ borderColor: "rgba(255,143,90,0.18)" }}>
          External — MetaMask / Ethereum
        </SectionHeader>
        <div style={{ fontSize: 10, color: T.muted, marginBottom: SP.sm, lineHeight: 1.6 }}>
          Sends real ETH transactions on-chain. Double-check recipient address before sending.
        </div>
        {!ethAccount ? (
          <div style={{ display: "flex", gap: SP.sm, alignItems: "center", flexWrap: "wrap" }}>
            <ActionBtn onClick={connectMM} disabled={mmBusy} color={T.orange}>
              {mmBusy ? "CONNECTING…" : "CONNECT METAMASK"}
            </ActionBtn>
            {mmMsg && <span style={{ fontSize: 10, color: T.red }}>{mmMsg}</span>}
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: SP.md }}>
            <div style={{ display: "flex", gap: SP.xl, flexWrap: "wrap" }}>
              <div>
                <Label style={{ marginBottom: SP.xs }}>Connected Address</Label>
                <div style={{ fontSize: 11, color: T.orange, fontWeight: 700, fontFamily: "ui-monospace, monospace" }}>
                  {ethAccount.slice(0,10)}…{ethAccount.slice(-8)}
                </div>
              </div>
              <div>
                <Label style={{ marginBottom: SP.xs }}>ETH Balance</Label>
                <div style={{ fontSize: 18, fontWeight: 800, color: T.orange, letterSpacing: "-0.02em" }}>
                  {ethBalance ?? "—"} ETH
                </div>
              </div>
            </div>
            <div>
              <Label style={{ marginBottom: SP.xs }}>Send ETH</Label>
              <div style={{ display: "flex", gap: SP.xs, flexWrap: "wrap", alignItems: "center",
                maxWidth: 560 }}>
                <input value={sendTo} onChange={e => setSendTo(e.target.value)}
                  placeholder="0x… recipient address"
                  style={{ flex: 2, minWidth: 180, padding: "7px 10px", borderRadius: 6, fontSize: 10 }} />
                <input value={sendAmt} onChange={e => setSendAmt(e.target.value)}
                  placeholder="ETH amount"
                  style={{ flex: 1, minWidth: 80, padding: "7px 10px", borderRadius: 6, fontSize: 10 }} />
                <ActionBtn onClick={sendEth} disabled={sendBusy} color={T.orange}>
                  {sendBusy ? "SENDING…" : "SEND"}
                </ActionBtn>
              </div>
            </div>
            {mmMsg && (
              <InlineNotice type={mmMsg.toLowerCase().includes("failed") || mmMsg.toLowerCase().includes("fill") ? "error" : "ok"}>
                {mmMsg}
              </InlineNotice>
            )}
          </div>
        )}
      </div>

      {/* ── SECTION D: External Transfer — placeholder section ── */}
      <div style={{ borderTop: `1px solid rgba(255,143,90,0.2)`, paddingTop: SP.lg }}>
        <SectionHeader style={{ borderColor: "rgba(255,143,90,0.15)" }}>
          External — Interac / PayPal / Wise
        </SectionHeader>
        <InlineNotice type="warn" style={{ marginBottom: SP.md }}>
          Requires platform API keys configured in Settings. Currently a placeholder — no real transfer will be sent.
        </InlineNotice>
        <div style={{ display: "flex", gap: SP.xs, marginBottom: SP.sm, flexWrap: "wrap" }}>
          {[["interac","Interac"], ["paypal","PayPal"], ["wise","Wise"]].map(([id, l]) => (
            <Chip key={id} active={xferMethod === id} onClick={() => setXferMethod(id)}>{l}</Chip>
          ))}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: SP.xs, maxWidth: 400 }}>
          <input value={xferTo} onChange={e => setXferTo(e.target.value)}
            placeholder={xferMethod === "interac" ? "Email or phone" : xferMethod === "wise" ? "Email / IBAN / SWIFT" : "PayPal email"}
            style={{ padding: "7px 10px", borderRadius: 6, fontSize: 10 }} />
          <input value={xferAmt} onChange={e => setXferAmt(e.target.value)}
            placeholder="Amount (USD)"
            style={{ padding: "7px 10px", borderRadius: 6, fontSize: 10 }} />
          <input value={xferNote} onChange={e => setXferNote(e.target.value)}
            placeholder="Note (optional)"
            style={{ padding: "7px 10px", borderRadius: 6, fontSize: 10 }} />
          <div style={{ display: "flex", gap: SP.xs, alignItems: "center" }}>
            <ActionBtn onClick={sendTransfer} color={T.orange}>
              SEND {xferMethod.toUpperCase()}
            </ActionBtn>
            {xferMsg && <span style={{ fontSize: 10, color: T.muted }}>{xferMsg}</span>}
          </div>
        </div>
      </div>

      {/* ── SECTION E: Transaction History ── */}
      {txs.length > 0 ? (
        <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: SP.md }}>
          <SectionHeader right={`${txs.length} entries`}>Transaction Log</SectionHeader>
          <div style={{ display: "flex", flexDirection: "column" }}>
            {txs.map((tx, i) => (
              <div key={i} className="dg-row-hover" style={{
                display: "grid",
                gridTemplateColumns: "120px 100px 1fr 80px",
                gap: SP.sm, alignItems: "center",
                padding: "7px 0",
                borderBottom: `1px solid ${T.line}28`, fontSize: 10 }}>
                <span style={{ color: T.muted, fontSize: 9, fontFamily: "ui-monospace, monospace" }}>
                  {tx.ts?.slice(0,16).replace("T"," ")}
                </span>
                <span style={{ color: BC[tx.bot_id] || T.muted, fontWeight: 600 }}>{tx.bot_id}</span>
                <span style={{ color: tx.type === "withdraw" ? T.yellow : T.green, fontWeight: 700 }}>
                  {tx.type === "withdraw" ? "−" : "+"}${tx.amount?.toFixed(2)}
                </span>
                <span style={{ fontSize: 8, color: T.muted, letterSpacing: "0.08em",
                  textAlign: "right" }}>{tx.type?.toUpperCase()}</span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: SP.md }}>
          <SectionHeader>Transaction Log</SectionHeader>
          <EmptyState message="No transactions recorded yet." />
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  SETTINGS TAB
// ─────────────────────────────────────────────────────────────────────────────
function SettingsTab({ token, tz, onTzChange }) {
  const { isMobile } = useViewport();
  const [fields, setFields] = useState({
    stake_api_token: "",
    kraken_api_key: "", kraken_secret: "",
    betfair_app_key: "", betfair_username: "", betfair_password: "",
    binance_api_key: "", binance_secret: "",
    kalshi_api_key: "", kalshi_private_key: "",
    telegram_bot_token: "", telegram_chat_id: "",
    proxy_host: "", proxy_port: "",
    dashboard_password: "",
  });
  const [polyWallet,    setPolyWallet]    = useState(null);
  const [polyWalletBusy,setPolyWalletBusy]= useState(false);
  const [vpn,          setVpn]          = useState({ status:"off", ip:null, country:null, installed:false });
  const [tunnel,       setTunnel]       = useState({ status:"off", url:null, installed:false });
  const [devices,      setDevices]      = useState([]);
  const [renameMap,    setRenameMap]    = useState({});
  const [show,         setShow]         = useState({});
  const [saving,       setSaving]       = useState({});
  const [bots,         setBots]         = useState([]);
  const [newBot,       setNewBot]       = useState({ id:"", platform:"stake", strategy:"dice", initial:"20", enabled:true });
  const [botMsg,       setBotMsg]       = useState("");
  const [soloBot,      setSoloBot]      = useState(null);
  const [adminVault,   setAdminVault]   = useState({ owner_name:"", owner_email:"", owner_phone:"", wallet_address:"", payout_notes:"", security_notes:"" });
  const [adminPass,    setAdminPass]    = useState("");
  const [adminNewPass, setAdminNewPass] = useState("");
  const [adminUnlocked,setAdminUnlocked]= useState(false);
  const [adminMsg,     setAdminMsg]     = useState("");
  const [lastActiveAt, setLastActiveAt] = useState(Date.now());

  const api = (path, opts = {}) =>
    fetch(path, { ...opts, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` } });

  useEffect(() => {
    api(`${API}/settings`).then(r => r.json()).then(d => {
      if (d.settings) setFields(f => ({ ...f, ...d.settings }));
    }).catch(() => {});
    api(`${API}/bots/registry`).then(r => r.json()).then(d => setBots(d.bots || [])).catch(() => {});
    api(`${API}/wallet/poly`).then(r => r.json()).then(d => setPolyWallet(d)).catch(() => {});
    api(`${API}/vpn/status`).then(r => r.json()).then(d => setVpn(d)).catch(() => {});
    api(`${API}/tunnel/status`).then(r => r.json()).then(d => setTunnel(d)).catch(() => {});
    api(`${API}/devices`).then(r => r.json()).then(d => setDevices(d.devices || [])).catch(() => {});
    // Poll VPN + tunnel status every 5s while settings is open
    const iv = setInterval(() => {
      api(`${API}/vpn/status`).then(r => r.json()).then(d => setVpn(d)).catch(() => {});
      api(`${API}/tunnel/status`).then(r => r.json()).then(d => setTunnel(d)).catch(() => {});
    }, 5000);
    return () => clearInterval(iv);
  }, []);

  const toBase64 = bytes => { let s = ""; bytes.forEach(b => { s += String.fromCharCode(b); }); return window.btoa(s); };
  const fromBase64 = value => Uint8Array.from(window.atob(value), c => c.charCodeAt(0));

  const deriveKey = async (passphrase, salt) => {
    const material = await window.crypto.subtle.importKey("raw", new TextEncoder().encode(passphrase), "PBKDF2", false, ["deriveKey"]);
    return window.crypto.subtle.deriveKey(
      { name: "PBKDF2", salt, iterations: 250000, hash: "SHA-256" },
      material, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  };

  const encryptAdminVault = async (data, passphrase) => {
    const salt = window.crypto.getRandomValues(new Uint8Array(16));
    const iv   = window.crypto.getRandomValues(new Uint8Array(12));
    const key  = await deriveKey(passphrase, salt);
    const cipher = await window.crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(JSON.stringify(data)));
    return { salt: toBase64(salt), iv: toBase64(iv), cipher: toBase64(new Uint8Array(cipher)) };
  };

  const decryptAdminVault = async (payload, passphrase) => {
    const key = await deriveKey(passphrase, fromBase64(payload.salt));
    const plain = await window.crypto.subtle.decrypt({ name: "AES-GCM", iv: fromBase64(payload.iv) }, key, fromBase64(payload.cipher));
    return JSON.parse(new TextDecoder().decode(plain));
  };

  const unlockAdmin = async () => {
    const raw = localStorage.getItem(ADMIN_VAULT_KEY);
    if (!raw || !adminPass) { setAdminMsg("Enter your passphrase to unlock local admin details."); return; }
    try {
      const data = await decryptAdminVault(JSON.parse(raw), adminPass);
      setAdminVault(data); setAdminUnlocked(true); setAdminMsg("Admin vault unlocked locally.");
    } catch { setAdminMsg("Wrong passphrase or unreadable local vault."); }
  };

  const saveAdmin = async () => {
    const passphrase = adminNewPass || adminPass;
    if (!passphrase) { setAdminMsg("Set a passphrase before saving admin details."); return; }
    try {
      const encrypted = await encryptAdminVault({ ...adminVault, updated_at: new Date().toISOString() }, passphrase);
      localStorage.setItem(ADMIN_VAULT_KEY, JSON.stringify(encrypted));
      setAdminPass(passphrase); setAdminNewPass(""); setAdminUnlocked(true);
      setAdminMsg("Encrypted admin details saved only in this browser.");
    } catch { setAdminMsg("Unable to save encrypted admin details."); }
  };

  const lockAdmin = () => {
    setAdminUnlocked(false); setAdminPass(""); setAdminNewPass("");
    setAdminVault({ owner_name:"", owner_email:"", owner_phone:"", wallet_address:"", payout_notes:"", security_notes:"" });
    setAdminMsg("Admin vault locked.");
  };

  const clearAdmin = () => { localStorage.removeItem(ADMIN_VAULT_KEY); lockAdmin(); setAdminMsg("Local admin vault deleted from this browser."); };

  const exportAdmin = () => {
    const raw = localStorage.getItem(ADMIN_VAULT_KEY);
    if (!raw) { setAdminMsg("No local encrypted vault found to export."); return; }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([raw], { type: "application/json" }));
    a.download = "degens-admin-vault.json"; a.click();
    setAdminMsg("Encrypted vault exported.");
  };

  const importAdmin = async e => {
    const file = e.target.files?.[0]; if (!file) return;
    try {
      const text = await file.text(); const parsed = JSON.parse(text);
      if (!parsed?.salt || !parsed?.iv || !parsed?.cipher) throw new Error("bad");
      localStorage.setItem(ADMIN_VAULT_KEY, text); setAdminUnlocked(false);
      setAdminMsg("Encrypted vault imported. Unlock it with your passphrase.");
    } catch { setAdminMsg("That file is not a valid encrypted admin vault."); }
    e.target.value = "";
  };

  useEffect(() => {
    if (!adminUnlocked) return;
    const onActivity = () => setLastActiveAt(Date.now());
    const t = setInterval(() => {
      if (Date.now() - lastActiveAt > 5 * 60 * 1000) { lockAdmin(); setAdminMsg("Admin vault auto-locked after inactivity."); }
    }, 15000);
    window.addEventListener("pointerdown", onActivity);
    window.addEventListener("keydown", onActivity);
    return () => { clearInterval(t); window.removeEventListener("pointerdown", onActivity); window.removeEventListener("keydown", onActivity); };
  }, [adminUnlocked, lastActiveAt]);

  const save = async key => {
    setSaving(p => ({ ...p, [key]: true }));
    await api(`${API}/settings`, { method: "POST", body: JSON.stringify({ key, value: fields[key] }) });
    setSaving(p => ({ ...p, [key]: false }));
  };

  const addBot = async () => {
    const r = await api(`${API}/bots/registry`, { method: "POST", body: JSON.stringify(newBot) });
    const d = await r.json();
    if (d.ok) { setBotMsg("Bot added. Restart server to activate."); setBots(d.bots || []); }
    else setBotMsg(`Error: ${d.error}`);
    setTimeout(() => setBotMsg(""), 3000);
  };

  const toggleArchive = async (botId) => {
    const updated = bots.map(b => b.id === botId ? { ...b, enabled: !b.enabled } : b);
    setBots(updated);
    await api(`${API}/settings`, { method: "POST", body: JSON.stringify({ key: "bot_registry", value: JSON.stringify(updated.map(b => ({ id: b.id, enabled: b.enabled, display_name: b.display_name || b.id }))) }) });
  };

  const runSolo = async (botId) => {
    setSoloBot(botId);
    await Promise.all(bots.map(b =>
      fetch(`${API}/bots/${b.id}/${b.id === botId ? "resume" : "pause"}`, {
        method: "POST", headers: { Authorization: `Bearer ${token}` },
      })
    ));
    await api(`${API}/start`, { method: "POST" });
  };

  // [db_key, label, isSecret, directLink, lifetime]
  const GROUPS = [
    {
      title: "Stake Casino",
      note: "Log into Stake → F12 → Network tab → click any request → Headers → copy x-access-token. Refreshes when you log out.",
      keys: [
        ["stake_api_token", "x-access-token", true,
         "https://stake.com/settings/security", "session"],
      ],
    },
    {
      title: "Kraken  —  Crypto Trading  (works in Canada)",
      note: "Click the link → Create API key → enable Trade + Query → copy both. Permanent, never expires.",
      keys: [
        ["kraken_api_key", "API Key",    true,
         "https://www.kraken.com/u/security/api", "permanent"],
        ["kraken_secret",  "Private Key", true,
         "https://www.kraken.com/u/security/api", "permanent"],
      ],
    },
    {
      title: "Betfair Exchange  —  Sports Betting  (works in Canada)",
      note: "Click the link → register → My Account → API → Create App Key. Bet against other people, not the house.",
      keys: [
        ["betfair_app_key",  "App Key",  true,
         "https://developer.betfair.com/exchange-api/", "permanent"],
        ["betfair_username", "Username", false, "", "permanent"],
        ["betfair_password", "Password", true,  "", "permanent"],
      ],
    },
    {
      title: "Binance  —  Crypto Trading  (VPN required from Canada)",
      note: "Requires proxy/VPN set below. Click the link → Create API → enable Spot trading → copy both keys. Permanent.",
      keys: [
        ["binance_api_key", "API Key",    true,
         "https://www.binance.com/en/my/settings/api-management", "permanent"],
        ["binance_secret",  "Secret Key", true,
         "https://www.binance.com/en/my/settings/api-management", "permanent"],
      ],
    },
    {
      title: "Kalshi  —  Prediction Markets  (VPN to US required)",
      note: "US residents only — use with proxy/VPN below. Click the link → API Keys → Create Key → paste Private Key (PEM). Permanent.",
      keys: [
        ["kalshi_api_key",     "API Key ID",        true,
         "https://kalshi.com/profile/api", "permanent"],
        ["kalshi_private_key", "Private Key (PEM)", true,
         "https://kalshi.com/profile/api", "permanent"],
      ],
    },
    {
      title: "Telegram  —  Free Alerts  (replaces SMS)",
      note: "1. Click @BotFather → /newbot → copy token.  2. Click @userinfobot → copy your ID. Free forever, instant delivery.",
      keys: [
        ["telegram_bot_token", "Bot Token",    true,
         "https://t.me/BotFather",   "permanent"],
        ["telegram_chat_id",   "Your Chat ID", false,
         "https://t.me/userinfobot", "permanent"],
      ],
    },
    {
      title: "Security",
      note: "",
      keys: [
        ["dashboard_password", "New dashboard password", true, "", ""],
      ],
    },
  ];

  const clockPreview = useClock(tz);

  // ── Credential completion score ──────────────────────────────────────────
  const CRED_CHECKS = [
    { label: "Stake token",    key: "stake_api_token",    pct: 25 },
    { label: "Poly wallet",   key: "__poly_wallet__",    pct: 10, isAuto: true },
    { label: "Kraken key",    key: "kraken_api_key",     pct: 20 },
    { label: "Betfair key",   key: "betfair_app_key",    pct: 15 },
    { label: "Telegram bot",  key: "telegram_bot_token", pct: 15 },
    { label: "Binance key",   key: "binance_api_key",    pct: 10 },
    { label: "Kalshi key",    key: "kalshi_api_key",     pct: 5  },
  ];
  const credScore = CRED_CHECKS.reduce((acc, c) => {
    const filled = c.isAuto ? Boolean(polyWallet?.address) : Boolean(fields[c.key]);
    return acc + (filled ? c.pct : 0);
  }, 0);
  const credColor = credScore >= 80 ? T.green : credScore >= 40 ? T.yellow : T.red;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: SP.xl, maxWidth: 760, animation: "fadein .25s ease" }}>

      {/* ── Connection Health ── clean checklist, not a score gimmick ── */}
      <div>
        <SectionHeader right={`${credScore}% connected`}>Connection Health</SectionHeader>
        <div style={{ height: 3, borderRadius: 2, background: T.line, overflow: "hidden",
          marginBottom: SP.md }}>
          <div style={{ height: "100%", width: `${credScore}%`, background: credColor,
            borderRadius: 2, transition: "width 0.4s ease" }} />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "5px 20px" }}>
          {CRED_CHECKS.map(c => {
            const filled = c.isAuto ? Boolean(polyWallet?.address) : Boolean(fields[c.key]);
            return (
              <div key={c.key} style={{ display: "flex", alignItems: "center", gap: SP.sm,
                padding: "5px 0", borderBottom: `1px solid ${T.line}22` }}>
                <div style={{ width: 5, height: 5, borderRadius: "50%", flexShrink: 0,
                  background: filled ? T.green : T.line,
                  boxShadow: filled ? `0 0 6px ${T.green}55` : "none" }} />
                <span style={{ flex: 1, fontSize: 10, color: filled ? T.fg : T.muted }}>
                  {c.label}
                </span>
                <span style={{ fontSize: 9, fontWeight: 700,
                  color: filled ? T.green : T.muted }}>
                  {filled ? "connected" : "missing"}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Timezone — demoted to a compact utility row ── */}
      <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: SP.md }}>
        <SectionHeader>Clock & Timezone</SectionHeader>
        <div style={{ display: "flex", gap: SP.xs, flexWrap: "wrap", alignItems: "center",
          marginBottom: SP.sm }}>
          {TIMEZONES.map(({ label, tz: z }) => (
            <button key={z} onClick={() => onTzChange(z)} className="dg-chip"
              style={{
                padding: "4px 9px", borderRadius: 5, fontSize: 9, fontWeight: 600,
                cursor: "pointer", letterSpacing: "0.04em",
                background: tz === z ? `${T.blue}18` : "transparent",
                border: `1px solid ${tz === z ? T.blue + "55" : T.line}`,
                color: tz === z ? T.blue : T.muted,
                transition: "all .15s",
              }}>
              {label}
            </button>
          ))}
        </div>
        <div style={{ fontSize: 18, fontWeight: 800, fontFamily: "ui-monospace, monospace",
          color: T.fg, letterSpacing: "0.04em", display: "flex", alignItems: "baseline", gap: 10 }}>
          {clockPreview.time}
          <span style={{ fontSize: 9, color: T.muted, fontWeight: 400, letterSpacing: "0.08em" }}>
            {clockPreview.date} · {clockPreview.city}
          </span>
        </div>
      </div>

      {/* ── VPN / Proxy ── */}
      <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: 16 }}>
        <Label style={{ marginBottom: 6 }}>VPN / Proxy  —  Unlock All Platforms</Label>
        <div style={{ fontSize: 10, color: T.muted, marginBottom: 10, lineHeight: 1.7 }}>
          Routes blocked platforms (Binance, Kalshi) through a proxy. Works with any SOCKS5 source:<br />
          <strong style={{ color: T.fg }}>Free option:</strong> Install Tor → run it → set host <code style={{ background: `${T.fg}15`, padding: "1px 4px", borderRadius: 3 }}>127.0.0.1</code> port <code style={{ background: `${T.fg}15`, padding: "1px 4px", borderRadius: 3 }}>9050</code><br />
          <strong style={{ color: T.fg }}>Paid option:</strong> NordVPN / Mullvad / ExpressVPN → enable SOCKS5 → paste host + port.<br />
          <a href="https://www.torproject.org/download/" target="_blank" rel="noopener noreferrer"
            style={{ color: T.blue, textDecoration: "none", borderBottom: `1px dotted ${T.blue}44` }}>
            Download Tor (free) ↗
          </a>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
          <div style={{ flex: 2 }}>
            <div style={{ fontSize: 10, color: T.muted, marginBottom: 5, fontWeight: 700 }}>SOCKS5 HOST</div>
            <input value={fields.proxy_host || ""} placeholder="127.0.0.1"
              onChange={e => setFields(p => ({ ...p, proxy_host: e.target.value }))}
              style={{ width: "100%", padding: "8px 10px", borderRadius: 8, fontSize: 11 }} />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 10, color: T.muted, marginBottom: 5, fontWeight: 700 }}>PORT</div>
            <input value={fields.proxy_port || ""} placeholder="9050"
              onChange={e => setFields(p => ({ ...p, proxy_port: e.target.value }))}
              style={{ width: "100%", padding: "8px 10px", borderRadius: 8, fontSize: 11 }} />
          </div>
          <Chip onClick={async () => {
            await api(`${API}/settings`, { method: "POST", body: JSON.stringify({ key: "proxy_host", value: fields.proxy_host }) });
            await api(`${API}/settings`, { method: "POST", body: JSON.stringify({ key: "proxy_port", value: fields.proxy_port }) });
          }}>SAVE</Chip>
          <Chip onClick={async () => {
            const r = await api(`${API}/proxy/test`);
            const d = await r.json();
            alert(d.ok ? `Proxy works. Your IP: ${d.ip} (${d.country})` : `Proxy failed: ${d.error}`);
          }}>TEST</Chip>
        </div>
        {(fields.proxy_host || fields.proxy_port) && (
          <div style={{ marginTop: 8, fontSize: 10, color: T.green }}>
            Proxy configured — Binance + Kalshi will route through {fields.proxy_host}:{fields.proxy_port}
          </div>
        )}
      </div>

      {/* Polymarket auto-wallet section */}
      <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: 16 }}>
        <Label style={{ marginBottom: 6 }}>Polymarket  —  Prediction Markets</Label>
        <div style={{ fontSize: 10, color: T.muted, marginBottom: 12, lineHeight: 1.6 }}>
          No MetaMask needed. We generate a dedicated wallet for you — permanent, lasts forever.
          Fund it with <strong style={{ color: T.fg }}>USDC on Polygon</strong> to start trading.
        </div>
        {polyWallet?.address ? (
          <div style={{ background: `${T.green}0d`, border: `1px solid ${T.green}33`,
            borderRadius: 10, padding: "12px 14px" }}>
            <div style={{ fontSize: 9, color: T.green, fontWeight: 800, letterSpacing: "0.12em", marginBottom: 6 }}>
              YOUR POLYMARKET DEPOSIT ADDRESS
            </div>
            <div style={{ fontFamily: "ui-monospace, monospace", fontSize: 11, color: T.fg,
              wordBreak: "break-all", marginBottom: 8 }}>
              {polyWallet.address}
            </div>
            <div style={{ fontSize: 9, color: T.muted }}>
              Send <strong style={{ color: T.fg }}>USDC (Polygon network)</strong> to this address to fund Polymarket bots.
              This address is permanent — it will never change.
            </div>
          </div>
        ) : (
          <button onClick={async () => {
            setPolyWalletBusy(true);
            const r = await api(`${API}/wallet/poly/generate`, { method: "POST" });
            const d = await r.json();
            setPolyWallet(d);
            setPolyWalletBusy(false);
          }} disabled={polyWalletBusy} style={{
            padding: "9px 18px", borderRadius: 8, fontSize: 11, fontWeight: 800,
            background: `${T.blue}22`, border: `1px solid ${T.blue}66`,
            color: T.blue, cursor: polyWalletBusy ? "not-allowed" : "pointer",
            letterSpacing: "0.08em",
          }}>
            {polyWalletBusy ? "Generating…" : "GENERATE WALLET"}
          </button>
        )}
      </div>

      {GROUPS.map(g => (
        <div key={g.title} style={{ borderTop: `1px solid ${T.line}`, paddingTop: SP.md }}>
          <SectionHeader>{g.title}</SectionHeader>
          {g.note && (
            <div style={{ fontSize: 10, color: T.muted, marginBottom: SP.sm, lineHeight: 1.7,
              paddingLeft: SP.xs, borderLeft: `2px solid ${T.line}` }}>
              {g.note}
            </div>
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: SP.md }}>
            {g.keys.map(([key, label, secret, link, lifetime]) => {
              const isFilled = Boolean(fields[key]);
              return (
                <div key={key}>
                  <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: SP.xs }}>
                    <div style={{ display: "flex", alignItems: "center", gap: SP.sm }}>
                      {link ? (
                        <a href={link} target="_blank" rel="noopener noreferrer"
                          style={{ fontSize: 10, fontWeight: 700, color: T.blue,
                            textDecoration: "none", borderBottom: `1px dotted ${T.blue}44` }}>
                          {label} ↗
                        </a>
                      ) : (
                        <span style={{ fontSize: 10, fontWeight: 700, color: T.muted }}>{label}</span>
                      )}
                      {lifetime === "permanent" && (
                        <span style={{ fontSize: 7, fontWeight: 800, letterSpacing: "0.1em",
                          padding: "2px 5px", borderRadius: 3,
                          background: `${T.green}14`, color: T.green }}>
                          PERM
                        </span>
                      )}
                      {lifetime === "session" && (
                        <span style={{ fontSize: 7, fontWeight: 800, letterSpacing: "0.1em",
                          padding: "2px 5px", borderRadius: 3,
                          background: `${T.yellow}14`, color: T.yellow }}>
                          SESSION
                        </span>
                      )}
                      {/* Connection indicator */}
                      <span style={{ fontSize: 8, color: isFilled ? T.green : T.muted,
                        fontWeight: 600 }}>
                        {isFilled ? "✓" : "—"}
                      </span>
                    </div>
                    {secret && (
                      <button onClick={() => setShow(p => ({ ...p, [key]: !p[key] }))}
                        style={{ background: "none", border: "none", fontSize: 8,
                          color: T.muted, cursor: "pointer", letterSpacing: "0.1em",
                          fontFamily: T.font }}>
                        {show[key] ? "HIDE" : "REVEAL"}
                      </button>
                    )}
                  </div>
                  <div style={{ display: "flex", gap: SP.xs }}>
                    <input type={secret && !show[key] ? "password" : "text"}
                      value={fields[key] || ""} placeholder="paste here"
                      onChange={e => setFields(p => ({ ...p, [key]: e.target.value }))}
                      style={{ flex: 1, padding: "7px 10px", borderRadius: 6, fontSize: 10,
                        borderColor: isFilled ? `${T.green}33` : T.line }} />
                    <Chip onClick={() => save(key)} style={{ minWidth: 52, justifyContent: "center" }}>
                      {saving[key] ? <Spinner size={9} /> : "SAVE"}
                    </Chip>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}

      <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: 16 }}>
        <Label style={{ marginBottom: 14 }}>Bot Management</Label>

        {/* Bot table */}
        {bots.length > 0 && (
          <div style={{ marginBottom: 18 }}>
            <div style={{ display: "grid", gridTemplateColumns: "16px 1fr 60px 90px 56px 80px 80px", gap: 8, alignItems: "center",
              fontSize: 9, color: T.muted, fontWeight: 700, letterSpacing: "0.12em", paddingBottom: 6,
              borderBottom: `1px solid ${T.line}` }}>
              <span></span>
              <span>BOT ID</span>
              <span>PLATFORM</span>
              <span>STRATEGY</span>
              <span>START $</span>
              <span>STATUS</span>
              <span>ACTION</span>
            </div>
            {bots.map(b => {
              const col = BC[b.id] || T.fg;
              const isSolo = soloBot === b.id;
              const isArchived = b.enabled === false;
              return (
                <div key={b.id} style={{
                  display: "grid", gridTemplateColumns: "16px 1fr 60px 90px 56px 80px 80px", gap: 8,
                  alignItems: "center", padding: "8px 0",
                  borderBottom: `1px solid ${T.line}22`,
                  opacity: isArchived ? 0.42 : 1,
                  background: isSolo ? `${col}11` : "transparent",
                  transition: "background 0.2s, opacity 0.2s",
                }}>
                  <span style={{ width: 10, height: 10, borderRadius: "50%", background: col, display: "inline-block", flexShrink: 0 }} />
                  <span style={{ fontSize: 11, fontWeight: 700, color: col, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{b.id}</span>
                  <span style={{
                    fontSize: 9, fontWeight: 700, padding: "2px 5px", borderRadius: 4,
                    background: b.platform === "stake" ? "rgba(94,161,255,0.12)" : "rgba(192,137,255,0.12)",
                    color: b.platform === "stake" ? T.blue : T.purple,
                  }}>{(b.platform || "").toUpperCase()}</span>
                  <span style={{ fontSize: 9, color: T.muted }}>{(b.strategy || b.kind || "").replace(/_/g," ")}</span>
                  <span style={{ fontSize: 10, color: T.fg }}>${b.start_amount || b.initial || 20}</span>
                  <button onClick={() => toggleArchive(b.id)} style={{
                    background: "none", border: `1px solid ${isArchived ? T.line : T.green + "55"}`,
                    color: isArchived ? T.muted : T.green, padding: "3px 7px", borderRadius: 5,
                    fontSize: 9, fontWeight: 700, cursor: "pointer", letterSpacing: "0.06em",
                  }}>
                    {isArchived ? "ARCHIVED" : "ACTIVE"}
                  </button>
                  <button onClick={() => runSolo(b.id)} disabled={isArchived} style={{
                    background: isSolo ? `${col}22` : "none",
                    border: `1px solid ${isSolo ? col : T.line}`,
                    color: isSolo ? col : T.muted, padding: "3px 7px", borderRadius: 5,
                    fontSize: 9, fontWeight: 700,
                    cursor: isArchived ? "not-allowed" : "pointer", letterSpacing: "0.06em",
                    opacity: isArchived ? 0.4 : 1,
                  }}>
                    <svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor" style={{verticalAlign:"middle",marginRight:3}}><path d="M8 5v14l11-7z"/></svg>
                    SOLO
                  </button>
                </div>
              );
            })}
            {soloBot && (
              <div style={{ marginTop: 8, fontSize: 10, color: T.yellow, display: "flex", alignItems: "center", gap: 6 }}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg>
                Running solo: <strong style={{ color: T.fg }}>{soloBot}</strong> — all others paused.
                <button onClick={() => setSoloBot(null)} style={{ background: "none", border: "none", color: T.muted, fontSize: 9, cursor: "pointer", marginLeft: 4 }}>clear</button>
              </div>
            )}
          </div>
        )}

        {/* Add new bot form */}
        <div style={{ marginTop: 4 }}>
          <div style={{ fontSize: 10, color: T.muted, marginBottom: 10, fontWeight: 700, letterSpacing: "0.1em" }}>ADD NEW BOT</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <input value={newBot.id} onChange={e => setNewBot(p => ({ ...p, id: e.target.value }))}
              placeholder="bot11_dice" style={{ width: 110, padding: "7px 10px", borderRadius: 8, fontSize: 11 }} />
            <select value={newBot.platform} onChange={e => setNewBot(p => ({ ...p, platform: e.target.value }))}
              style={{ padding: "7px 10px", borderRadius: 8, fontSize: 11 }}>
              <option value="stake">stake</option>
              <option value="poly">poly</option>
            </select>
            <select value={newBot.strategy} onChange={e => setNewBot(p => ({ ...p, strategy: e.target.value }))}
              style={{ padding: "7px 10px", borderRadius: 8, fontSize: 11 }}>
              <option value="dice">dice</option>
              <option value="limbo">limbo</option>
              <option value="mines">mines</option>
              <option value="edge_scanner">edge_scanner</option>
              <option value="btc_momentum">btc_momentum</option>
              <option value="intra_arb">intra_arb</option>
              <option value="resolution_sniper">resolution_sniper</option>
              <option value="volume_spike">volume_spike</option>
            </select>
            <input value={newBot.initial} onChange={e => setNewBot(p => ({ ...p, initial: e.target.value }))}
              placeholder="$20" style={{ width: 62, padding: "7px 10px", borderRadius: 8, fontSize: 11 }} />
            <ActionBtn onClick={addBot}>ADD</ActionBtn>
          </div>
          {botMsg && <div style={{ marginTop: 8, fontSize: 10, color: botMsg.startsWith("Error") ? T.red : T.green }}>{botMsg}</div>}
        </div>
      </div>

      {/* ── Embedded Tor VPN ── */}
      <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: 16 }}>
        <Label style={{ marginBottom: 6 }}>Built-in VPN  —  Tor Network</Label>
        <div style={{ fontSize: 10, color: T.muted, lineHeight: 1.7, marginBottom: 12 }}>
          Routes all restricted platforms (Binance, Kalshi) through the Tor network automatically.
          Free, anonymous, no account. Requires one-time install:
          <br />
          <code style={{ background: `${T.fg}12`, padding: "2px 6px", borderRadius: 4, fontSize: 10 }}>brew install tor</code>
          <span style={{ marginLeft: 8 }}>→ restart the app → Tor starts automatically.</span>
        </div>
        {(() => {
          const col = vpn.status === "ready" ? T.green : vpn.status === "starting" ? T.yellow : vpn.status === "not_installed" ? T.orange : T.muted;
          const label = { ready:"CONNECTED", starting:"CONNECTING…", not_installed:"NOT INSTALLED", failed:"FAILED", off:"OFFLINE" }[vpn.status] || vpn.status.toUpperCase();
          return (
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <div style={{ background: `${col}12`, border: `1px solid ${col}44`,
                borderRadius: 10, padding: "10px 14px", flex: 1, minWidth: 200 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <div style={{ width: 8, height: 8, borderRadius: "50%", background: col,
                    animation: vpn.status === "ready" ? "pulse 2s infinite" : "none" }} />
                  <span style={{ fontSize: 11, fontWeight: 800, color: col, letterSpacing: "0.08em" }}>{label}</span>
                </div>
                {vpn.status === "ready" && vpn.ip && (
                  <div style={{ fontSize: 10, color: T.muted }}>
                    Exit IP: <span style={{ color: T.fg }}>{vpn.ip}</span>
                    {vpn.country && <span> — {vpn.country}</span>}
                  </div>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {vpn.status !== "ready" && vpn.installed !== false && (
                  <Chip onClick={async () => {
                    await api(`${API}/vpn/start`, { method: "POST" });
                    setVpn(v => ({ ...v, status: "starting" }));
                  }}>START TOR</Chip>
                )}
                {vpn.status === "ready" && (
                  <Chip onClick={async () => {
                    setVpn(v => ({ ...v, ip: null, country: null }));
                    await api(`${API}/vpn/renew`, { method: "POST" });
                  }}>NEW CIRCUIT</Chip>
                )}
                {vpn.status === "not_installed" && (
                  <div style={{ fontSize: 9, color: T.orange }}>
                    Run: <code>brew install tor</code>
                  </div>
                )}
              </div>
            </div>
          );
        })()}
      </div>

      {/* ── Remote Access Tunnel ── */}
      <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: 16 }}>
        <Label style={{ marginBottom: 6 }}>Remote Access  —  iPhone / iPad from anywhere</Label>
        <div style={{ fontSize: 10, color: T.muted, lineHeight: 1.7, marginBottom: 12 }}>
          Your permanent free URL: <strong style={{ color: T.blue, fontFamily: "ui-monospace, monospace" }}>https://d3gns.loca.lt</strong><br />
          Open that on your iPhone, iPad, or any browser — works anywhere in the world.
          No account, no setup, free forever.
        </div>
        {(() => {
          const col = tunnel.status === "ready" ? T.green : tunnel.status === "starting" ? T.yellow : T.muted;
          return (
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <div style={{ background: `${col}12`, border: `1px solid ${col}44`,
                borderRadius: 10, padding: "10px 14px", flex: 1, minWidth: 200 }}>
                {tunnel.status === "ready" && tunnel.url ? (
                  <div>
                    <div style={{ fontSize: 9, color: T.green, fontWeight: 800, letterSpacing: "0.1em", marginBottom: 4 }}>
                      YOUR REMOTE URL
                    </div>
                    <div style={{ fontFamily: "ui-monospace, monospace", fontSize: 11, color: T.fg,
                      wordBreak: "break-all" }}>{tunnel.url}</div>
                    <div style={{ fontSize: 9, color: T.muted, marginTop: 4 }}>
                      Open this on your iPhone or iPad — works anywhere with internet.
                    </div>
                  </div>
                ) : (
                  <div style={{ fontSize: 11, color: T.muted }}>
                    {{ starting: "Starting tunnel…", off: "Tunnel not running",
                       failed: "Tunnel failed — check cloudflared is installed",
                       not_installed: "cloudflared not installed" }[tunnel.status] || "—"}
                  </div>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {tunnel.status !== "ready" && (
                  <Chip onClick={async () => {
                    await api(`${API}/tunnel/start`, { method: "POST" });
                    setTunnel(t => ({ ...t, status: "starting" }));
                  }}>START</Chip>
                )}
                {tunnel.status === "ready" && (
                  <Chip onClick={async () => {
                    await api(`${API}/tunnel/stop`, { method: "POST" });
                    setTunnel({ status: "off", url: null, installed: true });
                  }}>STOP</Chip>
                )}
                {tunnel.status === "not_installed" && (
                  <div style={{ fontSize: 9, color: T.orange, maxWidth: 180 }}>
                    npm/npx not found — install Node.js
                  </div>
                )}
              </div>
            </div>
          );
        })()}
      </div>

      {/* ── Device Manager ── */}
      <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: 16 }}>
        <Label style={{ marginBottom: 6 }}>Trusted Devices</Label>
        <div style={{ fontSize: 10, color: T.muted, marginBottom: 12, lineHeight: 1.6 }}>
          Only approved devices can log in. Your current device was auto-approved on first login.
          When a new device tries to log in, it appears here as pending — approve it to allow access.
        </div>
        {devices.length === 0 ? (
          <div style={{ fontSize: 10, color: T.muted }}>No devices registered yet.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {devices.map(d => {
              const isMe = d.id === getDeviceId();
              const col  = d.approved ? T.green : T.orange;
              return (
                <div key={d.id} style={{ display: "flex", alignItems: "center", gap: 10,
                  padding: "10px 12px", borderRadius: 8,
                  border: `1px solid ${col}33`, background: `${col}08` }}>
                  <div style={{ width: 8, height: 8, borderRadius: "50%", background: col, flexShrink: 0 }} />
                  <div style={{ flex: 1 }}>
                    <input value={renameMap[d.id] ?? d.name}
                      onChange={e => setRenameMap(m => ({ ...m, [d.id]: e.target.value }))}
                      onBlur={async () => {
                        const n = renameMap[d.id];
                        if (n && n !== d.name) {
                          await api(`${API}/devices/rename`, { method: "POST", body: JSON.stringify({ device_id: d.id, name: n }) });
                          setDevices(ds => ds.map(x => x.id === d.id ? { ...x, name: n } : x));
                        }
                      }}
                      style={{ background: "none", border: "none", fontSize: 11, fontWeight: 700,
                        color: T.fg, width: "100%", outline: "none", cursor: "text" }} />
                    <div style={{ fontSize: 9, color: T.muted, marginTop: 2 }}>
                      {isMe && <span style={{ color: T.blue, marginRight: 6 }}>THIS DEVICE</span>}
                      {d.approved ? "Approved" : "Pending"} · Last seen {d.last_seen?.slice(0,16).replace("T"," ")}
                    </div>
                  </div>
                  {!d.approved && (
                    <button onClick={async () => {
                      const r = await api(`${API}/devices/approve`, { method: "POST", body: JSON.stringify({ device_id: d.id }) });
                      const j = await r.json(); setDevices(j.devices || devices);
                    }} style={{ padding: "4px 10px", borderRadius: 6, fontSize: 9, fontWeight: 800,
                      background: `${T.green}22`, border: `1px solid ${T.green}55`, color: T.green, cursor: "pointer" }}>
                      APPROVE
                    </button>
                  )}
                  {d.approved && !isMe && (
                    <button onClick={async () => {
                      if (!confirm(`Revoke access for ${d.name}?`)) return;
                      const r = await api(`${API}/devices/revoke`, { method: "POST", body: JSON.stringify({ device_id: d.id }) });
                      const j = await r.json(); setDevices(j.devices || devices);
                    }} style={{ padding: "4px 10px", borderRadius: 6, fontSize: 9, fontWeight: 800,
                      background: `${T.red}22`, border: `1px solid ${T.red}55`, color: T.red, cursor: "pointer" }}>
                      REVOKE
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: 16 }}>
        <Label style={{ marginBottom: 14 }}>Admin Vault</Label>
        <div style={{ fontSize: 11, color: T.muted, lineHeight: 1.6, marginBottom: 12 }}>
          Local-only. Encrypted with your passphrase, stored in this browser only. Never sent to the backend.
        </div>
        {!adminUnlocked ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <input type="password" value={adminPass} onChange={e => setAdminPass(e.target.value)}
              placeholder="Enter admin passphrase"
              style={{ padding: "8px 10px", borderRadius: 8, fontSize: 11, maxWidth: 280 }} />
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Chip onClick={unlockAdmin}>UNLOCK</Chip>
              <Chip onClick={() => { setAdminUnlocked(true); setAdminMsg("New vault ready. Add details and save."); }}>NEW VAULT</Chip>
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr", gap: 10 }}>
              {[["owner_name","Full name"],["owner_email","Primary email"],["owner_phone","Phone"],["wallet_address","Wallet address"]].map(([key, label]) => (
                <div key={key}>
                  <div style={{ fontSize: 10, color: T.muted, marginBottom: 6 }}>{label}</div>
                  <input value={adminVault[key] || ""} onChange={e => setAdminVault(p => ({ ...p, [key]: e.target.value }))}
                    style={{ width: "100%", padding: "8px 10px", borderRadius: 8, fontSize: 11 }} />
                </div>
              ))}
            </div>
            {["payout_notes","security_notes"].map(key => (
              <div key={key}>
                <div style={{ fontSize: 10, color: T.muted, marginBottom: 6 }}>{key.replace("_"," ")}</div>
                <textarea value={adminVault[key] || ""} onChange={e => setAdminVault(p => ({ ...p, [key]: e.target.value }))}
                  style={{ width: "100%", minHeight: 70, padding: "8px 10px", borderRadius: 8, fontSize: 11,
                    background: "transparent", color: T.fg, border: `1px solid ${T.line}` }} />
              </div>
            ))}
            <div>
              <div style={{ fontSize: 10, color: T.muted, marginBottom: 6 }}>New or rotate passphrase</div>
              <input type="password" value={adminNewPass} onChange={e => setAdminNewPass(e.target.value)}
                placeholder="Leave blank to keep current"
                style={{ width: "100%", maxWidth: 320, padding: "8px 10px", borderRadius: 8, fontSize: 11 }} />
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <ActionBtn onClick={saveAdmin}>SAVE ENCRYPTED</ActionBtn>
              <Chip onClick={lockAdmin}>LOCK</Chip>
              <Chip onClick={exportAdmin}>EXPORT</Chip>
              <label style={{ display: "inline-flex", alignItems: "center" }}>
                <span style={{ fontSize: 11, border: `1px solid ${T.line}`, padding: "7px 10px", borderRadius: 8,
                  background: "rgba(255,255,255,0.02)", cursor: "pointer" }}>IMPORT</span>
                <input type="file" accept="application/json" onChange={importAdmin} style={{ display: "none" }} />
              </label>
              <Chip onClick={clearAdmin}>DELETE LOCAL VAULT</Chip>
            </div>
          </div>
        )}
        {adminMsg && <div style={{ marginTop: 10, fontSize: 11, color: adminMsg.toLowerCase().includes("wrong") || adminMsg.toLowerCase().includes("unable") ? T.red : T.green }}>{adminMsg}</div>}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  GUIDE / TUTORIAL TAB
// ─────────────────────────────────────────────────────────────────────────────
const GUIDE_SECTIONS = [
  {
    emoji: "🚀",
    title: "What Is DeG£N$?",
    body: `DeG£N$ is a 6-bot automated trading system that runs 24/7 and bets on two platforms simultaneously:

• Stake Casino — Bots 1, 2, 3 play Dice, Limbo, and Mines using a 5-phase risk engine.
• Polymarket — Bots 4, 5, 6 scan prediction markets and place bets where there is a real statistical edge.

The goal is to grow each bot's starting bankroll 10× using compounding, profit-locking, and automated risk management. You do not click buttons or watch trades. You start it and let it run.`,
  },
  {
    emoji: "🔑",
    title: "Step 1 — Connect Your Accounts (Settings)",
    body: `Open the Settings tab and fill in your credentials. Each field has a SAVE button — press it after typing.

━━ STAKE CASINO ━━
  Key needed:  x-access-token
  How to get it:
    1. Log in at stake.com in Chrome.
    2. Press F12 to open DevTools → click the Network tab.
    3. Place any small manual bet (Dice is easiest).
    4. In the Network list, click the request named "_api/graphql".
    5. Click "Headers" → scroll to "Request Headers".
    6. Copy the value next to  x-access-token.
    7. Paste it into Stake Casino → x-access-token in Settings and press SAVE.
  Note: Stake tokens expire. If bots stop responding, repeat this and save a fresh token.

━━ POLYMARKET ━━
  Keys needed:  Private Key · API Key · API Secret · Passphrase
  Requirements: MetaMask wallet with Polygon network added.

  Your Polygon wallet address shown in MetaMask (0xA2113…A6d35) is the right one.
  The Bitcoin address (bc1q6ta…) does NOT work — Polymarket runs on Polygon, not Bitcoin.

  Step A — Export your MetaMask Private Key:
    1. Open MetaMask → click the three dots next to Account 1 → Account details.
    2. Click "Show private key" → enter your MetaMask password.
    3. Copy the 64-character hex string (starts with 0x or just hex digits).
    4. Paste it into Polymarket → Wallet Private Key in Settings and press SAVE.
  WARNING: Never share this key with anyone. It controls your wallet.

  Step B — Generate Polymarket API keys (one-time setup, run in Terminal):
    cd ~/Desktop/~/DEGENS
    pip install py-clob-client
    python -c "
    from py_clob_client.client import ClobClient
    import os
    client = ClobClient(host='https://clob.polymarket.com', chain_id=137, key='YOUR_PRIVATE_KEY_HERE')
    creds = client.create_or_derive_api_creds()
    print('API Key:       ', creds.api_key)
    print('API Secret:    ', creds.api_secret)
    print('Passphrase:    ', creds.api_passphrase)
    "
    Copy the three values and paste them into the matching fields in Settings.`,
  },
  {
    emoji: "🤖",
    title: "Step 2 — Start the Backend Server",
    body: `The UI is just a dashboard. The actual bots run in a Python server on your machine.

  Open Terminal and run:
    cd ~/Desktop/~/DEGENS
    pip install -r requirements.txt
    python server.py

  You will see: "Uvicorn running on http://0.0.0.0:8000"
  Leave that Terminal window open — closing it stops the bots.

  Then open the UI (either the browser already on port 5173, or run: cd ui && npm run dev).
  Log in with your dashboard password.

  To run bots in PAPER MODE first (fake money, no real bets):
    python paper_main.py
  This lets you verify everything connects before using real funds.`,
  },
  {
    emoji: "📊",
    title: "Step 3 — The Overview Tab",
    body: `The Overview tab is your main dashboard.

  TOP ROW — Portfolio summary:
    • Total Deployed  — total real money across all bots
    • Active (in play) — current combined bankroll
    • Locked Profits  — money already secured/withdrawn from play
    • Combined Value  — active + locked (your real net worth in the system)
    • Overall Progress — how many X toward the 10x goal

  BOT CARDS — Each card shows:
    • Phase badge (Floor / Ultra-Safe / Safe / Normal / Turbo)
      – Green/Turbo = bot is winning and pressing hard
      – Blue/Normal = steady compounding
      – Yellow/Safe = small drawdown, playing conservatively
      – Orange/Ultra-Safe = significant drawdown, tiny bets
      – Red/Floor = emergency mode, dust bets only to recover
    • Bankroll — current cash for that bot
    • Progress bar — distance to 10x target
    • Win rate and bet count

  EQUITY CHART — Shows the combined bankroll over time. Rising = good.`,
  },
  {
    emoji: "🤖",
    title: "Step 4 — The Bots Tab",
    body: `The Bots tab lets you control individual bots.

  Each bot card expands to show:
    • Current phase, bankroll, streak, and drawdown
    • PAUSE / RESUME — pause one bot without stopping others
    • RESET — resets that bot's bankroll tracking (does not move real money)
    • Setup panel — change the bot's starting bankroll and strategy profile

  Strategy profiles:
    • Conservative — 0.5x bet sizes, very low risk, slower growth
    • Balanced — 1x (default), the system's intended operation
    • Aggressive — 2x bet sizes, faster growth but deeper drawdowns possible
    • Turbo — 3.5x, maximum speed, higher variance. Use only when winning.

  NOTE: Do not run all bots on Turbo. Bots regulate themselves automatically — let the phase engine do its job.`,
  },
  {
    emoji: "📈",
    title: "Step 5 — Analytics Tab",
    body: `The Analytics tab has four sub-sections:

  EQUITY — Line chart of portfolio value over time. Look for a steady upward slope.

  HISTORY — Every bet recorded. Green rows = wins, red rows = losses.
    Filter by bot or date to review specific sessions.

  PROJECTIONS — Monte Carlo simulation based on your actual win rate and bet sizing.
    • The center line is the median expected path.
    • The shaded band is the 10th–90th percentile range.
    • If the median path reaches 10x before your target hours, your settings are calibrated well.

  WORKSHOP — Test different strategy parameters (bankroll, bet %, target) in a sandbox.
    Changes here do NOT affect live bots. Use it to experiment safely.`,
  },
  {
    emoji: "💰",
    title: "Step 6 — Profit Locking (How You Take Money Out)",
    body: `The system automatically locks profits so you never give back big gains.

  How it works:
    • Every time a bot's bankroll grows +10% above its start, 50% of that gain is "locked."
    • Locked money is tracked separately and is safe even if the bot later loses.
    • When the bankroll hits +20% above start, the excess is auto-withdrawn.

  Example (starting at $20 per bot):
    • Bankroll reaches $22 → +$2 gain → $1 locked, $21 stays in play
    • Bankroll reaches $24 → +$4 total → another $1 locked, etc.
    • This ratchet keeps your floor rising over time.

  The Wallet tab shows your total locked profits and lets you track withdrawals.
  To actually move money out, do it manually on Stake or Polymarket — the system tracks it but does not transfer funds automatically.`,
  },
  {
    emoji: "🛑",
    title: "Step 7 — Risk Rules & Circuit Breakers",
    body: `The system protects your bankroll automatically. You do not need to babysit it.

  PHASE ENGINE (automatic, per bot):
    Floor      → bankroll dropped 10% from start → dust bets (0.1% of bank)
    Ultra-Safe → dropped 5% → tiny bets (0.2%)
    Safe       → dropped 3% → small bets (0.5%)
    Normal     → healthy → standard bets (2%)
    Turbo      → 3 consecutive wins + zero drawdown → press bets (4%)

  CIRCUIT BREAKERS (automatic pauses):
    • 3 consecutive losses → pause 2 minutes
    • 5 consecutive losses → pause 10 minutes
    • -3% loss in 5 minutes → pause 10 minutes
    • -5% loss in 30 minutes → pause 30 minutes
    After a circuit breaker, the bot resumes one phase lower than before.

  HARD FLOOR:
    If a bot's bankroll drops to 90% of its start ($18 on a $20 start), it stops permanently until you manually reset it. This prevents a runaway loss from wiping out more than $2.`,
  },
  {
    emoji: "⚙️",
    title: "Step 8 — Settings Reference",
    body: `All credentials are saved encrypted in the backend database. They are never stored in plain text in your browser.

  Field reference:
    stake_api_token      → The x-access-token from Stake DevTools (expires, refresh when bots stop)
    poly_private_key     → Your MetaMask Polygon private key (64-char hex, starts with 0x optional)
    poly_api_key         → Generated by py-clob-client (see Step 1)
    poly_api_secret      → Generated by py-clob-client
    poly_api_passphrase  → Generated by py-clob-client
    twilio_account_sid   → Optional. Sign up at twilio.com for free SMS alerts.
    twilio_auth_token    → Twilio auth token
    twilio_from_number   → Your Twilio phone number (e.g. +14155550100)
    notify_phone         → Your personal phone to receive alerts
    dashboard_password   → Change this from the default to something only you know.

  Admin Vault (local only):
    A separate encrypted store for personal info (name, wallet address, notes).
    Never sent to the server. Stored only in this browser, encrypted with a passphrase you choose.
    Export it as a JSON file and keep a backup somewhere safe.`,
  },
  {
    emoji: "❓",
    title: "FAQ & Common Issues",
    body: `Q: Bots show $0 bankroll and nothing is happening.
A: The Python server is not running. Open Terminal → cd ~/Desktop/~/DEGENS → python server.py

Q: Stake bots say "GraphQL error" or "401 Unauthorized."
A: Your Stake token expired. Get a fresh one from DevTools and save it in Settings.

Q: Polymarket bots are idle / not placing bets.
A: Either your Poly credentials are missing, or there are no markets with sufficient edge right now. POLY_MIN_EDGE = 0.05 means the bot needs a 5% statistical edge. This is intentionally strict to protect your money.

Q: I see a "CIRCUIT BREAKER" status on a bot.
A: Normal. The bot hit a losing streak and is cooling down. It resumes automatically. Do not restart the server.

Q: How do I add more starting money?
A: Change BOT_INITIAL_BANK in config.py and restart server.py. The UI will reflect the new starting point.

Q: Can I run this on a server so it runs 24/7?
A: Yes. Copy the DEGENS folder to a Linux VPS, run: nohup python server.py &
   Then access the dashboard remotely by opening port 8000 (or use an nginx reverse proxy).

Q: The Stake token — where exactly do I see it in DevTools?
A: F12 → Network tab → place a Dice bet → click the request "_api/graphql" → Headers tab → scroll down to "Request Headers" → x-access-token is right there.`,
  },
];

// First 2 sections shown inline; rest in accordions
const GUIDE_INLINE_COUNT = 2;

function GuideTab() {
  const [open, setOpen] = useState(null);
  const [activeSection, setActiveSection] = useState(null);
  const sectionRefs = useRef({});

  const scrollTo = (i) => {
    setActiveSection(i);
    const el = sectionRefs.current[i];
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const inlineSections = GUIDE_SECTIONS.slice(0, GUIDE_INLINE_COUNT);
  const accordionSections = GUIDE_SECTIONS.slice(GUIDE_INLINE_COUNT);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "180px 1fr", gap: SP.xl,
      maxWidth: 940, animation: "fadein .25s ease", alignItems: "start" }}>

      {/* ── Left: Table of Contents ── */}
      <div style={{ position: "sticky", top: 0 }}>
        <div style={{ fontSize: 8, fontWeight: 800, letterSpacing: "0.16em", color: T.muted,
          textTransform: "uppercase", marginBottom: SP.sm }}>Contents</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {GUIDE_SECTIONS.map((s, i) => (
            <button key={i} onClick={() => scrollTo(i)} className="dg-nav-btn"
              style={{ background: "none", border: "none", padding: "4px 0",
                textAlign: "left", cursor: "pointer",
                fontSize: 10, fontWeight: activeSection === i ? 700 : 400,
                color: activeSection === i ? T.fg : T.muted,
                borderLeft: `2px solid ${activeSection === i ? T.blue : "transparent"}`,
                paddingLeft: SP.sm, transition: "color .15s",
                lineHeight: 1.5 }}>
              {i < GUIDE_INLINE_COUNT ? "★ " : ""}{s.title.replace(/^Step \d+ — /, "")}
            </button>
          ))}
        </div>
      </div>

      {/* ── Right: Content ── */}
      <div style={{ display: "flex", flexDirection: "column", gap: SP.xl }}>

        {/* Quick Start — shown inline without clicking */}
        <div>
          <div style={{ fontSize: 8, fontWeight: 800, letterSpacing: "0.16em", color: T.blue,
            textTransform: "uppercase", marginBottom: SP.sm }}>
            Quick Start
          </div>
          {inlineSections.map((s, i) => (
            <div key={i} ref={el => sectionRefs.current[i] = el}
              style={{ marginBottom: SP.lg, paddingBottom: SP.lg,
                borderBottom: `1px solid ${T.line}` }}>
              <div style={{ fontSize: 14, fontWeight: 800, letterSpacing: "-0.02em",
                color: T.fg, marginBottom: SP.sm }}>
                {s.title}
              </div>
              <div style={{ fontSize: 11, color: T.fg, lineHeight: 1.85,
                whiteSpace: "pre-wrap" }}>
                {s.body}
              </div>
            </div>
          ))}
        </div>

        {/* Reference sections — accordion */}
        <div>
          <div style={{ fontSize: 8, fontWeight: 800, letterSpacing: "0.16em", color: T.muted,
            textTransform: "uppercase", marginBottom: SP.sm }}>
            Reference
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: SP.xs }}>
            {accordionSections.map((s, idx) => {
              const i = idx + GUIDE_INLINE_COUNT;
              const isOpen = open === i;
              return (
                <div key={i} ref={el => sectionRefs.current[i] = el}
                  style={{ border: `1px solid ${isOpen ? T.blue + "44" : T.line}`,
                    borderRadius: 8, overflow: "hidden",
                    transition: "border-color .2s",
                    background: isOpen ? "rgba(94,161,255,0.03)" : "transparent" }}>
                  <button onClick={() => setOpen(isOpen ? null : i)}
                    style={{ width: "100%", display: "flex", alignItems: "center",
                      gap: SP.sm, padding: "12px 14px",
                      background: "none", border: "none", cursor: "pointer", textAlign: "left" }}>
                    <span style={{ flex: 1, fontSize: 11, fontWeight: 700,
                      color: isOpen ? T.blue : T.fg, letterSpacing: "-0.01em" }}>
                      {s.title}
                    </span>
                    <span style={{ fontSize: 9, color: T.muted, transition: "transform .2s",
                      display: "inline-block",
                      transform: isOpen ? "rotate(180deg)" : "none" }}>▼</span>
                  </button>
                  {isOpen && (
                    <div style={{ padding: "0 14px 16px", fontSize: 11, color: T.fg,
                      lineHeight: 1.85, whiteSpace: "pre-wrap",
                      borderTop: `1px solid ${T.line}`, paddingTop: SP.sm }}>
                      {s.body}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  SIMULATE TAB — Paper Strategy Tester (M currency)
// ─────────────────────────────────────────────────────────────────────────────

// Double-barred M symbol  (like ₦ but M — Monopoly / paper money)
function Msym({ style = {} }) {
  return (
    <span style={{ display: "inline-block", position: "relative", fontWeight: 800,
      letterSpacing: 0, lineHeight: 1, ...style }}>
      M
      <span style={{ position: "absolute", left: "5%", right: "5%", top: "32%",
        borderTop: "1.5px solid currentColor", pointerEvents: "none" }} />
      <span style={{ position: "absolute", left: "5%", right: "5%", top: "56%",
        borderTop: "1.5px solid currentColor", pointerEvents: "none" }} />
    </span>
  );
}

function mfmt(n) {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : n.toFixed(2);
}

const SIM_PRESETS = [
  { id: "safe",       label: "SAFE",       winRate: 0.63, betPct: 0.020, color: "#59d47a",
    desc: "63% win · 2% bet / round" },
  { id: "balanced",   label: "BALANCED",   winRate: 0.59, betPct: 0.035, color: "#5ea1ff",
    desc: "59% win · 3.5% bet / round" },
  { id: "aggressive", label: "AGGRESSIVE", winRate: 0.55, betPct: 0.055, color: "#d6af41",
    desc: "55% win · 5.5% bet / round" },
  { id: "turbo",      label: "TURBO",      winRate: 0.51, betPct: 0.090, color: "#ef5f57",
    desc: "51% win · 9% bet / round — high risk" },
];

const SIM_PLATFORMS = [
  { id: "dice",  label: "Dice",  mult: 1.92 },
  { id: "limbo", label: "Limbo", mult: 1.98 },
  { id: "mines", label: "Mines", mult: 2.10 },
  { id: "poly",  label: "Poly",  mult: 1.80 },
];

function runSim({ startM, targetM, floorM, strategy, platform, maxBets }) {
  const preset = SIM_PRESETS.find(p => p.id === strategy) || SIM_PRESETS[1];
  const plat   = SIM_PLATFORMS.find(p => p.id === platform) || SIM_PLATFORMS[0];
  let bank = startM;
  let wins = 0, losses = 0, streak = 0, bestStreak = 0, worstStreak = 0;
  const curve = [{ i: 0, v: startM }];
  const log   = [];
  const step  = Math.max(1, Math.floor(maxBets / 150));

  for (let i = 1; i <= maxBets; i++) {
    if (bank <= floorM || bank >= targetM) break;
    const bet = Math.max(0.01, Math.min(bank * preset.betPct, bank - floorM));
    const won = Math.random() < preset.winRate;
    const pnl = won ? +(bet * (plat.mult - 1)).toFixed(4) : -bet;
    bank = +(Math.max(0, bank + pnl)).toFixed(4);
    if (won) { wins++; streak = streak > 0 ? streak + 1 : 1; }
    else     { losses++; streak = streak < 0 ? streak - 1 : -1; }
    bestStreak  = Math.max(bestStreak,  streak);
    worstStreak = Math.min(worstStreak, streak);
    if (i <= 40 || i % step === 0) curve.push({ i, v: +bank.toFixed(2) });
    if (log.length < 80) log.push({ i, won, bet: +bet.toFixed(2), pnl: +pnl.toFixed(2), bank: +bank.toFixed(2) });
  }

  const total = wins + losses;
  return {
    finalBank: bank, preset, platform,
    roi: +((bank - startM) / startM * 100).toFixed(1),
    wins, losses, total,
    winRatePct: total ? +(wins / total * 100).toFixed(1) : 0,
    bestStreak, worstStreak,
    hitTarget: bank >= targetM,
    hitFloor:  bank <= floorM,
    curve, log,
  };
}

function SimulateTab() {
  const [startM,   setStartM]   = useState("100");
  const [targetM,  setTargetM]  = useState("500");
  const [floorM,   setFloorM]   = useState("40");
  const [strategy, setStrategy] = useState("balanced");
  const [platform, setPlatform] = useState("dice");
  const [maxBets,  setMaxBets]  = useState("2000");
  const [result,   setResult]   = useState(null);
  const [runs,     setRuns]     = useState([]);
  const [busy,     setBusy]     = useState(false);
  const [progress, setProgress] = useState(0); // bets completed so far

  const run = () => {
    setBusy(true);
    setProgress(0);
    setResult(null);
    const max = parseInt(maxBets) || 2000;
    // Run in chunks so we can update the progress counter without blocking UI
    const CHUNK = Math.max(50, Math.floor(max / 40));
    const cfg = {
      startM:  parseFloat(startM)  || 100,
      targetM: parseFloat(targetM) || 500,
      floorM:  parseFloat(floorM)  || 40,
      strategy, platform, maxBets: max,
    };
    // Full sim still runs in one shot for accuracy, but we animate the counter
    setTimeout(() => {
      const r = runSim(cfg);
      // Animate bet counter from 0 → r.total
      let displayed = 0;
      const step = Math.max(1, Math.floor(r.total / 30));
      const tick = setInterval(() => {
        displayed = Math.min(displayed + step, r.total);
        setProgress(displayed);
        if (displayed >= r.total) {
          clearInterval(tick);
          setResult(r);
          setRuns(prev => [{ ...r, id: Date.now() }, ...prev].slice(0, 6));
          setBusy(false);
        }
      }, 30);
    }, 0);
  };

  const outcomeColor = result
    ? (result.hitTarget ? T.green : result.hitFloor ? T.red : T.blue)
    : T.blue;

  const numInput = (label, value, set, prefix) => (
    <div key={label}>
      <Label style={{ marginBottom: 4 }}>{label}</Label>
      <div style={{ display: "flex", alignItems: "center", gap: 4,
        borderBottom: `1px solid ${T.line}`, paddingBottom: 5 }}>
        {prefix && <Msym style={{ fontSize: 12, color: T.muted }} />}
        <input value={value} onChange={e => set(e.target.value)} type="number" min="0"
          style={{ background: "none", border: "none", color: T.fg, fontSize: 14,
            fontWeight: 600, width: "100%", outline: "none", fontFamily: "inherit" }} />
      </div>
    </div>
  );

  return (
    <div style={{ maxWidth: 900, animation: "fadein .25s ease" }}>
      <SectionHeader
        title={<span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
          <Msym style={{ fontSize: 18 }} /> Strategy Tester
        </span>}
        sub="Simulate bets with fake M money — no real funds at risk"
        style={{ marginBottom: SP.lg }}
      />

      {/* ── Config inputs ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
        gap: SP.sm, marginBottom: SP.lg }}>
        {numInput("Starting M", startM, setStartM, true)}
        {numInput("Target M",   targetM, setTargetM, true)}
        {numInput("Floor M",    floorM,  setFloorM,  true)}
        {numInput("Max Bets",   maxBets, setMaxBets,  false)}
      </div>

      {/* ── Strategy selector ── */}
      <div style={{ marginBottom: SP.md }}>
        <Label style={{ marginBottom: 8 }}>Strategy</Label>
        <div style={{ display: "flex", gap: SP.xl, flexWrap: "wrap" }}>
          {SIM_PRESETS.map(p => (
            <button key={p.id} onClick={() => setStrategy(p.id)} className="dg-nav-btn"
              style={{ background: "none", border: "none", padding: "4px 0 5px",
                borderBottom: strategy === p.id ? `2px solid ${p.color}` : "2px solid transparent",
                color: strategy === p.id ? p.color : T.muted,
                fontSize: 10, fontWeight: 700, letterSpacing: "0.1em", cursor: "pointer",
                textAlign: "left" }}>
              {p.label}
              <span style={{ display: "block", fontSize: 9, fontWeight: 400,
                color: T.muted, marginTop: 2, letterSpacing: "0.04em" }}>
                {p.desc}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* ── Platform selector ── */}
      <div style={{ marginBottom: SP.xl }}>
        <Label style={{ marginBottom: 8 }}>Platform</Label>
        <div style={{ display: "flex", gap: SP.lg, flexWrap: "wrap" }}>
          {SIM_PLATFORMS.map(p => (
            <button key={p.id} onClick={() => setPlatform(p.id)} className="dg-nav-btn"
              style={{ background: "none", border: "none", padding: "3px 0 4px",
                borderBottom: platform === p.id ? `2px solid ${T.fg}` : "2px solid transparent",
                color: platform === p.id ? T.fg : T.muted,
                fontSize: 10, fontWeight: platform === p.id ? 700 : 500,
                letterSpacing: "0.08em", cursor: "pointer" }}>
              {p.label}
              <span style={{ fontSize: 9, color: T.muted, marginLeft: 6, fontWeight: 400 }}>
                {p.mult}×
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* ── Run button + live counter ── */}
      <div style={{ marginBottom: SP.xl }}>
        <button onClick={run} disabled={busy} className="dg-btn"
          style={{ background: busy ? `${T.fg}18` : T.fg, color: busy ? T.muted : T.bg,
            border: "none", borderRadius: 6, padding: "10px 30px",
            fontSize: 11, fontWeight: 800, letterSpacing: "0.14em",
            cursor: busy ? "not-allowed" : "pointer",
            display: "inline-flex", alignItems: "center", gap: 8 }}>
          {busy && <Spinner size={12} />}
          {busy ? `SIMULATING…` : "RUN SIMULATION"}
        </button>
        {busy && (
          <div style={{ marginTop: SP.md }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: SP.sm, marginBottom: 6 }}>
              <span style={{ fontSize: 28, fontWeight: 800, letterSpacing: "-0.04em", fontVariantNumeric: "tabular-nums" }}>
                {progress.toLocaleString()}
              </span>
              <span style={{ fontSize: 10, color: T.muted, letterSpacing: "0.08em" }}>
                / {(parseInt(maxBets)||2000).toLocaleString()} BETS
              </span>
            </div>
            <div style={{ height: 2, background: T.line, borderRadius: 2, overflow: "hidden", width: 260 }}>
              <div style={{
                height: "100%", borderRadius: 2, background: T.blue,
                width: `${Math.min(100, (progress / (parseInt(maxBets)||2000)) * 100)}%`,
                transition: "width 0.03s linear"
              }} />
            </div>
          </div>
        )}
      </div>

      {/* ── Results ── */}
      {result && (
        <>
          {/* Outcome banner */}
          <div style={{ padding: "10px 14px", borderRadius: 7, marginBottom: SP.lg,
            background: `${outcomeColor}10`, border: `1px solid ${outcomeColor}28`,
            display: "flex", alignItems: "center", gap: SP.lg, flexWrap: "wrap" }}>
            <span style={{ fontSize: 11, fontWeight: 800, color: outcomeColor, letterSpacing: "0.1em" }}>
              {result.hitTarget ? "TARGET HIT" : result.hitFloor ? "FLOOR HIT — BUSTED" : "MAX BETS REACHED"}
            </span>
            <span style={{ fontSize: 10, color: T.muted }}>
              {result.total.toLocaleString()} bets · {result.strategy} · {result.platform}
            </span>
          </div>

          {/* Key metrics */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
            gap: SP.sm, marginBottom: SP.lg }}>
            {[
              { label: "Final Balance",
                value: <span style={{ display:"inline-flex", alignItems:"baseline", gap:2 }}>
                  <Msym style={{ fontSize:16 }} />{mfmt(result.finalBank)}
                </span>,
                color: result.roi >= 0 ? T.green : T.red, big: true },
              { label: "ROI",
                value: `${result.roi >= 0 ? "+" : ""}${result.roi}%`,
                color: result.roi >= 0 ? T.green : T.red, big: true },
              { label: "Win Rate",    value: `${result.winRatePct}%`,  color: T.fg },
              { label: "Bets Placed", value: result.total.toLocaleString(), color: T.fg },
              { label: "Best Streak", value: `+${result.bestStreak}`,  color: T.green },
              { label: "Worst Streak",value: `${result.worstStreak}`,  color: T.red },
            ].map(({ label, value, big, color }) => (
              <div key={label} style={{ borderTop: `2px solid ${color || T.line}`, paddingTop: SP.xs }}>
                <Label style={{ marginBottom: 3 }}>{label}</Label>
                <div style={{ fontSize: big ? 22 : 16, fontWeight: 700,
                  color: color || T.fg, lineHeight: 1.2 }}>
                  {value}
                </div>
              </div>
            ))}
          </div>

          {/* Equity curve */}
          <SectionHeader title="Equity Curve" style={{ marginBottom: SP.sm }} />
          <div style={{ height: 190, marginBottom: SP.lg }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={result.curve} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="simGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor={outcomeColor} stopOpacity={0.22} />
                    <stop offset="95%" stopColor={outcomeColor} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke={T.line} strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="i" tick={{ fontSize: 9, fill: T.muted }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fontSize: 9, fill: T.muted }} tickLine={false} axisLine={false} width={50}
                  tickFormatter={v => v >= 1000 ? `${(v/1000).toFixed(1)}k` : v} />
                <Tooltip
                  contentStyle={{ background: T.card, border: `1px solid ${T.line}`, borderRadius: 6, fontSize: 10 }}
                  formatter={v => [`${v.toFixed(2)}`, "Balance"]}
                  labelFormatter={l => `Bet #${l}`} />
                <ReferenceLine y={parseFloat(floorM)  || 40}  stroke={T.red}   strokeDasharray="4 3" strokeWidth={1} />
                <ReferenceLine y={parseFloat(targetM) || 500} stroke={T.green} strokeDasharray="4 3" strokeWidth={1} />
                <Area type="monotone" dataKey="v" stroke={outcomeColor} strokeWidth={1.5}
                  fill="url(#simGrad)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {/* Bet log */}
          <SectionHeader title="Bet Log" sub={`First ${result.log.length} of ${result.total}`}
            style={{ marginBottom: SP.sm }} />
          <div style={{ border: `1px solid ${T.line}`, borderRadius: 7, overflow: "hidden",
            marginBottom: SP.xl }}>
            <div style={{ display: "grid", gridTemplateColumns: "44px 52px 1fr 1fr 1fr",
              padding: "6px 12px", background: T.card, fontSize: 9, color: T.muted,
              fontWeight: 700, letterSpacing: "0.08em", borderBottom: `1px solid ${T.line}` }}>
              <span>#</span><span>RESULT</span><span>BET</span><span>P&L</span><span>BALANCE</span>
            </div>
            <div style={{ maxHeight: 240, overflowY: "auto" }}>
              {result.log.map(t => (
                <div key={t.i} className="dg-row-hover"
                  style={{ display: "grid", gridTemplateColumns: "44px 52px 1fr 1fr 1fr",
                    padding: "5px 12px", fontSize: 10, borderBottom: `1px solid ${T.line}`,
                    color: T.fg, alignItems: "center" }}>
                  <span style={{ color: T.muted, fontSize: 9 }}>{t.i}</span>
                  <span style={{ fontWeight: 700, color: t.won ? T.green : T.red, letterSpacing:"0.06em" }}>
                    {t.won ? "WIN" : "LOSS"}
                  </span>
                  <span><Msym style={{ fontSize: 9 }} />{t.bet.toFixed(2)}</span>
                  <span style={{ color: t.pnl >= 0 ? T.green : T.red, fontWeight: 600 }}>
                    {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(2)}
                  </span>
                  <span style={{ fontWeight: 600 }}>
                    <Msym style={{ fontSize: 9 }} />{t.bank.toFixed(2)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {/* ── Run history ── */}
      {runs.length > 1 && (
        <>
          <SectionHeader title="Run History" sub="Compare last simulations"
            style={{ marginBottom: SP.sm }} />
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {runs.map((r, i) => {
              const p = SIM_PRESETS.find(x => x.id === r.strategy);
              const outCol = r.hitTarget ? T.green : r.hitFloor ? T.red : T.muted;
              return (
                <div key={r.id} className="dg-row-hover"
                  style={{ display: "grid",
                    gridTemplateColumns: "22px 96px 70px 70px 68px 80px 70px",
                    padding: "7px 12px", border: `1px solid ${T.line}`, borderRadius: 6,
                    fontSize: 10, color: T.fg, alignItems: "center",
                    background: i === 0 ? `${T.fg}06` : "transparent" }}>
                  <span style={{ color: T.muted, fontSize: 9 }}>#{runs.length - i}</span>
                  <span style={{ color: p?.color, fontWeight: 700, letterSpacing:"0.07em" }}>
                    {r.strategy.toUpperCase()}
                  </span>
                  <span style={{ color: T.muted }}>{r.platform}</span>
                  <span style={{ color: r.roi >= 0 ? T.green : T.red, fontWeight: 700 }}>
                    {r.roi >= 0 ? "+" : ""}{r.roi}%
                  </span>
                  <span style={{ color: T.muted }}>{r.winRatePct}% WR</span>
                  <span style={{ fontWeight: 600 }}>
                    <Msym style={{ fontSize: 9 }} />{mfmt(r.finalBank)}
                  </span>
                  <span style={{ color: outCol, fontWeight: 700, fontSize: 9, letterSpacing:"0.08em" }}>
                    {r.hitTarget ? "TARGET" : r.hitFloor ? "BUSTED" : "ENDED"}
                  </span>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  ROOT DASHBOARD
// ─────────────────────────────────────────────────────────────────────────────
const TABS_DEF = [
  ["Overview",  "Overview"],
  ["Bots",      "Bots"],
  ["Analytics", "Analytics"],
  ["Wallet",    "Wallet"],
  ["Simulate",  "Simulate"],
  ["Settings",  "Settings"],
  ["Guide",     "Guide"],
];

function Dashboard({ token, onLogout, theme, onToggleTheme }) {
  const { isMobile, isTablet } = useViewport();
  const [tab,            setTab]            = useState("Overview");
  const [bots,           setBots]           = useState([]);
  const [equity,         setEquity]         = useState([]);
  const [info,           setInfo]           = useState({});
  const [historySummary, setHistorySummary] = useState({});
  const [strategy,       setStrategy]       = useState("balanced");
  const [uptime,         setUptime]         = useState("--");
  const [cycle,          setCycle]          = useState(0);
  const [notifications,  setNotifications]  = useState([]);
  const [running,        setRunning]        = useState(true);
  const [toggleBusy,     setToggleBusy]     = useState(false);
  const [wsConnected,    setWsConnected]    = useState(false);
  const [tz,             setTzState]        = useState(getTz);
  const [botFilter,      setBotFilter]      = useState("all");
  const [expandedBots,   setExpandedBots]   = useState({});
  const [playOpen,       setPlayOpen]       = useState(false);
  const [selectedBots,   setSelectedBots]   = useState(() => {
    try { return JSON.parse(localStorage.getItem("degens_selected_bots") || "null") || null; }
    catch { return null; }
  });
  const playRef  = useRef(null);
  const pollRef  = useRef(null);
  const wsRef    = useRef(null);
  const prevMsId = useRef(null);
  const clock    = useClock(tz);
  const allBotIds        = useMemo(() => bots.map(b => b.id), [bots]);
  const effectiveSelected = useMemo(() => selectedBots || allBotIds, [selectedBots, allBotIds]);

  const setTz = useCallback(newTz => { storeTz(newTz); setTzState(newTz); }, []);

  // Close play dropdown on outside click
  useEffect(() => {
    if (!playOpen) return;
    const handler = e => { if (playRef.current && !playRef.current.contains(e.target)) setPlayOpen(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [playOpen]);

  const toggleBotSelect = useCallback(id => {
    setSelectedBots(prev => {
      const cur  = prev || allBotIds;
      const next = cur.includes(id) ? cur.filter(x => x !== id) : [...cur, id];
      const saved = next.length === allBotIds.length ? null : next;
      localStorage.setItem("degens_selected_bots", JSON.stringify(saved));
      return saved;
    });
  }, [allBotIds]);

  const selectAll  = useCallback(() => { setSelectedBots(null); localStorage.removeItem("degens_selected_bots"); }, []);
  const selectNone = useCallback(() => { setSelectedBots([]); localStorage.setItem("degens_selected_bots", "[]"); }, []);

  const apiFetch = useCallback((path) => {
    if (token === DEMO_TOKEN) return demoFetch(path);
    return fetch(path, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => { if (r.status === 401) { clearToken(); onLogout(); } return r.json(); })
      .catch(() => null);
  }, [token, onLogout]);

  const poll = useCallback(async () => {
    const [statusData, equityData, infoData, histData] = await Promise.all([
      apiFetch(`${API}/status`),
      apiFetch(`${API}/equity`),
      apiFetch(`${API}/info`),
      apiFetch(`${API}/history/summary`),
    ]);

    if (statusData?.bots) {
      const mapped = statusData.bots.map(b => ({
        ...b,
        color:           BC[b.id] || "#888",
        equityKey:       `bot${b.id.match(/\d/)?.[0] || "1"}`,
        winRate:         b.winRate ?? b.win_rate_pct ?? 0,
        roi_pct:         b.roi_pct      || 0,
        bets:            b.bets         || 0,
        wins:            b.wins         || 0,
        losses:          b.losses       || 0,
        bankroll:        b.bankroll     || b.start_amount || 100,
        start_amount:    b.start_amount    || 100,
        target_amount:   b.target_amount   || (b.start_amount || 100) * 5,
        floor_amount:    b.floor_amount    || (b.start_amount || 100) * 0.4,
        withdraw_at:     b.withdraw_at     || (b.start_amount || 100) * 1.2,
        caution_at:      b.caution_at      || (b.start_amount || 100) * 0.95,
        recovery_at:     b.recovery_at     || (b.start_amount || 100) * 0.9,
        total_withdrawn: b.total_withdrawn ?? b.locked ?? 0,
        vault:           b.vault           || 0,
        progress:        b.progress        || b.bankroll || 100,
        progress_pct:    b.progress_pct    || 0,
        phase:           b.phase           || "active",
        halted:          Boolean(b.halted),
        danger:          b.danger          || false,
        milestoneHit:    b.milestoneHit    || false,
        streak:          b.streak          || 0,
        platform:        b.platform        || "stake",
        name:            b.name            || b.id,
        bet_scale:       b.bet_scale       || 1.0,
        strategy_mode:   b.strategy_mode   || "balanced",
        rolling:         b.rolling         || { w25: {}, w100: {} },
        autoDownscales:  b.autoDownscales  || 0,
        lastDownscaleReason: b.lastDownscaleReason || "",
        autoManaged:     b.autoManaged     || false,
        execution_mode:  b.execution_mode  || (b.platform === "poly" ? "paper" : "simulated"),
        data_mode:       b.data_mode       || (b.platform === "poly" ? "real_market_data" : "simulated"),
        scan_opportunity_count: b.scan_opportunity_count || 0,
        scan_best_edge:  b.scan_best_edge,
        scan_best_question: b.scan_best_question || "",
        scan_last_ts:    b.scan_last_ts    || 0,
        open_positions:  b.open_positions  || 0,
        runtime_error:   b.runtime_error   || "",
        opportunities:   b.opportunities   || [],
        btc_price:       b.btc_price       ?? null,
      }));
      setBots(mapped);
      setRunning(Boolean(statusData.running));

      const ms = mapped.find(b => b.milestoneHit);
      if (ms && ms.id !== prevMsId.current) {
        prevMsId.current = ms.id;
        setNotifications(prev => [...prev, {
          type: "milestone",
          title: `${ms.id} milestone`,
          body: `Bankroll milestone reached — $${(ms.bankroll || 0).toFixed(2)}`,
          time: new Date().toLocaleTimeString(),
        }]);
      }
    }

    if (equityData?.equity || equityData?.history) setEquity(equityData.equity || equityData.history);
    if (infoData) {
      setInfo(infoData);
      setUptime(infoData.uptimeStr || "--");
      setCycle(infoData.cycle ?? statusData?.tick ?? 0);
    } else {
      setUptime(statusData?.uptime || "--");
      setCycle(statusData?.tick || 0);
    }
    if (histData?.summary)   setHistorySummary(histData.summary);
    if (infoData?.strategy_mode) setStrategy(infoData.strategy_mode);
  }, [apiFetch]);

  const playSelected = useCallback(async () => {
    setPlayOpen(false);
    setToggleBusy(true);
    try {
      // Resume selected, pause unselected
      await Promise.all(allBotIds.map(id =>
        fetch(`${API}/bots/${id}/${effectiveSelected.includes(id) ? "resume" : "pause"}`, {
          method: "POST", headers: { Authorization: `Bearer ${token}` },
        })
      ));
      // Also hit global start to make sure the system is running
      await fetch(`${API}/start`, { method: "POST", headers: { Authorization: `Bearer ${token}` } });
      setRunning(true);
      await poll();
    } finally { setToggleBusy(false); }
  }, [allBotIds, effectiveSelected, token, poll]);

  const pauseSystem = useCallback(async () => {
    setToggleBusy(true);
    try {
      await fetch(`${API}/stop`, { method: "POST", headers: { Authorization: `Bearer ${token}` } });
      setRunning(false);
      await poll();
    } finally { setToggleBusy(false); }
  }, [token, poll]);

  // WebSocket connection — push updates, fall back to polling on disconnect
  useEffect(() => {
    if (token === DEMO_TOKEN) return; // no WS in demo mode
    let reconnectTimer = null;

    const connect = () => {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${window.location.host}/api/ws?token=${token}`);
      wsRef.current = ws;

      ws.onopen = () => { setWsConnected(true); };

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.type === "equity" && msg.snap) {
            setEquity(prev => {
              const next = [...prev, msg.snap];
              return next.length > 120 ? next.slice(-120) : next;
            });
          }
          if (msg.type === "bet" && msg.bot_id) {
            setBots(prev => prev.map(b =>
              b.id === msg.bot_id
                ? { ...b, bankroll: msg.bankroll ?? b.bankroll, phase: msg.phase ?? b.phase }
                : b
            ));
          }
          if (msg.type === "phase_change") {
            setBots(prev => prev.map(b =>
              b.id === msg.bot_id ? { ...b, phase: msg.phase } : b
            ));
          }
          if (msg.type === "auto_downscale") {
            setBots(prev => prev.map(b =>
              b.id === msg.bot_id ? { ...b, bet_scale: msg.to_scale } : b
            ));
          }
        } catch { /* ignore malformed WS messages */ }
      };

      ws.onclose = () => {
        setWsConnected(false);
        reconnectTimer = setTimeout(connect, 2000);
      };

      ws.onerror = () => ws.close();
    };

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      if (wsRef.current) wsRef.current.close();
    };
  }, [token]);

  // Polling — always on, provides full state refresh even when WS is live
  useEffect(() => {
    poll();
    pollRef.current = setInterval(poll, POLL_MS);
    return () => clearInterval(pollRef.current);
  }, [poll]);

  const quickDeposit = useCallback(async amt => {
    await fetch(`${API}/wallet/deposit`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ amount: amt }),
    });
  }, [token]);

  const toggleRunning = useCallback(async () => {
    // kept for legacy compatibility — not used in UI anymore
    setToggleBusy(true);
    try {
      await fetch(`${API}/${running ? "stop" : "start"}`, {
        method: "POST", headers: { Authorization: `Bearer ${token}` },
      });
      setRunning(prev => !prev);
      await poll();
    } finally {
      setToggleBusy(false);
    }
  }, [poll, running, token]);

  const active        = bots.filter(b => !b.halted).length;
  const danger        = bots.filter(b => b.danger).length;
  const coolingBots   = bots.filter(b => b.coolingDown);
  // System-wide phase: the most conservative active phase across all bots
  const _PHASE_RANK   = { floor:0, ultra_safe:1, safe:2, careful:3, normal:4, aggressive:5, turbo:6, milestone:4 };
  const weakestPhase  = bots.length
    ? bots.reduce((w, b) => (_PHASE_RANK[b.phase] ?? 4) < (_PHASE_RANK[w.phase] ?? 4) ? b : w, bots[0])?.phase
    : "normal";
  const weakestColor  = PHASE_COLORS[weakestPhase] || T.blue;

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <style>{getGlobalCss()}</style>

      {/* Demo mode banner */}
      {token === DEMO_TOKEN && (
        <div style={{
          padding: "7px 14px", background: "rgba(94,161,255,0.08)",
          borderBottom: `1px solid rgba(94,161,255,0.18)`,
          fontSize: 10, color: "#5ea1ff", fontWeight: 700,
          display: "flex", alignItems: "center", gap: 10,
          letterSpacing: "0.10em",
        }}>
          DEMO MODE — mock data only
          <span style={{ fontWeight: 400, color: T.muted, letterSpacing: "0.04em" }}>
            Connect your server to see live bot data
          </span>
        </div>
      )}

      {/* Circuit breaker banner */}
      {coolingBots.length > 0 && (
        <div style={{
          padding: "8px 14px", background: "rgba(214,175,65,0.10)",
          borderBottom: `1px solid rgba(214,175,65,0.25)`,
          fontSize: 10, color: "#d6af41", fontWeight: 700,
          display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap",
        }}>
          <span style={{ animation: "pulse 1.5s infinite" }}>CB</span>
          {coolingBots.map(b => (
            <span key={b.id} style={{ color: T.orange }}>
              {b.id}: paused {Math.ceil(b.cooldownSec || 0)}s — {b.cbReason || "circuit breaker"}
            </span>
          ))}
        </div>
      )}

      <header style={{ padding: "14px 18px 0", flexShrink: 0, borderBottom: `1px solid ${T.line}` }}>
        {/* ── Top row: brand · nav · clock+actions ── */}
        <div style={{ display: "grid",
          gridTemplateColumns: isMobile ? "1fr" : "200px 1fr 260px",
          alignItems: "center", gap: isMobile ? 10 : 0, marginBottom: 12 }}>

          {/* LEFT — brand + system status */}
          <div>
            <button onClick={onToggleTheme}
              style={{ background: "none", border: "none", padding: 0, color: T.fg,
                fontSize: 32, fontWeight: 800, letterSpacing: "-0.08em", lineHeight: 1,
                display: "flex", alignItems: "baseline", gap: 6 }}>
              DeG£N$
              <span style={{ fontSize: 9, color: T.muted, letterSpacing: "0.18em", fontWeight: 500 }}>
                {theme === "dark" ? "LIGHT" : "DARK"}
              </span>
            </button>
            {/* System status line — compact, scannable */}
            <div style={{ marginTop: 5, display: "flex", gap: 10, alignItems: "center",
              fontSize: 9, color: T.muted, letterSpacing: "0.06em", flexWrap: "wrap" }}>
              <span style={{ display:"flex", alignItems:"center", gap: 4 }}>
                <Dot ok={active > 0} size={5} />
                <span>{active}/{bots.length}</span>
              </span>
              <span>{uptime}</span>
              {wsConnected && (
                <span style={{ color: T.green, fontWeight: 700, letterSpacing: "0.1em" }}>WS</span>
              )}
              {bots.length > 0 && (
                <span style={{
                  fontSize: 8, fontWeight: 800, padding: "1px 5px", borderRadius: 3,
                  background: `${weakestColor}1a`, color: weakestColor, letterSpacing: "0.06em",
                  animation: weakestPhase === "turbo" ? "turbo-pulse 1.2s infinite" : "none",
                }}>
                  {(weakestPhase || "normal").toUpperCase()}
                </span>
              )}
              {danger > 0 && (
                <span style={{ color: T.red, fontWeight: 800, animation: "pulse 1.5s infinite",
                  fontSize: 9 }}>
                  {danger}⚠
                </span>
              )}
            </div>
          </div>

          {/* CENTER — navigation */}
          <nav style={{ display: "flex", gap: isMobile ? 10 : 20,
            justifyContent: isMobile ? "flex-start" : "center",
            alignItems: "center", flexWrap: "wrap" }}>
            {TABS_DEF.map(([id]) => (
              <button key={id} onClick={() => setTab(id)} className="dg-nav-btn"
                style={{ background: "none", border: "none", padding: "0 0 2px",
                  color: tab === id ? T.fg : T.muted,
                  fontSize: 11, fontWeight: tab === id ? 800 : 500,
                  cursor: "pointer", letterSpacing: "0.06em",
                  borderBottom: tab === id ? `1.5px solid ${T.fg}` : "1.5px solid transparent",
                  transition: "color .15s, border-color .15s" }}>
                {id}
              </button>
            ))}
          </nav>

          {/* RIGHT — clock + goal + actions */}
          <div style={{ display: "flex", flexDirection: "column",
            alignItems: isMobile ? "flex-start" : "flex-end", gap: SP.sm }}>
            {/* Clock */}
            <div style={{ fontFamily: "ui-monospace, monospace", lineHeight: 1.15,
              textAlign: isMobile ? "left" : "right" }}>
              <div style={{ fontSize: 20, fontWeight: 800, color: T.fg, letterSpacing: "0.04em" }}>
                {clock.time}
              </div>
              <div style={{ fontSize: 9, color: T.muted, letterSpacing: "0.1em" }}>
                {clock.date} · {clock.city}
              </div>
            </div>
            {/* Action row */}
            <div style={{ display: "flex", gap: SP.sm, alignItems: "center", flexWrap: "wrap",
              justifyContent: isMobile ? "flex-start" : "flex-end" }}>
              <NotificationBell notifications={notifications} onClear={() => setNotifications([])} />
              {(info?.goalPct || 0) > 0 && (
                <span style={{ fontSize: 9, fontWeight: 700, color: T.green,
                  letterSpacing: "0.08em" }}>
                  {(+(info?.goalPct || 0)).toFixed(1)}% GOAL
                </span>
              )}

              {/* PLAY button with dropdown */}
              <div ref={playRef} style={{ position: "relative" }}>
                <button onClick={() => setPlayOpen(p => !p)} disabled={toggleBusy}
                  className="dg-btn"
                  style={{ display: "inline-flex", alignItems: "center", gap: 5,
                    background: "rgba(89,212,122,0.08)", border: "1px solid rgba(89,212,122,0.32)",
                    color: toggleBusy ? T.muted : T.green, padding: "5px 11px",
                    borderRadius: 6, fontSize: 9, fontWeight: 800, letterSpacing: "0.12em",
                    cursor: toggleBusy ? "not-allowed" : "pointer" }}>
                  <svg width="8" height="8" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                  PLAY
                  <span style={{ fontSize: 8, opacity: 0.65 }}>
                    {effectiveSelected.length === allBotIds.length || allBotIds.length === 0
                      ? "ALL" : `${effectiveSelected.length}`}▾
                  </span>
                </button>

                {playOpen && (
                  <div style={{ position: "absolute", right: 0, top: "calc(100% + 6px)", zIndex: 200,
                    background: T.bg === "#000000" ? "#0d0d0d" : "#f5f5f5",
                    border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 0",
                    minWidth: 210, boxShadow: "0 8px 24px rgba(0,0,0,0.55)" }}>
                    <div style={{ display: "flex", gap: 8, padding: "0 12px 7px",
                      borderBottom: `1px solid ${T.line}`, marginBottom: 4 }}>
                      <button onClick={selectAll}
                        style={{ fontSize: 9, color: T.blue, background: "none", border: "none",
                          cursor: "pointer", fontWeight: 700, letterSpacing: "0.08em" }}>ALL</button>
                      <button onClick={selectNone}
                        style={{ fontSize: 9, color: T.muted, background: "none", border: "none",
                          cursor: "pointer", fontWeight: 700, letterSpacing: "0.08em" }}>NONE</button>
                    </div>
                    {(bots.length ? bots : [
                      {id:"bot1_dice",platform:"stake"},{id:"bot2_limbo",platform:"stake"},
                      {id:"bot3_mines",platform:"stake"},{id:"bot4_poly",platform:"poly"},
                      {id:"bot5_poly",platform:"poly"},{id:"bot6_poly",platform:"poly"},
                      {id:"bot7_momentum",platform:"poly"},{id:"bot8_arb",platform:"poly"},
                      {id:"bot9_sniper",platform:"poly"},{id:"bot10_volume",platform:"poly"},
                    ]).map(b => {
                      const on = effectiveSelected.includes(b.id);
                      const col = getBotColor(b.id);
                      return (
                        <div key={b.id} onClick={() => toggleBotSelect(b.id)} className="dg-row-hover"
                          style={{ display: "flex", alignItems: "center", gap: 9,
                            padding: "6px 12px", cursor: "pointer",
                            background: on ? `${col}0d` : "transparent",
                            transition: "background 0.12s" }}>
                          <div style={{ width: 10, height: 10, borderRadius: 2,
                            border: `1.5px solid ${on ? col : T.muted}`,
                            background: on ? col : "transparent",
                            flexShrink: 0, transition: "all 0.12s" }} />
                          <span style={{ fontSize: 10, color: on ? col : T.muted, fontWeight: 600,
                            flex: 1 }}>{b.id}</span>
                          <span style={{ fontSize: 8, color: T.muted, letterSpacing: "0.06em" }}>
                            {(b.platform || "").toUpperCase()}
                          </span>
                        </div>
                      );
                    })}
                    <div style={{ padding: "7px 10px 0", borderTop: `1px solid ${T.line}`, marginTop: 4 }}>
                      <button onClick={playSelected} disabled={toggleBusy || effectiveSelected.length === 0}
                        style={{ width: "100%", padding: "7px 0", borderRadius: 6,
                          background: effectiveSelected.length === 0 ? "transparent" : "rgba(89,212,122,0.10)",
                          border: `1px solid ${effectiveSelected.length === 0 ? T.line : "rgba(89,212,122,0.38)"}`,
                          color: effectiveSelected.length === 0 ? T.muted : T.green,
                          fontSize: 9, fontWeight: 800, letterSpacing: "0.1em", cursor: "pointer" }}>
                        {toggleBusy ? "STARTING…" : `▶ PLAY ${effectiveSelected.length === allBotIds.length ? "ALL" : effectiveSelected.length} BOT${effectiveSelected.length !== 1 ? "S" : ""}`}
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* PAUSE SYSTEM */}
              <button onClick={pauseSystem} disabled={toggleBusy} className="dg-btn"
                style={{ display: "inline-flex", alignItems: "center", gap: 5,
                  background: "rgba(239,95,87,0.07)", border: "1px solid rgba(239,95,87,0.28)",
                  color: toggleBusy ? T.muted : T.red, padding: "5px 11px",
                  borderRadius: 6, fontSize: 9, fontWeight: 800, letterSpacing: "0.12em",
                  cursor: toggleBusy ? "not-allowed" : "pointer" }}>
                <svg width="8" height="8" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12"/></svg>
                PAUSE
              </button>
            </div>
          </div>
        </div>
      </header>

      <Toolbar token={token} strategy={strategy} onStrategy={setStrategy} onDeposit={quickDeposit} />

      <main style={{ flex: 1, padding: "18px 14px 28px", overflowY: "auto" }}>
        {tab === "Overview"  && <OverviewTab bots={bots} equity={equity} info={info} historySummary={historySummary} token={token} />}
        {tab === "Bots"      && <BotsTab token={token} bots={bots} equity={equity} botFilter={botFilter} setBotFilter={setBotFilter} expandedBots={expandedBots} setExpandedBots={setExpandedBots} />}
        {tab === "Analytics" && <AnalyticsTab token={token} bots={bots} equity={equity} />}
        {tab === "Wallet"    && <WalletTab token={token} bots={bots} />}
        {tab === "Simulate"  && <SimulateTab />}
        {tab === "Settings"  && <SettingsTab token={token} tz={tz} onTzChange={setTz} />}
        {tab === "Guide"     && <GuideTab />}
      </main>

      <footer style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr auto",
        color: T.muted, fontSize: 10, padding: "10px 14px",
        borderTop: `1px solid ${T.line}`, gap: isMobile ? 6 : 0 }}>
        <div>DeG£N$ - Stake + Polymarket - auto-withdraw at +20%</div>
        <div>{new Date().toLocaleDateString("en-CA")}</div>
      </footer>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  ERROR BOUNDARY
// ─────────────────────────────────────────────────────────────────────────────
class ErrorBoundary extends Component {
  state = { error: null };
  static getDerivedStateFromError(e) { return { error: e }; }
  render() {
    if (this.state.error) {
      clearToken();
      return (
        <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 16, background: "#000", color: "#f5f5f5", fontFamily: "monospace" }}>
          <div style={{ fontSize: 28, fontWeight: 800, letterSpacing: "-0.06em" }}>DeG£N$</div>
          <div style={{ fontSize: 11, color: "#ef5f57" }}>Runtime error — session cleared</div>
          <div style={{ fontSize: 10, color: "#555", maxWidth: 400, textAlign: "center" }}>{String(this.state.error)}</div>
          <button onClick={() => { this.setState({ error: null }); window.location.reload(); }}
            style={{ marginTop: 8, background: "none", border: "1px solid #333", color: "#f5f5f5", padding: "8px 16px", borderRadius: 6, cursor: "pointer", fontSize: 11 }}>
            RELOAD
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
//  ROOT
// ─────────────────────────────────────────────────────────────────────────────
export default function DegensDashboard() {
  const [theme, setTheme] = useState(getTheme);
  // Auto-clear stale token on load — if it's expired server will 401 and logout kicks in
  const [token, setToken] = useState(() => {
    const t = getToken();
    // If the URL has ?reset, wipe everything and start fresh
    if (window.location.search.includes("reset")) {
      localStorage.clear();
      window.history.replaceState({}, "", "/");
      return "";
    }
    return t;
  });
  applyTheme(theme);

  useEffect(() => {
    applyTheme(theme);
    storeTheme(theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme(current => current === "dark" ? "light" : "dark");
  }, []);

  const logout = useCallback(() => { clearToken(); setToken(""); }, []);

  if (!token) {
    return (
      <ErrorBoundary>
        <Login
          onLogin={t => { storeToken(t); setToken(t); }}
          theme={theme}
          onToggleTheme={toggleTheme}
        />
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary>
      <Dashboard
        token={token}
        theme={theme}
        onToggleTheme={toggleTheme}
        onLogout={logout}
      />
    </ErrorBoundary>
  );
}
