from __future__ import annotations

import asyncio
import tempfile
import unittest
from types import SimpleNamespace

import pandas as pd

from engine.executor import Executor
from engine.paper_wallet import PaperWallet, Position
from engine.position_manager import PositionManager
from engine.risk import RiskManager
from engine.scanner import ScannerEngine
from exchange.mexc_futures import MarketSnapshot


class DummyConfig:
    symbols = ["BTC_USDT", "ETH_USDT"]
    open_interest_history_size = 24
    report_timezone = "UTC"
    scan_interval_seconds = 30
    max_open_positions = 2
    max_trades_per_day = 10
    cooldown_minutes = 0
    starting_balance = 1000.0
    daily_loss_limit_pct = 1.0
    max_consecutive_losses = 10
    timeframe = "Min15"
    higher_timeframe = "Hour4"
    kline_limit = 50
    higher_kline_limit = 50
    funding_abs_limit = 1.0
    max_spread_pct = 1.0
    max_last_candle_pct = 1.0
    min_expected_net_rr = 0.0
    min_expected_net_profit_pct = 0.0
    position_intrabar_from_klines = False
    websocket_enabled = False
    heartbeat_interval_minutes = 0
    health_stale_seconds = 180
    daily_report_enabled = False
    state_path = "storage/state.json"
    leverage = 5
    risk_per_trade = 0.03
    atr_stop_mult = 1.4
    rr_ratio = 2.0
    trailing_activation_r = 1.1
    trailing_gap_pct = 0.008
    fee_rate = 0.0004
    slippage_rate = 0.0003
    partial_take_profit_r = 1.5
    partial_take_profit_pct = 0.5
    break_even_on_partial = True
    max_symbol_correlation = 1.0
    min_correlation_lookback = 40


class DummyLogger:
    def info(self, *_args, **_kwargs) -> None:
        pass

    def warning(self, *_args, **_kwargs) -> None:
        pass

    def exception(self, *_args, **_kwargs) -> None:
        pass


class AlwaysLongStrategy:
    def analyze(self, symbol: str, *_args, **_kwargs):
        return SimpleNamespace(
            symbol=symbol,
            action="long",
            score=5,
            bullish_votes=5,
            bearish_votes=0,
            directional_votes=5,
            opposing_votes=0,
            signal_conflict_ratio=0.0,
            reason="test long",
            hold_vol_ratio=1.0,
            regime="trending",
            adx_value=30.0,
            atr_pct=0.01,
            higher_timeframe_bias="bullish",
            higher_timeframe_confirmed=True,
            volume_ratio=1.5,
            market_structure="bullish",
            vwap_distance_pct=0.01,
            volatility_expansion=True,
            breakout_up=True,
            breakout_down=False,
            blocked_by=[],
            atr_value=0.01,
        )


class SpyStrategy:
    def __init__(self):
        self.primary_last_close = None
        self.higher_last_close = None

    def analyze(self, _symbol: str, df: pd.DataFrame, snapshot=None, higher_df: pd.DataFrame | None = None, market_context=None):
        self.primary_last_close = float(df.iloc[-1]["close"])
        self.higher_last_close = float(higher_df.iloc[-1]["close"]) if higher_df is not None and not higher_df.empty else None
        return SimpleNamespace(
            symbol=_symbol,
            action="hold",
            score=0,
            bullish_votes=0,
            bearish_votes=0,
            directional_votes=0,
            opposing_votes=0,
            signal_conflict_ratio=0.0,
            reason="spy",
            hold_vol_ratio=1.0,
            regime="trending",
            adx_value=30.0,
            atr_pct=0.01,
            higher_timeframe_bias="neutral",
            higher_timeframe_confirmed=True,
            volume_ratio=1.0,
            market_structure="neutral",
            vwap_distance_pct=0.0,
            volatility_expansion=False,
            breakout_up=False,
            breakout_down=False,
            blocked_by=[],
            atr_value=0.01,
        )


class FakeClient:
    def __init__(self):
        self.frame = pd.DataFrame(
            [
                {
                    "time": pd.Timestamp("2026-04-09 00:00:00", tz="UTC"),
                    "open": 100.0,
                    "close": 100.1,
                    "high": 100.2,
                    "low": 99.9,
                    "volume": 1000,
                    "amount": 100100,
                },
                {
                    "time": pd.Timestamp("2026-04-09 00:15:00", tz="UTC"),
                    "open": 100.1,
                    "close": 100.2,
                    "high": 100.3,
                    "low": 100.0,
                    "volume": 1001,
                    "amount": 100300.2,
                }
            ]
        )

    def get_klines(self, _symbol: str, _interval: str, _limit: int) -> pd.DataFrame:
        return self.frame.copy()

    def get_ticker(self, symbol: str) -> MarketSnapshot:
        return MarketSnapshot(
            symbol=symbol,
            last_price=100.0,
            bid=100.0,
            ask=100.0,
            funding_rate=0.0,
            spread_pct=0.0,
            hold_vol=1.0,
            timestamp=1,
            source="test",
        )

    @property
    def stats(self) -> dict[str, int]:
        return {}


async def noop_notifier(_text: str) -> None:
    return None


