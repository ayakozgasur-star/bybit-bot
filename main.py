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
GRID_COUNT = 8
GRID_SPACING_PCT = 0.01
QTY_PER_GRID = 250
TARGET_ROT_PCT = 2  # Take profit 2% етіп өзгертілді

session = HTTP(
    testnet=False,  # Ішкі Демо трейдинг үшін False болуы тиіс
    api_key=API_KEY,
    api_secret=API_SECRET,
)

# Render/Railway порт талабын орындау үшін мини веб-сервер
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running successfully!")

def run_server():
    server = HTTPServer(('0.0.0.0', 8080), HealthCheckHandler)
    server.serve_forever()

# Веб-серверді бөлек ағында (thread) іске қосу
threading.Thread(target=run_server, daemon=True).start()

print("Бот сәтті іске қосылды және жұмыс істеп тұр...")

while True:
    try:
        # Мысал ретінде нарық деректерін алу және тексеру логикасы
        response = session.get_kline(
            category=CATEGORY,
            symbol=SYMBOL,
            interval="1",
            limit=50
        )
        time.sleep(60)
    except Exception as e:
        print(f"Қате орын алды: {e}")
        time.sleep(10)
