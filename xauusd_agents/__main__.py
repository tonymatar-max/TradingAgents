"""CLI:  python -m xauusd_agents demo | backtest --csv F | signal --csv F"""
from __future__ import annotations

import argparse

import pandas as pd

from .backtest import Config, run_backtest
from .committee import Committee
from .data import load_csv, synthetic_xauusd


def main():
    p = argparse.ArgumentParser(prog="xauusd_agents")
    p.add_argument("cmd", choices=["demo", "backtest", "signal", "mt5", "export", "optimize", "news", "tfscan", "confluence"])
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
    p.add_argument("--tfs", default="M15,M30,H1,H4", help="tfscan: comma list of timeframes")
    p.add_argument("--htf-min", type=int, default=0, help="require >= N of 10 agents on the higher timeframe to agree (0 = off)")
    p.add_argument("--htf-tf", default="H4", choices=["H4", "D1"], help="higher timeframe")
    p.add_argument("--htf-csv", help="optional CSV of the higher timeframe (else it is built from the main CSV)")
    p.add_argument("--htf-mins", default="0,5,6,7,8,9", help="confluence: values of --htf-min to compare")
    p.add_argument("--balance", type=float, default=10_000.0, help="backtest: starting balance in USD")
    p.add_argument("--lots", type=float, default=None, help="backtest: fixed lot size per trade (1 lot = 100 oz); default = risk-based sizing")
    p.add_argument("--leverage", type=float, default=0.0, help="backtest: account leverage, e.g. 100 for 1:100 (0 = ignore margin)")
    p.add_argument("--stopout", type=float, default=0.5, help="backtest: broker stop-out level as fraction of margin (0.5 = 50%%)")
    p.add_argument("--max-risk", type=float, default=None, help="with --lots: skip trades whose stop would risk more than this fraction of equity, e.g. 0.10 (mt5 default 0.10; backtest default off; 0 = no cap)")
    p.add_argument("--list-trades", action="store_true", help="backtest: print every trade (to compare with the bot log)")
    a = p.parse_args()

    from .agents import default_agents, extended_agents
    agents = extended_agents(a.ib_bars, a.ib_start_hour) if a.agents == 12 else default_agents()
    make_com = lambda: Committee(agents, threshold=a.threshold)

    def load_htf(df_):
        from .htf import RULES, resample_ohlc
        return load_csv(a.htf_csv) if a.htf_csv else resample_ohlc(df_, RULES[a.htf_tf])

    if a.cmd == "confluence":
        from .htf import confluence_table
        if not (a.csv and a.start and a.split):
            raise SystemExit("confluence needs --csv (H1 file), --from and --split")
        ltf = load_csv(a.csv)
        res = confluence_table(ltf, load_htf(ltf), a.start, a.split, [int(x) for x in a.htf_mins.split(",")],
                               a.threshold, a.sl_atr, a.tp_atr, a.spread, a.risk, agents)
        print(f"{a.htf_tf} confluence on the main timeframe: thr={a.threshold} sl={a.sl_atr} tp={a.tp_atr} spread={a.spread}")
        print(f"train = {a.start} -> {a.split}, test = {a.split} -> end. htf_min = agents needed on {a.htf_tf}\n")
        print(res.to_string(index=False))
        return

    if a.cmd == "tfscan":
        from .tfscan import scan_mt5
        if not (a.start and a.split):
            raise SystemExit("tfscan needs --from and --split")
        res = scan_mt5(a.symbol, a.tfs.split(","), a.start, a.end, a.split, thr=a.threshold, sl=a.sl_atr,
                       tp=a.tp_atr, spread=a.spread, risk=a.risk, agents=agents,
                    htf=load_htf(df) if a.htf_min else None, htf_min=a.htf_min)
        print(f"\nSame settings on every timeframe: thr={a.threshold} sl={a.sl_atr} tp={a.tp_atr} spread={a.spread}")
        print(f"train = {a.start} -> {a.split}, test = {a.split} -> end (test never used to choose settings)\n")
        print(res.to_string(index=False))
        return

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
               sl_atr=a.sl_atr, tp_atr=a.tp_atr, magic=a.magic,
               htf_min=a.htf_min, htf_tf=a.htf_tf, fixed_lots=a.lots, max_risk_pct=0.10 if a.max_risk is None else a.max_risk, dry_run=not a.live_orders).run()
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
    sigs = None
    if a.htf_min:
        from .htf import filter_signals, htf_direction
        sigs = filter_signals(com.signals(df), htf_direction(df, load_htf(df), a.htf_min))
    trades, _, stats = run_backtest(df, com, Config(balance=a.balance, risk_pct=a.risk, spread=a.spread, sl_atr=a.sl_atr, tp_atr=a.tp_atr, fixed_lots=a.lots, max_risk_pct=a.max_risk,
                                                  leverage=a.leverage, stopout_pct=a.stopout), start=a.start, signals=sigs)
    if a.start:
        print(f"Trading from {a.start} to {df.index[-1]} ({len(df)} candles loaded incl. warm-up)")
    print(f"Committee needs {com.required}/{len(com.agents)} agents to agree")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if a.list_trades and len(trades):
        print("\nTrades (broker-clock candle times; entry is the open of the candle after the signal):")
        for _, r in trades.iterrows():
            print(f"  {df.index[int(r.entry_i)]}  {'BUY ' if r['dir'] == 1 else 'SELL'}  entry {r.entry:.2f}  SL {r.sl:.2f}  "
                  f"TP {r.tp:.2f}  exit {r['exit']:.2f} {r.reason:7s} pnl {r.pnl:9.2f}")
    if a.report and len(trades):
        t = trades.copy()
        t["month"] = df.index[t.exit_i].to_period("M")
        m = t.groupby("month").pnl.agg(trades="count", net="sum", wins=lambda x: (x > 0).sum())
        print("\nMonthly (by exit date):\n" + m.round(2).to_string())
        t["year"] = df.index[t.exit_i].year
        def _pf(x):
            return round(x[x > 0].sum() / abs(x[x <= 0].sum()), 2) if (x <= 0).any() and x[x <= 0].sum() else float("inf")
        yr = t.groupby("year").pnl.agg(trades="count", net="sum", win_pct=lambda x: 100 * (x > 0).mean(), pf=_pf).round(2)
        dd = df[df.index >= pd.Timestamp(a.start)] if a.start else df
        yr["gold_%"] = (dd.groupby(dd.index.year).apply(lambda g: 100 * (g.close.iloc[-1] / g.open.iloc[0] - 1))).round(1)
        print("\nBy year (gold_% = what plain buy&hold gold did that year):\n" + yr.to_string())
        print("\nBy direction:")
        print(t.assign(side=t["dir"].map({1: "LONG", -1: "SHORT"})).groupby("side").pnl
              .agg(trades="count", net="sum", avg="mean").round(2).to_string())
        w = t.nsmallest(3, "pnl")
        print("\nThree worst trades (check these candles on your MT5 chart for bad data):")
        for _, r in w.iterrows():
            print(f"  {df.index[int(r.entry_i)]}  {'LONG' if r['dir'] == 1 else 'SHORT'}  lots {r.lots}  entry {r.entry:.2f}  "
                  f"stop distance {abs(r.entry - r.sl):.2f}  exit {r['exit']:.2f} ({r.reason})  pnl {r.pnl:.2f}")
        typ = (t.entry - t.sl).abs().median()
        print(f"  typical (median) stop distance: {typ:.2f}")
        d = df[df.index >= pd.Timestamp(a.start)] if a.start else df
        print(f"\nBuy&hold gold same period: {100 * (d.close.iloc[-1] / d.open.iloc[0] - 1):.1f}%  "
              f"({d.open.iloc[0]:.2f} -> {d.close.iloc[-1]:.2f})")


if __name__ == "__main__":
    main()
