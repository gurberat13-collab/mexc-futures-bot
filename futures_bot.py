"""
🤖 MEXC Futures Trading Bot
3x Kaldıraç | Grid + Trend Takip Stratejisi
4 Karar Motoru + ATR Stop + Trailing Stop
Simülasyon modu varsayılan
"""

import os, json, asyncio, logging, time, hmac, hashlib
from datetime import datetime
from typing import Dict, List, Optional
import requests as req

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
except ImportError:
    raise

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("futures_bot.log"), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_TOKEN")
MEXC_API_KEY    = os.getenv("MEXC_API_KEY",   "YOUR_MEXC_API_KEY")
MEXC_API_SECRET = os.getenv("MEXC_API_SECRET","YOUR_MEXC_API_SECRET")
SIM_MODE        = os.getenv("SIM_MODE", "true").lower() == "true"
LEVERAGE        = 3

FUTURES_BASE = "https://contract.mexc.com"

COIN_MAP = {
    "BTC_USDT": "bitcoin", "ETH_USDT": "ethereum",
    "SOL_USDT": "solana",  "BNB_USDT": "binancecoin",
}

FUTURES_CONFIGS = {
    "BTC_USDT": {"grid_count":8,"range_pct":0.03,"margin_usdt":50.0,"min_qty":0.001,"qty_precision":3,"atr_multiplier":1.5,"trailing_activation_pct":0.015,"trailing_step_pct":0.008,"rsi_oversold":35,"rsi_overbought":65,"trend_ema_fast":9,"trend_ema_slow":21},
    "ETH_USDT": {"grid_count":8,"range_pct":0.04,"margin_usdt":40.0,"min_qty":0.01,"qty_precision":2,"atr_multiplier":1.5,"trailing_activation_pct":0.015,"trailing_step_pct":0.008,"rsi_oversold":35,"rsi_overbought":65,"trend_ema_fast":9,"trend_ema_slow":21},
    "SOL_USDT": {"grid_count":8,"range_pct":0.05,"margin_usdt":30.0,"min_qty":0.1,"qty_precision":1,"atr_multiplier":2.0,"trailing_activation_pct":0.02,"trailing_step_pct":0.01,"rsi_oversold":35,"rsi_overbought":65,"trend_ema_fast":9,"trend_ema_slow":21},
    "BNB_USDT": {"grid_count":8,"range_pct":0.04,"margin_usdt":30.0,"min_qty":0.01,"qty_precision":2,"atr_multiplier":1.5,"trailing_activation_pct":0.015,"trailing_step_pct":0.008,"rsi_oversold":35,"rsi_overbought":65,"trend_ema_fast":9,"trend_ema_slow":21},
}

# ── SİMÜLASYON ────────────────────────────────────────────────────────────────
_sim_balance   = 2000.0
_sim_positions = {}
_sim_counter   = 0

def _sim_open(symbol, side, price, qty, margin):
    global _sim_balance
    if _sim_balance < margin:
        log.warning(f"🎮 Yetersiz marjin: ${_sim_balance:.2f}")
        return None
    _sim_balance -= margin
    _sim_positions[symbol] = {"side":side,"entry":price,"qty":qty,"margin":margin}
    log.info(f"🎮 {side} {qty} {symbol} @ ${price:,.2f} | Marjin:${margin:.2f} | Kaldıraç:{LEVERAGE}x | Bakiye:${_sim_balance:.2f}")
    return f"SIM_{symbol}"

def _sim_close(symbol, price):
    global _sim_balance
    pos = _sim_positions.pop(symbol, None)
    if not pos: return 0
    pnl = ((price-pos["entry"])/pos["entry"] if pos["side"]=="LONG" else (pos["entry"]-price)/pos["entry"]) * pos["margin"] * LEVERAGE
    _sim_balance += pos["margin"] + pnl
    log.info(f"🎮 KAPANDI {symbol} PNL:{pnl:+.2f}$ Bakiye:${_sim_balance:.2f}")
    return pnl

