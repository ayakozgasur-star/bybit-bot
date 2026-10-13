import os
import time
import pandas as pd
import ta
from pybit.unified_trading import HTTP

# API кілттерді орнату
API_KEY = os.environ.get("BYBIT_API_KEY")
API_SECRET = os.environ.get("BYBIT_API_SECRET")

session = HTTP(
    testnet=True, # Демо-шот үшін True (Нақты шотқа ауысқанда False)
    api_key=API_KEY,
    api_secret=API_SECRET
)

SYMBOLS = ["SOLUSDT", "ETHUSDT"]
CATEGORY = "linear"
LEVERAGE = 20        # Левередж 20x
BASE_MARGIN = 5.0    # Әр саудаға тігілетін маржа ($5)

# НАСТРОЙКАЛАР
STOP_LOSS_PCT = 0.0025   # Стоп-Лосс: -0.25%
TAKE_PROFIT_PCT = 0.009  # Тейк-Профит: +0.9%

def set_leverage(symbol):
    try:
        session.set_leverage(category=CATEGORY, symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except Exception:
        pass

def get_klines(symbol, interval="3"):
    res = session.get_kline(category=CATEGORY, symbol=symbol, interval=interval, limit=50)
    df = pd.DataFrame(res['result']['list'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
    df['close'] = df['close'].astype(float)
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
    print(f"✅ [{symbol}] 20x ОРДЕР АШЫЛДЫ (EMA + StochRSI): {side} | Баға: {current_price} | TP: {tp_price} | SL: {sl_price}")

def run_bot():
    print("🚀 Скальпинг Бот іске қосылды (20x, EMA + StochRSI фильтрі)...")
    for symbol in SYMBOLS:
        set_leverage(symbol)

    while True:
        try:
            for symbol in SYMBOLS:
                positions = get_active_positions(symbol)
                
                if len(positions) > 0:
                    continue

                df = get_klines(symbol)
                
                # Индикаторлар: EMA және StochRSI
                df['ema3'] = ta.trend.ema_indicator(df['close'], window=3)
                df['ema8'] = ta.trend.ema_indicator(df['close'], window=8)
                
                stoch_rsi = ta.momentum.StochRSIIndicator(close=df['close'], window=14, smooth1=3, smooth2=3)
                df['stoch_k'] = stoch_rsi.stochrsi_k()
                
                last = df.iloc[-1]
                prev = df.iloc[-2]
                current_price = last['close']

                # Лонг сигнал: EMA қиылысуы ЖӘНЕ StochRSI шамадан тыс жоғары емес (< 0.8)
                long_signal = (prev['ema3'] <= prev['ema8'] and last['ema3'] > last['ema8']) and (last['stoch_k'] < 0.8)
                
                # Шорт сигнал: EMA қиылысуы ЖӘНЕ StochRSI шамадан тыс төмен емес (> 0.2)
                short_signal = (prev['ema3'] >= prev['ema8'] and last['ema3'] < last['ema8']) and (last['stoch_k'] > 0.2)

                if long_signal:
                    open_scalp_order(symbol, "Buy", current_price)
                elif short_signal:
                    open_scalp_order(symbol, "Sell", current_price)

            time.sleep(10)

        except Exception as e:
            print(f"Қателік: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_bot()
