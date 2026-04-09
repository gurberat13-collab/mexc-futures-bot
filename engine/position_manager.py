from __future__ import annotations

from typing import Any


class PositionManager:
    def __init__(self, config, wallet):
        self.cfg = config
        self.wallet = wallet

    def mark_equity(self, price_by_symbol: dict[str, float]) -> float:
        open_positions = self.wallet.open_positions
        if not open_positions:
            self.wallet.set_equity(self.wallet.balance)
            return self.wallet.balance

        unrealized = 0.0
        margin_total = 0.0
        for pos in open_positions:
            last_price = price_by_symbol.get(pos["symbol"], float(pos["entry_price"]))
            qty = float(pos["quantity"])
            entry = float(pos["entry_price"])
            side = pos["side"]
            margin_total += float(pos["margin_used"])
            unrealized += (last_price - entry) * qty if side == "long" else (entry - last_price) * qty

        equity = self.wallet.balance + margin_total + unrealized
        self.wallet.set_equity(equity)
        return equity

    def update_positions(
        self,
        snapshots_by_symbol: dict[str, Any],
        intrabar_by_symbol: dict[str, dict[str, float]] | None = None,
    ) -> list[dict]:
        events: list[dict] = []
        intrabar_by_symbol = intrabar_by_symbol or {}
        # Snapshot: full close removes items from open_positions; mutating while iterating skips symbols.
        for original in list(self.wallet.open_positions):
            position_id = original["id"]
            snapshot = snapshots_by_symbol.get(original["symbol"])
            if snapshot is None:
                continue

            pos = dict(original)
            last_price = float(snapshot.last_price)
            bar = intrabar_by_symbol.get(original["symbol"], {})
            bar_high = float(bar.get("high", last_price))
            bar_low = float(bar.get("low", last_price))
            bar_close = float(bar.get("close", last_price))
            high_price = max(last_price, bar_high)
            low_price = min(last_price, bar_low)
            side = pos["side"]
            pos["highest_price"] = max(float(pos["highest_price"]), high_price)
            pos["lowest_price"] = min(float(pos["lowest_price"]), low_price)

            if not pos.get("partial_take_profit_taken", False):
                partial_hit = (
                    side == "long" and high_price >= float(pos["partial_take_profit_price"])
                ) or (
                    side == "short" and low_price <= float(pos["partial_take_profit_price"])
                )
                if partial_hit:
                    trade = self.wallet.reduce_trade(
                        position_id,
                        float(pos["partial_take_profit_price"]),
                        float(pos["partial_take_profit_pct"]),
                        "partial_take_profit",
                        self.cfg.fee_rate,
                        self.cfg.slippage_rate,
                    )
                    events.append({"kind": "partial_exit", "trade": trade})
                    pos = self.wallet.get_open_position(position_id)
                    if pos is None:
                        continue
                    pos["partial_take_profit_taken"] = True
                    if self.cfg.break_even_on_partial:
                        pos["break_even_armed"] = True
                        if side == "long":
                            pos["stop_loss"] = max(float(pos["stop_loss"]), float(pos["entry_price"]))
                        else:
                            pos["stop_loss"] = min(float(pos["stop_loss"]), float(pos["entry_price"]))

            if not pos.get("trailing_active", False):
                if side == "long" and high_price >= float(pos["trailing_activation_price"]):
                    pos["trailing_active"] = True
                    pos["trailing_stop"] = high_price * (1 - float(pos["trailing_gap_pct"]))
                elif side == "short" and low_price <= float(pos["trailing_activation_price"]):
                    pos["trailing_active"] = True
                    pos["trailing_stop"] = low_price * (1 + float(pos["trailing_gap_pct"]))
            else:
                if side == "long":
                    new_stop = float(pos["highest_price"]) * (1 - float(pos["trailing_gap_pct"]))
                    pos["trailing_stop"] = max(float(pos["trailing_stop"]), new_stop)
                else:
                    new_stop = float(pos["lowest_price"]) * (1 + float(pos["trailing_gap_pct"]))
                    pos["trailing_stop"] = min(float(pos["trailing_stop"]), new_stop)

            self.wallet.update_open_position(position_id, pos)

            exit_reason = None
            exit_price = bar_close
            if side == "long":
                if low_price <= float(pos["stop_loss"]):
                    exit_reason = "stop_loss"
                    exit_price = float(pos["stop_loss"])
                elif high_price >= float(pos["take_profit"]):
                    exit_reason = "take_profit"
                    exit_price = float(pos["take_profit"])
                elif pos.get("trailing_active") and low_price <= float(pos["trailing_stop"]):
                    exit_reason = "trailing_stop"
                    exit_price = float(pos["trailing_stop"])
            else:
                if high_price >= float(pos["stop_loss"]):
                    exit_reason = "stop_loss"
                    exit_price = float(pos["stop_loss"])
                elif low_price <= float(pos["take_profit"]):
                    exit_reason = "take_profit"
                    exit_price = float(pos["take_profit"])
                elif pos.get("trailing_active") and high_price >= float(pos["trailing_stop"]):
                    exit_reason = "trailing_stop"
                    exit_price = float(pos["trailing_stop"])

            if exit_reason:
                trade = self.wallet.close_trade(
                    position_id,
                    exit_price,
                    exit_reason,
                    self.cfg.fee_rate,
                    self.cfg.slippage_rate,
                )
                events.append({"kind": "full_exit", "trade": trade})

        return events