def _sim_pnl(symbol, price):
    pos = _sim_positions.get(symbol)
    if not pos: return 0
    return ((price-pos["entry"])/pos["entry"] if pos["side"]=="LONG" else (pos["entry"]-price)/pos["entry"]) * pos["margin"] * LEVERAGE

# ── FIYAT & VERİ ──────────────────────────────────────────────────────────────
def get_price(symbol):
    try:
        r = req.get(f"{FUTURES_BASE}/api/v1/contract/ticker", params={"symbol":symbol}, timeout=10)
        d = r.json()
        if d.get("success"): return float(d["data"]["lastPrice"])
    except Exception as e: log.error(f"Fiyat hatası {symbol}: {e}")
    return None

def get_ohlc(symbol, days=7):
    coin_id = COIN_MAP.get(symbol)
    if not coin_id: return []
    try:
        r = req.get(f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc",
                    params={"vs_currency":"usd","days":days}, timeout=15)
        data = r.json()
        if not isinstance(data,list) or len(data)<10: return []
        return [[d[0],str(d[1]),str(d[2]),str(d[3]),str(d[4]),"1"] for d in data]
    except Exception as e:
        log.error(f"OHLC hatası {symbol}: {e}"); return []

def get_balance():
    if SIM_MODE: return _sim_balance
    try:
        ts = int(time.time()*1000)
        p  = {"timestamp":ts}
        p["signature"] = hmac.new(MEXC_API_SECRET.encode(), "&".join(f"{k}={v}" for k,v in sorted(p.items())).encode(), hashlib.sha256).hexdigest()
        r = req.get(f"{FUTURES_BASE}/api/v1/private/account/assets", params=p, headers={"ApiKey":MEXC_API_KEY}, timeout=10)
        for a in r.json().get("data",[]): 
            if a["currency"]=="USDT": return float(a["availableBalance"])
    except Exception as e: log.error(f"Bakiye hatası: {e}")
    return 0.0

def open_order(symbol, side, qty):
    if SIM_MODE:
        price = get_price(symbol) or 0
        margin = (price*qty)/LEVERAGE
        return _sim_open(symbol, side, price, qty, margin)
    try:
        ts = int(time.time()*1000)
        p  = {"symbol":symbol,"side":1 if side=="LONG" else 3,"orderType":5,"vol":qty,"leverage":LEVERAGE,"openType":1,"timestamp":ts}
        p["signature"] = hmac.new(MEXC_API_SECRET.encode(), "&".join(f"{k}={v}" for k,v in sorted(p.items())).encode(), hashlib.sha256).hexdigest()
        r = req.post(f"{FUTURES_BASE}/api/v1/private/order/submit", json=p, headers={"ApiKey":MEXC_API_KEY,"Content-Type":"application/json"}, timeout=10)
        d = r.json()
        if d.get("success"): return str(d["data"])
        log.error(f"Emir hatası: {d}")
    except Exception as e: log.error(f"Emir hatası: {e}")
    return None

def close_position(symbol):
    if SIM_MODE:
        price = get_price(symbol) or 0
        return _sim_close(symbol, price)
    try:
        ts = int(time.time()*1000)
        p  = {"symbol":symbol,"timestamp":ts}
        p["signature"] = hmac.new(MEXC_API_SECRET.encode(), "&".join(f"{k}={v}" for k,v in sorted(p.items())).encode(), hashlib.sha256).hexdigest()
        req.post(f"{FUTURES_BASE}/api/v1/private/position/close_all", json=p, headers={"ApiKey":MEXC_API_KEY,"Content-Type":"application/json"}, timeout=10)
        return 0
    except Exception as e: log.error(f"Kapatma hatası: {e}"); return 0

# ── TEKNİK ANALİZ ─────────────────────────────────────────────────────────────
def ema(closes, span):
    k=2/(span+1); e=closes[0]
    for v in closes[1:]: e=v*k+e*(1-k)
    return e

def rsi(closes, period=14):
    if len(closes)<period+1: return 50.0
    g=[max(closes[i]-closes[i-1],0) for i in range(1,len(closes))]
    l=[max(closes[i-1]-closes[i],0) for i in range(1,len(closes))]
    ag=sum(g[-period:])/period; al=sum(l[-period:])/period
    return round(100-(100/(1+ag/al)),2) if al else 100.0

