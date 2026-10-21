import logging
import os
import sys
import time
from datetime import datetime, timezone
import pandas as pd
from pybit.unified_trading import HTTP

# ==============================================================================
# 1. ЛОГТАУ СИСТЕМАСЫ (LOGGING SETUP)
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
# 2. КОНФИГУРАЦИЯ ЖӘНЕ БАПТАУЛАР (CONFIGURATIONS)
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "СІЗДІҢ_BYBIT_DEMO_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET", "СІЗДІҢ_BYBIT_DEMO_API_SECRET")

SYMBOL = "BTCUSDT"
CATEGORY = "linear"  # USDT Futures
TIMEFRAME_ENTRY = "5"  # Кіру таймфреймі: 5 минуттық
TIMEFRAME_TREND = "60"  # Трендті растау таймфреймі: 1 сағаттық

LEVERAGE = 10  # 10x Плечо
TOTAL_DEPOSIT = 1000.0  # $1000 USDT Депозит
POSITION_MARGIN_USD = 200.0  # $200 маржа -> 2% Тәуекел ($20)

# Тәуекел мен Пайда деңгейлері (R:R 1:2)
STOP_LOSS_PCT = 0.010  # 1.0% Stop-Loss
TAKE_PROFIT_PCT = 0.020  # 2.0% Take-Profit

# Bybit Demo Trading HTTP Сессиясы
session = HTTP(demo=True, api_key=API_KEY, api_secret=API_SECRET)


# ==============================================================================
# 3. КӨМЕКШІ ФУНКЦИЯЛАР (HELPERS)
# ==============================================================================
def set_leverage(symbol, leverage):
  """Левереджді орнату"""
  try:
    session.set_leverage(
        category=CATEGORY,
        symbol=symbol,
        buyLeverage=str(leverage),
        sellLeverage=str(leverage),
    )
    logging.info(f"Левередж {leverage}x сәтті орнатылды.")
  except Exception as e:
    logging.debug(f"Левередж ескертуі: {e}")


def get_position_info(symbol):
  """Ашық позицияның бар-жоғын тексеру"""
  try:
    response = session.get_positions(category=CATEGORY, symbol=symbol)
    if response["retCode"] == 0:
      for pos in response["result"]["list"]:
        if float(pos["size"]) > 0:
          return pos
    return None
  except Exception as e:
    logging.error(f"Позицияны тексеруде қате: {e}")
    return None


def fetch_klines(symbol, interval, limit=250):
  """Bybit-тен свеча деректерін алу"""
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
      df["volume"] = df["volume"].astype(float)
      df = df.iloc[::-1].reset_index(drop=True)
      return df
    return None
  except Exception as e:
    logging.error(f"K-line қатесі: {e}")
    return None


def calculate_indicators(df):
  """Индикаторларды есептеу: 20 EMA, Stochastic (8, 5, 3) және Volume MA"""
  df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

  # Стохастик (8, 5, 3)
  low_min = df["low"].rolling(window=8).min()
  high_max = df["high"].rolling(window=8).max()
  stoch_k = 100 * ((df["close"] - low_min) / (high_max - low_min + 1e-9))
  df["stoch_k"] = stoch_k.rolling(window=5).mean()
  df["stoch_d"] = df["stoch_k"].rolling(window=3).mean()

  # Көлем орташа мәні (Volume Moving Average) импульсті тексеру үшін
  df["vol_ma"] = df["volume"].rolling(window=20).mean()

  return df


def get_higher_timeframe_trend(symbol):
  """1 сағаттық (1H) үлкен таймфрейм арқылы трендті анықтау"""
  df_1h = fetch_klines(symbol, TIMEFRAME_TREND, limit=50)
  if df_1h is not None and len(df_1h) >= 20:
    df_1h["ema50"] = df_1h["close"].ewm(span=50, adjust=False).mean()
    last_close = df_1h["close"].iloc[-1]
    ema50_1h = df_1h["ema50"].iloc[-1]
    # Егер баға 1H EMA50-ден жоғары болса — Bullish, төмен болса — Bearish
    return "BUY" if last_close > ema50_1h else "SELL"
  return "NEUTRAL"


