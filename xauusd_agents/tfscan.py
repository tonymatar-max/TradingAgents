"""Run the same settings across several timeframes and print one comparison table."""
from __future__ import annotations

import pandas as pd

from .backtest import Config, run_backtest
from .committee import Committee
from .data import load_csv


def scan_frames(frames: dict, start, split, thr=0.9, sl=1.5, tp=2.0, spread=0.30, risk=0.01, agents=None):
    rows = []
    split = pd.Timestamp(split)
    for tf, df in frames.items():
        com, cfg = Committee(agents, threshold=thr), Config(sl_atr=sl, tp_atr=tp, spread=spread, risk_pct=risk)
        table = com.vote_table(df)  # causal, so the same table serves every slice
        tr = df[df.index < split]
        res = {
            "all": run_backtest(df, com, cfg, start=start, table=table)[2],
            "train": run_backtest(tr, com, cfg, start=start, table=table.loc[tr.index])[2],
            "test": run_backtest(df, com, cfg, start=split, table=table)[2],
        }
        g = lambda k, f: res[k].get(f, float("nan"))
        rows.append({"tf": tf, "candles": len(df), "trades": g("all", "trades"),
                     "win%": g("all", "win_rate_%"), "pf": g("all", "profit_factor"),
                     "ret%": g("all", "return_%"), "dd%": g("all", "max_drawdown_%"),
                     "train_pf": g("train", "profit_factor"), "test_pf": g("test", "profit_factor"),
                     "test_trades": g("test", "trades")})
    return pd.DataFrame(rows)


def scan_mt5(symbol, tfs, start, end, split, **kw):
    from .mt5_export import export_csv
    frames = {}
    for tf in tfs:
        path = f"{symbol}_{tf}.csv"
        try:
            export_csv(symbol, tf, start, end, path)
            frames[tf] = load_csv(path)
        except SystemExit as e:
            print(f"[{tf}] skipped: {e}")
    if not frames:
        raise SystemExit("No timeframe could be exported.")
    return scan_frames(frames, start, split, **kw)
