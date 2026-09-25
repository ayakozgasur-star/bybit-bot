import os
import time
from datetime import datetime
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ==============================================================================
# CONFIG / PARAMETERS (ЖАҢАРТЫЛҒАН ИНДИКАТОРЛАР ЖӘНЕ ТЕЗ СИГНАЛДАР)
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True  # Реал сауда үшін False орнатыңыз

SYMBOLS = [
    "SOLUSDT", "XRPUSDT", "1000PEPEUSDT", "NEARUSDT", "AVAXUSDT", 
    "ETHUSDT", "DOGEUSDT", "SUIUSDT", "BTCUSDT", "ADAUSDT", 
    "BNBUSDT", "LTCUSDT", "LINKUSDT", "DOTUSDT", "APTUSDT"
]

LEVERAGE = 20                 # Плечо: 20x
TARGET_TOTAL_PROFIT = 100.0   # Мақсатты таза пайда: +100 USDT
MAX_OPEN_POSITIONS = 3        # БЕКІТІЛЕТІН МОНЕТАЛАР САНЫ (3 МОНЕТА)

BASE_MARGIN_USDT = 2.0        # БАСТАПҚЫ НОРМАЛЫ МАРЖА ($2)
MIN_TARGET_PROFIT = 0.50      # Минусты жапқанда үстіне түсуі тиіс ТАЗА ПАЙДА ($0.50)

TP_PCT = 0.005  # +0.5% TP (Тейк-профит)
SL_PCT = 0.003  # -0.3% SL (Стоп-лосс)
PRICE_OFFSET_PCT = 0.0002  # 0.02% Offset (Post-Only Maker үшін)

# ==============================================================================
# INITIALIZATION & GLOBALS
# ==============================================================================
session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
    demo=IS_DEMO
)

accumulated_losses = {}       # Әр монетаның жиналған минустары
last_checked_pnl_time = {}   # Бір ордерді екі рет есептемеу үшін
selected_3_symbols = []      # Бекітілген 3 монета
initial_balance = 0.0

def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [INFO] {msg}", flush=True)

def get_wallet_balance():
    try:
        res = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        if res['retCode'] == 0:
            coin_info = res['result']['list'][0]['coin'][0]
            return float(coin_info['walletBalance'])
    except Exception as e:
        log(f"Баланс алу қатесі: {e}")
    return 0.0

