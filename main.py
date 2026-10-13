import os
import time
from datetime import datetime
import pandas as pd
import ta
from pybit.unified_trading import HTTP

# ==============================================================================
# SCALPING BOT CONFIG (SOL & ETH ONLY - DEMO MODE)
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True

SYMBOLS = ["SOLUSDT", "ETHUSDT"]
CATEGORY = "linear"

# СІЗ СҰРАҒАН ЖАҢА ПАРАМЕТРЛЕР:
LEVERAGE = 25          # Левередж 25x
BASE_MARGIN = 20.0     # Маржа $20
TAKE_PROFIT_PCT = 0.004  # Тейк-Профит: +0.4%
STOP_LOSS_PCT = 0.003    # Стоп-Лосс: -0.3%

# Unified Trading Demo режиміне арналған қосылым
session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET, demo=IS_DEMO)

def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [SCALP-BOT] {msg}", flush=True)

def set_leverage(symbol):
    try:
        session.set_leverage(category=CATEGORY, symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except Exception:
        pass

def get_klines(symbol, interval="3"):
    try:
        res = session.get_kline(category=CATEGORY, symbol=symbol, interval=interval, limit=250)
        if res.get('retCode') != 0: 
            return None
        df = pd.DataFrame(res['result']['list'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        log(f"Klines қатесі ({symbol}): {e}")
        return None

def get_active_positions(symbol):
    try:
        res = session.get_positions(category=CATEGORY, symbol=symbol)
        if res.get('retCode') == 0:
            positions = [p for p in res['result']['list'] if float(p['size']) > 0]
            return positions
    except Exception:
        pass
    return []

def open_scalp_order(symbol, side, current_price):
    try:
        res = session.get_instruments_info(category=CATEGORY, symbol=symbol)
        info = res['result']['list'][0]
        qty_step = float(info['lotSizeFilter']['qtyStep'])
        price_step = float(info['priceFilter']['tickSize'])
    except Exception:
        qty_step = 0.01
        price_step = 0.01

    raw_qty = (BASE_MARGIN * LEVERAGE) / current_price
    
    step_str = f"{qty_step:.8f}".rstrip('0')
    precision = len(step_str.split('.')[1]) if '.' in step_str else 0
    qty = round(raw_qty, precision)
    if qty < qty_step:
        qty = qty_step

    if side == "Buy":
        sl_price = round(current_price * (1 - STOP_LOSS_PCT), precision)
        tp_price = round(current_price * (1 + TAKE_PROFIT_PCT), precision)
        pos_idx = 1
    else:
        sl_price = round(current_price * (1 + STOP_LOSS_PCT), precision)
        tp_price = round(current_price * (1 - TAKE_PROFIT_PCT), precision)
        pos_idx = 2

    try:
        order = session.place_order(
            category=CATEGORY,
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            stopLoss=str(sl_price),
            takeProfit=str(tp_price),
            positionIdx=pos_idx
        )
        if order.get('retCode') == 0:
            log(f"✅ [{symbol}] ОРДЕР АШЫЛДЫ: {side} | Маржа: ${BASE_MARGIN} | Левередж: {LEVERAGE}x | TP: {tp_price} | SL: {sl_price}")
    except Exception as e:
        log(f"Ордер ашу қатесі ({symbol}): {e}")

def run_bot():
    log(f"🚀 Скальпинг Бот жаңа баптаулармен іске қосылды (Маржа: $20, Левередж: 25x, TP: 0.4%, SL: 0.3%)...")
    for symbol in SYMBOLS:
        set_leverage(symbol)

    while True:
        try:
            for symbol in SYMBOLS:
                positions = get_active_positions(symbol)
                if len(positions) > 0:
                    continue

                df = get_klines(symbol)
                if df is None or len(df) < 220:
                    continue
                
                df['ema200'] = ta.trend.ema_indicator(df['close'], window=200)
                
                indicator_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
                df['bb_high'] = indicator_bb.bollinger_hband()
                df['bb_low'] = indicator_bb.bollinger_lband()
                
                curr = df.iloc[-1]
                current_price = curr['close']

                is_uptrend = current_price > curr['ema200']
                is_downtrend = current_price < curr['ema200']

                body_size = abs(curr['close'] - curr['open'])
                total_range = curr['high'] - curr['low']
                
                if total_range == 0:
                    continue

                lower_wick = min(curr['open'], curr['close']) - curr['low']
                upper_wick = curr['high'] - max(curr['open'], curr['close'])

                long_pinbar = (
                    is_uptrend and
                    (curr['low'] <= curr['bb_low'] or lower_wick >= total_range * 0.5) and
                    (lower_wick >= body_size * 2) and
                    (curr['close'] > curr['open'])
                )

                short_pinbar = (
                    is_downtrend and
                    (curr['high'] >= curr['bb_high'] or upper_wick >= total_range * 0.5) and
                    (upper_wick >= body_size * 2) and
                    (curr['close'] < curr['open'])
                )

                if long_pinbar:
                    open_scalp_order(symbol, "Buy", current_price)
                elif short_pinbar:
                    open_scalp_order(symbol, "Sell", current_price)

            time.sleep(10)

        except Exception as e:
            log(f"Цикл қатесі: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_bot()
