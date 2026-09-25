import os
import time
from datetime import datetime
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ==============================================================================
# CONFIG / PARAMETERS (DEEP ANALYSIS & SMART MONEY)
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True  # Реал сауда үшін False орнатыңыз

SYMBOLS = [
    "SOLUSDT", "XRPUSDT", "1000PEPEUSDT", "NEARUSDT", "AVAXUSDT", 
    "ETHUSDT", "DOGEUSDT", "SUIUSDT", "BTCUSDT", "ADAUSDT"
]

LEVERAGE = 15                 # Тәуекелді азайту үшін 15x
TARGET_TOTAL_PROFIT = 100.0   # Мақсатты пайда
MAX_OPEN_POSITIONS = 3        # Бір уақытта ашылатын максималды позиция

BASE_MARGIN_USDT = 3.0        # Бастапқы нормалы маржа ($3)
MIN_TARGET_PROFIT = 0.50      # Минусты жабу кезіндегі таза пайда

COOLDOWN_MINUTES = 15         # Минустан кейін кідіріс уақыты (минут)

# ==============================================================================
# INITIALIZATION & GLOBALS
# ==============================================================================
session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
    demo=IS_DEMO
)

accumulated_losses = {}       # Жиналған минустар
last_checked_pnl_time = {}   # Соңғы тексерілген PnL
cooldown_tracker = {}        # Кулдаун уақытын бақылау

def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [INFO] {msg}", flush=True)

def get_wallet_balance():
    try:
        res = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        if res['retCode'] == 0:
            return float(res['result']['list'][0]['coin'][0]['walletBalance'])
    except Exception as e:
        log(f"Баланс алу қатесі: {e}")
    return 0.0

# ==============================================================================
# ADVANCED INDICATORS (ADX, ATR, EMA, RSI)
# ==============================================================================
def calc_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calc_rsi(series, length=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=length).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=length).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))

