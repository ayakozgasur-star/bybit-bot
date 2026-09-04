import os
import time
import pandas as pd
from pybit.unified_trading import HTTP

API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")

SYMBOL = "XRPUSDT"
CATEGORY = "linear"
LEVERAGE = 3              
GRID_COUNT = 8            
GRID_SPACING_PCT = 0.01   
QTY_PER_GRID = 500        # Комиссияны жабу үшін оңтайландырылган көлем
TARGET_ROI_PCT = 2.0      # Тейк профит мақсаты екі жаққа да 2%

session = HTTP(
    demo=True,
    api_key=API_KEY,
    api_secret=API_SECRET,
)

def setup_market():
    try:
        session.set_leverage(
            category=CATEGORY,
            symbol=SYMBOL,
            buyLeverage=str(LEVERAGE),
            sellLeverage=str(LEVERAGE),
        )
        print(f"✅ {SYMBOL} үшін иық {LEVERAGE}x болып орнатылды.")
    except Exception as e:
        print(f"ℹ️ Иық баптауы ескертуі: {e}")

def get_market_data():
    response = session.get_kline(category=CATEGORY, symbol=SYMBOL, interval="15", limit=50)
    data = response['result']['list']
    df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
    df['close'] = df['close'].astype(float)
    
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # Тренді анықтау үшін EMA 20
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    
    return df['close'].iloc[-1], df['rsi'].iloc[-1], df['ema20'].iloc[-1]

def clear_orders():
    try:
        session.cancel_all_orders(category=CATEGORY, symbol=SYMBOL)
    except Exception as e:
        print(f"Ордерлерді өшіру қатесі: {e}")

def check_and_take_profit():
    """Лонг немесе Шорт позициясы 2% ROI жетсе, автоматты түрде жабады"""
    try:
        res = session.get_positions(category=CATEGORY, symbol=SYMBOL)
        positions = res['result']['list']
        for pos in positions:
            size = float(pos['size'])
            if size > 0:
                avg_price = float(pos['avgPrice'])
                mark_price = float(pos['markPrice'])
                leverage = float(pos['leverage'])
                side = pos['side'] # "Buy" (Лонг) немесе "Sell" (Шорт)
                
                # ROI есептеу
                if side == "Buy":
                    current_roi = ((mark_price - avg_price) / avg_price) * leverage * 100
                else:
                    current_roi = ((avg_price - mark_price) / avg_price) * leverage * 100
                
                print(f"📊 Позиция ({side}) ROI: {round(current_roi, 2)}% (Мақсат: {TARGET_ROI_PCT}%)")

                if current_roi >= TARGET_ROI_PCT:
                    print(f"🎯 Пайда мақсаты орындалды ({side})! Позиция жабылуда...")
                    clear_orders()
                    
                    close_side = "Sell" if side == "Buy" else "Buy"
                    pos_idx = 1 if side == "Buy" else 2
                    
                    session.place_order(
                        category=CATEGORY,
                        symbol=SYMBOL,
                        side=close_side,
                        orderType="Market",
                        qty=pos['size'],
                        reduceOnly=True,
                        positionIdx=pos_idx
                    )
                    print("✅ Пайда сәтті бекітілді! Бот 1 минут тынығады.")
                    time.sleep(60)
                    return True
    except Exception as e:
        print(f"Take Profit қатесі: {e}")
    return False

def place_directional_grid(current_price, rsi, ema):
    clear_orders()
    
    # Тек Лонг сеткасы
    if current_price > ema and rsi > 50:
        print(f"🚀 Тренд: Өсу үрдісі (Лонг сеткасы 500 XRP) | Баға: ${current_price}, RSI: {round(rsi, 2)}")
        for i in range(1, GRID_COUNT + 1):
            buy_price = round(current_price * (1 - (GRID_SPACING_PCT * i)), 4)
            try:
                session.place_order(
                    category=CATEGORY, symbol=SYMBOL, side="Buy", orderType="Limit",
                    price=str(buy_price), qty=str(QTY_PER_GRID), positionIdx=1, postOnly=True
                )
            except Exception as e:
                print(f"Buy қатесі: {e}")

    # Тек Шорт сеткасы
    elif current_price < ema and rsi < 50:
        print(f"📉 Тренд: Төмендеу үрдісі (Шорт сеткасы 500 XRP) | Баға: ${current_price}, RSI: {round(rsi, 2)}")
        for i in range(1, GRID_COUNT + 1):
            sell_price = round(current_price * (1 + (GRID_SPACING_PCT * i)), 4)
            try:
                session.place_order(
                    category=CATEGORY, symbol=SYMBOL, side="Sell", orderType="Limit",
                    price=str(sell_price), qty=str(QTY_PER_GRID), positionIdx=2, postOnly=True
                )
            except Exception as e:
                print(f"Sell қатесі: {e}")
    else:
        print(f"⏳ Нарық анық емес (Флэт), анализ күтілуде... Баға: ${current_price}, RSI: {round(rsi, 2)}")

def run_bot():
    setup_market()
    last_update = 0

    while True:
        try:
            if check_and_take_profit():
                last_update = 0 

            price, rsi, ema = get_market_data()
            print(f"[LIVE DEMO] XRP/USDT: ${price} | EMA20: ${round(ema, 4)} | RSI: {round(rsi, 2)}")

            if time.time() - last_update > 600:
                place_directional_grid(price, rsi, ema)
                last_update = time.time()

            time.sleep(15)
        except Exception as e:
            print(f"Қате: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_bot()
