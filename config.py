from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class BotConfig:
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    scan_interval_seconds: int = int(os.getenv("SCAN_INTERVAL_SECONDS", "30"))
    timeframe: str = os.getenv("TIMEFRAME", "Min15")
    kline_limit: int = int(os.getenv("KLINE_LIMIT", "120"))

    symbols: List[str] = field(
        default_factory=lambda: os.getenv("SYMBOLS", "BTC_USDT,ETH_USDT").split(",")
    )

    starting_balance: float = float(os.getenv("STARTING_BALANCE", "1000"))
    leverage: int = int(os.getenv("LEVERAGE", "5"))
    risk_per_trade: float = float(os.getenv("RISK_PER_TRADE", "0.03"))
    daily_loss_limit_pct: float = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.08"))
    max_consecutive_losses: int = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))
    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "1"))
    max_trades_per_day: int = int(os.getenv("MAX_TRADES_PER_DAY", "12"))
    cooldown_minutes: int = int(os.getenv("COOLDOWN_MINUTES", "15"))

    fee_rate: float = float(os.getenv("FEE_RATE", "0.0004"))
    slippage_rate: float = float(os.getenv("SLIPPAGE_RATE", "0.0003"))

    breakout_lookback: int = int(os.getenv("BREAKOUT_LOOKBACK", "20"))
    atr_period: int = int(os.getenv("ATR_PERIOD", "14"))
    atr_stop_mult: float = float(os.getenv("ATR_STOP_MULT", "1.4"))
    rr_ratio: float = float(os.getenv("RR_RATIO", "1.8"))
    trailing_activation_r: float = float(os.getenv("TRAILING_ACTIVATION_R", "1.1"))
    trailing_gap_pct: float = float(os.getenv("TRAILING_GAP_PCT", "0.008"))

    aggressive_score_threshold: int = int(os.getenv("AGGRESSIVE_SCORE_THRESHOLD", "3"))
    funding_abs_limit: float = float(os.getenv("FUNDING_ABS_LIMIT", "0.0025"))
    max_last_candle_pct: float = float(os.getenv("MAX_LAST_CANDLE_PCT", "0.025"))
    max_spread_pct: float = float(os.getenv("MAX_SPREAD_PCT", "0.0025"))

    state_path: str = os.getenv("STATE_PATH", "storage/state.json")
    wallet_path: str = os.getenv("WALLET_PATH", "storage/wallet.json")
    trades_path: str = os.getenv("TRADES_PATH", "storage/trades.json")
    log_path: str = os.getenv("LOG_PATH", "storage/logs.txt")

    bot_enabled: bool = os.getenv("BOT_ENABLED", "true").lower() == "true"
    sim_mode: bool = os.getenv("SIM_MODE", "true").lower() == "true"


CONFIG = BotConfig()