def execute_order(symbol, side, qty, price):
  """Ордерді Stop-Loss және Take-Profit-пен бірге ашу"""
  try:
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
          f"✅ Сапалы [{side.upper()}] Ордер ашылды! Көлем: {qty} BTC, Баға:"
          f" ~${price}, SL: ${sl_price}, TP: ${tp_price}"
      )
    else:
      logging.error(f"Ордер қатесі: {order['retMsg']}")
  except Exception as e:
    logging.error(f"Ордер жіберудегі қате: {e}")


# ==============================================================================
# 4. НЕГІЗГІ ЛООП (MAIN LOOP)
# ==============================================================================
def main():
  logging.info("==================================================")
  logging.info("   TRADER DNA SMART SCALP BOT (MULTI-TIMEFRAME)   ")
  logging.info(
      f"   Депозит: ${TOTAL_DEPOSIT} | Тәуекел: 2.0% ($20 USDT)      "
  )
  logging.info("==================================================")

  set_leverage(SYMBOL, LEVERAGE)

  while True:
    try:
      # 1. Ашық позиция бар-жоғын тексеру
      active_position = get_position_info(SYMBOL)
      if active_position:
        logging.info(
            f"⏳ Ашық позиция бар [{active_position['side']}]."
            f" PnL: {active_position['unrealisedPnl']} USDT. Күтеміз..."
        )
        time.sleep(30)
        continue

      # 2. 5 минуттық кіру деректерін алу
      df = fetch_klines(SYMBOL, TIMEFRAME_ENTRY, limit=50)
      if df is None or len(df) < 30:
        logging.warning("Деректер жеткіліксіз. 15 секунд күтеміз...")
        time.sleep(15)
        continue

      # 3. Индикаторлар
      df = calculate_indicators(df)

      last_close = df["close"].iloc[-1]
      prev_close = df["close"].iloc[-2]
      ema20 = df["ema20"].iloc[-1]

      stoch_k = df["stoch_k"].iloc[-1]
      stoch_d = df["stoch_d"].iloc[-1]
      prev_stoch_k = df["stoch_k"].iloc[-2]
      prev_stoch_d = df["stoch_d"].iloc[-2]

      current_volume = df["volume"].iloc[-1]
      avg_volume = df["vol_ma"].iloc[-1]

      # 4. Мульти-таймфреймдік трендті алу (1 сағаттық растау)
      higher_trend = get_higher_timeframe_trend(SYMBOL)

      # 5. Сапалы сигнал шарттары (EMA 20 + Stochastic + Volume + 1H Trend)
      # Көлем орташа мәннен жоғары болуы тиіс (импульсті растау үшін)
      is_volume_strong = current_volume > avg_volume

      buy_condition = (
          (higher_trend == "BUY")
          and (prev_close < ema20)
          and (last_close > ema20)
          and (prev_stoch_k <= prev_stoch_d)
          and (stoch_k > stoch_d)
          and is_volume_strong
      )

      sell_condition = (
          (higher_trend == "SELL")
          and (prev_close > ema20)
          and (last_close < ema20)
          and (prev_stoch_k >= prev_stoch_d)
          and (stoch_k < stoch_d)
          and is_volume_strong
      )

      # 6. Позиция көлемі (BTC)
      position_value_usd = POSITION_MARGIN_USD * LEVERAGE
      qty = round(position_value_usd / last_close, 3)

      # 7. Сигналды орындау
      if buy_condition:
        logging.info(
            f"🟢 ЖОҒАРЫ САПАЛЫ BUY СИГНАЛЫ! 1H Тренд Өсуде, Көлем жоғары."
            f" Баға: ${last_close}"
        )
        execute_order(SYMBOL, "Buy", qty, last_close)
        time.sleep(300)

      elif sell_condition:
        logging.info(
            f"🔴 ЖОҒАРЫ САПАЛЫ SELL СИГНАЛЫ! 1H Тренд Төмендеуде, Көлем жоғары."
            f" Баға: ${last_close}"
        )
        execute_order(SYMBOL, "Sell", qty, last_close)
        time.sleep(300)

      else:
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
        logging.info(
            f"[{now_str}] Нарық сүзгіден өтуде... 1H Тренд: {higher_trend} |"
            f" Баға: ${last_close}"
        )

      time.sleep(30)

    except KeyboardInterrupt:
      logging.info("🛑 Бот тоқтатылды.")
      break
    except Exception as e:
      logging.error(f"Қате орын алды: {e}")
      time.sleep(10)


if __name__ == "__main__":
  main()
