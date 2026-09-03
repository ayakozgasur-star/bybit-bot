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

def analyze_and_trade(closes):
    df = pd.DataFrame({'close': closes})
    
    # RSI (14)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # MACD
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    
    current_price = df['close'].iloc[-1]
    current_rsi = df['rsi'].iloc[-1]
    current_macd = df['macd'].iloc[-1]
    
    print(f"[{SYMBOL}] Баға: {current_price} | RSI: {current_rsi:.2f} | MACD: {current_macd:.4f}")
    
    if current_rsi < 45:
        print(">>> Жұмсартылған LONG сигналы! Ордер ашылуда...")
        try:
            session.set_leverage(
                category=CATEGORY,
                symbol=SYMBOL,
                buyLeverage=str(LEVERAGE),
                sellLeverage=str(LEVERAGE)
            )
        except Exception as e:
            pass
            
        session.place_order(
            category=CATEGORY,
            symbol=SYMBOL,
            side="Buy",
            orderType="Market",
            qty=str(QTY_PER_GRID),
            timeInForce="GTC",
            positionIdx=0
        )
    elif current_rsi > 55:
        print(">>> Жұмсартылған SHORT сигналы! Ордер ашылуда...")
        try:
            session.set_leverage(
                category=CATEGORY,
                symbol=SYMBOL,
                buyLeverage=str(LEVERAGE),
                sellLeverage=str(LEVERAGE)
            )
        except Exception as e:
            pass
            
        session.place_order(
            category=CATEGORY,
            symbol=SYMBOL,
            side="Sell",
            orderType="Market",
            qty=str(QTY_PER_GRID),
            timeInForce="GTC",
            positionIdx=0
        )

def main_loop():
    print("Жылдамдетілген сауда боты іске қосылды...")
    while True:
        try:
            response = session.get_kline(
                category=CATEGORY,
                symbol=SYMBOL,
                interval="1",
                limit=50
            )
            list_data = response.get("result", {}).get("list", [])
            if list_data:
                closes = [float(item[4]) for item in reversed(list_data)]
                analyze_and_trade(closes)
        except Exception as e:
            print(f"Сауда ордерін орындау қатесі: {e}")
            
        time.sleep(60)

if __name__ == "__main__":
    main_loop()
