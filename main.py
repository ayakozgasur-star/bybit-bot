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
TARGET_TOTAL_PROFIT = 100.0   # Мақсатты таза пайда: +100 USDT
MAX_OPEN_POSITIONS = 5        # Бекітілетін монеталар саны (5 монета)

TP_PCT = 0.005  # +0.5% Take Profit
SL_PCT = 0.005  # -0.5% Stop Loss
PRICE_OFFSET_PCT = 0.0002  # 0.02% Offset (Maker Post-Only)

# ==============================================================================
# DYNAMIC MARTINGALE STEPS (1-ден 100-ге дейін)
# ==============================================================================
def generate_martingale_steps(max_steps=100):
    steps = []
    current = 1
    for i in range(1, max_steps + 1):
        steps.append(current)
        if i % 3 == 0:
            current += 4
        else:
            current += 3
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

symbol_steps = {}            # Әр монетаның сатысын жеке сақтау
selected_5_symbols = []      # Бекітілген 5 монета
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

def find_initial_5_symbols():
    chosen = []
    for symbol in SYMBOLS:
        if len(chosen) >= MAX_OPEN_POSITIONS:
            break
        df = get_klines(symbol, "5", limit=50)
        if df is None or len(df) < 20: continue
        
        df['ema_fast'] = calc_ema(df['close'], 10)
        df['ema_slow'] = calc_ema(df['close'], 30)
        df['rsi'] = calc_rsi(df['close'], 14)
        
        last = df.iloc[-2]
        if (last['ema_fast'] > last['ema_slow'] and last['rsi'] > 50) or \
           (last['ema_fast'] < last['ema_slow'] and last['rsi'] < 50):
            chosen.append(symbol)
            
    return chosen

def get_symbol_signal(symbol):
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
# TRADE EXECUTION & STEP MANAGEMENT
# ==============================================================================
def check_last_closed_pnl(symbol):
    """Соңғы жабылған ордердің P&L нәтижесін тексеру"""
    try:
        res = session.get_closed_pnl(category="linear", symbol=symbol, limit=1)
        if res['retCode'] == 0 and len(res['result']['list']) > 0:
            last_pnl = float(res['result']['list'][0]['closedPnl'])
            return last_pnl
    except Exception as e:
        log(f"P&L тексеру қатесі ({symbol}): {e}")
    return 0.0

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
    global symbol_steps
    
    # Соңғы ордерді тексеріп, сатыны реттеу
    last_pnl = check_last_closed_pnl(symbol)
    if last_pnl > 0:
        symbol_steps[symbol] = 0 # Пайда соғылса -> 1-сатыға оралу ($1)
        log(f"✅ [{symbol}] Алдыңғы ордер ПЛЮСПЕН жабылды (+{last_pnl:.2f} USDT). Саты 1-ге түсірілді.")
    elif last_pnl < 0:
        symbol_steps[symbol] = symbol_steps.get(symbol, 0) + 1 # Шығын болса -> Саты +1
        log(f"🔻 [{symbol}] Алдыңғы ордер МИНУСПЕН жабылды ({last_pnl:.2f} USDT). Келесі саты: #{symbol_steps[symbol] + 1}")

    step_idx = symbol_steps.get(symbol, 0)
    usdt_margin = MARTINGALE_STEPS[step_idx]
    signal = get_symbol_signal(symbol)

    df = get_klines(symbol, "5", limit=5)
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
        log(f"🎯 [{symbol}] MAKER ОРДЕР АШЫЛДЫ | Саты #{step_idx + 1} | Маржа: ${usdt_margin} USDT | Бағасы: {formatted_limit}")
        return True
    return False

# ==============================================================================
# MAIN LOOP
# ==============================================================================
def main():
    global initial_balance, symbol_steps, selected_5_symbols
    log(f"🚀 Бот іске қосылды (100 сатылы Мартингейл | 5 Монета | Maker Post-Only | Мақсат: +{TARGET_TOTAL_PROFIT} USDT)")
    
    initial_balance = get_wallet_balance()
    log(f"💵 Бастапқы Баланс: {initial_balance:.2f} USDT")

    while True:
        try:
            current_balance = get_wallet_balance()
            total_profit = current_balance - initial_balance

            if total_profit >= TARGET_TOTAL_PROFIT:
                log(f"🎉 МАҚСАТ ОРЫНДАЛДЫ! +100 USDT таза пайда жиналды. Сауда тоқтатылды.")
                break

            if len(selected_5_symbols) < MAX_OPEN_POSITIONS:
                selected_5_symbols = find_initial_5_symbols()
                if len(selected_5_symbols) == MAX_OPEN_POSITIONS:
                    for s in selected_5_symbols:
                        symbol_steps[s] = 0
                    log(f"📌 БЕКІТІЛГЕН 5 МОНЕТА: {selected_5_symbols}.")
                else:
                    time.sleep(5)
                    continue

            positions = get_active_positions()
            active_symbols = [p['symbol'] for p in positions]

            for symbol in selected_5_symbols:
                if symbol not in active_symbols:
                    open_order_for_symbol(symbol)

            time.sleep(3)

        except Exception as e:
            log(f"Қате: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
