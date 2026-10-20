import csv
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from pybit.unified_trading import HTTP

# ==============================================================================
# 1. ЛОГТАУ ЖӘНЕ КАЗАЛАУ (LOGGING SETUP)
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot_execution.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

# ==============================================================================
# 2. КОНФИГУРАЦИЯ (CONFIGURATIONS)
# ==============================================================================
# API Кілттерді оқу (Environment Variables арқылы немесе осы жерге кірістіріңіз)
API_KEY = os.getenv("BYBIT_API_KEY", "СІЗДІҢ_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET", "СІЗДІҢ_API_SECRET")

# Баптаулар
IS_DEMO = False  # Тікелей нақты шот (Live) үшін False, Тест шот үшін True
SYMBOL = "BTCUSDT"
CATEGORY = "linear"  # USDT Perps
TIMEFRAME = "5"  # 5 минуттық таймфрейм

LEVERAGE = 10  # 10x Плечо
TOTAL_DEPOSIT = 1000.0  # $1000 USDT
RISK_PER_TRADE_USD = 10.0  # Депозиттің 1%-ы = $10
POSITION_MARGIN_USD = 100.0  # Позицияға бөлінетін сома (Margin)

# Тәуекел пайыздары
STOP_LOSS_PCT = 0.010  # 1.0% Stop Loss
TAKE_PROFIT_PCT = 0.020  # 2.0% Take Profit (Take 1:2 Risk/Reward)

# HTTP Сессия
session = HTTP(testnet=IS_DEMO, api_key=API_KEY, api_secret=API_SECRET)


# ==============================================================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (HELPERS)
# ==============================================================================
def set_leverage_and_mode(symbol, leverage):
  """Левередж бен режимді орнату (Қателерден қорғалған)"""
  try:
    session.set_leverage(
        category=CATEGORY,
        symbol=symbol,
        buyLeverage=str(leverage),
        sellLeverage=str(leverage),
    )
    logging.info(f"Левередж {leverage}x ретінде орнатылды.")
  except Exception as e:
    # Егер бұрын орнатылған болса, қатені елемейміз
    logging.debug(f"Левередж ескертуі: {e}")


def get_position_info(symbol):
  """Ашық позицияның бар-жоғын тексеру"""
  try:
    response = session.get_positions(category=CATEGORY, symbol=symbol)
    if response["retCode"] == 0:
      positions = response["result"]["list"]
      for pos in positions:
        size = float(pos["size"])
        if size > 0:
          return pos  # Ашық позиция бар
    return None
  except Exception as e:
    logging.error(f"Позицияны тексеруде қате: {e}")
    return None


def fetch_klines(symbol, interval, limit=250):
  """Bybit-тен баға деректерін (K-lines) оқу"""
  try:
    res = session.get_kline(
        category=CATEGORY, symbol=symbol, interval=interval, limit=limit
    )
    if res["retCode"] == 0:
      df = pd.DataFrame(
          res["result"]["list"],
          columns=[
              "startTime",
              "open",
              "high",
              "low",
              "close",
              "volume",
              "turnover",
          ],
      )
      df["close"] = df["close"].astype(float)
      df["high"] = df["high"].astype(float)
      df["low"] = df["low"].astype(float)
      df = df.iloc[::-1].reset_index(drop=True)  # Реверс
      return df
    else:
      logging.error(f"K-line қатесі: {res['retMsg']}")
      return None
  except Exception as e:
    logging.error(f"API сурау қатесі: {e}")
    return None


def calculate_indicators(df):
  """Индикаторларды есептеу: EMA 7, 17, 50, 200 & Stochastic"""
  df["ema7"] = df["close"].ewm(span=7, adjust=False).mean()
  df["ema17"] = df["close"].ewm(span=17, adjust=False).mean()
  df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
  df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

  # Стохастик (8, 5, 3)
  low_min = df["low"].rolling(window=8).min()
  high_max = df["high"].rolling(window=8).max()
  stoch_k = 100 * ((df["close"] - low_min) / (high_max - low_min + 1e-9))
  df["stoch_k"] = stoch_k.rolling(window=5).mean()
  df["stoch_d"] = df["stoch_k"].rolling(window=3).mean()

  return df


def execute_order(symbol, side, qty, price):
  """Ордерді Stop-Loss және Take-Profit-пен бірге ашу"""
  try:
    # Баға қадамдарын дұрыстау (Precision)
    if side == "Buy":
      sl_price = round(price * (1.0 - STOP_LOSS_PCT), 1)
      tp_price = round(price * (1.0 + TAKE_PROFIT_PCT), 1)
    else:
      sl_price = round(price * (1.0 + STOP_LOSS_PCT), 1)
      tp_price = round(price * (1.0 - TAKE_PROFIT_PCT), 1)

    order = session.place_order(
        category=CATEGORY,
        symbol=symbol,
        side=side,
        orderType="Market",
        qty=str(qty),
        stopLoss=str(sl_price),
        takeProfit=str(tp_price),
        tpslMode="Full",
    )
    if order["retCode"] == 0:
      logging.info(
          f"✅ [{side.upper()}] Ордер сәтті ашылды! Көлем: {qty} BTC, Баға:"
          f" ~{price}, SL: {sl_price}, TP: {tp_price}"
      )
    else:
      logging.error(f"Ордер ашу орындалмады: {order['retMsg']}")
  except Exception as e:
    logging.error(f"Ордер ашу кезіндегі қателік: {e}")


