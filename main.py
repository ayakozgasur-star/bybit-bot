import time
import json
import os
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ==================== 1. БАПТАУЛАР (SETTINGS) ====================
# 🔥 ОСЫ ЖЕРГЕ BYBIT-ТЕН АЛҒАН НАҚТЫ API КІЛТТЕРІҢІЗДІ ЖАЗЫҢЫЗ
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"

# Реальный аккаунт үшін False, Testnet үшін True
TESTNET = False  

# Бот тексеретін 6 кандидат монета
CANDIDATE_SYMBOLS = [
    "SOLUSDT", "AVAXUSDT", "NEARUSDT", 
    "XRPUSDT", "DOGEUSDT", "TONUSDT"
]

# Нысаналы пайда (Target Profit)
TARGET_PROFIT_USDT = 100.0 

# Мартингейл маржа сатылары (USDT)
MARTINGALE_STEPS = [1, 4, 7, 12, 19, 30, 48, 75, 120]

# Тейк-Профит және Стоп-Лосс (TP 0.5% / SL 0.5%)
TP_PCT = 0.005  # 0.5%
SL_PCT = 0.005  # 0.5%

# Post-Only лимиттік ордер үшін баға ығысуы (0.02%)
PRICE_OFFSET_PCT = 0.0002 

# Орындалмаған ордердің күту уақыты (секунд)
ORDER_TIMEOUT_SEC = 15

# Деректерді сақтайтын файл
STATE_FILE = "bot_state.json"

# ==================== 2. БИРЖАҒА ҚОСЫЛУ ====================
session = HTTP(
    testnet=TESTNET,
    api_key=API_KEY,
    api_secret=API_SECRET,
)

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

