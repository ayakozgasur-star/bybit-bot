import os
import time
from datetime import datetime
import pandas as pd
import pandas_ta as ta
from pybit.unified_trading import HTTP

# ==============================================================================
# CONFIG / PARAMETERS (ШЕКТЕУСІЗ МЫҚТЫ СИГНАЛДАР РЕЖИМІ)
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True

# 8 волатильді монета
SYMBOLS = ["SOLUSDT", "XRPUSDT", "1000PEPEUSDT", "NEARUSDT", "AVAXUSDT", "ETHUSDT", "DOGEUSDT", "SUIUSDT"]

LEVERAGE = 10
RISK_PCT = 0.08              # Тәуекел 8% (бір уақытта 5 ордерге дейін ұстау үшін)
MAX_ACTIVE_POSITIONS = 5      # Максимум 5 активті позиция

USE_CLOSED_CANDLE = True      # Тек жабық шаммен тексеру

# Индикаторлар баптаулары
ADX_PERIOD = 14
ADX_MIN = 20

RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

EMA_FAST = 20
EMA_SLOW = 50

VWAP_ENABLED = True
VOLUME_SMA_PERIOD = 20
MIN_VOLUME_RATIO = 1.05

BOS_ENABLED = True
SWING_LOOKBACK = 4

# Score Салмақтары (Жалпы = 100)
WEIGHT_TREND = 20
WEIGHT_MOMENTUM = 20
WEIGHT_VWAP = 15
WEIGHT_ADX_DI = 15
WEIGHT_VOLUME = 10
WEIGHT_BOS = 20

ENTRY_SCORE = 55              # 55+ баллда сауда ашыла береді
STRONG_SCORE = 75

SL_ATR_MULT = 0.7
TP_ATR_MULT = 0.7

# Dynamic Trailing & Time Exit
TRAILING_TRIGGER_PCT = 0.003  # +0.3% пайдада Трейлинг іске қосылады
TRAILING_DISTANCE_PCT = 0.0015 # 0.15% қашықтық
MAX_FLAT_TIME_MIN = 35        # Флэттегі саудаларды 35 минутта жабу

# ==============================================================================
# INITIALIZATION
# ==============================================================================
session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
    demo=IS_DEMO
)

processed_candles = {}
position_entry_times = {}

def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [INFO] {msg}", flush=True)

