"""Time machine: opening-range breakout on XAUUSD, New York open and London open sessions."""
import datetime as dt, os
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import MetaTrader5 as m

from orb import Params, session_trade, to_ny
from timemachine import stats, monte_carlo

HERE = os.path.dirname(os.path.abspath(__file__))
COST = 0.15          # measured round trip on your account: $5.50/lot/side commission + 0.04 spread
SPLIT, BAL, MAX_LOSS = "2026-07-01", 5000.0, 50.0
SESSIONS = {
    "New York open 09:30 (flat 15:55 NY)": (ZoneInfo("America/New_York"), "09:30", "09:45", "12:00", "15:55"),
    "London open 08:00 (flat 16:30 London)": (ZoneInfo("Europe/London"), "08:00", "08:15", "11:00", "16:30"),
}

csv = os.path.join(HERE, "XAUUSD_M5.csv")
if not os.path.exists(csv):
    m.initialize(); m.symbol_select("XAUUSD", True)
    r = pd.DataFrame(m.copy_rates_range("XAUUSD", m.TIMEFRAME_M5, dt.datetime(2026, 1, 1), dt.datetime.now()))
    r["time"] = pd.to_datetime(r.time, unit="s")
    r[["time", "open", "high", "low", "close"]].to_csv(csv, index=False)
raw = pd.read_csv(csv, parse_dates=["time"]).set_index("time")

for name, (tz, s, e, cut, flat) in SESSIONS.items():
    df = to_ny(raw, tz)
    for short in (False, True):
        for tr_mult in (1.0, 2.0):
            p = Params(or_start=s, or_end=e, entry_cutoff=cut, flat_at=flat, allow_short=short, target_r=tr_mult)
            rows = []
            for day, g in df.groupby(df.index.date):
                if g.index[0].weekday() >= 5:
                    continue
                t = session_trade(g, p, COST)
                if t:
                    t["day"] = pd.Timestamp(day); rows.append(t)
            t = pd.DataFrame(rows)
            if t.empty:
                continue
            # $5,000 account, $50 risk per trade, 1 trade/day (gold only)
            oz = (MAX_LOSS / t.risk_per_unit).clip(upper=None)
            lots = np.floor(oz / 100 / 0.01) * 0.01                   # 1 lot = 100 oz, step 0.01
            t["pnl"] = t.R * t.risk_per_unit * lots * 100
            t["skipped"] = lots < 0.01
            acc = pd.DataFrame({"day": t.day, "pnl": t.pnl.where(~t.skipped, 0.0)})
            acc["balance"] = BAL + acc.pnl.cumsum()
            print(f"\n{name} | {'buy+sell' if short else 'buy only'} | target {tr_mult:.0f}R")
            print(f"  all     {stats(t)}")
            print(f"  Jan-Jun {stats(t[t.day < SPLIT])}")
            print(f"  Jul-Nov {stats(t[t.day >= SPLIT])}")
            print(f"  $5,000 -> ${acc.balance.iloc[-1]:,.0f} (lowest ${acc.balance.min():,.0f}), "
                  f"{int(t.skipped.sum())} skipped (stop too wide for $50 at 0.01 lot)")
            print(f"  Monte Carlo 60 sessions: {monte_carlo(acc)}")
