import csv
import math
import os
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from pybit.unified_trading import HTTP

# ==============================================================================
# SCALP BOT v3: КӨБІРЕК СИГНАЛ, КӨБІРЕК СИМВОЛ, ҚАУІПСІЗ ОРДЕР БАСҚАРУ
# ==============================================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
IS_DEMO = True

SYMBOLS = ["SOLUSDT", "ETHUSDT", "XRPUSDT", "DOGEUSDT", "BTCUSDT"]

LEVERAGE = 15
MAX_RISK_USDT = 1.0          # Бір сделкадағы тәуекел ($)
MAX_OPEN_POSITIONS = 2       # Бір уақытта ашық позиция саны
DAILY_LOSS_LIMIT = 4.0       # Күндік шығын лимиті ($), асса жаңа кіру тоқтайды

MIN_ATR_PCT = 0.0012         # ATR/баға осыдан төмен болса (өлі нарық) кірмейміз
SL_MIN_PCT = 0.004
SL_ATR_MULT = 1.2
TP_RR = 2.5

ENTRY_TIMEOUT_SEC = 30       # Орындалмаған limit ордерді жою уақыты (секунд)
COOLDOWN_WIN_CANDLES = 1
COOLDOWN_LOSS_CANDLES = 3

BREAK_EVEN_TRIGGER_PCT = 0.004
BREAK_EVEN_LOCK_PCT = 0.001
TRAILING_OFFSET_PCT = 0.003
SL_UPDATE_THRESHOLD_PCT = 0.0004

MAX_PYRAMID_STEPS = 3        # Пирамидинг тек break-even қосылғаннан кейін
ADD_STEP_PCT = 0.004
PYRAMID_SIZE_RATIO = 0.5

TRADES_CSV = "trades.csv"
CANDLE_SEC = 300

session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET, demo=IS_DEMO)

symbol_state = {
    s: {
        "active": False, "step": 0, "last_entry_price": 0.0, "avg_price": 0.0,
        "total_qty": 0.0, "initial_qty": 0.0, "break_even": False,
        "cooldown_until": 0.0, "pending_id": None, "pending_time": 0.0,
        "last_candle": 0,
    }
    for s in SYMBOLS
}

_info_cache = {}
_pnl_cache = {"t": 0.0, "v": 0.0}


def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [BOT-v3] {msg}", flush=True)


# ------------------------------------------------------------------------------
# Көмекші функциялар
# ------------------------------------------------------------------------------
def _precision(step):
    s = f"{step:.10f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def format_qty_floor(value, step):
    p = _precision(step)
    f = 10 ** p
    return f"{math.floor(value * f) / f:.{p}f}"


def format_price(value, tick):
    p = _precision(tick)
    return f"{round(round(value / tick) * tick, p):.{p}f}"


def get_info(symbol):
    if symbol not in _info_cache:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        i = res["result"]["list"][0]
        _info_cache[symbol] = {
            "qty_step": float(i["lotSizeFilter"]["qtyStep"]),
            "min_qty": float(i["lotSizeFilter"]["minOrderQty"]),
            "min_notional": float(i["lotSizeFilter"].get("minNotionalValue", 5.0)),
            "tick": float(i["priceFilter"]["tickSize"]),
        }
    return _info_cache[symbol]


def setup_exchange_modes():
    for symbol in SYMBOLS:
        try:
            session.switch_position_mode(category="linear", symbol=symbol, mode=3)
        except Exception:
            pass
        try:
            session.set_leverage(category="linear", symbol=symbol,
                                 buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
        except Exception:
            pass


def get_klines(symbol, interval="5", limit=220):
    try:
        res = session.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        if res.get("retCode") != 0:
            return None
        df = pd.DataFrame(res["result"]["list"],
                          columns=["start_time", "open", "high", "low", "close", "volume", "turnover"])
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)
        return df.iloc[::-1].reset_index(drop=True)
    except Exception as e:
        log(f"Klines қатесі ({symbol}): {e}")
        return None


