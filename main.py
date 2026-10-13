import os
import time
from datetime import datetime
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ==============================================================================
# SMART PYRAMIDING (ANTI-MARTINGALE) CONFIG (SOL & ETH ONLY)
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True

SYMBOLS = ["SOLUSDT", "ETHUSDT"]

LEVERAGE = 15                  
BASE_MARGIN = 5.0              # Бастапқы маржа ($5)
MAX_PYRAMID_STEPS = 3          # Тренд бойымен ең көп дегенде 3 рет позиция қосылады
MAX_OPEN_POSITIONS = 2         # Максималды ашық монета саны
ADD_STEP_PCT = 0.0035          # Баға +0.35% өскен сайын жаңа қадам қосылады

session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET, demo=IS_DEMO)

symbol_state = {
    symbol: {
        "step": 0, "active": False, "last_entry_price": 0.0,
        "avg_price": 0.0, "total_qty": 0.0, "entry_side": None,
        "stop_loss_price": 0.0
    } for symbol in SYMBOLS
}

def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [PYRAMID-BOT] {msg}", flush=True)

def format_value(value, step):
    if step is None or step == 0: return str(value)
    step_str = f"{step:.8f}".rstrip('0')
    precision = len(step_str.split('.')[1]) if '.' in step_str else 0
    return f"{round(value, precision):.{precision}f}"

