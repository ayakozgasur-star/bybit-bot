import csv
import logging
import os
from datetime import datetime, timezone
import time

import pandas as pd
from pybit.unified_trading import HTTP

# ================= SETTINGS =================
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
CATEGORY = "linear"

# API кілттері тікелей жүйелік орта айнымалыларынан (Environment Variables) оқылады
API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()

# Алдымен false қалдырыңыз! (Тест үшін)
TRADING_ENABLED = (
    os.getenv("BOT_TRADING_ENABLED", "false").lower() == "true"
)

MARGIN_USDT = 10.0
LEVERAGE = 3

TP_PCT = 0.005  # 0.50%
SL_PCT = 0.003  # 0.30%

# 0.055% әр бағытқа — тек резервтік мән.
FALLBACK_TAKER_FEE = 0.00055

# Әр бағыттағы сырғудың болжамы
SLIPPAGE_PER_SIDE = 0.0002

# 0.08% асқан спредте сауда жасамау
MAX_SPREAD_PCT = 0.0008

# TP кезіндегі минималды болжамды таза пайда
MIN_EXPECTED_NET = 0.03

POLL_SECONDS = 5
CSV_FILE = "trades.csv"
LOG_FILE = "scalper.log"

# EMA 5/13: 1 минуттық шамдар
EMA_FAST = 5
EMA_SLOW = 13
RSI_PERIOD = 7
VOLUME_PERIOD = 20

# ============================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

log = logging.getLogger("BybitDemoScalper")


def floor_step(value, step):
  from decimal import Decimal, ROUND_DOWN

  val_dec = Decimal(str(value))
  step_dec = Decimal(str(step))
  return format(
      (val_dec / step_dec).to_integral_value(rounding=ROUND_DOWN) * step_dec,
      "f",
  )


def utc_now():
  return datetime.now(timezone.utc).isoformat()


