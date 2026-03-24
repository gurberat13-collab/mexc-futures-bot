from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from utils.helpers import load_json, save_json


@dataclass
class Position:
    symbol: str
    side: str
    entry_price: float
    quantity: float
    leverage: int
    stop_loss: float
    take_profit: float
    trailing_active: bool
    trailing_activation_price: float
    trailing_gap_pct: float
    trailing_stop: float | None
    opened_at: str
    highest_price: float
    lowest_price: float
    fees_paid: float
    reason: str


class PaperWallet:
    def __init__(self, path: str, starting_balance: float):
        self.path = path
        self.data = load_json(
            path,
            {
                "balance": starting_balance,
                "equity": starting_balance,
                "realized_pnl": 0.0,
                "daily_realized_pnl": 0.0,
                "consecutive_losses": 0,
                "trades_today": 0,
                "day": datetime.now(timezone.utc).date().isoformat(),
                "open_position": None,
                "history": [],
            },
        )
        self.save()

    def rollover_if_needed(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        if self.data["day"] != today:
            self.data["day"] = today
            self.data["daily_realized_pnl"] = 0.0
            self.data["consecutive_losses"] = 0
            self.data["trades_today"] = 0
            self.save()

    @property
    def balance(self) -> float:
        return float(self.data["balance"])

    @property
    def open_position(self) -> dict[str, Any] | None:
        return self.data.get("open_position")

    def save(self) -> None:
        save_json(self.path, self.data)

    def can_open_new_trade(self, max_open_positions: int, max_trades_per_day: int) -> bool:
        self.rollover_if_needed()
        if max_open_positions <= 0:
            return False
        if self.open_position is not None:
            return False
        if self.data["trades_today"] >= max_trades_per_day:
            return False
        return True

    def set_equity(self, equity: float) -> None:
        self.data["equity"] = equity
        self.save()

    def open_trade(self, position: Position, margin_used: float) -> None:
        self.data["balance"] -= margin_used
        self.data["open_position"] = {**asdict(position), "margin_used": margin_used}
        self.data["trades_today"] += 1
        self.save()

    def close_trade(self, exit_price: float, exit_reason: str, fee_rate: float, slippage_rate: float) -> dict[str, Any]:
        pos = self.data["open_position"]
        if pos is None:
            raise RuntimeError("No open position to close")

        qty = float(pos["quantity"])
        entry = float(pos["entry_price"])
        side = pos["side"]
        notional = qty * entry
        margin_used = float(pos["margin_used"])

        exit_slippage = exit_price * slippage_rate
        effective_exit = exit_price - exit_slippage if side == "long" else exit_price + exit_slippage

        gross = (effective_exit - entry) * qty if side == "long" else (entry - effective_exit) * qty
        close_fee = abs(effective_exit * qty) * fee_rate
        open_fee = float(pos.get("fees_paid", 0.0))
        net = gross - close_fee - open_fee

        self.data["balance"] += margin_used + net
        self.data["realized_pnl"] += net
        self.data["daily_realized_pnl"] += net
        self.data["consecutive_losses"] = self.data["consecutive_losses"] + 1 if net < 0 else 0

        trade = {
            **pos,
            "exit_price": round(effective_exit, 6),
            "exit_reason": exit_reason,
            "gross_pnl": round(gross, 6),
            "net_pnl": round(net, 6),
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "total_fees": round(open_fee + close_fee, 6),
        }
        self.data["history"] = [trade] + self.data["history"][:99]
        self.data["open_position"] = None
        self.data["equity"] = self.data["balance"]
        self.save()
        return trade

    def update_open_position(self, position: dict[str, Any]) -> None:
        self.data["open_position"] = position
        self.save()
