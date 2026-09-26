import os
import time
from datetime import datetime
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ==============================================================================
# SAFE & STRICT CONFIG
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True

SYMBOLS = [
    "SOLUSDT", "XRPUSDT", "1000PEPEUSDT", "NEARUSDT", "AVAXUSDT", 
    "ETHUSDT", "DOGEUSDT", "SUIUSDT", "BTCUSDT", "ADAUSDT"
]

LEVERAGE = 10                 # Қауіпсіз плечо: 10x
FIXED_MARGIN_USDT = 3.0       # ТҮБЕГЕЙЛІ ФИКСИРОВАННЫЙ МАРЖА ($3)
MAX_OPEN_POSITIONS = 2        # Бір уақытта максимум 2 ордер

TP_PCT = 0.012                # Take-Profit: +1.2%
SL_PCT = 0.006                # Stop-Loss: -0.6% (Risk/Reward 1:2)

session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
    demo=IS_DEMO
)

def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [INFO] {msg}", flush=True)

def calc_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def get_klines(symbol, interval="15", limit=210):
    try:
        res = session.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        if res['retCode'] != 0: return None
        list_data = res['result']['list']
        df = pd.DataFrame(list_data, columns=['start_time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df['start_time'] = pd.to_datetime(pd.to_numeric(df['start_time']), unit='ms')
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df.sort_values('start_time').reset_index(drop=True)
    except:
        return None

def get_market_trend_1h(symbol):
    """1-Сағаттық күшті трендті анықтау"""
    df_1h = get_klines(symbol, interval="60", limit=210)
    if df_1h is None or len(df_1h) < 200: return "NONE"
    
    close = df_1h['close'].iloc[-2]
    ema_200 = calc_ema(df_1h['close'], 200).iloc[-2]

    if close > ema_200:
        return "BULLISH" # Тек LONG
    elif close < ema_200:
        return "BEARISH" # Тек SHORT
    return "NONE"

def get_signal(symbol):
    """15-Минуттық шамдар бойынша қатаң фильтр"""
    trend_1h = get_market_trend_1h(symbol)
    if trend_1h == "NONE": return "WAIT"

    df_15m = get_klines(symbol, interval="15", limit=60)
    if df_15m is None or len(df_15m) < 50: return "WAIT"

    df_15m['ema_fast'] = calc_ema(df_15m['close'], 9)
    df_15m['ema_slow'] = calc_ema(df_15m['close'], 21)
    
    last = df_15m.iloc[-2]

    # Тек 1H тренд бағытында 15M EMA кесіп өткенде ғана кіреді
    if trend_1h == "BULLISH" and last['ema_fast'] > last['ema_slow']:
        return "LONG"
    elif trend_1h == "BEARISH" and last['ema_fast'] < last['ema_slow']:
        return "SHORT"

    return "WAIT"

def get_active_positions():
    try:
        res = session.get_positions(category="linear", settleCoin="USDT")
        if res['retCode'] == 0:
            return [p for p in res['result']['list'] if float(p['size']) > 0]
    except: pass
    return []

def open_safe_order(symbol):
    signal = get_signal(symbol)
    if signal == "WAIT": return False

    df = get_klines(symbol, "15", limit=5)
    if df is None: return False
    close_price = df['close'].iloc[-1]

    try:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        info = res['result']['list'][0]
        qty_step = float(info['lotSizeFilter']['qtyStep'])
        price_step = float(info['priceFilter']['tickSize'])
    except: return False

    position_size_usdt = FIXED_MARGIN_USDT * LEVERAGE
    raw_qty = position_size_usdt / close_price
    
    # Қалдық дәлдігі
    precision = len(str(qty_step).split('.')[1]) if '.' in str(qty_step) else 0
    formatted_qty = f"{round(raw_qty, precision):.{precision}f}"
    if float(formatted_qty) <= 0: return False

    if signal == "LONG":
        side = "Buy"
        pos_idx = 1
        tp = close_price * (1 + TP_PCT)
        sl = close_price * (1 - SL_PCT)
    else:
        side = "Sell"
        pos_idx = 2
        tp = close_price * (1 - TP_PCT)
        sl = close_price * (1 + SL_PCT)

    p_precision = len(str(price_step).split('.')[1]) if '.' in str(price_step) else 0

    try:
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except: pass

    res = session.place_order(
        category="linear",
        symbol=symbol,
        side=side,
        orderType="Market",
        qty=formatted_qty,
        takeProfit=f"{round(tp, p_precision):.{p_precision}f}",
        stopLoss=f"{round(sl, p_precision):.{p_precision}f}",
        positionIdx=pos_idx
    )

    if res['retCode'] == 0:
        log(f"🟢 [{symbol}] {signal} АШЫЛДЫ | Маржа: ${FIXED_MARGIN_USDT} USDT (Мартингейл өшірілген) | TP: +1.2% | SL: -0.6%")
        return True
    return False

def main():
    log("🛡 Қауіпсіз Скрипт Іске Қосылды (Мартингейлсіз + 1H Trend + 1:2 R/R)")
    while True:
        try:
            positions = get_active_positions()
            active_symbols = [p['symbol'] for p in positions]

            if len(active_symbols) < MAX_OPEN_POSITIONS:
                for symbol in SYMBOLS:
                    if symbol not in active_symbols:
                        if open_safe_order(symbol):
                            time.sleep(2)
                            if len(get_active_positions()) >= MAX_OPEN_POSITIONS:
                                break

            time.sleep(10)
        except Exception as e:
            log(f"Қате: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
