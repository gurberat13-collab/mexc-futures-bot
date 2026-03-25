from __future__ import annotations

import unittest

from engine.risk import RiskManager


class DummyConfig:
    risk_per_trade = 0.03
    atr_stop_mult = 1.4
    rr_ratio = 1.8
    trailing_activation_r = 1.1
    trailing_gap_pct = 0.008
    leverage = 5
    fee_rate = 0.0004
    partial_take_profit_r = 1.0
    partial_take_profit_pct = 0.5
    daily_loss_limit_pct = 0.08
    max_consecutive_losses = 3
    starting_balance = 1000


class RiskTests(unittest.TestCase):
    def test_build_plan_has_partial_target_and_margin_scaling(self) -> None:
        manager = RiskManager(DummyConfig())
        plan = manager.build_plan("BTC_USDT", "long", entry_price=70000, atr_value=500, wallet_balance=1000)

        self.assertGreater(plan.quantity, 0)
        self.assertLess(plan.partial_take_profit_price, plan.take_profit)
        self.assertGreater(plan.stop_loss, 0)
        self.assertLessEqual(plan.margin_used, 950)
        self.assertEqual(plan.partial_take_profit_pct, 0.5)


if __name__ == "__main__":
    unittest.main()

