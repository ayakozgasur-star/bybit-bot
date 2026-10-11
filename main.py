import os
import time
from datetime import datetime
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ==============================================================================
# ULTRA-FAST & SHARP STOCH-RSI MARTINGALE CONFIG
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True

SYMBOLS = ["SOLUSDT", "XRPUSDT", "1000PEPEUSDT", "NEARUSDT", "ETHUSDT"]

LEVERAGE = 15                  
BASE_MARGIN = 5.0              
MAX_STEPS = 10                 
MAX_OPEN_POSITIONS = 3         
STEP_DISTANCE_PCT = 0.003      # 0.3% адым арақашықтығы

session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET, demo=IS_DEMO)

symbol_state = {
    symbol: {
        "step": 0, "active": False, "last_price": 0.0,
        "avg_price": 0.0, "total_qty": 0.0, "entry_side": None
    } for symbol in SYMBOLS
}

def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [STOCH-BOT] {msg}", flush=True)

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

def check_stoch_signal(symbol):
    # 5 минуттық таймфрейм арқылы өте жиі әрі жылдам сигнал ұстау
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

    # Лонг сигналы: EMA 3 жоғарыға қиып өтті + StochRSI төменгі аймақтан (20-дан) жоғарыға бет алды
    if prev['ema3'] <= prev['ema8'] and curr['ema3'] > curr['ema8']:
        if curr['stoch_k'] < 80:  # Шамадан тыс жоғары болып кетпесе
            return "Buy", current_close, current_atr

    # Шорт сигналы: EMA 3 төменге қиып өтті + StochRSI жоғарғы аймақтан (80-нен) төменге бет алды
    if prev['ema3'] >= prev['ema8'] and curr['ema3'] < curr['ema8']:
        if curr['stoch_k'] > 20:  # Шамадан тыс төмен құлап кетпесе
            return "Sell", current_close, current_atr

    return "WAIT", current_close, current_atr

# ==============================================================================
# МАРЖА ЖӘНЕ ОРДЕР БАСҚАРУ
# ==============================================================================
def calculate_margin_for_step(step_index):
    margin = BASE_MARGIN
    for i in range(step_index):
        if i < 3: multiplier = 2.0
        elif i < 6: multiplier = 1.5
        else: multiplier = 1.2
        margin *= multiplier
    return round(margin, 2)

def get_active_positions(symbol=None):
    try:
        res = session.get_positions(category="linear", settleCoin="USDT")
        if res.get('retCode') == 0:
            positions = [p for p in res['result']['list'] if float(p['size']) > 0]
            if symbol: return [p for p in positions if p['symbol'] == symbol]
            return positions
    except Exception: pass
    return []

def open_martingale_step(symbol, side, step_index, close_price, atr_value):
    if step_index > 0 and symbol_state[symbol]["last_price"] > 0:
        last_price = symbol_state[symbol]["last_price"]
        if abs(close_price - last_price) / last_price < STEP_DISTANCE_PCT:
            return False

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

    new_avg_price = ((prev_avg * prev_qty) + (close_price * curr_qty)) / (prev_qty + curr_qty) if prev_qty > 0 else close_price

    tp_pct = 0.004  # +0.4% Take-Profit
    sl_pct = max(0.003, min((atr_value / close_price) * 1.2, 0.006)) # Динамикалық ATR Стоп-Лосс

    if side == "Buy":
        pos_idx = 1
        tp = new_avg_price * (1 + tp_pct)
        sl = close_price * (1 - sl_pct)
    else:
        pos_idx = 2
        tp = new_avg_price * (1 - tp_pct)
        sl = close_price * (1 + sl_pct)

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
            log(f"🔥 [{symbol}] STOCH-RSI ОРДЕР АШЫЛДЫ! Қадам: {step_index + 1}/{MAX_STEPS} | Бағыт: {side} | Маржа: ${margin}")
            symbol_state[symbol].update({
                "step": step_index, "active": True, "last_price": close_price,
                "avg_price": new_avg_price, "total_qty": symbol_state[symbol]["total_qty"] + curr_qty,
                "entry_side": side
            })
            return True
    except Exception as e:
        log(f"Ордер қатесі: {e}")
    return False

def check_recent_closed_pnl(symbol):
    try:
        res = session.get_closed_pnl(category="linear", symbol=symbol, limit=1)
        if res.get('retCode') == 0 and res['result']['list']:
            return float(res['result']['list'][0]['closedPnl'])
    except Exception: pass
    return None

def reset_symbol_state(symbol):
    symbol_state[symbol].update({
        "step": 0, "active": False, "last_price": 0.0, 
        "avg_price": 0.0, "total_qty": 0.0, "entry_side": None
    })

def main():
    log(f"🚀 STOCH-RSI PRO-БОТ ІСКЕ ҚОСЫЛДЫ (Жиі әрі дәл сауда)")
    while True:
        try:
            all_active = get_active_positions()
            active_syms = [p['symbol'] for p in all_active]

            for symbol in SYMBOLS:
                pos = [p for p in all_active if p['symbol'] == symbol]
                
                if not pos and symbol_state[symbol]["active"]:
                    last_pnl = check_recent_closed_pnl(symbol)
                    curr_step = symbol_state[symbol]["step"]
                    
                    if last_pnl is not None and last_pnl > 0:
                        log(f"✅ [{symbol}] ПЛЮСПЕН ЖАБЫЛДЫ! PnL: +${round(last_pnl, 2)}")
                        reset_symbol_state(symbol)
                    else:
                        next_step = curr_step + 1
                        if next_step < MAX_STEPS:
                            log(f"❌ [{symbol}] МИНУС. Келесі {next_step + 1}-адымға дайындық...")
                            symbol_state[symbol]["step"] = next_step
                            symbol_state[symbol]["active"] = False
                            symbol_state[symbol]["total_qty"] = 0.0
                            symbol_state[symbol]["avg_price"] = 0.0
                        else:
                            log(f"⚠️ [{symbol}] {MAX_STEPS} адым аяқталды. Цикл бастан басталады.")
                            reset_symbol_state(symbol)

                if not pos:
                    if symbol_state[symbol]["step"] == 0 and len(active_syms) >= MAX_OPEN_POSITIONS:
                        continue

                    signal, close_price, atr_val = check_stoch_signal(symbol)
                    if signal != "WAIT":
                        step_num = symbol_state[symbol]["step"]
                        if open_martingale_step(symbol, signal, step_num, close_price, atr_val):
                            if symbol not in active_syms: active_syms.append(symbol)

            time.sleep(5)
        except Exception as e:
            log(f"Цикл қатесі: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
