"""CLI:  python -m xauusd_agents demo | backtest --csv F | signal --csv F"""
from __future__ import annotations

import argparse

import pandas as pd

from .backtest import Config, run_backtest
from .committee import Committee
from .data import load_csv, synthetic_xauusd


def main():
    p = argparse.ArgumentParser(prog="xauusd_agents")
    p.add_argument("cmd", choices=["demo", "backtest", "signal", "mt5", "export"])
    p.add_argument("--csv", help="OHLC csv (time,open,high,low,close)")
    p.add_argument("--threshold", type=float, default=0.8, help="fraction of agents that must agree")
    p.add_argument("--risk", type=float, default=0.01)
    p.add_argument("--symbol", default="XAUUSD", help="broker gold symbol, e.g. XAUUSD / GOLD / XAUUSD.m")
    p.add_argument("--timeframe", default="H1", choices=["M1", "M5", "M15", "M30", "H1", "H4", "D1"])
    p.add_argument("--live-orders", action="store_true", help="mt5: really send orders (DEMO account only). Default is dry-run")
    p.add_argument("--from", dest="start", help="start date YYYY-MM-DD (backtest/export)")
    p.add_argument("--to", dest="end", help="end date YYYY-MM-DD (backtest/export)")
    p.add_argument("--out", default="xauusd.csv", help="export: output csv")
    p.add_argument("--spread", type=float, default=0.30, help="backtest: spread in USD per oz")
    a = p.parse_args()

    if a.cmd == "export":
        from .mt5_export import export_csv
        export_csv(a.symbol, a.timeframe, a.start, a.end, a.out)
        return

    if a.cmd == "mt5":
        import logging

        try:
            import MetaTrader5 as mt5
        except ImportError:
            raise SystemExit("Install with: pip install MetaTrader5   (Windows only, MT5 terminal must be open)")
        from .mt5_bot import Mt5Bot
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
        Mt5Bot(mt5, a.symbol, a.timeframe, Committee(threshold=a.threshold), risk_pct=a.risk,
               dry_run=not a.live_orders).run()
        return

    df = synthetic_xauusd() if a.cmd == "demo" else load_csv(a.csv)
    if a.end:
        df = df[: pd.Timestamp(a.end) + pd.Timedelta(days=1)]
    if a.cmd == "demo":
        print("NOTE: demo uses SYNTHETIC data, not real gold prices.\n")
    com = Committee(threshold=a.threshold)

    if a.cmd == "signal":
        d = com.decide(df)
        print(f"Decision: {d.action}  (BUY {d.buy} / SELL {d.sell} / NEUTRAL {d.neutral}; need {d.required})")
        for k, v in d.votes.items():
            print(f"  {k:24s} {'BUY' if v == 1 else 'SELL' if v == -1 else '-'}")
        return
    trades, _, stats = run_backtest(df, com, Config(risk_pct=a.risk, spread=a.spread), start=a.start)
    if a.start:
        print(f"Trading from {a.start} to {df.index[-1]} ({len(df)} candles loaded incl. warm-up)")
    print(f"Committee needs {com.required}/{len(com.agents)} agents to agree")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
