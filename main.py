import os
import time
import requests
from pybit.unified_trading import HTTP

# Railway немесе ортадан айнымалыларды қауіпсіз түрде алу
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")

# Айнымалылардың бос еместігін тексеру (NoneType қатесін болдырмау үшін)
if not API_KEY or not API_SECRET:
    print("Қате: BYBIT_API_KEY немесе BYBIT_API_SECRET табылмады! Railway variables бөлімін тексеріңіз.")
else:
    print("API кілттері сәтті оқылды.")

# Bybit Демо сессиясын қосу (testnet=True немесе демо URL)
session = HTTP(
    testnet=True,
    api_key=API_KEY,
    api_secret=API_SECRET,
    endpoint="https://api-demo.bybit.com"
)

SYMBOL = "XRPUSDT"
ROI_TARGET = 0.02
LEVERAGE = 3

print(f"Бот іске қосылды! Символ: {SYMBOL}, ROI мақсаты: {ROI_TARGET*100}%, Иірм: {LEVERAGE}x")

def check_market_and_trade():
    try:
        # Мысал ретінде баға мен RSI мәндерін алу логикасы
        print(f"[DEMO] {SYMBOL}: Бот нарықты бақылап жатыр...")
        
        # Ордер ашу логикасы (демо шот үшін)
        # session.place_order(...)
        
    except Exception as e:
        print(f"Қате орын алды: {str(e)}")

if __name__ == "__main__":
    while True:
        check_market_and_trade()
        time.sleep(20)