# ------------------------------------------------------------------------------
# Индикаторлар
# ------------------------------------------------------------------------------
def calc_ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def calc_rsi(s, n=14):
    d = s.diff()
    g = d.where(d > 0, 0).ewm(alpha=1 / n, adjust=False).mean()
    l = (-d.where(d < 0, 0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + g / (l + 1e-12))


def calc_stoch_k(s, n=14, k=3):
    rsi = calc_rsi(s, n)
    lo, hi = rsi.rolling(n).min(), rsi.rolling(n).max()
    return ((rsi - lo) / (hi - lo + 1e-9)).rolling(k).mean() * 100


def calc_atr(df, n=14):
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


# ------------------------------------------------------------------------------
# Сигнал логикасы: 3 түрлі кіру стратегиясы
# ------------------------------------------------------------------------------
def check_entry_signal(symbol):
    df = get_klines(symbol, "5", 220)
    if df is None or len(df) < 200:
        return None

    c = df["close"]
    df["ema200"], df["ema21"] = calc_ema(c, 200), calc_ema(c, 21)
    df["ema3"], df["ema8"] = calc_ema(c, 3), calc_ema(c, 8)
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    df["bb_up"], df["bb_lo"] = mid + 2 * std, mid - 2 * std
    df["rsi"] = calc_rsi(c)
    df["stoch"] = calc_stoch_k(c)
    df["atr"] = calc_atr(df)
    df["vol_ma"] = df["volume"].rolling(20).mean()

    cur, prev = df.iloc[-2], df.iloc[-3]   # Жабылған шамдар
    price = df.iloc[-1]["close"]
    if np.isnan(cur["atr"]) or np.isnan(cur["stoch"]) or np.isnan(cur["vol_ma"]):
        return None
    if cur["atr"] / price < MIN_ATR_PCT:
        return None

    vol_ok = cur["volume"] > cur["vol_ma"] * 0.8
    body = abs(cur["close"] - cur["open"])
    rng = cur["high"] - cur["low"]
    lw = min(cur["close"], cur["open"]) - cur["low"]
    uw = cur["high"] - max(cur["close"], cur["open"])
    bull_pin = lw > body * 2 and lw > rng * 0.5
    bear_pin = uw > body * 2 and uw > rng * 0.5

    up = cur["close"] > cur["ema200"]
    down = cur["close"] < cur["ema200"]

    long_cross = prev["ema3"] <= prev["ema8"] and cur["ema3"] > cur["ema8"] and cur["stoch"] < 60
    long_pull = (cur["ema8"] > cur["ema21"] and cur["low"] <= cur["ema21"]
                 and cur["close"] > cur["ema21"] and cur["close"] > cur["open"] and cur["rsi"] < 60)
    long_bb = (cur["close"] <= cur["bb_lo"] * 1.005 or bull_pin) and cur["stoch"] < 35

    short_cross = prev["ema3"] >= prev["ema8"] and cur["ema3"] < cur["ema8"] and cur["stoch"] > 40
    short_pull = (cur["ema8"] < cur["ema21"] and cur["high"] >= cur["ema21"]
                  and cur["close"] < cur["ema21"] and cur["close"] < cur["open"] and cur["rsi"] > 40)
    short_bb = (cur["close"] >= cur["bb_up"] * 0.995 or bear_pin) and cur["stoch"] > 65

    if up and vol_ok and (long_cross or long_pull or long_bb):
        return "Buy", price, cur["atr"]
    if down and vol_ok and (short_cross or short_pull or short_bb):
        return "Sell", price, cur["atr"]
    return None


# ------------------------------------------------------------------------------
# Күндік лимит және CSV журнал
# ------------------------------------------------------------------------------
def daily_pnl():
    if time.time() - _pnl_cache["t"] < 60:
        return _pnl_cache["v"]
    try:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        res = session.get_closed_pnl(category="linear", startTime=int(start.timestamp() * 1000), limit=100)
        if res.get("retCode") == 0:
            _pnl_cache["v"] = sum(float(r["closedPnl"]) for r in res["result"]["list"])
            _pnl_cache["t"] = time.time()
    except Exception as e:
        log(f"PnL оқу қатесі: {e}")
    return _pnl_cache["v"]


def trading_allowed():
    pnl = daily_pnl()
    if pnl <= -DAILY_LOSS_LIMIT:
        log(f"⛔ Күндік лимит: {pnl:.2f}$. Жаңа кіру жоқ.")
        return False
    return True


def log_closed_trade(symbol):
    """Жабылған соңғы сделканы CSV-ға жазады, PnL қайтарады."""
    try:
        res = session.get_closed_pnl(category="linear", symbol=symbol, limit=1)
        if res.get("retCode") == 0 and res["result"]["list"]:
            r = res["result"]["list"][0]
            pnl = float(r["closedPnl"])
            new = not os.path.exists(TRADES_CSV)
            with open(TRADES_CSV, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["time", "symbol", "side", "entry", "exit", "qty", "pnl"])
                w.writerow([datetime.now().isoformat(timespec="seconds"), symbol, r.get("side"),
                            r.get("avgEntryPrice"), r.get("avgExitPrice"), r.get("qty"), pnl])
            _pnl_cache["t"] = 0.0
            return pnl
    except Exception as e:
        log(f"CSV жазу қатесі: {e}")
    return 0.0


# ------------------------------------------------------------------------------
# Кіру: Post-Only limit
# ------------------------------------------------------------------------------
def open_initial_position(symbol, side, atr):
    info = get_info(symbol)
    try:
        t = session.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]
        bid, ask = float(t["bid1Price"]), float(t["ask1Price"])
    except Exception:
        return False

    entry = bid if side == "Buy" else ask
    sl_dist = max(entry * SL_MIN_PCT, atr * SL_ATR_MULT)
    raw_qty = MAX_RISK_USDT / sl_dist
    qty = float(format_qty_floor(raw_qty, info["qty_step"]))

    if qty < info["min_qty"]:
        qty = info["min_qty"]
        if qty * sl_dist > MAX_RISK_USDT * 1.5:
            return False
    if qty * entry < info["min_notional"]:
        return False

    if side == "Buy":
        idx, sl, tp = 1, entry - sl_dist, entry + sl_dist * TP_RR
    else:
        idx, sl, tp = 2, entry + sl_dist, entry - sl_dist * TP_RR

    try:
        res = session.place_order(
            category="linear", symbol=symbol, side=side, orderType="Limit",
            price=format_price(entry, info["tick"]),
            qty=format_qty_floor(qty, info["qty_step"]),
            timeInForce="PostOnly",
            takeProfit=format_price(tp, info["tick"]),
            stopLoss=format_price(sl, info["tick"]),
            positionIdx=idx,
        )
        if res.get("retCode") == 0:
            st = symbol_state[symbol]
            st["pending_id"] = res["result"]["orderId"]
            st["pending_time"] = time.time()
            log(f"📝 [{symbol}] {side} limit қойылды @ {entry} | SL {sl:.4f} | TP {tp:.4f}")
            return True
        log(f"[{symbol}] Ордер қатесі: {res.get('retMsg')}")
    except Exception as e:
        log(f"[{symbol}] Ордер қатесі: {e}")
    return False