def macd(closes):
    if len(closes)<35: return 0,0,0
    m=ema(closes,12)-ema(closes,26); s=m*0.9
    return round(m,4),round(s,4),round(m-s,4)

def atr(klines, period=14):
    trs=[]
    for i in range(1,len(klines)):
        try: h,l,pc=float(klines[i][2]),float(klines[i][3]),float(klines[i-1][4]); trs.append(max(h-l,abs(h-pc),abs(l-pc)))
        except: pass
    return round(sum(trs[-period:])/period,4) if len(trs)>=period else 0.0

def support_resistance(klines, lookback=20):
    try:
        return round(min(float(k[3]) for k in klines[-lookback:]),2), round(max(float(k[2]) for k in klines[-lookback:]),2)
    except: return 0.0,0.0

# ── 4 KARAR MOTORU ────────────────────────────────────────────────────────────
class FuturesEngine:
    def __init__(self, cfg): self.cfg=cfg

    def analyze(self, symbol):
        klines=get_ohlc(symbol,7)
        if len(klines)<35: return self._empty()
        closes=[float(k[4]) for k in klines]
        cur=closes[-1]
        sup,res=support_resistance(klines)
        sr_pos=(cur-sup)/(res-sup) if res>sup else 0.5

        if sr_pos<0.2:   m1,m1t=2,"Desteğe yakın → LONG 📗"
        elif sr_pos<0.4: m1,m1t=1,"Alt bölge 📗"
        elif sr_pos>0.8: m1,m1t=-2,"Dirençte → SHORT 📕"
        elif sr_pos>0.6: m1,m1t=-1,"Üst bölge 📕"
        else:             m1,m1t=0,"Orta bölge ⬜"

        r=rsi(closes)
        if r<self.cfg["rsi_oversold"]:    m2,m2t=2,f"RSI aşırı satım ({r}) 📗"
        elif r<45:                         m2,m2t=1,f"RSI düşük ({r}) 📗"
        elif r>self.cfg["rsi_overbought"]:m2,m2t=-2,f"RSI aşırı alım ({r}) 📕"
        elif r>55:                         m2,m2t=-1,f"RSI yüksek ({r}) 📕"
        else:                              m2,m2t=0,f"RSI nötr ({r}) ⬜"

        ef=ema(closes,self.cfg["trend_ema_fast"]); es=ema(closes,self.cfg["trend_ema_slow"])
        dp=(ef-es)/es*100
        if ef>es and dp>0.3:   m3,m3t=2,f"EMA boğa trendi ({dp:+.2f}%) 📗"
        elif ef>es:             m3,m3t=1,f"EMA hafif boğa ({dp:+.2f}%) 📗"
        elif ef<es and dp<-0.3:m3,m3t=-2,f"EMA ayı trendi ({dp:+.2f}%) 📕"
        elif ef<es:             m3,m3t=-1,f"EMA hafif ayı ({dp:+.2f}%) 📕"
        else:                   m3,m3t=0,f"EMA nötr ⬜"

        mc,ms,mh=macd(closes)
        if mc>0 and mh>0:  m4,m4t=2,f"MACD boğa ({mc}) 📗"
        elif mc>0:          m4,m4t=1,f"MACD pozitif ({mc}) 📗"
        elif mc<0 and mh<0:m4,m4t=-2,f"MACD ayı ({mc}) 📕"
        elif mc<0:          m4,m4t=-1,f"MACD negatif ({mc}) 📕"
        else:               m4,m4t=0,f"MACD nötr ({mc}) ⬜"

        total=m1+m2+m3+m4
        if abs(total)>=4:
            strat="TREND"; direction="LONG" if total>0 else "SHORT"
            regime=("🟢 GÜÇLÜ LONG" if total>0 else "🔴 GÜÇLÜ SHORT")
        elif abs(total)>=2:
            strat="TREND"; direction="LONG" if total>0 else "SHORT"
            regime=("🟡 LONG" if total>0 else "🟠 SHORT")
        else:
            strat="GRID"; direction="NEUTRAL"; regime="⚪ YATAY → Grid"

        return {"regime":regime,"strategy":strat,"direction":direction,"total":total,
                "rsi":r,"macd":mc,"ema_fast":round(ef,2),"ema_slow":round(es,2),
                "support":sup,"resistance":res,"atr":atr(klines),"current":cur,
                "motors":[
                    {"name":"🔲 Destek/Direnç","score":m1,"detail":m1t},
                    {"name":"📊 RSI","score":m2,"detail":m2t},
                    {"name":"📈 EMA Trend","score":m3,"detail":m3t},
                    {"name":"🌊 MACD","score":m4,"detail":m4t},
                ]}

    def _empty(self):
        return {"regime":"⚪ VERİ YOK","strategy":"GRID","direction":"NEUTRAL",
                "total":0,"rsi":50,"macd":0,"ema_fast":0,"ema_slow":0,
                "support":0,"resistance":0,"atr":0,"current":0,"motors":[]}