# ==============================================================================
# 4. НЕГІЗГІ ЛООП (MAIN ENGINE)
# ==============================================================================
def main():
  logging.info("==========================================")
  logging.info("   TRADER DNA SCALP BOT v3.0 ІСКЕ ҚОСЫЛДЫ   ")
  logging.info(f"   Депозит: ${TOTAL_DEPOSIT} | Тәуекел: {STOP_LOSS_PCT*100}%  ")
  logging.info("==========================================")

  set_leverage_and_mode(SYMBOL, LEVERAGE)

  while True:
    try:
      # 1. Алдымен ашық позицияның бар-жоғын тексереміз
      active_position = get_position_info(SYMBOL)
      if active_position:
        side = active_position["side"]
        size = active_position["size"]
        pnl = active_position["unrealisedPnl"]
        logging.info(
            f"⏳ Ашық позиция бар [{side} - {size} BTC]. PnL: {pnl} USDT."
            " Күтеміз..."
        )
        time.sleep(30)
        continue

      # 2. График деректерін алу
      df = fetch_klines(SYMBOL, TIMEFRAME)
      if df is None or len(df) < 200:
        logging.warning("Деректер жеткіліксіз. 15 секунд күту...")
        time.sleep(15)
        continue

      # 3. Индикаторларды есептеу
      df = calculate_indicators(df)

      last_close = df["close"].iloc[-1]
      ema7 = df["ema7"].iloc[-1]
      ema17 = df["ema17"].iloc[-1]
      ema200 = df["ema200"].iloc[-1]

      prev_ema7 = df["ema7"].iloc[-2]
      prev_ema17 = df["ema17"].iloc[-2]

      stoch_k = df["stoch_k"].iloc[-1]
      stoch_d = df["stoch_d"].iloc[-1]
      prev_stoch_k = df["stoch_k"].iloc[-2]
      prev_stoch_d = df["stoch_d"].iloc[-2]

      # 4. Стратегия шартын тексеру
      # Тренд: Егер баға EMA 200-ден жоғары болса — ТЕК BUY, төмен болса — ТЕК SELL
      is_uptrend = last_close > ema200
      is_downtrend = last_close < ema200

      # EMA7 және EMA17 Қиылысуы
      ema_buy_cross = (prev_ema7 <= prev_ema17) and (ema7 > ema17)
      ema_sell_cross = (prev_ema7 >= prev_ema17) and (ema7 < ema17)

      # Стохастик растауы
      stoch_buy_confirm = (prev_stoch_k <= prev_stoch_d) and (
          stoch_k > stoch_d
      )
      stoch_sell_confirm = (prev_stoch_k >= prev_stoch_d) and (
          stoch_k < stoch_d
      )

      # 5. Позиция көлемін есептеу (BTC мөлшері)
      position_value_usd = POSITION_MARGIN_USD * LEVERAGE  # $100 * 10 = $1000
      qty = round(position_value_usd / last_close, 3)  # Тоқтаусыз BTC санын есептеу

      # 6. Сигналдарды өңдеу
      if is_uptrend and ema_buy_cross and stoch_buy_confirm:
        logging.info(
            f"🟢 BUY Сигналы тіркелді! Баға: {last_close}, EMA200 үстінде."
        )
        execute_order(SYMBOL, "Buy", qty, last_close)
        time.sleep(300)  # Келесі свечаға дейін кідірту (5 мин)

      elif is_downtrend and ema_sell_cross and stoch_sell_confirm:
        logging.info(
            f"🔴 SELL Сигналы тіркелді! Баға: {last_close}, EMA200 астында."
        )
        execute_order(SYMBOL, "Sell", qty, last_close)
        time.sleep(300)

      else:
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
        logging.info(
            f"[{now_str}] Нарық тыныш. Баға: ${last_close} | EMA200:"
            f" ${round(ema200, 1)}"
        )

      # Әр 30 секунд сайын цикл қайталанады
      time.sleep(30)

    except KeyboardInterrupt:
      logging.info("🛑 Бот пайдаланушы тарапынан тоқтатылды.")
      break
    except Exception as e:
      logging.error(f"Негізгі циклдегі күтпеген қате: {e}")
      time.sleep(10)


if __name__ == "__main__":
  main()