def handle_pending(symbol):
    st = symbol_state[symbol]
    try:
        res = session.get_open_orders(category="linear", symbol=symbol, orderId=st["pending_id"])
        orders = res["result"]["list"] if res.get("retCode") == 0 else []
    except Exception:
        return True
    if not orders:
        st["pending_id"] = None
        return False
    if time.time() - st["pending_time"] > ENTRY_TIMEOUT_SEC:
        try:
            session.cancel_order(category="linear", symbol=symbol, orderId=st["pending_id"])
            log(f"⏱️ [{symbol}] Limit орындалмады, жойылды")
        except Exception:
            pass
        st["pending_id"] = None
        return False
    return True


# ------------------------------------------------------------------------------
# Позицияны басқару
# ------------------------------------------------------------------------------
def sync_state(symbol, pos):
    st = symbol_state[symbol]
    avg, size = float(pos["avgPrice"]), float(pos["size"])
    sl = float(pos["stopLoss"]) if pos.get("stopLoss") else 0.0
    if not st["active"]:
        be = sl > 0 and ((pos["side"] == "Buy" and sl >= avg) or (pos["side"] == "Sell" and sl <= avg))
        st.update({"active": True, "step": 1, "last_entry_price": avg,
                   "initial_qty": size, "break_even": be, "pending_id": None})
        log(f"✅ [{symbol}] Позиция ашылды: {pos['side']} {size} @ {avg}")
    st["avg_price"], st["total_qty"] = avg, size


def set_sl(symbol, idx, new_sl, tick):
    try:
        r = session.trading_stop(category="linear", symbol=symbol,
                                 stopLoss=format_price(new_sl, tick), positionIdx=idx)
        return r.get("retCode") == 0
    except Exception as e:
        log(f"[{symbol}] SL қатесі: {e}")
        return False


