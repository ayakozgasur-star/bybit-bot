import os
import time
import pandas as pd
from pybit.unified_trading import HTTP

API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")

SYMBOLS = ["SOLUSDT", "XRPUSDT", "1000PEPEUSDT", "NEARUSDT", "AVAXUSDT"]

LEVERAGE = 10             
RISK_PCT = 0.02           
TRAILING_STOP_TRIGGER = 0.006 # +0.6% пайдада Трейлинг іске қосылады

session = HTTP(
    demo=True,
    api_key=API_KEY,
    api_secret=API_SECRET
)

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
        print(f"[{symbol}] Kline алу қатесі: {e}")
        return None

def get_account_balance():
    try:
        res = session.get_wallet_balance(accountType="UNIFIED")
        balance = float(res['result']['list'][0]['totalEquity'])
        return balance
    except Exception:
        return 1000.0

def get_symbol_precision(symbol, price):
    margin_usd = get_account_balance() * RISK_PCT
    position_usd = margin_usd * LEVERAGE
    raw_qty = position_usd / price

    if symbol in ["SOLUSDT", "AVAXUSDT", "NEARUSDT"]:
        return round(raw_qty, 1)
    elif symbol == "XRPUSDT":
        return round(raw_qty, 0)
    elif symbol == "1000PEPEUSDT":
        return int(raw_qty)
    return round(raw_qty, 2)

def analyze_market(symbol):
    df_1h = fetch_klines(symbol, interval="60", limit=100)
    if df_1h is None or len(df_1h) < 50:
        return None, None
    df_1h['ema50'] = calculate_ema(df_1h['close'], 50)
    trend_1h_long = df_1h['close'].iloc[-1] > df_1h['ema50'].iloc[-1]
    trend_1h_short = df_1h['close'].iloc[-1] < df_1h['ema50'].iloc[-1]

    df_15m = fetch_klines(symbol, interval="15", limit=200)
    if df_15m is None or len(df_15m) < 200:
        return None, None
    df_15m['ema200'] = calculate_ema(df_15m['close'], 200)
    trend_15m_long = df_15m['close'].iloc[-1] > df_15m['ema200'].iloc[-1]
    trend_15m_short = df_15m['close'].iloc[-1] < df_15m['ema200'].iloc[-1]

    global_long = trend_1h_long and trend_15m_long
    global_short = trend_1h_short and trend_15m_short

    if not (global_long or global_short):
        return None, None

    df_5m = fetch_klines(symbol, interval="5", limit=100)
    if df_5m is None or len(df_5m) < 50:
        return None, None

    df_5m['rsi'] = calculate_rsi(df_5m['close'], 14)
    _, _, df_5m['macd_hist'] = calculate_macd(df_5m['close'])
    df_5m['vol_sma'] = df_5m['volume'].rolling(20).mean()
    df_5m['atr'] = calculate_atr(df_5m, 14)

    last_5m = df_5m.iloc[-1]
    prev_5m = df_5m.iloc[-2]

    volume_confirm = last_5m['volume'] > (last_5m['vol_sma'] * 1.25)
    macd_bull = last_5m['macd_hist'] > 0 and last_5m['macd_hist'] > prev_5m['macd_hist']
    macd_bear = last_5m['macd_hist'] < 0 and last_5m['macd_hist'] < prev_5m['macd_hist']

    if global_long and last_5m['rsi'] > 52 and macd_bull and volume_confirm:
        return "BUY", last_5m['atr']
    elif global_short and last_5m['rsi'] < 48 and macd_bear and volume_confirm:
        return "SELL", last_5m['atr']

    return None, None

def set_leverage_and_mode(symbol):
    try:
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except Exception:
        pass
    try:
        session.switch_position_mode(category="linear", symbol=symbol, mode=3)
    except Exception:
        pass

