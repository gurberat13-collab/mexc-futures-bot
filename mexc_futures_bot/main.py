from __future__ import annotations

import asyncio
import os

from config import CONFIG
from engine.executor import Executor
from engine.paper_wallet import PaperWallet
from engine.position_manager import PositionManager
from engine.risk import RiskManager
from engine.scanner import ScannerEngine
from engine.strategy import StrategyEngine
from exchange.mexc_futures import MexcFuturesClient
from telegram_bot.bot import TelegramController
from utils.helpers import ensure_parent
from utils.logger import setup_logger


async def main() -> None:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    ensure_parent(CONFIG.log_path)
    logger = setup_logger(CONFIG.log_path)

    wallet = PaperWallet(CONFIG.wallet_path, CONFIG.starting_balance)
    client = MexcFuturesClient()
    strategy = StrategyEngine(CONFIG)
    risk = RiskManager(CONFIG)
    executor = Executor(CONFIG, wallet)
    position_manager = PositionManager(CONFIG, wallet)

    dummy_notifier = lambda text: asyncio.sleep(0)
    scanner = ScannerEngine(CONFIG, client, strategy, risk, wallet, executor, position_manager, logger, dummy_notifier)

    if not CONFIG.telegram_token:
        logger.warning("TELEGRAM_TOKEN yok. Sadece terminal/log ile çalışacak.")
        await scanner.start()
        await scanner.run_forever()
        return

    telegram = TelegramController(CONFIG, scanner, wallet, client, strategy, logger)
    scanner.notifier = telegram.notify

    await telegram.start_polling()
    if CONFIG.bot_enabled:
        await scanner.start()
    else:
        logger.info("Bot başlangıçta pasif durumda")

    await scanner.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
