"""CLI:  python -m xauusd_agents demo | backtest --csv F | signal --csv F"""
from __future__ import annotations

import argparse

import pandas as pd

from .backtest import Config, run_backtest
from .committee import Committee
from .data import load_csv, synthetic_xauusd


def main():
    p = argparse.ArgumentParser(prog="xauusd_agents")
    p.add_argument("cmd", choices=["demo", "backtest", "signal", "mt5", "export", "optimize", "news"])
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
    p.add_argument("--report", action="store_true", help="backtest: monthly, long/short and buy&hold breakdown")
    p.add_argument("--split", help="optimize: first date of the TEST period, e.g. 2026-07-01")
    p.add_argument("--sl-atr", type=float, default=1.5, help="stop distance in ATR multiples")
    p.add_argument("--tp-atr", type=float, default=3.0, help="target distance in ATR multiples")
    p.add_argument("--agents", type=int, choices=[10, 12], default=10, help="10 = validated set; 12 adds Inside Bar + Initial Balance")
    p.add_argument("--ib-bars", type=int, default=2, help="Initial Balance length in bars (H1: 2 = first 2 hours)")
    p.add_argument("--ib-start-hour", type=int, default=0, help="session open hour in your broker's server clock")
    p.add_argument("--news", action="store_true", help="mt5: block new trades around high-impact USD news")
    p.add_argument("--news-before", type=int, default=30, help="minutes before an event")
    p.add_argument("--news-after", type=int, default=30, help="minutes after an event")
    p.add_argument("--magic", type=int, default=26001, help="mt5: order tag; use a different one per bot when running several")
    a = p.parse_args()

    from .agents import default_agents, extended_agents
    agents = extended_agents(a.ib_bars, a.ib_start_hour) if a.agents == 12 else default_agents()
    make_com = lambda: Committee(agents, threshold=a.threshold)

    if a.cmd == "news":
        from .news import NewsFilter
        nf = NewsFilter(a.news_before, a.news_after)
        ev = nf.upcoming(10)
        if nf.loaded_at is None:
            raise SystemExit(f"Could not load the calendar: {getattr(nf, 'last_error', '?')}")
        print(f"{len(nf.events)} high-impact USD events this week. Next ones (UTC):")
        for e in ev:
            print(f"  {e['time']:%a %Y-%m-%d %H:%M}  {e['title']}")
        print("Blocked right now:", nf.blocked())
        return

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
        from .news import NewsFilter
        news = NewsFilter(a.news_before, a.news_after) if a.news else None
        Mt5Bot(mt5, a.symbol, a.timeframe, make_com(), news=news, risk_pct=a.risk,
               sl_atr=a.sl_atr, tp_atr=a.tp_atr, magic=a.magic, dry_run=not a.live_orders).run()
        return

    df = synthetic_xauusd() if a.cmd == "demo" else load_csv(a.csv)
    if a.end:
        df = df[: pd.Timestamp(a.end) + pd.Timedelta(days=1)]
    if a.cmd == "demo":
        print("NOTE: demo uses SYNTHETIC data, not real gold prices.\n")
    com = make_com()

    if a.cmd == "optimize":
        from .optimize import sweep
        if not (a.start and a.split):
            raise SystemExit("optimize needs --from (train start) and --split (test start)")
        res = sweep(df, a.start, a.split, spread=a.spread, risk=a.risk, agents=agents)
        if res.empty:
            raise SystemExit("No setting produced >= 20 trades in both periods.")
        print(f"Train: {a.start} -> {a.split} | Test: {a.split} -> {df.index[-1]} | sorted by WORST of the two profit factors")
        print("(default settings are thr=0.8 sl=1.5 tp=3.0)\n")
        print(res.head(12).to_string(index=False))
        print("\nOnly trust a setting that is good in BOTH periods AND close to its neighbours in the table.")
        return

    if a.cmd == "signal":
        d = com.decide(df)
        print(f"Decision: {d.action}  (BUY {d.buy} / SELL {d.sell} / NEUTRAL {d.neutral}; need {d.required})")
        for k, v in d.votes.items():
            print(f"  {k:24s} {'BUY' if v == 1 else 'SELL' if v == -1 else '-'}")
        return
    trades, _, stats = run_backtest(df, com, Config(risk_pct=a.risk, spread=a.spread, sl_atr=a.sl_atr, tp_atr=a.tp_atr), start=a.start)
    if a.start:
        print(f"Trading from {a.start} to {df.index[-1]} ({len(df)} candles loaded incl. warm-up)")
    print(f"Committee needs {com.required}/{len(com.agents)} agents to agree")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if a.report and len(trades):
        t = trades.copy()
        t["month"] = df.index[t.exit_i].to_period("M")
        m = t.groupby("month").pnl.agg(trades="count", net="sum", wins=lambda x: (x > 0).sum())
        print("\nMonthly (by exit date):\n" + m.round(2).to_string())
        print("\nBy direction:")
        print(t.assign(side=t["dir"].map({1: "LONG", -1: "SHORT"})).groupby("side").pnl
              .agg(trades="count", net="sum", avg="mean").round(2).to_string())
        d = df[df.index >= pd.Timestamp(a.start)] if a.start else df
        print(f"\nBuy&hold gold same period: {100 * (d.close.iloc[-1] / d.open.iloc[0] - 1):.1f}%  "
              f"({d.open.iloc[0]:.2f} -> {d.close.iloc[-1]:.2f})")


if __name__ == "__main__":
    main()
