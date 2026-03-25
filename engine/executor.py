from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from engine.paper_wallet import Position


class Executor:
    def __init__(self, config, wallet):
        self.cfg = config
        self.wallet = wallet

    def open_position(self, symbol: str, side: str, entry_price: float, risk_plan, signal) -> dict:
        effective_entry = (
            entry_price + (entry_price * self.cfg.slippage_rate)
            if side == "long"
            else entry_price - (entry_price * self.cfg.slippage_rate)
        )
        fees = abs(risk_plan.notional) * self.cfg.fee_rate
        pos = Position(
            id=uuid4().hex[:12],
            symbol=symbol,
            side=side,
            entry_price=round(effective_entry, 6),
            initial_quantity=round(risk_plan.quantity, 6),
            quantity=round(risk_plan.quantity, 6),
            leverage=self.cfg.leverage,
            stop_loss=round(risk_plan.stop_loss, 6),
            take_profit=round(risk_plan.take_profit, 6),
            partial_take_profit_price=round(risk_plan.partial_take_profit_price, 6),
            partial_take_profit_pct=round(risk_plan.partial_take_profit_pct, 4),
            partial_take_profit_taken=False,
            break_even_armed=False,
            trailing_active=False,
            trailing_activation_price=round(risk_plan.trailing_activation_price, 6),
            trailing_gap_pct=self.cfg.trailing_gap_pct,
            trailing_stop=None,
            opened_at=datetime.now(timezone.utc).isoformat(),
            highest_price=round(effective_entry, 6),
            lowest_price=round(effective_entry, 6),
            fees_paid=round(fees, 6),
            realized_partial_pnl=0.0,
            margin_used=round(risk_plan.margin_used, 6),
            reason=signal.reason,
            score=int(signal.score),
            regime=signal.regime,
            higher_timeframe_bias=signal.higher_timeframe_bias,
        )
        self.wallet.open_trade(pos)
        return self.wallet.get_open_position(pos.id) or {}
