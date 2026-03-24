from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from utils.indicators import atr, ema, macd, rsi


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
    volume_ratio: float
    breakout_up: bool
    breakout_down: bool


class StrategyEngine:
    def __init__(self, config):
        self.cfg = config

    def analyze(self, symbol: str, df: pd.DataFrame) -> Signal:
        d = df.copy()
        d["ema_fast"] = ema(d["close"], 9)
        d["ema_slow"] = ema(d["close"], 21)
        d["rsi"] = rsi(d["close"], 14)
        _, _, d["macd_hist"] = macd(d["close"])
        d["atr"] = atr(d, self.cfg.atr_period)
        d["vol_sma"] = d["volume"].rolling(20).mean()

        latest = d.iloc[-1]
        lookback = d.iloc[-(self.cfg.breakout_lookback + 1):-1]

        score = 0
        reasons: list[str] = []

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

        volume_ratio = float(latest["volume"] / latest["vol_sma"]) if latest["vol_sma"] else 1.0
        if volume_ratio > 1.2:
            if latest["close"] >= latest["open"]:
                score += 1
                reasons.append("Bullish volume")
            else:
                score -= 1
                reasons.append("Bearish volume")

        breakout_up = float(latest["close"]) > float(lookback["high"].max()) if not lookback.empty else False
        breakout_down = float(latest["close"]) < float(lookback["low"].min()) if not lookback.empty else False

        if breakout_up:
            score += 1
            reasons.append("Breakout up")
        if breakout_down:
            score -= 1
            reasons.append("Breakout down")

        action = "hold"
        if score >= self.cfg.aggressive_score_threshold:
            action = "long"
        elif score <= -self.cfg.aggressive_score_threshold:
            action = "short"

        return Signal(
            symbol=symbol,
            action=action,
            score=score,
            reason=", ".join(reasons) if reasons else "No edge",
            atr_value=float(latest["atr"]),
            close_price=float(latest["close"]),
            ema_fast=float(latest["ema_fast"]),
            ema_slow=float(latest["ema_slow"]),
            rsi_value=float(latest["rsi"]),
            macd_hist=float(latest["macd_hist"]),
            volume_ratio=volume_ratio,
            breakout_up=breakout_up,
            breakout_down=breakout_down,
        )
