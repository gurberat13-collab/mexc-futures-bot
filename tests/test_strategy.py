from __future__ import annotations

import unittest

import pandas as pd

from engine.strategy import StrategyEngine


class DummyConfig:
    atr_period = 14
    breakout_lookback = 20
    aggressive_score_threshold = 4
    volume_spike_threshold = 1.2
    vwap_window = 30
    volatility_expansion_ratio = 1.01
    market_structure_lookback = 10
    open_interest_increase_ratio = 1.01
    regime_adx_threshold = 15
    regime_atr_pct_threshold = 0.001
    require_trending_regime = True
    higher_timeframe_required = True


def build_trend_df(rows: int = 160, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    data = []
    price = start
    for idx in range(rows):
        open_price = price
        close_price = price + step
        data.append(
            {
                "time": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(minutes=15 * idx),
                "open": open_price,
                "close": close_price,
                "high": close_price + 0.4,
                "low": open_price - 0.2,
                "volume": 1000 + (idx * 10),
                "amount": (1000 + idx * 10) * close_price,
            }
        )
        price = close_price
    return pd.DataFrame(data)


class StrategyTests(unittest.TestCase):
    def test_trending_market_produces_long_signal(self) -> None:
        strategy = StrategyEngine(DummyConfig())
        primary = build_trend_df()
        higher = build_trend_df(rows=120, step=3.0)

        signal = strategy.analyze(
            "BTC_USDT",
            primary,
            higher_df=higher,
            market_context={"oi_supported": True, "hold_vol_ratio": 1.05},
        )

        self.assertEqual(signal.action, "long")
        self.assertEqual(signal.regime, "trending")
        self.assertTrue(signal.higher_timeframe_confirmed)
        self.assertGreaterEqual(signal.score, DummyConfig.aggressive_score_threshold)


if __name__ == "__main__":
    unittest.main()