# ==================== 3. ДЕРЕКТЕРДІ САҚТАУ ЖӘНЕ ЖҮКТЕУ ====================
def load_bot_state():
    """Сақталған 3 монетаны, сатыларды және жиналған пайданы оқу"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                log(f"💾 Файлдан деректер жүктелді:")
                log(f"   📌 Монеталар: {data.get('selected_symbols', [])}")
                log(f"   📊 Сатылар: {data.get('steps', {})}")
                log(f"   💰 Жиналған пайда: +{data.get('accumulated_profit', 0.0):.2f}$/ {TARGET_PROFIT_USDT}$")
                return (
                    data.get("selected_symbols", []),
                    data.get("steps", {}),
                    float(data.get("accumulated_profit", 0.0))
                )
        except Exception as e:
            log(f"Файлды оқу қатесі: {e}")
    return [], {}, 0.0

def save_bot_state(selected_symbols, steps, profit):
    """Деректерді JSON файлға сақтау"""
    try:
        data = {
            "selected_symbols": selected_symbols,
            "steps": steps,
            "accumulated_profit": profit
        }
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        log(f"Деректерді сақтау қатесі: {e}")

# ==================== 4. 6 МОНЕТАДАН ЕҢ ҮЗДІК 3-ЕУІН ТАҢДАУ ====================
def select_best_3_symbols():
    """6 монетаны анализдеп, ең үздік 3 монетаны таңдау (Публичный API-мен)"""
    scores = {}
    log("🔍 6 кандидат монетаға анализ басталды (Жаңа ТОП-3 монета таңдалады)...")
    
    for symbol in CANDIDATE_SYMBOLS:
        try:
            res = session.get_kline(category="linear", symbol=symbol, interval="15", limit=50)
            if res.get('retCode') != 0 or not res.get('result', {}).get('list'):
                continue
                
            df = pd.DataFrame(res['result']['list'], columns=[
                'startTime', 'open', 'high', 'low', 'close', 'volume', 'turnover'
            ])
            df['close'] = df['close'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df = df.iloc[::-1].reset_index(drop=True)
            
            # ATR (Волатильность)
            df['tr'] = np.maximum(
                df['high'] - df['low'],
                np.maximum(
                    abs(df['high'] - df['close'].shift(1)),
                    abs(df['low'] - df['close'].shift(1))
                )
            )
            atr = df['tr'].rolling(14).mean().iloc[-1]
            volatility_pct = (atr / df['close'].iloc[-1]) * 100
            
            # RSI
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs.iloc[-1]))
            
            rsi_score = abs(rsi - 50)
            total_score = (volatility_pct * 0.6) + (rsi_score * 0.4)
            scores[symbol] = total_score
            
            log(f"   📊 {symbol} -> Волатильность: {volatility_pct:.2f}%, RSI: {rsi:.1f}, Балл: {total_score:.2f}")
        except Exception as e:
            log(f"Анализ қатесі ({symbol}): {e}")

    sorted_symbols = sorted(scores, key=scores.get, reverse=True)
    top_3 = sorted_symbols[:3]
    
    if len(top_3) < 3:
        top_3 = ["SOLUSDT", "XRPUSDT", "NEARUSDT"]
        
    log(f"🎯 ЖАҢА ТОП-3 МОНЕТА БЕКІТІЛДІ: {top_3}")
    return top_3

# ==================== 5. PRECISION (ДӘЛДІК) АЛУ ====================
def get_symbol_precisions(symbol):
    """Bybit-тен монетаның бағасы мен көлемінің дөңгелектеу дәлдігін алу"""
    try:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        if res.get('retCode') == 0:
            info = res['result']['list'][0]
            tick_size = info['priceFilter']['tickSize']
            qty_step = info['lotSizeFilter']['qtyStep']
            
            price_prec = len(tick_size.split('.')[1]) if '.' in tick_size else 0
            qty_prec = len(qty_step.split('.')[1]) if '.' in qty_step else 0
            return price_prec, qty_prec
    except Exception as e:
        log(f"Precision алу қатесі ({symbol}): {e}")
    return 4, 1

# ==================== 6. ОРДЕРЛЕРДІ ТАЗАЛАУ ЖӘНЕ P&L ====================
def cancel_unfilled_orders(symbol):
    """15 секундтан асып, орындалмай ілініп тұрған ордерлерді жою"""
    try:
        res = session.get_open_orders(category="linear", symbol=symbol)
        if res.get('retCode') == 0:
            orders = res['result']['list']
            current_time = time.time()
            for o in orders:
                created_time = int(o['createdTime']) / 1000
                if current_time - created_time > ORDER_TIMEOUT_SEC:
                    session.cancel_order(category="linear", symbol=symbol, orderId=o['orderId'])
                    log(f"⚠️ [{symbol}] {ORDER_TIMEOUT_SEC} сек ішінде орындалмаған ордер жойылды.")
        elif res.get('retCode') == 10003 or res.get('retCode') == 10004:
            log(f"🚨 API Key қатесі (401 / Invalid API). `API_KEY` мен `API_SECRET` тексеріңіз!")
    except Exception as e:
        log(f"Ордерді жою қатесі ({symbol}): {e}")

def update_step_for_symbol(symbol):
    """Позиция жабылғанда P&L тексеру және +100$ мақсатын бақылау"""
    global symbol_steps, selected_symbols, total_accumulated_profit
    try:
        time.sleep(2)
        res = session.get_closed_pnl(category="linear", symbol=symbol, limit=1)
        if res.get('retCode') == 0 and res['result']['list']:
            last_trade = res['result']['list'][0]
            closed_pnl = float(last_trade.get('closedPnl', 0))
            
            total_accumulated_profit += closed_pnl
            
            if closed_pnl > 0:
                symbol_steps[symbol] = 0
                log(f"✅ [{symbol}] TP соғылды (PnL: +{closed_pnl:.2f}$). Жалпы пайда: +{total_accumulated_profit:.2f}$/ {TARGET_PROFIT_USDT}$")
            else:
                symbol_steps[symbol] += 1
                log(f"❌ [{symbol}] SL соғылды (PnL: {closed_pnl:.2f}$). Келесі саты: #{symbol_steps[symbol] + 1}. Жалпы пайда: {total_accumulated_profit:.2f}$")
            
            if total_accumulated_profit >= TARGET_PROFIT_USDT:
                log(f"🎉🎉🎉 МAҚСАТ ОРЫНДАЛДЫ! Пайда +{total_accumulated_profit:.2f}$ USDT-ға жетті!")
                log("🔄 Монеталар тізімі тазаланып, жаңа ТОП-3 монета қайта таңдалады...")
                
                total_accumulated_profit = 0.0
                selected_symbols = select_best_3_symbols()
                symbol_steps = {s: 0 for s in selected_symbols}
                
                for s in selected_symbols:
                    try:
                        session.set_leverage(category="linear", symbol=s, buyLeverage="10", sellLeverage="10")
                    except:
                        pass

            save_bot_state(selected_symbols, symbol_steps, total_accumulated_profit)
    except Exception as e:
        log(f"PnL тексеру қатесі ({symbol}): {e}")

# ==================== 7. ПОЗИЦИЯ ЖӘНЕ ОРДЕР АШУ ====================
def get_position(symbol):
    try:
        res = session.get_positions(category="linear", symbol=symbol)
        if res.get('retCode') == 0:
            for p in res['result']['list']:
                if float(p['size']) > 0:
                    return p
        elif res.get('retCode') in [10003, 10004, 10005]:
            log(f"🚨 API Key/Secret қатесі немесе авторизация жоқ (ErrCode: {res.get('retCode')}).")
    except Exception as e:
        log(f"Позицияны тексеру қатесі ({symbol}): {e}")
    return None

def open_order_for_symbol(symbol):
    global symbol_steps
    
    cancel_unfilled_orders(symbol)
    
    pos = get_position(symbol)
    open_orders_res = session.get_open_orders(category="linear", symbol=symbol)
    
    if open_orders_res.get('retCode') != 0:
        return
        
    open_orders = open_orders_res['result']['list']
    
    if pos or len(open_orders) > 0:
        return

    update_step_for_symbol(symbol)

    step_idx = symbol_steps.get(symbol, 0)
    if step_idx >= len(MARTINGALE_STEPS):
        step_idx = len(MARTINGALE_STEPS) - 1

    usdt_margin = MARTINGALE_STEPS[step_idx]

    ticker = session.get_tickers(category="linear", symbol=symbol)
    last_price = float(ticker['result']['list'][0]['lastPrice'])

    limit_price = last_price * (1 - PRICE_OFFSET_PCT)
    qty = (usdt_margin * 10) / limit_price

    tp_price = limit_price * (1 + TP_PCT)
    sl_price = limit_price * (1 - SL_PCT)

    price_prec, qty_prec = get_symbol_precisions(symbol)

    try:
        res = session.place_order(
            category="linear",
            symbol=symbol,
            side="Buy",
            orderType="Limit",
            qty=str(round(qty, qty_prec)),
            price=str(round(limit_price, price_prec)),
            takeProfit=str(round(tp_price, price_prec)),
            stopLoss=str(round(sl_price, price_prec)),
            timeInForce="PostOnly",
            isLeverage=1
        )
        if res.get('retCode') == 0:
            log(f"🚀 [{symbol}] Post-Only ордер қойылды! Маржа: ${usdt_margin} (Саты #{step_idx + 1}) | Баға: {limit_price:.4f}")
        else:
            log(f"❌ [{symbol}] Ордер қойылмады: {res.get('retMsg')} (Code: {res.get('retCode')})")
    except Exception as e:
        log(f"Ордер қою қатесі ({symbol}): {e}")

# ==================== 8. НЕГІЗГІ ЦИКЛ (MAIN LOOP) ====================
def main():
    global selected_symbols, symbol_steps, total_accumulated_profit
    log("🤖 Бот іске қосылуда...")
    
    # 1. Файлды тексереміз
    selected_symbols, symbol_steps, total_accumulated_profit = load_bot_state()
    
    # 2. Егер файл бос болса, 3 монета таңдаймыз
    if not selected_symbols:
        selected_symbols = select_best_3_symbols()
        symbol_steps = {s: 0 for s in selected_symbols}
        total_accumulated_profit = 0.0
        save_bot_state(selected_symbols, symbol_steps, total_accumulated_profit)

    # 10x плечо орнату
    for s in selected_symbols:
        try:
            session.set_leverage(category="linear", symbol=s, buyLeverage="10", sellLeverage="10")
        except Exception as e:
            pass

    log(f"⚡ Бот сауданы бастайды. Ағымдағы монеталар: {selected_symbols}")
    log(f"📊 Жиналған пайда: +{total_accumulated_profit:.2f}$/ {TARGET_PROFIT_USDT}$")

    while True:
        try:
            for symbol in list(selected_symbols):
                open_order_for_symbol(symbol)
                
            time.sleep(1.0)  # API лимитке түсіп қалмас үшін 1 секундтық қауіпсіз кідіріс
        except Exception as e:
            log(f"Цикл қатесі: {e}")
            time.sleep(2)

if __name__ == "__main__":
    main()
