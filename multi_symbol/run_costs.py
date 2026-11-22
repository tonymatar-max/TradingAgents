"""Rerun committee backtests with realistic round-trip costs (spread + commission), at 1x and 2x.

Account is Exness Zero (spread ~0, commission per lot). Cost per trade is applied in price units
via the project's `spread` field (charged once, on entry), so here it holds the FULL round trip.
Reuses CSVs downloaded by run_multi.py.
"""
import os, sys
import MetaTrader5 as mt5
import pandas as pd

sys.path.insert(0, r"C:\Claude\tradingagents")
from xauusd_agents.backtest import Config, run_backtest
from xauusd_agents.committee import Committee

HERE = os.path.dirname(os.path.abspath(__file__))
START, SPLIT = "2026-01-01", "2026-07-01"

# Round-trip cost in PRICE units per 1 unit traded.
#  XAUUSD: MEASURED from your demo deals: commission $5.50/lot/side -> $11 / 100 oz = 0.11, + median spread 0.04
#  Others: ESTIMATES of a typical retail round trip (spread + commission), deliberately not optimistic.
COST = {
    "XAUUSD": 0.15,     # measured
    "XAGUSD": 0.030,    # ~3 cents
    "EURUSD": 0.00010,  # 1.0 pip
    "GBPUSD": 0.00012,  # 1.2 pips
    "USDJPY": 0.012,    # 1.2 pips
    "USOIL":  0.040,    # 4 cents/bbl
    "US500":  0.60,     # 0.6 index points
    "USTEC":  2.00,     # 2 index points
    "AAPL":   0.10,     # measured spread ~0.06-0.08 + margin
    "NVDA":   0.15,     # measured spread 0.08 median, 0.32 p75
}
# settings to test: the gold settings for all, plus each symbol's best from the zero-cost sweep
GOLD = (0.9, 1.5, 2.0)
BEST = {"XAUUSD": (0.9, 1.0, 3.0), "XAGUSD": (0.8, 2.0, 4.0), "GBPUSD": (0.9, 1.5, 4.0),
        "USOIL": (0.9, 1.0, 3.0), "USTEC": (0.9, 1.5, 3.0), "AAPL": (0.8, 1.0, 2.0), "NVDA": (0.9, 1.0, 2.0),
        "EURUSD": (0.7, 2.0, 4.0), "USDJPY": (0.8, 1.0, 2.0), "US500": (0.9, 1.0, 2.0)}


def specs():
    mt5.initialize()
    out = {}
    for s in COST:
        i = mt5.symbol_info(s)
        out[s] = (i.trade_contract_size, i.currency_profit, i.volume_max)
    mt5.shutdown()
    return out


def run(df, contract, ccy, vmax, thr, sl, tp, cost):
    c = contract / df.close.mean() if ccy != "USD" else contract
    com = Committee(threshold=thr)
    sig = com.signals(df)
    cfg = Config(sl_atr=sl, tp_atr=tp, spread=cost, contract_oz=c, max_lots=min(vmax, 1000.0), risk_pct=0.01)
    res = {}
    for tag, d, st in (("tr", df[df.index < pd.Timestamp(SPLIT)], START), ("te", df, SPLIT)):
        _, _, s = run_backtest(d, com, cfg, start=st, signals=sig.loc[d.index])
        res[tag] = s
    return res


def main():
    sp, rows = specs(), []
    for sym, cost in COST.items():
        df = pd.read_csv(os.path.join(HERE, f"{sym}_H1.csv"), parse_dates=["time"]).set_index("time")
        for label, (thr, sl, tp) in (("gold-settings", GOLD), ("best-sweep", BEST[sym])):
            for mult in (1, 2):
                r = run(df, *sp[sym], thr, sl, tp, cost * mult)
                rows.append({"symbol": sym, "settings": f"{label} {thr:.0%}/{sl}/{tp}", "cost": f"{mult}x",
                             "tr_n": r["tr"].get("trades", 0), "tr_pf": r["tr"].get("profit_factor"),
                             "tr_ret%": r["tr"].get("return_%"),
                             "te_n": r["te"].get("trades", 0), "te_pf": r["te"].get("profit_factor"),
                             "te_ret%": r["te"].get("return_%"), "te_dd%": r["te"].get("max_drawdown_%")})
    res = pd.DataFrame(rows)
    res["pass"] = (res.tr_pf > 1.1) & (res.te_pf > 1.1) & (res.tr_n >= 30) & (res.te_n >= 30)
    pd.set_option("display.width", 250)
    print(res.to_string(index=False))
    res.to_csv(os.path.join(HERE, "results_with_costs.csv"), index=False)


if __name__ == "__main__":
    main()
