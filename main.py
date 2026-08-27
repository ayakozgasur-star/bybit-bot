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
QTY_PER_GRID = 250        
TARGET_ROI_PCT = 10.0     # Пайда 10% ROI-ге жеткенде автоматты түрде жабылады

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
    
    # RSI есептеу
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # EMA (Trend) есептеу - 20 периоды
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    
    return df['close'].iloc[-1], df['rsi'].iloc[-1], df['ema20'].iloc[-1]

def clear_orders():
    try:
        session.cancel_all_orders(category=CATEGORY, symbol=SYMBOL)
    except Exception as e:
        print(f"Ордерлерді өшіру қатесі: {e}")

def check_and_take_profit():
    """Ашық позицияның пайдасы 10% ROI жетсе, оны жауып пайданы бекітеді"""
    try:
        res = session.get_positions(category=CATEGORY, symbol=SYMBOL)
        positions = res['result']['list']
        for pos in positions:
            size = float(pos['size'])
            if size > 0:
                side = pos['side'] # Buy (Long) немесе Sell (Short)
                avg_price = float(pos['avgPrice'])
                mark_price = float(pos['markPrice'])
                leverage = float(pos['leverage'])
                
                # ROI есептеу (Лонг және Шорт үшін бөлек)
                if side == "Buy":
                    current_roi = ((mark_price - avg_price) / avg_price) * leverage * 100
                    close_side = "Sell"
                    pos_idx = 1
                else:
                    current_roi = ((avg_price - mark_price) / avg_price) * leverage * 100
                    close_side = "Buy"
                    pos_idx = 2

                print(f"📊 [{side}] Ағымдағы ROI: {round(current_roi, 2)}% (Мақсат: {TARGET_ROI_PCT}%)")

                if current_roi >= TARGET_ROI_PCT:
                    print(f"🎯 Пайда мақсаты орындалды! {side} позициясы жабылуда...")
                    clear_orders()
                    session.place_order(
                        category=CATEGORY,
                        symbol=SYMBOL,
                        side=close_side,
                        orderType="Market",
                        qty=pos['size'],
                        reduceOnly=True,
                        positionIdx=pos_idx
                    )
                    print("✅ Пайда сәтті бекітілді! Бот 1 минут тынығып, жаңа сетка құрады.")
                    time.sleep(60)
                    return True
    except Exception as e:
        print(f"Take Profit қатесі: {e}")
    return False

def place_dynamic_grid(current_price, trend_is_bullish):
    clear_orders()
    half_grid = GRID_COUNT // 2

    if trend_is_bullish:
        print(f"📈 Өсу тренді анықталды (Bullish). LONG сетка құрылуда | Баға: ${current_price}")
        # Лонг сетка (Төмендеуге ордерлер Buy, жоғарыға Sell)
        for i in range(1, half_grid + 1):
            buy_price = round(current_price * (1 - (GRID_SPACING_PCT * i)), 4)
            try:
                session.place_order(category=CATEGORY, symbol=SYMBOL, side="Buy", orderType="Limit", price=str(buy_price), qty=str(QTY_PER_GRID), positionIdx=1, postOnly=True)
            except Exception as e:
                print(f"Buy қатесі: {e}")
        for i in range(1, half_grid + 1):
            sell_price = round(current_price * (1 + (GRID_SPACING_PCT * i)), 4)
            try:
                session.place_order(category=CATEGORY, symbol=SYMBOL, side="Sell", orderType="Limit", price=str(sell_price), qty=str(QTY_PER_GRID), positionIdx=2, postOnly=True)
            except Exception as e:
                print(f"Sell қатесі: {e}")
    else:
        print(f"📉 Құлау тренді анықталды (Bearish). SHORT сетка құрылуда | Баға: ${current_price}")
        # Шорт сетка (Жоғарылауға ордерлер Sell, төменге Buy)
        for i in range(1, half_grid + 1):
            sell_price = round(current_price * (1 + (GRID_SPACING_PCT * i)), 4)
            try:
                session.place_order(category=CATEGORY, symbol=SYMBOL, side="Sell", orderType="Limit", price=str(sell_price), qty=str(QTY_PER_GRID), positionIdx=2, postOnly=True)
            except Exception as e:
                print(f"Sell қатесі: {e}")
        for i in range(1, half_grid + 1):
            buy_price = round(current_price * (1 - (GRID_SPACING_PCT * i)), 4)
            try:
                session.place_order(category=CATEGORY, symbol=SYMBOL, side="Buy", orderType="Limit", price=str(buy_price), qty=str(QTY_PER_GRID), positionIdx=1, postOnly=True)
            except Exception as e:
                print(f"Buy қатесі: {e}")

def run_bot():
    setup_market()
    last_update = 0

    while True:
        try:
            if check_and_take_profit():
                last_update = 0 

            price, rsi, ema20 = get_market_data()
            trend_is_bullish = price >= ema20
            
            print(f"[DYNAMIC DEMO] XRP/USDT: ${price} | EMA20: ${round(ema20, 4)} | RSI: {round(rsi, 2)}")

            if 30 <= rsi <= 70:
                if time.time() - last_update > 600:
                    place_dynamic_grid(price, trend_is_bullish)
                    last_update = time.time()
            else:
                print(f"⚠️ RSI шектен тыс деңгейде ({round(rsi, 2)}).")

            time.sleep(15)
        except Exception as e:
            print(f"Қате: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_bot()
