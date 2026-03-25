from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


class TelegramController:
    def __init__(self, config, scanner, wallet, client, strategy, backtester, logger):
        self.cfg = config
        self.scanner = scanner
        self.wallet = wallet
        self.client = client
        self.strategy = strategy
        self.backtester = backtester
        self.logger = logger
        self.app = Application.builder().token(self.cfg.telegram_token).build()
        self.app.add_handler(CommandHandler("start", self.start_cmd))
        self.app.add_handler(CommandHandler("baslat", self.baslat_cmd))
        self.app.add_handler(CommandHandler("durdur", self.durdur_cmd))
        self.app.add_handler(CommandHandler("durum", self.durum_cmd))
        self.app.add_handler(CommandHandler("bakiye", self.bakiye_cmd))
        self.app.add_handler(CommandHandler("gecmis", self.gecmis_cmd))
        self.app.add_handler(CommandHandler("analiz", self.analiz_cmd))
        self.app.add_handler(CommandHandler("ayar", self.ayar_cmd))
        self.app.add_handler(CommandHandler("debug", self.debug_cmd))
        self.app.add_handler(CommandHandler("nedenislem", self.debug_cmd))
        self.app.add_handler(CommandHandler("health", self.health_cmd))
        self.app.add_handler(CommandHandler("backtest", self.backtest_cmd))
        self.app.add_handler(CommandHandler("gunsonu", self.daily_report_cmd))

    async def notify(self, text: str) -> None:
        if self.cfg.telegram_chat_id:
            await self.app.bot.send_message(chat_id=self.cfg.telegram_chat_id, text=text)

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "MEXC Futures Paper Bot\n"
            "Komutlar:\n"
            "/baslat\n"
            "/durdur\n"
            "/durum\n"
            "/bakiye\n"
            "/gecmis\n"
            "/analiz BTC\n"
            "/debug BTC\n"
            "/health\n"
            "/backtest BTC 800\n"
            "/gunsonu\n"
            "/ayar"
        )
        await update.message.reply_text(text)

    async def baslat_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.scanner.start()
        await update.message.reply_text("Bot aktif. Yeni sinyal ariyor.")

    async def durdur_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.scanner.stop()
        await update.message.reply_text("Bot pasif. Acik pozisyonlari yonetmeye devam eder.")

    async def durum_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        positions = self.wallet.open_positions
        lines = [f"Bot: {'AKTIF' if self.scanner.running else 'PASIF'}", f"Acik pozisyon: {len(positions)}"]
        if positions:
            for pos in positions[:5]:
                lines.append(
                    f"{pos['symbol']} {pos['side']} qty={pos['quantity']} entry={float(pos['entry_price']):.2f} "
                    f"SL={float(pos['stop_loss']):.2f} TP={float(pos['take_profit']):.2f}"
                )
        else:
            lines.append("Acik pozisyon yok.")
        await update.message.reply_text("\n".join(lines))

    async def bakiye_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.wallet.rollover_if_needed()
        stats = self.wallet.data.get("daily_stats", {})
        text = (
            "Paper Wallet\n"
            f"Bakiye: {self.wallet.data['balance']:.2f} USDT\n"
            f"Equity: {self.wallet.data['equity']:.2f} USDT\n"
            f"Toplam PnL: {self.wallet.data['realized_pnl']:.2f} USDT\n"
            f"Gunluk PnL: {self.wallet.data['daily_realized_pnl']:.2f} USDT\n"
            f"Bugun kapanan islem: {stats.get('closed_trades', 0)}\n"
            f"Win/Loss: {stats.get('wins', 0)}/{stats.get('losses', 0)}"
        )
        await update.message.reply_text(text)

    async def gecmis_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        history = self.wallet.data.get("history", [])[:8]
        if not history:
            await update.message.reply_text("Henuz kapanmis islem yok.")
            return
        lines = ["Son islemler"]
        for trade in history:
            lines.append(
                f"{trade['symbol']} {trade['side']} | {trade['trade_type']} | {trade['exit_reason']} | "
                f"net={trade['net_pnl']:.2f} | toplam={trade.get('position_net_pnl', trade['net_pnl']):.2f}"
            )
        await update.message.reply_text("\n".join(lines))

    async def analiz_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.message.reply_text("Kullanim: /analiz BTC")
            return
        symbol = self._normalize_symbol(context.args[0])
        debug = await self.scanner.debug_symbol(symbol)
        text = (
            f"{debug['symbol']}\n"
            f"Aksiyon: {debug.get('action', 'hold').upper()}\n"
            f"Skor: {debug.get('score', 0)}\n"
            f"Regime: {debug.get('regime', 'unknown')}\n"
            f"HTF: {debug.get('higher_timeframe_bias', 'neutral')}\n"
            f"Funding: {debug.get('funding_rate', 0.0):.5f}\n"
            f"Spread: {debug.get('spread_pct', 0.0):.5f}\n"
            f"Vol Ratio: {debug.get('volume_ratio', 0.0):.2f}\n"
            f"VWAP Dist: {debug.get('vwap_distance_pct', 0.0):.4f}\n"
            f"Sebep: {debug.get('reason', 'n/a')}"
        )
        await update.message.reply_text(text)

    async def debug_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            if not self.scanner.last_scan_summary.get("symbols"):
                await update.message.reply_text("Debug icin sembol ver: /debug BTC")
                return
            symbol, debug = next(iter(self.scanner.last_scan_summary["symbols"].items()))
        else:
            symbol = self._normalize_symbol(context.args[0])
            debug = await self.scanner.debug_symbol(symbol)

        blocks = ", ".join(debug.get("blocks", [])) or "none"
        text = (
            f"Debug {debug['symbol']}\n"
            f"Eligible: {debug.get('eligible', False)}\n"
            f"Action: {debug.get('action', 'hold').upper()}\n"
            f"Score: {debug.get('score', 0)}\n"
            f"Blocks: {blocks}\n"
            f"Regime: {debug.get('regime', 'unknown')}\n"
            f"HTF Confirm: {debug.get('higher_timeframe_confirmed', False)}\n"
            f"Funding: {debug.get('funding_rate', 0.0):.5f}\n"
            f"Spread: {debug.get('spread_pct', 0.0):.5f}\n"
            f"Last candle pct: {debug.get('last_candle_pct', 0.0):.4f}\n"
            f"Volume ratio: {debug.get('volume_ratio', 0.0):.2f}\n"
            f"OI ratio: {debug.get('hold_vol_ratio', 0.0):.3f}\n"
            f"Reason: {debug.get('reason', 'n/a')}"
        )
        await update.message.reply_text(text)

    async def health_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        health = self.scanner.get_health_status()
        text = (
            "Health\n"
            f"Running: {health['running']}\n"
            f"Uptime: {health['uptime_seconds']}s\n"
            f"Ticks: {health['tick_count']}\n"
            f"Open positions: {health['open_positions']}\n"
            f"WS connected: {health['websocket'].get('connected', False)}\n"
            f"WS stale: {health.get('stale_for_seconds', 'n/a')}\n"
            f"REST retries: {health['rest_stats'].get('rest_retries', 0)}\n"
            f"REST failures: {health['rest_stats'].get('rest_failures', 0)}\n"
            f"Last error: {health['last_error'] or 'none'}"
        )
        await update.message.reply_text(text)

    async def backtest_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.message.reply_text("Kullanim: /backtest BTC 800")
            return
        symbol = self._normalize_symbol(context.args[0])
        bars = int(context.args[1]) if len(context.args) > 1 and context.args[1].isdigit() else self.cfg.backtest_bars
        result, _ = await asyncio.to_thread(self.backtester.run, symbol, bars)
        text = (
            f"Backtest {result.symbol}\n"
            f"Bars: {result.bars}\n"
            f"Closed trades: {result.closed_trades}\n"
            f"Win rate: {result.win_rate_pct:.2f}%\n"
            f"Total PnL: {result.total_net_pnl:.2f} USDT\n"
            f"Return: {result.return_pct:.2f}%\n"
            f"Max DD: {result.max_drawdown_pct:.2f}%\n"
            f"Profit factor: {result.profit_factor:.2f}\n"
            f"Expectancy: {result.expectancy:.4f}\n"
            f"Final balance: {result.final_balance:.2f} USDT"
        )
        await update.message.reply_text(text)

    async def daily_report_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        counters = self.scanner.daily_counters
        stats = self.wallet.data.get("daily_stats", {})
        text = (
            f"Gunluk durum {self.wallet.data.get('day')}\n"
            f"Entry scans: {counters['entry_scans']}\n"
            f"Signals: {counters['signals']}\n"
            f"Opened trades: {counters['opened_trades']}\n"
            f"Partial exits: {counters['partial_exits']}\n"
            f"Full exits: {counters['full_exits']}\n"
            f"Closed trades: {stats.get('closed_trades', 0)}\n"
            f"Win/Loss: {stats.get('wins', 0)}/{stats.get('losses', 0)}\n"
            f"Daily PnL: {self.wallet.data.get('daily_realized_pnl', 0.0):.2f} USDT\n"
            f"Blocks: {dict(counters['blocks'])}"
        )
        await update.message.reply_text(text)

    async def ayar_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "Ayarlar\n"
            f"Kaldirac: {self.cfg.leverage}x\n"
            f"Risk/islem: %{self.cfg.risk_per_trade * 100:.1f}\n"
            f"Gunluk zarar limiti: %{self.cfg.daily_loss_limit_pct * 100:.1f}\n"
            f"Tarama araligi: {self.cfg.scan_interval_seconds}s\n"
            f"Timeframe: {self.cfg.timeframe} / {self.cfg.higher_timeframe}\n"
            f"Max acik pozisyon: {self.cfg.max_open_positions}\n"
            f"Semboller: {', '.join(self.cfg.symbols)}"
        )
        await update.message.reply_text(text)

    async def start_polling(self) -> None:
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

    def _normalize_symbol(self, raw: str) -> str:
        normalized = raw.upper().replace("/", "_").strip()
        if normalized.endswith("_USDT"):
            return normalized
        normalized = normalized.replace("USDT", "").replace("_", "")
        return f"{normalized}_USDT"
