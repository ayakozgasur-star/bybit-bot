import os
import time
from datetime import datetime
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ==============================================================================
# ULTIMATE SCALP BOT (ТОЛЫҚ ҚАТЕСІЗ ЖӘНЕ БАРЛЫҚ ФИЛЬТРЛЕРМЕН ЖАБДЫҚТАЛҒАН НҰСҚА)
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True

SYMBOLS = ["SOLUSDT", "ETHUSDT"]

LEVERAGE = 15                  
MAX_RISK_USDT = 1.0            # Әр сделкадағы ең көп жоғалтуға болатын тәуекел сомасы ($1)
MAX_PYRAMID_STEPS = 3          
MAX_OPEN_POSITIONS = 2         
ADD_STEP_PCT = 0.0035          

BREAK_EVEN_TRIGGER_PCT = 0.004 
TRAILING_OFFSET_PCT = 0.003    

session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET, demo=IS_DEMO)

symbol_state = {
    symbol: {
        "step": 0, "active": False, "last_entry_price": 0.0,
        "avg_price": 0.0, "total_qty": 0.0, "entry_side": None,
        "stop_loss_price": 0.0, "break_even_activated": False
    } for symbol in SYMBOLS
}

def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [ULTIMATE-BOT] {msg}", flush=True)

def format_value(value, step):
    if step is None or step == 0: return str(value)
    step_str = f"{step:.8f}".rstrip('0')
    precision = len(step_str.split('.')[1]) if '.' in step_str else 0
    return f"{round(value, precision):.{precision}f}"

def get_klines(symbol, interval, limit=220):
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
# ИНДИКАТОРЛАР (ТҮЗЕЛГЕН ATR ЖӘНЕ ЖАБЫЛҒАН ШАМДАР)
# ==============================================================================
def calc_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calc_bollinger_bands(series, period=20, deviation=2):
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = middle + (std * deviation)
    lower = middle - (std * deviation)
    return upper, middle, lower

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
    df = get_klines(symbol, interval="5", limit=220)
    if df is None or len(df) < 200: return "WAIT", 0.0, 0.0, 0.0

    df['ema200'] = calc_ema(df['close'], 200)
    df['ema3'] = calc_ema(df['close'], 3)
    df['ema8'] = calc_ema(df['close'], 8)
    df['bb_upper'], df['bb_mid'], df['bb_lower'] = calc_bollinger_bands(df['close'], period=20, deviation=2)
    df['stoch_k'], df['stoch_d'] = calc_stoch_rsi(df['close'])
    df['atr'] = calc_atr(df)
    
    # Тек жабылған шамдарды аламыз (-2 және -3)
    curr = df.iloc[-2]  
    prev = df.iloc[-3]  
    current_close = df.iloc[-1]['close']

    current_atr = curr['atr'] if not np.isnan(curr['atr']) else current_close * 0.003

    body_size = abs(curr['close'] - curr['open'])
    upper_wick = curr['high'] - max(curr['close'], curr['open'])
    lower_wick = min(curr['close'], curr['open']) - curr['low']
    total_range = curr['high'] - curr['low']
    
    is_bullish_pin = (lower_wick > body_size * 2) and (lower_wick > total_range * 0.5)
    is_bearish_pin = (upper_wick > body_size * 2) and (upper_wick > total_range * 0.5)

    uptrend = curr['close'] > curr['ema200']
    downtrend = curr['close'] < curr['ema200']

    long_signal = (
        uptrend and
        prev['ema3'] <= prev['ema8'] and curr['ema3'] > curr['ema8'] and
        curr['stoch_k'] < 80 and
        (curr['close'] <= curr['bb_lower'] * 1.01 or is_bullish_pin or curr['close'] > curr['bb_mid'])
    )

    short_signal = (
        downtrend and
        prev['ema3'] >= prev['ema8'] and curr['ema3'] < curr['ema8'] and
        curr['stoch_k'] > 20 and
        (curr['close'] >= curr['bb_upper'] * 0.99 or is_bearish_pin or curr['close'] < curr['bb_mid'])
    )

    if long_signal:
        return "Buy", current_close, current_atr, curr['ema200']
    elif short_signal:
        return "Sell", current_close, current_atr, curr['ema200']

    return "WAIT", current_close, current_atr, curr['ema200']

