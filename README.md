# MEXC Futures Paper Bot

Tam otomatik, paper-trading odaklı MEXC futures bot iskeleti.

## Bu sürüm ne yapar?
- MEXC futures public market verisini çeker
- BTC_USDT ve ETH_USDT tarar
- agresif trend/breakout sinyali üretir
- sanal 1000 USDT ile long/short paper işlem açar
- stop-loss, take-profit ve trailing stop yönetir
- Telegram üzerinden başlat/durdur/durum/bakiye komutları sunar

## Bu sürüm ne yapmaz?
- Gerçek emir göndermez
- MEXC private futures trading API kullanmaz
- Çoklu pozisyon açmaz

## Kurulum
```bash
pip install -r requirements.txt
```

## Railway Variables
```env
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
BOT_ENABLED=true
SIM_MODE=true
STARTING_BALANCE=1000
SYMBOLS=BTC_USDT,ETH_USDT
LEVERAGE=5
RISK_PER_TRADE=0.03
SCAN_INTERVAL_SECONDS=30
```

## Telegram Komutları
- `/start`
- `/baslat`
- `/durdur`
- `/durum`
- `/bakiye`
- `/gecmis`
- `/analiz BTC`
- `/ayar`

## Notlar
- Bu bot paper trading içindir.
- MEXC futures market data endpointleri resmi futures API dökümantasyonundan alınmıştır.
- MEXC resmi sayfalarında futures trading API erişiminin kurumsal kullanıcılarla sınırlı olabileceği notu bulunduğu için bu sürüm bilinçli olarak public-data + paper wallet şeklinde tasarlandı.
