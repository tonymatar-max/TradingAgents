"""Backtest the XAUUSD 10-agent committee (C:\\Claude\\tradingagents) on other Exness symbols.

Uses each symbol's real MT5 contract size, current spread and lot limits. Read-only on the project:
imports its code, writes CSVs and results into this folder.

  python run_multi.py            export + backtest
  python run_multi.py --no-export   reuse CSVs already downloaded
"""
import json, os, sys
import MetaTrader5 as mt5
import pandas as pd

sys.path.insert(0, r"C:\Claude\tradingagents")
from xauusd_agents.backtest import Config, run_backtest
from xauusd_agents.committee import Committee
from xauusd_agents.mt5_export import export_csv
from xauusd_agents.optimize import sweep

HERE = os.path.dirname(os.path.abspath(__file__))
SYMBOLS = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "USOIL", "US500", "USTEC", "AAPL", "NVDA"]
TF, START, SPLIT = "H1", "2026-01-01", "2026-07-01"
GOLD_SETTINGS = dict(thr=0.9, sl=1.5, tp=2.0)   # the gold settings from the project README


def spec(sym):
    mt5.initialize()
    mt5.symbol_select(sym, True)
    i = mt5.symbol_info(sym)
    t = mt5.symbol_info_tick(sym)
    out = {"contract": i.trade_contract_size, "spread": i.spread * i.point, "point": i.point,
           "profit_ccy": i.currency_profit, "vol_max": i.volume_max, "mid": (t.bid + t.ask) / 2 if t else None}
    mt5.shutdown()
    return out


def cfg_for(s, df, sl, tp):
    contract = s["contract"]
    if s["profit_ccy"] != "USD":          # e.g. USDJPY P&L is in JPY: convert to USD with the average rate
        contract = contract / df.close.mean()
    return Config(sl_atr=sl, tp_atr=tp, spread=s["spread"], contract_oz=contract,
                  max_lots=min(s["vol_max"], 1000.0), risk_pct=0.01)


def stats_row(tag, trades, st):
    if not st.get("trades"):
        return {f"{tag}_trades": 0}
    return {f"{tag}_trades": st["trades"], f"{tag}_win%": st["win_rate_%"], f"{tag}_pf": st["profit_factor"],
            f"{tag}_ret%": st["return_%"], f"{tag}_dd%": st["max_drawdown_%"]}


def main():
    export = "--no-export" not in sys.argv
    rows, sweeps = [], {}
    for sym in SYMBOLS:
        csv = os.path.join(HERE, f"{sym}_{TF}.csv")
        try:
            if export:
                export_csv(sym, TF, START, None, csv)
            df = pd.read_csv(csv, parse_dates=["time"]).set_index("time")
        except SystemExit as e:
            rows.append({"symbol": sym, "error": str(e)[:80]}); continue
        s = spec(sym)
        g = GOLD_SETTINGS
        com = Committee(threshold=g["thr"])
        sig = com.signals(df)
        cfg = cfg_for(s, df, g["sl"], g["tp"])
        r = {"symbol": sym, "spread": round(s["spread"], 5), "contract": s["contract"], "bars": len(df)}
        train = df[df.index < pd.Timestamp(SPLIT)]
        for tag, d, st in (("train", train, START), ("test", df, SPLIT)):
            t, _, stt = run_backtest(d, com, cfg, start=st, signals=sig.loc[d.index])
            r.update(stats_row(tag, t, stt))
        rows.append(r)
        sw = sweep(df, START, SPLIT, spread=s["spread"])  # NOTE: sweep uses gold contract size; PF/win% still valid
        sweeps[sym] = sw.head(5).to_dict("records") if len(sw) else []
        print(f"done {sym}", flush=True)

    res = pd.DataFrame(rows)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    print("\n=== Gold settings (9/10 agree, SL 1.5 ATR, TP 2 ATR), train Jan-Jun / test Jul-now ===")
    print(res.to_string(index=False))
    print("\n=== Best per-symbol settings from sweep (sorted by worse of train/test profit factor) ===")
    for sym, rs in sweeps.items():
        print(f"\n{sym}:", "no setting with >=20 trades in both halves" if not rs else "")
        if rs:
            print(pd.DataFrame(rs).to_string(index=False))
    res.to_csv(os.path.join(HERE, "results_gold_settings.csv"), index=False)
    json.dump(sweeps, open(os.path.join(HERE, "results_sweeps.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
