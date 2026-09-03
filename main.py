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
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running successfully!")

def run_server():
    server = HTTPServer(('0.0.0.0', 8080), HealthCheckHandler)
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

    # Bollinger Bands (20, 2)
    df['sma'] = df['close'].rolling(window=20).mean()
    df['std'] = df['close'].rolling(window=20).std()
    df['upper'] = df['sma'] + (df['std'] * 2)
    df['lower'] = df['sma'] - (df['std'] * 2)

    # MACD (12, 26, 9)
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    rsi = df['rsi'].iloc[-1]
    upper = df['upper'].iloc[-1]
    lower = df['lower'].iloc[-1]
    macd = df['macd'].iloc[-1]
    signal = df['signal'].iloc[-1]
    price = df['close'].iloc[-1]

    print(f"[{SYMBOL}] Баға: {price} | RSI: {rsi:.2f} | MACD: {macd:.4f}")

    try:
        # Жұмсартылған LONG логикасы (RSI < 45 болса)
        if rsi < 45:
            print(">>> Жұмсартылған LONG сигналы! Ордер ашылуда...")
            session.set_leverage(category=CATEGORY, symbol=SYMBOL, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
            session.place_order(
                category=CATEGORY,
                symbol=SYMBOL,
                side="Buy",
                orderType="Market",
                qty=str(QTY_PER_GRID),
                timeInForce="GoodTillCancel"
            )
            print("LONG ордері сәтті орналастырылды!")

        # Жұмсартылған SHORT логикасы (RSI > 55 болса)
        elif rsi > 55:
            print(">>> Жұмсартылған SHORT сигналы! Ордер ашылуда...")
            session.set_leverage(category=CATEGORY, symbol=SYMBOL, buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
            session.place_order(
                category=CATEGORY,
                symbol=SYMBOL,
                side="Sell",
                orderType="Market",
                qty=str(QTY_PER_GRID),
                timeInForce="GoodTillCancel"
            )
            print("SHORT ордері сәтті орналастырылды!")

    except Exception as e:
        print(f"Сауда ордерін орындау қатесі: {e}")

print("Жылдамдетілген сауда боты іске қосылды...")

while True:
    try:
        response = session.get_kline(
            category=CATEGORY,
            symbol=SYMBOL,
            interval="1",  # 1 минуттық свечалар
            limit=50
        )
        klines = response.get("result", {}).get("list", [])
        if klines:
            closes = [float(k[4]) for k in reversed(klines)]
            analyze_and_trade(closes)
            
        time.sleep(30)
    except Exception as e:
        print(f"Қате орын алды: {e}")
        time.sleep(10)
