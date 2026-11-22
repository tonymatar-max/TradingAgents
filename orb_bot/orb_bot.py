"""Opening-range breakout bot for Exness MT5 (US stocks + index CFDs, New York session).

  python orb_bot.py                    DRY RUN: logs what it would do, sends nothing
  python orb_bot.py --live-orders      sends orders - refuses unless the MT5 account is DEMO

Gates (like day-trader-skill's risk check): $50 max loss per trade, max 3 trades/day,
stop for the day after -$150, one trade per symbol per day, no leverage beyond cash,
everything flat at 15:55 New York time. Orders tagged with magic 26010.
"""
import argparse, datetime as dt, logging, math, os, time

import MetaTrader5 as mt5
import pandas as pd

from orb import NY, FLAT_AT, Params, live_state

ap = argparse.ArgumentParser()
ap.add_argument("--symbols", default="USTEC,US500,TSLA,NVDA,AAPL,AMZN,META,MSFT,AMD")
ap.add_argument("--max-loss", type=float, default=50.0)
ap.add_argument("--daily-loss", type=float, default=150.0)
ap.add_argument("--max-trades", type=int, default=3)
ap.add_argument("--target-r", type=float, default=1.0)
ap.add_argument("--short", action="store_true", help="also trade breakdowns below the range")
ap.add_argument("--magic", type=int, default=26010)
ap.add_argument("--live-orders", action="store_true")
a = ap.parse_args()

HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(os.path.join(HERE, "logs", "orb_bot.log"))])
log = logging.info
P = Params(target_r=a.target_r, allow_short=a.short)
SYMS = [s.strip() for s in a.symbols.split(",") if s.strip()]


def now_ny():
    return dt.datetime.now(dt.timezone.utc).astimezone(NY)


def today_bars(sym):
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 1, 200)       # pos 1 = closed bars only
    if r is None or len(r) == 0:
        return None
    df = pd.DataFrame(r)
    df.index = pd.to_datetime(df.time, unit="s").dt.tz_localize("UTC").dt.tz_convert(NY)
    return df[df.index.date == now_ny().date()]


def today_deals():
    start = now_ny().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc)
    ds = mt5.history_deals_get(start.replace(tzinfo=None), dt.datetime.utcnow() + dt.timedelta(days=1)) or []
    return [d for d in ds if d.magic == a.magic]


def gates(sym, acct):
    deals = today_deals()
    entries = [d for d in deals if d.entry == mt5.DEAL_ENTRY_IN]
    pnl = sum(d.profit + d.commission + d.swap for d in deals)
    if pnl <= -a.daily_loss:
        return f"BLOCK daily loss {pnl:.2f} <= -{a.daily_loss}"
    if len(entries) >= a.max_trades:
        return f"BLOCK {len(entries)} trades today (max {a.max_trades})"
    if any(d.symbol == sym for d in entries):
        return "BLOCK already traded this symbol today"
    return None


def lots_for(sym, entry, stop, acct):
    i = mt5.symbol_info(sym)
    per_unit_risk = abs(entry - stop)
    loss_per_lot = per_unit_risk / i.trade_tick_size * i.trade_tick_value
    lots = math.floor(a.max_loss / loss_per_lot / i.volume_step) * i.volume_step
    cash_lots = math.floor(acct.balance / (entry * i.trade_contract_size) / i.volume_step) * i.volume_step
    lots = min(lots, cash_lots, i.volume_max)
    return round(lots, 2) if lots >= i.volume_min else 0.0


def place(sym, sig, acct):
    tick = mt5.symbol_info_tick(sym)
    buy = sig["side"] == 1
    price = tick.ask if buy else tick.bid
    stop = sig["stop"]
    risk = (price - stop) if buy else (stop - price)
    if risk <= 0:
        return "skip: price already beyond the stop"
    tp = price + (1 if buy else -1) * a.target_r * risk
    lots = lots_for(sym, price, stop, acct)
    if lots <= 0:
        return f"skip: ${a.max_loss} max loss is less than the minimum lot (stop {risk:.2f} away)"
    risk_usd = lots * risk / mt5.symbol_info(sym).trade_tick_size * mt5.symbol_info(sym).trade_tick_value
    desc = f"{'BUY' if buy else 'SELL'} {lots} {sym} @ {price} SL {stop:.2f} TP {tp:.2f} [risk ${risk_usd:.0f}]"
    if not a.live_orders:
        return "DRY RUN would " + desc
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": lots, "price": price,
           "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, "sl": round(stop, 2), "tp": round(tp, 2),
           "deviation": 20, "magic": a.magic, "comment": "orb", "type_time": mt5.ORDER_TIME_GTC,
           "type_filling": mt5.ORDER_FILLING_IOC}
    r = mt5.order_send(req)
    if r is None or r.retcode != mt5.TRADE_RETCODE_DONE:
        return f"ORDER FAILED {getattr(r, 'retcode', None)} {getattr(r, 'comment', mt5.last_error())}: {desc}"
    return "PLACED " + desc


def flatten():
    for p in mt5.positions_get() or []:
        if p.magic != a.magic:
            continue
        if not a.live_orders:
            log(f"DRY RUN would close {p.symbol} #{p.ticket}"); continue
        tick = mt5.symbol_info_tick(p.symbol)
        sell = p.type == mt5.POSITION_TYPE_BUY
        r = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume, "position": p.ticket,
                            "type": mt5.ORDER_TYPE_SELL if sell else mt5.ORDER_TYPE_BUY,
                            "price": tick.bid if sell else tick.ask, "deviation": 20, "magic": a.magic,
                            "comment": "orb flat 15:55", "type_filling": mt5.ORDER_FILLING_IOC})
        log(f"Closed {p.symbol} #{p.ticket} at 15:55: {getattr(r, 'retcode', None)}")


def main():
    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    acct = mt5.account_info()
    if a.live_orders and acct.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        raise SystemExit("Refusing --live-orders: this is not a DEMO account.")
    for s in SYMS:
        if not mt5.symbol_select(s, True):
            log(f"WARNING symbol {s} not found - skipped")
    log(f"ORB bot: {acct.server} #{acct.login} {'DEMO' if acct.trade_mode == 0 else 'REAL'} balance {acct.balance} "
        f"| {'LIVE ORDERS' if a.live_orders else 'DRY RUN'} | symbols {','.join(SYMS)} | max loss ${a.max_loss}/trade")
    seen, flat_done = set(), None
    while True:
        t = now_ny()
        if t.weekday() < 5:
            if t.strftime("%H:%M") >= FLAT_AT and flat_done != t.date():
                flatten(); flat_done = t.date(); log("15:55 New York: flat for the day")
            for sym in SYMS:
                day = today_bars(sym)
                if day is None or day.empty:
                    continue
                sig = live_state(day, P)
                if not sig or (sym, sig["bar"]) in seen:
                    continue
                seen.add((sym, sig["bar"]))
                acct = mt5.account_info()
                why = gates(sym, acct)
                side = "BUY" if sig["side"] == 1 else "SELL"
                log(f"{sym} {side} breakout: bar {sig['bar']:%H:%M} close {sig['close']} range "
                    f"{sig['range_low']:.2f}-{sig['range_high']:.2f} -> " + (why or place(sym, sig, acct)))
        time.sleep(20)


if __name__ == "__main__":
    main()