# ── TRAILING STOP ─────────────────────────────────────────────────────────────
class TrailingStop:
    def __init__(self,entry,direction,act_pct,step_pct):
        self.entry=entry; self.dir=direction; self.act_pct=act_pct; self.step_pct=step_pct
        self.activated=False; self.peak=entry; self.stop=0.0
    def update(self,price):
        if self.dir=="LONG":
            gain=(price-self.entry)/self.entry
            if not self.activated:
                if gain>=self.act_pct: self.activated=True; self.peak=price; self.stop=price*(1-self.step_pct); log.info(f"🏄 LONG Trailing AKTİF @ ${price:.2f}")
                return False
            if price>self.peak: self.peak=price; self.stop=price*(1-self.step_pct)
            return price<=self.stop
        else:
            gain=(self.entry-price)/self.entry
            if not self.activated:
                if gain>=self.act_pct: self.activated=True; self.peak=price; self.stop=price*(1+self.step_pct); log.info(f"🏄 SHORT Trailing AKTİF @ ${price:.2f}")
                return False
            if price<self.peak: self.peak=price; self.stop=price*(1+self.step_pct)
            return price>=self.stop
    def status(self):
        if not self.activated:
            needed=self.entry*(1+self.act_pct) if self.dir=="LONG" else self.entry*(1-self.act_pct)
            return f"⏳ Bekliyor (${needed:,.2f} gerekli)"
        return f"🏄 AKTİF | Peak:${self.peak:,.2f} Stop:${self.stop:,.2f}"

# ── GLOBAL STATE ──────────────────────────────────────────────────────────────
active_bots={}; trailing_stops={}; atr_stops={}; engines={}; last_analysis={}