def calc_atr(df, length=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(length).mean()

def calc_adx(df, length=14):
    """Нарық трендте ме әлде флэтте ме екенін анықтайды (ADX > 20 - Күшті тренд)"""
    df = df.copy()
    df['up'] = df['high'] - df['high'].shift(1)
    df['down'] = df['low'].shift(1) - df['low']
    
    df['+dm'] = np.where((df['up'] > df['down']) & (df['up'] > 0), df['up'], 0)
    df['-dm'] = np.where((df['down'] > df['up']) & (df['down'] > 0), df['down'], 0)
    
    tr = calc_atr(df, length)
    df['+di'] = 100 * (calc_ema(df['+dm'], length) / (tr + 1e-9))
    df['-di'] = 100 * (calc_ema(df['-dm'], length) / (tr + 1e-9))
    
    dx = 100 * np.abs(df['+di'] - df['-di']) / (df['+di'] + df['-di'] + 1e-9)
    adx = calc_ema(dx, length)
    return adx

def get_klines(symbol, interval="5", limit=100):
    try:
        res = session.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        if res['retCode'] != 0: return None
        list_data = res['result']['list']
        df = pd.DataFrame(list_data, columns=['start_time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df['start_time'] = pd.to_datetime(pd.to_numeric(df['start_time']), unit='ms')
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df.sort_values('start_time').reset_index(drop=True)
    except Exception:
        return None

# ==============================================================================
# MULTI-TIMEFRAME DEEP ANALYSIS
# ==============================================================================
def get_higher_tf_trend(symbol):
    """15-минуттық шам бойынша жалпы трендті анықтау"""
    df_15m = get_klines(symbol, interval="15", limit=60)
    if df_15m is None or len(df_15m) < 50: return "NEUTRAL"
    
    ema_50 = calc_ema(df_15m['close'], 50).iloc[-2]
    ema_200 = calc_ema(df_15m['close'], 200).iloc[-2] if len(df_15m) >= 200 else ema_50
    close = df_15m['close'].iloc[-2]
    
    if close > ema_50:
        return "BULLISH"
    elif close < ema_50:
        return "BEARISH"
    return "NEUTRAL"

def analyze_entry_signal(symbol):
    """5-минуттық таймфреймде сапалы сигнал іздеу"""
    # 1. Кулдаун тексеру
    if symbol in cooldown_tracker:
        elapsed = (time.time() - cooldown_tracker[symbol]) / 60
        if elapsed < COOLDOWN_MINUTES:
            return "WAIT", 0, 0 # Кулдаун біткенше тоқтату

    # 2. Жоғарғы трендті алу (15M)
    macro_trend = get_higher_tf_trend(symbol)
    if macro_trend == "NEUTRAL":
        return "WAIT", 0, 0

    df = get_klines(symbol, interval="5", limit=60)
    if df is None or len(df) < 50: return "WAIT", 0, 0

    df['ema_fast'] = calc_ema(df['close'], 9)
    df['ema_slow'] = calc_ema(df['close'], 21)
    df['rsi'] = calc_rsi(df['close'], 14)
    df['adx'] = calc_adx(df, 14)
    df['atr'] = calc_atr(df, 14)
    df['vol_ma'] = df['volume'].rolling(20).mean()

    last = df.iloc[-2]
    close = last['close']
    atr = last['atr']

    # Флэт фильтрі: Егер ADX < 20 немесе Көлем аз болса, кірмейміз
    if last['adx'] < 20 or last['volume'] < last['vol_ma'] * 0.9:
        return "WAIT", 0, 0

    # LONG сигналы: 15M Тренд + 5M EMA Cross + RSI > 52
    if macro_trend == "BULLISH" and last['ema_fast'] > last['ema_slow'] and 52 < last['rsi'] < 70:
        tp_price = close + (atr * 2.0) # Динамикалық TP (2x ATR)
        sl_price = close - (atr * 1.2) # Динамикалық SL (1.2x ATR)
        return "LONG", tp_price, sl_price

    # SHORT сигналы: 15M Тренд + 5M EMA Cross + RSI < 48
    elif macro_trend == "BEARISH" and last['ema_fast'] < last['ema_slow'] and 30 < last['rsi'] < 48:
        tp_price = close - (atr * 2.0)
        sl_price = close + (atr * 1.2)
        return "SHORT", tp_price, sl_price

    return "WAIT", 0, 0

# ==============================================================================
# PNL TRACKER & COOLDOWN MANAGEMENT
# ==============================================================================
def update_loss_tracker(symbol):
    global accumulated_losses, last_checked_pnl_time, cooldown_tracker
    
    time.sleep(1.0)
    try:
        res = session.get_closed_pnl(category="linear", symbol=symbol, limit=1)
        if res['retCode'] == 0 and len(res['result']['list']) > 0:
            last_order = res['result']['list'][0]
            updated_time = last_order['updatedTime']
            
            if last_checked_pnl_time.get(symbol) == updated_time:
                return
            
            last_checked_pnl_time[symbol] = updated_time
            last_pnl = float(last_order['closedPnl'])
            curr_loss = accumulated_losses.get(symbol, 0.0)

            if last_pnl > 0:
                new_loss = curr_loss - last_pnl
                if new_loss <= 0:
                    accumulated_losses[symbol] = 0.0
                    log(f"✅ [{symbol}] ТЕЙК-ПРОФИТ (+{last_pnl:.2f} USDT)! Барлық минус жабылды.")
                else:
                    accumulated_losses[symbol] = new_loss
                    log(f"✅ [{symbol}] ТЕЙК-ПРОФИТ (+{last_pnl:.2f} USDT)! Қалған минус: -{new_loss:.2f} USDT.")
            else:
                accumulated_losses[symbol] = curr_loss + abs(last_pnl)
                cooldown_tracker[symbol] = time.time() # Минустан кейін 15 минут кулдаун
                log(f"🛑 [{symbol}] СТОП-ЛОСС (-{abs(last_pnl):.2f} USDT). Жиналған минус: -{accumulated_losses[symbol]:.2f} USDT. ⏳ 15 мин кулдаун қосылды.")

    except Exception as e:
        log(f"P&L тексеру қатесі ({symbol}): {e}")

# ==============================================================================
# TRADE EXECUTION
# ==============================================================================
def get_active_positions():
    try:
        res = session.get_positions(category="linear", settleCoin="USDT")
        if res['retCode'] == 0:
            return [p for p in res['result']['list'] if float(p['size']) > 0]
    except Exception as e:
        log(f"Позиция тексеру қатесі: {e}")
    return []

def format_value(value, step):
    if step is None or step == 0: return str(value)
    precision = 0
    step_str = f"{step:.8f}".rstrip('0')
    if '.' in step_str: precision = len(step_str.split('.')[1])
    return f"{round(value, precision):.{precision}f}"

def open_smart_order(symbol):
    update_loss_tracker(symbol)
    
    signal, tp_price, sl_price = analyze_entry_signal(symbol)
    if signal == "WAIT":
        return False

    current_loss = accumulated_losses.get(symbol, 0.0)

    # Маржаны динамикалық есептеу
    if current_loss > 0:
        needed_margin = (current_loss + MIN_TARGET_PROFIT) / (LEVERAGE * 0.008) # ~0.8% пайда есебінен
        usdt_margin = round(min(max(needed_margin, BASE_MARGIN_USDT), 30.0), 2) # Макс $30 лимит
    else:
        usdt_margin = BASE_MARGIN_USDT

    df = get_klines(symbol, "5", limit=5)
    if df is None: return False
    close_price = df['close'].iloc[-1]

    # Интструмент ақпараты
    try:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        info = res['result']['list'][0]
        qty_step = float(info['lotSizeFilter']['qtyStep'])
        price_step = float(info['priceFilter']['tickSize'])
    except:
        return False

    position_size_usdt = usdt_margin * LEVERAGE
    raw_qty = position_size_usdt / close_price
    formatted_qty = format_value(raw_qty, qty_step)

    if float(formatted_qty) <= 0: return False

    order_side = "Buy" if signal == "LONG" else "Sell"
    pos_idx = 1 if signal == "LONG" else 2

    # Плечо орнату
    try:
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except: pass

    res = session.place_order(
        category="linear",
        symbol=symbol,
        side=order_side,
        orderType="Market", # Мықты трендте кіру үшін Маркет
        qty=formatted_qty,
        takeProfit=format_value(tp_price, price_step),
        stopLoss=format_value(sl_price, price_step),
        positionIdx=pos_idx
    )

    if res['retCode'] == 0:
        log(f"🧠 [SMART ENTRY] [{symbol}] {signal} АШЫЛДЫ | Маржа: ${usdt_margin} USDT | TP: {tp_price:.4f} | SL: {sl_price:.4f}")
        return True
    return False

# ==============================================================================
# MAIN LOOP
# ==============================================================================
def main():
    log("🚀 Терең Аналитикалық Скрипт Іске Қосылды (15M Macro Trend + ADX Filter + Smart Margin)")
    
    init_bal = get_wallet_balance()
    log(f"💵 Бастапқы Баланс: {init_bal:.2f} USDT")

    while True:
        try:
            positions = get_active_positions()
            active_symbols = [p['symbol'] for p in positions]

            if len(active_symbols) < MAX_OPEN_POSITIONS:
                for symbol in SYMBOLS:
                    if symbol not in active_symbols:
                        if open_smart_order(symbol):
                            time.sleep(1)
                            if len(get_active_positions()) >= MAX_OPEN_POSITIONS:
                                break

            time.sleep(5) # 5 секунд сайын сканерлеу

        except Exception as e:
            log(f"Негізгі цикл қатесі: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