# ==============================================================================
# ОРДЕР БАСҚАРУ ЖӘНЕ STATE RECOVERY
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

def recover_states_from_exchange():
    try:
        active_positions = get_active_positions()
        for pos in active_positions:
            sym = pos['symbol']
            if sym in symbol_state:
                side = "Buy" if pos['side'] == "Buy" else "Sell"
                avg_price = float(pos['avgPrice'])
                size = float(pos['size'])
                sl = float(pos['stopLoss']) if pos.get('stopLoss') else 0.0
                
                symbol_state[sym].update({
                    "step": 1,
                    "active": True,
                    "last_entry_price": avg_price,
                    "avg_price": avg_price,
                    "total_qty": size,
                    "entry_side": side,
                    "stop_loss_price": sl,
                    "break_even_activated": False
                })
                log(f"🔄 ҚАЛПЫНА КЕЛТІРДІ [{sym}]: {side} | Бағасы: ${avg_price}")
    except Exception as e:
        log(f"Состояние қалпына келтіру қатесі: {e}")

def open_initial_position(symbol, side, close_price, atr_value):
    try:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        info = res['result']['list'][0]
        qty_step = float(info['lotSizeFilter']['qtyStep'])
        price_step = float(info['priceFilter']['tickSize'])
    except Exception: return False

    sl_dist = max(close_price * 0.003, atr_value * 1.0)
    sl_pct = sl_dist / close_price
    
    raw_qty = MAX_RISK_USDT / (close_price * sl_pct)
    formatted_qty = format_value(raw_qty, qty_step)
    tp_dist = sl_dist * 2.5

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
            log(f"🚀 [{symbol}] ОРДЕР АШЫЛДЫ ({side}) | Көл.: {formatted_qty} | SL: ${round(sl, 4)}")
            symbol_state[symbol].update({
                "step": 1, "active": True, "last_entry_price": close_price,
                "avg_price": close_price, "total_qty": float(formatted_qty),
                "entry_side": side, "stop_loss_price": sl,
                "break_even_activated": False
            })
            return True
    except Exception as e:
        log(f"Бастапқы ордер қатесі: {e}")
    return False

def manage_trailing_and_pyramid(symbol, pos):
    state = symbol_state[symbol]
    side = state["entry_side"]
    avg_price = state["avg_price"]
    current_price = float(pos['markPrice'])
    pos_idx = 1 if side == "Buy" else 2

    try:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        info = res['result']['list'][0]
        price_step = float(info['priceFilter']['tickSize'])
        qty_step = float(info['lotSizeFilter']['qtyStep'])
    except Exception: return

    df = get_klines(symbol, interval="5", limit=30)
    atr_val = calc_atr(df).iloc[-1] if df is not None and not df.empty else current_price * 0.003

    if side == "Buy":
        profit_pct = (current_price - avg_price) / avg_price
        if profit_pct >= BREAK_EVEN_TRIGGER_PCT and not state["break_even_activated"]:
            new_sl = avg_price + (avg_price * 0.0005)
            session.trading_stop(category="linear", symbol=symbol, stopLoss=format_value(new_sl, price_step), positionIdx=pos_idx)
            state["break_even_activated"] = True
            state["stop_loss_price"] = new_sl
            log(f"🛡️ [{symbol}] BREAK-EVEN ҚОСЫЛДЫ (BUY)")
        elif state["break_even_activated"]:
            potential_sl = current_price - (current_price * TRAILING_OFFSET_PCT)
            if potential_sl > state["stop_loss_price"]:
                session.trading_stop(category="linear", symbol=symbol, stopLoss=format_value(potential_sl, price_step), positionIdx=pos_idx)
                state["stop_loss_price"] = potential_sl
                log(f"📈 [{symbol}] TRAILING STOP ЖАҢАРТЫЛДЫ: ${round(potential_sl, 4)}")

    elif side == "Sell":
        profit_pct = (avg_price - current_price) / avg_price
        if profit_pct >= BREAK_EVEN_TRIGGER_PCT and not state["break_even_activated"]:
            new_sl = avg_price - (avg_price * 0.0005)
            session.trading_stop(category="linear", symbol=symbol, stopLoss=format_value(new_sl, price_step), positionIdx=pos_idx)
            state["break_even_activated"] = True
            state["stop_loss_price"] = new_sl
            log(f"🛡️ [{symbol}] BREAK-EVEN ҚОСЫЛДЫ (SELL)")
        elif state["break_even_activated"]:
            potential_sl = current_price + (current_price * TRAILING_OFFSET_PCT)
            if potential_sl < state["stop_loss_price"]:
                session.trading_stop(category="linear", symbol=symbol, stopLoss=format_value(potential_sl, price_step), positionIdx=pos_idx)
                state["stop_loss_price"] = potential_sl
                log(f"📉 [{symbol}] TRAILING STOP ЖАҢАРТЫЛДЫ: ${round(potential_sl, 4)}")

    if state["step"] < MAX_PYRAMID_STEPS:
        last_price = state["last_entry_price"]
        if side == "Buy" and (current_price - last_price) / last_price >= ADD_STEP_PCT:
            execute_pyramid_step(symbol, side, current_price, atr_val, price_step, qty_step)
        elif side == "Sell" and (last_price - current_price) / last_price >= ADD_STEP_PCT:
            execute_pyramid_step(symbol, side, current_price, atr_val, price_step, qty_step)

