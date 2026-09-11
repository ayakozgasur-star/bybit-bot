import os
import time
import pandas as pd
import pandas_ta as ta
from pybit.unified_trading import HTTP

# ==========================================
# 1. КОНФИГУРАЦИЯ ЖӘНЕ ПАРАМЕТРЛЕР
# ==========================================
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")

# Сауда жасайтын монеталар
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "1000PEPEUSDT"]

LEVERAGE = 20           # 20x Плечо
STOP_LOSS_PCT = 0.005   # 0.5% Stop-Loss
TAKE_PROFIT_PCT = 0.10  # 10.0% Take-Profit
QTY_USD = 10            # Арбір ордерге кіретін маржа (USD)

session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET
)

# ==========================================
# 2. ИНДИКАТОРЛАР ЖӘНЕ АНАЛИЗ (15m & 5m)
# ==========================================
def fetch_klines(symbol, interval, limit=200):
    try:
        res = session.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(res['result']['list'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df = df.iloc[::-1].reset_index(drop=True)
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['volume'] = df['volume'].astype(float)
        return df
    except Exception as e:
        print(f"[{symbol}] Қателік (kline): {e}")
        return None

def analyze_market(symbol):
    # 15m Тренд фильтрі (EMA 200)
    df_15m = fetch_klines(symbol, interval="15", limit=200)
    if df_15m is None or len(df_15m) < 200:
        return None
    df_15m['ema200'] = ta.ema(df_15m['close'], length=200)
    trend_15m_long = df_15m['close'].iloc[-1] > df_15m['ema200'].iloc[-1]
    trend_15m_short = df_15m['close'].iloc[-1] < df_15m['ema200'].iloc[-1]

    # 5m Негізгі Анализ (RSI, ADX, Volume SMA)
    df_5m = fetch_klines(symbol, interval="5", limit=100)
    if df_5m is None or len(df_5m) < 50:
        return None
    
    df_5m['rsi'] = ta.rsi(df_5m['close'], length=14)
    adx_df = ta.adx(df_5m['high'], df_5m['low'], df_5m['close'], length=14)
    df_5m['adx'] = adx_df['ADX_14']
    df_5m['vol_sma'] = ta.sma(df_5m['volume'], length=20)

    last_5m = df_5m.iloc[-1]
    
    # Сигналдарды тексеру
    vol_confirm = last_5m['volume'] > last_5m['vol_sma']
    adx_confirm = last_5m['adx'] > 20

    if trend_15m_long and last_5m['rsi'] > 55 and vol_confirm and adx_confirm:
        return "BUY"
    elif trend_15m_short and last_5m['rsi'] < 45 and vol_confirm and adx_confirm:
        return "SELL"
    
    return None

# ==========================================
# 3. ОРДЕРЛЕРДІ АШУ ЖӘНЕ РИСК МЕНЕДЖМЕНТ (20x, 0.5% SL, 10% TP)
# ==========================================
def set_leverage(symbol):
    try:
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except Exception:
        pass  # Егер плечо бұрын қойылған болса, қателікті өткізіп жібереді

def open_position(symbol, side):
    set_leverage(symbol)
    
    # Ағымдағы бағаны алу
    ticker = session.get_tickers(category="linear", symbol=symbol)
    price = float(ticker['result']['list'][0]['lastPrice'])
    
    qty = round((QTY_USD * LEVERAGE) / price, 3)
    if symbol == "1000PEPEUSDT":
        qty = int(qty) # PEPE үшін бүтін сан қажет

    # Hedge Mode позиция индекстері: 1 - Long, 2 - Short
    pos_idx = 1 if side == "BUY" else 2
    
    # Stop Loss мен Take Profit есептеу
    if side == "BUY":
        sl_price = round(price * (1 - STOP_LOSS_PCT), 4)
        tp_price = round(price * (1 + TAKE_PROFIT_PCT), 4)
    else:
        sl_price = round(price * (1 + STOP_LOSS_PCT), 4)
        tp_price = round(price * (1 - TAKE_PROFIT_PCT), 4)

    try:
        session.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            positionIdx=pos_idx,
            stopLoss=str(sl_price),
            takeProfit=str(tp_price),
            timeInForce="GTC"
        )
        print(f"🚀 [{symbol}] {side} Ордер ашылды! Бағасы: {price} | SL: {sl_price} (-0.5%) | TP: {tp_price} (+10%)")
    except Exception as e:
        print(f"[{symbol}] Ордер ашудағы қателік: {e}")

# ==========================================
# 4. БОТТЫҢ НЕГІЗГІ ЦИКЛІ (Цикл)
# ==========================================
def run_bot():
    print("🤖 5m Scalper Bot (20x Leverage, 0.5% SL, 10% TP) іске қосылды...")
    while True:
        for symbol in SYMBOLS:
            signal = analyze_market(symbol)
            if signal:
                print(f"🔥 [{symbol}] Сигнал анықталды: {signal}")
                open_position(symbol, signal)
            else:
                print(f"💤 [{symbol}] Сигнал жоқ, күту режимі...")
        
        # 5 минуттық шамның жабылуын күту (300 секунд)
        time.sleep(300)

if __name__ == "__main__":
    run_bot()
