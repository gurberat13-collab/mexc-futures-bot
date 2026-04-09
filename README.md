# MEXC Futures Paper Bot

Paper-trading odakli MEXC futures botu. Bu surum artik sadece sinyal ureten basit bir iskelet degil; teshis, healthcheck, backtest, multi-timeframe filtreleri, websocket market akisi ve gelismis pozisyon yonetimi de icerir.

## Neler var
- REST + WebSocket market veri akisi
- BTC/ETH/SOL dahil coklu sembol tarama
- Multi-timeframe trend onayi
- Rejim filtresi (trending/ranging)
- EMA, RSI, MACD, breakout, VWAP, market structure, volatility expansion ve OI proxy tabanli skor
- Korelasyon limiti
- Coklu acik pozisyon destegi
- Partial take profit + break-even + trailing stop
- Gun sonu raporu
- Heartbeat / health status
- Telegram debug ve backtest komutlari
- Unit test iskeleti

## Kurulum
```bash
pip install -r requirements.txt
```

## Onemli ortam degiskenleri
```env
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
BOT_ENABLED=true
SIM_MODE=true

SYMBOLS=BTC_USDT,ETH_USDT,SOL_USDT
SCAN_INTERVAL_SECONDS=30
TIMEFRAME=Min15
HIGHER_TIMEFRAME=Hour4

STARTING_BALANCE=1000
LEVERAGE=5
RISK_PER_TRADE=0.03
MAX_OPEN_POSITIONS=2
MAX_TRADES_PER_DAY=12
COOLDOWN_MINUTES=60
RR_RATIO=2.2
REGIME_ADX_THRESHOLD=25
PARTIAL_TAKE_PROFIT_R=1.5
MIN_EXPECTED_NET_RR=1.10
MIN_EXPECTED_NET_PROFIT_PCT=0.0008
MIN_DIRECTIONAL_VOTES=4
MAX_CONFLICT_RATIO=0.42
POSITION_INTRABAR_FROM_KLINES=true

WEBSOCKET_ENABLED=true
HEARTBEAT_INTERVAL_MINUTES=30
DAILY_REPORT_ENABLED=true
BACKTEST_BARS=800
```

## Hazir profil presetleri

Botta artik `TRADING_PROFILE` ile uc farkli varsayilan profil secilebilir:

- `conservative`: daha az islem, daha dusuk risk
- `balanced`: orta risk, orta frekans
- `aggressive_safe`: daha fazla firsat, guvenlik filtreleri aktif

Kullanim:

```env
TRADING_PROFILE=conservative
```

Not:
- `TRADING_PROFILE` sadece varsayilan degerleri ayarlar.
- `.env` icinde tek tek verdigin degerler her zaman profil varsayilanlarini ezer.

Hazir ornek dosyalar:
- `.env.conservative.example`
- `.env.balanced.example`
- `.env.aggressive_safe.example`

## Telegram komutlari
- `/baslat`
- `/durdur`
- `/durum`
- `/bakiye`
- `/gecmis`
- `/analiz BTC`
- `/debug BTC`
- `/nedenislem BTC`
- `/health`
- `/backtest BTC 800`
- `/gunsonu`
- `/ayar`

## Notlar
- Bot paper trading modunda tasarlandi.
- `storage/state.json` health ve scan ozetini tutar.
- `storage/wallet.json` wallet durumunu tutar.
- `storage/trades.json` kapanan trade eventlerini tutar.
- Deploy tarafinda `runtime.txt` icindeki Python surumunu kullanman tavsiye edilir.
