from __future__ import annotations

import os
from dataclasses import dataclass, field

from utils.helpers import env_bool, env_list


@dataclass
class BotConfig:
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    scan_interval_seconds: int = int(os.getenv("SCAN_INTERVAL_SECONDS", "30"))
    timeframe: str = os.getenv("TIMEFRAME", "Min15")
    higher_timeframe: str = os.getenv("HIGHER_TIMEFRAME", "Hour4")
    kline_limit: int = int(os.getenv("KLINE_LIMIT", "160"))
    higher_kline_limit: int = int(os.getenv("HIGHER_KLINE_LIMIT", "220"))

    symbols: list[str] = field(
        default_factory=lambda: env_list("SYMBOLS", "BTC_USDT,ETH_USDT,SOL_USDT")
    )

    starting_balance: float = float(os.getenv("STARTING_BALANCE", "1000"))
    leverage: int = int(os.getenv("LEVERAGE", "5"))
    risk_per_trade: float = float(os.getenv("RISK_PER_TRADE", "0.03"))
    daily_loss_limit_pct: float = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.08"))
    max_consecutive_losses: int = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))
    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "2"))
    max_trades_per_day: int = int(os.getenv("MAX_TRADES_PER_DAY", "12"))
    cooldown_minutes: int = int(os.getenv("COOLDOWN_MINUTES", "60"))

    fee_rate: float = float(os.getenv("FEE_RATE", "0.0004"))
    slippage_rate: float = float(os.getenv("SLIPPAGE_RATE", "0.0003"))

    breakout_lookback: int = int(os.getenv("BREAKOUT_LOOKBACK", "20"))
    market_structure_lookback: int = int(os.getenv("MARKET_STRUCTURE_LOOKBACK", "12"))
    atr_period: int = int(os.getenv("ATR_PERIOD", "14"))
    atr_stop_mult: float = float(os.getenv("ATR_STOP_MULT", "1.4"))
    rr_ratio: float = float(os.getenv("RR_RATIO", "2.2"))
    trailing_activation_r: float = float(os.getenv("TRAILING_ACTIVATION_R", "1.1"))
    trailing_gap_pct: float = float(os.getenv("TRAILING_GAP_PCT", "0.008"))
    partial_take_profit_r: float = float(os.getenv("PARTIAL_TAKE_PROFIT_R", "1.5"))
    partial_take_profit_pct: float = float(os.getenv("PARTIAL_TAKE_PROFIT_PCT", "0.5"))
    break_even_on_partial: bool = env_bool("BREAK_EVEN_ON_PARTIAL", True)

    aggressive_score_threshold: int = int(os.getenv("AGGRESSIVE_SCORE_THRESHOLD", "4"))
    funding_abs_limit: float = float(os.getenv("FUNDING_ABS_LIMIT", "0.0025"))
    max_last_candle_pct: float = float(os.getenv("MAX_LAST_CANDLE_PCT", "0.025"))
    max_spread_pct: float = float(os.getenv("MAX_SPREAD_PCT", "0.0025"))
    volume_spike_threshold: float = float(os.getenv("VOLUME_SPIKE_THRESHOLD", "1.2"))
    vwap_window: int = int(os.getenv("VWAP_WINDOW", "48"))
    volatility_expansion_ratio: float = float(os.getenv("VOLATILITY_EXPANSION_RATIO", "1.05"))
    regime_adx_threshold: float = float(os.getenv("REGIME_ADX_THRESHOLD", "25"))
    regime_atr_pct_threshold: float = float(os.getenv("REGIME_ATR_PCT_THRESHOLD", "0.002"))
    require_trending_regime: bool = env_bool("REQUIRE_TRENDING_REGIME", True)
    higher_timeframe_required: bool = env_bool("HIGHER_TIMEFRAME_REQUIRED", True)

    open_interest_history_size: int = int(os.getenv("OPEN_INTEREST_HISTORY_SIZE", "24"))
    open_interest_increase_ratio: float = float(os.getenv("OPEN_INTEREST_INCREASE_RATIO", "1.01"))
    max_symbol_correlation: float = float(os.getenv("MAX_SYMBOL_CORRELATION", "0.85"))
    min_correlation_lookback: int = int(os.getenv("MIN_CORRELATION_LOOKBACK", "40"))

    websocket_enabled: bool = env_bool("WEBSOCKET_ENABLED", True)
    websocket_url: str = os.getenv("WEBSOCKET_URL", "wss://contract.mexc.com/edge")
    websocket_ping_seconds: int = int(os.getenv("WEBSOCKET_PING_SECONDS", "20"))
    websocket_reconnect_seconds: int = int(os.getenv("WEBSOCKET_RECONNECT_SECONDS", "5"))

    rest_retry_attempts: int = int(os.getenv("REST_RETRY_ATTEMPTS", "3"))
    rest_retry_backoff_seconds: float = float(os.getenv("REST_RETRY_BACKOFF_SECONDS", "0.7"))

    heartbeat_interval_minutes: int = int(os.getenv("HEARTBEAT_INTERVAL_MINUTES", "30"))
    health_stale_seconds: int = int(os.getenv("HEALTH_STALE_SECONDS", "180"))
    report_timezone: str = os.getenv("REPORT_TIMEZONE", "UTC")
    daily_report_enabled: bool = env_bool("DAILY_REPORT_ENABLED", True)

    backtest_bars: int = int(os.getenv("BACKTEST_BARS", "800"))
    backtest_warmup_bars: int = int(os.getenv("BACKTEST_WARMUP_BARS", "120"))

    state_path: str = os.getenv("STATE_PATH", "storage/state.json")
    wallet_path: str = os.getenv("WALLET_PATH", "storage/wallet.json")
    trades_path: str = os.getenv("TRADES_PATH", "storage/trades.json")
    log_path: str = os.getenv("LOG_PATH", "storage/logs.txt")

    bot_enabled: bool = env_bool("BOT_ENABLED", True)
    sim_mode: bool = env_bool("SIM_MODE", True)


CONFIG = BotConfig()
