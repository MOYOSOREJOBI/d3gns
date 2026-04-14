"""
Technical analysis indicators — pure Python/stdlib, no external dependencies.
No TA-Lib, no pandas required (though they work with lists or numpy arrays).

Implements: SMA, EMA, RSI, MACD, Bollinger Bands, ATR, Stochastic,
            VWAP, momentum, rate-of-change, linear regression slope.
"""
from __future__ import annotations

import math
from typing import Any


def sma(prices: list[float], period: int) -> list[float | None]:
    """Simple Moving Average."""
    result: list[float | None] = [None] * (period - 1)
    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1: i + 1]
        result.append(sum(window) / period)
    return result


def ema(prices: list[float], period: int) -> list[float | None]:
    """Exponential Moving Average."""
    if not prices or period < 1:
        return []
    k = 2.0 / (period + 1)
    result: list[float | None] = [None] * (period - 1)
    # Seed with SMA of first `period` values
    seed = sum(prices[:period]) / period
    result.append(seed)
    prev = seed
    for price in prices[period:]:
        val = price * k + prev * (1 - k)
        result.append(val)
        prev = val
    return result


def rsi(prices: list[float], period: int = 14) -> list[float | None]:
    """Relative Strength Index (Wilder smoothing)."""
    if len(prices) < period + 1:
        return [None] * len(prices)
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    result: list[float | None] = [None] * period
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi_val(avg_g: float, avg_l: float) -> float:
        if avg_l == 0:
            return 100.0
        rs = avg_g / avg_l
        return 100.0 - (100.0 / (1.0 + rs))

    result.append(round(_rsi_val(avg_gain, avg_loss), 2))

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        result.append(round(_rsi_val(avg_gain, avg_loss), 2))

    return result


