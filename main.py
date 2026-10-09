import os
import time
from datetime import datetime
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ==============================================================================
# ALWAYS-SIGNAL CHECK 10-STEP MARTINGALE CONFIG
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True  # Demo режимі

SYMBOLS = ["SOLUSDT", "XRPUSDT", "1000PEPEUSDT", "NEARUSDT", "ETHUSDT"]

LEVERAGE = 15                  # 15x Плечо
BASE_MARGIN = 5.0              # 1-ордер маржасы $5 USDT
MAX_STEPS = 10                 # Максимум 10 адым
MAX_OPEN_POSITIONS = 2         # Бір уақытта максимум 2 монета
STEP_DISTANCE_PCT = 0.008      # Минималды қашықтық (-0.8%)

session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
    demo=IS_DEMO
)

symbol_state = {
    symbol: {
        "step": 0, 
        "active": False, 
        "pending_next_step": False,  # Келесі адым үшін сигнал күту режимі
        "last_price": 0.0,
        "avg_price": 0.0,
        "total_qty": 0.0
    } for symbol in SYMBOLS
}

def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [SIGNAL-MARTINGALE] {msg}", flush=True)

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
        df = pd.DataFrame(res['result']['list'], columns=['start_time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        for col in ['open', 'high', 'low', 'close']: 
            df[col] = df[col].astype(float)
        return df.iloc[::-1].reset_index(drop=True)
    except Exception as e:
        log(f"Klines қатесі ({symbol}): {e}")
        return None

# ==============================================================================
# ИНДИКАТОРЛАР ЖӘНЕ ТЕРЕҢ АНАЛИЗ (EMA, RSI, ADX)
# ==============================================================================
def calc_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_adx(df, length=14):
    df = df.copy()
    df['up'] = df['high'] - df['high'].shift(1)
    df['down'] = df['low'].shift(1) - df['low']
    df['+dm'] = np.where((df['up'] > df['down']) & (df['up'] > 0), df['up'], 0)
    df['-dm'] = np.where((df['down'] > df['up']) & (df['down'] > 0), df['down'], 0)
    
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    df['tr'] = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    
    df['atr'] = df['tr'].rolling(length).mean()
    df['+di'] = 100 * (df['+dm'].rolling(length).mean() / df['atr'])
    df['-di'] = 100 * (df['-dm'].rolling(length).mean() / df['atr'])
    
    df['dx'] = 100 * np.abs(df['+di'] - df['-di']) / (df['+di'] + df['-di'])
    return df['dx'].rolling(length).mean()

def analyze_deep_market(symbol):
    """ӘРБІР адым алдында шақырылатын талдау"""
    df_1h = get_klines(symbol, interval="60", limit=200)
    if df_1h is None or len(df_1h) < 200: return "WAIT"
    
    df_1h['ema200'] = calc_ema(df_1h['close'], 200)
    main_trend = "BULL" if df_1h['close'].iloc[-1] >= df_1h['ema200'].iloc[-1] else "BEAR"

    df_15m = get_klines(symbol, interval="15", limit=100)
    if df_15m is None or len(df_15m) < 50: return "WAIT"

    df_15m['ema_fast'] = calc_ema(df_15m['close'], 9)
    df_15m['ema_slow'] = calc_ema(df_15m['close'], 21)
    df_15m['rsi'] = calc_rsi(df_15m['close'], 14)
    df_15m['adx'] = calc_adx(df_15m, 14)

    curr = df_15m.iloc[-1]
    prev = df_15m.iloc[-2]

    # ADX Флет фильтрі (ADX > 20 болуы шарт)
    if pd.isna(curr['adx']) or curr['adx'] < 20:
        return "WAIT"

    if main_trend == "BULL" and prev['ema_fast'] <= prev['ema_slow'] and curr['ema_fast'] > curr['ema_slow']:
        if curr['rsi'] > 50: return "Buy"

    if main_trend == "BEAR" and prev['ema_fast'] >= prev['ema_slow'] and curr['ema_fast'] < curr['ema_slow']:
        if curr['rsi'] < 50: return "Sell"

    return "WAIT"

# ==============================================================================
# ДИНАМИКАЛЫҚ МАРЖА ЖӘНЕ ТРЕЙДИНГ
# ==============================================================================
def calculate_margin_for_step(step_index):
    margin = BASE_MARGIN
    for i in range(step_index):
        if i < 3:
            multiplier = 2.0
        elif i < 6:
            multiplier = 1.5
        else:
            multiplier = 1.2
        margin *= multiplier
    return round(margin, 2)

def get_active_positions(symbol=None):
    try:
        res = session.get_positions(category="linear", settleCoin="USDT")
        if res.get('retCode') == 0:
            positions = [p for p in res['result']['list'] if float(p['size']) > 0]
            if symbol:
                return [p for p in positions if p['symbol'] == symbol]
            return positions
    except Exception as e:
        log(f"Позиция тексеру қатесі: {e}")
    return []

def open_martingale_step(symbol, side, step_index):
    df = get_klines(symbol, interval="15", limit=5)
    if df is None: return False
    close_price = df['close'].iloc[-1]

    # Қашықтықты тексеру (2-адымнан бастап)
    if step_index > 0 and symbol_state[symbol]["last_price"] > 0:
        last_price = symbol_state[symbol]["last_price"]
        price_diff = abs(close_price - last_price) / last_price
        if price_diff < STEP_DISTANCE_PCT:
            return False  # Баға әлі -0.8% жылжыған жоқ

    try:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        info = res['result']['list'][0]
        qty_step = float(info['lotSizeFilter']['qtyStep'])
        price_step = float(info['priceFilter']['tickSize'])
    except Exception: return False

    margin = calculate_margin_for_step(step_index)
    position_size_usdt = margin * LEVERAGE
    raw_qty = position_size_usdt / close_price
    formatted_qty = format_value(raw_qty, qty_step)

    prev_qty = symbol_state[symbol]["total_qty"]
    prev_avg = symbol_state[symbol]["avg_price"]
    curr_qty = float(formatted_qty)

    if prev_qty > 0:
        new_avg_price = ((prev_avg * prev_qty) + (close_price * curr_qty)) / (prev_qty + curr_qty)
    else:
        new_avg_price = close_price

    tp_pct = 0.005  # +0.5% TP
    sl_pct = 0.004  # -0.4% SL

    if side == "Buy":
        pos_idx = 1
        tp = new_avg_price * (1 + tp_pct)
        sl = close_price * (1 - sl_pct)
    else:
        pos_idx = 2
        tp = new_avg_price * (1 - tp_pct)
        sl = close_price * (1 + sl_pct)

    formatted_tp = format_value(tp, price_step)
    formatted_sl = format_value(sl, price_step)

    try:
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except Exception: pass

    try:
        res = session.place_order(
            category="linear", symbol=symbol, side=side, orderType="Market",
            qty=formatted_qty, takeProfit=formatted_tp, stopLoss=formatted_sl, positionIdx=pos_idx
        )
        if res.get('retCode') == 0:
            direction_name = "LONG" if side == "Buy" else "SHORT"
            log(f"🔥 [{symbol}] СИГНАЛ БОЙЫНША {direction_name} АШЫЛДЫ! | АДЫМ: {step_index + 1}/{MAX_STEPS} | Маржа: ${margin} USDT")
            
            symbol_state[symbol]["step"] = step_index
            symbol_state[symbol]["active"] = True
            symbol_state[symbol]["pending_next_step"] = False
            symbol_state[symbol]["last_price"] = close_price
            symbol_state[symbol]["avg_price"] = new_avg_price
            symbol_state[symbol]["total_qty"] += curr_qty
            return True
    except Exception as e:
        log(f"Ордер жіберу қатесі: {e}")
    return False

def check_last_order_pnl(symbol):
    try:
        res = session.get_closed_pnl(category="linear", symbol=symbol, limit=1)
        if res.get('retCode') == 0 and res['result']['list']:
            return float(res['result']['list'][0]['closedPnl'])
    except Exception: pass
    return 0

def reset_symbol_state(symbol):
    symbol_state[symbol]["step"] = 0
    symbol_state[symbol]["active"] = False
    symbol_state[symbol]["pending_next_step"] = False
    symbol_state[symbol]["last_price"] = 0.0
    symbol_state[symbol]["avg_price"] = 0.0
    symbol_state[symbol]["total_qty"] = 0.0

def main():
    log(f"🚀 ӘРБІР АДЫМДА СИГНАЛ ТЕКСЕРЕТІН МАРТИНГЕЙЛ БОТЫ ІСКЕ ҚОСЫЛДЫ")
    while True:
        try:
            all_active_positions = get_active_positions()
            active_symbols = [p['symbol'] for p in all_active_positions]

            for symbol in SYMBOLS:
                pos = [p for p in all_active_positions if p['symbol'] == symbol]
                
                # 1. Егер ашық ордер жабылса
                if not pos and symbol_state[symbol]["active"]:
                    last_pnl = check_last_order_pnl(symbol)
                    curr_step = symbol_state[symbol]["step"]
                    
                    if last_pnl > 0:
                        log(f"✅ [{symbol}] ПЛЮС! PnL: +${round(last_pnl, 2)}. Цикл сәтті аяқталды.")
                        reset_symbol_state(symbol)
                    else:
                        next_step = curr_step + 1
                        if next_step < MAX_STEPS:
                            log(f"❌ [{symbol}] МИНУС. {next_step + 1}-адым үшін СИГНАЛ КҮТЕМІЗ (Маржа: ${calculate_margin_for_step(next_step)})...")
                            symbol_state[symbol]["step"] = next_step
                            symbol_state[symbol]["active"] = False
                            symbol_state[symbol]["pending_next_step"] = True
                        else:
                            log(f"⚠️ [{symbol}] 10 адым таусылды. 1-адымға ораламыз.")
                            reset_symbol_state(symbol)

                # 2. Адамдар үшін сигнал күту (Бірінші ордер де, Мартингейл адымдары да СИГНАЛМЕН ашылады)
                if not pos:
                    # Егер бұл 1-адым болса, позиция лимитін тексереміз
                    if symbol_state[symbol]["step"] == 0 and len(active_symbols) >= MAX_OPEN_POSITIONS:
                        continue

                    # СИГНАЛДЫ ТЕКСЕРУ
                    signal = analyze_deep_market(symbol)
                    if signal != "WAIT":
                        step_num = symbol_state[symbol]["step"]
                        log(f"🎯 [{symbol}] {step_num + 1}-АДЫМҒА СИГНАЛ ТАБЫЛДЫ: {signal}!")
                        if open_martingale_step(symbol, signal, step_num):
                            if symbol not in active_symbols:
                                active_symbols.append(symbol)

            time.sleep(15)
        except Exception as e:
            log(f"Басты цикл қатесі: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
