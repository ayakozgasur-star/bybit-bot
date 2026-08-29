import hashlib
import hmac
import json
import os
import time
import numpy as np
import pandas as pd
import requests

# --- БОТ ҚОСЫМШАСЫНЫҢ ПАРАМЕТРЛЕРІ ---
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
SYMBOL = "XRPUSDT"
LEVERAGE = 3
QTY_PER_GRID = 250
ROI_TARGET = 0.02  # 2% ROI мақсаты

# Bybit Demo Trading Endpoint
BASE_URL = "https://api-demo.bybit.com"


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
  df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()
  delta = df["close"].diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
  rs = gain / loss
  df["RSI"] = 100 - (100 / (1 + rs))
  return df


def place_order(side, qty):
  path = "/v5/order/create"
  url = BASE_URL + path
  timestamp = str(int(time.time() * 1000))
  recv_window = "5000"

  payload = {
      "category": "linear",
      "symbol": SYMBOL,
      "side": side,  # "Buy" (Лонг) немесе "Sell" (Шорт)
      "orderType": "Market",
      "qty": str(qty),
      "timeInForce": "GoodTillCancel",
  }
  body_str = json.dumps(payload)

  # Bybit V5 қауіпсіздік қолтаңбасы (Signature)
  signature_payload = timestamp + API_KEY + recv_window + body_str
  signature = hmac.new(
      API_SECRET.encode("utf-8"),
      signature_payload.encode("utf-8"),
      hashlib.sha256,
  ).hexdigest()

  headers = {
      "X-BAPI-API-KEY": API_KEY,
      "X-BAPI-SIGN": signature,
      "X-BAPI-TIMESTAMP": timestamp,
      "X-BAPI-RECV-WINDOW": recv_window,
      "Content-Type": "application/json; charset=utf-8",
  }

  response = requests.post(url, data=body_str, headers=headers)
  print("Биржа жауабы (Order Response):", response.json())


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

        if 30 <= current_rsi <= 70:
          if current_price > current_ema:
            print("Сигнал: Лонг шарттары орындалуда. Ордер ашылуда...")
            place_order("Buy", QTY_PER_GRID)
            time.sleep(300)
          elif current_price < current_ema:
            print("Сигнал: Шорт шарттары орындалуда. Ордер ашылуда...")
            place_order("Sell", QTY_PER_GRID)
            time.sleep(300)
        else:
          print(f"⚠️ RSI шектен тыс деңгейде ({current_rsi:.2f}). Күту режимі.")

      time.sleep(20)

    except Exception as e:
      print(f"Қате орын алды: {e}")
      time.sleep(10)


if __name__ == "__main__":
  trading_bot_loop()

