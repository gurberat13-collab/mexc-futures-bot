from __future__ import annotations

import unittest
from types import SimpleNamespace

import pandas as pd

from engine.backtester import Backtester
from engine.risk import RiskManager


class DummyConfig:
    timeframe = "Min15"
    higher_timeframe = "Hour4"
    higher_kline_limit = 20
    backtest_bars = 12
    backtest_warmup_bars = 1
    starting_balance = 1000.0
    risk_per_trade = 0.03
    atr_stop_mult = 1.4
    rr_ratio = 1.8
    trailing_activation_r = 1.1
    trailing_gap_pct = 0.008
    leverage = 5
    fee_rate = 0.0004
    partial_take_profit_r = 1.0
    partial_take_profit_pct = 0.5
    break_even_on_partial = True
    max_last_candle_pct = 1.0
    daily_loss_limit_pct = 0.01
    max_consecutive_losses = 3
    max_trades_per_day = 12
    cooldown_minutes = 0
    funding_abs_limit = 0.0025
    slippage_rate = 0.001


class DummyLogger:
    def info(self, *_args, **_kwargs) -> None:
        pass

    def warning(self, *_args, **_kwargs) -> None:
        pass

    def exception(self, *_args, **_kwargs) -> None:
        pass


class AlwaysLongStrategy:
    def analyze(self, *_args, **_kwargs):
        return SimpleNamespace(action="long", regime_ok=True, atr_value=1.0)


class FakeClient:
    def __init__(self, primary_df: pd.DataFrame, higher_df: pd.DataFrame, funding_history=None):
        self.primary_df = primary_df
        self.higher_df = higher_df
        self.funding_history = funding_history or []

    def get_klines(self, _symbol: str, interval: str, _limit: int) -> pd.DataFrame:
        return self.higher_df.copy() if interval == DummyConfig.higher_timeframe else self.primary_df.copy()

    def get_funding_rate_history(self, _symbol: str, page_size: int = 1000):
        return list(self.funding_history)


class RecordingRiskManager:
    def __init__(self):
        self.entry_prices: list[float] = []

    def build_plan(self, _symbol: str, side: str, entry_price: float, _atr_value: float, _wallet_balance: float):
        self.entry_prices.append(entry_price)
        return SimpleNamespace(
            quantity=1.0,
            margin_used=100.0,
            estimated_fee=0.0,
            stop_loss=entry_price - 1 if side == "long" else entry_price + 1,
            take_profit=entry_price + 2 if side == "long" else entry_price - 2,
            partial_take_profit_price=entry_price + 1 if side == "long" else entry_price - 1,
            partial_take_profit_pct=0.5,
            trailing_activation_price=entry_price + 1 if side == "long" else entry_price - 1,
            trailing_gap_pct=0.008,
        )


def build_df(closes: list[float]) -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-03-26 00:00:00", tz="UTC")
    previous = closes[0]
    for idx, close in enumerate(closes):
        rows.append(
            {
                "time": start + pd.Timedelta(minutes=15 * idx),
                "open": previous,
                "close": close,
                "high": max(previous, close) + 0.2,
                "low": min(previous, close) - 0.2,
                "volume": 1000 + idx,
                "amount": (1000 + idx) * close,
            }
        )
        previous = close
    return pd.DataFrame(rows)


