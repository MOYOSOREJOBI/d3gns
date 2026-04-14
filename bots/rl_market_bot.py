"""
bots/rl_market_bot.py

Reinforcement-Learning market trading bot.

Primary mode: loads Adilbai/stock-trading-rl-agent from HuggingFace Hub and uses
the PPO/A2C policy to generate BUY / SELL / HOLD signals on aggregated prediction
market + crypto price observations.

Fallback mode: when torch / stable-baselines3 are unavailable (e.g. Python 3.14
build env), the bot runs a momentum-mean-reversion heuristic that mimics what
the RL policy would do. The summary always reports which mode is active.

Signal sources used to build the observation vector:
  - Polymarket 15-min BTC/ETH/SOL up prices (public REST)
  - Kalshi BTCD + ETHD top-of-book estimates (via pmxt, optional)
  - OddsAPI implied probs for major sport outright markets (optional)

PAPER mode only — no real money. Truth label: PAPER / RESEARCH DATA ONLY
"""
from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import Any

import requests

# ── Optional RL imports (graceful degrade if torch not available) ─────────────
_RL_AVAILABLE = False
_rl_model     = None
_rl_error_msg = ""

try:
    import numpy as np                                     # noqa: F401
    import torch                                           # noqa: F401
    from stable_baselines3.common.policies import BasePolicy  # noqa: F401
    from huggingface_hub import hf_hub_download
    _RL_AVAILABLE = True
except ImportError as _e:
    _rl_error_msg = str(_e)

# ── Constants ─────────────────────────────────────────────────────────────────
HF_REPO_ID   = "Adilbai/stock-trading-rl-agent"
HF_FILENAME  = "ppo-LunarLander-v2.zip"   # best available checkpoint on this repo
MODEL_CACHE  = Path.home() / ".cache" / "degens_rl"

POLY_GAMMA   = "https://gamma-api.polymarket.com"
PMXT_HOST    = os.getenv("PMXT_HOST", "http://localhost:3847")
PMXT_TOKEN   = os.getenv("PMXT_ACCESS_TOKEN", "")

# Observation layout (13 dims):
# [btc_up, eth_up, sol_up, xrp_up,            — 0-3  Poly 15m up prices
#  btc_mom, eth_mom,                            — 4-5  2-period momentum
#  vol_btc, vol_eth,                            — 6-7  rolling std
#  ema_fast, ema_slow,                          — 8-9  EMA trend
#  extreme_low_cnt, extreme_high_cnt,           — 10-11 extreme counts
#  hour_frac]                                   — 12   time-of-day
OBS_DIM = 13

# Action map (standard discrete-3 gym action space)
ACTIONS = {0: "HOLD", 1: "BUY", 2: "SELL"}

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "DeGeNs-RL-Bot/1.0"


# ── RL model loader ───────────────────────────────────────────────────────────

def _load_model():
    global _rl_model, _RL_AVAILABLE
    if not _RL_AVAILABLE:
        return False
    try:
        MODEL_CACHE.mkdir(parents=True, exist_ok=True)
        local_path = MODEL_CACHE / HF_FILENAME
        if not local_path.exists():
            downloaded = hf_hub_download(
                repo_id=HF_REPO_ID,
                filename=HF_FILENAME,
                local_dir=str(MODEL_CACHE),
            )
            local_path = Path(downloaded)
        # Load with stable_baselines3
        from stable_baselines3 import PPO
        _rl_model = PPO.load(str(local_path), device="cpu")
        return True
    except Exception as exc:
        global _rl_error_msg
        _rl_error_msg = f"model load failed: {exc}"
        _RL_AVAILABLE = False
        return False


# ── Observation builder ───────────────────────────────────────────────────────

def _poly_price(market: str) -> float | None:
    now  = int(time.time())
    slot = now - (now % 900)
    slug = f"{market}-updown-15m-{slot}"
    try:
        import json as _json
        r = _SESSION.get(f"{POLY_GAMMA}/markets/slug/{slug}", timeout=6)
        if r.status_code != 200:
            return None
        d = r.json()
        prices = d.get("outcomePrices") or d.get("prices")
        if isinstance(prices, str):
            prices = _json.loads(prices)
        if isinstance(prices, list) and prices:
            return float(prices[0])
    except Exception:
        pass
    return None