# ==============================================================================
# MARKET DATA FUNCTIONS
# ==============================================================================
def get_klines(symbol, interval, limit=100):
    try:
        res = session.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        if res['retCode'] != 0:
            return None
        list_data = res['result']['list']
        df = pd.DataFrame(list_data, columns=['start_time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df['start_time'] = pd.to_datetime(pd.to_numeric(df['start_time']), unit='ms')
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        df = df.sort_values('start_time').reset_index(drop=True)
        return df
    except Exception as e:
        log(f"Кэндл алу қатесі ({symbol} {interval}): {e}")
        return None

def calculate_vwap(df):
    tp = (df['high'] + df['low'] + df['close']) / 3
    vwap = (tp * df['volume']).cumsum() / df['volume'].cumsum()
    return vwap

def get_market_analysis(symbol):
    df_1h = get_klines(symbol, "60", limit=100)
    df_15m = get_klines(symbol, "15", limit=100)
    df_5m = get_klines(symbol, "5", limit=100)

    if df_1h is None or df_15m is None or df_5m is None or len(df_5m) < 50:
        return None

    # 1H Trend
    df_1h['ema_fast'] = ta.ema(df_1h['close'], length=EMA_FAST)
    df_1h['ema_slow'] = ta.ema(df_1h['close'], length=EMA_SLOW)
    trend_1h = 'BULLISH' if df_1h['ema_fast'].iloc[-1] > df_1h['ema_slow'].iloc[-1] else 'BEARISH'

    # 15M Trend
    df_15m['ema_fast'] = ta.ema(df_15m['close'], length=EMA_FAST)
    df_15m['ema_slow'] = ta.ema(df_15m['close'], length=EMA_SLOW)
    trend_15m = 'BULLISH' if df_15m['ema_fast'].iloc[-1] > df_15m['ema_slow'].iloc[-1] else 'BEARISH'

    # 5M Indicators
    idx = -2 if USE_CLOSED_CANDLE else -1
    
    df_5m['rsi'] = ta.rsi(df_5m['close'], length=RSI_PERIOD)
    macd = ta.macd(df_5m['close'], fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
    df_5m['macd'] = macd['MACD_12_26_9']
    df_5m['macd_sig'] = macd['MACDs_12_26_9']
    
    adx_df = ta.adx(df_5m['high'], df_5m['low'], df_5m['close'], length=ADX_PERIOD)
    df_5m['adx'] = adx_df[f'ADX_{ADX_PERIOD}']
    df_5m['dipi'] = adx_df[f'DMP_{ADX_PERIOD}']
    df_5m['dimi'] = adx_df[f'DMN_{ADX_PERIOD}']

    df_5m['vwap'] = calculate_vwap(df_5m)
    df_5m['vol_sma'] = ta.sma(df_5m['volume'], length=VOLUME_SMA_PERIOD)
    df_5m['atr'] = ta.atr(df_5m['high'], df_5m['low'], df_5m['close'], length=14)

    # Swing Low/High (BOS)
    df_5m['swing_high'] = df_5m['high'].shift(1).rolling(SWING_LOOKBACK).max()
    df_5m['swing_low'] = df_5m['low'].shift(1).rolling(SWING_LOOKBACK).min()

    curr = df_5m.iloc[idx]
    
    # BOS logic
    bos = "NONE"
    if curr['close'] > curr['swing_high']:
        bos = "BULLISH_BOS"
    elif curr['close'] < curr['swing_low']:
        bos = "BEARISH_BOS"

    return {
        'symbol': symbol,
        'candle_time': df_5m['start_time'].iloc[idx],
        'close': curr['close'],
        'atr': curr['atr'],
        'trend_1h': trend_1h,
        'trend_15m': trend_15m,
        'rsi': curr['rsi'],
        'macd': curr['macd'],
        'macd_sig': curr['macd_sig'],
        'adx': curr['adx'],
        'dipi': curr['dipi'],
        'dimi': curr['dimi'],
        'vwap': curr['vwap'],
        'vol_ratio': curr['volume'] / curr['vol_sma'] if curr['vol_sma'] > 0 else 1.0,
        'bos': bos
    }

def get_btc_trend():
    df = get_klines("BTCUSDT", "15", limit=50)
    if df is None: return "NEUTRAL"
    df['ema_fast'] = ta.ema(df['close'], length=EMA_FAST)
    df['ema_slow'] = ta.ema(df['close'], length=EMA_SLOW)
    if df['ema_fast'].iloc[-1] > df['ema_slow'].iloc[-1]: return "BULLISH"
    elif df['ema_fast'].iloc[-1] < df['ema_slow'].iloc[-1]: return "BEARISH"
    return "NEUTRAL"

# ==============================================================================
# SCORE CALCULATOR
# ==============================================================================
def calculate_scores(data, btc_trend):
    long_score = 0
    short_score = 0

    # 1. Trend (20)
    if data['trend_1h'] == 'BULLISH' and data['trend_15m'] == 'BULLISH': long_score += WEIGHT_TREND
    if data['trend_1h'] == 'BEARISH' and data['trend_15m'] == 'BEARISH': short_score += WEIGHT_TREND

    # 2. Momentum RSI & MACD (20)
    if data['rsi'] > 50 and data['macd'] > data['macd_sig']: long_score += WEIGHT_MOMENTUM
    if data['rsi'] < 50 and data['macd'] < data['macd_sig']: short_score += WEIGHT_MOMENTUM

    # 3. VWAP (15)
    if data['close'] > data['vwap']: long_score += WEIGHT_VWAP
    if data['close'] < data['vwap']: short_score += WEIGHT_VWAP

    # 4. ADX & DI (15)
    if data['adx'] >= ADX_MIN:
        if data['dipi'] > data['dimi']: long_score += WEIGHT_ADX_DI
        elif data['dimi'] > data['dipi']: short_score += WEIGHT_ADX_DI

    # 5. Volume (10)
    if data['vol_ratio'] >= MIN_VOLUME_RATIO:
        long_score += WEIGHT_VOLUME
        short_score += WEIGHT_VOLUME

    # 6. BOS (20)
    if data['bos'] == 'BULLISH_BOS': long_score += WEIGHT_BOS
    if data['bos'] == 'BEARISH_BOS': short_score += WEIGHT_BOS

    # Filter Reasons
    reasons = []
    if data['adx'] < ADX_MIN: reasons.append(f"ADX төмен ({data['adx']:.1f})")
    if data['bos'] == "NONE": reasons.append("BOS жоқ")
    
    signal = "NO TRADE"
    if long_score >= ENTRY_SCORE and data['adx'] >= ADX_MIN and data['bos'] == 'BULLISH_BOS' and btc_trend != "BEARISH":
        signal = "LONG"
    elif short_score >= ENTRY_SCORE and data['adx'] >= ADX_MIN and data['bos'] == 'BEARISH_BOS' and btc_trend != "BULLISH":
        signal = "SHORT"

    return long_score, short_score, signal, ", ".join(reasons) if reasons else "Сүзгілерден өтті"

# ==============================================================================
# TRADE EXECUTION & MANAGEMENT
# ==============================================================================
def get_active_positions():
    try:
        res = session.get_positions(category="linear", settleCoin="USDT")
        if res['retCode'] == 0:
            positions = [p for p in res['result']['list'] if float(p['size']) > 0]
            return positions
    except Exception as e:
        log(f"Позицияларды алу қатесі: {e}")
    return []

def set_leverage(symbol):
    try:
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except:
        pass

def place_order(symbol, side, close_price, atr):
    try:
        set_leverage(symbol)
        bal_res = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        if bal_res['retCode'] != 0: return
        
        balance = float(bal_res['result']['list'][0]['totalMarginBalance'])
        position_size_usdt = balance * RISK_PCT * LEVERAGE
        qty = round(position_size_usdt / close_price, 3)

        if side == "LONG":
            sl = round(close_price - (atr * SL_ATR_MULT), 4)
            tp = round(close_price + (atr * TP_ATR_MULT), 4)
            order_side = "Buy"
        else:
            sl = round(close_price + (atr * SL_ATR_MULT), 4)
            tp = round(close_price - (atr * TP_ATR_MULT), 4)
            order_side = "Sell"

        res = session.place_order(
            category="linear",
            symbol=symbol,
            side=order_side,
            orderType="Market",
            qty=str(qty),
            takeProfit=str(tp),
            stopLoss=str(sl),
            tpTriggerBy="LastPrice",
            slTriggerBy="LastPrice"
        )
        if res['retCode'] == 0:
            log(f"🚀 ОРДЕР АШЫЛДЫ: {symbol} {side} | Көлемі: {qty} | Бағасы: {close_price} | TP: {tp} | SL: {sl}")
            position_entry_times[symbol] = time.time()
        else:
            log(f"❌ Ордер қатесі ({symbol}): {res['retMsg']}")
    except Exception as e:
        log(f"Ордер ашуда қате ({symbol}): {e}")

def manage_positions():
    positions = get_active_positions()
    for pos in positions:
        symbol = pos['symbol']
        side = pos['side']
        entry_price = float(pos['avgPrice'])
        current_price = float(pos['markPrice'])
        qty = pos['size']
        
        # 1. Dynamic Trailing Stop
        pnl_pct = ((current_price - entry_price) / entry_price) if side == "Buy" else ((entry_price - current_price) / entry_price)
        
        if pnl_pct >= TRAILING_TRIGGER_PCT:
            new_sl = current_price * (1 - TRAILING_DISTANCE_PCT) if side == "Buy" else current_price * (1 + TRAILING_DISTANCE_PCT)
            curr_sl = float(pos.get('stopLoss', 0) or 0)
            
            should_update = (side == "Buy" and new_sl > curr_sl) or (side == "Sell" and (curr_sl == 0 or new_sl < curr_sl))
            if should_update:
                session.set_trading_stop(category="linear", symbol=symbol, stopLoss=str(round(new_sl, 4)), slTriggerBy="LastPrice")
                log(f"🎯 Трейлинг-Стоп жаңартылды [{symbol}]: {round(new_sl, 4)}")

        # 2. Time Exit (35 минутта флэттен шығару)
        if symbol in position_entry_times:
            duration_min = (time.time() - position_entry_times[symbol]) / 60
            if duration_min >= MAX_FLAT_TIME_MIN:
                close_side = "Sell" if side == "Buy" else "Buy"
                session.place_order(category="linear", symbol=symbol, side=close_side, orderType="Market", qty=qty, reduceOnly=True)
                log(f"⏱️ Time Exit ({MAX_FLAT_TIME_MIN} мин толды): {symbol} позициясы жабылды.")
                del position_entry_times[symbol]

# ==============================================================================
# MAIN LOOP
# ==============================================================================
def main():
    log("🚀 Бот іске қосылды (Шектеусіз мықты сигналдар режимі)...")
    
    while True:
        try:
            active_positions = get_active_positions()
            active_count = len(active_positions)
            btc_trend = get_btc_trend()

            manage_positions()

            for symbol in SYMBOLS:
                data = get_market_analysis(symbol)
                if data is None: continue

                # Бир шамда екі рет кірмеу
                if processed_candles.get(symbol) == data['candle_time']:
                    continue

                long_score, short_score, signal, reasons = calculate_scores(data, btc_trend)

                # Лог шығару
                log(f"--- [ {symbol} | {data['candle_time']} ] ---")
                log(f"1H: {data['trend_1h']} | 15M: {data['trend_15m']} | BTC: {btc_trend}")
                log(f"ADX: {data['adx']:.1f} | Vol: {data['vol_ratio']:.2f}x | BOS: {data['bos']}")
                log(f"LONG SCORE: {long_score}/100 | SHORT SCORE: {short_score}/100")
                log(f"SIGNAL: {signal} | REASONS: {reasons}")

                if signal != "NO TRADE":
                    if active_count < MAX_ACTIVE_POSITIONS:
                        # Монетада ашық ордер жоқ екенін тексеру
                        if not any(p['symbol'] == symbol for p in active_positions):
                            place_order(symbol, signal, data['close'], data['atr'])
                            processed_candles[symbol] = data['candle_time']
                            active_count += 1
                    else:
                        log(f"⚠️ Белсенді ордерлер лимиті толды ({MAX_ACTIVE_POSITIONS}/{MAX_ACTIVE_POSITIONS}).")

            time.sleep(15)

        except Exception as e:
            log(f"Негізгі циклдегі қате: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
