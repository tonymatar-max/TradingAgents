"""Export historical candles from a running MT5 terminal to CSV (Windows).

Downloads in chunks: MT5 rejects very large requests with (-2, 'Terminal: Invalid params') (e.g. two years of M5)."""
from __future__ import annotations

import pandas as pd

TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
MAX_CHUNK_BARS = 20_000          # far below the terminal's 100,000-bar default limit


def warmup_bars(scale: float = 1.0) -> int:
    """Candles before the start date so the slowest indicator (EMA200 x scale) has converged (~3x its length)."""
    return int(max(300, 650 * max(1.0, scale)))


def _chunk(mt5, symbol, tf_const, t0, t1, minutes, depth=0):
    """Candles in [t0, t1). If MT5 refuses the request, retry in 4 smaller pieces (up to 3 levels)."""
    rates = mt5.copy_rates_range(symbol, tf_const, t0.to_pydatetime(), t1.to_pydatetime())
    if rates is not None:
        return [pd.DataFrame(rates)] if len(rates) else [], 0
    if depth >= 3:
        return [], 1
    step = (t1 - t0) / 4
    parts, gaps = [], 0
    for i in range(4):
        p, g = _chunk(mt5, symbol, tf_const, t0 + step * i, t0 + step * (i + 1), minutes, depth + 1)
        parts += p; gaps += g
    return parts, gaps


def export_csv(symbol, timeframe, start, end, out, scale: float = 1.0):
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()} (open + log in to the terminal first)")
    try:
        mt5.symbol_select(symbol, True)
        minutes = TF_MIN[timeframe]
        tf_const = getattr(mt5, f"TIMEFRAME_{timeframe}")
        d0 = pd.Timestamp(start) - pd.Timedelta(minutes=minutes * warmup_bars(scale) * 7 / 5)   # x7/5: weekends
        d1 = (pd.Timestamp(end) if end else pd.Timestamp.now()) + pd.Timedelta(days=1)
        span = pd.Timedelta(minutes=minutes * MAX_CHUNK_BARS)
        parts, gaps, t = [], 0, d0
        while t < d1:
            t2 = min(t + span, d1)
            p, g = _chunk(mt5, symbol, tf_const, t, t2, minutes)
            parts += p; gaps += g
            t = t2
        if not parts:
            raise SystemExit(f"No candles returned ({mt5.last_error()}). Scroll the {timeframe} chart back to the start date in MT5 "
                             "(and raise Tools > Options > Charts > Max bars in chart), then retry.")
        df = pd.concat(parts, ignore_index=True)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.drop_duplicates("time").sort_values("time")
        df[["time", "open", "high", "low", "close"]].to_csv(out, index=False)
        print(f"Saved {len(df)} candles {df.time.iloc[0]} -> {df.time.iloc[-1]} to {out}")
        if gaps:
            print(f"WARNING: MT5 refused {gaps} date range(s); the file has gaps there. Scroll the chart back and retry.")
        if df.time.iloc[0] > pd.Timestamp(start):
            print("WARNING: history starts after your start date. In MT5 raise Tools > Options > Charts > Max bars in chart "
                  "(restart MT5), scroll the chart back, and retry.")
    finally:
        mt5.shutdown()
