import os
import time
from datetime import datetime
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ==============================================================================
# PROFESSIONAL STRATEGY CONFIG (DEMO MODE)
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True  # Демо-счет режимі белсенді

SYMBOLS = [
    "SOLUSDT", "XRPUSDT", "1000PEPEUSDT", "NEARUSDT", "AVAXUSDT", 
    "ETHUSDT", "DOGEUSDT", "SUIUSDT", "BTCUSDT", "ADAUSDT"
]

LEVERAGE = 10                 # Қауіпсіз плечо: 10x
FIXED_MARGIN_USDT = 5.0       # Тек фиксированный маржа ($5 USDT)
MAX_OPEN_POSITIONS = 3        # Бір уақытта максимум 3 ашық позиция

# Risk/Reward: 1:2 (Стоптан 2 есе көп Тейк)
RR_RATIO = 2.0 

session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
    demo=IS_DEMO
)

def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [DEMO-SMART] {msg}", flush=True)

# ==============================================================================
# INDICATORS
# ==============================================================================
def calc_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calc_atr(df, length=14):
    """Бағаның құбылмалылығын анықтау (Average True Range)"""
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(length).mean()

def format_value(value, step):
    if step is None or step == 0: 
        return str(value)
    step_str = f"{step:.8f}".rstrip('0')
    precision = len(step_str.split('.')[1]) if '.' in step_str else 0
    return f"{round(value, precision):.{precision}f}"

def get_klines(symbol, interval, limit=200):
    try:
        res = session.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        if res.get('retCode') != 0: 
            return None
        list_data = res['result']['list']
        if not list_data:
            return None
        df = pd.DataFrame(list_data, columns=['start_time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df['start_time'] = pd.to_datetime(pd.to_numeric(df['start_time']), unit='ms')
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df.sort_values('start_time').reset_index(drop=True)
    except Exception as e:
        log(f"Klines алу қатесі ({symbol}): {e}")
        return None

# ==============================================================================
# MARKET ANALYSIS & SIGNALS
# ==============================================================================
def analyze_market_trend(symbol):
    """1 Сағаттық уақыт аралығында басты трендті анықтау (200 EMA)"""
    df_1h = get_klines(symbol, interval="60", limit=200)
    if df_1h is None or len(df_1h) < 200: 
        return "NONE"

    ema_200 = calc_ema(df_1h['close'], 200).iloc[-2]
    last_close = df_1h['close'].iloc[-2]

    if last_close > ema_200:
        return "BULLISH"
    elif last_close < ema_200:
        return "BEARISH"
    return "NONE"

def get_entry_signal(symbol):
    """15-Минуттық шамда кіру сигналын тексеру (Тренд + EMA Cross + ATR)"""
    main_trend = analyze_market_trend(symbol)
    if main_trend == "NONE": 
        return "WAIT", 0, 0

    df_15m = get_klines(symbol, interval="15", limit=100)
    if df_15m is None or len(df_15m) < 50: 
        return "WAIT", 0, 0

    df_15m['ema_fast'] = calc_ema(df_15m['close'], 9)
    df_15m['ema_slow'] = calc_ema(df_15m['close'], 21)
    df_15m['atr'] = calc_atr(df_15m, 14)

    curr = df_15m.iloc[-2]
    prev = df_15m.iloc[-3]
    
    atr_val = curr['atr']
    if pd.isna(atr_val) or atr_val <= 0: 
        return "WAIT", 0, 0

    # Bullish Cross (Жоғарыға кіру)
    if main_trend == "BULLISH" and prev['ema_fast'] <= prev['ema_slow'] and curr['ema_fast'] > curr['ema_slow']:
        stop_dist = atr_val * 1.5
        take_dist = stop_dist * RR_RATIO
        return "LONG", stop_dist, take_dist

    # Bearish Cross (Төменге кіру)
    if main_trend == "BEARISH" and prev['ema_fast'] >= prev['ema_slow'] and curr['ema_fast'] < curr['ema_slow']:
        stop_dist = atr_val * 1.5
        take_dist = stop_dist * RR_RATIO
        return "SHORT", stop_dist, take_dist

    return "WAIT", 0, 0

# ==============================================================================
# TRADE EXECUTION
# ==============================================================================
def get_active_positions():
    try:
        res = session.get_positions(category="linear", settleCoin="USDT")
        if res.get('retCode') == 0:
            return [p for p in res['result']['list'] if float(p['size']) > 0]
    except Exception as e:
        log(f"Позицияларды тексеру қатесі: {e}")
    return []

def open_smart_order(symbol):
    signal, stop_dist, take_dist = get_entry_signal(symbol)
    if signal == "WAIT": 
        return False

    df = get_klines(symbol, "15", limit=5)
    if df is None: 
        return False
    close_price = df['close'].iloc[-1]

    try:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        if res.get('retCode') != 0: 
            return False
        info = res['result']['list'][0]
        qty_step = float(info['lotSizeFilter']['qtyStep'])
        price_step = float(info['priceFilter']['tickSize'])
    except Exception as e:
        log(f"Инструмент ақпаратын алу қатесі ({symbol}): {e}")
        return False

    position_size_usdt = FIXED_MARGIN_USDT * LEVERAGE
    raw_qty = position_size_usdt / close_price

    formatted_qty = format_value(raw_qty, qty_step)
    if float(formatted_qty) <= 0: 
        return False

    if signal == "LONG":
        side = "Buy"
        pos_idx = 1
        tp = close_price + take_dist
        sl = close_price - stop_dist
    else:
        side = "Sell"
        pos_idx = 2
        tp = close_price - take_dist
        sl = close_price + stop_dist

    formatted_tp = format_value(tp, price_step)
    formatted_sl = format_value(sl, price_step)

    try:
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except Exception:
        pass # Плечо бұрын орнатылған болса қате бермеуі үшін

    try:
        res = session.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=formatted_qty,
            takeProfit=formatted_tp,
            stopLoss=formatted_sl,
            positionIdx=pos_idx
        )

        if res.get('retCode') == 0:
            log(f"🔥 [{symbol}] {signal} АШЫЛДЫ! | Маржа: ${FIXED_MARGIN_USDT} USDT | Бағасы: {close_price} | TP: {formatted_tp} | SL: {formatted_sl}")
            return True
        else:
            log(f"Ордер ашу қатесі ({symbol}): {res.get('retMsg')}")
    except Exception as e:
        log(f"Ордер жіберу кезіндегі қате: {e}")

    return False

# ==============================================================================
# MAIN LOOP
# ==============================================================================
def main():
    log("💎 ДЕМО: Кәсіби Trend + ATR (1:2 Risk/Reward) Бот Іске Қосылды")
    while True:
        try:
            positions = get_active_positions()
            active_symbols = [p['symbol'] for p in positions]

            if len(active_symbols) < MAX_OPEN_POSITIONS:
                for symbol in SYMBOLS:
                    if symbol not in active_symbols:
                        if open_smart_order(symbol):
                            time.sleep(2)
                            if len(get_active_positions()) >= MAX_OPEN_POSITIONS:
                                break

            time.sleep(15)
        except Exception as e:
            log(f"Басты цикл қатесі: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
