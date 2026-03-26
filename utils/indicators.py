from __future__ import annotations

from typing import Tuple

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi_series = 100 - (100 / (1 + rs))
    rsi_series = rsi_series.where(avg_loss != 0, 100.0)
    rsi_series = rsi_series.where(avg_gain != 0, 0.0)
    return rsi_series


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = atr(df, period=1)
    atr_smooth = tr.ewm(alpha=1 / period, adjust=False).mean().replace(0, pd.NA)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_smooth
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_smooth
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)).fillna(0.0)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def rolling_vwap(df: pd.DataFrame, period: int) -> pd.Series:
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    tpv = typical_price * df["volume"]
    rolling_tpv = tpv.rolling(period, min_periods=1).sum()
    rolling_volume = df["volume"].rolling(period, min_periods=1).sum().replace(0, pd.NA)
    return (rolling_tpv / rolling_volume).fillna(df["close"])
