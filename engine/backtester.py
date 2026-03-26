from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class BacktestResult:
    symbol: str
    bars: int
    closed_trades: int
    wins: int
    losses: int
    total_net_pnl: float
    return_pct: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float
    expectancy: float
    final_balance: float
    guardrail_blocks: dict[str, int] = field(default_factory=dict)


class Backtester:
    def __init__(self, config, client, strategy, risk, logger):
        self.cfg = config
        self.client = client
        self.strategy = strategy
        self.risk = risk
        self.logger = logger

    def run(self, symbol: str, bars: int | None = None) -> tuple[BacktestResult, list[dict[str, Any]]]:
        bars = bars or self.cfg.backtest_bars
        primary_df = self.client.get_klines(symbol, self.cfg.timeframe, max(bars, self.cfg.backtest_warmup_bars + 50))
        higher_df = self.client.get_klines(symbol, self.cfg.higher_timeframe, self.cfg.higher_kline_limit)
        funding_timeline = self._load_funding_timeline(symbol)

        balance = float(self.cfg.starting_balance)
        peak_equity = balance
        max_drawdown_pct = 0.0
        position: dict[str, Any] | None = None
        completed_trades: list[dict[str, Any]] = []
        guardrail_blocks: defaultdict[str, int] = defaultdict(int)
        current_day: str | None = None
        daily_realized_pnl = 0.0
        consecutive_losses = 0
        trades_today = 0
        last_trade_time: pd.Timestamp | None = None
        funding_idx = 0
        current_funding_rate = 0.0
        pending_entry: dict[str, Any] | None = None

        for idx in range(self.cfg.backtest_warmup_bars, len(primary_df)):
            current = primary_df.iloc[idx]
            current_time = current["time"]
            open_price = float(current["open"])
            close_price = float(current["close"])
            high_price = float(current["high"])
            low_price = float(current["low"])
            trade_day = current_time.date().isoformat()
            if current_day != trade_day:
                current_day = trade_day
                daily_realized_pnl = 0.0
                consecutive_losses = 0
                trades_today = 0

            current_ms = int(current_time.timestamp() * 1000)
            while funding_idx < len(funding_timeline) and funding_timeline[funding_idx][0] <= current_ms:
                current_funding_rate = funding_timeline[funding_idx][1]
                funding_idx += 1

            if pending_entry is not None and position is None:
                global_block = self._entry_block(
                    current_time,
                    last_trade_time,
                    daily_realized_pnl,
                    consecutive_losses,
                    trades_today,
                )
                if global_block is not None:
                    guardrail_blocks[global_block] += 1
                elif abs(current_funding_rate) > self.cfg.funding_abs_limit:
                    guardrail_blocks["funding_limit"] += 1
                else:
                    position = self._open_backtest_position(open_price, pending_entry["signal"], balance)
                    balance -= float(position["margin_used"])
                    trades_today += 1
                    last_trade_time = current_time
                pending_entry = None

            if position is not None:
                events, position, balance = self._process_bar(position, high_price, low_price, close_price, balance)
                completed_trades.extend(events)
                for event in events:
                    daily_realized_pnl += float(event["net_pnl"])
                    if event["trade_type"] == "full_close":
                        consecutive_losses = consecutive_losses + 1 if float(event["position_net_pnl"]) < 0 else 0

            if position is None and idx + 1 < len(primary_df):
                primary_slice = primary_df.iloc[: idx + 1]
                higher_slice = higher_df[higher_df["time"] <= current_time].tail(self.cfg.higher_kline_limit)
                signal = self.strategy.analyze(
                    symbol,
                    primary_slice,
                    higher_df=higher_slice,
                    market_context={"oi_supported": False, "hold_vol_ratio": 0.0},
                )
                last_candle_pct = abs((primary_slice.iloc[-1]["close"] - primary_slice.iloc[-1]["open"]) / primary_slice.iloc[-1]["open"])
                if signal.action != "hold" and signal.regime_ok and last_candle_pct <= self.cfg.max_last_candle_pct:
                    pending_entry = {"signal": signal}

            equity = balance
            if position is not None:
                equity += float(position["margin_used"])
                if position["side"] == "long":
                    equity += (close_price - float(position["entry_price"])) * float(position["quantity"])
                else:
                    equity += (float(position["entry_price"]) - close_price) * float(position["quantity"])

            peak_equity = max(peak_equity, equity)
            if peak_equity > 0:
                max_drawdown_pct = max(max_drawdown_pct, ((peak_equity - equity) / peak_equity) * 100)

        position_summaries = [trade for trade in completed_trades if trade["trade_type"] == "full_close"]
        closed_trades = len(position_summaries)
        wins = sum(1 for trade in position_summaries if trade["position_net_pnl"] >= 0)
        losses = closed_trades - wins
        total_net_pnl = sum(trade["position_net_pnl"] for trade in position_summaries)
        gross_profit = sum(trade["position_net_pnl"] for trade in position_summaries if trade["position_net_pnl"] > 0)
        gross_loss = abs(sum(trade["position_net_pnl"] for trade in position_summaries if trade["position_net_pnl"] < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else gross_profit
        expectancy = total_net_pnl / closed_trades if closed_trades else 0.0
        final_balance = balance
        if position is not None:
            last_close = float(primary_df.iloc[-1]["close"])
            unrealized = (
                (last_close - float(position["entry_price"])) * float(position["quantity"])
                if position["side"] == "long"
                else (float(position["entry_price"]) - last_close) * float(position["quantity"])
            )
            final_balance += float(position["margin_used"]) + unrealized
        return_pct = ((final_balance - self.cfg.starting_balance) / self.cfg.starting_balance) * 100
        win_rate_pct = (wins / closed_trades) * 100 if closed_trades else 0.0

        result = BacktestResult(
            symbol=symbol,
            bars=len(primary_df),
            closed_trades=closed_trades,
            wins=wins,
            losses=losses,
            total_net_pnl=round(total_net_pnl, 4),
            return_pct=round(return_pct, 2),
            max_drawdown_pct=round(max_drawdown_pct, 2),
            win_rate_pct=round(win_rate_pct, 2),
            profit_factor=round(profit_factor, 2),
            expectancy=round(expectancy, 4),
            final_balance=round(final_balance, 2),
            guardrail_blocks=dict(guardrail_blocks),
        )
        return result, completed_trades

    def _open_backtest_position(self, entry_price: float, signal: Any, balance: float) -> dict[str, Any]:
        plan = self.risk.build_plan("BACKTEST", signal.action, entry_price, signal.atr_value, balance)
        effective_entry = (
            entry_price + (entry_price * self.cfg.slippage_rate)
            if signal.action == "long"
            else entry_price - (entry_price * self.cfg.slippage_rate)
        )
        return {
            "symbol": getattr(signal, "symbol", "BACKTEST"),
            "side": signal.action,
            "entry_price": effective_entry,
            "quantity": plan.quantity,
            "initial_quantity": plan.quantity,
            "margin_used": plan.margin_used,
            "fees_paid": plan.estimated_fee,
            "stop_loss": plan.stop_loss,
            "take_profit": plan.take_profit,
            "partial_take_profit_price": plan.partial_take_profit_price,
            "partial_take_profit_pct": plan.partial_take_profit_pct,
            "partial_take_profit_taken": False,
            "break_even_armed": False,
            "trailing_active": False,
            "trailing_activation_price": plan.trailing_activation_price,
            "trailing_gap_pct": plan.trailing_gap_pct,
            "trailing_stop": None,
            "highest_price": effective_entry,
            "lowest_price": effective_entry,
            "realized_partial_pnl": 0.0,
        }

    def _entry_block(
        self,
        current_time: pd.Timestamp,
        last_trade_time: pd.Timestamp | None,
        daily_realized_pnl: float,
        consecutive_losses: int,
        trades_today: int,
    ) -> str | None:
        if last_trade_time is not None and current_time < last_trade_time + pd.Timedelta(minutes=self.cfg.cooldown_minutes):
            return "cooldown"
        if daily_realized_pnl <= -(self.cfg.starting_balance * self.cfg.daily_loss_limit_pct):
            return "daily_loss_limit"
        if consecutive_losses >= self.cfg.max_consecutive_losses:
            return "consecutive_loss_limit"
        if trades_today >= self.cfg.max_trades_per_day:
            return "position_or_trade_limit"
        return None

    def _load_funding_timeline(self, symbol: str) -> list[tuple[int, float]]:
        if self.cfg.funding_abs_limit <= 0:
            return []
        if not hasattr(self.client, "get_funding_rate_history"):
            return []
        try:
            raw_history = self.client.get_funding_rate_history(symbol, page_size=1000)
        except Exception as exc:
            self.logger.warning("Funding history unavailable for %s: %s", symbol, exc)
            return []

        timeline: list[tuple[int, float]] = []
        for item in raw_history:
            try:
                settle_time = int(item.get("settleTime", 0) or 0)
                funding_rate = float(item.get("fundingRate", 0.0) or 0.0)
            except (TypeError, ValueError, AttributeError):
                continue
            timeline.append((settle_time, funding_rate))
        timeline.sort(key=lambda row: row[0])
        return timeline

    def _process_bar(self, position: dict[str, Any], high_price: float, low_price: float, close_price: float, balance: float):
        side = position["side"]
        position["highest_price"] = max(float(position["highest_price"]), high_price)
        position["lowest_price"] = min(float(position["lowest_price"]), low_price)
        events: list[dict[str, Any]] = []

        if not position["partial_take_profit_taken"]:
            partial_hit = (
                side == "long" and high_price >= float(position["partial_take_profit_price"])
            ) or (
                side == "short" and low_price <= float(position["partial_take_profit_price"])
            )
            if partial_hit:
                event, position, balance = self._close_fraction(
                    position,
                    float(position["partial_take_profit_price"]),
                    float(position["partial_take_profit_pct"]),
                    "partial_take_profit",
                    balance,
                )
                events.append(event)
                if position is None:
                    return events, None, balance
                position["partial_take_profit_taken"] = True
                if self.cfg.break_even_on_partial:
                    position["break_even_armed"] = True
                    if side == "long":
                        position["stop_loss"] = max(float(position["stop_loss"]), float(position["entry_price"]))
                    else:
                        position["stop_loss"] = min(float(position["stop_loss"]), float(position["entry_price"]))

        if not position["trailing_active"]:
            if side == "long" and high_price >= float(position["trailing_activation_price"]):
                position["trailing_active"] = True
                position["trailing_stop"] = high_price * (1 - float(position["trailing_gap_pct"]))
            elif side == "short" and low_price <= float(position["trailing_activation_price"]):
                position["trailing_active"] = True
                position["trailing_stop"] = low_price * (1 + float(position["trailing_gap_pct"]))
        else:
            if side == "long":
                position["trailing_stop"] = max(float(position["trailing_stop"]), float(position["highest_price"]) * (1 - float(position["trailing_gap_pct"])))
            else:
                position["trailing_stop"] = min(float(position["trailing_stop"]), float(position["lowest_price"]) * (1 + float(position["trailing_gap_pct"])))

        exit_reason = None
        exit_price = close_price
        if side == "long":
            if low_price <= float(position["stop_loss"]):
                exit_reason = "stop_loss"
                exit_price = float(position["stop_loss"])
            elif high_price >= float(position["take_profit"]):
                exit_reason = "take_profit"
                exit_price = float(position["take_profit"])
            elif position["trailing_active"] and low_price <= float(position["trailing_stop"]):
                exit_reason = "trailing_stop"
                exit_price = float(position["trailing_stop"])
        else:
            if high_price >= float(position["stop_loss"]):
                exit_reason = "stop_loss"
                exit_price = float(position["stop_loss"])
            elif low_price <= float(position["take_profit"]):
                exit_reason = "take_profit"
                exit_price = float(position["take_profit"])
            elif position["trailing_active"] and high_price >= float(position["trailing_stop"]):
                exit_reason = "trailing_stop"
                exit_price = float(position["trailing_stop"])

        if exit_reason:
            event, _, balance = self._close_fraction(position, exit_price, 1.0, exit_reason, balance)
            events.append(event)
            return events, None, balance
        return events, position, balance

    def _close_fraction(self, position: dict[str, Any], exit_price: float, fraction: float, reason: str, balance: float):
        current_qty = float(position["quantity"])
        qty_closed = current_qty * fraction
        entry = float(position["entry_price"])
        side = position["side"]
        margin_release = float(position["margin_used"]) * fraction
        open_fee_alloc = float(position["fees_paid"]) * fraction
        exit_slippage = exit_price * self.cfg.slippage_rate
        effective_exit = exit_price - exit_slippage if side == "long" else exit_price + exit_slippage
        close_fee = abs(effective_exit * qty_closed) * self.cfg.fee_rate
        gross = (effective_exit - entry) * qty_closed if side == "long" else (entry - effective_exit) * qty_closed
        net = gross - close_fee - open_fee_alloc

        balance += margin_release + net
        position["quantity"] = round(current_qty - qty_closed, 8)
        position["margin_used"] = round(float(position["margin_used"]) - margin_release, 8)
        position["fees_paid"] = round(float(position["fees_paid"]) - open_fee_alloc, 8)
        position["realized_partial_pnl"] = round(float(position["realized_partial_pnl"]) + net, 8)

        fully_closed = position["quantity"] <= 1e-9
        event = {
            "symbol": position["symbol"],
            "side": side,
            "trade_type": "full_close" if fully_closed else "partial_close",
            "exit_reason": reason,
            "quantity_closed": round(qty_closed, 6),
            "remaining_quantity": round(max(position["quantity"], 0.0), 6),
            "net_pnl": round(net, 6),
            "position_net_pnl": round(float(position["realized_partial_pnl"]), 6),
        }
        if fully_closed:
            return event, None, balance
        return event, position, balance
