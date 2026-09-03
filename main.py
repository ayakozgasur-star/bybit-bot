import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import pandas as pd
from pybit.unified_trading import HTTP

API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
SYMBOL = "XRPUSDT"
CATEGORY = "linear"
LEVERAGE = 3
QTY_PER_GRID = 25
ROI_TARGET = 0.02

session = HTTP(
    demo=True,
    api_key=API_KEY,
    api_secret=API_SECRET,
)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running successfully!")

def run_server():
    server = HTTPServer(("0.0.0.0", 8080), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()

def get_market_data(symbol):
    try:
        response = session.get_kline(
            category=CATEGORY,
            symbol=symbol,
            interval="15",
            limit=50
        )
        list_data = response.get("result", {}).get("list", [])
        if list_data:
            df = pd.DataFrame(
                list_data,
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
    except Exception as e:
        print(f"Маркет деректерін алу қатесі: {e}")
    return None

def calculate_indicators(df):
    df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))
    return df

def get_open_position_size(position_idx):
    try:
        response = session.get_positions(
            category=CATEGORY,
            symbol=SYMBOL
        )
        list_pos = response.get("result", {}).get("list", [])
        for pos in list_pos:
            if int(pos.get("positionIdx", 0)) == position_idx:
                size = float(pos.get("size", 0))
                if size > 0:
                    return True
    except Exception as e:
        print(f"Позицияны тексеру қатесі: {e}")
    return False

def trading_bot_loop():
    print(f"Бот іске қосылды! Символ: {SYMBOL}, Иық: {LEVERAGE}x")

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
                        if get_open_position_size(1):
                            print("ℹ️ Лонг позициясы қазірдің өзінде ашық. Күту режимі.")
                        else:
                            print("Сигнал: Лонг шарттары орындалуда. Жаңа ордер ашылуда...")
                            try:
                                session.set_leverage(
                                    category=CATEGORY,
                                    symbol=SYMBOL,
                                    buyLeverage=str(LEVERAGE),
                                    sellLeverage=str(LEVERAGE)
                                )
                            except Exception:
                                pass
                                
                            session.place_order(
                                category=CATEGORY,
                                symbol=SYMBOL,
                                side="Buy",
                                orderType="Market",
                                qty=str(QTY_PER_GRID),
                                timeInForce="GTC",
                                positionIdx=1
                            )
                    elif current_price < current_ema:
                        if get_open_position_size(2):
                            print("ℹ️ Шорт позициясы қазірдің өзінде ашық. Күту режимі.")
                        else:
                            print("Сигнал: Шорт шарттары орындалуда. Жаңа ордер ашылуда...")
                            try:
                                session.set_leverage(
                                    category=CATEGORY,
                                    symbol=SYMBOL,
                                    buyLeverage=str(LEVERAGE),
                                    sellLeverage=str(LEVERAGE)
                                )
                            except Exception:
                                pass
                                
                            session.place_order(
                                category=CATEGORY,
                                symbol=SYMBOL,
                                side="Sell",
                                orderType="Market",
                                qty=str(QTY_PER_GRID),
                                timeInForce="GTC",
                                positionIdx=2
                            )
                else:
                    print(f"⚠️ RSI шектен тыс деңгейде ({current_rsi:.2f}). Күту режимі.")

            time.sleep(60)

        except Exception as e:
            print(f"Қате орын алды: {e}")
            time.sleep(10)

if __name__ == "__main__":
    trading_bot_loop()
