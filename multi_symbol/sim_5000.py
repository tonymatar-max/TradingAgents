"""What if $5,000 started on 2026-01-01 with the two demo bots' settings (with realistic costs)?"""
import os, sys
import pandas as pd
sys.path.insert(0, r"C:\Claude\tradingagents")
from xauusd_agents.backtest import Config, run_backtest
from xauusd_agents.committee import Committee

HERE = os.path.dirname(os.path.abspath(__file__))
BAL = 5000.0
BOTS = {  # symbol: (threshold, sl_atr, tp_atr, round-trip cost, contract)
    "XAUUSD": (0.9, 1.0, 3.0, 0.15, 100.0),
    "USTEC":  (0.9, 1.5, 2.0, 2.00, 1.0),
}
curves, monthly = {}, {}
for sym, (thr, sl, tp, cost, contract) in BOTS.items():
    df = pd.read_csv(os.path.join(HERE, f"{sym}_H1.csv"), parse_dates=["time"]).set_index("time")
    for risk in (0.01, 0.02):
        cfg = Config(balance=BAL, sl_atr=sl, tp_atr=tp, spread=cost, contract_oz=contract,
                     max_lots=1000.0, risk_pct=risk)
        tr, curve, st = run_backtest(df, Committee(threshold=thr), cfg, start="2026-01-01")
        curve = curve[curve.index >= "2026-01-01"]
        curves[(sym, risk)] = curve
        print(f"{sym} risk {risk:.0%}: trades {st['trades']}, win {st['win_rate_%']}%, PF {st['profit_factor']}, "
              f"final ${st['final_balance']:,.0f} ({st['return_%']:+}%), max drawdown {st['max_drawdown_%']}%, "
              f"lowest ${curve.min():,.0f}, smallest lot {tr.lots.min()}")
        if risk == 0.01:
            monthly[sym] = curve.resample("ME").last().diff().fillna(curve.resample("ME").last().iloc[0] - BAL)

for risk in (0.01, 0.02):
    # one shared $5,000 account: add both bots' P&L (approximation: no cross-compounding)
    a, b = curves[("XAUUSD", risk)], curves[("USTEC", risk)]
    idx = a.index.union(b.index)
    comb = BAL + (a.reindex(idx).ffill().fillna(BAL) - BAL) + (b.reindex(idx).ffill().fillna(BAL) - BAL)
    peak = comb.cummax()
    print(f"\nBOTH on one $5,000 account, {risk:.0%} risk each: final ${comb.iloc[-1]:,.0f} "
          f"({100*(comb.iloc[-1]/BAL-1):+.1f}%), max drawdown {100*((peak-comb)/peak).max():.1f}%, lowest ${comb.min():,.0f}")
    if risk == 0.01:
        m = comb.resample("ME").last()
        print("Month-end balance (1% risk):")
        for t, v in m.items():
            print(f"  {t:%b}: ${v:,.0f}   gold {monthly['XAUUSD'].get(t, 0):+,.0f}   USTEC {monthly['USTEC'].get(t, 0):+,.0f}")