def manage_trailing_stop():
    try:
        positions = session.get_positions(category="linear", settleCoin="USDT")['result']['list']
        for pos in positions:
            size = float(pos['size'])
            if size > 0:
                symbol = pos['symbol']
                side = pos['side']
                entry_price = float(pos['avgPrice'])
                current_price = float(pos['markPrice'])
                current_sl = float(pos['stopLoss']) if pos['stopLoss'] else 0.0

                if side == "Buy":
                    profit_pct = (current_price - entry_price) / entry_price
                    new_sl = round(entry_price * 1.003, 6) # +0.3% Безубыток
                    if profit_pct >= TRAILING_STOP_TRIGGER and (current_sl < new_sl or current_sl == 0):
                        session.set_trading_stop(
                            category="linear", symbol=symbol, positionIdx=1, stopLoss=str(new_sl)
                        )
                        print(f"🛡️ [{symbol}] BUY Трейлинг-Стоп іске қосылды! Жаңа SL: {new_sl}")

                elif side == "Sell":
                    profit_pct = (entry_price - current_price) / entry_price
                    new_sl = round(entry_price * 0.997, 6) # +0.3% Безубыток
                    if profit_pct >= TRAILING_STOP_TRIGGER and (current_sl > new_sl or current_sl == 0):
                        session.set_trading_stop(
                            category="linear", symbol=symbol, positionIdx=2, stopLoss=str(new_sl)
                        )
                        print(f"🛡️ [{symbol}] SELL Трейлинг-Стоп іске қосылды! Жаңа SL: {new_sl}")
    except Exception as e:
        print(f"Трейлинг-стоп қателігі: {e}")

def open_position(symbol, side, atr):
    set_leverage_and_mode(symbol)
    
    ticker = session.get_tickers(category="linear", symbol=symbol)
    price = float(ticker['result']['list'][0]['lastPrice'])
    
    total_qty = get_symbol_precision(symbol, price)
    if total_qty <= 0:
        return

    # Позицияны 2-ге бөлеміз (50% TP1, 50% TP2)
    qty_tp1 = total_qty / 2
    if symbol in ["SOLUSDT", "AVAXUSDT", "NEARUSDT"]:
        qty_tp1 = round(qty_tp1, 1)
    elif symbol == "XRPUSDT":
        qty_tp1 = round(qty_tp1, 0)
    elif symbol == "1000PEPEUSDT":
        qty_tp1 = int(qty_tp1)
    else:
        qty_tp1 = round(qty_tp1, 2)

    qty_tp2 = total_qty - qty_tp1

    pos_idx = 1 if side == "BUY" else 2
    
    sl_distance = atr * 1.5
    tp1_distance = atr * 1.0  # Жақын TP1 (Жылдам фиксация)
    tp2_distance = atr * 2.5  # Алыс TP2 (Тренд бойынша үлкен профит)

    if side == "BUY":
        sl_price = round(price - sl_distance, 6)
        tp1_price = round(price + tp1_distance, 6)
        tp2_price = round(price + tp2_distance, 6)
        close_side = "Sell"
    else:
        sl_price = round(price + sl_distance, 6)
        tp1_price = round(price - tp1_distance, 6)
        tp2_price = round(price - tp2_distance, 6)
        close_side = "Buy"

    try:
        # 1. Негізгі позицияны ашу (Жалпы Стоп-Лосспен)
        session.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(total_qty),
            positionIdx=pos_idx,
            stopLoss=str(sl_price),
            timeInForce="GTC"
        )
        
        # 2. TP1 ордерін қою (көлемнің 50%-ы)
        session.place_order(
            category="linear",
            symbol=symbol,
            side=close_side,
            orderType="Limit",
            price=str(tp1_price),
            qty=str(qty_tp1),
            positionIdx=pos_idx,
            reduceOnly=True,
            timeInForce="GTC"
        )

        # 3. TP2 ордерін қою (қалған 50%-ы)
        session.place_order(
            category="linear",
            symbol=symbol,
            side=close_side,
            orderType="Limit",
            price=str(tp2_price),
            qty=str(qty_tp2),
            positionIdx=pos_idx,
            reduceOnly=True,
            timeInForce="GTC"
        )

        print(f"🔥 [ОДЕР АШЫЛДЫ] [{symbol}] {side} | Qty: {total_qty} | SL: {sl_price} | TP1: {tp1_price} | TP2: {tp2_price}")
    except Exception as e:
        print(f"[{symbol}] Ордер ашу қатесі: {e}")

def run_bot():
    print("🚀 Бот іске қосылды (2 Тейк-Профит: TP1 50% + TP2 50%)...")
    while True:
        manage_trailing_stop()
        
        for symbol in SYMBOLS:
            signal, atr = analyze_market(symbol)
            if signal and atr:
                open_position(symbol, side=signal, atr=atr)
            else:
                print(f"💤 [{symbol}] Сигнал жоқ...")
        
        time.sleep(150)

if __name__ == "__main__":
    run_bot()
