import os
import time
import pandas as pd
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
QTY_USD = 10            # Әрбір ордерге кіретін маржа (USD)

session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET
)

# ==========================================
# 2. ИНДИКАТОРЛАРДЫ ТАЗА PANDAS-ПЕН ЕСЕПТЕУ
# ==========================================
def calculate_ema(df, window=200):
    return df['close'].ewm(span=window, adjust=False).mean()

def calculate_rsi(df, window=14):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_adx(df, window=14):
    df = df.copy()
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['close'].shift(1))
    df['tr2'] = abs(df['low'] - df['close'].shift(1))
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    
    df['up'] = df['high'] - df['high'].shift(1)
    df['down'] = df['low'].shift(1) - df['low']
    
    df['p_dm'] = df['up'].where((df['up'] > df['down']) & (df['up'] > 0), 0)
    df['m_dm'] = df['down'].where((df['down'] > df['up']) & (df['down'] > 0), 0)
    
    tr_s = df['tr'].rolling(window).mean()
    p_di = 100 * (df['p_dm'].rolling(window).mean() / tr_s)
    m_di = 100 * (df['m_dm'].rolling(window).mean() / tr_s)
    
    dx = 100 * (abs(p_di - m_di) / (p_di + m_di))
    adx = dx.rolling(window).mean()
    return adx

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
    df_15m['ema200'] = calculate_ema(df_15m, 200)
    trend_15m_long = df_15m['close'].iloc[-1] > df_15m['ema200'].iloc[-1]
    trend_15m_short = df_15m['close'].iloc[-1] < df_15m['ema200'].iloc[-1]

    # 5m Негізгі Анализ (RSI, ADX, Volume SMA)
    df_5m = fetch_klines(symbol, interval="5", limit=100)
    if df_5m is None or len(df_5m) < 50:
        return None
    
    df_5m['rsi'] = calculate_rsi(df_5m, 14)
    df_5m['adx'] = calculate_adx(df_5m, 14)
    df_5m['vol_sma'] = df_5m['volume'].rolling(20).mean()

    last_5m = df_5m.iloc[-1]
    
    vol_confirm = last_5m['volume'] > last_5m['vol_sma']
    adx_confirm = last_5m['adx'] > 20 if pd.notna(last_5m['adx']) else False

    if trend_15m_long and last_5m['rsi'] > 55 and vol_confirm and adx_confirm:
        return "BUY"
    elif trend_15m_short and last_5m['rsi'] < 45 and vol_confirm and adx_confirm:
        return "SELL"
    
    return None

# ==========================================
# 3. ОРДЕРЛЕРДІ АШУ ЖӘНЕ РИСК МЕНЕДЖМЕНТ
# ==========================================
def set_leverage(symbol):
    try:
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except Exception:
        pass

def open_position(symbol, side):
    set_leverage(symbol)
    
    ticker = session.get_tickers(category="linear", symbol=symbol)
    price = float(ticker['result']['list'][0]['lastPrice'])
    
    qty = round((QTY_USD * LEVERAGE) / price, 3)
    if symbol == "1000PEPEUSDT":
        qty = int(qty)

    pos_idx = 1 if side == "BUY" else 2
    
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
# 4. БОТТЫҢ НЕГІЗГІ ЦИКЛІ
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
        
        time.sleep(300)

if __name__ == "__main__":
    run_bot()
