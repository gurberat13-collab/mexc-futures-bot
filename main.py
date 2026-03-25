from __future__ import annotations

import asyncio
import os

from config import CONFIG
from engine.backtester import Backtester
from engine.executor import Executor
from engine.paper_wallet import PaperWallet
from engine.position_manager import PositionManager
from engine.risk import RiskManager
from engine.scanner import ScannerEngine
from engine.strategy import StrategyEngine
from exchange.mexc_futures import MexcFuturesClient
from exchange.mexc_ws import MexcWsMarketStream
from telegram_bot.bot import TelegramController
from utils.helpers import ensure_parent
from utils.logger import setup_logger


async def main() -> None:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    ensure_parent(CONFIG.log_path)
    ensure_parent(CONFIG.state_path)
    ensure_parent(CONFIG.wallet_path)
    ensure_parent(CONFIG.trades_path)
    logger = setup_logger(CONFIG.log_path)

    wallet = PaperWallet(CONFIG.wallet_path, CONFIG.trades_path, CONFIG.starting_balance)
    client = MexcFuturesClient(CONFIG)
    strategy = StrategyEngine(CONFIG)
    risk = RiskManager(CONFIG)
    executor = Executor(CONFIG, wallet)
    position_manager = PositionManager(CONFIG, wallet)
    backtester = Backtester(CONFIG, client, strategy, risk, logger)

    market_stream = MexcWsMarketStream(CONFIG, CONFIG.symbols, logger) if CONFIG.websocket_enabled else None
    if market_stream:
        await market_stream.start()

    dummy_notifier = lambda text: asyncio.sleep(0)
    scanner = ScannerEngine(
        CONFIG,
        client,
        strategy,
        risk,
        wallet,
        executor,
        position_manager,
        logger,
        dummy_notifier,
        market_stream=market_stream,
    )

    if not CONFIG.telegram_token:
        logger.warning("TELEGRAM_TOKEN yok. Terminal/log modunda calisacak.")
        if CONFIG.bot_enabled:
            await scanner.start()
        else:
            logger.info("Bot baslangicta pasif durumda")
        await scanner.run_forever()
        return

    telegram = TelegramController(CONFIG, scanner, wallet, client, strategy, backtester, logger)
    scanner.notifier = telegram.notify

    await telegram.start_polling()
    if CONFIG.bot_enabled:
        await scanner.start()
    else:
        logger.info("Bot baslangicta pasif durumda")

    await scanner.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
