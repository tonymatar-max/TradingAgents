"""Opening-range breakout bot for Exness MT5 (US stocks + index CFDs, New York session).

  python orb_bot.py                    DRY RUN: logs what it would do, sends nothing
  python orb_bot.py --live-orders      sends orders - refuses unless the MT5 account is DEMO

Gates (like day-trader-skill's risk check): $50 max loss per trade, max 3 trades/day,
stop for the day after -$150, one trade per symbol per day, no leverage beyond cash,
everything flat at 15:55 New York time. Orders tagged with magic 26010.
"""
import argparse, datetime as dt, json, logging, math, os, time

import MetaTrader5 as mt5
import pandas as pd

from orb import NY, FLAT_AT, OR_END, OR_START, Params, live_state, session_trade

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


STATUS = os.path.join(HERE, "logs", "status_orb.json")
PAPER = os.path.join(HERE, "logs", "orb_paper.jsonl")


def symbol_view(sym, day):
    """What the portal shows for one symbol: today's bars, the opening range and today's (paper) trade so far."""
    v = {"symbol": sym, "bars": [], "range_high": None, "range_low": None, "trade": None}
    if day is None or day.empty:
        v["state"] = "no bars today"
        return v
    t = day.index.strftime("%H:%M")
    v["bars"] = [{"t": f"{i:%H:%M}", "o": r.open, "h": r.high, "l": r.low, "c": r.close} for i, r in day.iterrows()]
    rng = day[(t >= OR_START) & (t < OR_END)]
    if len(rng) >= 3:
        v["range_high"], v["range_low"] = float(rng.high.max()), float(rng.low.min())
    tr = session_trade(day, P)
    if tr:
        done = tr["reason"] != "CLOSE" or now_ny().strftime("%H:%M") >= FLAT_AT
        v["trade"] = {**{k: (f"{x:%H:%M}" if hasattr(x, "strftime") else x) for k, x in tr.items()},
                      "open": not done, "usd": round(tr["R"] * a.max_loss, 2)}
        v["state"] = ("OPEN " if not done else "CLOSED ") + tr["side"]
    elif v["range_high"] is None:
        v["state"] = "range forming (09:30-09:45 NY)" if t[-1] >= OR_START else "waiting for 09:30 NY"
    elif t[-1] >= P.entry_cutoff:
        v["state"] = "no breakout today"
    else:
        v["state"] = "watching for a breakout"
    return v


def write_status(views, acct, note=""):
    st = {"updated": time.time(), "ny_time": now_ny().strftime("%Y-%m-%d %H:%M"), "mode": "LIVE ORDERS (demo)" if a.live_orders else "DRY RUN",
          "account": acct.login, "balance": acct.balance, "max_loss": a.max_loss, "target_r": a.target_r,
          "short": a.short, "magic": a.magic, "note": note, "symbols": views}
    tmp = STATUS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, default=str)
    os.replace(tmp, STATUS)


def record_day(views, date):
    """At 15:55 NY: save each symbol's paper result for the day (the portal's history)."""
    with open(PAPER, "a", encoding="utf-8") as f:
        for v in views:
            if v.get("trade"):
                f.write(json.dumps({"date": str(date), "symbol": v["symbol"], **v["trade"]}, default=str) + "\n")


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
        views = []
        if t.weekday() < 5:
            if t.strftime("%H:%M") >= FLAT_AT and flat_done != t.date():
                flatten(); flat_done = t.date(); log("15:55 New York: flat for the day")
                try:
                    record_day([symbol_view(s, today_bars(s)) for s in SYMS], t.date())
                except Exception as e:  # noqa: BLE001 - the history is a convenience, never stop trading for it
                    log(f"could not record the day: {e}")
            for sym in SYMS:
                day = today_bars(sym)
                try:
                    views.append(symbol_view(sym, day))
                except Exception as e:  # noqa: BLE001
                    views.append({"symbol": sym, "state": f"error: {e}", "bars": []})
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
        try:
            write_status(views, mt5.account_info() or acct, "" if t.weekday() < 5 else "weekend: market closed")
        except Exception as e:  # noqa: BLE001
            log(f"could not write status: {e}")
        time.sleep(20)


if __name__ == "__main__":
    main()
