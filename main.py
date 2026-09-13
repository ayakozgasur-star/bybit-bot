import os
import time
import pandas as pd
from pybit.unified_trading import HTTP

# ==========================================
# 1. КОНФИГУРАЦИЯ
# ==========================================
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "1000PEPEUSDT"]

LEVERAGE = 10           # 10x қауіпсіз плечо
QTY_USD = 10            # Әр ордерге 10 USD маржа

session = HTTP(
    demo=True,
    api_key=API_KEY,
    api_secret=API_SECRET
)

# ==========================================
# 2. МЫҚТЫ ТЕХНИКАЛЫҚ ИНДИКАТОРЛАР (Pandas)
# ==========================================
def calculate_ema(series, window):
    return series.ewm(span=window, adjust=False).mean()

def calculate_rsi(series, window=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_atr(df, window=14):
    high_low = df['high'] - df['low']
    high_close = abs(df['high'] - df['close'].shift(1))
    low_close = abs(df['low'] - df['close'].shift(1))
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window).mean()

def calculate_macd(series, fast=12, slow=26, signal=9):
    ema_fast = calculate_ema(series, fast)
    ema_slow = calculate_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def fetch_klines(symbol, interval, limit=200):
    try:
        res = session.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(res['result']['list'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df = df.iloc[::-1].reset_index(drop=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df
    except Exception as e:
        print(f"[{symbol}] Kline алудағы қате: {e}")
        return None

# ==========================================
# 3. ТЕРЕН ДЕТАЛЬДЫ НАРАҚ АНАЛИЗІ
# ==========================================
def analyze_market(symbol):
    # 1-қадам: 1h Жоғары трендті тексеру (EMA50)
    df_1h = fetch_klines(symbol, interval="60", limit=100)
    if df_1h is None or len(df_1h) < 50:
        return None, None
    df_1h['ema50'] = calculate_ema(df_1h['close'], 50)
    trend_1h_long = df_1h['close'].iloc[-1] > df_1h['ema50'].iloc[-1]
    trend_1h_short = df_1h['close'].iloc[-1] < df_1h['ema50'].iloc[-1]

    # 2-қадам: 15m Орташа трендті тексеру (EMA200)
    df_15m = fetch_klines(symbol, interval="15", limit=200)
    if df_15m is None or len(df_15m) < 200:
        return None, None
    df_15m['ema200'] = calculate_ema(df_15m['close'], 200)
    trend_15m_long = df_15m['close'].iloc[-1] > df_15m['ema200'].iloc[-1]
    trend_15m_short = df_15m['close'].iloc[-1] < df_15m['ema200'].iloc[-1]

    # Егер 1h және 15m трендтері сәйкес келмесе — КІРМЕЙМІЗ!
    global_long = trend_1h_long and trend_15m_long
    global_short = trend_1h_short and trend_15m_short

    if not (global_long or global_short):
        return None, None

    # 3-қадам: 5m Нақты сигнал торабы (RSI + MACD + Volume + ATR)
    df_5m = fetch_klines(symbol, interval="5", limit=100)
    if df_5m is None or len(df_5m) < 50:
        return None, None

    df_5m['rsi'] = calculate_rsi(df_5m['close'], 14)
    _, _, df_5m['macd_hist'] = calculate_macd(df_5m['close'])
    df_5m['vol_sma'] = df_5m['volume'].rolling(20).mean()
    df_5m['atr'] = calculate_atr(df_5m, 14)

    last_5m = df_5m.iloc[-1]
    prev_5m = df_5m.iloc[-2]

    # Фильтрлер
    volume_confirm = last_5m['volume'] > (last_5m['vol_sma'] * 1.3) # Көлем 30% жоғары
    macd_bull = last_5m['macd_hist'] > 0 and last_5m['macd_hist'] > prev_5m['macd_hist']
    macd_bear = last_5m['macd_hist'] < 0 and last_5m['macd_hist'] < prev_5m['macd_hist']

    # Түпкілікті сигнал
    if global_long and last_5m['rsi'] > 53 and macd_bull and volume_confirm:
        return "BUY", last_5m['atr']
    elif global_short and last_5m['rsi'] < 47 and macd_bear and volume_confirm:
        return "SELL", last_5m['atr']

    return None, None

# ==========================================
# 4. ОРДЕР АШУ ЖӘНЕ ДИНАМИКАЛЫҚ ATR РИСК
# ==========================================
def set_leverage_and_mode(symbol):
    try:
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except Exception:
        pass
    try:
        session.switch_position_mode(category="linear", symbol=symbol, mode=3)
    except Exception:
        pass

def open_position(symbol, side, atr):
    set_leverage_and_mode(symbol)
    
    ticker = session.get_tickers(category="linear", symbol=symbol)
    price = float(ticker['result']['list'][0]['lastPrice'])
    
    # Qty дөңгелектеу
    if symbol == "BTCUSDT":
        qty = round((QTY_USD * LEVERAGE) / price, 3)
    elif symbol == "ETHUSDT":
        qty = round((QTY_USD * LEVERAGE) / price, 2)
    elif symbol in ["SOLUSDT", "XRPUSDT"]:
        qty = round((QTY_USD * LEVERAGE) / price, 1)
    elif symbol == "1000PEPEUSDT":
        qty = int((QTY_USD * LEVERAGE) / price)

    pos_idx = 1 if side == "BUY" else 2
    
    # ATR негізінде Динамикалық SL / TP есептеу
    sl_distance = atr * 1.5
    tp_distance = atr * 3.0

    if side == "BUY":
        sl_price = round(price - sl_distance, 4)
        tp_price = round(price + tp_distance, 4)
    else:
        sl_price = round(price + sl_distance, 4)
        tp_price = round(price - tp_distance, 4)

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
        print(f"🎯 [САПАЛЫ КІРУ] [{symbol}] {side} | Баға: {price} | ATR: {atr:.4f} | SL: {sl_price} | TP: {tp_price}")
    except Exception as e:
        print(f"[{symbol}] Ордер қатесі: {e}")

# ==========================================
# 5. БОТТЫ ЖҮРГІЗУ
# ==========================================
def run_bot():
    print("🧠 Көпденгейлі терең аналитикалық бот [DEMO] іске қосылды...")
    while True:
        for symbol in SYMBOLS:
            signal, atr = analyze_market(symbol)
            if signal and atr:
                print(f"🔥 [{symbol}] МІНСІЗ СИГНАЛ: {signal}")
                open_position(symbol, side=signal, atr=atr)
            else:
                print(f"💤 [{symbol}] Анализ бойынша кіруге әлі ерте...")
        
        time.sleep(300) # 5 минуттық шам жабылғанда сканерлеу

if __name__ == "__main__":
    run_bot()
