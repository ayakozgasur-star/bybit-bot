import os
import time
import logging
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ==============================================================================
# LOGGING CONFIGURATION
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# ==============================================================================
# CONFIG / PARAMETERS
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True

SYMBOLS = ["SOLUSDT", "XRPUSDT", "1000PEPEUSDT", "NEARUSDT", "AVAXUSDT"]
LEVERAGE = 10
RISK_PCT = 0.10             # 10% Маржа
MAX_ACTIVE_POSITIONS = 2     # Максимум 2 белсенді позиция

USE_CLOSED_CANDLE = True     # Сигналдарды тек жабық шаммен (iloc[-2]) тексеру

# Индикаторлар баптауы
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
MIN_VOLUME_RATIO = 1.20

BOS_ENABLED = True
SWING_LOOKBACK = 5

# Score Салмақтары (Жалпы = 100)
WEIGHT_TREND = 20
WEIGHT_MOMENTUM = 20
WEIGHT_VWAP = 15
WEIGHT_ADX_DI = 15
WEIGHT_VOLUME = 10
WEIGHT_BOS = 20

ENTRY_SCORE = 70
STRONG_SCORE = 80

SL_ATR_MULT = 0.7
TP_ATR_MULT = 0.7

# Dynamic Trailing & Time Exit Баптаулары
TRAILING_TRIGGER_PCT = 0.003  # +0.3% пайда болғанда Трейлинг іске қосылады
TRAILING_DISTANCE_PCT = 0.0015 # Ағымдағы бағадан 0.15% артта жүреді
MAX_FLAT_TIME_MIN = 45        # 45 мин бойы қозғалыс болмаса позиция жабылады

# ==============================================================================
# BYBIT API SESSION
# ==============================================================================
session = HTTP(
    demo=IS_DEMO,
    api_key=API_KEY,
    api_secret=API_SECRET
)

processed_candles = {}

# ==============================================================================
# TECHNICAL INDICATORS
# ==============================================================================
def calculate_ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()

def calculate_rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))

def calculate_atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    high_low = df['high'] - df['low']
    high_close = abs(df['high'] - df['close'].shift(1))
    low_close = abs(df['low'] - df['close'].shift(1))
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window).mean()

def calculate_macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = calculate_ema(series, fast)
    ema_slow = calculate_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """Түзетілген, Series қайтаратын VWAP функциясы"""
    df_temp = df.copy()
    tp = (df_temp['high'] + df_temp['low'] + df_temp['close']) / 3.0
    pv = tp * df_temp['volume']
    
    dates = pd.to_datetime(df_temp['time'], unit='ms').dt.date
    cum_pv = pv.groupby(dates).cumsum()
    cum_vol = df_temp['volume'].groupby(dates).cumsum()
    
    return cum_pv / (cum_vol + 1e-10)

def calculate_adx_di(df: pd.DataFrame, window: int = 14):
    df_temp = df.copy()
    up_move = df_temp['high'] - df_temp['high'].shift(1)
    down_move = df_temp['low'].shift(1) - df_temp['low']
    
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    tr = calculate_atr(df_temp, window=1)
    tr_smooth = tr.rolling(window).sum()
    
    plus_di = 100 * (pd.Series(plus_dm, index=df_temp.index).rolling(window).sum() / (tr_smooth + 1e-10))
    minus_di = 100 * (pd.Series(minus_dm, index=df_temp.index).rolling(window).sum() / (tr_smooth + 1e-10))
    
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10))
    adx = dx.rolling(window).mean()
    return adx, plus_di, minus_di

def calculate_volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    vol_sma = df['volume'].rolling(period).mean()
    return df['volume'] / (vol_sma + 1e-10)

def calculate_bos_and_structure(df: pd.DataFrame, lookback: int = 5):
    df_temp = df.copy()
    df_temp['swing_high'] = df_temp['high'].shift(1).rolling(window=lookback).max()
    df_temp['swing_low'] = df_temp['low'].shift(1).rolling(window=lookback).min()
    
    bullish_bos = df_temp['close'] > df_temp['swing_high']
    bearish_bos = df_temp['close'] < df_temp['swing_low']
    
    hh_hl = (df_temp['high'] > df_temp['high'].shift(lookback)) & (df_temp['low'] > df_temp['low'].shift(lookback))
    lh_ll = (df_temp['high'] < df_temp['high'].shift(lookback)) & (df_temp['low'] < df_temp['low'].shift(lookback))
    return bullish_bos, bearish_bos, hh_hl, lh_ll

