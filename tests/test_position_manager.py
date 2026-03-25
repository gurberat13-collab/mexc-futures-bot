from __future__ import annotations

import tempfile
import unittest

from engine.paper_wallet import PaperWallet, Position
from engine.position_manager import PositionManager
from exchange.mexc_futures import MarketSnapshot


class DummyConfig:
    fee_rate = 0.0004
    slippage_rate = 0.0003
    break_even_on_partial = True


class PositionManagerTests(unittest.TestCase):
    def test_partial_take_profit_arms_break_even(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wallet = PaperWallet(f"{tmp}/wallet.json", f"{tmp}/trades.json", 1000)
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
                    margin_used=100.0,
                    reason="test",
                    score=5,
                    regime="trending",
                    higher_timeframe_bias="bullish",
                )
            )
            manager = PositionManager(DummyConfig(), wallet)
            events = manager.update_positions(
                {
                    "BTC_USDT": MarketSnapshot(
                        symbol="BTC_USDT",
                        last_price=105.0,
                        bid=105.0,
                        ask=105.1,
                        funding_rate=0.0,
                        spread_pct=0.001,
                        hold_vol=1.0,
                        timestamp=1,
                        source="test",
                    )
                }
            )

            updated = wallet.get_open_position("p1")
            self.assertEqual(len(events), 1)
            self.assertIsNotNone(updated)
            self.assertTrue(updated["partial_take_profit_taken"])
            self.assertTrue(updated["break_even_armed"])
            self.assertGreaterEqual(float(updated["stop_loss"]), float(updated["entry_price"]))


if __name__ == "__main__":
    unittest.main()
