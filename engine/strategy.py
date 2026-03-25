from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from utils.indicators import adx, atr, ema, macd, rolling_vwap, rsi


@dataclass
class Signal:
    symbol: str
    action: str
    score: int
    reason: str
    atr_value: float
    close_price: float
    ema_fast: float
    ema_slow: float
    rsi_value: float
    macd_hist: float
    adx_value: float
    atr_pct: float
    volume_ratio: float
    breakout_up: bool
    breakout_down: bool
    vwap_value: float
    vwap_distance_pct: float
    market_structure: str
    volatility_expansion: bool
    regime: str
    regime_ok: bool
    higher_timeframe_bias: str
    higher_timeframe_confirmed: bool
    higher_timeframe_score: int
    hold_vol_ratio: float
    oi_supported: bool
    blocked_by: list[str] = field(default_factory=list)


class StrategyEngine:
    def __init__(self, config):
        self.cfg = config

    def analyze(
        self,
        symbol: str,
        df: pd.DataFrame,
        snapshot=None,
        higher_df: pd.DataFrame | None = None,
        market_context: dict[str, Any] | None = None,
    ) -> Signal:
        d = df.copy()
        d["ema_fast"] = ema(d["close"], 9)
        d["ema_slow"] = ema(d["close"], 21)
        d["rsi"] = rsi(d["close"], 14)
        _, _, d["macd_hist"] = macd(d["close"])
        d["atr"] = atr(d, self.cfg.atr_period)
        d["adx"] = adx(d, self.cfg.atr_period)
        d["vol_sma"] = d["volume"].rolling(20).mean()
        d["atr_pct"] = d["atr"] / d["close"].replace(0, pd.NA)
        d["atr_sma"] = d["atr"].rolling(20).mean()
        d["vwap"] = rolling_vwap(d, self.cfg.vwap_window)

        latest = d.iloc[-1]
        lookback = d.iloc[-(self.cfg.breakout_lookback + 1):-1]

        score = 0
        reasons: list[str] = []
        blocked_by: list[str] = []

        if latest["ema_fast"] > latest["ema_slow"]:
            score += 1
            reasons.append("EMA bullish")
        elif latest["ema_fast"] < latest["ema_slow"]:
            score -= 1
            reasons.append("EMA bearish")

        if latest["rsi"] > 55:
            score += 1
            reasons.append("RSI strong")
        elif latest["rsi"] < 45:
            score -= 1
            reasons.append("RSI weak")

        if latest["macd_hist"] > 0:
            score += 1
            reasons.append("MACD positive")
        elif latest["macd_hist"] < 0:
            score -= 1
            reasons.append("MACD negative")

        volume_ratio = float(latest["volume"] / latest["vol_sma"]) if pd.notna(latest["vol_sma"]) and latest["vol_sma"] else 1.0
        if volume_ratio >= self.cfg.volume_spike_threshold:
            if latest["close"] >= latest["open"]:
                score += 1
                reasons.append("Bullish volume spike")
            else:
                score -= 1
                reasons.append("Bearish volume spike")

        breakout_up = float(latest["close"]) > float(lookback["high"].max()) if not lookback.empty else False
        breakout_down = float(latest["close"]) < float(lookback["low"].min()) if not lookback.empty else False
        if breakout_up:
            score += 1
            reasons.append("Breakout up")
        if breakout_down:
            score -= 1
            reasons.append("Breakout down")

        vwap_value = float(latest["vwap"])
        vwap_distance_pct = float((latest["close"] - latest["vwap"]) / latest["vwap"]) if latest["vwap"] else 0.0
        if vwap_distance_pct > 0:
            score += 1
            reasons.append("Above VWAP")
        elif vwap_distance_pct < 0:
            score -= 1
            reasons.append("Below VWAP")

        market_structure = self._market_structure(d, self.cfg.market_structure_lookback)
        if market_structure == "bullish":
            score += 1
            reasons.append("Bullish structure")
        elif market_structure == "bearish":
            score -= 1
            reasons.append("Bearish structure")

        volatility_expansion = bool(
            pd.notna(latest["atr_sma"]) and latest["atr"] >= (latest["atr_sma"] * self.cfg.volatility_expansion_ratio)
        )
        if volatility_expansion:
            if latest["close"] >= latest["open"]:
                score += 1
                reasons.append("Volatility expansion up")
            else:
                score -= 1
                reasons.append("Volatility expansion down")

        hold_vol_ratio = 0.0
        oi_supported = False
        if market_context:
            hold_vol_ratio = float(market_context.get("hold_vol_ratio", 0.0) or 0.0)
            oi_supported = bool(market_context.get("oi_supported", False))
        if oi_supported and hold_vol_ratio >= self.cfg.open_interest_increase_ratio:
            if latest["close"] >= latest["open"]:
                score += 1
                reasons.append("Open interest rising with price")
            else:
                score -= 1
                reasons.append("Open interest rising with sell pressure")

        regime = "trending" if float(latest["adx"]) >= self.cfg.regime_adx_threshold and float(latest["atr_pct"]) >= self.cfg.regime_atr_pct_threshold else "ranging"
        regime_ok = (not self.cfg.require_trending_regime) or regime == "trending"
        if regime != "trending":
            blocked_by.append("regime_not_trending")

        higher_timeframe_score = 0
        higher_timeframe_bias = "neutral"
        higher_timeframe_confirmed = True
        if higher_df is not None and not higher_df.empty:
            higher_timeframe_score = self._higher_timeframe_score(higher_df)
            higher_timeframe_bias = "bullish" if higher_timeframe_score > 0 else "bearish" if higher_timeframe_score < 0 else "neutral"
            if higher_timeframe_bias == "bullish":
                reasons.append("Higher timeframe bullish")
            elif higher_timeframe_bias == "bearish":
                reasons.append("Higher timeframe bearish")
            score += 1 if higher_timeframe_bias == "bullish" else -1 if higher_timeframe_bias == "bearish" else 0

        action = "hold"
        if score >= self.cfg.aggressive_score_threshold:
            action = "long"
        elif score <= -self.cfg.aggressive_score_threshold:
            action = "short"

        if action == "long" and higher_df is not None:
            higher_timeframe_confirmed = higher_timeframe_bias == "bullish"
        elif action == "short" and higher_df is not None:
            higher_timeframe_confirmed = higher_timeframe_bias == "bearish"

        if action != "hold" and self.cfg.higher_timeframe_required and higher_df is not None and not higher_timeframe_confirmed:
            blocked_by.append("higher_timeframe_mismatch")
            action = "hold"

        if action != "hold" and not regime_ok:
            action = "hold"

        reason = ", ".join(reasons) if reasons else "No edge"
        if blocked_by:
            reason = f"{reason} | blocked: {', '.join(blocked_by)}"

        return Signal(
            symbol=symbol,
            action=action,
            score=score,
            reason=reason,
            atr_value=float(latest["atr"]),
            close_price=float(latest["close"]),
            ema_fast=float(latest["ema_fast"]),
            ema_slow=float(latest["ema_slow"]),
            rsi_value=float(latest["rsi"]),
            macd_hist=float(latest["macd_hist"]),
            adx_value=float(latest["adx"]),
            atr_pct=float(latest["atr_pct"]),
            volume_ratio=volume_ratio,
            breakout_up=breakout_up,
            breakout_down=breakout_down,
            vwap_value=vwap_value,
            vwap_distance_pct=vwap_distance_pct,
            market_structure=market_structure,
            volatility_expansion=volatility_expansion,
            regime=regime,
            regime_ok=regime_ok,
            higher_timeframe_bias=higher_timeframe_bias,
            higher_timeframe_confirmed=higher_timeframe_confirmed,
            higher_timeframe_score=higher_timeframe_score,
            hold_vol_ratio=hold_vol_ratio,
            oi_supported=oi_supported,
            blocked_by=blocked_by,
        )

    def _higher_timeframe_score(self, df: pd.DataFrame) -> int:
        d = df.copy()
        d["ema_fast"] = ema(d["close"], 9)
        d["ema_slow"] = ema(d["close"], 21)
        d["rsi"] = rsi(d["close"], 14)
        _, _, d["macd_hist"] = macd(d["close"])
        latest = d.iloc[-1]

        score = 0
        if latest["ema_fast"] > latest["ema_slow"]:
            score += 1
        elif latest["ema_fast"] < latest["ema_slow"]:
            score -= 1

        if latest["rsi"] > 52:
            score += 1
        elif latest["rsi"] < 48:
            score -= 1

        if latest["macd_hist"] > 0:
            score += 1
        elif latest["macd_hist"] < 0:
            score -= 1
        return score

    def _market_structure(self, df: pd.DataFrame, lookback: int) -> str:
        if len(df) < lookback * 2:
            return "neutral"
        window = df.iloc[-(lookback * 2):]
        first_half = window.iloc[:lookback]
        second_half = window.iloc[lookback:]
        higher_high = float(second_half["high"].max()) > float(first_half["high"].max())
        higher_low = float(second_half["low"].min()) > float(first_half["low"].min())
        lower_high = float(second_half["high"].max()) < float(first_half["high"].max())
        lower_low = float(second_half["low"].min()) < float(first_half["low"].min())
        if higher_high and higher_low:
            return "bullish"
        if lower_high and lower_low:
            return "bearish"
        return "neutral"