class ScannerTests(unittest.TestCase):
    def test_scanner_rebuilds_plan_with_remaining_balance_before_second_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = DummyConfig()
            cfg.state_path = f"{tmp}/state.json"
            wallet = PaperWallet(f"{tmp}/wallet.json", f"{tmp}/trades.json", 1000)
            scanner = ScannerEngine(
                cfg,
                FakeClient(),
                AlwaysLongStrategy(),
                RiskManager(cfg),
                wallet,
                Executor(cfg, wallet),
                PositionManager(cfg, wallet),
                DummyLogger(),
                noop_notifier,
            )

            asyncio.run(scanner.start())
            asyncio.run(scanner.tick())

            positions = wallet.open_positions
            self.assertEqual(len(positions), 2)
            self.assertGreaterEqual(wallet.balance, 0.0)
            self.assertAlmostEqual(float(positions[0]["margin_used"]), 950.0)
            self.assertAlmostEqual(float(positions[1]["margin_used"]), 47.5)

    def test_daily_loss_limit_kill_switch_closes_remaining_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = DummyConfig()
            cfg.state_path = f"{tmp}/state.json"
            cfg.daily_loss_limit_pct = 0.01
            wallet = PaperWallet(f"{tmp}/wallet.json", f"{tmp}/trades.json", 1000)
            wallet.open_trade(
                Position(
                    id="p1",
                    symbol="BTC_USDT",
                    side="long",
                    entry_price=100.0,
                    initial_quantity=1.0,
                    quantity=1.0,
                    leverage=5,
                    stop_loss=90.0,
                    take_profit=120.0,
                    partial_take_profit_price=110.0,
                    partial_take_profit_pct=0.5,
                    partial_take_profit_taken=False,
                    break_even_armed=False,
                    trailing_active=False,
                    trailing_activation_price=115.0,
                    trailing_gap_pct=0.01,
                    trailing_stop=None,
                    opened_at="2026-01-01T00:00:00+00:00",
                    highest_price=100.0,
                    lowest_price=100.0,
                    fees_paid=0.0,
                    realized_partial_pnl=0.0,
                    margin_used=20.0,
                    reason="test",
                    score=5,
                    regime="trending",
                    higher_timeframe_bias="bullish",
                )
            )
            wallet.data["daily_realized_pnl"] = -11.0
            wallet.save()

            scanner = ScannerEngine(
                cfg,
                FakeClient(),
                AlwaysLongStrategy(),
                RiskManager(cfg),
                wallet,
                Executor(cfg, wallet),
                PositionManager(cfg, wallet),
                DummyLogger(),
                noop_notifier,
            )

            asyncio.run(scanner.start())
            asyncio.run(scanner.tick())

            self.assertFalse(wallet.open_positions)
            self.assertEqual(wallet.data["history"][0]["exit_reason"], "daily_loss_kill_switch")

    def test_scanner_uses_only_closed_candles_for_signal_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = DummyConfig()
            cfg.state_path = f"{tmp}/state.json"
            wallet = PaperWallet(f"{tmp}/wallet.json", f"{tmp}/trades.json", 1000)
            client = FakeClient()
            client.frame = pd.DataFrame(
                [
                    {
                        "time": pd.Timestamp("2026-04-09 00:00:00", tz="UTC"),
                        "open": 100.0,
                        "close": 101.0,
                        "high": 101.2,
                        "low": 99.8,
                        "volume": 1000,
                        "amount": 101000,
                    },
                    {
                        "time": pd.Timestamp("2026-04-09 00:15:00", tz="UTC"),
                        "open": 101.0,
                        "close": 102.0,
                        "high": 102.2,
                        "low": 100.8,
                        "volume": 1001,
                        "amount": 102102,
                    },
                    {
                        "time": pd.Timestamp("2026-04-09 00:30:00", tz="UTC"),
                        "open": 102.0,
                        "close": 999.0,
                        "high": 999.5,
                        "low": 101.5,
                        "volume": 1002,
                        "amount": 1000998,
                    },
                ]
            )
            strategy = SpyStrategy()
            scanner = ScannerEngine(
                cfg,
                client,
                strategy,
                RiskManager(cfg),
                wallet,
                Executor(cfg, wallet),
                PositionManager(cfg, wallet),
                DummyLogger(),
                noop_notifier,
            )

            asyncio.run(scanner._analyze_symbol("BTC_USDT"))

            self.assertEqual(strategy.primary_last_close, 102.0)
            self.assertEqual(strategy.higher_last_close, 102.0)

    def test_wallet_rejects_position_when_margin_exceeds_balance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wallet = PaperWallet(f"{tmp}/wallet.json", f"{tmp}/trades.json", 1000)
            with self.assertRaisesRegex(RuntimeError, "Insufficient balance"):
                wallet.open_trade(
                    Position(
                        id="p1",
                        symbol="BTC_USDT",
                        side="long",
                        entry_price=100.0,
                        initial_quantity=10.0,
                        quantity=10.0,
                        leverage=5,
                        stop_loss=95.0,
                        take_profit=110.0,
                        partial_take_profit_price=105.0,
                        partial_take_profit_pct=0.5,
                        partial_take_profit_taken=False,
                        break_even_armed=False,
                        trailing_active=False,
                        trailing_activation_price=107.0,
                        trailing_gap_pct=0.01,
                        trailing_stop=None,
                        opened_at="2026-01-01T00:00:00+00:00",
                        highest_price=100.0,
                        lowest_price=100.0,
                        fees_paid=1.0,
                        realized_partial_pnl=0.0,
                        margin_used=1001.0,
                        reason="test",
                        score=5,
                        regime="trending",
                        higher_timeframe_bias="bullish",
                    )
                )


if __name__ == "__main__":
    unittest.main()
