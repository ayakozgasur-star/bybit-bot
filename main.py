import os
import time
from datetime import datetime
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ==============================================================================
# INCREASED MARGIN CONFIG ($50 USDT, 15X LEVERAGE)
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True  # Реал саудаға көшерде False жасаңыз

SYMBOLS = [
    "SOLUSDT", "XRPUSDT", "1000PEPEUSDT", "NEARUSDT", "AVAXUSDT", 
    "ETHUSDT", "DOGEUSDT", "SUIUSDT", "BTCUSDT", "ADAUSDT"
]

LEVERAGE = 15                  # Плечо 15x
FIXED_MARGIN_USDT = 50.0       # Өсірілген маржа: $50 USDT
MAX_OPEN_POSITIONS = 2         # Бір уақытта максимум 2 позиция (Тәуекелді азайту үшін)

# Risk/Reward 2:1 (TP = +10% ROI, SL = -5% ROI)
TP_PCT = 0.0067                # +0.67% баға қозғалысы (ROI +10%)
SL_PCT = 0.0033                # -0.33% баға қозғалысы (ROI -5%)

session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
    demo=IS_DEMO
)

def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [HIGH-MARGIN-BOT] {msg}", flush=True)

def calc_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def format_value(value, step):
    if step is None or step == 0: 
        return str(value)
    step_str = f"{step:.8f}".rstrip('0')
    precision = len(step_str.split('.')[1]) if '.' in step_str else 0
    return f"{round(value, precision):.{precision}f}"

def get_klines(symbol, interval, limit=200):
    try:
        res = session.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        if res.get('retCode') != 0: return None
        list_data = res['result']['list']
        if not list_data: return None
        df = pd.DataFrame(list_data, columns=['start_time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df['start_time'] = pd.to_datetime(pd.to_numeric(df['start_time']), unit='ms')
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df.sort_values('start_time').reset_index(drop=True)
    except Exception as e:
        log(f"Klines алу қатесі ({symbol}): {e}")
        return None

def analyze_market_trend(symbol):
    """1 Сағаттық уақыт аралығында басты трендті анықтау"""
    df_1h = get_klines(symbol, interval="60", limit=200)
    if df_1h is None or len(df_1h) < 200: return "NONE"

    ema_200 = calc_ema(df_1h['close'], 200).iloc[-2]
    last_close = df_1h['close'].iloc[-2]

    if last_close > ema_200:
        return "BULLISH"
    elif last_close < ema_200:
        return "BEARISH"
    return "NONE"

def get_entry_signal(symbol):
    """15-Минуттық шамда кіру сигналы"""
    main_trend = analyze_market_trend(symbol)
    if main_trend == "NONE": return "WAIT"

    df_15m = get_klines(symbol, interval="15", limit=50)
    if df_15m is None or len(df_15m) < 30: return "WAIT"

    df_15m['ema_fast'] = calc_ema(df_15m['close'], 9)
    df_15m['ema_slow'] = calc_ema(df_15m['close'], 21)

    curr = df_15m.iloc[-2]
    prev = df_15m.iloc[-3]

    if main_trend == "BULLISH" and prev['ema_fast'] <= prev['ema_slow'] and curr['ema_fast'] > curr['ema_slow']:
        return "LONG"

    if main_trend == "BEARISH" and prev['ema_fast'] >= prev['ema_slow'] and curr['ema_fast'] < curr['ema_slow']:
        return "SHORT"

    return "WAIT"

def get_active_positions():
    try:
        res = session.get_positions(category="linear", settleCoin="USDT")
        if res.get('retCode') == 0:
            return [p for p in res['result']['list'] if float(p['size']) > 0]
    except Exception as e:
        log(f"Позиция тексеру қатесі: {e}")
    return []

def open_smart_order(symbol):
    signal = get_entry_signal(symbol)
    if signal == "WAIT": return False

    df = get_klines(symbol, "15", limit=5)
    if df is None: return False
    close_price = df['close'].iloc[-1]

    try:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        if res.get('retCode') != 0: return False
        info = res['result']['list'][0]
        qty_step = float(info['lotSizeFilter']['qtyStep'])
        price_step = float(info['priceFilter']['tickSize'])
    except Exception as e:
        log(f"Инструмент ақпараты қатесі ({symbol}): {e}")
        return False

    position_size_usdt = FIXED_MARGIN_USDT * LEVERAGE
    raw_qty = position_size_usdt / close_price

    formatted_qty = format_value(raw_qty, qty_step)
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

    formatted_tp = format_value(tp, price_step)
    formatted_sl = format_value(sl, price_step)

    try:
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except Exception: pass

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
            log(f"💰 [{symbol}] {signal} АШЫЛДЫ! | 15x Плечо | Маржа: ${FIXED_MARGIN_USDT} USDT | TP: {formatted_tp} | SL: {formatted_sl}")
            return True
        else:
            log(f"Ордер ашу қатесі ({symbol}): {res.get('retMsg')}")
    except Exception as e:
        log(f"Ордер жіберу кезіндегі қате: {e}")

    return False

def main():
    log(f"🚀 БОТ ІСКЕ ҚОСЫЛДЫ | Маржа: ${FIXED_MARGIN_USDT} USDT | Плечо: 15x | TP: +10% ROI | SL: -5% ROI")
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
