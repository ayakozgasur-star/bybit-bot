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
MAX_OPEN_POSITIONS = 3        # БЕКІТІЛЕТІН МОНЕТАЛАР САНЫ (3 МОНЕТА)

TP_PCT = 0.005  # +0.5% TP
SL_PCT = 0.003  # -0.3% SL
PRICE_OFFSET_PCT = 0.0002  # 0.02% Offset (Post-Only Maker үшін)

# ==============================================================================
# EXACT MARTINGALE STEPS (1, 4, 7, 11, 14, 17, 21, 24, 27, 31...)
# ==============================================================================
def get_exact_martingale_steps():
    """Сіз көрсеткен сатыларды шатаспайтындай етіп 100-сатыға дейін дайындау"""
    steps = [1, 4, 7, 11, 14, 17, 21, 24, 27, 31]
    current = 31
    # Калған сатыларды да тура осы 2 рет +3, 1 рет +4 ережесімен толтырамыз
    for i in range(11, 101):
        if i % 3 == 1:
            current += 4
        else:
            current += 3
        steps.append(current)
    return steps

MARTINGALE_STEPS = get_exact_martingale_steps()

# ==============================================================================
# INITIALIZATION & GLOBALS
# ==============================================================================
session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
    demo=IS_DEMO
)

symbol_steps = {}            # Әр монетаның сатысы
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

def find_initial_3_symbols():
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
# MARTINGALE STEP & PNL ACCURACY CONTROL
# ==============================================================================
def update_step_for_symbol(symbol):
    """ByBit-тен жабылған ордер P&L-ін дәл алып, сатыны реттеу"""
    global symbol_steps, last_checked_pnl_time
    
    time.sleep(2)  # ByBit базасын жаңартуға 2 сек кідіріс
    try:
        res = session.get_closed_pnl(category="linear", symbol=symbol, limit=1)
        if res['retCode'] == 0 and len(res['result']['list']) > 0:
            last_order = res['result']['list'][0]
            updated_time = last_order['updatedTime']
            
            # Егер осы ордер бұған дейін тексерілген болса, қайталамаймыз
            if last_checked_pnl_time.get(symbol) == updated_time:
                return
            
            last_checked_pnl_time[symbol] = updated_time
            last_pnl = float(last_order['closedPnl'])

            if last_pnl > 0:
                symbol_steps[symbol] = 0  # Тейк-профит соғылса -> 1-сатыға ($1) түсеміз
                log(f"✅ [{symbol}] ТЕЙК-ПРОФИТ (+{last_pnl:.2f} USDT)! Саты қайтадан #1-ге ($1) түсті.")
            elif last_pnl < 0:
                symbol_steps[symbol] = symbol_steps.get(symbol, 0) + 1  # Стоп-лосс соғылса -> Саты +1
                current_step_num = symbol_steps[symbol] + 1
                next_margin = MARTINGALE_STEPS[symbol_steps[symbol]]
                log(f"🔻 [{symbol}] СТОП-ЛОСС ({last_pnl:.2f} USDT). Келесі саты: #{current_step_num} (Маржа: ${next_margin} USDT)")

    except Exception as e:
        log(f"P&L бақылау қатесі ({symbol}): {e}")

# ==============================================================================
# TRADE EXECUTION (MAKER POST-ONLY)
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
    global symbol_steps
    
    # 1. Алдымен соңғы P&L нәтижесін тексереміз
    update_step_for_symbol(symbol)

    step_idx = symbol_steps.get(symbol, 0)
    # Саты индексі массивтен асып кетпеуін қадағалаймыз
    if step_idx >= len(MARTINGALE_STEPS):
        step_idx = len(MARTINGALE_STEPS) - 1

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

    # MAKER ЛОГИКАСЫ (Post-Only offset)
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
        execInst="PostOnly"  # ТЕК MAKER МЕН ОРЫНДАУ
    )
    
    if res['retCode'] == 0:
        log(f"🎯 [{symbol}] MAKER ОРДЕР АШЫЛДЫ | Саты #{step_idx + 1} | Маржа: ${usdt_margin} USDT | Бағасы: {formatted_limit}")
        return True
    return False

# ==============================================================================
# MAIN LOOP
# ==============================================================================
def main():
    global initial_balance, symbol_steps, selected_3_symbols
    log(f"🚀 Бот іске қосылды (Нақты Сатылық Мартингейл | 3 Монета | Post-Only Maker | Мақсат: +{TARGET_TOTAL_PROFIT} USDT)")
    
    initial_balance = get_wallet_balance()
    log(f"💵 Бастапқы Баланс: {initial_balance:.2f} USDT")

    while True:
        try:
            current_balance = get_wallet_balance()
            total_profit = current_balance - initial_balance

            if total_profit >= TARGET_TOTAL_PROFIT:
                log(f"🎉 МАҚСАТ ОРЫНДАЛДЫ! +100 USDT таза пайда жиналды. Сауда тоқтатылды.")
                break

            # 1. 3 негізгі монетаны таңдау
            if len(selected_3_symbols) < MAX_OPEN_POSITIONS:
                selected_3_symbols = find_initial_3_symbols()
                if len(selected_3_symbols) == MAX_OPEN_POSITIONS:
                    for s in selected_3_symbols:
                        symbol_steps[s] = 0
                    log(f"📌 НЕГІЗГІ 3 МОНЕТА БЕКІТІЛДІ: {selected_3_symbols}.")
                else:
                    time.sleep(5)
                    continue

            # 2. Белсенді позицияларды бақылау
            positions = get_active_positions()
            active_symbols = [p['symbol'] for p in positions]

            # 3. 3 монетаның сатысын қатаң тексеріп, жаңа ордер ашу
            for symbol in selected_3_symbols:
                if symbol not in active_symbols:
                    open_order_for_symbol(symbol)

            time.sleep(3)

        except Exception as e:
            log(f"Қате: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
