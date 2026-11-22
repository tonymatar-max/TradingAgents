"""Committee backtest on Exness crypto CFDs (BTCUSD, ETHUSD, XRPUSD), H1, with costs.
Cost = 0.08% of price round trip (estimate; account shows zero spread, commission unknown), plus 2x stress.
"""
import itertools, os, sys
import pandas as pd
sys.path.insert(0, r"C:\Claude\tradingagents")
from xauusd_agents.backtest import Config, run_backtest
from xauusd_agents.committee import Committee
from xauusd_agents.mt5_export import export_csv

HERE = os.path.dirname(os.path.abspath(__file__))
START, SPLIT, COST_PCT = "2026-01-01", "2026-07-01", 0.0008
GRID = list(itertools.product((0.7, 0.8, 0.9), (1.0, 1.5, 2.0), (2.0, 3.0, 4.0)))


def halves(df, com, sig, cfg):
    out = {}
    for tag, d, st in (("tr", df[df.index < pd.Timestamp(SPLIT)], START), ("te", df, SPLIT)):
        _, _, s = run_backtest(d, com, cfg, start=st, signals=sig.loc[d.index])
        out[tag] = s
    return out


rows = []
for sym in ("BTCUSD", "ETHUSD", "XRPUSD"):
    csv = os.path.join(HERE, f"{sym}_H1.csv")
    if "--no-export" not in sys.argv:
        export_csv(sym, "H1", START, None, csv)
    df = pd.read_csv(csv, parse_dates=["time"]).set_index("time")
    cost = COST_PCT * df.close.mean()
    tables = Committee().vote_table(df)
    for thr, sl, tp in GRID:
        com = Committee(threshold=thr)
        sig = com.signals(df, tables)
        for mult in (1, 2):
            cfg = Config(sl_atr=sl, tp_atr=tp, spread=cost * mult, contract_oz=1.0, max_lots=1e6, risk_pct=0.01)
            r = halves(df, com, sig, cfg)
            rows.append({"symbol": sym, "thr": thr, "sl": sl, "tp": tp, "cost": f"{mult}x",
                         "tr_n": r["tr"].get("trades", 0), "tr_pf": r["tr"].get("profit_factor", 0),
                         "tr_ret%": r["tr"].get("return_%"), "te_n": r["te"].get("trades", 0),
                         "te_pf": r["te"].get("profit_factor", 0), "te_ret%": r["te"].get("return_%"),
                         "te_dd%": r["te"].get("max_drawdown_%")})
res = pd.DataFrame(rows)
res.to_csv(os.path.join(HERE, "results_crypto.csv"), index=False)
pd.set_option("display.width", 250)
gold = res[(res.thr == 0.9) & (res.sl == 1.5) & (res.tp == 2.0)]
print("=== Gold settings (90% / 1.5 / 2.0) ===")
print(gold.to_string(index=False))
one = res[res.cost == "1x"].copy()
two = res[res.cost == "2x"][["symbol", "thr", "sl", "tp", "tr_pf", "te_pf"]].rename(columns={"tr_pf": "tr_pf2x", "te_pf": "te_pf2x"})
m = one.merge(two, on=["symbol", "thr", "sl", "tp"])
m["worst"] = m[["tr_pf", "te_pf", "tr_pf2x", "te_pf2x"]].min(axis=1)
m = m[(m.tr_n >= 30) & (m.te_n >= 30)]
print("\n=== Best 5 settings per symbol, ranked by worst profit factor across both halves and both cost levels ===")
for sym, g in m.groupby("symbol"):
    print(f"\n{sym}  ({(g.worst > 1.1).sum()} of {len(g)} settings pass worst>1.1)")
    print(g.sort_values("worst", ascending=False).head(5).drop(columns="cost").to_string(index=False))
