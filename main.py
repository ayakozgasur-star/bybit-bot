import time
import hmac
import hashlib
import requests
import numpy as np
import pandas as pd

# --- БОТ ҚОСЫМШАСЫНЫҢ ПАРАМЕТРЛЕРІ ---
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"
SYMBOL = "XRPUSDT"
LEVERAGE = 3
QTY_PER_GRID = 250
ROI_TARGET = 0.02  # Жаңартылған 2% ROI мақсаты

# Bybit Demo / Testnet Endpoint
BASE_URL = "https://api-testnet.bybit.com"


def get_market_data(symbol):
  url = f"{BASE_URL}/v5/market/kline"
  params = {"category": "linear", "symbol": symbol, "interval": "15", "limit": 50}
  response = requests.get(url, params=params)
  data = response.json()
  if data["retCode"] == 0:
    df = pd.DataFrame(
        data["result"]["list"],
        columns=[
            "start_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
        ],
    )
    df["close"] = df["close"].astype(float)
    return df[::-1].reset_index(drop=True)
  return None


def calculate_indicators(df):
  # EMA 20 есептеу
  df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()

  # RSI 14 есептеу
  delta = df["close"].diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
  rs = gain / loss
  df["RSI"] = 100 - (100 / (1 + rs))
  return df


def trading_bot_loop():
  print(
      f"Бот іске қосылды! Символ: {SYMBOL}, ROI мақсаты: {ROI_TARGET*100}%, Иық:"
      f" {LEVERAGE}x"
  )

  while True:
    try:
      df = get_market_data(SYMBOL)
      if df is not None and not df.empty:
        df = calculate_indicators(df)
        current_rsi = df["RSI"].iloc[-1]
        current_price = df["close"].iloc[-1]
        current_ema = df["EMA20"].iloc[-1]

        print(
            f"[DEMO] {SYMBOL}: Баға: ${current_price:.4f} | EMA20:"
            f" ${current_ema:.4f} | RSI: {current_rsi:.2f}"
        )

        # Қауіпсіздік фильтрі: RSI шектен тыс аймақта болмауын тексеру (30 мен 70 аралығы)
        if 30 <= current_rsi <= 70:
          if current_price > current_ema:
            print("Сигнал: Лонг шарттары орындалуда...")
            # Лонг ордерін ашу логикасы
          elif current_price < current_ema:
            print("Сигнал: Шорт шарттары орындалуда...")
            # Шорт ордерін ашу логикасы
        else:
          print(f"⚠️ RSI шектен тыс деңгейде ({current_rsi:.2f}). Күту режимі.")

      time.sleep(20)

    except Exception as e:
      print(f"Қате орын алды: {e}")
      time.sleep(10)


if __name__ == "__main__":
  trading_bot_loop()
