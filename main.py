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

SYMBOLS = [
    "SOLUSDT", "XRPUSDT", "1000PEPEUSDT", "NEARUSDT", "AVAXUSDT", 
    "ETHUSDT", "DOGEUSDT", "SUIUSDT", "BTCUSDT", "ADAUSDT", 
    "BNBUSDT", "LTCUSDT", "LINKUSDT", "DOTUSDT", "APTUSDT"
]

LEVERAGE = 20                 # Плечо: 20x
TARGET_TOTAL_PROFIT = 100.0   # Жалпы мақсатты таза пайда: +100 USDT

# TP / SL Проценттері (Баға қозғалысы бойынша)
TP_PCT = 0.005  # +0.5% (20x плечомен маржаға +10% пайда)
SL_PCT = 0.003  # -0.3% (20x плечомен маржаға -6% шығын)

# ==============================================================================
# DYNAMIC MARTINGALE STEPS (1-ден 100-ге дейін)
# ==============================================================================
def generate_martingale_steps(max_steps=100):
    steps = []
    current = 1
    for i in range(1, max_steps + 1):
        steps.append(current)
        if i % 3 == 0:
            current += 4  # Әр 3-ші сатыдан кейін +4
        else:
            current += 3  # Әдеттегі өсім +3
    return steps

MARTINGALE_STEPS = generate_martingale_steps(100)

# ==============================================================================
# INITIALIZATION & GLOBALS
# ==============================================================================
session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
    demo=IS_DEMO
)

current_step_idx = 0          # Саты индексі ($1-ден басталады)
initial_balance = 0.0
selected_symbol = None        # Бекітілген жалғыз монета

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

def find_first_target_symbol():
    """Ең алғашқы кіретін негізгі монетаны табу"""
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

def get_symbol_signal(symbol):
    """Тек бекітілген монета бойынша бағытты анықтау"""
    df = get_klines(symbol, "5", limit=50)
    if df is None or len(df) < 20: return "LONG"
    
    df['ema_fast'] = calc_ema(df['close'], 10)
    df['ema_slow'] = calc_ema(df['close'], 30)
    df['rsi'] = calc_rsi(df['close'], 14)
    
    last = df.iloc[-2]
    if last['ema_fast'] < last['ema_slow'] and last['rsi'] < 50:
        return "SHORT"
    return "LONG"

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
            price_step = float(info['priceFilter']['tickSize'])
            return qty_step, price_step
    except: pass
    return None, None

def open_order_for_selected_symbol():
    global current_step_idx, selected_symbol
    
    if not selected_symbol:
        return False

    usdt_margin = MARTINGALE_STEPS[current_step_idx]
    signal = get_symbol_signal(selected_symbol)

    df = get_klines(selected_symbol, "5", limit=5)
    if df is None: return False
    close_price = df['close'].iloc[-1]
    
    qty_step, price_step = get_symbol_info(selected_symbol)
    if not qty_step or not price_step: return False

    set_leverage(selected_symbol)
    
    position_size_usdt = usdt_margin * LEVERAGE
    raw_qty = position_size_usdt / close_price
    formatted_qty = format_value(raw_qty, qty_step)

    if float(formatted_qty) <= 0: return False

    if signal == "LONG":
        order_side = "Buy"
        pos_idx = 1
        limit_price = close_price
        tp_price = close_price * (1 + TP_PCT)
        sl_price = close_price * (1 - SL_PCT)
    else:
        order_side = "Sell"
        pos_idx = 2
        limit_price = close_price
        tp_price = close_price * (1 - TP_PCT)
        sl_price = close_price * (1 + SL_PCT)

    formatted_limit = format_value(limit_price, price_step)
    formatted_tp = format_value(tp_price, price_step)
    formatted_sl = format_value(sl_price, price_step)

    res = session.place_order(
        category="linear",
        symbol=selected_symbol,
        side=order_side,
        orderType="Limit",
        price=formatted_limit,
        qty=formatted_qty,
        takeProfit=formatted_tp,
        stopLoss=formatted_sl,
        positionIdx=pos_idx
    )
    
    if res['retCode'] == 0:
        log(f"🎯 [{selected_symbol}] ОРДЕР АШЫЛДЫ | Саты #{current_step_idx + 1} | Маржа: ${usdt_margin} USDT | Бағасы: {formatted_limit}")
        return True
    else:
        log(f"Ордер ашу қатесі ({selected_symbol}): {res['retMsg']}")
    return False

# ==============================================================================
# MAIN LOOP
# ==============================================================================
def main():
    global initial_balance, current_step_idx, selected_symbol
    log(f"🚀 Бот іске қосылды (Бекітілген жалғыз монета режимі | Мақсат: +{TARGET_TOTAL_PROFIT} USDT)")
    
    initial_balance = get_wallet_balance()
    last_balance = initial_balance
    log(f"💵 Бастапқы Баланс: {initial_balance:.2f} USDT")

    while True:
        try:
            current_balance = get_wallet_balance()
            total_profit = current_balance - initial_balance

            if total_profit >= TARGET_TOTAL_PROFIT:
                log(f"🎉 МАҚСАТ ОРЫНДАЛДЫ! +100 USDT таза пайда жиналды. Сауда тоқтатылды.")
                break

            # 1. Егер монета таңдалмаған болса, ең алғашқы монетаны бекітіп аламыз
            if selected_symbol is None:
                symbol, signal = find_first_target_symbol()
                if symbol and signal != "NO TRADE":
                    selected_symbol = symbol
                    log(f"📌 НЕГІЗГІ МОНЕТА ТАҢДАЛДЫ: [{selected_symbol}]. Барлық Мартингейл +100 USDT-ге дейін тек осы монетада жүреді!")
                else:
                    log("🔍 Сигнал ізделуде...")
                    time.sleep(5)
                    continue

            # 2. Ашық позицияны тексеру
            positions = get_active_positions()
            has_active_pos = any(p['symbol'] == selected_symbol for p in positions)

            # 3. Баланс өзгерісін бақылау (TP немесе SL соғылғанын білу)
            balance_change = current_balance - last_balance

            if balance_change > 0.05:
                log(f"✅ [{selected_symbol}] ТЕЙК-ПРОФИТ СОҒЫЛДЫ (+{balance_change:.2f} USDT)! Кайтадан 1-сатыға ($1) түсеміз.")
                current_step_idx = 0
                last_balance = current_balance

            elif balance_change < -0.05:
                log(f"❌ [{selected_symbol}] СТОП-ЛОСС СОҒЫЛДЫ ({balance_change:.2f} USDT). Келесі сатыға өтеміз.")
                current_step_idx = min(current_step_idx + 1, len(MARTINGALE_STEPS) - 1)
                log(f"➡️ Жаңа саты: #{current_step_idx + 1} (Маржа: ${MARTINGALE_STEPS[current_step_idx]} USDT)")
                last_balance = current_balance

            # 4. Егер ашық позиция болмаса, жаңа сатыдағы ордерді ашамыз
            if not has_active_pos:
                open_order_for_selected_symbol()

            time.sleep(3)

        except Exception as e:
            log(f"Қате: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