# ── FUTURES DÖNGÜSÜ ───────────────────────────────────────────────────────────
async def futures_loop(app, symbol):
    log.info(f"Futures bot başlatıldı: {symbol}")
    cfg=FUTURES_CONFIGS[symbol]; engine=FuturesEngine(cfg); engines[symbol]=engine
    center=get_price(symbol)
    if not center: log.error(f"{symbol}: Fiyat alınamadı."); return

    an=engine.analyze(symbol); last_analysis[symbol]=an
    log.info(f"🧠 {symbol} Strateji:{an['strategy']} Yön:{an['direction']} Skor:{an['total']}")

    atr_val=an.get("atr",0); pos_id=None

    if an["strategy"]=="TREND" and an["direction"]!="NEUTRAL":
        qty=round((cfg["margin_usdt"]*LEVERAGE)/center, cfg["qty_precision"])
        if qty<cfg["min_qty"]: qty=cfg["min_qty"]
        pos_id=open_order(symbol, an["direction"], qty)
        if pos_id:
            atr_stops[symbol]=(round(center-atr_val*cfg["atr_multiplier"],2) if an["direction"]=="LONG" else round(center+atr_val*cfg["atr_multiplier"],2)) if atr_val else (center*0.97 if an["direction"]=="LONG" else center*1.03)
            trailing_stops[symbol]=TrailingStop(center,an["direction"],cfg["trailing_activation_pct"],cfg["trailing_step_pct"])
            active_bots[symbol]={"strategy":"TREND","direction":an["direction"],"entry":center,"qty":qty,"started":datetime.now().isoformat(),"pnl":0.0,"regime":an["regime"]}
            try:
                uid=app.bot_data.get("owner_id")
                if uid: await app.bot.send_message(chat_id=uid,parse_mode="Markdown",text=(
                    f"🚀 *{symbol} TREND Pozisyonu*\nYön: *{an['direction']}* | `{LEVERAGE}x`\n"
                    f"Giriş: `${center:,.2f}` | Miktar: `{qty}`\n"
                    f"🛡 ATR Stop: `${atr_stops[symbol]:,.2f}`\n📡 {an['regime']}"))
            except: pass
    else:
        active_bots[symbol]={"strategy":"GRID","direction":"NEUTRAL","entry":center,"qty":0,"started":datetime.now().isoformat(),"pnl":0.0,"regime":an["regime"]}
        log.info(f"⚪ {symbol} GRID modu")

    loop_count=0
    while symbol in active_bots:
        await asyncio.sleep(30); loop_count+=1
        if symbol not in active_bots: break
        try:
            price=get_price(symbol)
            if not price: continue
            bot=active_bots[symbol]
            if SIM_MODE and symbol in _sim_positions: bot["pnl"]=_sim_pnl(symbol,price)

            if bot["strategy"]=="TREND":
                atr_stop=atr_stops.get(symbol,0)
                triggered=(price<=atr_stop if bot["direction"]=="LONG" else price>=atr_stop) and atr_stop>0
                ts=trailing_stops.get(symbol)
                ts_triggered=ts and ts.update(price)
                if triggered or ts_triggered:
                    pnl=close_position(symbol); active_bots.pop(symbol); trailing_stops.pop(symbol,None)
                    reason="🛡 ATR Stop" if triggered else f"🏄 Trailing (Peak:${ts.peak:,.2f})"
                    try:
                        uid=app.bot_data.get("owner_id")
                        if uid: await app.bot.send_message(chat_id=uid,parse_mode="Markdown",text=(
                            f"{reason} *{symbol}*\nFiyat: `${price:,.2f}` | PNL: `{pnl:+.2f}$`\n"
                            f"/futures_baslat {symbol.split('_')[0]} ile yeniden başlat"))
                    except: pass
                    asyncio.create_task(futures_loop(app,symbol)); break

            if loop_count%20==0:
                na=engine.analyze(symbol); last_analysis[symbol]=na
                if na["strategy"]!=bot["strategy"] or na["direction"]!=bot.get("direction",""):
                    pnl=close_position(symbol) if bot["strategy"]=="TREND" else 0
                    active_bots.pop(symbol); trailing_stops.pop(symbol,None)
                    try:
                        uid=app.bot_data.get("owner_id")
                        if uid: await app.bot.send_message(chat_id=uid,parse_mode="Markdown",text=(
                            f"🔄 *{symbol} Strateji Değişti*\n`{bot['strategy']}` → `{na['strategy']}`\nPNL: `{pnl:+.2f}$`"))
                    except: pass
                    asyncio.create_task(futures_loop(app,symbol)); break

        except Exception as e: log.error(f"Futures döngü hatası {symbol}: {e}")

# ── TELEGRAM KOMUTLARI ────────────────────────────────────────────────────────
async def cmd_start(update,ctx):
    ctx.application.bot_data["owner_id"]=update.effective_user.id
    mode="🎮 SİMÜLASYON ($2000 sanal)" if SIM_MODE else "💰 GERÇEK İŞLEM"
    await update.message.reply_text(
        f"🤖 *MEXC Futures Bot*\n_{mode} | {LEVERAGE}x Kaldıraç_\n\n"
        "*/tum_baslat* — 4 coini başlat\n*/futures_baslat* `BTC` — Tek coin\n"
        "*/futures_durdur* `BTC` — Durdur\n*/tum_durdur* — Hepsini durdur\n"
        "*/durum* — Aktif pozisyonlar + PNL\n*/analiz* `BTC` — 4 motor analizi\n"
        "*/bakiye* — Futures bakiye\n*/fiyat* `BTC` — Anlık fiyat",
        parse_mode="Markdown")