def get_klines(symbol, interval, limit=60):
    try:
        res = session.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        if res.get('retCode') != 0: return None
        df = pd.DataFrame(res['result']['list'], columns=['start_time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        for col in ['open', 'high', 'low', 'close', 'volume']: 
            df[col] = df[col].astype(float)
        return df.iloc[::-1].reset_index(drop=True)
    except Exception as e:
        log(f"Klines қатесі ({symbol}): {e}")
        return None

# ==============================================================================
# ЖЫЛДАМ ИНДИКАТОРЛАР (EMA 3/8 + ATR + STOCHASTIC RSI)
# ==============================================================================
def calc_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_stoch_rsi(series, period=14, smooth_k=3, smooth_d=3):
    rsi = calc_rsi(series, period)
    min_rsi = rsi.rolling(window=period).min()
    max_rsi = rsi.rolling(window=period).max()
    stoch_rsi = (rsi - min_rsi) / (max_rsi - min_rsi + 1e-9)
    k = stoch_rsi.rolling(window=smooth_k).mean() * 100
    d = k.rolling(window=smooth_d).mean()
    return k, d

def calc_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def check_entry_signal(symbol):
    df = get_klines(symbol, interval="5", limit=60)
    if df is None or len(df) < 30: return "WAIT", 0.0, 0.0

    df['ema3'] = calc_ema(df['close'], 3)
    df['ema8'] = calc_ema(df['close'], 8)
    df['stoch_k'], df['stoch_d'] = calc_stoch_rsi(df['close'])
    df['atr'] = calc_atr(df)
    
    curr = df.iloc[-1]
    prev = df.iloc[-2]

    current_close = curr['close']
    current_atr = curr['atr'] if not np.isnan(curr['atr']) else current_close * 0.003

    # Лонг сигналы
    if prev['ema3'] <= prev['ema8'] and curr['ema3'] > curr['ema8']:
        if curr['stoch_k'] < 80:
            return "Buy", current_close, current_atr

    # Шорт сигналы
    if prev['ema3'] >= prev['ema8'] and curr['ema3'] < curr['ema8']:
        if curr['stoch_k'] > 20:
            return "Sell", current_close, current_atr

    return "WAIT", current_close, current_atr

# ==============================================================================
# ОРДЕР БАСҚАРУ ЖӘНЕ ПИРАМИДИНГ
# ==============================================================================
def get_active_positions(symbol=None):
    try:
        res = session.get_positions(category="linear", settleCoin="USDT")
        if res.get('retCode') == 0:
            positions = [p for p in res['result']['list'] if float(p['size']) > 0]
            if symbol: return [p for p in positions if p['symbol'] == symbol]
            return positions
    except Exception: pass
    return []

def open_initial_position(symbol, side, close_price, atr_value):
    try:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        info = res['result']['list'][0]
        qty_step = float(info['lotSizeFilter']['qtyStep'])
        price_step = float(info['priceFilter']['tickSize'])
    except Exception: return False

    margin = BASE_MARGIN
    position_size_usdt = margin * LEVERAGE
    raw_qty = position_size_usdt / close_price
    formatted_qty = format_value(raw_qty, qty_step)

    sl_dist = max(close_price * 0.003, atr_value * 1.0)
    tp_dist = sl_dist * 2.5  # Тәуекелге қарағанда пайда 2.5 есе жоғары

    if side == "Buy":
        pos_idx = 1
        sl = close_price - sl_dist
        tp = close_price + tp_dist
    else:
        pos_idx = 2
        sl = close_price + sl_dist
        tp = close_price - tp_dist

    try:
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except Exception: pass

    try:
        res = session.place_order(
            category="linear", symbol=symbol, side=side, orderType="Market",
            qty=formatted_qty, takeProfit=format_value(tp, price_step),
            stopLoss=format_value(sl, price_step), positionIdx=pos_idx
        )
        if res.get('retCode') == 0:
            log(f"🚀 [{symbol}] БАСТАПҚЫ ОРДЕР АШЫЛДЫ! Бағыт: {side} | Баға: ${close_price} | SL: ${round(sl, 4)}")
            symbol_state[symbol].update({
                "step": 1, "active": True, "last_entry_price": close_price,
                "avg_price": close_price, "total_qty": float(formatted_qty),
                "entry_side": side, "stop_loss_price": sl
            })
            return True
    except Exception as e:
        log(f"Бастапқы ордер қатесі: {e}")
    return False

def add_pyramid_step(symbol, side, current_price):
    state = symbol_state[symbol]
    if state["step"] >= MAX_PYRAMID_STEPS:
        return

    last_price = state["last_entry_price"]
    
    # Тек баға біздің пайдамызға қарай жүрсе ғана жаңа қадам қосылады
    if side == "Buy" and (current_price - last_price) / last_price < ADD_STEP_PCT:
        return
    if side == "Sell" and (last_price - current_price) / last_price < ADD_STEP_PCT:
        return

    try:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        info = res['result']['list'][0]
        qty_step = float(info['lotSizeFilter']['qtyStep'])
        price_step = float(info['priceFilter']['tickSize'])
    except Exception: return

    margin = BASE_MARGIN * (1 + (state["step"] * 0.5)) # Маржаны сәл өсіру
    position_size_usdt = margin * LEVERAGE
    raw_qty = position_size_usdt / current_price
    formatted_qty = format_value(raw_qty, qty_step)

    prev_qty = state["total_qty"]
    prev_avg = state["avg_price"]
    curr_qty = float(formatted_qty)
    new_total_qty = prev_qty + curr_qty
    new_avg_price = ((prev_avg * prev_qty) + (current_price * curr_qty)) / new_total_qty

    # Стоп-лосс-ты ескі кірген бағаға жылжытамыз (Break-even — Тәуекелсіз зона)
    new_sl = prev_avg if side == "Buy" else prev_avg
    pos_idx = 1 if side == "Buy" else 2

    try:
        res = session.place_order(
            category="linear", symbol=symbol, side=side, orderType="Market",
            qty=formatted_qty, positionIdx=pos_idx
        )
        if res.get('retCode') == 0:
            session.trading_stop(
                category="linear", symbol=symbol,
                stopLoss=format_value(new_sl, price_step), positionIdx=pos_idx
            )
            log(f"🔥 [{symbol}] ПИРАМИДИНГ ҚАДАМЫ ҚОСЫЛДЫ! Қадам: {state['step'] + 1}/{MAX_PYRAMID_STEPS} | Жаңа SL: ${round(new_sl, 4)}")
            state.update({
                "step": state["step"] + 1, "last_entry_price": current_price,
                "avg_price": new_avg_price, "total_qty": new_total_qty,
                "stop_loss_price": new_sl
            })
    except Exception as e:
        log(f"Пирамидинг ордер қатесі: {e}")

def reset_symbol_state(symbol):
    symbol_state[symbol].update({
        "step": 0, "active": False, "last_entry_price": 0.0,
        "avg_price": 0.0, "total_qty": 0.0, "entry_side": None,
        "stop_loss_price": 0.0
    })

def main():
    log(f"🚀 АҚЫЛДЫ ПИРАМИДИНГ БОТЫ ІСКЕ ҚОСЫЛДЫ (SOL & ETH)")
    while True:
        try:
            all_active = get_active_positions()
            active_syms = [p['symbol'] for p in all_active]

            for symbol in SYMBOLS:
                pos = [p for p in all_active if p['symbol'] == symbol]
                
                # Егер позиция жабылған болса (TP немесе SL орындалды)
                if not pos and symbol_state[symbol]["active"]:
                    log(f"🏁 [{symbol}] Позиция жабылды! Жаңа циклге дайындық...")
                    reset_symbol_state(symbol)

                # Егер активті позиция жоқ болса — жаңа сигнал күтеміз
                if not pos:
                    if len(active_syms) >= MAX_OPEN_POSITIONS:
                        continue

                    signal, close_price, atr_val = check_entry_signal(symbol)
                    if signal != "WAIT":
                        if open_initial_position(symbol, signal, close_price, atr_val):
                            if symbol not in active_syms: active_syms.append(symbol)

                # Егер позиция бар болса — Пирамидинг қадамын тексереміз
                else:
                    curr_price = float(pos[0]['markPrice'])
                    side = symbol_state[symbol]["entry_side"]
                    if side:
                        add_pyramid_step(symbol, side, curr_price)

            time.sleep(5)
        except Exception as e:
            log(f"Цикл қатесі: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
