import os
import time
from datetime import datetime
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ==============================================================================
# CONFIG / PARAMETERS
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True  # Реал сауда үшін False орнатыңыз

SYMBOLS = ["SOLUSDT", "XRPUSDT", "1000PEPEUSDT", "NEARUSDT", "AVAXUSDT", "ETHUSDT", "DOGEUSDT", "SUIUSDT"]

LEVERAGE = 20                 # Плечо: 20x
TARGET_TOTAL_PROFIT = 100.0   # Жалпы мақсатты таза пайда: +100 USDT
MAX_STEP = 100                # Максималды саты ($100-ге дейін)

# ==============================================================================
# INITIALIZATION & GLOBALS
# ==============================================================================
session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
    demo=IS_DEMO
)

current_step = 1              # Бастапқы саты ($1)
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
# INDICATORS & SIGNALS
# ==============================================================================
def calc_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calc_rsi(series, length=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=length).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=length).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_klines(symbol, interval, limit=50):
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

def get_best_signal():
    for symbol in SYMBOLS:
        df = get_klines(symbol, "5", limit=50)
        if df is None or len(df) < 20: continue
        
        df['ema_fast'] = calc_ema(df['close'], 10)
        df['ema_slow'] = calc_ema(df['close'], 30)
        df['rsi'] = calc_rsi(df['close'], 14)
        
        last = df.iloc[-2]
        if last['ema_fast'] > last['ema_slow'] and last['rsi'] > 50:
            return symbol, "LONG"
        elif last['ema_fast'] < last['ema_slow'] and last['rsi'] < 50:
            return symbol, "SHORT"
            
    return None, "NO TRADE"

# ==============================================================================
# TRADE EXECUTION & MANAGEMENT
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
            return qty_step
    except: pass
    return None

def open_new_step_order():
    global current_step
    
    symbol, signal = get_best_signal()
    if not symbol or signal == "NO TRADE":
        return False

    usdt_amount = float(current_step)

    df = get_klines(symbol, "5", limit=5)
    if df is None: return False
    close_price = df['close'].iloc[-1]
    
    qty_step = get_symbol_info(symbol)
    if not qty_step: return False

    set_leverage(symbol)
    
    position_size_usdt = usdt_amount * LEVERAGE
    raw_qty = position_size_usdt / close_price
    formatted_qty = format_value(raw_qty, qty_step)

    if float(formatted_qty) <= 0: return False

    # Hedge Mode үшін positionIdx орнату: LONG = 1, SHORT = 2
    if signal == "LONG":
        order_side = "Buy"
        pos_idx = 1
    else:
        order_side = "Sell"
        pos_idx = 2

    res = session.place_order(
        category="linear",
        symbol=symbol,
        side=order_side,
        orderType="Market",
        qty=formatted_qty,
        positionIdx=pos_idx
    )
    
    if res['retCode'] == 0:
        log(f"🚀 [{current_step}-САТЫ ОРДЕР] {symbol} {signal} | Маржа: ${usdt_amount} USDT | Бағасы: {close_price}")
        return True
    else:
        log(f"Ордер ашу қатесі: {res['retMsg']} (Code: {res['retCode']})")
    return False

def manage_single_position():
    global current_step
    
    positions = get_active_positions()
    
    if len(positions) == 0:
        open_new_step_order()
        return

    pos = positions[0]
    symbol = pos['symbol']
    side = pos['side']
    qty = pos['size']
    pos_idx = int(pos.get('positionIdx', 0))
    unrealised_pnl = float(pos.get('unrealisedPnl', 0))

    take_profit_usdt = float(current_step) * 0.16
    stop_loss_usdt = float(current_step) * 0.06

    # 1. МИНУС БОЛСА -> Жауып, келесі сатыға өту ($1 -> $2 -> $3 ... $100)
    if unrealised_pnl <= -stop_loss_usdt:
        close_side = "Sell" if side == "Buy" else "Buy"
        session.place_order(
            category="linear",
            symbol=symbol,
            side=close_side,
            orderType="Market",
            qty=qty,
            reduceOnly=True,
            positionIdx=pos_idx
        )
        log(f"❌ [{current_step}-САТЫ МИНУС] {symbol} -${abs(unrealised_pnl):.2f} тіркелді (SL соғылды). Жабылды!")
        
        current_step += 1
        if current_step > MAX_STEP:
            log(f"⚠️ {MAX_STEP}-сатыға жетті. Қайтадан 1-сатыдан ($1) бастайды.")
            current_step = 1

    # 2. ПЛЮС БОЛСА -> Жауып, ҚАЙТАДАН 1-САТЫҒА ($1) ОРАЛУ
    elif unrealised_pnl >= take_profit_usdt:
        close_side = "Sell" if side == "Buy" else "Buy"
        session.place_order(
            category="linear",
            symbol=symbol,
            side=close_side,
            orderType="Market",
            qty=qty,
            reduceOnly=True,
            positionIdx=pos_idx
        )
        log(f"💰 [{current_step}-САТЫ ПАЙДА] {symbol} +${unrealised_pnl:.2f} пайдамен жабылды! Қайтадан 1-сатыға ($1) оралу.")
        
        current_step = 1

# ==============================================================================
# MAIN LOOP
# ==============================================================================
def main():
    global initial_balance
    log("🚀 Бот іске қосылды (20x Плечо | TP: 0.8% | SL: 0.3% | Hedge Mode / positionIdx орнатылды)")
    
    initial_balance = get_wallet_balance()
    log(f"💵 Бастапқы Баланс: {initial_balance:.2f} USDT | Мақсат: +{TARGET_TOTAL_PROFIT} USDT пайда табу")

    while True:
        try:
            current_balance = get_wallet_balance()
            total_profit = current_balance - initial_balance

            if total_profit >= TARGET_TOTAL_PROFIT:
                log(f"🎉 МАҚСАТ ОРЫНДАЛДЫ! Жалпы таза пайда: +{total_profit:.2f} USDT. Бот сауданы аяқтады.")
                break

            manage_single_position()
            time.sleep(5)

        except Exception as e:
            log(f"Қате: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