# ==============================================================================
# DATA FETCHING
# ==============================================================================
def fetch_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    try:
        res = session.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        if res.get('retCode') != 0:
            return None
        df = pd.DataFrame(res['result']['list'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df = df.iloc[::-1].reset_index(drop=True)
        df['time'] = df['time'].astype(int)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df
    except Exception as e:
        logging.error(f"[{symbol}] Klines қатесі: {e}")
        return None

def get_account_balance() -> float:
    try:
        res = session.get_wallet_balance(accountType="UNIFIED")
        if res.get('retCode') == 0:
            return float(res['result']['list'][0]['totalEquity'])
        return 1000.0
    except Exception:
        return 1000.0

def get_active_positions():
    try:
        res = session.get_positions(category="linear", settleCoin="USDT")
        if res.get('retCode') == 0:
            return [pos for pos in res['result']['list'] if float(pos['size']) > 0]
        return []
    except Exception:
        return []

# ==============================================================================
# SCORE CALCULATORS
# ==============================================================================
def calculate_trend_score(c_5m, c_15m, c_1h) -> tuple:
    l_score, s_score = 0, 0
    if c_1h['close'] > c_1h['ema50']: l_score += 8
    else: s_score += 8
    if c_15m['close'] > c_15m['ema200']: l_score += 7
    else: s_score += 7
    if c_5m['close'] > c_5m['ema20']: l_score += 5
    else: s_score += 5
    return l_score, s_score

def calculate_momentum_score(c_5m, prev_5m) -> tuple:
    l_score, s_score = 0, 0
    if 52 <= c_5m['rsi'] <= 70: l_score += 10
    elif 30 <= c_5m['rsi'] <= 48: s_score += 10
    if c_5m['macd_hist'] > 0 and c_5m['macd_hist'] > prev_5m['macd_hist']: l_score += 10
    elif c_5m['macd_hist'] < 0 and c_5m['macd_hist'] < prev_5m['macd_hist']: s_score += 10
    return l_score, s_score

def calculate_vwap_score(c_5m) -> tuple:
    if c_5m['close'] > c_5m['vwap']: return WEIGHT_VWAP, 0
    elif c_5m['close'] < c_5m['vwap']: return 0, WEIGHT_VWAP
    return 0, 0

def calculate_adx_di_score(c_5m) -> tuple:
    if c_5m['adx'] < ADX_MIN: return 0, 0
    l_score, s_score = 7, 7
    if c_5m['plus_di'] > c_5m['minus_di']: l_score += 8
    elif c_5m['minus_di'] > c_5m['plus_di']: s_score += 8
    return l_score, s_score

def calculate_volume_score(c_5m) -> tuple:
    if c_5m['vol_ratio'] >= MIN_VOLUME_RATIO: return WEIGHT_VOLUME, WEIGHT_VOLUME
    elif c_5m['vol_ratio'] >= 1.0: return WEIGHT_VOLUME // 2, WEIGHT_VOLUME // 2
    return 0, 0

def calculate_bos_score(c_5m) -> tuple:
    l_score, s_score = 0, 0
    if c_5m['bullish_bos']: l_score += 12
    if c_5m['bearish_bos']: s_score += 12
    if c_5m['hh_hl']: l_score += 8
    if c_5m['lh_ll']: s_score += 8
    return l_score, s_score

# ==============================================================================
# ANALYZER
# ==============================================================================
def analyze_market(symbol: str):
    df_1h = fetch_klines(symbol, "60", 100)
    df_15m = fetch_klines(symbol, "15", 200)
    df_5m = fetch_klines(symbol, "5", 200)
    df_btc = fetch_klines("BTCUSDT", "15", 50)

    if any(df is None or len(df) < 50 for df in [df_1h, df_15m, df_5m, df_btc]):
        return None, "Data Error"

    df_1h['ema50'] = calculate_ema(df_1h['close'], EMA_SLOW)
    df_15m['ema200'] = calculate_ema(df_15m['close'], 200)
    df_btc['ema20'] = calculate_ema(df_btc['close'], EMA_FAST)

    df_5m['ema20'] = calculate_ema(df_5m['close'], EMA_FAST)
    df_5m['rsi'] = calculate_rsi(df_5m['close'], RSI_PERIOD)
    _, _, df_5m['macd_hist'] = calculate_macd(df_5m['close'], MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    df_5m['vwap'] = calculate_vwap(df_5m)
    df_5m['adx'], df_5m['plus_di'], df_5m['minus_di'] = calculate_adx_di(df_5m, ADX_PERIOD)
    df_5m['vol_ratio'] = calculate_volume_ratio(df_5m, VOLUME_SMA_PERIOD)
    df_5m['atr'] = calculate_atr(df_5m, 14)
    
    bull_bos, bear_bos, hh_hl, lh_ll = calculate_bos_and_structure(df_5m, SWING_LOOKBACK)
    df_5m['bullish_bos'], df_5m['bearish_bos'] = bull_bos, bear_bos
    df_5m['hh_hl'], df_5m['lh_ll'] = hh_hl, lh_ll

    idx = -2 if USE_CLOSED_CANDLE else -1
    c_5m, prev_5m = df_5m.iloc[idx], df_5m.iloc[idx - 1]
    c_15m, c_1h, c_btc = df_15m.iloc[-1], df_1h.iloc[-1], df_btc.iloc[-1]

    tr_l, tr_s = calculate_trend_score(c_5m, c_15m, c_1h)
    mom_l, mom_s = calculate_momentum_score(c_5m, prev_5m)
    vw_l, vw_s = calculate_vwap_score(c_5m)
    adx_l, adx_s = calculate_adx_di_score(c_5m)
    vol_l, vol_s = calculate_volume_score(c_5m)
    bos_l, bos_s = calculate_bos_score(c_5m)

    long_score = tr_l + mom_l + vw_l + adx_l + vol_l + bos_l
    short_score = tr_s + mom_s + vw_s + adx_s + vol_s + bos_s

    btc_bull = c_btc['close'] > c_btc['ema20']
    btc_bear = c_btc['close'] < c_btc['ema20']

    long_hard_filters = (
        (c_1h['close'] > c_1h['ema50']) and (c_15m['close'] > c_15m['ema200']) and
        btc_bull and (c_5m['adx'] >= ADX_MIN) and (c_5m['close'] > c_5m['vwap'])
    )

    short_hard_filters = (
        (c_1h['close'] < c_1h['ema50']) and (c_15m['close'] < c_15m['ema200']) and
        btc_bear and (c_5m['adx'] >= ADX_MIN) and (c_5m['close'] < c_5m['vwap'])
    )

    reasons = []
    if c_5m['adx'] < ADX_MIN: reasons.append(f"ADX төмен ({c_5m['adx']:.1f})")
    if not (btc_bull or btc_bear): reasons.append("BTC тренді нейтралды")
    if not c_5m['bullish_bos'] and not c_5m['bearish_bos']: reasons.append("BOS жоқ")
    if c_5m['vol_ratio'] < MIN_VOLUME_RATIO: reasons.append(f"Көлем аз ({c_5m['vol_ratio']:.2f}x)")

    analysis_result = {
        "symbol": symbol, "candle_time": int(c_5m['time']),
        "1h_trend": "BULLISH" if c_1h['close'] > c_1h['ema50'] else "BEARISH",
        "15m_trend": "BULLISH" if c_15m['close'] > c_15m['ema200'] else "BEARISH",
        "btc_trend": "BULLISH" if btc_bull else "BEARISH",
        "adx": c_5m['adx'], "plus_di": c_5m['plus_di'], "minus_di": c_5m['minus_di'],
        "rsi": c_5m['rsi'], "vwap_status": "ABOVE" if c_5m['close'] > c_5m['vwap'] else "BELOW",
        "vol_ratio": c_5m['vol_ratio'],
        "structure": "HH/HL" if c_5m['hh_hl'] else ("LH/LL" if c_5m['lh_ll'] else "RANGE"),
        "bos": "BULLISH" if c_5m['bullish_bos'] else ("BEARISH" if c_5m['bearish_bos'] else "NONE"),
        "long_score": long_score, "short_score": short_score,
        "scores_detail": {
            "long": {"trend": tr_l, "mom": mom_l, "vwap": vw_l, "adx": adx_l, "vol": vol_l, "bos": bos_l},
            "short": {"trend": tr_s, "mom": mom_s, "vwap": vw_s, "adx": adx_s, "vol": vol_s, "bos": bos_s}
        },
        "atr": c_5m['atr'], "close_price": c_5m['close'], "reasons": reasons
    }

    if long_hard_filters and long_score >= ENTRY_SCORE and c_5m['bullish_bos'] and c_5m['vol_ratio'] >= MIN_VOLUME_RATIO:
        analysis_result["signal"] = "STRONG LONG" if long_score >= STRONG_SCORE else "LONG"
        analysis_result["action"] = "BUY"
    elif short_hard_filters and short_score >= ENTRY_SCORE and c_5m['bearish_bos'] and c_5m['vol_ratio'] >= MIN_VOLUME_RATIO:
        analysis_result["signal"] = "STRONG SHORT" if short_score >= STRONG_SCORE else "SHORT"
        analysis_result["action"] = "SELL"
    else:
        analysis_result["signal"] = "NO TRADE"
        analysis_result["action"] = "NONE"

    return analysis_result, None

def print_signal_log(res: dict):
    s = res["symbol"]
    dt = pd.to_datetime(res["candle_time"], unit='ms')
    print(f"\n================================ [ {s} | {dt} ] ================================")
    print(f"1H Trend: {res['1h_trend']} | 15M Trend: {res['15m_trend']} | BTC Trend: {res['btc_trend']}")
    print(f"5M: VWAP: {res['vwap_status']} | ADX: {res['adx']:.1f} (+DI: {res['plus_di']:.1f}, -DI: {res['minus_di']:.1f}) | RSI: {res['rsi']:.1f} | Vol: {res['vol_ratio']:.2f}x")
    print(f"Structure: {res['structure']} | BOS: {res['bos']}")
    ld, sd = res["scores_detail"]["long"], res["scores_detail"]["short"]
    print(f"LONG SCORE: {res['long_score']}/100 [Trd:{ld['trend']} Mom:{ld['mom']} VWAP:{ld['vwap']} ADX:{ld['adx']} Vol:{ld['vol']} BOS:{ld['bos']}]")
    print(f"SHORT SCORE: {res['short_score']}/100 [Trd:{sd['trend']} Mom:{sd['mom']} VWAP:{sd['vwap']} ADX:{sd['adx']} Vol:{sd['vol']} BOS:{sd['bos']}]")
    print(f"SIGNAL: {res['signal']} | ACTION: {res['action']}")
    if res['action'] == "NONE" and res['reasons']:
        print(f"REASONS: {', '.join(res['reasons'])}")
    print("====================================================================================\n")

# ==============================================================================
# POSITION EXECUTION & ADVANCED TRAILING MANAGEMENT
# ==============================================================================
def get_symbol_precision(symbol: str, price: float) -> float:
    margin_usd = get_account_balance() * RISK_PCT
    position_usd = margin_usd * LEVERAGE
    raw_qty = position_usd / price

    if symbol in ["SOLUSDT", "AVAXUSDT", "NEARUSDT"]: return round(raw_qty, 1)
    elif symbol == "XRPUSDT": return round(raw_qty, 0)
    elif symbol == "1000PEPEUSDT": return int(raw_qty)
    return round(raw_qty, 2)

def set_leverage_and_mode(symbol: str):
    try:
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except Exception: pass
    try:
        session.switch_position_mode(category="linear", symbol=symbol, mode=3)
    except Exception: pass

def manage_advanced_trailing_and_exit():
    try:
        positions = get_active_positions()
        for pos in positions:
            size = float(pos['size'])
            if size <= 0: continue

            symbol = pos['symbol']
            side = pos['side']
            entry_price = float(pos['avgPrice'])
            current_price = float(pos['markPrice'])
            current_sl = float(pos['stopLoss']) if pos['stopLoss'] else 0.0
            updated_time = int(pos['updatedTime']) / 1000.0

            if side == "Buy":
                profit_pct = (current_price - entry_price) / entry_price
                if profit_pct >= TRAILING_TRIGGER_PCT:
                    new_sl = round(current_price * (1.0 - TRAILING_DISTANCE_PCT), 6)
                    if new_sl > current_sl:
                        session.set_trading_stop(category="linear", symbol=symbol, positionIdx=1, stopLoss=str(new_sl))
                        logging.info(f"🛡️ [{symbol}] BUY Dynamic Trailing SL: {new_sl}")

            elif side == "Sell":
                profit_pct = (entry_price - current_price) / entry_price
                if profit_pct >= TRAILING_TRIGGER_PCT:
                    new_sl = round(current_price * (1.0 + TRAILING_DISTANCE_PCT), 6)
                    if current_sl == 0 or new_sl < current_sl:
                        session.set_trading_stop(category="linear", symbol=symbol, positionIdx=2, stopLoss=str(new_sl))
                        logging.info(f"🛡️ [{symbol}] SELL Dynamic Trailing SL: {new_sl}")

            time_in_trade_min = (time.time() - updated_time) / 60.0
            price_change_pct = abs((current_price - entry_price) / entry_price)

            if time_in_trade_min >= MAX_FLAT_TIME_MIN and price_change_pct < 0.0015:
                pos_idx = 1 if side == "Buy" else 2
                close_side = "Sell" if side == "Buy" else "Buy"
                session.place_order(
                    category="linear", symbol=symbol, side=close_side,
                    orderType="Market", qty=str(size), positionIdx=pos_idx, reduceOnly=True
                )
                logging.info(f"⏱️ [{symbol}] TIME EXIT: {MAX_FLAT_TIME_MIN} мин бойы флэт болған соң жабылды.")

    except Exception as e:
        logging.error(f"Позиция басқару қатесі: {e}")

def open_position(symbol: str, side: str, atr: float, price: float):
    active_positions = get_active_positions()
    
    if len(active_positions) >= MAX_ACTIVE_POSITIONS:
        logging.info(f"⚠️ [{symbol}] Лимит толып тұр (Макс {MAX_ACTIVE_POSITIONS} ордер). Өткізіп жіберілді.")
        return

    for pos in active_positions:
        if pos['symbol'] == symbol:
            logging.info(f"⚠️ [{symbol}] Позиция ашық тұр.")
            return

    set_leverage_and_mode(symbol)
    qty = get_symbol_precision(symbol, price)
    if qty <= 0: return

    pos_idx = 1 if side == "BUY" else 2
    sl_distance = atr * SL_ATR_MULT
    tp_distance = atr * TP_ATR_MULT

    if side == "BUY":
        sl_price = round(price - sl_distance, 6)
        tp_price = round(price + tp_distance, 6)
    else:
        sl_price = round(price + sl_distance, 6)
        tp_price = round(price - tp_distance, 6)

    try:
        res = session.place_order(
            category="linear", symbol=symbol, side=side, orderType="Market",
            qty=str(qty), positionIdx=pos_idx, stopLoss=str(sl_price), takeProfit=str(tp_price), timeInForce="GTC"
        )
        if res.get('retCode') == 0:
            logging.info(f"🔥 [ОДЕР АШЫЛДЫ] [{symbol}] {side} | Qty: {qty} | SL: {sl_price} | TP: {tp_price}")
    except Exception as e:
        logging.error(f"[{symbol}] Ордер ашу қатесі: {e}")

# ==============================================================================
# MAIN BOT LOOP
# ==============================================================================
def run_bot():
    logging.info("🚀 Бот іске қосылды Multi-Factor + Dynamic Trailing...")
    while True:
        try:
            manage_advanced_trailing_and_exit()

            for symbol in SYMBOLS:
                res, err = analyze_market(symbol)
                if err or res is None: continue

                print_signal_log(res)

                c_time = res["candle_time"]
                if processed_candles.get(symbol) == c_time:
                    continue

                action = res["action"]
                if action in ["BUY", "SELL"]:
                    open_position(symbol, side=action, atr=res["atr"], price=res["close_price"])
                    processed_candles[symbol] = c_time

        except Exception as e:
            logging.error(f"Бас цикл қателігі: {e}")

        time.sleep(150)

if __name__ == "__main__":
    run_bot()