def execute_pyramid_step(symbol, side, current_price, atr_val, price_step, qty_step):
    state = symbol_state[symbol]
    
    sl_dist = max(current_price * 0.003, atr_val * 1.0)
    sl_pct = sl_dist / current_price
    raw_qty = (MAX_RISK_USDT * 0.5) / (current_price * sl_pct)
    formatted_qty = format_value(raw_qty, qty_step)

    prev_qty = state["total_qty"]
    prev_avg = state["avg_price"]
    curr_qty = float(formatted_qty)
    new_total_qty = prev_qty + curr_qty
    new_avg_price = ((prev_avg * prev_qty) + (current_price * curr_qty)) / new_total_qty
    pos_idx = 1 if side == "Buy" else 2

    try:
        res = session.place_order(
            category="linear", symbol=symbol, side=side, orderType="Market",
            qty=formatted_qty, positionIdx=pos_idx
        )
        if res.get('retCode') == 0:
            new_sl_dist = max(new_avg_price * 0.003, atr_val * 1.0)
            if side == "Buy":
                new_sl = new_avg_price - new_sl_dist
            else:
                new_sl = new_avg_price + new_sl_dist

            session.trading_stop(
                category="linear",
                symbol=symbol,
                stopLoss=format_value(new_sl, price_step),
                positionIdx=pos_idx
            )

            log(f"🔥 [{symbol}] ПИРАМИДИНГ ҚОСЫЛДЫ! Қадам: {state['step'] + 1}/{MAX_PYRAMID_STEPS} | Жаңа SL: ${round(new_sl, 4)}")
            
            state.update({
                "step": state["step"] + 1,
                "last_entry_price": current_price,
                "avg_price": new_avg_price,
                "total_qty": new_total_qty,
                "stop_loss_price": new_sl
            })
    except Exception as e:
        log(f"Пирамидинг қатесі: {e}")

def reset_symbol_state(symbol):
    symbol_state[symbol].update({
        "step": 0, "active": False, "last_entry_price": 0.0,
        "avg_price": 0.0, "total_qty": 0.0, "entry_side": None,
        "stop_loss_price": 0.0, "break_even_activated": False
    })

def main():
    log(f"🚀 ULTIMATE SCALP BOT (ТОЛЫҚ ДАЙЫН НҰСҚА) ІСКЕ ҚОСЫЛДЫ")
    recover_states_from_exchange()

    while True:
        try:
            all_active = get_active_positions()
            active_syms = [p['symbol'] for p in all_active]

            for symbol in SYMBOLS:
                pos_list = [p for p in all_active if p['symbol'] == symbol]
                
                if not pos_list and symbol_state[symbol]["active"]:
                    log(f"🏁 [{symbol}] Позиция жабылды! Жаңа цикл...")
                    reset_symbol_state(symbol)

                if not pos_list:
                    if len(active_syms) >= MAX_OPEN_POSITIONS:
                        continue

                    signal, close_price, atr_val, _ = check_entry_signal(symbol)
                    if signal != "WAIT":
                        if open_initial_position(symbol, signal, close_price, atr_val):
                            if symbol not in active_syms: active_syms.append(symbol)
                else:
                    manage_trailing_and_pyramid(symbol, pos_list[0])

            time.sleep(5)
        except Exception as e:
            log(f"Цикл қатесі: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
