"""Export historical candles from a running MT5 terminal to CSV (Windows)."""
from __future__ import annotations

import pandas as pd

TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
WARMUP_BARS = 300  # for EMA200 etc.


def export_csv(symbol, timeframe, start, end, out):
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()} (open + log in to the terminal first)")
    try:
        mt5.symbol_select(symbol, True)
        d0 = pd.Timestamp(start) - pd.Timedelta(minutes=TF_MIN[timeframe] * WARMUP_BARS * 7 / 5)  # weekends
        d1 = (pd.Timestamp(end) if end else pd.Timestamp.now()) + pd.Timedelta(days=1)
        rates = mt5.copy_rates_range(symbol, getattr(mt5, f"TIMEFRAME_{timeframe}"),
                                     d0.to_pydatetime(), d1.to_pydatetime())
        if rates is None or len(rates) == 0:
            raise SystemExit(f"No candles returned ({mt5.last_error()}). Scroll the {timeframe} chart back "
                             "to the start date in MT5 to download history, then retry.")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df[["time", "open", "high", "low", "close"]].to_csv(out, index=False)
        print(f"Saved {len(df)} candles {df.time.iloc[0]} -> {df.time.iloc[-1]} to {out}")
        if df.time.iloc[0] > pd.Timestamp(start):
            print("WARNING: history starts after your start date - scroll the chart back in MT5 and retry.")
    finally:
        mt5.shutdown()