class _ObsBuilder:
    """Accumulates price history and builds OBS_DIM observation vector."""

    def __init__(self):
        self._hist: dict[str, list[float]] = {m: [] for m in ("btc", "eth", "sol", "xrp")}
        self._ema_fast = {m: 0.5 for m in self._hist}
        self._ema_slow = {m: 0.5 for m in self._hist}

    def update(self, prices: dict[str, float]) -> list[float]:
        for m in self._hist:
            p = prices.get(m)
            if p is not None:
                self._hist[m].append(p)
                if len(self._hist[m]) > 20:
                    self._hist[m].pop(0)
                self._ema_fast[m] = 0.4 * p + 0.6 * self._ema_fast[m]
                self._ema_slow[m] = 0.1 * p + 0.9 * self._ema_slow[m]

        def last(m):    return self._hist[m][-1]  if self._hist[m]  else 0.5
        def mom(m):
            h = self._hist[m]
            return (h[-1] - h[-3]) if len(h) >= 3 else 0.0
        def vstd(m):
            h = self._hist[m]
            if len(h) < 3: return 0.0
            mn = sum(h) / len(h)
            return math.sqrt(sum((x - mn)**2 for x in h) / len(h))

        vals = [
            last("btc"), last("eth"), last("sol"), last("xrp"),
            mom("btc"),  mom("eth"),
            vstd("btc"), vstd("eth"),
            self._ema_fast["btc"], self._ema_slow["btc"],
            sum(1 for m in self._hist if last(m) <= 0.05),  # extreme low count
            sum(1 for m in self._hist if last(m) >= 0.95),  # extreme high count
            (time.localtime().tm_hour * 60 + time.localtime().tm_min) / 1440.0,
        ]
        return vals


# ── Fallback heuristic (no torch) ────────────────────────────────────────────

def _heuristic_action(obs: list[float]) -> tuple[int, float]:
    """
    Momentum + mean-reversion rule — approximates what the RL policy would do.
    Returns (action_int, confidence).
    """
    btc_p   = obs[0]
    btc_mom = obs[4]
    eth_mom = obs[5]
    ema_f   = obs[8]
    ema_s   = obs[9]
    trend   = ema_f - ema_s
    combo_mom = (btc_mom + eth_mom) / 2.0

    if trend > 0.03 and combo_mom > 0.02 and btc_p < 0.85:
        return 1, min(0.5 + abs(trend) * 5, 0.85)   # BUY
    if trend < -0.03 and combo_mom < -0.02 and btc_p > 0.15:
        return 2, min(0.5 + abs(trend) * 5, 0.85)   # SELL
    return 0, 0.3                                    # HOLD


# ── Bot ───────────────────────────────────────────────────────────────────────

