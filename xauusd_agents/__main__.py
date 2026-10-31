"""CLI:  python -m xauusd_agents demo | backtest --csv F | signal --csv F"""
from __future__ import annotations

import argparse

from .backtest import Config, run_backtest
from .committee import Committee
from .data import load_csv, synthetic_xauusd


def main():
    p = argparse.ArgumentParser(prog="xauusd_agents")
    p.add_argument("cmd", choices=["demo", "backtest", "signal", "mt5"])
    p.add_argument("--csv", help="OHLC csv (time,open,high,low,close)")
    p.add_argument("--threshold", type=float, default=0.8, help="fraction of agents that must agree")
    p.add_argument("--risk", type=float, default=0.01)
    p.add_argument("--symbol", default="XAUUSD", help="broker gold symbol, e.g. XAUUSD / GOLD / XAUUSD.m")
    p.add_argument("--timeframe", default="H1", choices=["M1", "M5", "M15", "M30", "H1", "H4", "D1"])
    p.add_argument("--live-orders", action="store_true", help="mt5: really send orders (DEMO account only). Default is dry-run")
    a = p.parse_args()

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
    if a.cmd == "demo":
        print("NOTE: demo uses SYNTHETIC data, not real gold prices.\n")
    com = Committee(threshold=a.threshold)

    if a.cmd == "signal":
        d = com.decide(df)
        print(f"Decision: {d.action}  (BUY {d.buy} / SELL {d.sell} / NEUTRAL {d.neutral}; need {d.required})")
        for k, v in d.votes.items():
            print(f"  {k:24s} {'BUY' if v == 1 else 'SELL' if v == -1 else '-'}")
        return
    trades, _, stats = run_backtest(df, com, Config(risk_pct=a.risk))
    print(f"Committee needs {com.required}/{len(com.agents)} agents to agree")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