def macd(
    prices: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> dict[str, list[float | None]]:
    """MACD line, signal line, and histogram."""
    ema_fast   = ema(prices, fast)
    ema_slow   = ema(prices, slow)
    macd_line  = [
        (f - s) if f is not None and s is not None else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid_macd = [v for v in macd_line if v is not None]
    sig_raw    = ema(valid_macd, signal_period)
    # Pad signal to align with macd_line
    pad = len(macd_line) - len(sig_raw)
    signal_line: list[float | None] = [None] * pad + sig_raw  # type: ignore[operator]
    histogram = [
        (m - s) if m is not None and s is not None else None
        for m, s in zip(macd_line, signal_line)
    ]
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def bollinger_bands(
    prices: list[float], period: int = 20, std_dev: float = 2.0
) -> dict[str, list[float | None]]:
    """Bollinger Bands: upper, middle (SMA), lower."""
    middle = sma(prices, period)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for i, mid in enumerate(middle):
        if mid is None or i < period - 1:
            upper.append(None)
            lower.append(None)
        else:
            window = prices[i - period + 1: i + 1]
            mean = sum(window) / period
            variance = sum((x - mean) ** 2 for x in window) / period
            std = math.sqrt(variance)
            upper.append(round(mid + std_dev * std, 6))
            lower.append(round(mid - std_dev * std, 6))
    return {"upper": upper, "middle": middle, "lower": lower}


def atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> list[float | None]:
    """Average True Range — volatility measure."""
    if len(highs) != len(lows) or len(highs) != len(closes):
        return []
    trs: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    result: list[float | None] = [None] * period
    atr_val = sum(trs[:period]) / period
    result.append(round(atr_val, 6))
    for i in range(period, len(trs)):
        atr_val = (atr_val * (period - 1) + trs[i]) / period
        result.append(round(atr_val, 6))
    return result


def stochastic(
    highs: list[float], lows: list[float], closes: list[float],
    k_period: int = 14, d_period: int = 3,
) -> dict[str, list[float | None]]:
    """Stochastic Oscillator %K and %D."""
    k_values: list[float | None] = []
    for i in range(len(closes)):
        if i < k_period - 1:
            k_values.append(None)
            continue
        h = max(highs[i - k_period + 1: i + 1])
        l = min(lows[i - k_period + 1: i + 1])
        denom = h - l
        k = ((closes[i] - l) / denom * 100) if denom != 0 else 50.0
        k_values.append(round(k, 2))
    valid_k = [v for v in k_values if v is not None]
    d_raw = sma(valid_k, d_period)
    pad = len(k_values) - len(d_raw)
    d_values: list[float | None] = [None] * pad + d_raw  # type: ignore[operator]
    return {"k": k_values, "d": d_values}


def rate_of_change(prices: list[float], period: int = 10) -> list[float | None]:
    """Rate of Change (momentum %) = (close - close[n]) / close[n] * 100."""
    result: list[float | None] = [None] * period
    for i in range(period, len(prices)):
        prev = prices[i - period]
        if prev == 0:
            result.append(None)
        else:
            result.append(round((prices[i] - prev) / prev * 100, 4))
    return result


def linear_regression_slope(prices: list[float], period: int = 20) -> list[float | None]:
    """Linear regression slope over a rolling window — trend direction/strength."""
    result: list[float | None] = [None] * (period - 1)
    x = list(range(period))
    x_mean = sum(x) / period
    ss_x = sum((xi - x_mean) ** 2 for xi in x)
    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1: i + 1]
        y_mean = sum(window) / period
        ss_xy = sum((x[j] - x_mean) * (window[j] - y_mean) for j in range(period))
        slope = ss_xy / ss_x if ss_x != 0 else 0.0
        result.append(round(slope, 6))
    return result


def vwap(
    highs: list[float], lows: list[float], closes: list[float], volumes: list[float],
) -> list[float]:
    """Volume-Weighted Average Price (cumulative)."""
    typical_prices = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    cum_tp_vol = 0.0
    cum_vol = 0.0
    result = []
    for tp, v in zip(typical_prices, volumes):
        cum_tp_vol += tp * v
        cum_vol += v
        result.append(round(cum_tp_vol / cum_vol if cum_vol > 0 else tp, 6))
    return result


# ── Composite signal builder ──────────────────────────────────────────────────

def compute_signal_snapshot(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
) -> dict[str, Any]:
    """
    Run a full suite of indicators on price data and return a signal snapshot.
    Requires at least 30 close prices for reliable signals.
    """
    if len(closes) < 20:
        return {"error": "Need at least 20 closes", "signal": "insufficient_data"}

    _highs  = highs  or closes
    _lows   = lows   or closes
    _vols   = volumes or [1.0] * len(closes)

    latest_close = closes[-1]

    # RSI
    rsi_vals = rsi(closes, 14)
    current_rsi = next((v for v in reversed(rsi_vals) if v is not None), None)

    # MACD
    macd_data = macd(closes)
    current_macd = next((v for v in reversed(macd_data["macd"]) if v is not None), None)
    current_signal_line = next((v for v in reversed(macd_data["signal"]) if v is not None), None)
    current_hist = next((v for v in reversed(macd_data["histogram"]) if v is not None), None)

    # Bollinger Bands
    bb = bollinger_bands(closes)
    current_upper = next((v for v in reversed(bb["upper"]) if v is not None), None)
    current_lower = next((v for v in reversed(bb["lower"]) if v is not None), None)
    current_mid   = next((v for v in reversed(bb["middle"]) if v is not None), None)
    bb_position = None
    if current_upper and current_lower and current_upper != current_lower:
        bb_position = round((latest_close - current_lower) / (current_upper - current_lower), 3)

    # SMA 20 / 50
    sma20 = sma(closes, 20)
    sma50 = sma(closes, min(50, len(closes)))
    current_sma20 = next((v for v in reversed(sma20) if v is not None), None)
    current_sma50 = next((v for v in reversed(sma50) if v is not None), None)

    # Trend: price vs SMAs
    trend = "unknown"
    if current_sma20 and current_sma50:
        if latest_close > current_sma20 > current_sma50:
            trend = "uptrend"
        elif latest_close < current_sma20 < current_sma50:
            trend = "downtrend"
        else:
            trend = "sideways"

    # ROC momentum
    roc_vals = rate_of_change(closes, 10)
    current_roc = next((v for v in reversed(roc_vals) if v is not None), None)

    # Linear regression slope (trend strength)
    lr_slopes = linear_regression_slope(closes, min(20, len(closes)))
    current_slope = next((v for v in reversed(lr_slopes) if v is not None), None)

    # Composite signal
    bull_points = 0
    bear_points = 0
    if current_rsi is not None:
        if current_rsi < 35:
            bull_points += 2
        elif current_rsi > 65:
            bear_points += 2
        elif current_rsi < 50:
            bull_points += 1
        else:
            bear_points += 1
    if current_hist is not None:
        if current_hist > 0:
            bull_points += 1
        else:
            bear_points += 1
    if trend == "uptrend":
        bull_points += 2
    elif trend == "downtrend":
        bear_points += 2
    if bb_position is not None:
        if bb_position < 0.2:
            bull_points += 1   # near lower band
        elif bb_position > 0.8:
            bear_points += 1   # near upper band

    total = bull_points + bear_points
    composite_score = (bull_points - bear_points) / total if total > 0 else 0.0
    composite_signal = "bullish" if composite_score > 0.2 else "bearish" if composite_score < -0.2 else "neutral"

    return {
        "latest_close": latest_close,
        "rsi": current_rsi,
        "rsi_signal": "oversold" if (current_rsi or 50) < 30 else "overbought" if (current_rsi or 50) > 70 else "neutral",
        "macd": current_macd,
        "macd_signal_line": current_signal_line,
        "macd_histogram": current_hist,
        "macd_crossover": "bullish" if (current_macd or 0) > (current_signal_line or 0) else "bearish",
        "bollinger_upper": current_upper,
        "bollinger_lower": current_lower,
        "bollinger_mid": current_mid,
        "bollinger_position": bb_position,
        "sma20": current_sma20,
        "sma50": current_sma50,
        "trend": trend,
        "roc_10": current_roc,
        "lr_slope": current_slope,
        "composite_score": round(composite_score, 3),
        "composite_signal": composite_signal,
        "bull_points": bull_points,
        "bear_points": bear_points,
        "candles_used": len(closes),
    }