# ==============================================================================
# ADVANCED INDICATORS & SIGNALS (1-MIN TIMEFRAME)
# ==============================================================================
def calc_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calc_rsi(series, length=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=length).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=length).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_atr(df, length=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(length).mean()

def get_klines(symbol, interval="1", limit=50): # Тез анализ үшін 1-минуттық шамдар
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

def find_initial_3_symbols():
    chosen = []
    for symbol in SYMBOLS:
        if len(chosen) >= MAX_OPEN_POSITIONS:
            break
        df = get_klines(symbol, "1", limit=50)
        if df is None or len(df) < 30: continue
        
        df['ema_fast'] = calc_ema(df['close'], 9)
        df['ema_slow'] = calc_ema(df['close'], 21)
        df['rsi'] = calc_rsi(df['close'], 14)
        df['atr'] = calc_atr(df, 14)
        df['vol_ma'] = df['volume'].rolling(20).mean()

        last = df.iloc[-2]
        # Бесенді әрі волатильді монеталарды сұрыптау
        if last['atr'] > 0 and last['volume'] > last['vol_ma'] * 0.8:
            if (last['ema_fast'] > last['ema_slow'] and last['rsi'] > 50) or \
               (last['ema_fast'] < last['ema_slow'] and last['rsi'] < 50):
                chosen.append(symbol)
            
    return chosen

def get_symbol_signal(symbol):
    """Сапалы әрі жылдам сигнал (EMA + RSI + ATR + Volume)"""
    df = get_klines(symbol, "1", limit=50)
    if df is None or len(df) < 30: return "LONG"
    
    df['ema_fast'] = calc_ema(df['close'], 9)
    df['ema_slow'] = calc_ema(df['close'], 21)
    df['rsi'] = calc_rsi(df['close'], 14)
    df['vol_ma'] = df['volume'].rolling(20).mean()
    
    last = df.iloc[-2]
    
    # Көлем және тренд сүзгісі
    has_volume = last['volume'] >= last['vol_ma'] * 0.7

    if last['ema_fast'] < last['ema_slow'] and last['rsi'] < 48 and has_volume:
        return "SHORT"
    elif last['ema_fast'] > last['ema_slow'] and last['rsi'] > 52 and has_volume:
        return "LONG"
        
    # Егер әлсіз бейтарап аймақ болса, орташа бағытқа сүйенеді
    return "LONG" if last['rsi'] >= 50 else "SHORT"

# ==============================================================================
# LOSS RECOVERY CALCULATION
# ==============================================================================
def update_loss_tracker(symbol):
    global accumulated_losses, last_checked_pnl_time
    
    time.sleep(1.5)
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
                    log(f"✅ [{symbol}] ТЕЙК-ПРОФИТ (+{last_pnl:.2f} USDT)! БАРЛЫҚ МИНУС ЖАБЫЛДЫ!")
                else:
                    accumulated_losses[symbol] = new_loss
                    log(f"✅ [{symbol}] ТЕЙК-ПРОФИТ (+{last_pnl:.2f} USDT)! Қалған минус: -{new_loss:.2f} USDT.")
            else:
                accumulated_losses[symbol] = curr_loss + abs(last_pnl)
                log(f"🔻 [{symbol}] СТОП-ЛОСС ({last_pnl:.2f} USDT). Жиналған минус: -{accumulated_losses[symbol]:.2f} USDT.")

    except Exception as e:
        log(f"P&L тексеру қатесі ({symbol}): {e}")

# ==============================================================================
# TRADE EXECUTION (SMART MARGIN / MAKER POST-ONLY)
# ==============================================================================
def get_active_positions():
    try:
        res = session.get_positions(category="linear", settleCoin="USDT")
        if res['retCode'] == 0:
            return [p for p in res['result']['list'] if float(p['size']) > 0]
    except Exception as e:
        log(f"Позицияларды алу қатесі: {e}")
    return []

def set_leverage(symbol):
    try:
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except:
        pass

def format_value(value, step):
    if step is None or step == 0: return str(value)
    precision = 0
    step_str = f"{step:.8f}".rstrip('0')
    if '.' in step_str: precision = len(step_str.split('.')[1])
    return f"{round(value, precision):.{precision}f}"

def get_symbol_info(symbol):
    try:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        if res['retCode'] == 0:
            info = res['result']['list'][0]
            qty_step = float(info['lotSizeFilter']['qtyStep'])
            price_step = float(info['priceFilter']['tickSize'])
            return qty_step, price_step
    except: pass
    return None, None

def open_order_for_symbol(symbol):
    global accumulated_losses
    
    update_loss_tracker(symbol)
    current_loss = accumulated_losses.get(symbol, 0.0)

    if current_loss > 0:
        needed_margin = (current_loss + MIN_TARGET_PROFIT) / (LEVERAGE * TP_PCT)
        usdt_margin = round(max(needed_margin, BASE_MARGIN_USDT), 2)
        log(f"🔄 [{symbol}] ТЕЗ ӨСІМ МАРЖАСЫ: ${usdt_margin} USDT (Минусты: -${current_loss:.2f} жабады)")
    else:
        usdt_margin = BASE_MARGIN_USDT

    signal = get_symbol_signal(symbol)

    df = get_klines(symbol, "1", limit=5)
    if df is None: return False
    close_price = df['close'].iloc[-1]
    
    qty_step, price_step = get_symbol_info(symbol)
    if not qty_step or not price_step: return False

    set_leverage(symbol)
    
    position_size_usdt = usdt_margin * LEVERAGE
    raw_qty = position_size_usdt / close_price
    formatted_qty = format_value(raw_qty, qty_step)

    if float(formatted_qty) <= 0: return False

    if signal == "LONG":
        order_side = "Buy"
        pos_idx = 1
        limit_price = close_price * (1 - PRICE_OFFSET_PCT)
        tp_price = close_price * (1 + TP_PCT)
        sl_price = close_price * (1 - SL_PCT)
    else:
        order_side = "Sell"
        pos_idx = 2
        limit_price = close_price * (1 + PRICE_OFFSET_PCT)
        tp_price = close_price * (1 - TP_PCT)
        sl_price = close_price * (1 + SL_PCT)

    formatted_limit = format_value(limit_price, price_step)
    formatted_tp = format_value(tp_price, price_step)
    formatted_sl = format_value(sl_price, price_step)

    res = session.place_order(
        category="linear",
        symbol=symbol,
        side=order_side,
        orderType="Limit",
        price=formatted_limit,
        qty=formatted_qty,
        takeProfit=formatted_tp,
        stopLoss=formatted_sl,
        positionIdx=pos_idx,
        orderFilter="Order",
        execInst="PostOnly"
    )
    
    if res['retCode'] == 0:
        log(f"🎯 [{symbol}] {signal} ОРДЕР АШЫЛДЫ | Маржа: ${usdt_margin} USDT | Бағасы: {formatted_limit}")
        return True
    return False

# ==============================================================================
# MAIN LOOP
# ==============================================================================
def main():
    global initial_balance, selected_3_symbols
    log(f"🚀 Бот іске қосылды (Көөп индикаторлы Тез Сигнал + Шығынды Жабу Жүйесі)")
    
    initial_balance = get_wallet_balance()
    log(f"💵 Бастапқы Баланс: {initial_balance:.2f} USDT")

    while True:
        try:
            current_balance = get_wallet_balance()
            total_profit = current_balance - initial_balance

            if total_profit >= TARGET_TOTAL_PROFIT:
                log(f"🎉 МАҚСАТ ОРЫНДАЛДЫ! +100 USDT таза пайда жиналды. Сауда тоқтатылды.")
                break

            if len(selected_3_symbols) < MAX_OPEN_POSITIONS:
                selected_3_symbols = find_initial_3_symbols()
                if len(selected_3_symbols) == MAX_OPEN_POSITIONS:
                    for s in selected_3_symbols:
                        accumulated_losses[s] = 0.0
                    log(f"📌 НЕГІЗГІ 3 МОНЕТА БЕКІТІЛДІ: {selected_3_symbols}.")
                else:
                    time.sleep(2)
                    continue

            positions = get_active_positions()
            active_symbols = [p['symbol'] for p in positions]

            for symbol in selected_3_symbols:
                if symbol not in active_symbols:
                    open_order_for_symbol(symbol)

            time.sleep(1.5)  # Тез әрекет ету үшін 1.5 секундтық тексеру

        except Exception as e:
            log(f"Қате: {e}")
            time.sleep(2)

if __name__ == "__main__":
    main()
