"""Out-of-sample parameter sweep. Pick settings that work on BOTH the train and the test period."""
from __future__ import annotations

import itertools

import pandas as pd

from .backtest import Config, run_backtest
from .committee import Committee


def sweep(df, start, split, thresholds=(0.7, 0.8, 0.9), sls=(1.0, 1.5, 2.0), tps=(2.0, 3.0, 4.0),
          spread=0.30, risk=0.01):
    base = Committee()
    table = base.vote_table(df)  # causal: identical whether computed on full or truncated data
    split = pd.Timestamp(split)
    tr_df = df[df.index < split]
    rows = []
    for th, sl, tp in itertools.product(thresholds, sls, tps):
        com = Committee(threshold=th)
        cfg = Config(sl_atr=sl, tp_atr=tp, spread=spread, risk_pct=risk)
        r = {}
        for tag, d, st in (("train", tr_df, start), ("test", df, split)):
            _, _, s = run_backtest(d, com, cfg, start=st, table=table.loc[d.index])
            r[tag] = s
        if r["train"].get("trades", 0) < 20 or r["test"].get("trades", 0) < 20:
            continue  # too few trades to mean anything
        rows.append({"thr": th, "sl_atr": sl, "tp_atr": tp,
                     "tr_trades": r["train"]["trades"], "tr_pf": r["train"]["profit_factor"],
                     "tr_ret%": r["train"]["return_%"], "tr_dd%": r["train"]["max_drawdown_%"],
                     "te_trades": r["test"]["trades"], "te_pf": r["test"]["profit_factor"],
                     "te_ret%": r["test"]["return_%"], "te_dd%": r["test"]["max_drawdown_%"]})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["worst_pf"] = out[["tr_pf", "te_pf"]].min(axis=1)
    return out.sort_values("worst_pf", ascending=False)
