import os
import time
from datetime import datetime
import pandas as pd
from pybit.unified_trading import HTTP

# ==============================================================================
# 10-STEP MARTINGALE CONFIG (MULTIPLIER = 2.5X)
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True  # Demo аккаунт режимі

SYMBOLS = ["SOLUSDT", "XRPUSDT", "1000PEPEUSDT", "NEARUSDT", "ETHUSDT"]

LEVERAGE = 15                  # 15x Плечо
BASE_MARGIN = 5.0              # 1-ордер маржасы $5 USDT
MULTIPLIER = 2.5               # Маржаны 2.5 есеге өсіру
MAX_STEPS = 10                 # 10 адымдық шек

session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
    demo=IS_DEMO
)

symbol_state = {symbol: {"step": 0, "active": False, "side": "Buy"} for symbol in SYMBOLS}

def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [MARTINGALE-2.5X] {msg}", flush=True)

def format_value(value, step):
    if step is None or step == 0: 
        return str(value)
    step_str = f"{step:.8f}".rstrip('0')
    precision = len(step_str.split('.')[1]) if '.' in step_str else 0
    return f"{round(value, precision):.{precision}f}"

def get_klines(symbol, interval="15", limit=50):
    try:
        res = session.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        if res.get('retCode') != 0: 
            return None
        df = pd.DataFrame(res['result']['list'], columns=['start_time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        for col in ['open', 'high', 'low', 'close']: 
            df[col] = df[col].astype(float)
        return df.iloc[::-1].reset_index(drop=True)
    except Exception as e:
        log(f"Klines қатесі ({symbol}): {e}")
        return None

def get_active_positions(symbol):
    try:
        res = session.get_positions(category="linear", symbol=symbol)
        if res.get('retCode') == 0:
            return [p for p in res['result']['list'] if float(p['size']) > 0]
    except Exception as e:
        log(f"Позиция тексеру қатесі: {e}")
    return []

def calculate_margin_for_step(step_index):
    """Маржаны 2.5 есе өсіріп есептеу ($5 * 2.5^step)"""
    return round(BASE_MARGIN * (MULTIPLIER ** step_index), 2)

def open_martingale_step(symbol, side, step_index):
    df = get_klines(symbol)
    if df is None: 
        return False
    close_price = df['close'].iloc[-1]

    try:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        info = res['result']['list'][0]
        qty_step = float(info['lotSizeFilter']['qtyStep'])
        price_step = float(info['priceFilter']['tickSize'])
    except Exception: 
        return False

    margin = calculate_margin_for_step(step_index)
    position_size_usdt = margin * LEVERAGE
    raw_qty = position_size_usdt / close_price
    formatted_qty = format_value(raw_qty, qty_step)

    # TP/SL деңгейлері (ROI +7.5% TP, ROI -6.0% SL)
    tp_pct = 0.005  # +0.5% баға қозғалысы
    sl_pct = 0.004  # -0.4% баға қозғалысы

    if side == "Buy":
        pos_idx = 1
        tp = close_price * (1 + tp_pct)
        sl = close_price * (1 - sl_pct)
    else:
        pos_idx = 2
        tp = close_price * (1 - tp_pct)
        sl = close_price * (1 + sl_pct)

    formatted_tp = format_value(tp, price_step)
    formatted_sl = format_value(sl, price_step)

    try:
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except Exception: 
        pass

    try:
        res = session.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=formatted_qty,
            takeProfit=formatted_tp,
            stopLoss=formatted_sl,
            positionIdx=pos_idx
        )
        if res.get('retCode') == 0:
            log(f"🔄 [{symbol}] {side} АШЫЛДЫ | АДЫМ: {step_index + 1}/{MAX_STEPS} | Маржа: ${margin} USDT | Көлем: ${position_size_usdt}")
            symbol_state[symbol]["step"] = step_index
            symbol_state[symbol]["active"] = True
            symbol_state[symbol]["side"] = side
            return True
    except Exception as e:
        log(f"Ордер жіберу қатесі: {e}")
    return False

def check_last_order_pnl(symbol):
    """Соңғы ордердің PnL-ін тексеру"""
    try:
        res = session.get_closed_pnl(category="linear", symbol=symbol, limit=1)
        if res.get('retCode') == 0 and res['result']['list']:
            return float(res['result']['list'][0]['closedPnl'])
    except Exception as e:
        log(f"PnL тексеру қатесі ({symbol}): {e}")
    return 0

def main():
    log(f"🚀 2.5X МАРТИНГЕЙЛ БОТЫ ІСКЕ ҚОСЫЛДЫ | Плечо: {LEVERAGE}x | Бастапқы маржа: ${BASE_MARGIN} USDT | Multiplier: {MULTIPLIER}x")
    while True:
        for symbol in SYMBOLS:
            positions = get_active_positions(symbol)
            
            # Ордер жабылған кезде
            if not positions and symbol_state[symbol]["active"]:
                last_pnl = check_last_order_pnl(symbol)
                curr_step = symbol_state[symbol]["step"]
                
                if last_pnl > 0:
                    log(f"✅ [{symbol}] ПЛЮС! PnL: +${round(last_pnl, 2)}. Цикл қайта 1-адымнан басталады.")
                    symbol_state[symbol]["step"] = 0
                    symbol_state[symbol]["active"] = False
                else:
                    next_step = curr_step + 1
                    if next_step < MAX_STEPS:
                        log(f"❌ [{symbol}] МИНУС. PnL: -${round(abs(last_pnl), 2)}. {next_step + 1}-адым ашылуда (Маржа: ${calculate_margin_for_step(next_step)})...")
                        open_martingale_step(symbol, symbol_state[symbol]["side"], next_step)
                    else:
                        log(f"⚠️ [{symbol}] 10 адым таусылды. Депозитті сақтау үшін 1-адымға ораламыз.")
                        symbol_state[symbol]["step"] = 0
                        symbol_state[symbol]["active"] = False

            # Жаңа циклды бастау
            if not positions and not symbol_state[symbol]["active"]:
                open_martingale_step(symbol, "Buy", 0)

        time.sleep(15)

if __name__ == "__main__":
    main()
