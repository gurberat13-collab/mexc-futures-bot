from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional


class ScannerEngine:
    def __init__(self, config, client, strategy, risk_manager, wallet, executor, position_manager, logger, notifier):
        self.cfg = config
        self.client = client
        self.strategy = strategy
        self.risk = risk_manager
        self.wallet = wallet
        self.executor = executor
        self.position_manager = position_manager
        self.logger = logger
        self.notifier = notifier
        self.running = False
        self.last_trade_time: datetime | None = None

    async def start(self) -> None:
        self.running = True
        self.logger.info("Scanner started")
        await self.notifier("🤖 Futures paper bot başlatıldı.")

    async def stop(self) -> None:
        self.running = False
        self.logger.info("Scanner stopped")
        await self.notifier("⏹ Bot durduruldu. Yeni işlem açmayacak.")

    def in_cooldown(self) -> bool:
        if self.last_trade_time is None:
            return False
        return datetime.now(timezone.utc) < self.last_trade_time + timedelta(minutes=self.cfg.cooldown_minutes)

    async def tick(self) -> None:
        self.wallet.rollover_if_needed()
        if self.wallet.open_position:
            symbol = self.wallet.open_position["symbol"]
            snapshot = self.client.get_ticker(symbol)
            self.position_manager.mark_equity(snapshot.last_price)
            should_exit, exit_reason = self.position_manager.update_and_check_exit(snapshot.last_price)
            if should_exit and exit_reason:
                trade = self.wallet.close_trade(snapshot.last_price, exit_reason, self.cfg.fee_rate, self.cfg.slippage_rate)
                self.last_trade_time = datetime.now(timezone.utc)
                msg = (
                    f"🔔 Pozisyon kapandı\n"
                    f"{trade['symbol']} {trade['side']}\n"
                    f"Sebep: {exit_reason}\n"
                    f"Net PnL: {trade['net_pnl']:.2f} USDT\n"
                    f"Bakiye: {self.wallet.balance:.2f} USDT"
                )
                self.logger.info(msg.replace("\n", " | "))
                await self.notifier(msg)
            return

        if not self.running:
            return
        if self.in_cooldown():
            return
        if self.risk.daily_loss_breached(self.wallet):
            return
        if self.risk.consecutive_losses_breached(self.wallet):
            return
        if not self.wallet.can_open_new_trade(self.cfg.max_open_positions, self.cfg.max_trades_per_day):
            return

        best_signal = None
        best_snapshot = None

        for symbol in self.cfg.symbols:
            df = self.client.get_klines(symbol, self.cfg.timeframe, self.cfg.kline_limit)
            snapshot = self.client.get_ticker(symbol)

            if abs(snapshot.funding_rate) > self.cfg.funding_abs_limit:
                continue
            if snapshot.spread_pct > self.cfg.max_spread_pct:
                continue

            last_candle_pct = abs((df.iloc[-1]["close"] - df.iloc[-1]["open"]) / df.iloc[-1]["open"])
            if last_candle_pct > self.cfg.max_last_candle_pct:
                continue

            signal = self.strategy.analyze(symbol, df)
            if signal.action == "hold":
                continue
            if best_signal is None or abs(signal.score) > abs(best_signal.score):
                best_signal = signal
                best_snapshot = snapshot

        if best_signal and best_snapshot:
            plan = self.risk.build_plan(
                symbol=best_signal.symbol,
                side=best_signal.action,
                entry_price=best_snapshot.last_price,
                atr_value=best_signal.atr_value,
                wallet_balance=self.wallet.balance,
            )
            opened = self.executor.open_position(
                best_signal.symbol,
                best_signal.action,
                best_snapshot.last_price,
                plan,
                best_signal.reason,
            )
            self.last_trade_time = datetime.now(timezone.utc)
            msg = (
                f"🚀 Yeni pozisyon açıldı\n"
                f"{opened['symbol']} {opened['side']} {opened['quantity']}\n"
                f"Giriş: {opened['entry_price']:.2f}\n"
                f"SL: {opened['stop_loss']:.2f} | TP: {opened['take_profit']:.2f}\n"
                f"Skor: {best_signal.score} | Sebep: {best_signal.reason}"
            )
            self.logger.info(msg.replace("\n", " | "))
            await self.notifier(msg)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:
                self.logger.exception("Tick error: %s", exc)
                await self.notifier(f"⚠️ Bot hatası: {exc}")
            await asyncio.sleep(self.cfg.scan_interval_seconds)
