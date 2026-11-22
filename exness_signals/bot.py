"""Exness (MetaTrader 5) signal scanner with optional, guarded order execution.

Usage:
  python bot.py symbols            list symbols on your Exness account
  python bot.py scan               scan watchlist once, alert on signals
  python bot.py run                scan every new bar, forever (Ctrl+C to stop)
  python bot.py --json scan        machine-readable output (for agents / CLI-Anything)

Requires the MetaTrader 5 terminal installed and logged in to your Exness account.
The bot never handles your password: it attaches to the already-logged-in terminal.
"""
import argparse, datetime as dt, json, os, sys, time, urllib.parse, urllib.request

import MetaTrader5 as mt5
import pandas as pd
import yaml

from strategy import evaluate

HERE = os.path.dirname(os.path.abspath(__file__))
TF = {"M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1,
      "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1}
TF_SEC = {"M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


def load_cfg():
    with open(os.path.join(HERE, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def connect():
    if not mt5.initialize():
        sys.exit(f"MT5 initialize failed: {mt5.last_error()} - is the terminal open and logged in?")
    return mt5.account_info()


def bars(symbol, tf, n):
    mt5.symbol_select(symbol, True)
    r = mt5.copy_rates_from_pos(symbol, TF[tf], 1, n)  # start=1 -> closed bars only
    if r is None or len(r) == 0:
        return None
    df = pd.DataFrame(r)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df.set_index("time")


# ---------------- alerts ----------------
def telegram(text):
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=10)
    except Exception as e:
        print(f"[telegram error] {e}", file=sys.stderr)


def fmt(sym, s, digits):
    return (f"{'🟢' if s['side']=='BUY' else '🔴'} {s['side']} {sym} @ {s['price']:.{digits}f}\n"
            f"SL {s['sl']:.{digits}f} | TP {s['tp']:.{digits}f} | RSI {s['rsi']} | bar {s['time']}")


# ---------------- execution (guarded) ----------------
def todays_pnl(magic):
    start = dt.datetime.combine(dt.date.today(), dt.time())
    deals = mt5.history_deals_get(start, dt.datetime.now()) or []
    return sum(d.profit + d.swap + d.commission for d in deals if d.magic == magic)


def lot_size(sym, entry, sl, risk_money):
    info = mt5.symbol_info(sym)
    ticks = abs(entry - sl) / info.trade_tick_size
    loss_per_lot = ticks * info.trade_tick_value
    if loss_per_lot <= 0:
        return 0.0
    vol = risk_money / loss_per_lot
    vol = max(info.volume_min, min(info.volume_max, round(vol / info.volume_step) * info.volume_step))
    return round(vol, 2)


def execute(sym, s, ex, acct):
    if ex["demo_only"] and acct.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        return "blocked: account is not DEMO (demo_only=true)"
    mine = [p for p in (mt5.positions_get() or []) if p.magic == ex["magic"]]
    if len(mine) >= ex["max_open_positions"]:
        return "blocked: max_open_positions reached"
    if any(p.symbol == sym for p in mine):
        return "blocked: already in a position on this symbol"
    if todays_pnl(ex["magic"]) <= -acct.balance * ex["max_daily_loss_pct"] / 100:
        return "blocked: daily loss limit hit"

    tick = mt5.symbol_info_tick(sym)
    buy = s["side"] == "BUY"
    price = tick.ask if buy else tick.bid
    shift = price - s["price"]                       # keep SL/TP distances from live price
    sl, tp = s["sl"] + shift, s["tp"] + shift
    vol = lot_size(sym, price, sl, acct.balance * ex["risk_per_trade_pct"] / 100)
    if vol <= 0:
        return "blocked: lot size 0"
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": vol,
           "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, "price": price,
           "sl": sl, "tp": tp, "deviation": 20, "magic": ex["magic"], "comment": "signal-bot",
           "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC}
    r = mt5.order_send(req)
    if r is None or r.retcode != mt5.TRADE_RETCODE_DONE:
        return f"order failed: {getattr(r, 'retcode', None)} {getattr(r, 'comment', mt5.last_error())}"
    return f"FILLED {vol} lots, ticket {r.order}"


# ---------------- commands ----------------
def scan(cfg, acct, as_json=False):
    out = []
    for sym in cfg["watchlist"]:
        df = bars(sym, cfg["timeframe"], cfg["bars"])
        if df is None:
            out.append({"symbol": sym, "error": "no data (check symbol name)"})
            continue
        s = evaluate(df, cfg["strategy"])
        row = {"symbol": sym, "signal": s}
        if s:
            msg = fmt(sym, s, mt5.symbol_info(sym).digits)
            if cfg["execution"]["enabled"]:
                row["execution"] = execute(sym, s, cfg["execution"], acct)
                msg += f"\n⚙️ {row['execution']}"
            if cfg["alerts"].get("telegram"):
                telegram(msg)
            if cfg["alerts"].get("console") and not as_json:
                print(msg + "\n")
        out.append(row)
    if as_json:
        print(json.dumps(out, indent=2, default=str))
    elif not any(r.get("signal") for r in out):
        print(f"{dt.datetime.now():%H:%M} no signals on {len(out)} symbols")
    for r in out:
        if "error" in r and not as_json:
            print(f"  ! {r['symbol']}: {r['error']}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("cmd", choices=["symbols", "scan", "run"])
    a = ap.parse_args()
    cfg = load_cfg()
    acct = connect()
    mode = "DEMO" if acct.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "REAL"
    if not a.json:
        print(f"Connected: {acct.server} #{acct.login} ({mode}) balance {acct.balance} {acct.currency}"
              f" | execution {'ON' if cfg['execution']['enabled'] else 'OFF (signals only)'}")
    try:
        if a.cmd == "symbols":
            names = sorted(s.name for s in mt5.symbols_get())
            print(json.dumps(names) if a.json else "\n".join(names))
        elif a.cmd == "scan":
            scan(cfg, acct, a.json)
        else:
            step = TF_SEC[cfg["timeframe"]]
            while True:
                scan(cfg, mt5.account_info(), a.json)
                time.sleep(step - time.time() % step + 5)  # wake just after next bar closes
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