async def cmd_analiz(update,ctx):
    raw=(ctx.args[0].upper() if ctx.args else "BTC")
    symbol=raw if "_" in raw else raw+"_USDT"
    if symbol not in FUTURES_CONFIGS:
        await update.message.reply_text("❌ Desteklenmiyor.",parse_mode="Markdown"); return
    await update.message.reply_text(f"🧠 `{symbol}` analiz ediliyor...",parse_mode="Markdown")
    engine=engines.get(symbol) or FuturesEngine(FUTURES_CONFIGS[symbol])
    an=engine.analyze(symbol); last_analysis[symbol]=an
    ml="\n".join([f"  {m['name']}: `{'+' if m['score']>0 else ''}{m['score']}` — {m['detail']}" for m in an["motors"]])
    await update.message.reply_text(
        f"🧠 *{symbol} — Futures Analizi*\n━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 `${an['current']:,.2f}` | EMA9:`${an['ema_fast']:,.2f}` EMA21:`${an['ema_slow']:,.2f}`\n"
        f"📐 Destek:`${an['support']:,.2f}` Direnç:`${an['resistance']:,.2f}`\n\n"
        f"*Motorlar:*\n{ml}\n\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 Skor: `{an['total']}/8` | 📡 {an['regime']}\n"
        f"⚙️ Strateji: `{an['strategy']}` | Yön: `{an['direction']}`",
        parse_mode="Markdown")

async def cmd_futures_baslat(update,ctx):
    ctx.application.bot_data["owner_id"]=update.effective_user.id
    raw=(ctx.args[0].upper() if ctx.args else "BTC")
    symbol=raw if "_" in raw else raw+"_USDT"
    if symbol not in FUTURES_CONFIGS:
        await update.message.reply_text("❌ Desteklenmiyor.",parse_mode="Markdown"); return
    if symbol in active_bots:
        await update.message.reply_text("⚠️ Zaten aktif!",parse_mode="Markdown"); return
    await update.message.reply_text(f"⏳ `{symbol}` analiz + strateji belirleniyor...",parse_mode="Markdown")
    ctx.application.bot_data[f"task_{symbol}"]=asyncio.create_task(futures_loop(ctx.application,symbol))

async def cmd_futures_durdur(update,ctx):
    raw=(ctx.args[0].upper() if ctx.args else "BTC")
    symbol=raw if "_" in raw else raw+"_USDT"
    if symbol not in active_bots:
        await update.message.reply_text("⚠️ Aktif değil.",parse_mode="Markdown"); return
    pnl=close_position(symbol) if active_bots[symbol]["strategy"]=="TREND" else 0
    info=active_bots.pop(symbol); trailing_stops.pop(symbol,None); atr_stops.pop(symbol,None)
    await update.message.reply_text(f"🛑 *{symbol} Durduruldu*\nPNL: `{pnl:+.2f}$`",parse_mode="Markdown")

async def cmd_tum_baslat(update,ctx):
    ctx.application.bot_data["owner_id"]=update.effective_user.id
    await update.message.reply_text("🚀 4 coin başlatılıyor...")
    for symbol in FUTURES_CONFIGS:
        if symbol not in active_bots:
            ctx.application.bot_data[f"task_{symbol}"]=asyncio.create_task(futures_loop(ctx.application,symbol))
            await asyncio.sleep(2)
    await update.message.reply_text("✅ BTC, ETH, SOL, BNB — Futures aktif!")

async def cmd_tum_durdur(update,ctx):
    if not active_bots: await update.message.reply_text("⚠️ Aktif bot yok."); return
    total=sum(close_position(s) if active_bots[s]["strategy"]=="TREND" else 0 for s in list(active_bots.keys()))
    active_bots.clear(); trailing_stops.clear(); atr_stops.clear()
    await update.message.reply_text(f"🛑 Tüm botlar durduruldu.\nToplam PNL: `{total:+.2f}$`",parse_mode="Markdown")

