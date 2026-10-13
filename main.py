import os
import time
import pandas as pd
import ta
from pybit.unified_trading import HTTP

# API кілттерді орнату (Railway Variables-тен алады)
API_KEY = os.environ.get("BYBIT_API_KEY")
API_SECRET = os.environ.get("BYBIT_API_SECRET")

# Демо (Testnet) үшін арнайы қосылым (401 қатесі шықпас үшін testnet=True және дұрыс баптау)
session = HTTP(
    testnet=True,
    api_key=API_KEY,
    api_secret=API_SECRET
)

SYMBOLS = ["SOLUSDT", "ETHUSDT"]
CATEGORY = "linear"
LEVERAGE = 20        # Левередж 20x
BASE_MARGIN = 5.0    # Әр саудаға тігілетін маржа ($5)

# НАСТРОЙКАЛАР (Скалпинг үшін)
STOP_LOSS_PCT = 0.0025   # Стоп-Лосс: -0.25%
TAKE_PROFIT_PCT = 0.009  # Тейк-Профит: +0.9%

def set_leverage(symbol):
    try:
        session.set_leverage(category=CATEGORY, symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except Exception:
        pass

def get_klines(symbol, interval="3"):
    # EMA 200 және Bollinger Bands үшін limit 250
    res = session.get_kline(category=CATEGORY, symbol=symbol, interval=interval, limit=250)
    df = pd.DataFrame(res['result']['list'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    df = df.iloc[::-1].reset_index(drop=True)
    return df

def get_active_positions(symbol):
    res = session.get_positions(category=CATEGORY, symbol=symbol)
    positions = [p for p in res['result']['list'] if float(p['size']) > 0]
    return positions

def open_scalp_order(symbol, side, current_price):
    qty = round((BASE_MARGIN * LEVERAGE) / current_price, 2)
    if symbol == "ETHUSDT":
        qty = round(qty, 2)
    elif symbol == "SOLUSDT":
        qty = round(qty, 1)

    if side == "Buy":
        sl_price = round(current_price * (1 - STOP_LOSS_PCT), 2)
        tp_price = round(current_price * (1 + TAKE_PROFIT_PCT), 2)
    else:
        sl_price = round(current_price * (1 + STOP_LOSS_PCT), 2)
        tp_price = round(current_price * (1 - TAKE_PROFIT_PCT), 2)

    order = session.place_order(
        category=CATEGORY,
        symbol=symbol,
        side=side,
        orderType="Market",
        qty=str(qty),
        stopLoss=str(sl_price),
        takeProfit=str(tp_price)
    )
    print(f"✅ [{symbol}] 20x WICK/PINBAR ДЕМО ОРДЕРІ АШЫЛДЫ: {side} | Баға: {current_price} | TP: {tp_price} | SL: {sl_price}")

def run_bot():
    print("🚀 Скальпинг Бот іске қосылды (Testnet Demo режимі, EMA 200 + Pin Bar / Bollinger)...")
    for symbol in SYMBOLS:
        set_leverage(symbol)

    while True:
        try:
            for symbol in SYMBOLS:
                positions = get_active_positions(symbol)
                if len(positions) > 0:
                    continue

                df = get_klines(symbol)
                
                # Индикаторлар: EMA 200 және Bollinger Bands
                df['ema200'] = ta.trend.ema_indicator(df['close'], window=200)
                
                indicator_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
                df['bb_high'] = indicator_bb.bollinger_hband()
                df['bb_low'] = indicator_bb.bollinger_lband()
                
                curr = df.iloc[-1]
                current_price = curr['close']

                # Тренд сүзгісі
                is_uptrend = current_price > curr['ema200']
                is_downtrend = current_price < curr['ema200']

                # Price Action / Pin Bar & Bollinger Rejection логикасы
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
            print(f"Қателік: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_bot()
