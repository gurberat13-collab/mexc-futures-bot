from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from utils.helpers import save_json, utc_now


class ScannerEngine:
    def __init__(
        self,
        config,
        client,
        strategy,
        risk_manager,
        wallet,
        executor,
        position_manager,
        logger,
        notifier,
        market_stream=None,
    ):
        self.cfg = config
        self.client = client
        self.strategy = strategy
        self.risk = risk_manager
        self.wallet = wallet
        self.executor = executor
        self.position_manager = position_manager
        self.logger = logger
        self.notifier = notifier
        self.market_stream = market_stream

        self.running = False
        self.started_at = utc_now()
        self.last_trade_time: datetime | None = None
        self.last_entry_scan_time: datetime | None = None
        self.last_heartbeat_at: datetime | None = None
        self.last_error: str = ""
        self.tick_count = 0

        self.snapshot_history: dict[str, deque] = {
            symbol: deque(maxlen=self.cfg.open_interest_history_size) for symbol in self.cfg.symbols
        }
        self.last_scan_summary: dict[str, Any] = {
            "generated_at": None,
            "global_blocks": [],
            "symbols": {},
            "opened_positions": [],
        }
        self.daily_counters = self._new_daily_counters()
        self.report_zone = ZoneInfo(self.cfg.report_timezone)
        self.report_day = self._current_report_day()

    async def start(self) -> None:
        self.running = True
        self.logger.info("Scanner started")
        self._persist_state()
        await self.notifier("Futures paper bot baslatildi.")

    async def stop(self) -> None:
        self.running = False
        self.logger.info("Scanner stopped")
        self._persist_state()
        await self.notifier("Bot durduruldu. Yeni islem acmayacak.")

    def in_cooldown(self) -> bool:
        if self.last_trade_time is None:
            return False
        return utc_now() < self.last_trade_time + timedelta(minutes=self.cfg.cooldown_minutes)

    async def tick(self) -> None:
        self.tick_count += 1
        await self._maybe_send_daily_report()
        self.wallet.rollover_if_needed()

        open_symbols = sorted({pos["symbol"] for pos in self.wallet.open_positions})
        position_snapshots = await self._get_snapshots(open_symbols)
        if position_snapshots:
            intrabar_ranges = self._get_latest_bar_ranges(open_symbols)
            self.position_manager.mark_equity({symbol: snap.last_price for symbol, snap in position_snapshots.items()})
            events = self.position_manager.update_positions(position_snapshots, intrabar_by_symbol=intrabar_ranges)
            for event in events:
                await self._handle_position_event(event)
            if self.risk.daily_loss_breached(self.wallet) and self.wallet.open_positions:
                for event in self._flatten_positions(position_snapshots, "daily_loss_kill_switch"):
                    await self._handle_position_event(event)
        else:
            self.wallet.set_equity(self.wallet.balance)

        global_blocks = self._global_blocks()
        if not self.running or global_blocks or not self._entry_scan_due():
            self.last_scan_summary = {
                "generated_at": utc_now().isoformat(),
                "global_blocks": global_blocks,
                "symbols": self.last_scan_summary.get("symbols", {}),
                "opened_positions": [],
            }
            self._persist_state()
            await self._maybe_send_heartbeat()
            return

        symbol_debug: dict[str, Any] = {}
        primary_frames: dict[str, pd.DataFrame] = {}
        candidates: list[dict[str, Any]] = []

        for symbol in self.cfg.symbols:
            try:
                analysis = await self._analyze_symbol(symbol)
                symbol_debug[symbol] = analysis["diagnostics"]
                primary_frames[symbol] = analysis["primary_df"]
                for block in analysis["diagnostics"]["blocks"]:
                    self.daily_counters["blocks"][block] += 1
                if analysis["eligible"]:
                    self.daily_counters["signals"] += 1
                    candidates.append(analysis)
            except Exception as exc:
                self.last_error = str(exc)
                self.daily_counters["blocks"]["analysis_error"] += 1
                symbol_debug[symbol] = {
                    "symbol": symbol,
                    "eligible": False,
                    "blocks": ["analysis_error"],
                    "error": str(exc),
                }
                self.logger.exception("Analysis error for %s: %s", symbol, exc)

        candidates.sort(
            key=lambda item: (abs(item["signal"].score), item["signal"].adx_value, item["signal"].volume_ratio),
            reverse=True,
        )

        opened_positions: list[dict[str, Any]] = []
        reserved_symbols = {pos["symbol"] for pos in self.wallet.open_positions}
        for candidate in candidates:
            if not self.wallet.can_open_new_trade(self.cfg.max_open_positions, self.cfg.max_trades_per_day):
                break
            symbol = candidate["symbol"]
            if symbol in reserved_symbols:
                symbol_debug[symbol]["blocks"].append("already_open")
                continue

            corr_block = self._correlation_block(symbol, candidate["primary_df"], primary_frames, reserved_symbols)
            if corr_block is not None:
                symbol_debug[symbol]["eligible"] = False
                symbol_debug[symbol]["blocks"].append(corr_block)
                self.daily_counters["blocks"][corr_block] += 1
                continue

            signal = candidate["signal"]
            plan = self.risk.build_plan(
                symbol=symbol,
                side=signal.action,
                entry_price=candidate["snapshot"].last_price,
                atr_value=signal.atr_value,
                wallet_balance=self.wallet.balance,
            )
            edge_metrics = self._estimate_entry_edge(signal.action, candidate["snapshot"], plan)
            if edge_metrics.get("blocked_reason"):
                symbol_debug[symbol]["eligible"] = False
                symbol_debug[symbol]["blocks"].append(str(edge_metrics["blocked_reason"]))
                self.daily_counters["blocks"][str(edge_metrics["blocked_reason"])] += 1
                continue

            opened = self.executor.open_position(
                symbol,
                signal.action,
                candidate["snapshot"].last_price,
                plan,
                signal,
            )
            reserved_symbols.add(symbol)
            self.last_trade_time = utc_now()
            self.daily_counters["opened_trades"] += 1
            opened_positions.append(opened)

            msg = (
                "Yeni pozisyon acildi\n"
                f"{opened['symbol']} {opened['side']} {opened['quantity']}\n"
                f"Giris: {opened['entry_price']:.2f}\n"
                f"Partial TP: {opened['partial_take_profit_price']:.2f}\n"
                f"SL: {opened['stop_loss']:.2f} | TP: {opened['take_profit']:.2f}\n"
                f"Skor: {signal.score} | Sebep: {signal.reason}"
            )
            self.logger.info(msg.replace("\n", " | "))
            await self.notifier(msg)

        self.last_entry_scan_time = utc_now()
        self.daily_counters["entry_scans"] += 1
        self.last_scan_summary = {
            "generated_at": utc_now().isoformat(),
            "global_blocks": global_blocks,
            "symbols": symbol_debug,
            "opened_positions": opened_positions,
        }
        self._persist_state()
        await self._maybe_send_heartbeat()

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:
                self.last_error = str(exc)
                self.logger.exception("Tick error: %s", exc)
                await self.notifier(f"Bot hatasi: {exc}")
                self._persist_state()

            if self.market_stream and self.cfg.websocket_enabled:
                await self.market_stream.wait_for_update(timeout=self.cfg.scan_interval_seconds)
            else:
                await asyncio.sleep(self.cfg.scan_interval_seconds)

    async def debug_symbol(self, symbol: str) -> dict[str, Any]:
        symbol = symbol.upper()
        if not symbol.endswith("_USDT"):
            symbol = f"{symbol}_USDT"

        if symbol in self.last_scan_summary.get("symbols", {}):
            return self.last_scan_summary["symbols"][symbol]

        analysis = await self._analyze_symbol(symbol)
        return analysis["diagnostics"]

    def get_health_status(self) -> dict[str, Any]:
        ws_status = self.market_stream.status() if self.market_stream else {"enabled": False, "connected": False}
        stale_for = None
        if ws_status.get("last_message_at"):
            stale_for = max(int(datetime.now(timezone.utc).timestamp()) - int(ws_status["last_message_at"]), 0)

        return {
            "running": self.running,
            "uptime_seconds": int((utc_now() - self.started_at).total_seconds()),
            "tick_count": self.tick_count,
            "last_trade_time": self.last_trade_time.isoformat() if self.last_trade_time else None,
            "last_entry_scan_time": self.last_entry_scan_time.isoformat() if self.last_entry_scan_time else None,
            "last_error": self.last_error,
            "websocket": ws_status,
            "stale_for_seconds": stale_for,
            "rest_stats": dict(self.client.stats),
            "open_positions": len(self.wallet.open_positions),
            "daily_counters": {
                "entry_scans": self.daily_counters["entry_scans"],
                "signals": self.daily_counters["signals"],
                "opened_trades": self.daily_counters["opened_trades"],
                "partial_exits": self.daily_counters["partial_exits"],
                "full_exits": self.daily_counters["full_exits"],
                "blocks": dict(self.daily_counters["blocks"]),
            },
        }

    async def _analyze_symbol(self, symbol: str) -> dict[str, Any]:
        primary_df = self._closed_signal_frame(self.client.get_klines(symbol, self.cfg.timeframe, self.cfg.kline_limit))
        higher_df = self._closed_signal_frame(self.client.get_klines(symbol, self.cfg.higher_timeframe, self.cfg.higher_kline_limit))
        snapshot = await self._get_snapshot(symbol)

        last_candle_pct = abs((primary_df.iloc[-1]["close"] - primary_df.iloc[-1]["open"]) / primary_df.iloc[-1]["open"])
        blocks: list[str] = []

        if abs(snapshot.funding_rate) > self.cfg.funding_abs_limit:
            blocks.append("funding_limit")
        if snapshot.spread_pct > self.cfg.max_spread_pct:
            blocks.append("spread_limit")
        if last_candle_pct > self.cfg.max_last_candle_pct:
            blocks.append("last_candle_limit")

        signal = self.strategy.analyze(
            symbol,
            primary_df,
            snapshot=snapshot,
            higher_df=higher_df,
            market_context=self._market_context(symbol, snapshot),
        )

        blocks.extend(signal.blocked_by)
        if signal.action == "hold":
            blocks.append("strategy_hold")

        diagnostics = {
            "symbol": symbol,
            "eligible": not blocks and signal.action != "hold",
            "blocks": blocks,
            "action": signal.action,
            "score": signal.score,
            "bullish_votes": signal.bullish_votes,
            "bearish_votes": signal.bearish_votes,
            "directional_votes": signal.directional_votes,
            "opposing_votes": signal.opposing_votes,
            "signal_conflict_ratio": signal.signal_conflict_ratio,
            "reason": signal.reason,
            "last_price": snapshot.last_price,
            "funding_rate": snapshot.funding_rate,
            "spread_pct": snapshot.spread_pct,
            "hold_vol": snapshot.hold_vol,
            "hold_vol_ratio": signal.hold_vol_ratio,
            "snapshot_source": snapshot.source,
            "last_candle_pct": float(last_candle_pct),
            "regime": signal.regime,
            "adx": signal.adx_value,
            "atr_pct": signal.atr_pct,
            "higher_timeframe_bias": signal.higher_timeframe_bias,
            "higher_timeframe_confirmed": signal.higher_timeframe_confirmed,
            "volume_ratio": signal.volume_ratio,
            "market_structure": signal.market_structure,
            "vwap_distance_pct": signal.vwap_distance_pct,
            "volatility_expansion": signal.volatility_expansion,
            "breakout_up": signal.breakout_up,
            "breakout_down": signal.breakout_down,
            "last_candle_time": str(primary_df.iloc[-1]["time"]),
        }

        plan = None
        if diagnostics["eligible"]:
            plan = self.risk.build_plan(
                symbol=symbol,
                side=signal.action,
                entry_price=snapshot.last_price,
                atr_value=signal.atr_value,
                wallet_balance=self.wallet.balance,
            )
            edge_metrics = self._estimate_entry_edge(signal.action, snapshot, plan)
            diagnostics.update(edge_metrics)
            if edge_metrics.get("blocked_reason"):
                diagnostics["eligible"] = False
                diagnostics["blocks"].append(str(edge_metrics["blocked_reason"]))
                plan = None

        return {
            "symbol": symbol,
            "eligible": diagnostics["eligible"],
            "diagnostics": diagnostics,
            "signal": signal,
            "snapshot": snapshot,
            "primary_df": primary_df,
            "higher_df": higher_df,
            "plan": plan,
        }

    def _closed_signal_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        if len(frame) <= 1:
            raise RuntimeError("Insufficient closed candles for signal analysis")
        return frame.iloc[:-1].reset_index(drop=True)

    async def _get_snapshot(self, symbol: str):
        snapshot = self.market_stream.get_snapshot(symbol) if self.market_stream else None
        if snapshot is None:
            snapshot = self.client.get_ticker(symbol)
            if self.market_stream:
                self.market_stream.mark_rest_snapshot(snapshot)
        self._record_snapshot(symbol, snapshot)
        return snapshot

    async def _get_snapshots(self, symbols: list[str]) -> dict[str, Any]:
        snapshots: dict[str, Any] = {}
        for symbol in symbols:
            snapshots[symbol] = await self._get_snapshot(symbol)
        return snapshots

    def _get_latest_bar_ranges(self, symbols: list[str]) -> dict[str, dict[str, float]]:
        if not getattr(self.cfg, "position_intrabar_from_klines", True):
            return {}
        ranges: dict[str, dict[str, float]] = {}
        for symbol in symbols:
            try:
                frame = self.client.get_klines(symbol, self.cfg.timeframe, 2)
                if frame.empty:
                    continue
                row = frame.iloc[-1]
                ranges[symbol] = {
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
            except Exception as exc:
                self.logger.warning("Intrabar range unavailable for %s: %s", symbol, exc)
        return ranges

    def _estimate_entry_edge(self, side: str, snapshot: Any, plan: Any) -> dict[str, Any]:
        qty = float(getattr(plan, "quantity", 0.0) or 0.0)
        if qty <= 0:
            return {
                "expected_net_tp": 0.0,
                "expected_net_sl": 0.0,
                "expected_net_rr": 0.0,
                "expected_roundtrip_cost": 0.0,
                "blocked_reason": "cost_edge_limit",
            }

        bid = float(getattr(snapshot, "bid", 0.0) or 0.0)
        ask = float(getattr(snapshot, "ask", 0.0) or 0.0)
        last = float(getattr(snapshot, "last_price", 0.0) or 0.0)
        entry = ask if side == "long" and ask > 0 else bid if side == "short" and bid > 0 else last

        slippage = float(self.cfg.slippage_rate)
        fee_rate = float(self.cfg.fee_rate)
        tp = float(plan.take_profit)
        sl = float(plan.stop_loss)
        tp_exit = tp - (tp * slippage) if side == "long" else tp + (tp * slippage)
        sl_exit = sl - (sl * slippage) if side == "long" else sl + (sl * slippage)

        gross_tp = (tp_exit - entry) * qty if side == "long" else (entry - tp_exit) * qty
        gross_sl = (sl_exit - entry) * qty if side == "long" else (entry - sl_exit) * qty

        open_fee = abs(entry * qty) * fee_rate
        close_fee_tp = abs(tp_exit * qty) * fee_rate
        close_fee_sl = abs(sl_exit * qty) * fee_rate
        spread_cost = abs(ask - bid) * qty if bid > 0 and ask > 0 else 0.0

        net_tp = gross_tp - open_fee - close_fee_tp - spread_cost
        net_sl = gross_sl - open_fee - close_fee_sl - spread_cost
        net_rr = net_tp / abs(net_sl) if net_sl < 0 else (999.0 if net_tp > 0 else 0.0)

        min_rr = float(getattr(self.cfg, "min_expected_net_rr", 0.0))
        min_profit_pct = float(getattr(self.cfg, "min_expected_net_profit_pct", 0.0))
        min_profit_abs = max(float(self.wallet.balance) * min_profit_pct, 0.0)
        blocked_reason = None
        if net_tp <= 0 or net_rr < min_rr or net_tp < min_profit_abs:
            blocked_reason = "cost_edge_limit"

        return {
            "expected_net_tp": round(net_tp, 6),
            "expected_net_sl": round(net_sl, 6),
            "expected_net_rr": round(net_rr, 4),
            "expected_roundtrip_cost": round(open_fee + close_fee_tp + spread_cost, 6),
            "blocked_reason": blocked_reason,
        }

    def _record_snapshot(self, symbol: str, snapshot) -> None:
        history = self.snapshot_history.setdefault(symbol, deque(maxlen=self.cfg.open_interest_history_size))
        if not history or history[-1]["timestamp"] != snapshot.timestamp or history[-1]["hold_vol"] != snapshot.hold_vol:
            history.append({"timestamp": snapshot.timestamp, "hold_vol": snapshot.hold_vol, "price": snapshot.last_price})

    def _market_context(self, symbol: str, snapshot) -> dict[str, Any]:
        history = self.snapshot_history.get(symbol, deque())
        baseline = [item["hold_vol"] for item in history if item["hold_vol"] > 0]
        average_hold = (sum(baseline) / len(baseline)) if baseline else 0.0
        hold_vol_ratio = (snapshot.hold_vol / average_hold) if average_hold else 0.0
        return {
            "oi_supported": len(baseline) >= 3 and average_hold > 0,
            "hold_vol_ratio": hold_vol_ratio,
        }

    def _global_blocks(self) -> list[str]:
        blocks: list[str] = []
        if not self.running:
            blocks.append("scanner_stopped")
        if self.in_cooldown():
            blocks.append("cooldown")
        if self.risk.daily_loss_breached(self.wallet):
            blocks.append("daily_loss_limit")
        if self.risk.consecutive_losses_breached(self.wallet):
            blocks.append("consecutive_loss_limit")
        if not self.wallet.can_open_new_trade(self.cfg.max_open_positions, self.cfg.max_trades_per_day):
            blocks.append("position_or_trade_limit")
        return blocks

    def _entry_scan_due(self) -> bool:
        if self.last_entry_scan_time is None:
            return True
        return utc_now() >= self.last_entry_scan_time + timedelta(seconds=self.cfg.scan_interval_seconds)

    def _correlation_block(
        self,
        candidate_symbol: str,
        candidate_df: pd.DataFrame,
        primary_frames: dict[str, pd.DataFrame],
        reserved_symbols: set[str],
    ) -> str | None:
        open_symbols = {pos["symbol"] for pos in self.wallet.open_positions}
        compare_symbols = sorted((reserved_symbols | open_symbols) - {candidate_symbol})
        if not compare_symbols:
            return None

        candidate_returns = candidate_df["close"].pct_change().tail(self.cfg.min_correlation_lookback)
        if candidate_returns.dropna().shape[0] < max(self.cfg.min_correlation_lookback // 2, 10):
            return None

        for other_symbol in compare_symbols:
            other_df = primary_frames.get(other_symbol)
            if other_df is None:
                other_df = self.client.get_klines(other_symbol, self.cfg.timeframe, self.cfg.kline_limit)
                primary_frames[other_symbol] = other_df
            other_returns = other_df["close"].pct_change().tail(self.cfg.min_correlation_lookback)
            joined = pd.concat([candidate_returns, other_returns], axis=1).dropna()
            if len(joined) < max(self.cfg.min_correlation_lookback // 2, 10):
                continue
            corr = float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))
            if abs(corr) >= self.cfg.max_symbol_correlation:
                return f"correlation_limit:{other_symbol}:{corr:.2f}"
        return None

    async def _handle_position_event(self, event: dict[str, Any]) -> None:
        trade = event["trade"]
        if event["kind"] == "partial_exit":
            self.daily_counters["partial_exits"] += 1
            msg = (
                "Kismi kar alindi\n"
                f"{trade['symbol']} {trade['side']}\n"
                f"Miktar: {trade['quantity_closed']}\n"
                f"Net PnL: {trade['net_pnl']:.2f} USDT\n"
                f"Kalan: {trade['remaining_quantity']}"
            )
        else:
            self.daily_counters["full_exits"] += 1
            msg = (
                "Pozisyon kapandi\n"
                f"{trade['symbol']} {trade['side']}\n"
                f"Sebep: {trade['exit_reason']}\n"
                f"Net PnL: {trade['net_pnl']:.2f} USDT\n"
                f"Pozisyon toplam net: {trade['position_net_pnl']:.2f} USDT\n"
                f"Bakiye: {self.wallet.balance:.2f} USDT"
            )
        self.logger.info(msg.replace("\n", " | "))
        await self.notifier(msg)

    def _flatten_positions(self, snapshots_by_symbol: dict[str, Any], exit_reason: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for pos in list(self.wallet.open_positions):
            snapshot = snapshots_by_symbol.get(pos["symbol"])
            if snapshot is None:
                continue
            trade = self.wallet.close_trade(
                pos["id"],
                float(snapshot.last_price),
                exit_reason,
                self.cfg.fee_rate,
                self.cfg.slippage_rate,
            )
            events.append({"kind": "full_exit", "trade": trade})
        return events

    async def _maybe_send_heartbeat(self) -> None:
        if self.cfg.heartbeat_interval_minutes <= 0:
            return
        now = utc_now()
        if self.last_heartbeat_at and now < self.last_heartbeat_at + timedelta(minutes=self.cfg.heartbeat_interval_minutes):
            return
        health = self.get_health_status()
        msg = (
            "Heartbeat\n"
            f"Running: {health['running']}\n"
            f"Open positions: {health['open_positions']}\n"
            f"Entry scans: {health['daily_counters']['entry_scans']}\n"
            f"Signals: {health['daily_counters']['signals']}\n"
            f"Opened: {health['daily_counters']['opened_trades']}\n"
            f"WS connected: {health['websocket'].get('connected', False)}\n"
            f"Last error: {health['last_error'] or 'none'}"
        )
        self.last_heartbeat_at = now
        await self.notifier(msg)

    async def _maybe_send_daily_report(self) -> None:
        current_day = self._current_report_day()
        if current_day == self.report_day:
            return

        if self.cfg.daily_report_enabled:
            stats = self.wallet.data.get("daily_stats", {})
            msg = (
                f"Gun sonu raporu {self.report_day}\n"
                f"Entry scans: {self.daily_counters['entry_scans']}\n"
                f"Signals: {self.daily_counters['signals']}\n"
                f"Opened trades: {self.daily_counters['opened_trades']}\n"
                f"Partial exits: {self.daily_counters['partial_exits']}\n"
                f"Full exits: {self.daily_counters['full_exits']}\n"
                f"Closed trades: {stats.get('closed_trades', 0)}\n"
                f"Wins/Losses: {stats.get('wins', 0)}/{stats.get('losses', 0)}\n"
                f"Daily PnL: {self.wallet.data.get('daily_realized_pnl', 0.0):.2f} USDT\n"
                f"Blocks: {dict(self.daily_counters['blocks'])}"
            )
            await self.notifier(msg)

        self.report_day = current_day
        self.daily_counters = self._new_daily_counters()

    def _current_report_day(self) -> str:
        return datetime.now(self.report_zone).date().isoformat()

    def _new_daily_counters(self) -> dict[str, Any]:
        return {
            "entry_scans": 0,
            "signals": 0,
            "opened_trades": 0,
            "partial_exits": 0,
            "full_exits": 0,
            "blocks": defaultdict(int),
        }

    def _persist_state(self) -> None:
        ws_status = self.market_stream.status() if self.market_stream else {"enabled": False, "connected": False}
        state = {
            "running": self.running,
            "started_at": self.started_at.isoformat(),
            "last_trade_time": self.last_trade_time.isoformat() if self.last_trade_time else None,
            "last_entry_scan_time": self.last_entry_scan_time.isoformat() if self.last_entry_scan_time else None,
            "last_error": self.last_error,
            "tick_count": self.tick_count,
            "open_positions": len(self.wallet.open_positions),
            "daily_counters": {
                "entry_scans": self.daily_counters["entry_scans"],
                "signals": self.daily_counters["signals"],
                "opened_trades": self.daily_counters["opened_trades"],
                "partial_exits": self.daily_counters["partial_exits"],
                "full_exits": self.daily_counters["full_exits"],
                "blocks": dict(self.daily_counters["blocks"]),
            },
            "websocket": ws_status,
            "rest_stats": dict(self.client.stats),
            "last_scan_summary": self.last_scan_summary,
        }
        save_json(self.cfg.state_path, state)