async def cmd_durum(update,ctx):
    if not active_bots: await update.message.reply_text("📭 Aktif bot yok."); return
    lines=["📊 *Aktif Futures Botları*\n━━━━━━━━━━━━━━━━"]
    for symbol,info in active_bots.items():
        price=get_price(symbol) or 0; ts=trailing_stops.get(symbol)
        pnl=_sim_pnl(symbol,price) if SIM_MODE and symbol in _sim_positions else info.get("pnl",0)
        lines.append(
            f"🟢 *{symbol}* | `{info['strategy']}` `{info.get('direction','')}`\n"
            f"   💵 `${price:,.2f}` | Giriş:`${info['entry']:,.2f}`\n"
            f"   {'📈' if pnl>=0 else '📉'} PNL: `{pnl:+.2f}$` | `{LEVERAGE}x`\n"
            f"   🛡 ATR Stop: `${atr_stops.get(symbol,0):,.2f}`\n"
            f"   🏄 {ts.status() if ts else '—'}")
    lines.append(f"\n💼 Bakiye: `${get_balance():,.2f}`")
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Tümünü Durdur",callback_data="tum_durdur")]])
    await update.message.reply_text("\n".join(lines),parse_mode="Markdown",reply_markup=kb)

async def cmd_bakiye(update,ctx):
    bal=get_balance()
    lines=[f"💼 *Futures Bakiye*\n━━━━━━━━━━━━━━\n💵 USDT: `${bal:,.2f}`"]
    if SIM_MODE and _sim_positions:
        lines.append("\n*Açık Pozisyonlar:*")
        for sym,pos in _sim_positions.items():
            price=get_price(sym) or pos["entry"]
            pnl=_sim_pnl(sym,price)
            lines.append(f"  {sym}: `{pos['side']}` @ `${pos['entry']:,.2f}` PNL:`{pnl:+.2f}$`")
    await update.message.reply_text("\n".join(lines),parse_mode="Markdown")

async def cmd_fiyat(update,ctx):
    raw=(ctx.args[0].upper() if ctx.args else "BTC")
    symbol=raw if "_" in raw else raw+"_USDT"
    price=get_price(symbol)
    if price:
        extra=f"\n🛡 ATR Stop:`${atr_stops[symbol]:,.2f}`" if symbol in atr_stops else ""
        await update.message.reply_text(f"💰 *{symbol}*: `${price:,.4f}`{extra}",parse_mode="Markdown")
    else: await update.message.reply_text("❌ Fiyat alınamadı.")

async def callback_handler(update,ctx):
    q=update.callback_query; await q.answer()
    if q.data=="tum_durdur":
        total=sum(close_position(s) if active_bots[s]["strategy"]=="TREND" else 0 for s in list(active_bots.keys()))
        active_bots.clear(); trailing_stops.clear(); atr_stops.clear()
        await q.edit_message_text(f"🛑 Tüm botlar durduruldu. PNL:{total:+.2f}$")

def main():
    if "YOUR" in TELEGRAM_TOKEN: print("❌ TELEGRAM_TOKEN eksik!"); return
    if "YOUR" in MEXC_API_KEY:   print("❌ MEXC_API_KEY eksik!"); return
    app=Application.builder().token(TELEGRAM_TOKEN).build()
    for cmd,handler in [("start",cmd_start),("futures_baslat",cmd_futures_baslat),("futures_durdur",cmd_futures_durdur),
        ("tum_baslat",cmd_tum_baslat),("tum_durdur",cmd_tum_durdur),("durum",cmd_durum),
        ("analiz",cmd_analiz),("bakiye",cmd_bakiye),("fiyat",cmd_fiyat)]:
        app.add_handler(CommandHandler(cmd,handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    log.info(f"🤖 MEXC Futures Bot | {'SİMÜLASYON' if SIM_MODE else 'GERÇEK'} | {LEVERAGE}x")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    main()
