from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskPlan:
    side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    trailing_activation_price: float
    trailing_gap_pct: float
    quantity: float
    margin_used: float
    notional: float
    estimated_fee: float


class RiskManager:
    def __init__(self, config):
        self.cfg = config

    def daily_loss_breached(self, wallet) -> bool:
        return wallet.data["daily_realized_pnl"] <= -(self.cfg.starting_balance * self.cfg.daily_loss_limit_pct)

    def consecutive_losses_breached(self, wallet) -> bool:
        return wallet.data["consecutive_losses"] >= self.cfg.max_consecutive_losses

    def build_plan(self, symbol: str, side: str, entry_price: float, atr_value: float, wallet_balance: float) -> RiskPlan:
        risk_amount = wallet_balance * self.cfg.risk_per_trade
        stop_distance = max(atr_value * self.cfg.atr_stop_mult, entry_price * 0.003)

        if side == "long":
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + (stop_distance * self.cfg.rr_ratio)
            trailing_activation = entry_price + (stop_distance * self.cfg.trailing_activation_r)
        else:
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - (stop_distance * self.cfg.rr_ratio)
            trailing_activation = entry_price - (stop_distance * self.cfg.trailing_activation_r)

        qty = risk_amount / stop_distance
        notional = qty * entry_price
        margin_used = notional / self.cfg.leverage
        estimated_fee = notional * self.cfg.fee_rate

        if margin_used > wallet_balance * 0.95:
            scale = (wallet_balance * 0.95) / margin_used
            qty *= scale
            notional = qty * entry_price
            margin_used = notional / self.cfg.leverage
            estimated_fee = notional * self.cfg.fee_rate

        return RiskPlan(
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_activation_price=trailing_activation,
            trailing_gap_pct=self.cfg.trailing_gap_pct,
            quantity=qty,
            margin_used=margin_used,
            notional=notional,
            estimated_fee=estimated_fee,
        )