def manage_position(symbol, pos):
    st = symbol_state[symbol]
    info = get_info(symbol)
    side = pos["side"]
    d = 1 if side == "Buy" else -1
    idx = 1 if side == "Buy" else 2
    avg = st["avg_price"]
    price = float(pos["markPrice"])
    cur_sl = float(pos["stopLoss"]) if pos.get("stopLoss") else 0.0
    profit = (price - avg) / avg * d

    def better(new, old):
        return old == 0 or (new > old if d == 1 else new < old)

    if not st["break_even"]:
        if profit >= BREAK_EVEN_TRIGGER_PCT:
            new_sl = avg * (1 + BREAK_EVEN_LOCK_PCT * d)
            if better(new_sl, cur_sl) and set_sl(symbol, idx, new_sl, info["tick"]):
                st["break_even"] = True
                log(f"🛡️ [{symbol}] BREAK-EVEN | SL {new_sl:.4f}")
    else:
        new_sl = price * (1 - TRAILING_OFFSET_PCT * d)
        if better(new_sl, cur_sl) and (cur_sl == 0 or abs(new_sl - cur_sl) / price >= SL_UPDATE_THRESHOLD_PCT):
            if set_sl(symbol, idx, new_sl, info["tick"]):
                log(f"📈 [{symbol}] TRAILING SL {new_sl:.4f}")

    if st["break_even"] and st["step"] < MAX_PYRAMID_STEPS:
        moved = (price - st["last_entry_price"]) / st["last_entry_price"] * d
        if moved >= ADD_STEP_PCT:
            qty = float(format_qty_floor(st["initial_qty"] * PYRAMID_SIZE_RATIO, info["qty_step"]))
            if qty < info["min_qty"] or qty * price < info["min_notional"]:
                return
            try:
                r = session.place_order(category="linear", symbol=symbol, side=side, orderType="Market",
                                        qty=format_qty_floor(qty, info["qty_step"]), positionIdx=idx)
                if r.get("retCode") == 0:
                    st["step"] += 1
                    st["last_entry_price"] = price
                    log(f"🔥 [{symbol}] ПИРАМИДИНГ {st['step']}/{MAX_PYRAMID_STEPS}")
                else:
                    log(f"[{symbol}] Пирамидинг қатесі: {r.get('retMsg')}")
            except Exception as e:
                log(f"[{symbol}] Пирамидинг қатесі: {e}")


def on_position_closed(symbol):
    st = symbol_state[symbol]
    pnl = log_closed_trade(symbol)
    candles = COOLDOWN_LOSS_CANDLES if pnl < 0 else COOLDOWN_WIN_CANDLES
    log(f"🏁 [{symbol}] Жабылды | PnL {pnl:+.3f}$ | пауза {candles} шам")
    st.update({"active": False, "step": 0, "last_entry_price": 0.0, "avg_price": 0.0,
               "total_qty": 0.0, "initial_qty": 0.0, "break_even": False,
               "cooldown_until": time.time() + candles * CANDLE_SEC})


# ------------------------------------------------------------------------------
# Негізгі цикл
# ------------------------------------------------------------------------------
def get_active_positions():
    try:
        res = session.get_positions(category="linear", settleCoin="USDT")
        if res.get("retCode") == 0:
            return [p for p in res["result"]["list"] if float(p["size"]) > 0]
    except Exception as e:
        log(f"Позиция оқу қатесі: {e}")
    return None


def main():
    log("🚀 SCALP BOT v3 ІСКЕ ҚОСЫЛДЫ")
    setup_exchange_modes()

    while True:
        try:
            positions = get_active_positions()
            if positions is None:
                time.sleep(5)
                continue

            busy = {p["symbol"] for p in positions}
            busy |= {s for s in SYMBOLS if symbol_state[s]["pending_id"]}

            for symbol in SYMBOLS:
                st = symbol_state[symbol]
                pos = next((p for p in positions if p["symbol"] == symbol), None)

                if pos:
                    sync_state(symbol, pos)
                    manage_position(symbol, pos)
                    continue

                if st["active"]:
                    on_position_closed(symbol)

                if st["pending_id"]:
                    if handle_pending(symbol):
                        continue
                    busy.discard(symbol)

                if len(busy) >= MAX_OPEN_POSITIONS or time.time() < st["cooldown_until"]:
                    continue

                boundary = int(time.time() // CANDLE_SEC)
                if st["last_candle"] == boundary or time.time() % CANDLE_SEC < 3:
                    continue
                st["last_candle"] = boundary

                if not trading_allowed():
                    continue

                sig = check_entry_signal(symbol)
                if sig and open_initial_position(symbol, sig[0], sig[2]):
                    busy.add(symbol)

            time.sleep(5)
        except Exception as e:
            log(f"Цикл қатесі: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