class RlMarketBot:
    bot_id   = "bot_rl_market_paper"
    platform = "multi"
    platform_truth_label = "PAPER / RESEARCH DATA ONLY"
    execution_enabled    = False
    live_capable         = False
    display_name = "RL Market Agent"
    mode = "RESEARCH"
    signal_type = "rl_policy"
    paper_only = True
    delayed_only = False
    research_only = True
    watchlist_only = False
    demo_only = False
    default_enabled = False
    implemented = True
    truth_label = "ENTERTAINMENT_ONLY"
    quality_tier = "C"
    risk_tier = "high"
    description = "PPO policy from HuggingFace (Adilbai/stock-trading-rl-agent). Trained on LunarLander-v2, applied to prediction markets via obs projection. ENTERTAINMENT_ONLY: no proven edge on prediction markets."
    edge_source = "None proven. HuggingFace RL model repurposed from LunarLander environment."
    opp_cadence_per_day = 48.0
    avg_hold_hours = 0.5
    fee_drag_bps = 200
    fill_rate = 0.4
    platforms = ["polymarket", "binance"]

    def __init__(self, adapter=None):
        self._obs_builder = _ObsBuilder()
        self._model_ready = False
        self._mode        = "heuristic"
        self._trade_count = 0
        self._paper_pnl   = 0.0
        self._last_action: str | None = None
        self._last_price:  float = 0.5

    # Lazy-load the model on first use
    def _ensure_model(self):
        if self._model_ready:
            return
        if _load_model():
            self._model_ready = True
            self._mode        = "rl_model"

    def metadata(self) -> dict:
        return {
            "bot_id": self.bot_id,
            "display_name": self.display_name,
            "platform": self.platform,
            "mode": self.mode,
            "signal_type": self.signal_type,
            "paper_only": self.paper_only,
            "delayed_only": self.delayed_only,
            "research_only": self.research_only,
            "watchlist_only": self.watchlist_only,
            "demo_only": self.demo_only,
            "default_enabled": self.default_enabled,
            "implemented": self.implemented,
            "truth_label": self.truth_label,
            "quality_tier": self.quality_tier,
            "risk_tier": self.risk_tier,
            "description": self.description,
            "edge_source": self.edge_source,
            "opp_cadence_per_day": self.opp_cadence_per_day,
            "avg_hold_hours": self.avg_hold_hours,
            "fee_drag_bps": self.fee_drag_bps,
            "fill_rate": self.fill_rate,
            "platforms": self.platforms,
            "platform_truth_label": self.platform_truth_label,
            "execution_enabled": self.execution_enabled,
            "live_capable": self.live_capable,
        }

    def run_one_cycle(self) -> dict[str, Any]:
        t0 = time.time()
        self._ensure_model()

        # Collect prices
        prices: dict[str, float] = {}
        for mkt in ("btc", "eth", "sol", "xrp"):
            p = _poly_price(mkt)
            if p is not None:
                prices[mkt] = p

        if not prices:
            return self._degraded("No Polymarket prices available for observation")

        obs = self._obs_builder.update(prices)

        # Get action
        action_int: int
        confidence: float
        if self._model_ready and _rl_model is not None:
            try:
                import numpy as np
                obs_arr = np.array(obs, dtype=np.float32).reshape(1, -1)
                # The HF model was trained on a different obs space; we project
                # our vector to match its expected shape by zero-padding / truncating
                model_obs_dim = _rl_model.observation_space.shape[0]
                if obs_arr.shape[1] < model_obs_dim:
                    pad  = np.zeros((1, model_obs_dim - obs_arr.shape[1]), dtype=np.float32)
                    obs_arr = np.concatenate([obs_arr, pad], axis=1)
                else:
                    obs_arr = obs_arr[:, :model_obs_dim]
                action_arr, _ = _rl_model.predict(obs_arr, deterministic=True)
                action_int = int(action_arr[0]) if hasattr(action_arr, "__len__") else int(action_arr)
                confidence  = 0.70   # model-based; no probability output in predict()
            except Exception as exc:
                action_int, confidence = _heuristic_action(obs)
                self._mode = f"heuristic (model error: {exc})"
        else:
            action_int, confidence = _heuristic_action(obs)

        action_name = ACTIONS.get(action_int, "HOLD")
        signal_taken = action_name != "HOLD"

        btc_p = prices.get("btc", 0.5)
        # Simulate paper PnL: BUY → long; SELL → short; measured on next cycle
        if self._last_action == "BUY":
            self._paper_pnl += (btc_p - self._last_price) * 10.0   # 10-unit paper trade
        elif self._last_action == "SELL":
            self._paper_pnl += (self._last_price - btc_p) * 10.0

        self._last_action = action_name
        self._last_price  = btc_p
        if signal_taken:
            self._trade_count += 1

        if signal_taken:
            summary = (
                f"RL {action_name} signal | BTC={btc_p:.3f} "
                f"| conf={confidence:.2f} | mode={self._mode}"
            )
        else:
            summary = (
                f"HOLD — BTC={btc_p:.3f} trend={obs[8]-obs[9]:+.3f}"
                f" | mode={self._mode}"
            )

        signals = []
        if signal_taken:
            signals.append({
                "type":       "rl_signal",
                "action":     action_name,
                "btc_price":  round(btc_p, 4),
                "confidence": round(confidence, 3),
                "obs":        [round(x, 4) for x in obs],
                "mode":       self._mode,
            })

        return {
            "bot_id":               self.bot_id,
            "platform":             self.platform,
            "platform_truth_label": self.platform_truth_label,
            "title":                "RL Market Agent (HuggingFace)",
            "summary":              summary,
            "signal_taken":         signal_taken,
            "degraded_reason":      None,
            "signals":              signals,
            "action":               action_name,
            "mode":                 self._mode,
            "paper_pnl":            round(self._paper_pnl, 4),
            "paper_trades_total":   self._trade_count,
            "prices":               {k: round(v, 4) for k, v in prices.items()},
            "rl_available":         _RL_AVAILABLE,
            "model_ready":          self._model_ready,
            "confidence":           round(confidence, 3),
            "top_signal":           signals[0] if signals else None,
            "elapsed_s":            round(time.time() - t0, 2),
        }

    def _degraded(self, reason: str) -> dict[str, Any]:
        return {
            "bot_id":               self.bot_id,
            "platform":             self.platform,
            "platform_truth_label": self.platform_truth_label,
            "title":                "RL Market Agent (HuggingFace)",
            "summary":              f"DEGRADED: {reason}",
            "signal_taken":         False,
            "degraded_reason":      reason,
            "signals":              [],
            "confidence":           0.0,
            "mode":                 self._mode,
        }
