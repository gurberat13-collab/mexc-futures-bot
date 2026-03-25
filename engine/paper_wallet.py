from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from utils.helpers import iso_utc_now, load_json, save_json, utc_now


@dataclass
class Position:
    id: str
    symbol: str
    side: str
    entry_price: float
    initial_quantity: float
    quantity: float
    leverage: int
    stop_loss: float
    take_profit: float
    partial_take_profit_price: float
    partial_take_profit_pct: float
    partial_take_profit_taken: bool
    break_even_armed: bool
    trailing_active: bool
    trailing_activation_price: float
    trailing_gap_pct: float
    trailing_stop: float | None
    opened_at: str
    highest_price: float
    lowest_price: float
    fees_paid: float
    realized_partial_pnl: float
    margin_used: float
    reason: str
    score: int
    regime: str
    higher_timeframe_bias: str


class PaperWallet:
    def __init__(self, path: str, trades_path: str, starting_balance: float):
        self.path = path
        self.trades_path = trades_path
        self.data = load_json(
            path,
            {
                "balance": starting_balance,
                "equity": starting_balance,
                "realized_pnl": 0.0,
                "daily_realized_pnl": 0.0,
                "consecutive_losses": 0,
                "trades_today": 0,
                "day": utc_now().date().isoformat(),
                "open_positions": [],
                "history": [],
                "daily_stats": {
                    "closed_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "partial_closes": 0,
                },
            },
        )
        self._migrate_legacy_state(starting_balance)
        self.save()

    def _migrate_legacy_state(self, starting_balance: float) -> None:
        self.data.setdefault("balance", starting_balance)
        self.data.setdefault("equity", self.data["balance"])
        self.data.setdefault("realized_pnl", 0.0)
        self.data.setdefault("daily_realized_pnl", 0.0)
        self.data.setdefault("consecutive_losses", 0)
        self.data.setdefault("trades_today", 0)
        self.data.setdefault("day", utc_now().date().isoformat())
        self.data.setdefault("history", [])
        self.data.setdefault(
            "daily_stats",
            {"closed_trades": 0, "wins": 0, "losses": 0, "partial_closes": 0},
        )

        if "open_positions" not in self.data:
            legacy_pos = self.data.pop("open_position", None)
            self.data["open_positions"] = [legacy_pos] if legacy_pos else []

        for pos in self.data["open_positions"]:
            pos.setdefault("initial_quantity", pos.get("quantity", 0.0))
            pos.setdefault("partial_take_profit_price", pos.get("take_profit", 0.0))
            pos.setdefault("partial_take_profit_pct", 0.5)
            pos.setdefault("partial_take_profit_taken", False)
            pos.setdefault("break_even_armed", False)
            pos.setdefault("realized_partial_pnl", 0.0)
            pos.setdefault("margin_used", pos.get("margin_used", 0.0))
            pos.setdefault("score", 0)
            pos.setdefault("regime", "unknown")
            pos.setdefault("higher_timeframe_bias", "neutral")

    def rollover_if_needed(self) -> None:
        today = utc_now().date().isoformat()
        if self.data["day"] != today:
            self.data["day"] = today
            self.data["daily_realized_pnl"] = 0.0
            self.data["consecutive_losses"] = 0
            self.data["trades_today"] = 0
            self.data["daily_stats"] = {
                "closed_trades": 0,
                "wins": 0,
                "losses": 0,
                "partial_closes": 0,
            }
            self.save()

    @property
    def balance(self) -> float:
        return float(self.data["balance"])

    @property
    def open_positions(self) -> list[dict[str, Any]]:
        return list(self.data.get("open_positions", []))

    @property
    def open_position(self) -> dict[str, Any] | None:
        positions = self.open_positions
        return positions[0] if positions else None

    def save(self) -> None:
        save_json(self.path, self.data)
        save_json(self.trades_path, self.data.get("history", []))

    def can_open_new_trade(self, max_open_positions: int, max_trades_per_day: int) -> bool:
        self.rollover_if_needed()
        if max_open_positions <= 0:
            return False
        if len(self.open_positions) >= max_open_positions:
            return False
        if self.data["trades_today"] >= max_trades_per_day:
            return False
        return True

    def set_equity(self, equity: float) -> None:
        self.data["equity"] = equity
        self.save()

    def get_open_position(self, position_id: str) -> dict[str, Any] | None:
        for pos in self.data.get("open_positions", []):
            if pos["id"] == position_id:
                return pos
        return None

    def open_trade(self, position: Position) -> None:
        self.data["balance"] -= float(position.margin_used)
        self.data.setdefault("open_positions", []).append(asdict(position))
        self.data["trades_today"] += 1
        self.save()

    def update_open_position(self, position_id: str, position: dict[str, Any]) -> None:
        positions = self.data.get("open_positions", [])
        for idx, pos in enumerate(positions):
            if pos["id"] == position_id:
                positions[idx] = position
                self.save()
                return
        raise RuntimeError(f"Open position not found: {position_id}")

    def reduce_trade(
        self,
        position_id: str,
        exit_price: float,
        fraction: float,
        exit_reason: str,
        fee_rate: float,
        slippage_rate: float,
    ) -> dict[str, Any]:
        pos = self.get_open_position(position_id)
        if pos is None:
            raise RuntimeError("No open position to reduce")

        current_qty = float(pos["quantity"])
        if current_qty <= 0:
            raise RuntimeError("Position quantity is zero")

        ratio = min(max(fraction, 0.0), 1.0)
        qty_closed = current_qty * ratio
        if qty_closed <= 0:
            raise RuntimeError("Close quantity must be positive")

        entry = float(pos["entry_price"])
        side = pos["side"]
        margin_used = float(pos["margin_used"])
        margin_released = margin_used * ratio
        open_fee_alloc = float(pos.get("fees_paid", 0.0)) * ratio

        exit_slippage = exit_price * slippage_rate
        effective_exit = exit_price - exit_slippage if side == "long" else exit_price + exit_slippage
        gross = (effective_exit - entry) * qty_closed if side == "long" else (entry - effective_exit) * qty_closed
        close_fee = abs(effective_exit * qty_closed) * fee_rate
        net = gross - close_fee - open_fee_alloc

        self.data["balance"] += margin_released + net
        self.data["realized_pnl"] += net
        self.data["daily_realized_pnl"] += net

        pos["quantity"] = round(current_qty - qty_closed, 6)
        pos["margin_used"] = round(margin_used - margin_released, 6)
        pos["fees_paid"] = round(float(pos.get("fees_paid", 0.0)) - open_fee_alloc, 6)
        pos["realized_partial_pnl"] = round(float(pos.get("realized_partial_pnl", 0.0)) + net, 6)

        fully_closed = pos["quantity"] <= 1e-9
        if fully_closed:
            positions = self.data.get("open_positions", [])
            self.data["open_positions"] = [item for item in positions if item["id"] != position_id]
            total_position_net = float(pos.get("realized_partial_pnl", 0.0))
            self.data["consecutive_losses"] = self.data["consecutive_losses"] + 1 if total_position_net < 0 else 0
            self.data["daily_stats"]["closed_trades"] += 1
            if total_position_net >= 0:
                self.data["daily_stats"]["wins"] += 1
            else:
                self.data["daily_stats"]["losses"] += 1
        else:
            self.data["daily_stats"]["partial_closes"] += 1
            self.update_open_position(position_id, pos)

        trade = {
            "position_id": position_id,
            "symbol": pos["symbol"],
            "side": side,
            "entry_price": entry,
            "exit_price": round(effective_exit, 6),
            "quantity_closed": round(qty_closed, 6),
            "remaining_quantity": round(max(pos["quantity"], 0.0), 6),
            "exit_reason": exit_reason,
            "trade_type": "full_close" if fully_closed else "partial_close",
            "gross_pnl": round(gross, 6),
            "net_pnl": round(net, 6),
            "position_net_pnl": round(float(pos.get("realized_partial_pnl", 0.0)), 6),
            "closed_at": iso_utc_now(),
            "total_fees": round(open_fee_alloc + close_fee, 6),
            "opened_at": pos["opened_at"],
            "reason": pos["reason"],
        }
        self.data["history"] = [trade] + self.data["history"][:299]
        self.data["equity"] = self.data["balance"]
        self.save()
        return trade

    def close_trade(self, position_id: str, exit_price: float, exit_reason: str, fee_rate: float, slippage_rate: float) -> dict[str, Any]:
        return self.reduce_trade(position_id, exit_price, 1.0, exit_reason, fee_rate, slippage_rate)
