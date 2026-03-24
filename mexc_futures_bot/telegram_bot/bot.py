from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


class TelegramController:
    def __init__(self, config, scanner, wallet, client, strategy, logger):
        self.cfg = config
        self.scanner = scanner
        self.wallet = wallet
        self.client = client
        self.strategy = strategy
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

    async def notify(self, text: str) -> None:
        if self.cfg.telegram_chat_id:
            await self.app.bot.send_message(chat_id=self.cfg.telegram_chat_id, text=text)

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "🤖 MEXC Futures Paper Bot\n"
            "Komutlar:\n"
            "/baslat\n/durdur\n/durum\n/bakiye\n/gecmis\n/analiz BTC\n/ayar"
        )
        await update.message.reply_text(text)

    async def baslat_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.scanner.running = True
        await update.message.reply_text("✅ Bot aktif. Yeni sinyal aramaya başladı.")

    async def durdur_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.scanner.running = False
        await update.message.reply_text("⏸ Bot pasif. Açık pozisyon varsa yönetmeye devam eder, yeni pozisyon açmaz.")

    async def durum_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        pos = self.wallet.open_position
        if pos:
            text = (
                f"📊 Bot: {'AKTIF' if self.scanner.running else 'PASIF'}\n"
                f"Açık Pozisyon: {pos['symbol']} {pos['side']}\n"
                f"Giriş: {pos['entry_price']:.2f}\n"
                f"SL: {pos['stop_loss']:.2f} | TP: {pos['take_profit']:.2f}\n"
                f"Trailing: {'ACIK' if pos['trailing_active'] else 'BEKLIYOR'}"
            )
        else:
            text = f"📊 Bot: {'AKTIF' if self.scanner.running else 'PASIF'}\nAçık pozisyon yok."
        await update.message.reply_text(text)

    async def bakiye_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.wallet.rollover_if_needed()
        text = (
            f"💼 Sanal Cüzdan\n"
            f"Bakiye: {self.wallet.data['balance']:.2f} USDT\n"
            f"Equity: {self.wallet.data['equity']:.2f} USDT\n"
            f"Gerçekleşen PnL: {self.wallet.data['realized_pnl']:.2f} USDT\n"
            f"Günlük PnL: {self.wallet.data['daily_realized_pnl']:.2f} USDT"
        )
        await update.message.reply_text(text)

    async def gecmis_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        history = self.wallet.data.get("history", [])[:5]
        if not history:
            await update.message.reply_text("Henüz kapanmış işlem yok.")
            return
        lines = ["🧾 Son İşlemler"]
        for trade in history:
            lines.append(
                f"{trade['symbol']} {trade['side']} | {trade['exit_reason']} | {trade['net_pnl']:.2f} USDT"
            )
        await update.message.reply_text("\n".join(lines))

    async def analiz_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.message.reply_text("Kullanım: /analiz BTC")
            return
        raw = context.args[0].upper().replace("USDT", "")
        symbol = f"{raw}_USDT"
        df = self.client.get_klines(symbol, self.cfg.timeframe, self.cfg.kline_limit)
        snapshot = self.client.get_ticker(symbol)
        signal = self.strategy.analyze(symbol, df)
        text = (
            f"🧠 {symbol}\n"
            f"Fiyat: {snapshot.last_price:.2f}\n"
            f"Skor: {signal.score}\n"
            f"Aksiyon: {signal.action.upper()}\n"
            f"RSI: {signal.rsi_value:.2f}\n"
            f"MACD Hist: {signal.macd_hist:.4f}\n"
            f"Vol Ratio: {signal.volume_ratio:.2f}\n"
            f"Funding: {snapshot.funding_rate:.5f}\n"
            f"Sebep: {signal.reason}"
        )
        await update.message.reply_text(text)

    async def ayar_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            f"⚙️ Ayarlar\n"
            f"Kaldıraç: {self.cfg.leverage}x\n"
            f"Risk/işlem: %{self.cfg.risk_per_trade * 100:.1f}\n"
            f"Günlük zarar limiti: %{self.cfg.daily_loss_limit_pct * 100:.1f}\n"
            f"Tarama aralığı: {self.cfg.scan_interval_seconds}s\n"
            f"Semboller: {', '.join(self.cfg.symbols)}"
        )
        await update.message.reply_text(text)

    async def start_polling(self) -> None:
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