class DemoScalper:

  def __init__(self):
    if not API_KEY or not API_SECRET:
      raise RuntimeError(
          "BYBIT_API_KEY және BYBIT_API_SECRET орта айнымалыларында"
          " (Environment Variables) көрсетілуі тиіс."
      )

    self.api = HTTP(
        testnet=False, demo=True, api_key=API_KEY, api_secret=API_SECRET
    )

    self.qty_step = "0.001"
    self.min_qty = 0.001
    self.tick_size = "0.1"

    self.fee_rate = FALLBACK_TAKER_FEE
    self.last_candle = None
    self.seen_closed = set()

    self.total_net = 0.0
    self.total_fees = 0.0
    self.wins = 0
    self.losses = 0
    self.closed_trades = 0

    self.prepare_csv()
    self.load_instrument()
    self.load_fee()

    try:
      self.api.set_leverage(
          category=CATEGORY,
          symbol=SYMBOL,
          buyLeverage=str(LEVERAGE),
          sellLeverage=str(LEVERAGE),
      )
    except Exception as exc:
      log.warning("Leverage өзгертілмеді немесе бұрын орнатылған: %s", exc)

  def prepare_csv(self):
    if not os.path.exists(CSV_FILE):
      with open(CSV_FILE, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([
            "utc_time",
            "symbol",
            "side",
            "qty",
            "entry_price",
            "exit_price",
            "closed_pnl",
            "open_fee",
            "close_fee",
            "funding_fee",
        ])

  def load_instrument(self):
    response = self.api.get_instruments_info(category=CATEGORY, symbol=SYMBOL)

    instruments = response["result"]["list"]
    if not instruments:
      raise RuntimeError(f"Bybit инструменті табылмады: {SYMBOL}")

    info = instruments[0]
    lot = info["lotSizeFilter"]
    price = info["priceFilter"]

    self.qty_step = lot["qtyStep"]
    self.min_qty = float(lot["minOrderQty"])
    self.tick_size = price["tickSize"]

    log.info(
        "Instrument=%s qty_step=%s min_qty=%s tick=%s",
        SYMBOL,
        self.qty_step,
        self.min_qty,
        self.tick_size,
    )

  def load_fee(self):
    try:
      response = self.api.get_fee_rates(category=CATEGORY, symbol=SYMBOL)
      item = response["result"]["list"][0]
      self.fee_rate = float(item["takerFeeRate"])
      log.info("API taker fee: %.5f%%", self.fee_rate * 100)
    except Exception as exc:
      self.fee_rate = FALLBACK_TAKER_FEE
      log.warning(
          "API комиссиясы алынбады; резервтік %.5f%%: %s",
          self.fee_rate * 100,
          exc,
      )

  def get_market(self):
    response = self.api.get_tickers(category=CATEGORY, symbol=SYMBOL)
    item = response["result"]["list"][0]

    bid = float(item["bid1Price"])
    ask = float(item["ask1Price"])
    last = float(item["lastPrice"])

    if min(bid, ask, last) <= 0:
      raise RuntimeError("Баға деректері жарамсыз")

    mid = (bid + ask) / 2
    spread = (ask - bid) / mid

    return {"bid": bid, "ask": ask, "last": last, "spread": spread}

  def get_candles(self):
    response = self.api.get_kline(
        category=CATEGORY, symbol=SYMBOL, interval="1", limit=100
    )

    rows = sorted(response["result"]["list"], key=lambda row: int(row[0]))

    now_ms = int(time.time() * 1000)
    rows = [row for row in rows if int(row[0]) + 60000 <= now_ms]

    if len(rows) < 30:
      return None

    df = pd.DataFrame(
        rows,
        columns=["start", "open", "high", "low", "close", "volume", "turnover"],
    )

    for column in ["close", "volume"]:
      df[column] = df[column].astype(float)

    df["start"] = df["start"].astype("int64")

    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()

    rs = gain / loss.replace(0, 1e-12)
    df["rsi"] = 100 - 100 / (1 + rs)

    df["volume_avg"] = df["volume"].rolling(VOLUME_PERIOD).mean()

    return df.reset_index(drop=True)

  def get_signal(self, df):
    previous = df.iloc[-2]
    current = df.iloc[-1]

    candle_id = int(current["start"])
    if candle_id == self.last_candle:
      return None

    self.last_candle = candle_id

    volume_ok = current["volume"] >= current["volume_avg"] * 0.8

    cross_up = (
        previous["ema_fast"] <= previous["ema_slow"]
        and current["ema_fast"] > current["ema_slow"]
    )

    cross_down = (
        previous["ema_fast"] >= previous["ema_slow"]
        and current["ema_fast"] < current["ema_slow"]
    )

    if volume_ok and cross_up and 45 <= current["rsi"] <= 72:
      return "Buy"

    if volume_ok and cross_down and 28 <= current["rsi"] <= 55:
      return "Sell"

    return None

  def get_positions(self):
    response = self.api.get_positions(category=CATEGORY, symbol=SYMBOL)
    return [
        p
        for p in response["result"]["list"]
        if float(p.get("size", 0) or 0) > 0
    ]

  def estimate_costs(self, notional, spread):
    fees = notional * self.fee_rate * 2
    spread_cost = notional * spread
    slippage = notional * SLIPPAGE_PER_SIDE * 2
    total = fees + spread_cost + slippage
    return fees, spread_cost, slippage, total

  def send_entry(self, side, market):
    spread = market["spread"]

    if spread > MAX_SPREAD_PCT:
      log.info("Сауда өткізіліп жіберілді: спред %.4f%%", spread * 100)
      return

    reference_price = market["ask"] if side == "Buy" else market["bid"]

    notional_target = MARGIN_USDT * LEVERAGE
    qty = floor_step(notional_target / reference_price, self.qty_step)

    if float(qty) < self.min_qty:
      log.warning("Ордер көлемі минималды мөлшерден аз: %s", qty)
      return

    notional = float(qty) * reference_price

    fees, spread_cost, slippage, total_cost = self.estimate_costs(
        notional, spread
    )

    gross_tp = notional * TP_PCT
    estimated_net = gross_tp - total_cost

    log.info(
        "%s | көлем=$%.2f | комиссия≈$%.4f | спред≈$%.4f | сырғу≈$%.4f | TP"
        " жалпы=$%.4f | TP таза≈$%.4f",
        side,
        notional,
        fees,
        spread_cost,
        slippage,
        gross_tp,
        estimated_net,
    )

    if estimated_net < MIN_EXPECTED_NET:
      log.info(
          "Сауда жоқ: болжамды таза пайда $%.4f шектен төмен", estimated_net
      )
      return

    if not TRADING_ENABLED:
      log.info("ҚАУІПСІЗ РЕЖИМ: ордер жіберілген жоқ.")
      return

    if self.get_positions():
      log.info("Ашық позиция бар, жаңа ордер жіберілмейді.")
      return

    tp_price = reference_price * (1 + TP_PCT if side == "Buy" else 1 - TP_PCT)
    sl_price = reference_price * (1 - SL_PCT if side == "Buy" else 1 + SL_PCT)

    tp = floor_step(tp_price, self.tick_size)
    sl = floor_step(sl_price, self.tick_size)

    response = self.api.place_order(
        category=CATEGORY,
        symbol=SYMBOL,
        side=side,
        orderType="Market",
        qty=qty,
        positionIdx=0,
        takeProfit=tp,
        stopLoss=sl,
        tpTriggerBy="MarkPrice",
        slTriggerBy="MarkPrice",
        tpslMode="Full",
        tpOrderType="Market",
        slOrderType="Market",
    )

    log.info(
        "DEMO ордері жіберілді: side=%s qty=%s TP=%s SL=%s id=%s",
        side,
        qty,
        tp,
        sl,
        response.get("result", {}).get("orderId"),
    )

  def update_closed_trades(self):
    try:
      response = self.api.get_closed_pnl(
          category=CATEGORY, symbol=SYMBOL, limit=100
      )

      for item in reversed(response["result"]["list"]):
        key = (
            str(item.get("orderId", ""))
            + ":"
            + str(item.get("createdTime", ""))
        )

        if key in self.seen_closed:
          continue

        self.seen_closed.add(key)

        net = float(item.get("closedPnl", 0) or 0)
        open_fee = float(item.get("openFee", 0) or 0)
        close_fee = float(item.get("closeFee", 0) or 0)
        funding = float(item.get("fundingFee", 0) or 0)

        self.total_net += net
        self.total_fees += open_fee + close_fee

        self.closed_trades += 1
        if net > 0:
          self.wins += 1
        elif net < 0:
          self.losses += 1

        with open(CSV_FILE, "a", newline="", encoding="utf-8") as file:
          csv.writer(file).writerow([
              utc_now(),
              SYMBOL,
              item.get("side", ""),
              item.get("qty", ""),
              item.get("avgEntryPrice", ""),
              item.get("avgExitPrice", ""),
              net,
              open_fee,
              close_fee,
              funding,
          ])

        log.info(
            "ЖАБЫЛДЫ: таза PnL=%.4f USDT, ашу/жабу комиссиясы=%.4f,"
            " funding=%.4f",
            net,
            open_fee + close_fee,
            funding,
        )

    except Exception as exc:
      log.warning("Жабылған мәмілелер тарихы алынбады: %s", exc)

  def show_stats(self):
    total = self.wins + self.losses
    winrate = 100 * self.wins / total if total else 0

    log.info(
        "СТАТИСТИКА | мәміле=%d | win=%d | loss=%d | winrate=%.1f%% |"
        " комиссия=$%.4f | таза PnL=$%.4f",
        self.closed_trades,
        self.wins,
        self.losses,
        winrate,
        self.total_fees,
        self.total_net,
    )

  def run(self):
    log.info(
        "ІСКЕ ҚОСЫЛДЫ | DEMO | %s | маржа=$%.2f | иық=%sx | TP=%.2f%% |"
        " SL=%.2f%% | сауда=%s",
        SYMBOL,
        MARGIN_USDT,
        LEVERAGE,
        TP_PCT * 100,
        SL_PCT * 100,
        TRADING_ENABLED,
    )

    last_stats = 0

    while True:
      try:
        self.update_closed_trades()

        market = self.get_market()
        df = self.get_candles()

        if df is not None:
          signal = self.get_signal(df)

          if signal:
            log.info("СИГНАЛ: %s", signal)
            self.send_entry(signal, market)

        if time.time() - last_stats >= 60:
          self.show_stats()
          last_stats = time.time()

        time.sleep(POLL_SECONDS)

      except KeyboardInterrupt:
        log.info("Бот тоқтатылды.")
        self.update_closed_trades()
        self.show_stats()
        break

      except Exception:
        log.exception("Қате шықты; бот жалғастырады.")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
  DemoScalper().run()
