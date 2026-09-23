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
MAX_OPEN_POSITIONS = 3        # БІР УАҚЫТТА АШЫЛАТЫН МАКСИМАЛДЫ ПОЗИЦИЯ САНЫ

# TP / SL Проценттері (Баға қозғалысы бойынша)
TP_PCT = 0.005  # +0.5% (20x плечомен маржаға +10% пайда)
SL_PCT = 0.003  # -0.3% (20x плечомен маржаға -6% шығын)

# ==============================================================================
# DYNAMIC MARTINGALE STEPS (1-ден 100-ге дейін)
# ==============================================================================
def generate_martingale_steps(max_steps=100):
    """
    1-ордер: $1, 2-ордер: $4, 3-ордер: $7
    4-ордер: $11, 5-ордер: $14, 6-ордер: $17
    7-ордер: $21, 8-ордер: $24, 9-ордер: $27 ...
    """
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

current_step_idx = 0          # Саты индексі (0-ден басталады = $1)
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

def get_best_signal(active_symbols):
    for symbol in SYMBOLS:
        if symbol in active_symbols:
            continue
            
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
            price_step = float(info['priceFilter']['tickSize'])
            return qty_step, price_step
    except: pass
    return None, None

def open_new_step_order(active_symbols):
    global current_step_idx
    
    symbol, signal = get_best_signal(active_symbols)
    if not symbol or signal == "NO TRADE":
        log(f"🔍 [АШЫҚ ПОЗИЦИЯЛАР: {len(active_symbols)}/{MAX_OPEN_POSITIONS}] Сигнал ізделуде...")
        return False

    # Ағымдағы сатының маржа сомасы ($1, $4, $7, $11, ...)
    usdt_margin = MARTINGALE_STEPS[current_step_idx]

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

    # Native TP/SL & Limit Price
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

    # LIMIT (MAKER) ОРДЕР АШУ
    res = session.place_order(
        category="linear",
        symbol=symbol,
        side=order_side,
        orderType="Limit",
        price=formatted_limit,
        qty=formatted_qty,
        takeProfit=formatted_tp,
        stopLoss=formatted_sl,
        positionIdx=pos_idx
    )
    
    if res['retCode'] == 0:
        log(f"⚡ [САТЫ #{current_step_idx + 1} | МАРЖА: ${usdt_margin} USDT] {symbol} {signal} | Бағасы: {formatted_limit} | TP: {formatted_tp} | SL: {formatted_sl}")
        return True
    else:
        log(f"Ордер ашу қатесі ({symbol}): {res['retMsg']} (Code: {res['retCode']})")
    return False

def manage_multi_positions():
    global current_step_idx
    
    positions = get_active_positions()
    active_symbols = [p['symbol'] for p in positions]

    if len(positions) < MAX_OPEN_POSITIONS:
        open_new_step_order(active_symbols)

    for pos in positions:
        symbol = pos['symbol']
        side = pos['side']
        unrealised_pnl = float(pos.get('unrealisedPnl', 0))
        log(f"📊 [ПОЗИЦИЯ] {symbol} {side} | PnL: {unrealised_pnl:.2f} USDT | Саты маржасы: ${MARTINGALE_STEPS[current_step_idx]} USDT")

# ==============================================================================
# MAIN LOOP
# ==============================================================================
def main():
    global initial_balance, current_step_idx
    log(f"🚀 Бот іске қосылды (Арнайы Мартингейл Кестесі: $1, $4, $7, $11... | Limit/Maker Режим)")
    
    initial_balance = get_wallet_balance()
    last_balance = initial_balance
    log(f"💵 Бастапқы Баланс: {initial_balance:.2f} USDT | Мақсат: +{TARGET_TOTAL_PROFIT} USDT")

    while True:
        try:
            current_balance = get_wallet_balance()
            total_profit = current_balance - initial_balance

            # Пайданы тексеру
            if total_profit >= TARGET_TOTAL_PROFIT:
                log(f"🎉 МАҚСАТ ОРЫНДАЛДЫ! Жалпы таза пайда: +{total_profit:.2f} USDT.")
                break

            # Баланс өзгерісін бақылау
            balance_change = current_balance - last_balance
            if balance_change > 0.05:
                log(f"✅ ТЕЙК-ПРОФИТ СОҒЫЛДЫ (+{balance_change:.2f} USDT)! Барлық минустар жабылды. 1-сатыға ($1) қайтамыз.")
                current_step_idx = 0  # 1-сатыға ($1) оралу
                last_balance = current_balance
            elif balance_change < -0.05:
                log(f"❌ СТОП-ЛОСС СОҒЫЛДЫ ({balance_change:.2f} USDT). Келесі сатыға өтеміз.")
                current_step_idx = min(current_step_idx + 1, len(MARTINGALE_STEPS) - 1)
                log(f"➡️ Жаңа саты: #{current_step_idx + 1} (Маржа: ${MARTINGALE_STEPS[current_step_idx]} USDT)")
                last_balance = current_balance

            manage_multi_positions()
            time.sleep(3)

        except Exception as e:
            log(f"Қате: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