class BacktesterTests(unittest.TestCase):
    def test_entry_uses_next_bar_open_not_signal_bar_close(self) -> None:
        cfg = DummyConfig()
        primary_df = pd.DataFrame(
            [
                {"time": pd.Timestamp("2026-03-26 00:00:00", tz="UTC"), "open": 100.0, "close": 101.0, "high": 101.2, "low": 99.8, "volume": 1000, "amount": 101000},
                {"time": pd.Timestamp("2026-03-26 00:15:00", tz="UTC"), "open": 101.0, "close": 102.0, "high": 102.2, "low": 100.8, "volume": 1001, "amount": 102102},
                {"time": pd.Timestamp("2026-03-26 00:30:00", tz="UTC"), "open": 120.0, "close": 121.0, "high": 121.2, "low": 119.8, "volume": 1002, "amount": 121242},
            ]
        )
        higher_df = build_df([100.0] * 3)
        risk = RecordingRiskManager()
        backtester = Backtester(cfg, FakeClient(primary_df, higher_df), AlwaysLongStrategy(), risk, DummyLogger())

        backtester.run("BTC_USDT", bars=3)

        self.assertEqual(risk.entry_prices, [120.0])

    def test_close_fraction_applies_slippage_on_exit(self) -> None:
        cfg = DummyConfig()
        backtester = Backtester(cfg, FakeClient(build_df([100.0, 101.0]), build_df([100.0, 101.0])), AlwaysLongStrategy(), RiskManager(cfg), DummyLogger())
        position = {
            "symbol": "BTC_USDT",
            "side": "long",
            "entry_price": 100.0,
            "quantity": 1.0,
            "margin_used": 20.0,
            "fees_paid": 0.0,
            "realized_partial_pnl": 0.0,
        }

        event, _, balance = backtester._close_fraction(position, 110.0, 1.0, "take_profit", 0.0)

        expected_exit = 110.0 - (110.0 * cfg.slippage_rate)
        expected_close_fee = expected_exit * cfg.fee_rate
        expected_net = (expected_exit - 100.0) - expected_close_fee
        self.assertAlmostEqual(event["net_pnl"], round(expected_net, 6))
        self.assertAlmostEqual(balance, 20.0 + expected_net)

    def test_daily_loss_limit_blocks_new_entries_after_first_losing_trade(self) -> None:
        cfg = DummyConfig()
        primary_df = build_df([100.0, 100.0, 98.5, 100.0, 98.5, 100.0, 98.5])
        higher_df = build_df([100.0] * 7)
        backtester = Backtester(cfg, FakeClient(primary_df, higher_df), AlwaysLongStrategy(), RiskManager(cfg), DummyLogger())

        result, trades = backtester.run("BTC_USDT", bars=7)

        full_closes = [trade for trade in trades if trade["trade_type"] == "full_close"]
        self.assertEqual(len(full_closes), 1)
        self.assertEqual(result.guardrail_blocks.get("daily_loss_limit"), 4)

    def test_consecutive_loss_limit_blocks_after_threshold(self) -> None:
        cfg = DummyConfig()
        cfg.daily_loss_limit_pct = 1.0
        cfg.max_consecutive_losses = 1
        primary_df = build_df([100.0, 100.0, 98.5, 100.0, 98.5, 100.0])
        higher_df = build_df([100.0] * 6)
        backtester = Backtester(cfg, FakeClient(primary_df, higher_df), AlwaysLongStrategy(), RiskManager(cfg), DummyLogger())

        result, trades = backtester.run("BTC_USDT", bars=6)

        full_closes = [trade for trade in trades if trade["trade_type"] == "full_close"]
        self.assertEqual(len(full_closes), 1)
        self.assertEqual(result.guardrail_blocks.get("consecutive_loss_limit"), 3)

    def test_funding_limit_can_block_all_entries(self) -> None:
        cfg = DummyConfig()
        primary_df = build_df([100.0, 100.5, 101.0, 101.5, 102.0])
        higher_df = build_df([100.0] * 5)
        funding_history = [{"symbol": "BTC_USDT", "fundingRate": 0.01, "settleTime": 0}]
        backtester = Backtester(cfg, FakeClient(primary_df, higher_df, funding_history), AlwaysLongStrategy(), RiskManager(cfg), DummyLogger())

        result, trades = backtester.run("BTC_USDT", bars=5)

        self.assertFalse(trades)
        self.assertEqual(result.closed_trades, 0)
        self.assertEqual(result.guardrail_blocks.get("funding_limit"), 3)


if __name__ == "__main__":
    unittest.main()
