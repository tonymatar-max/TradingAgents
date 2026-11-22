"""Time machine for the ORB strategy on Exness data.

1) Historical replay: every New York session of 2026 on each symbol (M5 bars from your MT5).
2) $5,000 account replay: all symbols together, $50 max loss per trade, gates as in the live bot.
3) Monte Carlo: 10,000 random 60-session futures, built by resampling the real daily results.

  python timemachine.py             (downloads M5 history from MT5)
  python timemachine.py --cached    (reuse CSVs)
"""
import datetime as dt, os, sys
import numpy as np
import pandas as pd

from orb import Params, session_trade, to_ny

HERE = os.path.dirname(os.path.abspath(__file__))
SYMBOLS = ["TSLA", "NVDA", "AAPL", "AMZN", "META", "MSFT", "AMD", "USTEC", "US500"]
SPLIT = "2026-07-01"
START_BAL, MAX_LOSS, DAILY_LOSS, MAX_TRADES = 5000.0, 50.0, 150.0, 3
EXTRA_COST = {"USTEC": 2.0, "US500": 0.6}   # index CFDs: estimated round trip; stocks: measured spread + 0.02


def load(sym):
    csv = os.path.join(HERE, f"{sym}_M5.csv")
    if "--cached" not in sys.argv or not os.path.exists(csv):
        import MetaTrader5 as m
        m.initialize(); m.symbol_select(sym, True)
        r = pd.DataFrame(m.copy_rates_range(sym, m.TIMEFRAME_M5, dt.datetime(2026, 1, 1), dt.datetime.now()))
        r["time"] = pd.to_datetime(r.time, unit="s")
        info = m.symbol_info(sym)
        r["spread_px"] = r.spread * info.point
        r[["time", "open", "high", "low", "close", "spread_px"]].to_csv(csv, index=False)
    df = pd.read_csv(csv, parse_dates=["time"]).set_index("time")
    return to_ny(df)


def replay(p: Params, cost_mult=1.0):
    trades = []
    for sym in SYMBOLS:
        df = load(sym)
        cost = EXTRA_COST.get(sym, df.spread_px.median() + 0.02) * cost_mult
        for day, g in df.groupby(df.index.date):
            tr = session_trade(g, p, cost)
            if tr:
                tr.update(symbol=sym, day=pd.Timestamp(day))
                trades.append(tr)
    return pd.DataFrame(trades).sort_values("entry_time").reset_index(drop=True)


def stats(t):
    if t.empty:
        return {"trades": 0}
    w = t[t.R > 0].R.sum(); l = -t[t.R < 0].R.sum()
    return {"trades": len(t), "target%": round(100 * (t.reason == "TARGET").mean(), 1),
            "stop%": round(100 * (t.reason == "STOP").mean(), 1), "close%": round(100 * (t.reason == "CLOSE").mean(), 1),
            "win%": round(100 * (t.R > 0).mean(), 1), "avg_R": round(t.R.mean(), 3),
            "PF": round(w / l, 2) if l else float("inf")}


def account(t):
    """$5,000 account: $50 max loss per trade, max 3 trades/day, stop for the day after -$150."""
    bal, rows = START_BAL, []
    for day, g in t.groupby("day"):
        day_pnl, n = 0.0, 0
        for _, tr in g.iterrows():
            if n >= MAX_TRADES or day_pnl <= -DAILY_LOSS:
                break
            units = int(MAX_LOSS / tr.risk_per_unit)             # shares (1 share = 0.01 lot) / index units
            units = min(units, int(bal / tr.entry))               # no leverage beyond cash, like the original
            if units < 1:
                continue
            pnl = tr.R * tr.risk_per_unit * units
            day_pnl += pnl; n += 1
        bal += day_pnl
        rows.append({"day": day, "pnl": day_pnl, "trades": n, "balance": bal})
    return pd.DataFrame(rows)


def monte_carlo(daily, n_sims=10_000, horizon=60, seed=1):
    rng = np.random.default_rng(seed)
    pnl = daily.pnl.to_numpy()
    paths = START_BAL + np.cumsum(rng.choice(pnl, size=(n_sims, horizon)), axis=1)
    end = paths[:, -1]
    dd = (np.maximum.accumulate(paths, axis=1) - paths).max(axis=1)
    return {"median_end": round(float(np.median(end)), 0), "p5_end": round(float(np.percentile(end, 5)), 0),
            "p95_end": round(float(np.percentile(end, 95)), 0), "chance_of_loss%": round(100 * (end < START_BAL).mean(), 1),
            "median_max_drawdown$": round(float(np.median(dd)), 0), "worst_5%_drawdown$": round(float(np.percentile(dd, 95)), 0)}


def main():
    pd.set_option("display.width", 220)
    for label, p in (("LONG ONLY (original default)", Params()), ("LONG + SHORT", Params(allow_short=True))):
        print(f"\n################ {label} ################")
        for mult in (1, 2):
            t = replay(p, mult)
            tr, te = t[t.day < SPLIT], t[t.day >= SPLIT]
            print(f"\n-- cost {mult}x --  all: {stats(t)}")
            print(f"   Jan-Jun: {stats(tr)}")
            print(f"   Jul-Nov: {stats(te)}")
            if mult == 1:
                by = t.groupby("symbol").apply(lambda g: pd.Series(stats(g)), include_groups=False)
                print(by.to_string())
                acc = account(t)
                print(f"\n   $5,000 account replay: end ${acc.balance.iloc[-1]:,.0f}, "
                      f"lowest ${acc.balance.min():,.0f}, trading days {len(acc)}, trades {acc.trades.sum()}")
                print("   month-end:", {k.strftime('%b'): round(v) for k, v in acc.set_index('day').balance.resample('ME').last().items()})
                print("   Monte Carlo (10,000 x 60 sessions from $5,000):", monte_carlo(acc))
                t.to_csv(os.path.join(HERE, f"trades_{'long' if not p.allow_short else 'both'}.csv"), index=False)


if __name__ == "__main__":
    main()
