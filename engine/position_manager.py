from __future__ import annotations


class PositionManager:
    def __init__(self, config, wallet):
        self.cfg = config
        self.wallet = wallet

    def mark_equity(self, last_price: float) -> float:
        pos = self.wallet.open_position
        if pos is None:
            self.wallet.set_equity(self.wallet.balance)
            return self.wallet.balance

        qty = float(pos["quantity"])
        entry = float(pos["entry_price"])
        side = pos["side"]
        unrealized = (last_price - entry) * qty if side == "long" else (entry - last_price) * qty
        equity = self.wallet.balance + float(pos["margin_used"]) + unrealized
        self.wallet.set_equity(equity)
        return equity

    def update_and_check_exit(self, last_price: float) -> tuple[bool, str | None]:
        pos = self.wallet.open_position
        if pos is None:
            return False, None

        side = pos["side"]
        pos["highest_price"] = max(float(pos["highest_price"]), last_price)
        pos["lowest_price"] = min(float(pos["lowest_price"]), last_price)

        if not pos["trailing_active"]:
            if side == "long" and last_price >= float(pos["trailing_activation_price"]):
                pos["trailing_active"] = True
                pos["trailing_stop"] = last_price * (1 - float(pos["trailing_gap_pct"]))
            elif side == "short" and last_price <= float(pos["trailing_activation_price"]):
                pos["trailing_active"] = True
                pos["trailing_stop"] = last_price * (1 + float(pos["trailing_gap_pct"]))
        else:
            if side == "long":
                new_stop = float(pos["highest_price"]) * (1 - float(pos["trailing_gap_pct"]))
                pos["trailing_stop"] = max(float(pos["trailing_stop"]), new_stop)
            else:
                new_stop = float(pos["lowest_price"]) * (1 + float(pos["trailing_gap_pct"]))
                pos["trailing_stop"] = min(float(pos["trailing_stop"]), new_stop)

        self.wallet.update_open_position(pos)

        if side == "long":
            if last_price <= float(pos["stop_loss"]):
                return True, "stop_loss"
            if last_price >= float(pos["take_profit"]):
                return True, "take_profit"
            if pos["trailing_active"] and last_price <= float(pos["trailing_stop"]):
                return True, "trailing_stop"
        else:
            if last_price >= float(pos["stop_loss"]):
                return True, "stop_loss"
            if last_price <= float(pos["take_profit"]):
                return True, "take_profit"
            if pos["trailing_active"] and last_price >= float(pos["trailing_stop"]):
                return True, "trailing_stop"

        return False, None
