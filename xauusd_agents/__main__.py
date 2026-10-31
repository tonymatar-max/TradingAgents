"""CLI:  python -m xauusd_agents demo | backtest --csv F | signal --csv F"""
from __future__ import annotations

import argparse

from .backtest import Config, run_backtest
from .committee import Committee
from .data import load_csv, synthetic_xauusd


def main():
    p = argparse.ArgumentParser(prog="xauusd_agents")
    p.add_argument("cmd", choices=["demo", "backtest", "signal"])
    p.add_argument("--csv", help="OHLC csv (time,open,high,low,close)")
    p.add_argument("--threshold", type=float, default=0.8, help="fraction of agents that must agree")
    p.add_argument("--risk", type=float, default=0.01)
    a = p.parse_args()

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
